#!/bin/bash
# =============================================================================
# OTA rollback test — breaks an update on purpose.
#
#   bash raspberry-pi/tools/ota_rollback_test.sh
#
# "An untested rollback is not a safety net" (CLAUDE.md). This drives the real
# update_oceankind.sh against a throwaway git repo with stubbed systemctl /
# sudo / raspi-config. Runs on a workstation: no Pi, no systemd, no network,
# nothing installed. Exit 0 = the rollback contract holds.
#
# Since D-021 the script converges to an ASSIGNED release (a tag), never to a
# branch: every scenario assigns one the way the service does (--ref) or the
# daily timer does (.assigned_release), and the last two check that without an
# assignment, or with a hold, nothing moves.
#
# Scenarios, each one a way the field can bite:
#   1. First OTA on a setup.sh-provisioned unit — must reconcile, not skip
#   2. A good update installs and verifies; .installed_release records the tag
#   3. A poisoned build crash-loops → rolls back, records the bad SHA and tag
#   4. The next timer run does NOT retry a release that already broke the unit
#   5. A broken checkout never wipes a working install
#   6. No assignment → nothing happens, even with newer tags on origin
#   7. hold → nothing happens, even with a newer release assigned
#
# What it does NOT cover: the overlayfs two-phase path (needs a real Pi), and
# whether the service user can sudo without a password from cron. Both must be
# checked on the bench before trusting OTA on a deployed unit.
#
# The systemctl stub deliberately reports a crash-looping unit as "active" and
# zeroes NRestarts on a manual restart, because both are true of real systemd
# and both have already produced bugs in this script.
# =============================================================================
set -u

BASE="${TMPDIR:-/tmp}/oceankind-ota-test"
SCRIPT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/update_oceankind.sh}"
echo "Testing: $SCRIPT"
rm -rf "$BASE"; mkdir -p "$BASE"

FAKE_HOME="$BASE/home"
ORIGIN="$BASE/origin.git"
WORK="$BASE/work"
STUBS="$BASE/stubs"
CTRL="$BASE/ctrl"          # stub state: healthy | crashloop | dead
mkdir -p "$FAKE_HOME/oceankind/venv/bin" "$STUBS" "$WORK"

# ── stubs ────────────────────────────────────────────────────────────────────
cat > "$STUBS/sudo" <<'EOF'
#!/bin/bash
exec "$@"
EOF

# Health is decided by WHAT IS INSTALLED, not by a global flag — so a rollback
# to a good commit really does recover, exactly as in the field. $CTRL names the
# version that is poison.
#
# The stub also mimics systemd zeroing NRestarts on a manual restart, which is
# the behaviour that made sampling the baseline before the restart wrong.
cat > "$STUBS/systemctl" <<EOF
#!/bin/bash
CTRL="$CTRL"
COUNT="$BASE/nrestarts"
INSTALLED="$FAKE_HOME/oceankind/marfutura_iot_audio.py"
[ -f "\$COUNT" ] || echo 0 > "\$COUNT"
bad=\$(cat "\$CTRL" 2>/dev/null || echo "")
cur=\$(grep -o "'[^']*'" "\$INSTALLED" 2>/dev/null | tr -d "'")
case "\$1" in
  restart)
     echo 0 > "\$COUNT"                      # systemd resets NRestarts here
     exit 0 ;;
  is-active)
     exit 0 ;;                               # a crash loop still reads "active"
  show)
     # Report now, then accumulate: a poisoned build keeps restarting DURING
     # the verification window, so the second sample is higher than the first.
     cat "\$COUNT"
     if [ -n "\$bad" ] && [ "\$cur" = "\$bad" ]; then
         echo \$(( \$(cat "\$COUNT") + 3 )) > "\$COUNT"
     fi
     exit 0 ;;
  *) exit 0 ;;
esac
EOF

cat > "$STUBS/raspi-config" <<'EOF'
#!/bin/bash
# get_overlayfs: 1 = overlay OFF (take the direct update path)
[ "$2" = "get_overlayfs" ] && { echo 1; exit 0; }
exit 0
EOF

