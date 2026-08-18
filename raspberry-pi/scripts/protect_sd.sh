#!/bin/bash
# =============================================================================
# OceanKind — Protección de tarjeta SD
# Configura overlay filesystem (root de solo lectura) en Raspberry Pi OS.
#
# Qué hace:
#   1. Crea symlinks ~/oceankind/logs y ~/oceankind/clips → /tmp/oceankind/...
#      (los writes de runtime van a RAM, no a la SD)
#   2. Actualiza el servicio oceankind para crear los dirs en /tmp al arrancar
#   3. Habilita el overlay filesystem via raspi-config
#   4. Pide reiniciar
#
# Uso:
#   sudo bash protect_sd.sh
#
# NOTA: Con overlay activo, los cambios al filesystem se pierden al reiniciar.
#       Las actualizaciones OTA usan update_oceankind.sh que deshabilita
#       el overlay temporalmente para persistir los cambios.
# =============================================================================

set -e

# Debe correr como root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Ejecuta como root: sudo bash protect_sd.sh"
    exit 1
fi

# Usuario del servicio — MISMA expresión que setup.sh (F-17: si difieren, los
# clips caen fuera del tmpfs que esta protección crea). Override: SERVICE_USER=<user>.
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-marfutura}}"
HOME_DIR="/home/${SERVICE_USER}"
OCEANKIND_DIR="${HOME_DIR}/oceankind"

echo ""
echo "=============================================="
echo "  OceanKind — Protección SD"
echo "=============================================="
echo "  Usuario: ${SERVICE_USER}"
echo "  Directorio: ${OCEANKIND_DIR}"
echo ""

# ── 1. Verificar que raspi-config existe ─────────────────────────────────────
if ! command -v raspi-config &>/dev/null; then
    echo "ERROR: raspi-config no encontrado. Este script requiere Raspberry Pi OS."
    exit 1
fi

# ── 2. Verificar estado actual del overlay ───────────────────────────────────
OVERLAY_STATUS=$(raspi-config nonint get_overlayfs 2>/dev/null || echo "1")
if [ "$OVERLAY_STATUS" = "0" ]; then
    echo "INFO: El overlay filesystem ya está habilitado."
    echo "      Si quieres reconfigurar, deshabilítalo primero:"
    echo "      sudo raspi-config nonint do_overlayfs 1 && sudo reboot"
    exit 0
fi

# ── 3. Crear estructura de directorios runtime en /tmp ───────────────────────
echo "[1/5] Configurando directorios runtime en /tmp..."

# systemd-tmpfiles: crea /tmp/oceankind/{logs,clips} en cada arranque
cat > /etc/tmpfiles.d/oceankind.conf << 'EOF'
# OceanKind runtime directories (recreados en cada boot por systemd-tmpfiles)
d /tmp/oceankind        0755 marfutura marfutura -
d /tmp/oceankind/logs   0755 marfutura marfutura -
d /tmp/oceankind/clips  0755 marfutura marfutura -
EOF

# Reemplazar 'marfutura' por el usuario real si es diferente
sed -i "s/marfutura/${SERVICE_USER}/g" /etc/tmpfiles.d/oceankind.conf

# Crear los dirs ahora (para no tener que reiniciar todavía)
mkdir -p /tmp/oceankind/logs /tmp/oceankind/clips
chown -R "${SERVICE_USER}:${SERVICE_USER}" /tmp/oceankind

echo "    ✓ /etc/tmpfiles.d/oceankind.conf creado"
echo "    ✓ /tmp/oceankind/{logs,clips} creados"

# ── 4. Reemplazar ~/oceankind/logs y clips con symlinks ──────────────────────
echo "[2/5] Creando symlinks en ~/oceankind/..."

# Migrar logs existentes a /tmp (sólo los del boot actual)
if [ -d "${OCEANKIND_DIR}/logs" ] && [ ! -L "${OCEANKIND_DIR}/logs" ]; then
    echo "    Moviendo logs existentes a /tmp/oceankind/logs..."
    cp -a "${OCEANKIND_DIR}/logs/." /tmp/oceankind/logs/ 2>/dev/null || true
    rm -rf "${OCEANKIND_DIR}/logs"
fi

if [ -d "${OCEANKIND_DIR}/clips" ] && [ ! -L "${OCEANKIND_DIR}/clips" ]; then
    rm -rf "${OCEANKIND_DIR}/clips"
fi

# Crear symlinks
ln -sfn /tmp/oceankind/logs  "${OCEANKIND_DIR}/logs"
ln -sfn /tmp/oceankind/clips "${OCEANKIND_DIR}/clips"
chown -h "${SERVICE_USER}:${SERVICE_USER}" "${OCEANKIND_DIR}/logs" "${OCEANKIND_DIR}/clips"

echo "    ✓ ${OCEANKIND_DIR}/logs  → /tmp/oceankind/logs"
echo "    ✓ ${OCEANKIND_DIR}/clips → /tmp/oceankind/clips"

# ── 5. Actualizar unidad systemd con ExecStartPre ────────────────────────────
echo "[3/5] Actualizando oceankind.service..."

SERVICE_FILE="/etc/systemd/system/oceankind.service"

if [ -f "$SERVICE_FILE" ]; then
    # Añadir ExecStartPre si no existe ya
    if ! grep -q "ExecStartPre" "$SERVICE_FILE"; then
        sed -i '/^ExecStart=/i ExecStartPre=/bin/mkdir -p /tmp/oceankind/logs /tmp/oceankind/clips' "$SERVICE_FILE"
        sed -i "/ExecStartPre=/a ExecStartPre=/bin/chown -R ${SERVICE_USER}:${SERVICE_USER} /tmp/oceankind" "$SERVICE_FILE"
        echo "    ✓ ExecStartPre añadido al servicio"
    else
        echo "    INFO: ExecStartPre ya existe en el servicio"
    fi
    systemctl daemon-reload
else
    echo "    AVISO: ${SERVICE_FILE} no encontrado — crea el servicio antes de continuar"
fi

# ── 6. Habilitar boot partition read-only ────────────────────────────────────
echo "[4/5] Habilitando boot partition read-only..."
raspi-config nonint do_boot_ro 0 2>/dev/null || true
echo "    ✓ /boot configurado como read-only"

# ── 7. Habilitar overlay filesystem ─────────────────────────────────────────
echo "[5/5] Habilitando overlay filesystem..."
raspi-config nonint do_overlayfs 0
echo "    ✓ Overlay filesystem habilitado"

echo ""
echo "=============================================="
echo "  ✓ Protección SD configurada"
echo ""
echo "  IMPORTANTE — Lee esto antes de reiniciar:"
echo ""
echo "  • El filesystem raíz quedará en SOLO LECTURA."
echo "    Los cambios en '/' se guardan en RAM y se"
echo "    PIERDEN al reiniciar. Esto es lo deseado."
echo ""
echo "  • ~/oceankind/logs y clips → /tmp (RAM)."
echo "    Los logs del último boot se ven con:"
echo "    journalctl -u oceankind"
echo ""
echo "  • Para actualizaciones OTA, usa el script:"
echo "    bash ~/oceankind/update_oceankind.sh"
echo "    (deshabilita overlay, actualiza, y lo re-habilita)"
echo ""
echo "  • Para acceso manual de escritura:"
echo "    sudo raspi-config nonint do_overlayfs 1"
echo "    sudo reboot   ← luego de los cambios:"
echo "    sudo raspi-config nonint do_overlayfs 0 && sudo reboot"
echo ""
echo "  Reinicia ahora: sudo reboot"
echo "=============================================="
