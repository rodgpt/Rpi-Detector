#!/bin/bash
# =============================================================================
# OceanKind — Actualización OTA con verificación y rollback, a prueba de cortes
#
# Desde el panel:  el servicio (oceankind/ota.py) lanza
#                  update_oceankind.sh --ref <tag>  al ver una release asignada
#                  en su configuración firmada (D-021). Es el camino normal.
# Timer diario:    oceankind-ota.timer → update_oceankind.sh  (sin argumento:
#                  converge a .assigned_release, o pregunta al backend si ese
#                  fichero no está). Red de seguridad para una unidad caída.
# Manual:          bash ~/oceankind/update_oceankind.sh [--ref <tag>]
# Al arrancar:     oceankind-ota-boot.service  →  update_oceankind.sh --boot
#                  (lo instala setup.sh; repara una OTA que se cortó y ejecuta
#                  la ventana de mantenimiento en unidades con overlay)
#
# ── Releases asignadas (D-021) ───────────────────────────────────────────────
# El OTA ya NO sigue una rama. El objetivo es siempre un ref concreto (un tag
# git), decidido en el panel por unidad. Sin asignación no hay actualización.
#   --ref <tag>           lo que el servicio pide; manda sobre todo lo demás
#   .assigned_release     "<tag|-> <hold 0|1>", escrito por el servicio al
#                         verificar una config; lo lee el timer
#   backend               GET /api/devices/config con la credencial de
#                         /etc/oceankind.env, cuando el fichero no está (una
#                         unidad con overlay lo pierde al reiniciar a la
#                         ventana de mantenimiento)
# Al confirmar se escriben .installed_sha Y .installed_release; al fallar la
# verificación, .ota_failed_sha Y .ota_failed_release. El servicio los lee
# para status.json → ota y para no relanzar un tag que ya rompió esta unidad.
#
# ── Layout (ÚNICA definición; setup.sh instala este script en ~/oceankind) ────
#   ~/oceankind/code/      checkout git — la FUENTE de las actualizaciones OTA
#   ~/oceankind/*.py       lo que systemd ejecuta realmente (copia instalada)
#   ~/oceankind/oceankind/ el paquete Python instalado
#   ~/oceankind/venv/      las dependencias del servicio
#   ~/oceankind/.snapshot/ copia local de la última instalación buena
#   ~/oceankind/.ota_journal   existe SÓLO mientras una OTA está a medias
#
# `git pull` NO actualiza el servicio: systemd ejecuta la COPIA en
# ~/oceankind, no el checkout. Un update que miente es peor que uno que falla.
#
# ── Contrato de rollback ─────────────────────────────────────────────────────
# Toda actualización se verifica antes de darse por buena: el servicio debe
# seguir activo Y no haber reiniciado ni una vez durante SETTLE_S segundos.
# `Restart=always` significa que "activo" por sí solo NO prueba nada.
# Si la verificación falla se restaura el snapshot y se vuelve a verificar. Si
# el rollback TAMBIÉN falla, se grita y se sale con error.
# El SHA que falló se anota en .ota_failed_sha y NO se reintenta.
#
# ── Contrato ante cortes (luz, red, kernel) ──────────────────────────────────
# La unidad puede perder la alimentación en CUALQUIER punto de este script.
# Probado exhaustivamente por tools/ota_fault_test.sh, que corta el script
# antes/después de cada comando. Lo que lo hace posible:
#
#   1. JOURNAL. Antes de tocar la instalación se escribe .ota_journal
#      (installing → verifying → rollingback). Existe si y sólo si hay una OTA
#      a medias. Se escribe atómicamente (temporal + sync + rename).
#   2. SNAPSHOT. Antes del journal se copia el árbol instalado a .snapshot.
#      Invariante: journal presente ⇒ snapshot completo. El rollback restaura
#      de ahí: no necesita git, ni pip, ni red — la red suele ser lo que se
#      cae justo cuando hace falta el rollback.
#   3. .installed_sha se escribe SÓLO al confirmar (build verificado), nunca
#      antes. Un build sin verificar jamás figura como instalado.
#   4. RECUPERACIÓN. Al arrancar (--boot) y al inicio de cada ejecución, un
#      journal que sobrevive significa "una OTA se cortó":
#        installing → restaurar snapshot (no es culpa del commit: se reintenta)
#        verifying  → re-verificar; bueno → confirmar, malo → marcar y revertir
#        rollingback→ terminar la restauración
#   5. Errores explícitos. Sin `set -e`: dentro de una función llamada desde
#      `||` o `if`, set -e se ignora en silencio — así fallaban pip o cp sin
#      que nadie se enterase y la OTA anunciaba éxito. Cada paso comprueba.
#   6. Un solo actualizador a la vez (bloqueo por mkdir + pid vivo).
#
# ── Overlay filesystem (unidades protegidas: existe .sd_protection) ──────────
# Con el overlay activo TODO lo que se escribe en la raíz se pierde al
# reiniciar — incluido cualquier "flag" o unidad one-shot creada para la fase
# 2. Por eso la señal de "hay que actualizar" no es un fichero: es el propio
# estado del overlay.
#   Fase 1 (cron, overlay ON): sólo lee y hace fetch (a RAM). Si hay versión
#     nueva, desactiva el overlay y reinicia.
#   Mantenimiento (arranque con overlay OFF + .sd_protection): la unidad
#     permanente oceankind-ota-boot.service (instalada por setup.sh, persiste)
#     ejecuta la actualización con verificación y rollback, y SIEMPRE termina
#     re-habilitando el overlay — aunque la actualización falle o no haya red.
#   Si se corta la luz en la ventana, el siguiente arranque vuelve a la
#     ventana (overlay sigue OFF) y la retoma. Máximo MAX_MAINT arranques de
#     mantenimiento: después se abandona la OTA y se protege la SD igualmente.
#   Mantenimiento manual: `touch ~/oceankind/.hold_maintenance` mientras el
#     overlay está OFF impide que la unidad lo re-habilite bajo tus pies.
#   Sin oceankind-ota-boot.service el script se NIEGA a desactivar el overlay:
#     nadie lo volvería a activar.
# =============================================================================

