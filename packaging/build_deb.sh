#!/usr/bin/env bash
# packaging/build_deb.sh
#
# Build a self-contained .deb for NemoHeadUnit-Wireless.
# Supports two packaging modes:
#   1. --method venv (Default): uses system python3 + venv + uv installer.
#   2. --method micromamba (or --micromamba): uses standalone Micromamba conda environment.
#
# Usage:
#   bash packaging/build_deb.sh [--method venv|micromamba] [--arch amd64|arm64] [--output-dir /path]
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

log()  { echo "${BOLD}[build_deb]${RESET} $*"; }
die()  { echo "${BOLD}[build_deb] ERROR:${RESET} $*" >&2; exit 1; }
step() { echo; echo "${BOLD}>>> $* ${RESET}"; }

# ---------------------------------------------------------------------------
# Defaults / CLI args
# ---------------------------------------------------------------------------

ARCH="amd64"
OUTPUT_DIR="dist"
METHOD="venv"

while [[ $# -gt 0 ]]; do
    case $1 in
        --method)
            METHOD="$2"
            shift 2
            ;;
        --venv)
            METHOD="venv"
            shift 1
            ;;
        --micromamba|--conda)
            METHOD="micromamba"
            shift 1
            ;;
        --auto)
            METHOD="auto"
            shift 1
            ;;
        --arch)
            ARCH="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

[[ "$ARCH" == "amd64" || "$ARCH" == "arm64" ]] \
    || die "--arch must be 'amd64' or 'arm64'"

[[ "$METHOD" == "venv" || "$METHOD" == "micromamba" || "$METHOD" == "auto" ]] \
    || die "--method must be 'venv', 'micromamba', or 'auto'"

log "Packaging target: ARCH=${ARCH}, METHOD=${METHOD}"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BUILD_DIR="${REPO_ROOT}/build"
STAGE_DIR="${BUILD_DIR}/stage"
OUTPUT_DIR="${REPO_ROOT}/${OUTPUT_DIR}"

VERSION_FILE="${REPO_ROOT}/VERSION"
ENV_YML="${REPO_ROOT}/environment.yml"
REQUIREMENTS_TXT="${REPO_ROOT}/packaging/requirements.txt"

if [ "$METHOD" = "micromamba" ]; then
    SYS_DEPS_FILE="${REPO_ROOT}/packaging/system-deps.txt"
else
    SYS_DEPS_FILE="${REPO_ROOT}/packaging/system-deps-venv.txt"
fi

POSTINST="${REPO_ROOT}/packaging/postinst"
PRERM="${REPO_ROOT}/packaging/prerm"
BT_RULES="${REPO_ROOT}/packaging/org.nemo.bluetooth.rules"
HW_FIXES_SRC="${REPO_ROOT}/packaging/hardware_fixes"
LAUNCHER_SRC="${REPO_ROOT}/packaging"

SERVICES_SRC="${REPO_ROOT}/services/linux/ap_manager_service"

# ---------------------------------------------------------------------------
# Step 0 — Read & Increment version
# ---------------------------------------------------------------------------
step "Reading & Auto-Incrementing version"
[[ -f "${VERSION_FILE}" ]] || die "VERSION file not found at ${REPO_ROOT}/VERSION"
RAW_VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
[[ -n "${RAW_VERSION}" ]] || die "VERSION file is empty"

# Auto-increment patch/revision number (e.g. 0.2.4 -> 0.2.5)
if [[ "${RAW_VERSION}" =~ ^([0-9]+\.[0-9]+\.)([0-9]+)$ ]]; then
  BASE_VERSION="${BASH_REMATCH[1]}"
  PATCH_REV="${BASH_REMATCH[2]}"
  NEW_PATCH_REV=$((PATCH_REV + 1))
  VERSION="${BASE_VERSION}${NEW_PATCH_REV}"
  echo "${VERSION}" > "${VERSION_FILE}"
  log "Auto-incremented version: ${RAW_VERSION} -> ${VERSION}"
else
  VERSION="${RAW_VERSION}"
  log "Version: ${VERSION}"
fi

PACKAGE_NAME="nemo-headunit"
DEB_FILENAME="${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"

# ---------------------------------------------------------------------------
# Step 1 — Check required tools
# ---------------------------------------------------------------------------
step "Checking required tools"

for tool in fpm dpkg-deb; do
    if ! command -v "${tool}" &>/dev/null; then
        die "'${tool}' not found. Install it before running this script."
    fi
    log "  ${tool}: $(command -v "${tool}")"
done

if [ -f "${REQUIREMENTS_TXT}" ]; then
    log "  requirements.txt: found"
fi
if [ -f "${ENV_YML}" ]; then
    log "  environment.yml: found"
fi

