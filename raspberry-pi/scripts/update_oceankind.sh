#!/bin/bash
# =============================================================================
# OceanKind — Actualización OTA con verificación y rollback
#
# Uso manual:   bash ~/oceankind/update_oceankind.sh
# Cron diario:  0 3 * * * bash ~/oceankind/update_oceankind.sh >> /tmp/oceankind/logs/update.log 2>&1
#
# ── Layout (ÚNICA definición; setup.sh instala este script en ~/oceankind) ────
#   ~/oceankind/code/      checkout git — la FUENTE de las actualizaciones OTA
#   ~/oceankind/*.py       lo que systemd ejecuta realmente (copia instalada)
#   ~/oceankind/oceankind/ el paquete Python instalado
#   ~/oceankind/venv/      las dependencias del servicio
#
# El punto que la versión anterior de este script no tenía: `git pull` NO
# actualiza el servicio. systemd ejecuta la COPIA en ~/oceankind, no el
# checkout. Sin el paso de instalación, la OTA hacía pull, reiniciaba, y
# anunciaba "✓ Actualización completada" mientras la unidad seguía corriendo el
# código viejo. Un update que miente es peor que uno que falla.
#
# ── Contrato de rollback ─────────────────────────────────────────────────────
# Toda actualización se verifica antes de darse por buena: el servicio debe
# seguir activo Y no haber reiniciado ni una vez durante SETTLE_S segundos.
# `Restart=always` significa que "activo" por sí solo NO prueba nada — un
# servicio en bucle de crash se ve activo a ratos. Por eso se cuenta NRestarts.
# Si la verificación falla se vuelve al commit anterior, se reinstala y se
# vuelve a verificar. Si el rollback TAMBIÉN falla, se grita y se sale con
# error: no hay nada más que este script pueda hacer sin manos en el sitio.
#
# El SHA que falló se anota en .ota_failed_sha y NO se reintenta. Sin eso, un
# commit malo se reintentaría cada noche para siempre en una unidad sin acceso.
#
# Comportamiento con overlay filesystem activo:
#   Fase 1 (overlay ON):  Deshabilita overlay, crea servicio one-shot, reinicia.
#   Fase 2 (overlay OFF): Update real + verificación + rollback, re-habilita
#                         overlay, reinicia.
# =============================================================================

set -e

OCEANKIND_DIR="$HOME/oceankind"
REPO_DIR="$OCEANKIND_DIR/code"
REPO_URL="${OCEANKIND_REPO_URL:-https://github.com/rodgpt/Rpi-Detector.git}"
REPO_BRANCH="${OCEANKIND_REPO_BRANCH:-main}"

SRC_DIR="$REPO_DIR/raspberry-pi/src"
REQ_FILE="$REPO_DIR/raspberry-pi/requirements.txt"
MODEL_FILE="$REPO_DIR/raspberry-pi/models/model.joblib"
VENV_PY="$OCEANKIND_DIR/venv/bin/python"

SERVICE_NAME="oceankind"
LOG_FILE="/tmp/oceankind/logs/update.log"
ONESHOT_FLAG="/var/tmp/oceankind_update_pending"
FAILED_SHA_FILE="$OCEANKIND_DIR/.ota_failed_sha"
# Qué commit está REALMENTE instalado. No se puede usar el HEAD del checkout:
# el checkout no es lo que corre, la copia en ~/oceankind sí. Una unidad
# provisionada por rsync tiene un clone recién hecho ya en origin/main mientras
# lo instalado viene de otro sitio — sin este fichero la OTA diría "ya
# actualizado" y no reconciliaría nunca esa diferencia.
INSTALLED_SHA_FILE="$OCEANKIND_DIR/.installed_sha"

# Ventana de verificación. RestartSec=15 en la unidad, así que 60 s deja pasar
# ~4 intentos de reinicio: suficiente para que un crash de arranque se vea.
SETTLE_S="${OCEANKIND_OTA_SETTLE_S:-60}"

mkdir -p /tmp/oceankind/logs

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

echo ""
log "=== OceanKind OTA Update ==="

# ── Detectar si el overlay filesystem está activo ────────────────────────────
overlay_enabled() {
    # raspi-config devuelve 0 si overlay está ON, 1 si OFF
    raspi-config nonint get_overlayfs 2>/dev/null | grep -q "^0$"
}

# ── Dependencias: SIEMPRE en el venv del servicio ────────────────────────────
# Un `pip3 --break-system-packages` instalaría en el Python del sistema, que el
# servicio ya no usa: el update diría "dependencias actualizadas" y el servicio
# seguiría con las viejas. Además falla en trixie (pip no puede desinstalar
# paquetes de dpkg). Si no hay venv, se avisa fuerte y se conserva el
# comportamiento anterior.
pip_install() {
    if [ -x "$VENV_PY" ]; then
        "$VENV_PY" -m pip install -r "$1" -q
    else
        log "AVISO: no hay venv en $VENV_PY — instalando en el Python del"
        log "       sistema. Re-provisiona con setup.sh."
        pip3 install -r "$1" --break-system-packages -q
    fi
}