# NO `set -e`: ver punto 5. `set -u` sí, para no usar variables sin definir.
set -u

OCEANKIND_DIR="$HOME/oceankind"
REPO_DIR="$OCEANKIND_DIR/code"
REPO_URL="${OCEANKIND_REPO_URL:-https://github.com/rodgpt/Rpi-Detector.git}"
REPO_BRANCH="${OCEANKIND_REPO_BRANCH:-main}"

SRC_DIR="$REPO_DIR/raspberry-pi/src"
REQ_FILE="$REPO_DIR/raspberry-pi/requirements.txt"
MODEL_FILE="$REPO_DIR/raspberry-pi/models/model.joblib"
VENV_PY="$OCEANKIND_DIR/venv/bin/python"

SERVICE_NAME="oceankind"
BOOT_UNIT="oceankind-ota-boot.service"
UNIT_DIR="${OCEANKIND_UNIT_DIR:-/etc/systemd/system}"
RUN_DIR="${OCEANKIND_RUN_DIR:-/tmp/oceankind}"

FAILED_SHA_FILE="$OCEANKIND_DIR/.ota_failed_sha"
FAILED_REF_FILE="$OCEANKIND_DIR/.ota_failed_release"
# Qué commit está REALMENTE instalado y VERIFICADO. No el HEAD del checkout: el
# checkout no es lo que corre. Vacío en una unidad provisionada por setup.sh:
# se fuerza el deploy para reconciliar, en vez de asumir que coinciden.
INSTALLED_SHA_FILE="$OCEANKIND_DIR/.installed_sha"
INSTALLED_REF_FILE="$OCEANKIND_DIR/.installed_release"
ASSIGNED_FILE="$OCEANKIND_DIR/.assigned_release"
ENV_FILE="${OCEANKIND_ENV_FILE:-/etc/oceankind.env}"
# El ref objetivo de ESTA ejecución (tag). Lo fija --ref o resolve_target.
TARGET_REF=""
# Sólo esto puede ser un nombre de release (contrato §Device configuration):
# acaba en una línea de comandos de git, y esta expresión es lo que lo hace seguro.
REF_RE='^v?[0-9A-Za-z][0-9A-Za-z._-]{0,63}$'
JOURNAL="$OCEANKIND_DIR/.ota_journal"
SNAP_DIR="$OCEANKIND_DIR/.snapshot"
SD_MARK="$OCEANKIND_DIR/.sd_protection"
HOLD_MARK="$OCEANKIND_DIR/.hold_maintenance"
ATTEMPTS_FILE="$OCEANKIND_DIR/.ota_attempts"

# Ventana de verificación. RestartSec=15 en la unidad, así que 60 s deja pasar
# ~4 intentos de reinicio: suficiente para que un crash de arranque se vea.
SETTLE_S="${OCEANKIND_OTA_SETTLE_S:-60}"
MAX_MAINT="${OCEANKIND_OTA_MAX_MAINT:-3}"

# Un fetch/clone/pip sin timeout es una ejecución que no termina nunca y, con
# el bloqueo, impide todas las siguientes. Explícito en cada llamada de red.
GIT_NET="-c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30"

mkdir -p "$RUN_DIR/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# `timeout` de coreutils si existe (siempre en la Pi); si no, sin él.
tmo() { if command -v timeout >/dev/null 2>&1; then timeout "$@"; else shift; "$@"; fi; }

