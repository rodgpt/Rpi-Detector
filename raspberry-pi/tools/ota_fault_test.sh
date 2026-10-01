#!/bin/bash
# =============================================================================
# OTA fault-injection test — cuts an update short at every step.
#
#   bash raspberry-pi/tools/ota_fault_test.sh            # everything
#   OTA_FAULT_ONLY="S2 F" bash raspberry-pi/tools/ota_fault_test.sh
#
# ota_rollback_test.sh proves the happy rollback. This proves the ugly ones:
# power lost or the network dropped at ANY point of an update. It drives the
# real, unmodified update_oceankind.sh on a workstation, with every command
# that changes state (git, cp, rm, mv, touch, sleep, systemctl, raspi-config,
# reboot, pip, tee) replaced by a wrapper that counts calls and can SIGKILL
# the script just before or just after call number k. cp can also be cut
# MID-copy (first file only) to leave a torn install.
#
# Method (exhaustive, not sampled):
#   1. Run the scenario once clean → the trace is the list of N cut points.
#   2. For each k in 1..N and each of before/after (+mid for cp):
#        reset the unit to its starting state, run the update, cut it at k,
#        "restore power" (reboot: volatile state lost, boot hooks run), then
#        let three nightly cron runs happen, each followed by any reboots.
#   3. Assert the invariants below.
#
# Scenarios (enumerated at every cut point):
#   S1  good update v1→v2,             root writable (bench)
#   S2  poisoned update v2→v3,         root writable — cut anywhere, incl. rollback
#   S3  good update v1→v2,             RAM overlay ON (production, two-phase)
#   S4  poisoned update v2→v3,         RAM overlay ON
#   D1-D4  the same four, but power is lost AGAIN during the boot-time recovery
#          (every first cut × every cut of the recovery that follows it)
# Named faults (single run each):
#   F1  network down at fetch          F2  pip fails on the way forward
#   F3  pip fails during rollback      F4  disk full on the Nth cp
#   F5  corrupt checkout + stale lock  F6  two updaters at once
#   F7  stale lock left by a dead run
#
# Invariants (the exit code):
#   I-BOOT     after power returns and boot-time activity settles, BEFORE the
#              next nightly run, the service tree is complete and not poison.
#              (A unit that stays down until 03:00 is a day of deaf time.)
#   I-HEALTH   after three nightly runs the tree is complete and not poison
#   I-FINAL    …and is the expected version (good update landed / bad one
#              never stuck)
#   I-SHA      .installed_sha never names a version that is not installed
#   I-OVERLAY  a protected unit ends with the overlay re-enabled — never
#              left writable (SD wear) by an interrupted update
#
# Models the RAM overlay: with it ON, everything written to the root fs (home,
# /var/tmp, /etc/systemd) is discarded at reboot; toggling it takes effect at
# the next boot. It does NOT model torn single writes or unsynced data — that
# is what the real power-cut runbook in docs/OTA-FAULT-TESTING.md is for.
# =============================================================================
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${1:-$HERE/../scripts/update_oceankind.sh}"
BASE="${TMPDIR:-/tmp}"; BASE="${BASE%/}/oceankind-ota-fault"
ONLY="${OTA_FAULT_ONLY:-S1 S2 S3 S4 D1 D2 D3 D4 F}"
STUBS="$BASE/stubs"
echo "Testing: $SCRIPT"

# The script's boot entry point, if it has one. Legacy scripts rely on a
# one-shot unit + flag file written during phase 1.
HAS_BOOT=0; grep -q -- '--boot' "$SCRIPT" && HAS_BOOT=1

for t in git cp rm mv sleep touch sync; do
  eval "REAL_$t=\"$(type -P $t)\""
done

rm -rf "$BASE"; mkdir -p "$BASE" "$STUBS"

