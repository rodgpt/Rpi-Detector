#!/bin/bash
# =============================================================================
# OceanKind — Script de actualización OTA
# Descarga los últimos cambios del repositorio y reinicia el servicio.
#
# Uso manual:   bash ~/oceankind/update_oceankind.sh
# Cron diario:  0 3 * * * bash ~/oceankind/update_oceankind.sh >> /tmp/oceankind/logs/update.log 2>&1
#
# Comportamiento con overlay filesystem activo:
#   Fase 1 (overlay ON):  Deshabilita overlay, crea servicio one-shot, reinicia.
#   Fase 2 (overlay OFF): Hace el update real, re-habilita overlay, reinicia.
# =============================================================================

set -e

REPO_DIR="$HOME/oceankind/code"
SERVICE_NAME="oceankind"

# El servicio corre desde el venv que crea setup.sh (ExecStart apunta ahí).
# Las dependencias DEBEN instalarse en ese mismo intérprete: un `pip3
# --break-system-packages` instalaría en el Python del sistema, que el servicio
# ya no usa — el update diría "dependencias actualizadas" y el servicio seguiría
# con las viejas. Además falla en trixie (pip no puede desinstalar paquetes de
# dpkg). Si el venv no existe (unidad provisionada antes de este cambio), se
# avisa fuerte y se conserva el comportamiento anterior.
VENV_PY="$HOME/oceankind/venv/bin/python"
pip_install() {
    if [ -x "$VENV_PY" ]; then
        "$VENV_PY" -m pip install -r "$1" -q
    else
        echo "[$TIMESTAMP] AVISO: no hay venv en $VENV_PY — instalando en el Python"
        echo "[$TIMESTAMP]        del sistema. Re-provisiona con setup.sh."
        pip3 install -r "$1" --break-system-packages -q
    fi
}
LOG_FILE="/tmp/oceankind/logs/update.log"
ONESHOT_FLAG="/var/tmp/oceankind_update_pending"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p /tmp/oceankind/logs

echo ""
echo "[$TIMESTAMP] === OceanKind OTA Update ==="

# ── Detectar si el overlay filesystem está activo ────────────────────────────
overlay_enabled() {
    # raspi-config devuelve 0 si overlay está ON, 1 si OFF
    raspi-config nonint get_overlayfs 2>/dev/null | grep -q "^0$"
}

# ── FASE 2: Venimos de un reboot con overlay desactivado para actualizar ──────
if [ -f "$ONESHOT_FLAG" ]; then
    echo "[$TIMESTAMP] Fase 2: overlay desactivado — ejecutando actualización..."
    rm -f "$ONESHOT_FLAG"

    # Deshabilitar el servicio one-shot para que no vuelva a correr
    sudo systemctl disable oceankind-update.service 2>/dev/null || true

    if [ ! -d "$REPO_DIR/.git" ]; then
        echo "[$TIMESTAMP] ERROR: No se encontró repositorio git en $REPO_DIR"
        sudo raspi-config nonint do_overlayfs 0
        exit 1
    fi

    cd "$REPO_DIR"
    CURRENT=$(git rev-parse --short HEAD)
    echo "[$TIMESTAMP] Versión actual: $CURRENT"

    git fetch origin main 2>&1
    REMOTE=$(git rev-parse --short origin/main)

    if [ "$CURRENT" = "$REMOTE" ]; then
        echo "[$TIMESTAMP] Ya en la versión más reciente ($CURRENT) — sin cambios."
    else
        echo "[$TIMESTAMP] Actualizando: $CURRENT → $REMOTE"
        git pull origin main 2>&1

        if [ -f "$REPO_DIR/requirements.txt" ]; then
            echo "[$TIMESTAMP] Actualizando dependencias..."
            pip_install "$REPO_DIR/requirements.txt"
        fi
        echo "[$TIMESTAMP] ✓ Código actualizado a $REMOTE"
    fi

    # Re-habilitar overlay filesystem
    echo "[$TIMESTAMP] Re-habilitando overlay filesystem..."
    sudo raspi-config nonint do_overlayfs 0
    echo "[$TIMESTAMP] ✓ Overlay re-habilitado — reiniciando para aplicar..."
    sudo reboot
    exit 0
fi

# ── FASE 1: Ejecución normal ──────────────────────────────────────────────────
if [ ! -d "$REPO_DIR/.git" ]; then
    echo "[$TIMESTAMP] ERROR: No se encontró repositorio git en $REPO_DIR"
    echo "[$TIMESTAMP] Clona primero el repo: git clone <url> $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"

# Verificar si hay cambios disponibles
CURRENT=$(git rev-parse --short HEAD)
echo "[$TIMESTAMP] Versión actual: $CURRENT"

git fetch origin main 2>&1
REMOTE=$(git rev-parse --short origin/main)

if [ "$CURRENT" = "$REMOTE" ]; then
    echo "[$TIMESTAMP] Ya en la versión más reciente ($CURRENT) — sin cambios."
    exit 0
fi

echo "[$TIMESTAMP] Nueva versión disponible: $CURRENT → $REMOTE"

# ── Si el overlay está activo: deshabilitar y reiniciar para actualizar ───────
if overlay_enabled; then
    echo "[$TIMESTAMP] Overlay filesystem detectado — iniciando proceso de actualización en 2 fases."
    echo "[$TIMESTAMP] Fase 1: Deshabilitando overlay y reiniciando..."

    # Flag para que la Fase 2 sepa que debe continuar
    touch "$ONESHOT_FLAG"

    # Crear servicio one-shot que ejecuta la Fase 2 al arrancar
    sudo tee /etc/systemd/system/oceankind-update.service > /dev/null << SERVICE_EOF
[Unit]
Description=OceanKind OTA Update (Fase 2)
After=network-online.target
Wants=network-online.target
ConditionPathExists=${ONESHOT_FLAG}

[Service]
Type=oneshot
User=${USER}
ExecStart=/bin/bash ${HOME}/oceankind/update_oceankind.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    sudo systemctl daemon-reload
    sudo systemctl enable oceankind-update.service

    # Deshabilitar overlay (requiere reboot para tener efecto)
    sudo raspi-config nonint do_overlayfs 1

    echo "[$TIMESTAMP] Reiniciando en 5 segundos para aplicar..."
    sleep 5
    sudo reboot
    exit 0
fi

# ── Sin overlay: actualizar directamente ─────────────────────────────────────
echo "[$TIMESTAMP] Actualizando código..."
git pull origin main 2>&1

if [ -f "$REPO_DIR/requirements.txt" ]; then
    echo "[$TIMESTAMP] Actualizando dependencias..."
    pip_install "$REPO_DIR/requirements.txt"
fi

# Reiniciar servicio
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "[$TIMESTAMP] Reiniciando servicio $SERVICE_NAME..."
    sudo systemctl restart "$SERVICE_NAME"
    sleep 3
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "[$TIMESTAMP] ✓ Servicio reiniciado correctamente"
    else
        echo "[$TIMESTAMP] ERROR: El servicio no arrancó — revisar con: journalctl -u $SERVICE_NAME -n 30"
        exit 1
    fi
else
    echo "[$TIMESTAMP] Servicio $SERVICE_NAME no estaba activo — no se reinició"
fi

echo "[$TIMESTAMP] ✓ Actualización completada: $CURRENT → $REMOTE"