# ── Bloqueo: un solo actualizador ────────────────────────────────────────────
HAVE_LOCK=0
acquire_lock() {
    local lock="$RUN_DIR/ota.lock" pid
    if mkdir "$lock" 2>/dev/null; then
        echo $$ > "$lock/pid"; HAVE_LOCK=1; return 0
    fi
    pid=$(cat "$lock/pid" 2>/dev/null || echo "")
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "Otra actualización en curso (pid $pid) — no se toca nada."
        return 1
    fi
    log "Bloqueo huérfano (pid ${pid:-?} ya no existe) — se retoma."
    rm -rf "$lock"
    mkdir "$lock" 2>/dev/null || return 1
    echo $$ > "$lock/pid"; HAVE_LOCK=1
}
release_lock() { [ "$HAVE_LOCK" = 1 ] && rm -rf "$RUN_DIR/ota.lock"; return 0; }

# ── Escritura atómica ante cortes de luz ─────────────────────────────────────
atomic_write() {   # $1 fichero  $2 contenido
    printf '%s\n' "$2" > "$1.tmp" || return 1
    sync
    mv -f "$1.tmp" "$1" || return 1
    sync
}

overlay_active() {
    # ¿La raíz ES el overlay en RAM AHORA? Se mira el montaje real, no la
    # configuración: `raspi-config` cambia la configuración inmediatamente pero
    # el efecto llega en el siguiente arranque.
    [ "$(findmnt -n -o FSTYPE / 2>/dev/null)" = overlay ] && return 0
    grep -qE 'boot=overlay|overlayroot' /proc/cmdline 2>/dev/null
}

# ── Dependencias: SIEMPRE en el venv del servicio ────────────────────────────
pip_install() {
    if [ -x "$VENV_PY" ]; then
        tmo 900 "$VENV_PY" -m pip install --timeout 30 --retries 2 -r "$1" -q
    else
        log "AVISO: no hay venv en $VENV_PY — instalando en el Python del"
        log "       sistema. Re-provisiona con setup.sh."
        tmo 900 pip3 install --timeout 30 --retries 2 -r "$1" --break-system-packages -q
    fi
}

# ── Checkout git: es una caché, se recrea si hace falta ──────────────────────
repo_ok() {
    [ -d "$REPO_DIR/.git" ] && \
        git -C "$REPO_DIR" rev-parse --verify -q 'HEAD^{commit}' >/dev/null 2>&1
}

clone_repo() {
    # Se clona aparte y se intercambia: si la red se cae a mitad, el checkout
    # anterior sigue ahí.
    rm -rf "$REPO_DIR.new"
    if ! tmo 600 git $GIT_NET clone --quiet --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR.new" 2>&1; then
        rm -rf "$REPO_DIR.new"
        return 1
    fi
    rm -rf "$REPO_DIR"
    mv "$REPO_DIR.new" "$REPO_DIR"
}

ensure_repo() {
    # Tenemos el bloqueo: cualquier index.lock es resto de un corte.
    rm -f "$REPO_DIR/.git/index.lock" 2>/dev/null
    repo_ok && return 0
    log "Checkout ausente o dañado en $REPO_DIR — recreando $REPO_URL ($REPO_BRANCH)..."
    if clone_repo; then
        log "✓ Checkout creado en $REPO_DIR"
        return 0
    fi
    log "ERROR: no se pudo clonar (¿sin red?). No se toca la instalación."
    return 1
}

fetch_target() {
    # Tags, no una rama: el objetivo es un ref concreto (D-021). --force porque
    # un tag movido en el remoto debe ganar al local; --prune-tags para no
    # conservar tags borrados.
    if tmo 300 git $GIT_NET -C "$REPO_DIR" fetch --quiet --force --tags --prune --prune-tags origin 2>&1; then
        return 0
    fi
    log "fetch falló."
    # ¿Red caída, o checkout con objetos corruptos? Se distingue con ls-remote.
    if tmo 60 git $GIT_NET ls-remote --exit-code --heads "$REPO_URL" "$REPO_BRANCH" >/dev/null 2>&1; then
        log "Hay red pero el fetch falla: checkout dañado — se recrea."
        clone_repo && return 0
    else
        log "Sin red — se reintenta en la próxima ejecución."
    fi
    return 1
}

installed_sha() { [ -f "$INSTALLED_SHA_FILE" ] && cat "$INSTALLED_SHA_FILE" || echo ""; }
installed_ref() { [ -f "$INSTALLED_REF_FILE" ] && cat "$INSTALLED_REF_FILE" || echo ""; }
failed_ref()    { [ -f "$FAILED_REF_FILE" ] && cat "$FAILED_REF_FILE" || echo ""; }

valid_ref() { printf '%s' "$1" | grep -Eq "$REF_RE"; }

