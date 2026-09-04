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
# Five scenarios, each one a way the field can bite:
#   1. First OTA on an rsync-provisioned unit — must reconcile, not skip
#   2. A good update installs and verifies
#   3. A poisoned build crash-loops → rolls back, records the bad SHA
#   4. The next nightly run does NOT retry a commit that already broke the unit
#   5. A broken checkout never wipes a working install
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
seed() {
  echo "VERSION = '$1'" > raspberry-pi/src/marfutura_iot_audio.py
  echo "VERSION = '$1'" > raspberry-pi/src/oceankind/__init__.py
  git add -A >/dev/null; git commit -qm "$1"
}
seed v1
git branch -M main; git push -q origin main

run() { HOME="$FAKE_HOME" PATH="$STUBS:$PATH" OCEANKIND_OTA_SETTLE_S=1 \
        OCEANKIND_REPO_URL="$ORIGIN" bash "$SCRIPT" 2>&1; }

check() { # label expected_version
  got=$(grep -o "'[^']*'" "$FAKE_HOME/oceankind/marfutura_iot_audio.py" | tr -d "'")
  if [ "$got" = "$2" ]; then echo "  PASS: $1 (installed=$got)";
  else echo "  FAIL: $1 (installed=$got, expected=$2)"; FAILED=1; fi
}
FAILED=0

echo "=== 1. first run on an rsync-provisioned unit: must reconcile, not skip ==="
: > "$CTRL"
run | grep -E "desconocida|Actualización completada" | sed 's/^/  /'
check "v1 installed" v1

echo ""
echo "=== 2. good update to v2 ==="
cd "$WORK"; seed v2; git push -q origin main
run | tail -3
check "v2 installed" v2

echo ""
echo "=== 3. BAD update to v3 — service crash-loops, must roll back to v2 ==="
cd "$WORK"; seed v3; git push -q origin main
echo v3 > "$CTRL"          # v3 is the poison; v2 is still good
out=$(run); echo "$out" | grep -E "FALLO|revirtiendo|Rollback" | sed 's/^/  /'
check "rolled back to v2" v2
[ -f "$FAKE_HOME/oceankind/.ota_failed_sha" ] \
  && echo "  PASS: failed SHA recorded ($(cat "$FAKE_HOME/oceankind/.ota_failed_sha"))" \
  || { echo "  FAIL: no .ota_failed_sha written"; FAILED=1; }

echo ""
echo "=== 4. next nightly run must NOT retry the known-bad commit ==="
: > "$CTRL"
run | grep -E "ya falló|NO se reintenta" | sed 's/^/  /' \
  || { echo "  FAIL: retried a commit that already broke the unit"; FAILED=1; }
check "still on v2" v2

echo ""
echo "=== 5. broken checkout must not wipe a working install ==="
rm -f "$WORK/raspberry-pi/src/marfutura_iot_audio.py"
cd "$WORK"; git add -A >/dev/null; git commit -qm v4-broken; git push -q origin main
rm -f "$FAKE_HOME/oceankind/.ota_failed_sha"
run | grep -E "checkout inválido|revirtiendo|Rollback" | sed 's/^/  /'
check "install survived" v2

echo ""
[ "$FAILED" = 0 ] && echo "ALL PASS" || echo "SOME FAILED"
exit "$FAILED"
