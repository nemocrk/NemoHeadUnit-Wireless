#!/usr/bin/env bash
# packaging/build_deb.sh
#
# Build a self-contained .deb for NemoHeadUnit-Wireless v2.
#
# Usage:
#   bash packaging/build_deb.sh [--arch amd64|arm64] [--output-dir /path]
#
# What this script does:
#   1.  Reads VERSION from repo root
#   2.  Validates required tools (conda, fpm, dpkg-deb)
#   3.  Creates a clean Conda env at build/env from environment.yml
#   4.  Assembles a staging directory (build/stage/) mirroring the
#       final filesystem layout:
#         /opt/nemo-headunit/
#           env/              ← full Conda environment
#           v2/               ← application source (v2/)
#           services/         ← ap_manager_service
#           hardware_fixes/   ← platform-specific fix scripts + registry
#           bus_broker.py     ← ZMQ bus broker entry point
#         /usr/lib/systemd/system/
#           org.nemo.APManager.service
#         /etc/dbus-1/system.d/
#           org.nemo.APManager.conf
#         /usr/share/polkit-1/actions/
#           org.nemo.APManager.policy
#         /etc/polkit-1/rules.d/
#           org.nemo.bluetooth.rules
#   5.  Builds the .deb with FPM
#   6.  Runs dpkg-deb --info + dpkg-deb --contents to verify the package
#
# Requirements (build machine only — NOT declared as .deb deps):
#   conda   (Miniconda or Mambaforge)
#   fpm     (gem install fpm)
#   ruby    (for fpm)
#   dpkg    (to verify)
#
# The resulting .deb declares APT deps from packaging/system-deps.txt
# and installs the full Conda env, so the target machine does NOT need
# conda, pip, or any Python tooling.
#
# Arch note:
#   --arch arm64 cross-compiles the .deb metadata only.
#   The Conda env is built natively on the build machine.
#   For true arm64 binaries, run this script ON an arm64 machine
#   (or use a QEMU/Docker arm64 container).

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

while [[ $# -gt 0 ]]; do
    case $1 in
        --arch)       ARCH="$2";       shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ "$ARCH" == "amd64" || "$ARCH" == "arm64" ]] \
    || die "--arch must be 'amd64' or 'arm64'"

# ---------------------------------------------------------------------------
# Paths (all relative to repo root)
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BUILD_DIR="${REPO_ROOT}/build"
STAGE_DIR="${BUILD_DIR}/stage"
ENV_DIR="${BUILD_DIR}/env"
OUTPUT_DIR="${REPO_ROOT}/${OUTPUT_DIR}"

VERSION_FILE="${REPO_ROOT}/VERSION"
ENV_YML="${REPO_ROOT}/environment.yml"
SYS_DEPS_FILE="${REPO_ROOT}/packaging/system-deps.txt"
POSTINST="${REPO_ROOT}/packaging/postinst"
PRERM="${REPO_ROOT}/packaging/prerm"
BT_RULES="${REPO_ROOT}/packaging/org.nemo.bluetooth.rules"
HW_FIXES_SRC="${REPO_ROOT}/packaging/hardware_fixes"

SERVICES_SRC="${REPO_ROOT}/services/ap_manager_service"

# ---------------------------------------------------------------------------
# Step 0 — Read version
# ---------------------------------------------------------------------------
step "Reading version"
[[ -f "${VERSION_FILE}" ]] || die "VERSION file not found at ${REPO_ROOT}/VERSION"
VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
[[ -n "${VERSION}" ]] || die "VERSION file is empty"
log "Version: ${VERSION}"

PACKAGE_NAME="nemo-headunit"
DEB_FILENAME="${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"

# ---------------------------------------------------------------------------
# Step 1 — Check required tools
# ---------------------------------------------------------------------------
step "Checking required tools"

for tool in conda fpm dpkg-deb; do
    if ! command -v "${tool}" &>/dev/null; then
        die "'${tool}' not found. Install it before running this script."
    fi
    log "  ${tool}: $(command -v "${tool}")"
done

# ---------------------------------------------------------------------------
# Step 2 — Clean previous build
# ---------------------------------------------------------------------------
step "Cleaning previous build artefacts"
rm -rf "${BUILD_DIR}" "${OUTPUT_DIR}/${DEB_FILENAME}"
mkdir -p "${OUTPUT_DIR}"
log "Build dir: ${BUILD_DIR}"

# ---------------------------------------------------------------------------
# Step 3 — Create Conda environment
# ---------------------------------------------------------------------------
step "Creating Conda environment"
log "  env.yml : ${ENV_YML}"
log "  target  : ${ENV_DIR}"

conda env create \
    --file "${ENV_YML}" \
    --prefix "${ENV_DIR}" \
    --quiet

log "Conda env created at ${ENV_DIR}"

# Rewrite shebangs so they work at the install prefix /opt/nemo-headunit/env
# (conda-pack-style relocation)
if command -v conda-unpack &>/dev/null; then
    log "Running conda-unpack for shebang relocation"
    conda run --prefix "${ENV_DIR}" conda-unpack || true
fi

# ---------------------------------------------------------------------------
# Step 4 — Assemble staging directory
# ---------------------------------------------------------------------------
step "Assembling staging directory"