# venv python stub: swallows `-m pip install -r ...`
cat > "$FAKE_HOME/oceankind/venv/bin/python" <<'EOF'
#!/bin/bash
exit 0
EOF
chmod +x "$STUBS"/* "$FAKE_HOME/oceankind/venv/bin/python"

# ── a repo shaped like this one ──────────────────────────────────────────────
git init -q --bare "$ORIGIN"
git clone -q "$ORIGIN" "$WORK" 2>/dev/null
cd "$WORK"
git config user.email t@t; git config user.name t
mkdir -p raspberry-pi/src/oceankind
seed() {   # one commit, tagged with its version (a release, D-021)
  echo "VERSION = '$1'" > raspberry-pi/src/marfutura_iot_audio.py
  echo "VERSION = '$1'" > raspberry-pi/src/oceankind/__init__.py
  git add -A >/dev/null; git commit -qm "$1"; git tag "$1"
}
seed v1
git branch -M main; git push -q origin main --tags

# $@ = script args. --ref <tag> is how the service launches it; no args is the
# daily timer, which reads .assigned_release. No env file → no backend lookup.
run() { HOME="$FAKE_HOME" PATH="$STUBS:$PATH" OCEANKIND_OTA_SETTLE_S=1 \
        OCEANKIND_ENV_FILE="$BASE/no-such-env" \
        OCEANKIND_REPO_URL="$ORIGIN" bash "$SCRIPT" "$@" 2>&1; }
assign() { printf '%s %s\n' "$1" "${2:-0}" > "$FAKE_HOME/oceankind/.assigned_release"; }

check() { # label expected_version
  got=$(grep -o "'[^']*'" "$FAKE_HOME/oceankind/marfutura_iot_audio.py" | tr -d "'")
  if [ "$got" = "$2" ]; then echo "  PASS: $1 (installed=$got)";
  else echo "  FAIL: $1 (installed=$got, expected=$2)"; FAILED=1; fi
}
FAILED=0

echo "=== 1. first run on a setup.sh-provisioned unit: must reconcile, not skip ==="
: > "$CTRL"
run --ref v1 | grep -E "desconocida|Actualización completada" | sed 's/^/  /'
check "v1 installed" v1
[ "$(cat "$FAKE_HOME/oceankind/.installed_release" 2>/dev/null)" = v1 ] \
  && echo "  PASS: .installed_release = v1" \
  || { echo "  FAIL: .installed_release not written"; FAILED=1; }

echo ""
echo "=== 2. good update to v2 (assigned from the panel → --ref) ==="
cd "$WORK"; seed v2; git push -q origin main --tags
run --ref v2 | tail -3
check "v2 installed" v2

echo ""
echo "=== 3. BAD update to v3 — service crash-loops, must roll back to v2 ==="
cd "$WORK"; seed v3; git push -q origin main --tags
echo v3 > "$CTRL"          # v3 is the poison; v2 is still good
out=$(run --ref v3); echo "$out" | grep -E "FALLO|revirtiendo|Rollback" | sed 's/^/  /'
check "rolled back to v2" v2
[ -f "$FAKE_HOME/oceankind/.ota_failed_sha" ] \
  && echo "  PASS: failed SHA recorded ($(cat "$FAKE_HOME/oceankind/.ota_failed_sha"))" \
  || { echo "  FAIL: no .ota_failed_sha written"; FAILED=1; }
[ "$(cat "$FAKE_HOME/oceankind/.ota_failed_release" 2>/dev/null)" = v3 ] \
  && echo "  PASS: failed release recorded (v3)" \
  || { echo "  FAIL: no .ota_failed_release written"; FAILED=1; }

echo ""
echo "=== 4. the daily timer (no args, .assigned_release=v3) must NOT retry the known-bad release ==="
: > "$CTRL"
assign v3
run | grep -E "ya falló|NO se reintenta" | sed 's/^/  /' \
  || { echo "  FAIL: retried a release that already broke the unit"; FAILED=1; }
check "still on v2" v2

echo ""
echo "=== 5. broken checkout must not wipe a working install ==="
rm -f "$WORK/raspberry-pi/src/marfutura_iot_audio.py"
cd "$WORK"; git add -A >/dev/null; git commit -qm v4-broken; git tag v4; git push -q origin main --tags
rm -f "$FAKE_HOME/oceankind/.ota_failed_sha" "$FAKE_HOME/oceankind/.ota_failed_release"
run --ref v4 | grep -E "checkout inválido|revirtiendo|Rollback" | sed 's/^/  /'
check "install survived" v2

echo ""
echo "=== 6. no assignment → the OTA does nothing, whatever origin holds (D-021) ==="
rm -f "$FAKE_HOME/oceankind/.assigned_release"
run | grep -E "Sin asignación|Nada que hacer" | sed 's/^/  /' \
  || { echo "  FAIL: moved without an assignment"; FAILED=1; }
check "still on v2" v2

echo ""
echo "=== 7. hold → nothing moves even with a release assigned ==="
assign v3 1
run | grep -E "RETENIDA" | sed 's/^/  /' \
  || { echo "  FAIL: hold was not honoured"; FAILED=1; }
check "still on v2" v2

echo ""
echo "=== 8. a release that is not a tag is refused before touching git ==="
run --ref 'main; rm -rf /' >/dev/null 2>&1 && { echo "  FAIL: accepted a non-tag ref"; FAILED=1; } \
  || echo "  PASS: non-tag ref refused (exit $?)"
check "still on v2" v2

echo ""
[ "$FAILED" = 0 ] && echo "ALL PASS" || echo "SOME FAILED"
exit "$FAILED"