# ---------------------------------------------------------------------------
# Step 2 — Clean previous build
# ---------------------------------------------------------------------------
step "Cleaning previous build artefacts"
rm -rf "${BUILD_DIR}" "${OUTPUT_DIR}/${DEB_FILENAME}"
mkdir -p "${OUTPUT_DIR}"
log "Build dir: ${BUILD_DIR}"

# ---------------------------------------------------------------------------
# Step 3 — Assemble staging directory
# ---------------------------------------------------------------------------
step "Assembling staging directory (${METHOD} mode)"

APP_OPT="${STAGE_DIR}/opt/nemo-headunit"
mkdir -p "${APP_OPT}"

# Stage both venv and micromamba environment descriptors so postinst can preserve existing runtime
if [ -f "${REQUIREMENTS_TXT}" ]; then
    log "  Copying requirements.txt"
    cp "${REQUIREMENTS_TXT}" "${APP_OPT}/requirements.txt"
fi
if [ -f "${REPO_ROOT}/packaging/bootstrap_uv.sh" ]; then
    log "  Copying bootstrap_uv.sh"
    cp "${REPO_ROOT}/packaging/bootstrap_uv.sh" "${APP_OPT}/bootstrap_uv.sh"
    chmod +x "${APP_OPT}/bootstrap_uv.sh"
fi
if [ -f "${ENV_YML}" ]; then
    log "  Copying environment.yml"
    cp "${ENV_YML}" "${APP_OPT}/environment.yml"
fi
if [ -f "${REPO_ROOT}/packaging/bootstrap_micromamba.sh" ]; then
    log "  Copying bootstrap_micromamba.sh"
    cp "${REPO_ROOT}/packaging/bootstrap_micromamba.sh" "${APP_OPT}/bootstrap_micromamba.sh"
    chmod +x "${APP_OPT}/bootstrap_micromamba.sh"
fi
if [ "$METHOD" != "auto" ]; then
    echo "${METHOD}" > "${APP_OPT}/.packaging_mode"
fi

log "  Copying application source"
cp "${REPO_ROOT}/main.py"      "${APP_OPT}/main.py"
cp -a "${REPO_ROOT}/backend"   "${APP_OPT}/backend"
cp -a "${REPO_ROOT}/frontend"  "${APP_OPT}/frontend"
cp -a "${REPO_ROOT}/scripts"   "${APP_OPT}/scripts"
cp -a "${REPO_ROOT}/protos"   "${APP_OPT}/protos"

log "  Copying services/"
cp -a "${REPO_ROOT}/services" "${APP_OPT}/services"

log "  Copying hardware_fixes/"
cp -a "${HW_FIXES_SRC}" "${APP_OPT}/hardware_fixes"
chmod +x "${APP_OPT}/hardware_fixes/run_hardware_fixes.sh"
find "${APP_OPT}/hardware_fixes" -name 'fix_*.sh' -exec chmod +x {} \;

# —— /opt/nemo-headunit/bin/ (launcher wrapper) ——
mkdir -p "${APP_OPT}/bin"
log "  Copying launcher script"
cp "${REPO_ROOT}/packaging/nemo-headunit.sh" "${APP_OPT}/bin/nemo-headunit"
chmod 755 "${APP_OPT}/bin/nemo-headunit"

# Prune bytecode / tests
log "  Pruning bytecode and test files"
find "${APP_OPT}" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "${APP_OPT}" -name '*.pyc' -delete 2>/dev/null || true
find "${APP_OPT}" -name '*.pyo' -delete 2>/dev/null || true
rm -rf "${APP_OPT}/tests" 2>/dev/null || true

# —— /usr/lib/systemd/system/ ——
SYSTEMD_STAGE="${STAGE_DIR}/usr/lib/systemd/system"
mkdir -p "${SYSTEMD_STAGE}"
log "  Copying systemd unit"
cp "${SERVICES_SRC}/org.nemo.APManager.service" "${SYSTEMD_STAGE}/"
sed -i \
    "s|ExecStart=.*ap_manager_service.py|ExecStart=/opt/nemo-headunit/env/bin/python /opt/nemo-headunit/services/linux/ap_manager_service/ap_manager_service.py|" \
    "${SYSTEMD_STAGE}/org.nemo.APManager.service"
if [ -f "${REPO_ROOT}/packaging/systemd/bluez-obex.service" ]; then
    cp "${REPO_ROOT}/packaging/systemd/bluez-obex.service" "${SYSTEMD_STAGE}/"
fi

# —— /etc/dbus-1/system.d/ ——
DBUS_STAGE="${STAGE_DIR}/etc/dbus-1/system.d"
mkdir -p "${DBUS_STAGE}"
log "  Copying D-Bus policies"
cp "${SERVICES_SRC}/org.nemo.APManager.conf" "${DBUS_STAGE}/"
cp "${REPO_ROOT}/packaging/org.nemo.bluez.conf" "${DBUS_STAGE}/"

