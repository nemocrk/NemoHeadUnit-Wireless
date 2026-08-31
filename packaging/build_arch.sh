#!/usr/bin/env bash
# packaging/build_arch.sh
#
# Build a self-contained Arch Linux package (.pkg.tar.zst) for NemoHeadUnit-Wireless.
#
# Usage:
#   bash packaging/build_arch.sh [--arch x86_64|aarch64|amd64|arm64] [--output-dir /path]
#
# What this script does:
#   1. Reads VERSION from repo root & auto-increments patch revision
#   2. Validates required build tools (fpm, zstd, tar)
#   3. Assembles a staging directory (build/stage_arch/) mirroring filesystem layout:
#         /opt/nemo-headunit/
#         /usr/lib/systemd/system/
#         /etc/dbus-1/system.d/
#         /usr/share/dbus-1/system-services/
#         /usr/share/polkit-1/actions/
#         /etc/polkit-1/rules.d/
#         /usr/share/applications/
#         /usr/share/pixmaps/
#   4. Builds .pkg.tar.zst with FPM (pacman target)
#   5. Verifies package archive contents
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

log()  { echo "${BOLD}[build_arch_micromamba]${RESET} $*"; }
die()  { echo "${BOLD}[build_arch_micromamba] ERROR:${RESET} $*" >&2; exit 1; }
step() { echo; echo "${BOLD}>>> $* ${RESET}"; }

# ---------------------------------------------------------------------------
# Defaults / CLI args
# ---------------------------------------------------------------------------

ARCH="x86_64"
OUTPUT_DIR="dist"

while [[ $# -gt 0 ]]; do
    case $1 in
        --arch)
            RAW_ARCH="$2"
            case "${RAW_ARCH}" in
                amd64|x86_64) ARCH="x86_64" ;;
                arm64|aarch64) ARCH="aarch64" ;;
                *) die "Unsupported architecture: ${RAW_ARCH} (expected x86_64, aarch64, amd64, arm64)" ;;
            esac
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BUILD_DIR="${REPO_ROOT}/build"
STAGE_DIR="${BUILD_DIR}/stage_arch"
OUTPUT_DIR="${REPO_ROOT}/${OUTPUT_DIR}"

VERSION_FILE="${REPO_ROOT}/VERSION"
ENV_YML="${REPO_ROOT}/environment.yml"
SYS_DEPS_FILE="${REPO_ROOT}/packaging/system-deps-arch.txt"
POSTINST="${REPO_ROOT}/packaging/postinst"
PRERM="${REPO_ROOT}/packaging/prerm"
BT_RULES="${REPO_ROOT}/packaging/org.nemo.bluetooth.rules"
HW_FIXES_SRC="${REPO_ROOT}/packaging/hardware_fixes"
BOOTSTRAP_MICROMAMBA="${REPO_ROOT}/packaging/bootstrap_micromamba.sh"
LAUNCHER_SRC="${REPO_ROOT}/packaging"

SERVICES_SRC="${REPO_ROOT}/services/linux/ap_manager_service"

# ---------------------------------------------------------------------------
# Step 0 — Read & Increment version
# ---------------------------------------------------------------------------
step "Reading & Auto-Incrementing version"
[[ -f "${VERSION_FILE}" ]] || die "VERSION file not found at ${REPO_ROOT}/VERSION"
RAW_VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
[[ -n "${RAW_VERSION}" ]] || die "VERSION file is empty"

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
PKG_FILENAME="${PACKAGE_NAME}-${VERSION}-1-${ARCH}.pkg.tar.zst"

# ---------------------------------------------------------------------------
# Step 1 — Check required tools
# ---------------------------------------------------------------------------
step "Checking required tools"

for tool in fpm tar; do
    if ! command -v "${tool}" &>/dev/null; then
        die "'${tool}' not found. Install it before running this script."
    fi
    log "  ${tool}: $(command -v "${tool}")"
done

[[ -f "${ENV_YML}" ]] || die "environment.yml not found at ${REPO_ROOT}/environment.yml"
[[ -f "${SYS_DEPS_FILE}" ]] || die "system-deps-arch.txt not found at ${SYS_DEPS_FILE}"
log "  environment.yml: found"
log "  system-deps-arch.txt: found"

# ---------------------------------------------------------------------------
# Step 2 — Clean previous build
# ---------------------------------------------------------------------------
step "Cleaning previous build artefacts"
rm -rf "${STAGE_DIR}" "${OUTPUT_DIR}/${PKG_FILENAME}"
mkdir -p "${OUTPUT_DIR}"
log "Stage dir: ${STAGE_DIR}"

# ---------------------------------------------------------------------------
# Step 3 — Assemble staging directory
# ---------------------------------------------------------------------------
step "Assembling staging directory"

APP_OPT="${STAGE_DIR}/opt/nemo-headunit"
mkdir -p "${APP_OPT}"

log "  Copying environment.yml"
cp "${ENV_YML}" "${APP_OPT}/environment.yml"

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

if [ -f "${BOOTSTRAP_MICROMAMBA}" ]; then
    log "  Copying bootstrap_micromamba.sh"
    cp "${BOOTSTRAP_MICROMAMBA}" "${APP_OPT}/bootstrap_micromamba.sh"
    chmod +x "${APP_OPT}/bootstrap_micromamba.sh"
fi

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

# —— /etc/dbus-1/system.d/ ——
DBUS_STAGE="${STAGE_DIR}/etc/dbus-1/system.d"
mkdir -p "${DBUS_STAGE}"
log "  Copying D-Bus policy"
cp "${SERVICES_SRC}/org.nemo.APManager.conf" "${DBUS_STAGE}/"

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
# Step 5 — Run FPM (Pacman format)
# ---------------------------------------------------------------------------
step "Running FPM (Pacman)"

fpm \
    --input-type  dir \
    --output-type pacman \
    --name        "${PACKAGE_NAME}" \
    --version     "${VERSION}" \
    --iteration   "1" \
    --architecture "${ARCH}" \
    --description "NemoHeadUnit-Wireless — Android Auto wireless head unit" \
    --url         "https://github.com/nemocrk/NemoHeadUnit-Wireless" \
    --maintainer  "nemocrk <nemocrk@users.noreply.github.com>" \
    --license     "GPL-2.0-only" \
    --after-install  "${POSTINST}" \
    --before-remove  "${PRERM}" \
    --pacman-compression "zstd" \
    --package     "${OUTPUT_DIR}/${PKG_FILENAME}" \
    --chdir       "${STAGE_DIR}" \
    "${DEPENDS_ARGS[@]}" \
    .

log "Package written to: ${OUTPUT_DIR}/${PKG_FILENAME}"

# ---------------------------------------------------------------------------
# Step 6 — Verify
# ---------------------------------------------------------------------------
step "Verifying package"

echo
echo "--- Package contents (first 40 lines) ---"
tar -tf "${OUTPUT_DIR}/${PKG_FILENAME}" | head -n 40 || true

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
log "✔  Build successful: ${OUTPUT_DIR}/${PKG_FILENAME}"
echo
log "Install on Arch Linux target:"
log "  sudo pacman -U ./${PKG_FILENAME}"
echo