# ── shared library sourced by every stub ─────────────────────────────────────
cat > "$BASE/fp.sh" <<'EOF'
# Session state lives under $OTA_S; the unit's files under $OTA_HOME.
ver() { grep -o "'[^']*'" "$1" 2>/dev/null | tr -d "'"; }
tree_state() {  # healthy | poison | torn | missing
  local h="$OTA_HOME/oceankind" a b c va vb vc
  a="$h/marfutura_iot_audio.py"; b="$h/oceankind/__init__.py"; c="$h/helper.py"
  [ -s "$a" ] && [ -s "$b" ] && [ -s "$c" ] || { echo missing; return; }
  va=$(ver "$a"); vb=$(ver "$b"); vc=$(ver "$c")
  [ -n "$va" ] && [ "$va" = "$vb" ] && [ "$va" = "$vc" ] || { echo torn; return; }
  [ "$va" = "$(cat "$OTA_S/ctrl" 2>/dev/null)" ] && echo poison || echo healthy
}
fp_die() {
  echo "$FP_N" > "$OTA_S/state/killed_at"; : > "$OTA_S/state/died"
  kill -9 "$(cat "$OTA_PIDFILE")" 2>/dev/null; exit 137
}
fp_hit() {
  local n; n=$(( $(cat "$OTA_S/state/cnt" 2>/dev/null || echo 0) + 1 ))
  echo "$n" > "$OTA_S/state/cnt"
  echo "$n ${FP_TAG:-main} $1" >> "$OTA_S/state/trace"
  FP_N=$n; FP_ARMED=0; FP_WHEN=
  if [ "$n" = "$(cat "$OTA_S/state/kill_at" 2>/dev/null)" ]; then
    FP_ARMED=1; FP_WHEN=$(cat "$OTA_S/state/kill_when")
  fi
  [ "$FP_ARMED" = 1 ] && [ "$FP_WHEN" = before ] && fp_die
  return 0
}
fp_after() { [ "$FP_ARMED" = 1 ] && [ "$FP_WHEN" = after ] && fp_die; return 0; }
EOF

# generic wrapper: count, maybe cut before/after, run the real thing
mkstub() {
  cat > "$STUBS/$1" <<EOF
#!/bin/bash
. "$BASE/fp.sh"
fp_hit "$1 \$*"
"$2" "\$@"; rc=\$?
fp_after
exit \$rc
EOF
}
mkstub rm    "$REAL_rm"
mkstub mv    "$REAL_mv"
mkstub touch "$REAL_touch"

cat > "$STUBS/sleep" <<EOF
#!/bin/bash
. "$BASE/fp.sh"
fp_hit "sleep \$*"
[ -f "\$OTA_S/state/slow" ] && [ "\${FP_TAG:-main}" = A ] && "$REAL_sleep" 3
fp_after; exit 0
EOF

cat > "$STUBS/git" <<EOF
#!/bin/bash
. "$BASE/fp.sh"
fp_hit "git \$*"
if [ "\$(cat "\$OTA_S/state/net" 2>/dev/null)" = down ]; then
  for a in "\$@"; do case "\$a" in fetch|clone|ls-remote)
    echo "fatal: unable to access '\$OCEANKIND_REPO_URL': network is down" >&2; exit 128;;
  esac; done
fi
for a in "\$@"; do
  if [ "\$a" = fetch ] && [ -f "\$OTA_S/state/slow" ] && [ "\${FP_TAG:-main}" = A ]; then "$REAL_sleep" 3; fi
done
"$REAL_git" "\$@"; rc=\$?
fp_after
exit \$rc
EOF

# cp: also the disk-full injection (cp_fail_at) and the torn copy (mid)
cat > "$STUBS/cp" <<EOF
#!/bin/bash
. "$BASE/fp.sh"
fp_hit "cp \$*"
n=\$(( \$(cat "\$OTA_S/state/cpn" 2>/dev/null || echo 0) + 1 )); echo \$n > "\$OTA_S/state/cpn"
if [ "\$n" = "\$(cat "\$OTA_S/state/cp_fail_at" 2>/dev/null)" ]; then
  echo "cp: error writing: No space left on device" >&2; exit 1
fi
if [ "\$FP_ARMED" = 1 ] && [ "\$FP_WHEN" = mid ]; then
  rec=0; args=()
  for a in "\$@"; do case "\$a" in -*) [ "\$a" = -R ] || [ "\$a" = -r ] || [ "\$a" = -a ] && rec=1;; *) args+=("\$a");; esac; done
  dest="\${args[\${#args[@]}-1]}"; src="\${args[0]}"
  if [ "\$rec" = 1 ]; then
    "$REAL_git" --version >/dev/null; mkdir -p "\$dest"
    first=\$(ls -A "\$src" | head -1); [ -n "\$first" ] && "$REAL_cp" -R "\$src/\$first" "\$dest/"
  else
    "$REAL_cp" "\$src" "\$dest"
  fi
  fp_die
