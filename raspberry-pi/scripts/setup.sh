#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# OceanKind — Raspberry Pi provisioning
# Run once as root from this repository: sudo bash raspberry-pi/scripts/setup.sh
#
# Installs the PRODUCTION system (src/marfutura_iot_audio.py). The previous
# version of this script installed main.py, the abandoned prototype now in
# legacy/ (F-11). Do not point ExecStart anywhere else.
#
# Flags:
#   --live-env   install raspberry-pi/oceankind.env (the gitignored live file)
#                as /etc/oceankind.env when none exists. Off by default since
#                D-020: the normal path is the template plus ONE secret pasted
#                from the admin panel; the live file is a dev-machine artefact.
# ─────────────────────────────────────────────────────────────────────────────
set -e

INSTALL_LIVE_ENV=0
for arg in "$@"; do
    case "$arg" in
        --live-env) INSTALL_LIVE_ENV=1 ;;
        *) echo "unknown flag: $arg (known: --live-env)"; exit 64 ;;
    esac
done

# Service user. MUST agree with protect_sd.sh, which uses the same expression
# (F-17: the two scripts disagreeing meant clips could land outside the tmpfs
# the SD protection provides). Override for both with SERVICE_USER=<user>.
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-marfutura}}"
HOME_DIR="/home/${SERVICE_USER}"
OCEANKIND_DIR="${HOME_DIR}/oceankind"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SCRIPT_DIR}/../src"
REQ_FILE="${SCRIPT_DIR}/../requirements.txt"

echo "======================================================"
echo "  OceanKind — Raspberry Pi Setup"
echo "  Service user: ${SERVICE_USER}"
echo "======================================================"

if ! id "${SERVICE_USER}" &>/dev/null; then
    echo "ERROR: user '${SERVICE_USER}' does not exist. Create it or run with SERVICE_USER=<user>."
    exit 1
fi

# ── 1. System packages ────────────────────────────────────────────────────────
# BLAS: numpy/scipy need one. Debian removed ATLAS after bookworm, so
# libatlas-base-dev has no installation candidate on trixie and later — apt
# fails the whole install. Pick whichever the running release actually ships.
apt-get update

BLAS_PKG=""
for candidate in libopenblas-dev libatlas-base-dev libblas-dev; do
    if apt-cache policy "$candidate" 2>/dev/null | grep -q "Candidate: [^(]"; then
        BLAS_PKG="$candidate"
        break
    fi
done
if [ -z "$BLAS_PKG" ]; then
    echo "ERROR: no BLAS package available (tried libopenblas-dev, libatlas-base-dev, libblas-dev)."
    echo "       numpy/scipy will not build or run. Fix apt sources before continuing."
    exit 1
fi
echo "BLAS provider: ${BLAS_PKG}"

apt-get install -y python3-pip python3-venv alsa-utils "$BLAS_PKG" libportaudio2 git

# ── 2. Audio HAT overlay ──────────────────────────────────────────────────────
# D-009 DECIDED 2026-10-01: Raspberry Pi Codec Zero is the project's ADC.
# (Previously HifiBerry DAC+ ADC Pro was assumed here; that was never
# confirmed on a real unit and the client has since settled on Codec Zero.)
CONFIG_FILE="/boot/firmware/config.txt"
[ -f "$CONFIG_FILE" ] || CONFIG_FILE="/boot/config.txt"   # pre-bookworm fallback

if ! grep -q "rpi-codeczero" "$CONFIG_FILE"; then
    {
        echo ""
        echo "# Raspberry Pi Codec Zero (D-009)"
        echo "dtoverlay=rpi-codeczero"
    } >> "$CONFIG_FILE"
    echo "Added Codec Zero overlay to $CONFIG_FILE"
else
    echo "Codec Zero overlay already in $CONFIG_FILE"
fi
sed -i 's/^dtparam=audio=on/#dtparam=audio=on  # disabled for Codec Zero/' "$CONFIG_FILE"

# ── 3. Project directory ──────────────────────────────────────────────────────
mkdir -p "${OCEANKIND_DIR}/logs" "${OCEANKIND_DIR}/clips"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${OCEANKIND_DIR}"

# ── 4. Python dependencies (production set — see requirements.txt) ────────────
# Into a venv, NOT the system interpreter. `pip --break-system-packages` fails
# here the moment a dependency needs a newer version of something dpkg owns:
# pip cannot uninstall a Debian-installed package (no RECORD file) and aborts
# mid-install — which is how a trixie provision dies on urllib3. Forcing it
# with --ignore-installed would overwrite dpkg's files and leave apt broken on
# a unit nobody can reach; that is the worse failure. The venv gives the
# service its own dependency set and touches nothing the OS manages.
VENV_DIR="${OCEANKIND_DIR}/venv"
VENV_PY="${VENV_DIR}/bin/python"