# ── Qué release toca (D-021) ─────────────────────────────────────────────────
# Imprime el tag asignado, "HOLD" si la unidad está retenida, o nada si no hay
# asignación. Orden: --ref (ya en TARGET_REF) › .assigned_release › backend.
assigned_from_file() {
    [ -f "$ASSIGNED_FILE" ] || return 1
    local rel hold
    read -r rel hold < "$ASSIGNED_FILE" || return 1
    [ "${hold:-0}" = 1 ] && { echo HOLD; return 0; }
    [ -n "$rel" ] && [ "$rel" != "-" ] && { echo "$rel"; return 0; }
    echo ""; return 0
}

assigned_from_backend() {
    # Misma derivación que oceankind/config.py y el backend (§Provisioning
    # secret): auth_key = HMAC-SHA256(secret, "oceankind:auth:v1"). La firma del
    # documento no se verifica aquí (eso lo hace el servicio en Python); este
    # valor sólo elige un tag que ya existe en el repositorio que la unidad ya
    # confía, y pasa por REF_RE antes de tocar git.
    [ -r "$ENV_FILE" ] || return 1
    local url secret id key doc
    url=$(sed -n 's/^[[:space:]]*OCEANKIND_BACKEND_URL=//p' "$ENV_FILE" | tail -1 | tr -d '"'"'"' ')
    secret=$(sed -n 's/^[[:space:]]*OCEANKIND_DEVICE_SECRET=//p' "$ENV_FILE" | tail -1 | tr -d '"'"'"' ')
    id=$(sed -n 's/^[[:space:]]*OCEANKIND_DEVICE_ID=//p' "$ENV_FILE" | tail -1 | tr -d '"'"'"' ')
    [ -n "$url" ] && [ -n "$secret" ] && [ -n "$id" ] || return 1
    command -v openssl >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 || return 1
    key=$(printf 'oceankind:auth:v1' | openssl dgst -sha256 -hmac "$secret" 2>/dev/null | awk '{print $NF}')
    [ -n "$key" ] || return 1
    doc=$(tmo 30 curl -fsS --max-time 20 -H "X-Device-Id: $id" -H "X-Device-Key: $key" \
          -H "User-Agent: oceankind-ota" "${url%/}/api/devices/config" 2>/dev/null) || return 1
    printf '%s' "$doc" | python3 -c '
import json, sys
try:
    c = json.load(sys.stdin).get("config") or {}
except Exception:
    sys.exit(1)
print("HOLD" if c.get("hold") else (c.get("release") or ""))
' 2>/dev/null
}

resolve_target() {
    # Deja TARGET_REF fijado, o devuelve: 0 = nada que hacer (sin asignación /
    # retenida), 1 = objetivo listo, 2 = no se pudo resolver.
    local src="argumento" rel
    if [ -z "$TARGET_REF" ]; then
        if rel=$(assigned_from_file); then
            src=".assigned_release"
        elif rel=$(assigned_from_backend); then
            src="backend"
        else
            log "Sin asignación conocida (ni $ASSIGNED_FILE ni respuesta del backend)."
            log "Nada que hacer: el OTA sólo converge a una release asignada desde el panel (D-021)."
            return 0
        fi
        if [ "$rel" = HOLD ]; then
            log "Unidad RETENIDA desde el panel (hold) — no se actualiza."
            return 0
        fi
        if [ -z "$rel" ]; then
            log "Sin release asignada ($src) — la unidad conserva lo que corre."
            return 0
        fi
        TARGET_REF="$rel"
    fi
    if ! valid_ref "$TARGET_REF"; then
        log "ERROR: '$TARGET_REF' ($src) no tiene forma de tag — se ignora."
        return 2
    fi
    log "Release objetivo: $TARGET_REF ($src)"
    return 1
}

check_env_generation() {
    # D-020: la variable vieja sin el secreto nuevo hace que la unidad se niegue
    # a arrancar en cualquier release con D-020. Decirlo ANTES de desplegar,
    # aunque el rollback lo cubra: un rollback es un reinicio que nadie pidió.
    [ -r "$ENV_FILE" ] || return 0
    if grep -qE '^[[:space:]]*OCEANKIND_DEVICE_KEY=.+' "$ENV_FILE" \
       && ! grep -qE '^[[:space:]]*OCEANKIND_DEVICE_SECRET=.+' "$ENV_FILE"; then
        log "AVISO: $ENV_FILE tiene OCEANKIND_DEVICE_KEY (anterior a D-020) y no OCEANKIND_DEVICE_SECRET."
        log "       Una release con D-020 se negará a arrancar y se revertirá. Re-crear el"
        log "       dispositivo en el panel y pegar el secreto nuevo antes de asignarla."
    fi
    return 0
}

# ── Journal ──────────────────────────────────────────────────────────────────
journal_write() { atomic_write "$JOURNAL" "state=$1 prev=$2 target=$3"; }   # sin espacios en los valores
journal_field() { tr ' ' '\n' < "$JOURNAL" 2>/dev/null | sed -n "s/^$1=//p" | head -1; }
journal_clear() { rm -f "$JOURNAL"; sync; }