fi
"$REAL_cp" "\$@"; rc=\$?
fp_after
exit \$rc
EOF

cat > "$STUBS/sync" <<EOF
#!/bin/bash
exit 0
EOF

# sudo: only `tee` needs help (paths under /etc/systemd are remapped)
cat > "$STUBS/sudo" <<'EOF'
#!/bin/bash
. "$OTA_BASE/fp.sh"
if [ "$1" = tee ]; then
  p="$2"; case "$p" in /etc/systemd/system/*) p="$OTA_UNIT_DIR/${p##*/}";; esac
  fp_hit "tee $p"; cat > "$p"; fp_after; exit 0
fi
exec "$@"
EOF

cat > "$STUBS/systemctl" <<'EOF'
#!/bin/bash
. "$OTA_BASE/fp.sh"
NR="$OTA_S/state/nrestarts"; [ -f "$NR" ] || echo 0 > "$NR"
case "$1" in
  restart)       fp_hit "systemctl restart"; echo 0 > "$NR"; fp_after ;;
  enable)        fp_hit "systemctl enable $2"; : > "$OTA_UNIT_DIR/.enabled_$2"; fp_after ;;
  disable)       fp_hit "systemctl disable $2"; rm -f "$OTA_UNIT_DIR/.enabled_$2"; fp_after ;;
  daemon-reload) fp_hit "systemctl daemon-reload"; fp_after ;;
  is-active)     st=$(tree_state); { [ "$st" = healthy ] || [ "$st" = poison ]; } && exit 0; exit 3 ;;
  show)          cat "$NR"
                 [ "$(tree_state)" != healthy ] && echo $(( $(cat "$NR") + 3 )) > "$NR"
                 exit 0 ;;
  *) exit 0 ;;
esac
EOF

cat > "$STUBS/raspi-config" <<'EOF'
#!/bin/bash
. "$OTA_BASE/fp.sh"
P="$OTA_S/state/pending_overlay"
case "$2" in
  get_overlayfs) [ "$(cat "$P")" = on ] && echo 0 || echo 1 ;;
  do_overlayfs)  fp_hit "raspi-config do_overlayfs $3"
                 [ "$3" = 0 ] && echo on > "$P" || echo off > "$P"; fp_after ;;
esac
exit 0
EOF

cat > "$STUBS/findmnt" <<'EOF'
#!/bin/bash
[ "$(cat "$OTA_S/state/eff_overlay" 2>/dev/null)" = on ] && echo overlay || echo ext4
EOF

cat > "$STUBS/reboot" <<'EOF'
#!/bin/bash
. "$OTA_BASE/fp.sh"
fp_hit "reboot"; : > "$OTA_S/state/reboot_requested"
kill -9 "$(cat "$OTA_PIDFILE")" 2>/dev/null; exit 0
EOF

# the service's venv interpreter: only `-m pip install` is ever called
cat > "$BASE/venv_python" <<'EOF'
#!/bin/bash
. "$OTA_BASE/fp.sh"
fp_hit "pip $*"
n=$(( $(cat "$OTA_S/state/pipn" 2>/dev/null || echo 0) + 1 )); echo $n > "$OTA_S/state/pipn"
mode=$(cat "$OTA_S/state/pip_fail" 2>/dev/null)
if [ "$mode" = always ] || { [ "$mode" = after1 ] && [ "$n" -gt 1 ]; }; then
  echo "ERROR: Could not fetch URL https://pypi.org: connection lost" >&2; exit 1