# —— /opt/nemo-headunit/ ——
APP_OPT="${STAGE_DIR}/opt/nemo-headunit"
mkdir -p "${APP_OPT}"

log "  Copying Conda env → env/"
cp -a "${ENV_DIR}" "${APP_OPT}/env"

log "  Copying v2/ source"
cp -a "${REPO_ROOT}/v2" "${APP_OPT}/v2"

log "  Copying services/"
mkdir -p "${APP_OPT}/services"
cp -a "${SERVICES_SRC}" "${APP_OPT}/services/ap_manager_service"

log "  Copying bus_broker.py"
cp "${REPO_ROOT}/bus_broker.py" "${APP_OPT}/bus_broker.py"

log "  Copying hardware_fixes/"
cp -a "${HW_FIXES_SRC}" "${APP_OPT}/hardware_fixes"
chmod +x "${APP_OPT}/hardware_fixes/run_hardware_fixes.sh"
find "${APP_OPT}/hardware_fixes" -name 'fix_*.sh' -exec chmod +x {} \;

# Remove test files, __pycache__, .pyc from staged source
log "  Pruning test files and bytecode from staged source"
find "${APP_OPT}/v2" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "${APP_OPT}/v2" -name '*.pyc'              -delete             2>/dev/null || true
find "${APP_OPT}/v2" -name '*.pyo'              -delete             2>/dev/null || true
rm -rf "${APP_OPT}/v2/tests" 2>/dev/null || true

# —— /usr/lib/systemd/system/ ——
SYSTEMD_STAGE="${STAGE_DIR}/usr/lib/systemd/system"
mkdir -p "${SYSTEMD_STAGE}"
log "  Copying systemd unit"
cp "${SERVICES_SRC}/org.nemo.APManager.service" "${SYSTEMD_STAGE}/"

# Patch ExecStart to the installed path
sed -i \
    "s|ExecStart=.*ap_manager_service.py|ExecStart=/opt/nemo-headunit/env/bin/python /opt/nemo-headunit/services/ap_manager_service/ap_manager_service.py|" \
    "${SYSTEMD_STAGE}/org.nemo.APManager.service"

# —— /etc/dbus-1/system.d/ ——
DBUS_STAGE="${STAGE_DIR}/etc/dbus-1/system.d"
mkdir -p "${DBUS_STAGE}"
log "  Copying D-Bus policy"
cp "${SERVICES_SRC}/org.nemo.APManager.conf" "${DBUS_STAGE}/"

# —— /usr/share/polkit-1/actions/ ——
POLKIT_STAGE="${STAGE_DIR}/usr/share/polkit-1/actions"
mkdir -p "${POLKIT_STAGE}"
log "  Copying PolicyKit policy"
cp "${SERVICES_SRC}/org.nemo.APManager.policy" "${POLKIT_STAGE}/"

# —— /etc/polkit-1/rules.d/ ——
POLKIT_RULES_STAGE="${STAGE_DIR}/etc/polkit-1/rules.d"
mkdir -p "${POLKIT_RULES_STAGE}"
log "  Copying polkit JS rules"
cp "${BT_RULES}" "${POLKIT_RULES_STAGE}/"

# ---------------------------------------------------------------------------
# Step 5 — Build --depends list from system-deps.txt
# ---------------------------------------------------------------------------
step "Building --depends list"

DEPENDS_ARGS=()
while IFS= read -r line; do
    # Skip blank lines and comments
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    DEPENDS_ARGS+=("--depends" "${line}")
done < "${SYS_DEPS_FILE}"

log "  ${#DEPENDS_ARGS[@]} --depends flags assembled"

# ---------------------------------------------------------------------------
# Step 6 — Run FPM
# ---------------------------------------------------------------------------
step "Running FPM"

fpm \
    --input-type  dir \
    --output-type deb \
    --name        "${PACKAGE_NAME}" \
    --version     "${VERSION}" \
    --architecture "${ARCH}" \
    --description "NemoHeadUnit-Wireless v2 — Android Auto wireless head unit" \
    --url         "https://github.com/nemocrk/NemoHeadUnit-Wireless" \
    --maintainer  "nemocrk <nemocrk@users.noreply.github.com>" \
    --license     "GPL-2.0-only" \
    --after-install  "${POSTINST}" \
    --before-remove  "${PRERM}" \
    --deb-no-default-config-files \
    --package     "${OUTPUT_DIR}/${DEB_FILENAME}" \
    --chdir       "${STAGE_DIR}" \
    "${DEPENDS_ARGS[@]}" \
    .

log "Package written to: ${OUTPUT_DIR}/${DEB_FILENAME}"

# ---------------------------------------------------------------------------
# Step 7 — Verify
# ---------------------------------------------------------------------------
step "Verifying package"

echo
echo "--- dpkg-deb --info ---"
dpkg-deb --info "${OUTPUT_DIR}/${DEB_FILENAME}"

echo
echo "--- dpkg-deb --contents (first 40 lines) ---"
dpkg-deb --contents "${OUTPUT_DIR}/${DEB_FILENAME}" | head -n 40

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
log "✔  Build successful: ${OUTPUT_DIR}/${DEB_FILENAME}"
echo
log "Install on target:"
log "  sudo apt install --fix-broken ./${DEB_FILENAME}"
log "  # or:"
log "  sudo dpkg -i ./${DEB_FILENAME} && sudo apt-get install -f"
echo
