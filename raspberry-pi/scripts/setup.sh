#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# OceanKind — Raspberry Pi provisioning
# Run once as root from this repository: sudo bash raspberry-pi/scripts/setup.sh
#
# Installs the PRODUCTION system (src/marfutura_iot_audio.py). The previous
# version of this script installed main.py, the abandoned prototype now in
# legacy/ (F-11). Do not point ExecStart anywhere else.
# ─────────────────────────────────────────────────────────────────────────────
set -e

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
apt-get update
apt-get install -y python3-pip alsa-utils libatlas-base-dev libportaudio2 git

# ── 2. Audio HAT overlay ──────────────────────────────────────────────────────
# D-009: which ADC is actually installed (HifiBerry DAC+ ADC Pro vs Codec Zero)
# is UNCONFIRMED. This assumes HifiBerry, matching the deployed units' config.
# If the bench unit has a different HAT, adjust before rebooting.
CONFIG_FILE="/boot/firmware/config.txt"
[ -f "$CONFIG_FILE" ] || CONFIG_FILE="/boot/config.txt"   # pre-bookworm fallback

if ! grep -q "hifiberry-dacplusadcpro" "$CONFIG_FILE"; then
    {
        echo ""
        echo "# HifiBerry ADC Pro (see D-009 if this is not the installed HAT)"
        echo "dtoverlay=hifiberry-dacplusadcpro"
    } >> "$CONFIG_FILE"
    echo "Added HifiBerry overlay to $CONFIG_FILE"
else
    echo "HifiBerry overlay already in $CONFIG_FILE"
fi
sed -i 's/^dtparam=audio=on/#dtparam=audio=on  # disabled for HifiBerry/' "$CONFIG_FILE"

# ── 3. Project directory ──────────────────────────────────────────────────────
mkdir -p "${OCEANKIND_DIR}/logs" "${OCEANKIND_DIR}/clips"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${OCEANKIND_DIR}"

# ── 4. Python dependencies (production set — see requirements.txt) ────────────
pip3 install --break-system-packages -r "${REQ_FILE}"

# ── 5. Copy the production system (launcher + oceankind package) ─────────────
cp "${SRC_DIR}"/*.py "${OCEANKIND_DIR}/"
rm -rf "${OCEANKIND_DIR}/oceankind"
cp -R "${SRC_DIR}/oceankind" "${OCEANKIND_DIR}/oceankind"
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
ExecStart=/usr/bin/python3 ${OCEANKIND_DIR}/marfutura_iot_audio.py
Restart=always
RestartSec=15
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
# Preferred source: raspberry-pi/oceankind.env — the gitignored live config
# (real Twilio credentials, per-unit identity). Falls back to a blank template.
LIVE_ENV="${SCRIPT_DIR}/../oceankind.env"
if [ ! -f /etc/oceankind.env ] && [ -f "${LIVE_ENV}" ]; then
    install -m 600 "${LIVE_ENV}" /etc/oceankind.env
    echo "Live configuration installed to /etc/oceankind.env from ${LIVE_ENV}"
    echo "⚠️  REMINDER: the Twilio token in it is pending rotation (F-04)."
elif [ ! -f /etc/oceankind.env ]; then
    cat > /etc/oceankind.env << 'ENV_EOF'
# OceanKind — per-unit configuration. THE ONLY place secrets live (F-04).
# The service refuses to start until the REQUIRED values are filled in.

# ── Identity — v2 contract: SITE and coordinates REQUIRED with storage ──
OCEANKIND_DEVICE_ID=
# Site id: everything writes under sites/{id}/ in the v2 container. [a-z0-9_-] only.
OCEANKIND_SITE=
OCEANKIND_SENSOR_LOCATION=
# Unit coordinates — go into _sites.json; set at provisioning, never in source (F-08)
OCEANKIND_SENSOR_LAT=
OCEANKIND_SENSOR_LON=

# ── Secrets (REQUIRED — service will not start without them) ──
OCEANKIND_TWILIO_SID=
OCEANKIND_TWILIO_TOKEN=
# Bench units without Twilio only: uncomment to allow starting without
# credentials. The unit then CANNOT send any alert and says so in health.
# OCEANKIND_ALLOW_NO_TWILIO=1

# ── Azure (optional; without them the unit runs local-only) ──
# Use the NEW v2 storage account/container, not the prototypes' blob (D-016).
OCEANKIND_STORAGE_CONNECTION_STRING=
OCEANKIND_STORAGE_CONTAINER=alerts
OCEANKIND_IOTHUB_CONNECTION_STRING=

# ── Notification routing ─────────────────────────────────
OCEANKIND_TWILIO_FROM=
OCEANKIND_TWILIO_TO=
# OCEANKIND_TO_ALERTA=
# OCEANKIND_TO_TECNICO=
# OCEANKIND_CALL_TO=

# ── Audio (continuous capture; device matched BY NAME, never by index) ──
# OCEANKIND_AUDIO_DEVICE_NAME=hifiberry,sndrpihifiberry,dacplusadc,codec
# OCEANKIND_AUDIO_SOURCE=device       # or synthetic:tone for a bench without hydrophone

# ── Detection (defaults are sane; remotely tunable via remote_config.json) ──
# OCEANKIND_DETECTION_MODE=psd        # psd | rms | auto
# OCEANKIND_SCORE_MIN=0.60
# OCEANKIND_ALERT_MIN_RMS=0.010
# OCEANKIND_DETECTION_LABEL=MOTOR
# OCEANKIND_CONFIG_HMAC_KEY=          # set to require signed remote config (F-10)
ENV_EOF
    chmod 600 /etc/oceankind.env
    echo "Environment template written to /etc/oceankind.env — FILL IT IN before starting."
fi

# ── 8. Enable service ─────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable oceankind

echo ""
echo "======================================================"
echo "  Setup complete."
echo ""
echo "  Next steps:"
echo "  1. Edit /etc/oceankind.env  (device ID, site, coords, Twilio, Azure)"
echo "  2. Reboot: sudo reboot"
echo "  3. Check:  sudo systemctl status oceankind"
echo "  4. Logs:   journalctl -u oceankind -f"
echo ""
echo "  The service refuses to start with missing secrets."
echo "  That is by design (R-8.1) — fill the env file."
echo "======================================================"