fi
fp_after; exit 0
EOF
chmod +x "$STUBS"/* "$BASE/venv_python"

# ── the repo: v1, v2, v3, each a complete tree ───────────────────────────────
WORK="$BASE/work"
git init -q "$WORK"; cd "$WORK"
git config user.email t@t; git config user.name t
mkdir -p raspberry-pi/src/oceankind
for v in v1 v2 v3; do
  for f in raspberry-pi/src/marfutura_iot_audio.py raspberry-pi/src/helper.py raspberry-pi/src/oceankind/__init__.py; do
    echo "VERSION = '$v'" > "$f"
  done
  echo "numpy" > raspberry-pi/requirements.txt
  git add -A >/dev/null; git commit -qm "$v"; git tag "$v"
done
sha_of() { git -C "$WORK" rev-parse --short "$1"; }
cd "$BASE"

# ── golden units: built once per starting state, copied per session ──────────
# $1 name  $2 installed  $3 origin tip  $4 poison  $5 protected(overlay ON) 0|1
build_golden() {
  local g="$BASE/golden_$1" o="$BASE/golden_$1/origin.git" h="$BASE/golden_$1/home/oceankind"
  mkdir -p "$g/home/oceankind/venv/bin"
  git init -q --bare "$o"
  git -C "$WORK" push -q "$o" "$2:refs/heads/main"
  git clone -q --branch main "$o" "$h/code" 2>/dev/null
  cp "$h/code/raspberry-pi/src/"*.py "$h/"
  cp -R "$h/code/raspberry-pi/src/oceankind" "$h/oceankind"
  sha_of "$2" > "$h/.installed_sha"
  cp "$BASE/venv_python" "$h/venv/bin/python"
  echo "$4" > "$g/poison"
  [ "$5" = 1 ] && : > "$h/.sd_protection"
  git -C "$WORK" push -q -f "$o" "$3:refs/heads/main"     # origin moves ahead
}
build_golden good   v1 v2 ""   0
build_golden bad    v2 v3 v3   0
build_golden goodP  v1 v2 ""   1
build_golden badP   v2 v3 v3   1

# ── one session = one unit, one timeline ─────────────────────────────────────
# Globals per scenario process: SCN GOLD PROT  (set by run_scenario)
sess_reset() {
  S="$BASE/$SCN/s"; rm -rf "$S"; mkdir -p "$S/state" "$S/run" "$S/var_tmp" "$S/unit_dir"
  cp -R "$BASE/golden_$GOLD/home" "$S/home"; cp -R "$BASE/golden_$GOLD/origin.git" "$S/origin.git"
  "$REAL_git" -C "$S/home/oceankind/code" remote set-url origin "$S/origin.git"
  cp "$BASE/golden_$GOLD/poison" "$S/ctrl"
  if [ "$HAS_BOOT" = 1 ]; then
    echo "[Unit]" > "$S/unit_dir/oceankind-ota-boot.service"; : > "$S/unit_dir/.enabled_oceankind-ota-boot.service"
  fi
  if [ "$PROT" = 1 ]; then echo on > "$S/state/eff_overlay"; echo on > "$S/state/pending_overlay"
  else echo off > "$S/state/eff_overlay"; echo off > "$S/state/pending_overlay"; fi
  echo 0 > "$S/state/cnt"; : > "$S/state/trace"; echo 0 > "$S/state/kill_at"
  bootsnap
  OUT="$S/out.log"; : > "$OUT"
}
bootsnap() { # what "the SD card" holds when a RAM overlay boot begins
  rm -rf "$S/bootsnap"; mkdir "$S/bootsnap"
  cp -R "$S/home" "$S/var_tmp" "$S/unit_dir" "$S/bootsnap/"
}
armed() { echo "$1" > "$S/state/kill_at"; echo "$2" > "$S/state/kill_when"; }

# run the script with the harness environment; a watchdog stops a hang
run_script() { # args passed to the script; FP_TAG optional
  rm -f "$S/state/died" "$S/state/reboot_requested"
  local rc
  (
    export HOME="$S/home" PATH="$STUBS:$PATH" OTA_S="$S" OTA_HOME="$S/home" OTA_BASE="$BASE" \
           OTA_UNIT_DIR="$S/unit_dir" OTA_PIDFILE="$S/state/script.pid" \
           OCEANKIND_OTA_SETTLE_S="${SETTLE:-1}" OCEANKIND_REPO_URL="$S/origin.git" \
           OCEANKIND_ONESHOT_FLAG="$S/var_tmp/oceankind_update_pending" \
           OCEANKIND_UNIT_DIR="$S/unit_dir" OCEANKIND_RUN_DIR="$S/run"
    bash -c 'echo $$ > "$OTA_PIDFILE"; exec bash "$@"' _ "$SCRIPT" "$@"
  ) >> "$OUT" 2>&1 &
  local bg=$!
  ( i=0; while kill -0 "$bg" 2>/dev/null; do
      i=$((i+1)); [ "$i" -gt 300 ] && { kill -9 "$bg" 2>/dev/null; echo hang > "$S/state/hang"; break; }
      "$REAL_sleep" 0.2
    done ) 2>/dev/null &
  local wd=$!
  wait "$bg" 2>/dev/null; rc=$?
  kill -9 "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
  echo "── rc=$rc ──" >> "$OUT"
  return 0
}

boot() {
  echo "── BOOT ──" >> "$OUT"
  if [ "$(cat "$S/state/eff_overlay")" = on ]; then       # RAM layer is lost
    rm -rf "$S/home" "$S/var_tmp" "$S/unit_dir"
    cp -R "$S/bootsnap/home" "$S/bootsnap/var_tmp" "$S/bootsnap/unit_dir" "$S/"
  fi
  cp "$S/state/pending_overlay" "$S/state/eff_overlay"      # toggle applies now
  rm -rf "$S/run"; mkdir "$S/run"; echo 0 > "$S/state/nrestarts"
  [ "$(cat "$S/state/eff_overlay")" = on ] && bootsnap
  rm -f "$S/state/died" "$S/state/reboot_requested"
  if [ "$HAS_BOOT" = 1 ]; then
    run_script --boot
  elif [ -f "$S/unit_dir/oceankind-update.service" ] && [ -f "$S/var_tmp/oceankind_update_pending" ]; then
    run_script                                              # legacy phase 2
  fi
}

settle() { # after any run: power-loss or reboot ⇒ boot, until quiet
  local guard=0
  while [ -f "$S/state/died" ] || [ -f "$S/state/reboot_requested" ]; do
    guard=$((guard+1)); [ "$guard" -gt 8 ] && { echo "BOOT-LOOP" >> "$OUT"; touch "$S/state/bootloop"; return; }
    boot
  done
}
cron_cycle() { echo "── CRON ──" >> "$OUT"; run_script; settle; }

state_now() { OTA_S="$S" OTA_HOME="$S/home" bash -c '. "$0"; tree_state' "$BASE/fp.sh"; }
ver_now()   { OTA_S="$S" bash -c '. "$0"; ver "$1"' "$BASE/fp.sh" "$S/home/oceankind/helper.py"; }

# Evaluate after the cut session. Prints violations, one per line.
evaluate() { # $1 expected final version
  local bootst finst fin isha want
  bootst="$BOOT_STATE"
  [ "$bootst" != healthy ] && echo "I-BOOT(unit $bootst after power restored)"
  cron_cycle; cron_cycle; cron_cycle
  finst=$(state_now); fin=$(ver_now)
  [ "$finst" != healthy ] && echo "I-HEALTH(final tree $finst)"
  [ "$fin" != "$1" ] && echo "I-FINAL(final=${fin:-none} expected=$1)"
  isha=$(cat "$S/home/oceankind/.installed_sha" 2>/dev/null)
  if [ -n "$isha" ] && [ -n "$fin" ] && [ "$isha" != "$(sha_of "$fin")" ]; then
    echo "I-SHA(.installed_sha=$isha but installed=$fin)"
  fi
  if [ "$PROT" = 1 ]; then
    [ "$(cat "$S/state/pending_overlay")" != on ] && echo "I-OVERLAY(left writable: SD unprotected)"
  fi
  [ -f "$S/state/bootloop" ] && echo "BOOT-LOOP"
  [ -f "$S/state/hang" ] && echo "HANG(script did not exit)"
  return 0
}

# ── enumerated scenarios ─────────────────────────────────────────────────────
run_scenario() { # name gold prot expect
  SCN="$1"; GOLD="$2"; PROT="$3"; local expect="$4" res="$BASE/$1.res"
  mkdir -p "$BASE/$SCN"; : > "$res"
  sess_reset; armed 0 before; cron_cycle
  local n; n=$(wc -l < "$S/state/trace" | tr -d ' ')
  cp "$S/state/trace" "$BASE/$SCN/trace"
  local clean; clean="$(BOOT_STATE=$(state_now); evaluate "$expect")"
  [ -n "$clean" ] && echo "UNCUT baseline already violates: $clean" >> "$res"
  local k when desc total=0 bad=0 v
  for k in $(seq 1 "$n"); do
    desc=$(sed -n "${k}p" "$BASE/$SCN/trace" | cut -d' ' -f3- | sed -e "s#$S/home/oceankind/code/raspberry-pi/src#SRC#g" -e "s#$S/home/oceankind#~/oceankind#g" -e "s#$S/##g")
    local whens="before after"; case "$desc" in cp*) whens="before mid after";; esac
    for when in $whens; do
      sess_reset; armed "$k" "$when"; cron_cycle
      BOOT_STATE=$(state_now)
      total=$((total+1))
      v="$(evaluate "$expect" | paste -sd';' -)"
      if [ -n "$v" ]; then bad=$((bad+1)); echo "FAIL cut=$k/$when [$desc] → $v" >> "$res"; fi
    done
  done
  echo "SUMMARY $SCN: $total cut points, $bad violate ($n commands in a clean run)" >> "$res"
}

# ── double faults: power lost, then lost AGAIN during the recovery ───────────
# Cut the first run at every point (before, plus mid for cp), then cut the
# boot-time recovery at every one of ITS points. "after k" is the same state as
# "before k+1", so only `before` is enumerated here.
run_double() { # name gold prot expect
  SCN="$1"; GOLD="$2"; PROT="$3"; local expect="$4" res="$BASE/$1.res"
  mkdir -p "$BASE/$SCN"; : > "$res"
  sess_reset; armed 0 before; cron_cycle
  local n; n=$(wc -l < "$S/state/trace" | tr -d ' ')
  cp "$S/state/trace" "$BASE/$SCN/trace"
  local k when j n2 v d1 d2 total=0 bad=0
  for k in $(seq 1 "$n"); do
    d1=$(sed -n "${k}p" "$BASE/$SCN/trace" | cut -d' ' -f3- | sed -e "s#$S/home/oceankind/code/raspberry-pi/src#SRC#g" -e "s#$S/home/oceankind#~/oceankind#g" -e "s#$S/##g")
    local whens="before"; case "$d1" in cp*) whens="before mid";; esac
    for when in $whens; do
      # how long is the recovery after this first cut?
      sess_reset; armed "$k" "$when"; run_script
      echo 0 > "$S/state/cnt"; : > "$S/state/trace"; armed 0 before; settle
      n2=$(wc -l < "$S/state/trace" | tr -d ' '); cp "$S/state/trace" "$BASE/$SCN/trace2"
      for j in $(seq 1 "$n2"); do
        d2=$(sed -n "${j}p" "$BASE/$SCN/trace2" | cut -d' ' -f3- | sed -e "s#$S/home/oceankind/code/raspberry-pi/src#SRC#g" -e "s#$S/home/oceankind#~/oceankind#g" -e "s#$S/##g")
        sess_reset; armed "$k" "$when"; run_script
        echo 0 > "$S/state/cnt"; armed "$j" before; settle
        BOOT_STATE=$(state_now); total=$((total+1))
        v="$(evaluate "$expect" | paste -sd';' -)"
        if [ -n "$v" ]; then bad=$((bad+1)); echo "FAIL cut1=$k/$when [$d1] cut2=$j [$d2] → $v" >> "$res"; fi
      done
    done
  done
  echo "SUMMARY $SCN: $total double-cut combinations, $bad violate" >> "$res"
}

# ── named faults ─────────────────────────────────────────────────────────────
fault() { # id gold prot label
  SCN="$1"; GOLD="$2"; PROT="$3"; mkdir -p "$BASE/$1"; sess_reset
}
report() { # id label violations
  if [ -z "$3" ]; then echo "PASS $1: $2" >> "$BASE/F.res"; else echo "FAIL $1: $2 → $3" >> "$BASE/F.res"; fi
}
run_faults() {
  : > "$BASE/F.res"; local v out

  fault F1 good 0; echo down > "$S/state/net"; cron_cycle
  v=""; [ "$(ver_now)" != v1 ] && v="changed version with no network;"
  echo up > "$S/state/net"; BOOT_STATE=healthy; v="$v$(evaluate v2 | paste -sd';' -)"
  report F1 "network down at fetch: nothing changes, lands once it returns (root writable)" "$v"

  fault F1P goodP 1; echo down > "$S/state/net"; cron_cycle
  v=""; [ "$(cat "$S/state/pending_overlay")" != on ] && v="disabled the overlay with no network;"
  echo up > "$S/state/net"; BOOT_STATE=healthy; v="$v$(evaluate v2 | paste -sd';' -)"
  report F1P "network down at fetch: overlay untouched, lands once it returns (overlay ON)" "$v"

  fault F2 good 0; echo always > "$S/state/pip_fail"; cron_cycle
  v=""; [ "$(ver_now)" != v1 ] && v="installed code although dependencies failed;"
  grep -q "Actualización completada" "$OUT" && v="${v}claimed success although pip failed;"
  echo none > "$S/state/pip_fail"; BOOT_STATE=healthy; v="$v$(evaluate v2 | paste -sd';' -)"
  report F2 "pip fails on the way forward: old code kept, no false success, retried later" "$v"

  fault F3 bad 0; echo after1 > "$S/state/pip_fail"; BOOT_STATE=healthy
  cron_cycle; v="$(evaluate v2 | paste -sd';' -)"
  report F3 "pip fails during rollback: still reverts to the good version" "$v"

  for n in 1 2 3; do
    fault F4_$n good 0; echo "$n" > "$S/state/cp_fail_at"; cron_cycle
    BOOT_STATE=$(state_now); v="$(evaluate v2 | paste -sd';' -)"
    report F4_$n "disk full at cp #$n: never left torn, update lands later" "$v"
  done

  fault F5 good 0
  : > "$S/home/oceankind/code/.git/index.lock"; echo "garbage" > "$S/home/oceankind/code/.git/HEAD"
  cron_cycle; BOOT_STATE=$(state_now); v="$(evaluate v2 | paste -sd';' -)"
  report F5 "corrupt checkout + stale index.lock: recovers by re-cloning" "$v"

  fault F6 good 0; echo 1 > "$S/state/slow"
  ( FP_TAG=A run_script; ) &
  local apid=$!
  "$REAL_sleep" 1; FP_TAG=B run_script
  wait "$apid"
  v=""; grep -E ' B (cp|rm -rf|git .*reset)' "$S/state/trace" >/dev/null && v="second updater mutated the install while the first was running;"
  rm -f "$S/state/slow"; BOOT_STATE=$(state_now); v="$v$(evaluate v2 | paste -sd';' -)"
  report F6 "two updaters at once: the second must not touch anything" "$v"

  fault F7 good 0; mkdir -p "$S/run/ota.lock"; echo 999999 > "$S/run/ota.lock/pid"
  BOOT_STATE=healthy; cron_cycle; v="$(evaluate v2 | paste -sd';' -)"
  report F7 "lock left by a dead run does not block updates forever" "$v"
}

# ── drive: scenarios in parallel, then report ────────────────────────────────
want() { case " $ONLY " in *" $1 "*) return 0;; esac; return 1; }
pids=""
want S1 && { run_scenario S1 good  0 v2 & pids="$pids $!"; }
want S2 && { run_scenario S2 bad   0 v2 & pids="$pids $!"; }
want S3 && { run_scenario S3 goodP 1 v2 & pids="$pids $!"; }
want S4 && { run_scenario S4 badP  1 v2 & pids="$pids $!"; }
want D1 && { run_double D1 good  0 v2 & pids="$pids $!"; }
want D2 && { run_double D2 bad   0 v2 & pids="$pids $!"; }
want D3 && { run_double D3 goodP 1 v2 & pids="$pids $!"; }
want D4 && { run_double D4 badP  1 v2 & pids="$pids $!"; }
want F  && { run_faults & pids="$pids $!"; }
for p in $pids; do wait "$p"; done

FAILED=0
for r in S1 S2 S3 S4 D1 D2 D3 D4; do
  [ -f "$BASE/$r.res" ] || continue
  echo ""; echo "=== $r ==="
  [ "$(grep -c '^FAIL' "$BASE/$r.res")" != 0 ] && FAILED=1
  grep '^UNCUT' "$BASE/$r.res"
  grep '^FAIL' "$BASE/$r.res" | head -"${OTA_FAULT_SHOW:-12}"
  [ "$(grep -c '^FAIL' "$BASE/$r.res")" -gt "${OTA_FAULT_SHOW:-12}" ] && echo "  … full list: $BASE/$r.res"
  grep '^SUMMARY' "$BASE/$r.res"
done
if [ -f "$BASE/F.res" ]; then
  echo ""; echo "=== named faults ==="; cat "$BASE/F.res"
  grep -q '^FAIL' "$BASE/F.res" && FAILED=1
fi
echo ""
[ "$FAILED" = 0 ] && echo "ALL PASS" || echo "VIOLATIONS FOUND (logs under $BASE/<scenario>/s/out.log)"
exit "$FAILED"