# —— /etc/wireplumber/wireplumber.conf.d/ ——
WIREPLUMBER_STAGE="${STAGE_DIR}/etc/wireplumber/wireplumber.conf.d"
mkdir -p "${WIREPLUMBER_STAGE}"
log "  Copying WirePlumber configuration"
cp "${REPO_ROOT}/packaging/50-bluez.conf" "${WIREPLUMBER_STAGE}/"

# —— /usr/share/dbus-1/system-services/ ——
DBUS_SERVICES_STAGE="${STAGE_DIR}/usr/share/dbus-1/system-services"
mkdir -p "${DBUS_SERVICES_STAGE}"
log "  Copying D-Bus activation file"
cp "${SERVICES_SRC}/org.nemo.APManager.dbus-service" \
   "${DBUS_SERVICES_STAGE}/org.nemo.APManager.service"

# —— /usr/share/polkit-1/actions/ ——
POLKIT_STAGE="${STAGE_DIR}/usr/share/polkit-1/actions"
mkdir -p "${POLKIT_STAGE}"
log "  Copying PolicyKit policy"
cp "${SERVICES_SRC}/org.nemo.APManager.policy" \
    "${POLKIT_STAGE}/org.nemo.apmanager.policy"

# —— /etc/polkit-1/rules.d/ ——
POLKIT_RULES_STAGE="${STAGE_DIR}/etc/polkit-1/rules.d"
mkdir -p "${POLKIT_RULES_STAGE}"
log "  Copying polkit JS rules"
cp "${BT_RULES}" "${POLKIT_RULES_STAGE}/"

# —— /usr/share/applications/ (.desktop entry & icon) ——
APPS_STAGE="${STAGE_DIR}/usr/share/applications"
mkdir -p "${APPS_STAGE}"
log "  Copying .desktop entry"
cp "${REPO_ROOT}/packaging/nemo-headunit.desktop" "${APPS_STAGE}/"

PIXMAPS_STAGE="${STAGE_DIR}/usr/share/pixmaps"
mkdir -p "${PIXMAPS_STAGE}"
log "  Copying application icon"
cp "${REPO_ROOT}/packaging/assets/nemo-headunit.png" "${PIXMAPS_STAGE}/nemo-headunit.png"

# ---------------------------------------------------------------------------
# Step 4 — Build --depends list
# ---------------------------------------------------------------------------
step "Building --depends list"

DEPENDS_ARGS=()
while IFS= read -r line; do
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    DEPENDS_ARGS+=("--depends" "${line}")
done < "${SYS_DEPS_FILE}"

log "  ${#DEPENDS_ARGS[@]} --depends flags assembled"

# ---------------------------------------------------------------------------
# Step 5 — Run FPM
# ---------------------------------------------------------------------------
step "Running FPM"

fpm \
    --input-type  dir \
    --output-type deb \
    --name        "${PACKAGE_NAME}" \
    --version     "${VERSION}" \
    --architecture "${ARCH}" \
    --description "NemoHeadUnit-Wireless — Android Auto wireless head unit (${METHOD})" \
    --url         "https://github.com/nemocrk/NemoHeadUnit-Wireless" \
    --maintainer  "nemocrk <nemocrk@users.noreply.github.com>" \
    --license     "GPL-2.0-only" \
    --after-install  "${POSTINST}" \
    --before-remove  "${PRERM}" \
    --deb-recommends "falkon | chromium-browser | chromium | google-chrome | surf, i965-va-driver | intel-media-va-driver | nvidia-va-driver | mesa-va-drivers" \
    --deb-no-default-config-files \
    "${DEPENDS_ARGS[@]}" \
    -C "${STAGE_DIR}" \
    -p "${OUTPUT_DIR}/${DEB_FILENAME}" \
    .

log "DEB package created: ${OUTPUT_DIR}/${DEB_FILENAME}"

# ---------------------------------------------------------------------------
# Step 6 — Verify package
# ---------------------------------------------------------------------------
step "Verifying package with dpkg-deb"

log "Package info:"
dpkg-deb --info "${OUTPUT_DIR}/${DEB_FILENAME}"

echo
log "Package size:"
ls -lh "${OUTPUT_DIR}/${DEB_FILENAME}"

echo
log "Build successful!"
log "Output: ${OUTPUT_DIR}/${DEB_FILENAME}"
log "Install on target:"
log "  sudo apt install --fix-broken ./${DEB_FILENAME}"
log "  # or: "
log "  sudo dpkg -i ./${DEB_FILENAME} && sudo apt-get install -f"