if [ ! -x "${VENV_PY}" ]; then
    python3 -m venv "${VENV_DIR}"
    echo "Virtualenv created at ${VENV_DIR}"
else
    echo "Virtualenv already present at ${VENV_DIR}"
fi

# piwheels (prebuilt ARM numpy/scipy) is configured in /etc/pip.conf, which a
# venv inherits. If pip starts COMPILING scipy, that lookup failed — stop and
# fix it rather than waiting out a multi-hour source build on this chip.
"${VENV_PY}" -m pip install --upgrade pip
"${VENV_PY}" -m pip install -r "${REQ_FILE}"

# ── 5. Copy the production system (launcher + oceankind package) ─────────────
cp "${SRC_DIR}"/*.py "${OCEANKIND_DIR}/"
rm -rf "${OCEANKIND_DIR}/oceankind"
cp -R "${SRC_DIR}/oceankind" "${OCEANKIND_DIR}/oceankind"
# ml_mfcc detector model (used only if that detector is enabled; see env file)
[ -f "${SCRIPT_DIR}/../models/model.joblib" ] && \
    cp "${SCRIPT_DIR}/../models/model.joblib" "${OCEANKIND_DIR}/model.joblib"

# Operational scripts. update_oceankind.sh MUST land here: both its own header
# and protect_sd.sh tell the operator to run ~/oceankind/update_oceankind.sh,
# and until now nothing ever put it there — the documented OTA command pointed
# at a file that did not exist on a freshly provisioned unit.
cp "${SCRIPT_DIR}/update_oceankind.sh" "${SCRIPT_DIR}/protect_sd.sh" "${OCEANKIND_DIR}/"
chmod +x "${OCEANKIND_DIR}/update_oceankind.sh" "${OCEANKIND_DIR}/protect_sd.sh"

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${OCEANKIND_DIR}"
echo "Production code copied to ${OCEANKIND_DIR}"

# ── 6. systemd service ────────────────────────────────────────────────────────
cat > /etc/systemd/system/oceankind.service << SERVICE_EOF
[Unit]
Description=OceanKind acoustic monitor
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${OCEANKIND_DIR}
# Codec Zero (D-009) boots with AUX IN capture fully muted — Aux/Mixin/ADC
# switches all default off. Nothing in ALSA or our code enables this on its
# own; found 2026-10-24 (F-28) when capture failed with "Input/output error"
# / "I2S SYNC error!" that looked kernel-related but wasn't. Not "-" guarded:
# a missing control means this is no longer the hardware D-009 committed to,
# and that should fail loud, not run with audio silently muted.
ExecStartPre=/usr/bin/amixer -c 0 cset name='AUX Jack Switch' on
ExecStartPre=/usr/bin/amixer -c 0 cset name='Aux Switch' on,on
ExecStartPre=/usr/bin/amixer -c 0 cset name='Mixin Left Aux Left Switch' on
ExecStartPre=/usr/bin/amixer -c 0 cset name='Mixin Right Aux Right Switch' on
ExecStartPre=/usr/bin/amixer -c 0 cset name='Mixin PGA Switch' on,on
ExecStartPre=/usr/bin/amixer -c 0 cset name='ADC Switch' on,on
ExecStart=${VENV_PY} ${OCEANKIND_DIR}/marfutura_iot_audio.py
Restart=always
RestartSec=15
# Watchdog (R-2.7): the service pings WATCHDOG=1 only while its worker threads
# prove alive; a hung thread starves the ping and systemd restarts us. A hang
# was previously invisible. NotifyAccess lets the main process send the pings.
WatchdogSec=120
NotifyAccess=main
StandardOutput=journal
StandardError=journal
# All configuration and secrets come from here. The service REFUSES TO START
# if required secrets are missing (R-8.1) — that is intentional, fix the env
# file rather than the check.
EnvironmentFile=/etc/oceankind.env

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# ── 7. Environment file ───────────────────────────────────────────────────────
# Default: a template with exactly what a unit needs — identity, the backend
# URL, the ONE secret the admin panel printed (D-020), audio. With --live-env,
# raspberry-pi/oceankind.env (gitignored) is installed instead when present.
LIVE_ENV="${SCRIPT_DIR}/../oceankind.env"
if [ ! -f /etc/oceankind.env ] && [ "${INSTALL_LIVE_ENV}" = 1 ] && [ -f "${LIVE_ENV}" ]; then
    install -m 600 "${LIVE_ENV}" /etc/oceankind.env
    echo "Live configuration installed to /etc/oceankind.env from ${LIVE_ENV} (--live-env)"
elif [ ! -f /etc/oceankind.env ]; then
    cat > /etc/oceankind.env << 'ENV_EOF'
# OceanKind — per-unit configuration. THE ONLY place secrets live (F-04).
# The service refuses to start until the REQUIRED values are filled in.
#
# No phone numbers, no Twilio, no bench flag here (D-020): every alert to a
# human leaves from the backend, driven by what this unit already sends it.

# ── Identity — must match the device you created in Admin → Dispositivos ──
OCEANKIND_DEVICE_ID=
# Site id: everything writes under sites/{id}/ in the v2 container. [a-z0-9_-] only.
OCEANKIND_SITE=
OCEANKIND_SENSOR_LOCATION=
# Unit coordinates — go into _sites.json; set at provisioning, never in source (F-08).
# Only used when Azure storage is configured below.
OCEANKIND_SENSOR_LAT=
OCEANKIND_SENSOR_LON=

# ── Dashboard backend ──
# Base URL only (no /api/...). Empty = no push, no heartbeat, nobody alerted.
OCEANKIND_BACKEND_URL=
# THE secret, shown once when the device was created in the panel. From it the
# unit derives its authentication key and its configuration-verification key
# (contract §Provisioning secret). URL without secret = refuse to start.
# If lost: delete the device in the panel, create it again, paste the new one.
OCEANKIND_DEVICE_SECRET=

# ── Audio (continuous capture; device matched BY NAME, never by index) ──
# Codec Zero is the project's ADC (D-009). Its exact ALSA card name isn't
# stable across kernel/driver versions (seen as "IQaudIOCODEC" and later
# "Zero [RPi Codec Zero]" on the same hardware) — "codec" is the one
# substring common to both. Uncommented so it's explicit, not an invisible
# default.
OCEANKIND_AUDIO_DEVICE_NAME=codec
# No hydrophone yet? Uncomment these two lines to test the whole chain with a
# realistic synthetic detection every ~12 min on average. Leave both
# commented out for a real deployment.
# OCEANKIND_AUDIO_SOURCE=synthetic:sporadic
# OCEANKIND_SYNTHETIC_SPORADIC_MEAN_S=720

# ── Detection (defaults are sane; tuned from the panel, signed per device) ──
# Initial values only. Admin → Dispositivos → Configurar changes them live;
# the unit verifies the document with the key derived from its secret (F-10).
# OCEANKIND_DETECTION_MODE=psd        # psd | rms | auto
# OCEANKIND_SCORE_MIN=0.60
# OCEANKIND_ALERT_MIN_RMS=0.010
# OCEANKIND_DETECTION_LABEL=MOTOR

# ── Azure blob storage (optional; without it the unit is push-only) ──
# Use the NEW v2 storage account/container, not the prototypes' blob (D-016).
OCEANKIND_STORAGE_CONNECTION_STRING=
OCEANKIND_STORAGE_CONTAINER=alerts
ENV_EOF
    chmod 600 /etc/oceankind.env
    echo "Environment template written to /etc/oceankind.env — FILL IT IN before starting."
fi

# ── 7a. Env generation check (re-running setup.sh is the update path) ─────────
# D-020 (2026-10-24): one secret per device replaces OCEANKIND_DEVICE_KEY and
# OCEANKIND_CONFIG_HMAC_KEY. A unit still carrying the old key and no secret
# REFUSES TO START on this code, on purpose — it could authenticate but never
# verify a configuration or a release. Nothing here can mint the secret (only
# the panel can), so the only honest move is to say exactly what to do.
if [ -f /etc/oceankind.env ] \
   && grep -qE '^[[:space:]]*OCEANKIND_DEVICE_KEY=.+' /etc/oceankind.env \
   && ! grep -qE '^[[:space:]]*OCEANKIND_DEVICE_SECRET=.+' /etc/oceankind.env; then
    echo ""
    echo "⚠️  D-020: /etc/oceankind.env has OCEANKIND_DEVICE_KEY (pre-2026-10-24) and no"
    echo "   OCEANKIND_DEVICE_SECRET. The service WILL REFUSE TO START until you:"
    echo "     1. delete this device in Admin → Dispositivos and create it again"
    echo "     2. put the new secret in /etc/oceankind.env as OCEANKIND_DEVICE_SECRET="
    echo "     3. remove the OCEANKIND_DEVICE_KEY line"
    echo "   Twilio/IoT/BENCH lines in the file are ignored and can be deleted."
    echo ""
fi

# ── 7b. OTA boot unit ─────────────────────────────────────────────────────────
# Permanent — installed here, while the root is still writable, so it survives
# the RAM overlay. Runs `update_oceankind.sh --boot` on EVERY boot: it repairs an
# OTA that was cut short (power, kernel) and, on an SD-protected unit, runs the
# maintenance window and re-enables the overlay afterwards. Without it the OTA
# script refuses to disable the overlay: nobody would turn it back on.
cat > /etc/systemd/system/oceankind-ota-boot.service << BOOT_EOF
[Unit]
Description=OceanKind OTA recovery / maintenance window (at boot)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${SERVICE_USER}
# Verifies for SETTLE_S and may roll back, so it takes minutes, not seconds.
TimeoutStartSec=1200
ExecStart=/bin/bash ${OCEANKIND_DIR}/update_oceankind.sh --boot
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
BOOT_EOF

# ── 7c. OTA daily timer (D-021) ───────────────────────────────────────────────
# The normal update path is the service itself: it sees a release assigned in
# its signed configuration and launches update_oceankind.sh --ref <tag>. This
# timer is the safety net for a unit whose service is down and therefore not
# polling: once a day it converges to .assigned_release (or asks the backend).
# It never tracks a branch — with no assignment it does nothing.
cat > /etc/systemd/system/oceankind-ota.service << OTA_EOF
[Unit]
Description=OceanKind OTA — converge to the assigned release (D-021)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${SERVICE_USER}
TimeoutStartSec=1800
ExecStart=/bin/bash ${OCEANKIND_DIR}/update_oceankind.sh
StandardOutput=journal
StandardError=journal
OTA_EOF

# The service launches an assigned update through THIS template, as its own
# transient unit (`systemctl start --no-block oceankind-ota@<tag>.service`),
# never as a child process: the script restarts oceankind.service to verify the
# build, and a child — even in its own session — lives in the service's cgroup
# and dies with it. That produced a 7-second restart loop on the bench on
# 2026-10-24. %i is the tag; it has already matched the release regex.
cat > /etc/systemd/system/oceankind-ota@.service << OTA_T_EOF
[Unit]
Description=OceanKind OTA — deploy assigned release %i (D-021)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${SERVICE_USER}
TimeoutStartSec=1800
# The log directory is tmpfs and may not exist yet on a unit that has not run
# protect_sd.sh; systemd opens the append: target before ExecStart runs.
ExecStartPre=/bin/mkdir -p /tmp/oceankind/logs
ExecStart=/bin/bash ${OCEANKIND_DIR}/update_oceankind.sh --ref %i
StandardOutput=append:/tmp/oceankind/logs/update.log
StandardError=append:/tmp/oceankind/logs/update.log
OTA_T_EOF

# ── 7d. Exactly the sudo the OTA needs, and nothing else ──────────────────────
# The service user has to start the OTA unit and the OTA has to restart the
# service, read its restart counter, toggle the overlay and reboot. Until now
# this relied on the imager's blanket NOPASSWD for the first user; write it
# down as a drop-in so a unit provisioned with a locked-down user still works,
# and so the list of privileged commands is in one place.
cat > /etc/sudoers.d/oceankind-ota << SUDO_EOF
# OceanKind OTA (D-021): the service launches updates as their own unit and the
# update script restarts the service to verify. Installed by setup.sh.
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block oceankind-ota@*.service
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart oceankind
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart oceankind.service
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/bin/raspi-config nonint do_overlayfs *
${SERVICE_USER} ALL=(root) NOPASSWD: /usr/sbin/reboot, /sbin/reboot
SUDO_EOF
chmod 440 /etc/sudoers.d/oceankind-ota
if ! visudo -c -f /etc/sudoers.d/oceankind-ota >/dev/null; then
    echo "ERROR: generated sudoers drop-in is invalid; removing it so sudo keeps working."
    rm -f /etc/sudoers.d/oceankind-ota
    exit 1
fi

cat > /etc/systemd/system/oceankind-ota.timer << TIMER_EOF
[Unit]
Description=OceanKind OTA daily convergence (D-021)

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=1800
Persistent=true

[Install]
WantedBy=timers.target
TIMER_EOF

# ── 8. Enable service ─────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable oceankind
systemctl enable oceankind-ota-boot
systemctl enable --now oceankind-ota.timer

echo ""
echo "======================================================"
echo "  Setup complete."
echo ""
echo "  Next steps:"
echo "  1. Edit /etc/oceankind.env  (device ID, site, backend URL, the ONE secret"
echo "     from Admin → Dispositivos; Azure only if this unit also writes blobs)"
echo "  2. Reboot: sudo reboot"
echo "  3. Check:  sudo systemctl status oceankind"
echo "  4. Logs:   journalctl -u oceankind -f"
echo "  Updates arrive from the panel (assign a release to this device)."
echo ""
echo "  Dependencies live in ${VENV_DIR}, not the system Python."
echo "  To run anything that imports the oceankind package by hand, use"
echo "  ${VENV_PY} — plain 'python3' will not see numpy/scipy/azure."
echo ""
echo "  The service refuses to start with missing secrets."
echo "  That is by design (R-8.1) — fill the env file."
echo "======================================================"