# ── Snapshot de la instalación ───────────────────────────────────────────────
# Sólo se toma con la instalación INTACTA (sin journal), y el journal se
# escribe DESPUÉS: journal presente ⇒ snapshot completo.
snapshot_take() {
    rm -rf "$SNAP_DIR.tmp"
    mkdir -p "$SNAP_DIR.tmp" || return 1
    if [ -f "$OCEANKIND_DIR/marfutura_iot_audio.py" ]; then
        cp "$OCEANKIND_DIR"/*.py "$SNAP_DIR.tmp/" || return 1
        cp -R "$OCEANKIND_DIR/oceankind" "$SNAP_DIR.tmp/oceankind" || return 1
        if [ -f "$OCEANKIND_DIR/model.joblib" ]; then
            cp "$OCEANKIND_DIR/model.joblib" "$SNAP_DIR.tmp/model.joblib" || return 1
        fi
        # Dependencias tal como están: para poder deshacer un pip. Mejor esfuerzo.
        if [ -x "$VENV_PY" ]; then
            tmo 60 "$VENV_PY" -m pip freeze > "$SNAP_DIR.tmp/freeze.txt" 2>/dev/null || rm -f "$SNAP_DIR.tmp/freeze.txt"
        fi
    else
        : > "$SNAP_DIR.tmp/.none"      # primera instalación: no hay nada que conservar
    fi
    : > "$SNAP_DIR.tmp/.complete"
    sync
    rm -rf "$SNAP_DIR"
    mv "$SNAP_DIR.tmp" "$SNAP_DIR"
}

restore_snapshot() {
    if [ ! -f "$SNAP_DIR/.complete" ]; then
        log "CRÍTICO: no hay snapshot completo en $SNAP_DIR — no se puede restaurar."
        return 1
    fi
    local f
    if [ -f "$SNAP_DIR/.none" ]; then
        rm -f "$OCEANKIND_DIR"/*.py; rm -rf "$OCEANKIND_DIR/oceankind"
        return 0
    fi
    cp "$SNAP_DIR"/*.py "$OCEANKIND_DIR/" || return 1
    # .py que el build fallido añadió y el anterior no tenía
    for f in "$OCEANKIND_DIR"/*.py; do
        [ -f "$SNAP_DIR/$(basename "$f")" ] || rm -f "$f"
    done
    rm -rf "$OCEANKIND_DIR/oceankind" || return 1
    cp -R "$SNAP_DIR/oceankind" "$OCEANKIND_DIR/oceankind" || return 1
    if [ -f "$SNAP_DIR/model.joblib" ]; then
        cp "$SNAP_DIR/model.joblib" "$OCEANKIND_DIR/model.joblib" || return 1
    fi
    sync
    return 0
}

# Deshacer cambios de pip del build fallido. Mejor esfuerzo: si no hay red se
# avisa y se sigue — el código ya está restaurado y las dependencias suelen ser
# compatibles hacia atrás. Lo que NO se puede es bloquear el rollback por esto.
restore_deps() {
    [ -s "$SNAP_DIR/freeze.txt" ] || return 0
    pip_install "$SNAP_DIR/freeze.txt" \
        || log "AVISO: no se pudieron restaurar las dependencias anteriores (¿sin red?). Código restaurado igualmente."
    return 0
}

# ── Verificación ─────────────────────────────────────────────────────────────
service_restarts() {
    systemctl show -p NRestarts --value "$SERVICE_NAME" 2>/dev/null || echo 0
}

restart_and_verify() {
    local before after

    log "Reiniciando $SERVICE_NAME..."
    if ! sudo systemctl restart "$SERVICE_NAME"; then
        log "FALLO: systemctl restart $SERVICE_NAME devolvió error."
        return 1
    fi

    # La línea base se toma DESPUÉS del restart: systemd pone NRestarts a cero
    # en un arranque manual, así que comparar contra el valor previo daría un
    # falso fallo y revertiría una actualización buena.
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

# ── Instalación: del checkout a lo que systemd ejecuta ───────────────────────
validate_checkout() {
    # Un checkout roto o a medias NO debe tocar la instalación que funciona.
    if [ ! -f "$SRC_DIR/marfutura_iot_audio.py" ] || [ ! -d "$SRC_DIR/oceankind" ]; then
        log "ERROR: checkout inválido — falta $SRC_DIR/marfutura_iot_audio.py"
        log "       o el paquete oceankind/. No se toca la instalación actual."
        return 1
    fi
    return 0
}

install_files() {
    cp "$SRC_DIR"/*.py "$OCEANKIND_DIR/" || return 1
    rm -rf "$OCEANKIND_DIR/oceankind" || return 1
    cp -R "$SRC_DIR/oceankind" "$OCEANKIND_DIR/oceankind" || return 1
    if [ -f "$MODEL_FILE" ]; then
        cp "$MODEL_FILE" "$OCEANKIND_DIR/model.joblib" || return 1
    fi
    sync
    return 0
}

# Confirmar: el build está instalado Y verificado. Primero el SHA, luego se
# cierra el journal; un corte entre ambos sólo repite una confirmación idempotente.
commit() {
    atomic_write "$INSTALLED_SHA_FILE" "$1" || return 1
    # El ref del que vino (D-021): es lo que el servicio compara con la
    # asignación. Mejor esfuerzo: sin él, el servicio compara SHAs… que no
    # conoce, y relanzaría; por eso se escribe aquí, no en el servicio.
    [ -n "$TARGET_REF" ] && atomic_write "$INSTALLED_REF_FILE" "$TARGET_REF"
    rm -f "$FAILED_SHA_FILE" "$FAILED_REF_FILE"
    journal_clear
    return 0
}

mark_failed() {   # $1 sha
    atomic_write "$FAILED_SHA_FILE" "$1"
    [ -n "$TARGET_REF" ] && atomic_write "$FAILED_REF_FILE" "$TARGET_REF"
    return 0
}

# Desplegar un commit y verificarlo.
#   0 = instalado y verificado
#   1 = el BUILD es malo (no verificó). El journal sigue: el llamador revierte
#   2 = fallo de INFRAESTRUCTURA (red, disco, git). La instalación no cambió
#       o ya se restauró; no es culpa del commit y no se marca como fallido
deploy() {
    local sha="$1" prev="$2"
    log "Desplegando $sha..."

    # reset --hard, no pull: el checkout puede estar en HEAD separado.
    if ! git -C "$REPO_DIR" reset --hard "$sha" >/dev/null 2>&1; then
        log "git reset a $sha falló — checkout dañado; se recrea."
        clone_repo || { log "ERROR: sin red para recrear el checkout."; return 2; }
        git -C "$REPO_DIR" reset --hard "$sha" >/dev/null 2>&1 || { log "ERROR: $sha no existe en el checkout nuevo."; return 2; }
    fi
    validate_checkout || return 1

    # Dependencias ANTES de tocar el código: si la red cae aquí no hay nada que
    # deshacer y la unidad sigue con lo que funciona.
    if [ -f "$REQ_FILE" ]; then
        log "Actualizando dependencias..."
        if ! pip_install "$REQ_FILE"; then
            log "ERROR: pip falló (¿red?). La instalación NO se toca; se reintenta."
            return 2
        fi
    else
        log "AVISO: no se encontró $REQ_FILE — dependencias sin tocar."
    fi

    snapshot_take || { log "ERROR: no se pudo tomar el snapshot (¿disco lleno?). No se instala."; return 2; }
    journal_write installing "$prev" "$sha" || { log "ERROR: no se pudo escribir el journal."; return 2; }

    if ! install_files; then
        log "ERROR: la copia falló a medias (¿disco lleno?) — restaurando snapshot."
        if restore_snapshot; then journal_clear; else log "CRÍTICO: la restauración también falló; el journal se conserva para reintentarla."; fi
        return 2
    fi

    journal_write verifying "$prev" "$sha" || { log "ERROR: journal."; return 2; }
    if restart_and_verify; then
        commit "$sha" || return 2
        return 0
    fi
    return 1
}

# Volver al snapshot y comprobar que arranca.
rollback() {
    local prev="$1" target="$2"
    journal_write rollingback "$prev" "$target" || return 1
    restore_snapshot || return 1
    restore_deps
    if restart_and_verify; then
        journal_clear
        return 0
    fi
    return 1
}

# ── Recuperación tras un corte ───────────────────────────────────────────────
# Un journal que sobrevive = una OTA se cortó (luz, kernel, matada). Se llama
# al arrancar y al inicio de cada ejecución.
recover_journal() {
    [ -f "$JOURNAL" ] || return 0
    local state prev target
    state=$(journal_field state); prev=$(journal_field prev); target=$(journal_field target)
    log "Journal encontrado (state=${state:-?} $prev → $target): una OTA anterior se interrumpió."

    case "$state" in
        verifying)
            log "Se cortó verificando $target — se re-verifica el build instalado."
            if restart_and_verify; then
                commit "$target" && log "✓ $target verificado tras el corte — confirmado."
                return 0
            fi
            log "✗ $target no verifica — revirtiendo a $prev"
            mark_failed "$target"
            ;;
        *)
            # installing / rollingback / ilegible: el árbol puede estar a medias.
            # No es culpa del commit: no se marca como fallido, se reintentará.
            log "Se cortó a mitad de instalar/revertir — restaurando el snapshot."
            ;;
    esac

    if rollback "$prev" "$target"; then
        log "✓ Rollback correcto tras el corte — la unidad sigue en ${prev}."
        return 0
    fi
    log "════════════════════════════════════════════════════════════"
    log "FALLO CRÍTICO: no se pudo recuperar la instalación tras el corte."
    log "El journal se conserva: cada arranque lo reintentará."
    log "journalctl -u $SERVICE_NAME -n 50"
    log "════════════════════════════════════════════════════════════"
    return 1
}

# ── El update completo. Usado por el modo directo y por el mantenimiento ─────
# Resuelve el SHA corto del ref objetivo en el checkout (tras fetch). Tag
# primero (anotado o ligero), luego un commit/sha suelto. Imprime el sha.
resolve_sha() {
    local sha
    sha=$(git -C "$REPO_DIR" rev-parse --short "refs/tags/$TARGET_REF^{commit}" 2>/dev/null) \
        || sha=$(git -C "$REPO_DIR" rev-parse --short "$TARGET_REF^{commit}" 2>/dev/null) \
        || return 1
    echo "$sha"
}

do_update() {
    local rc
    resolve_target; rc=$?
    [ "$rc" = 0 ] && return 0
    [ "$rc" = 2 ] && return 2
    check_env_generation
    ensure_repo || return 2

    local current target previous
    current=$(installed_sha)
    log "Versión instalada: ${current:-desconocida (provisionada por setup.sh)}${current:+ ($(installed_ref))}"

    fetch_target || return 2
    if ! target=$(resolve_sha); then
        log "ERROR: la release '$TARGET_REF' no existe en $REPO_URL (¿tag sin publicar, o typo en el panel?)."
        log "       La instalación no cambió. Se reintenta en la próxima ejecución."
        return 2
    fi

    if [ -n "$current" ] && [ "$current" = "$target" ]; then
        if [ "$(installed_ref)" != "$TARGET_REF" ]; then
            # Mismo commit, otro nombre (p.ej. tag puesto sobre lo ya instalado):
            # registrar el nombre para que el servicio deje de pedirlo.
            atomic_write "$INSTALLED_REF_FILE" "$TARGET_REF"
        fi
        log "Ya en la release asignada ($TARGET_REF = $current) — sin cambios."
        return 0
    fi

    # Un commit que ya rompió esta unidad no se reintenta solo.
    if [ -f "$FAILED_SHA_FILE" ] && [ "$(cat "$FAILED_SHA_FILE")" = "$target" ]; then
        log "El commit $target ($TARGET_REF) ya falló en esta unidad y se revirtió."
        log "NO se reintenta automáticamente. Publica otra release, o borra"
        log "$FAILED_SHA_FILE a mano para forzar otro intento."
        return 0
    fi

    previous="${current:-$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo desconocida)}"
    log "Release asignada: ${current:-?} → $target ($TARGET_REF)"

    deploy "$target" "$previous"; rc=$?
    case "$rc" in
        0)
            log "✓ Actualización completada y verificada: $previous → $target ($TARGET_REF)"
            return 0 ;;
        2)
            log "✗ Actualización NO aplicada por un fallo de infraestructura (no del commit)."
            log "  La instalación no cambió. Se reintenta en la próxima ejecución."
            return 2 ;;
    esac

    # ── El build es malo ─────────────────────────────────────────────────────
    log "✗ $target ($TARGET_REF) falló la verificación — revirtiendo a $previous"
    mark_failed "$target"

    if [ ! -f "$JOURNAL" ]; then
        log "  El commit era inválido antes de instalarse: la instalación sigue intacta."
        return 0
    fi
    if rollback "$previous" "$target"; then
        log "✓ Rollback correcto — la unidad sigue en $previous"
        log "  El commit $target queda marcado como fallido."
        return 0
    fi

    log "════════════════════════════════════════════════════════════"
    log "FALLO CRÍTICO: $target rompió el servicio y el rollback a"
    log "$previous TAMPOCO arrancó. La unidad NO está corriendo."
    log "El journal se conserva: el próximo arranque reintenta la recuperación."
    log "Requiere intervención manual: journalctl -u $SERVICE_NAME -n 50"
    log "════════════════════════════════════════════════════════════"
    return 1
}

# ── Unidades con overlay ─────────────────────────────────────────────────────
# Fase 1 (overlay activo): no se puede escribir nada persistente. Sólo decide
# si vale la pena reiniciar en modo mantenimiento.
ota_phase1() {
    if [ ! -f "$UNIT_DIR/$BOOT_UNIT" ] || [ ! -f "$SD_MARK" ]; then
        log "AVISO: falta $UNIT_DIR/$BOOT_UNIT o $SD_MARK. Sin ellos nadie volvería"
        log "a activar el overlay tras la ventana de mantenimiento: NO se desactiva."
        log "Re-provisiona con setup.sh + protect_sd.sh."
        return 1
    fi
    if [ -f "$HOLD_MARK" ]; then
        log "Mantenimiento retenido por $HOLD_MARK — nada que hacer."
        return 0
    fi

    local rc
    resolve_target; rc=$?
    [ "$rc" = 0 ] && return 0
    [ "$rc" = 2 ] && return 2
    ensure_repo || return 2       # (a RAM: se pierde al reiniciar, sólo sirve para decidir)
    fetch_target || return 2

    local current target
    current=$(installed_sha)
    if ! target=$(resolve_sha); then
        log "ERROR: la release '$TARGET_REF' no existe en el remoto. Sin cambios."
        return 2
    fi
    if [ -n "$current" ] && [ "$current" = "$target" ]; then
        log "Ya en la release asignada ($TARGET_REF = $current) — sin cambios."
        return 0
    fi
    if [ -f "$FAILED_SHA_FILE" ] && [ "$(cat "$FAILED_SHA_FILE")" = "$target" ]; then
        log "El commit $target ($TARGET_REF) ya falló en esta unidad y se revirtió."
        log "NO se reintenta. Publica otra release, o borra $FAILED_SHA_FILE para forzar otro intento."
        return 0
    fi

    log "Release asignada: ${current:-?} → $target ($TARGET_REF)"
    log "Overlay activo — entrando en modo mantenimiento (overlay OFF + reinicio)."
    log "(La asignación se vuelve a pedir al backend en la ventana: el fichero local no sobrevive al reinicio.)"
    if ! sudo raspi-config nonint do_overlayfs 1; then
        log "ERROR: no se pudo desactivar el overlay. Sin cambios."
        return 1
    fi
    log "Reiniciando en 5 segundos..."
    sleep 5
    sudo reboot
    return 0
}

# Ventana de mantenimiento: overlay OFF en una unidad protegida. SIEMPRE
# termina re-habilitando el overlay.
maintenance_main() {
    if [ -f "$HOLD_MARK" ]; then
        log "Mantenimiento manual ($HOLD_MARK): no se actualiza ni se re-habilita el overlay."
        return 0
    fi

    local attempts rc=0
    attempts=$(cat "$ATTEMPTS_FILE" 2>/dev/null || echo 0)
    if [ "$attempts" -ge "$MAX_MAINT" ]; then
        log "CRÍTICO: $attempts arranques de mantenimiento sin completar — se abandona la OTA."
        log "         Se re-habilita la protección de la SD igualmente."
        recover_journal
        finish_maintenance
        return 1
    fi
    atomic_write "$ATTEMPTS_FILE" $((attempts + 1)) || log "AVISO: no se pudo contar el intento."

    log "Ventana de mantenimiento (arranque $((attempts + 1))/$MAX_MAINT, overlay OFF)."
    recover_journal || rc=1
    if [ "$rc" = 0 ]; then
        do_update || rc=$?
    fi
    finish_maintenance
    return "$rc"
}

finish_maintenance() {
    # Contador a cero ANTES de re-habilitar: un corte entre ambos sólo repite
    # una ventana, con contador nuevo.
    rm -f "$ATTEMPTS_FILE"; sync
    log "Re-habilitando overlay filesystem..."
    if ! sudo raspi-config nonint do_overlayfs 0; then
        log "CRÍTICO: no se pudo re-habilitar el overlay — la SD queda sin proteger."
        log "         El siguiente arranque reintenta la ventana."
        return 1
    fi
    log "✓ Overlay re-habilitado — reiniciando para aplicar..."
    sudo reboot
    return 0
}

# ── Punto de entrada ─────────────────────────────────────────────────────────
MODE=timer
case "${1:-}" in
    --boot) MODE=boot ;;
    --ref)
        MODE=assigned
        TARGET_REF="${2:-}"
        if [ -z "$TARGET_REF" ] || ! valid_ref "$TARGET_REF"; then
            echo "uso: $0 --ref <tag>   (tag git, p.ej. v2.2.0)"; exit 64
        fi ;;
    "")     ;;
    *)      echo "uso: $0 [--boot | --ref <tag>]"; exit 64 ;;
esac

echo ""
log "=== OceanKind OTA Update ($MODE${TARGET_REF:+ → $TARGET_REF}) ==="

acquire_lock || exit 0
trap release_lock EXIT

if [ "$MODE" = boot ]; then
    if overlay_active; then
        recover_journal; exit $?            # arranque normal protegido
    fi
    if [ -f "$SD_MARK" ]; then
        maintenance_main; exit $?
    fi
    recover_journal; exit $?                # unidad sin overlay: sólo reparar
fi

if overlay_active; then
    recover_journal
    ota_phase1; exit $?
fi
if [ -f "$SD_MARK" ]; then
    maintenance_main; exit $?
fi
recover_journal || exit 1
do_update; exit $?