# ── Checkout git: se auto-clona si falta ─────────────────────────────────────
# La unidad se provisiona por rsync (ver BENCH.md §2), así que tras un setup.sh
# limpio no hay .git en ninguna parte. La OTA se arranca sola en vez de exigir
# un clone manual en una unidad que nadie puede tocar.
ensure_repo() {
    if [ -d "$REPO_DIR/.git" ]; then
        return 0
    fi
    log "No hay checkout en $REPO_DIR — clonando $REPO_URL ($REPO_BRANCH)..."
    rm -rf "$REPO_DIR"
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR" 2>&1
    log "✓ Checkout creado en $REPO_DIR"
}

# ── Instalación: del checkout a lo que systemd ejecuta ───────────────────────
# Este es el paso que faltaba. Refleja exactamente lo que hace setup.sh §5.
install_code() {
    # Un checkout roto o a medias NO debe borrar la instalación que funciona.
    if [ ! -f "$SRC_DIR/marfutura_iot_audio.py" ] || [ ! -d "$SRC_DIR/oceankind" ]; then
        log "ERROR: checkout inválido — falta $SRC_DIR/marfutura_iot_audio.py"
        log "       o el paquete oceankind/. No se toca la instalación actual."
        return 1
    fi

    cp "$SRC_DIR"/*.py "$OCEANKIND_DIR/"
    rm -rf "$OCEANKIND_DIR/oceankind"
    cp -R "$SRC_DIR/oceankind" "$OCEANKIND_DIR/oceankind"
    [ -f "$MODEL_FILE" ] && cp "$MODEL_FILE" "$OCEANKIND_DIR/model.joblib"

    if [ -f "$REQ_FILE" ]; then
        log "Actualizando dependencias..."
        pip_install "$REQ_FILE"
    else
        log "AVISO: no se encontró $REQ_FILE — dependencias sin tocar."
    fi

    git -C "$REPO_DIR" rev-parse --short HEAD > "$INSTALLED_SHA_FILE"
    return 0
}

# Lo instalado, no lo que haya en el checkout. Vacío si nunca lo escribió esta
# OTA (unidad provisionada por rsync + setup.sh): entonces se fuerza el deploy
# para reconciliar, en vez de asumir que coinciden.
installed_sha() {
    [ -f "$INSTALLED_SHA_FILE" ] && cat "$INSTALLED_SHA_FILE" || echo ""
}

# ── Verificación ─────────────────────────────────────────────────────────────
service_restarts() {
    systemctl show -p NRestarts --value "$SERVICE_NAME" 2>/dev/null || echo 0
}

restart_and_verify() {
    local before after

    log "Reiniciando $SERVICE_NAME..."
    sudo systemctl restart "$SERVICE_NAME"

    # La línea base se toma DESPUÉS del restart, no antes: systemd pone
    # NRestarts a cero en un arranque manual, así que comparar contra el valor
    # previo daría un falso fallo en cualquier unidad que ya hubiera reiniciado
    # alguna vez — y revertiría una actualización buena.
    before=$(service_restarts)

    log "Verificando durante ${SETTLE_S}s (activo + sin reinicios)..."
    sleep "$SETTLE_S"

    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
        log "FALLO: $SERVICE_NAME no está activo tras ${SETTLE_S}s."
        return 1
    fi

    after=$(service_restarts)
    if [ "$after" != "$before" ]; then
        log "FALLO: $SERVICE_NAME reinició $before → $after durante la ventana."
        log "       Está en bucle de crash aunque systemctl lo muestre activo."
        return 1
    fi

    log "✓ Servicio estable (${SETTLE_S}s, sin reinicios)"
    return 0
}

# ── Desplegar un commit concreto y verificarlo ───────────────────────────────
deploy() {
    local sha="$1"
    log "Desplegando $sha..."
    # reset --hard, no pull: el checkout puede haber quedado en HEAD separado
    # tras un rollback anterior, y ahí `git pull` falla.
    git -C "$REPO_DIR" reset --hard "$sha" 2>&1
    install_code || return 1
    restart_and_verify || return 1
    return 0
}

# ── El update completo, con rollback. Usado por ambas fases ──────────────────
do_update() {
    ensure_repo

    local current target previous
    current=$(installed_sha)
    log "Versión instalada: ${current:-desconocida (provisionada fuera de la OTA)}"

    git -C "$REPO_DIR" fetch origin "$REPO_BRANCH" 2>&1
    target=$(git -C "$REPO_DIR" rev-parse --short "origin/$REPO_BRANCH")

    if [ -n "$current" ] && [ "$current" = "$target" ]; then
        log "Ya en la versión más reciente ($current) — sin cambios."
        return 0
    fi

    # Un commit que ya rompió esta unidad no se reintenta solo.
    if [ -f "$FAILED_SHA_FILE" ] && [ "$(cat "$FAILED_SHA_FILE")" = "$target" ]; then
        log "El commit $target ya falló en esta unidad y se revirtió."
        log "NO se reintenta automáticamente. Corrige el commit, o borra"
        log "$FAILED_SHA_FILE a mano para forzar otro intento."
        return 0
    fi

    # Destino del rollback. Si no se sabe qué está instalado (primera OTA sobre
    # una unidad provisionada por rsync), lo mejor disponible es el HEAD del
    # checkout ANTES de moverlo — nunca una cadena vacía, que rompería el
    # `git reset` del rollback justo cuando más falta hace.
    previous="${current:-$(git -C "$REPO_DIR" rev-parse --short HEAD)}"
    log "Nueva versión disponible: ${current:-?} → $target"

    if deploy "$target"; then
        rm -f "$FAILED_SHA_FILE"
        log "✓ Actualización completada y verificada: $previous → $target"
        return 0
    fi

    # ── Rollback ─────────────────────────────────────────────────────────────
    log "✗ $target falló la verificación — revirtiendo a $previous"
    echo "$target" > "$FAILED_SHA_FILE"

    if deploy "$previous"; then
        log "✓ Rollback correcto — la unidad sigue en $previous"
        log "  El commit $target queda marcado como fallido."
        return 0
    fi

    log "════════════════════════════════════════════════════════════"
    log "FALLO CRÍTICO: $target rompió el servicio y el rollback a"
    log "$previous TAMPOCO arrancó. La unidad NO está corriendo."
    log "Requiere intervención manual: journalctl -u $SERVICE_NAME -n 50"
    log "════════════════════════════════════════════════════════════"
    return 1
}

# ── FASE 2: venimos de un reboot con overlay desactivado para actualizar ─────
if [ -f "$ONESHOT_FLAG" ]; then
    log "Fase 2: overlay desactivado — ejecutando actualización..."
    rm -f "$ONESHOT_FLAG"

    # Deshabilitar el servicio one-shot para que no vuelva a correr
    sudo systemctl disable oceankind-update.service 2>/dev/null || true

    # La verificación ocurre AQUÍ, con el overlay aún desactivado. Re-habilitar
    # el overlay antes de verificar (lo que hacía la versión anterior) deja la
    # unidad en un estado donde el rollback ya no puede escribir nada.
    UPDATE_RC=0
    do_update || UPDATE_RC=$?

    log "Re-habilitando overlay filesystem..."
    sudo raspi-config nonint do_overlayfs 0
    log "✓ Overlay re-habilitado — reiniciando para aplicar..."
    sudo reboot
    exit "$UPDATE_RC"
fi

# ── FASE 1: ejecución normal ─────────────────────────────────────────────────
ensure_repo

# Pre-vuelo: sólo sirve para decidir si vale la pena el ciclo de reboot del
# overlay. La comprobación autoritativa vive en do_update().
if overlay_enabled; then
    CURRENT=$(installed_sha)
    git -C "$REPO_DIR" fetch origin "$REPO_BRANCH" 2>&1
    REMOTE=$(git -C "$REPO_DIR" rev-parse --short "origin/$REPO_BRANCH")

    if [ -n "$CURRENT" ] && [ "$CURRENT" = "$REMOTE" ]; then
        log "Ya en la versión más reciente ($CURRENT) — sin cambios."
        exit 0
    fi
    if [ -f "$FAILED_SHA_FILE" ] && [ "$(cat "$FAILED_SHA_FILE")" = "$REMOTE" ]; then
        log "El commit $REMOTE ya falló en esta unidad y se revirtió."
        log "NO se reintenta. Borra $FAILED_SHA_FILE para forzar otro intento."
        exit 0
    fi

    log "Nueva versión disponible: $CURRENT → $REMOTE"
    log "Overlay filesystem detectado — actualización en 2 fases."
    log "Fase 1: deshabilitando overlay y reiniciando..."

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
# La Fase 2 verifica el arranque durante SETTLE_S y puede hacer rollback, así
# que tarda minutos, no segundos. Sin esto systemd la mataría a mitad.
TimeoutStartSec=900
ExecStart=/bin/bash ${OCEANKIND_DIR}/update_oceankind.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    sudo systemctl daemon-reload
    sudo systemctl enable oceankind-update.service

    # Deshabilitar overlay (requiere reboot para tener efecto)
    sudo raspi-config nonint do_overlayfs 1

    log "Reiniciando en 5 segundos para aplicar..."
    sleep 5
    sudo reboot
    exit 0
fi

# ── Sin overlay: actualizar directamente, con verificación y rollback ────────
do_update
