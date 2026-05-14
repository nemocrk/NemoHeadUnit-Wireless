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
#   2.  Validates required tools (fpm, dpkg-deb)
#   3.  Assembles a staging directory (build/stage/) mirroring the
#       final filesystem layout:
#         /opt/nemo-headunit/
#           v2/               ← application source (v2/)
#           services/         ← ap_manager_service
#           hardware_fixes/   ← platform-specific fix scripts + registry
#           bus_broker.py     ← ZMQ bus broker entry point
#           environment.yml   ← Conda env spec (built on target by postinst)
#         /usr/lib/systemd/system/
#           org.nemo.APManager.service
#         /etc/dbus-1/system.d/
#           org.nemo.APManager.conf
#         /usr/share/polkit-1/actions/
#           org.nemo.APManager.policy
#         /etc/polkit-1/rules.d/
#           org.nemo.bluetooth.rules
#   4.  Builds the .deb with FPM
#   5.  Runs dpkg-deb --info + dpkg-deb --contents to verify the package
#
# NOTE: The Conda environment is NOT pre-built into the .deb.
#       postinst runs 'conda env create' on the target machine so that
#       all native libs (glibc, ALSA, VA-API …) are compiled for the
#       actual target OS — avoiding ABI mismatches / segfaults.
#
# Requirements (build machine only):
#   fpm     (gem install fpm)
#   ruby    (for fpm)
#   dpkg    (to verify)
#
# The resulting .deb declares APT deps from packaging/system-deps.txt.
# Conda must be available on the TARGET machine (postinst bootstraps it
# via packaging/bootstrap_conda.sh if not found).
#
# Arch note:
#   --arch arm64 cross-compiles the .deb metadata only.
#   For true arm64 binaries, run this script ON an arm64 machine.

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
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BUILD_DIR="${REPO_ROOT}/build"
STAGE_DIR="${BUILD_DIR}/stage"
OUTPUT_DIR="${REPO_ROOT}/${OUTPUT_DIR}"

VERSION_FILE="${REPO_ROOT}/VERSION"
ENV_YML="${REPO_ROOT}/environment.yml"
SYS_DEPS_FILE="${REPO_ROOT}/packaging/system-deps.txt"
POSTINST="${REPO_ROOT}/packaging/postinst"
PRERM="${REPO_ROOT}/packaging/prerm"
BT_RULES="${REPO_ROOT}/packaging/org.nemo.bluetooth.rules"
HW_FIXES_SRC="${REPO_ROOT}/packaging/hardware_fixes"
BOOTSTRAP_CONDA="${REPO_ROOT}/packaging/bootstrap_conda.sh"

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

for tool in fpm dpkg-deb; do
    if ! command -v "${tool}" &>/dev/null; then
        die "'${tool}' not found. Install it before running this script."
    fi
    log "  ${tool}: $(command -v "${tool}")"
done

[[ -f "${ENV_YML}" ]] || die "environment.yml not found at ${REPO_ROOT}/environment.yml"
log "  environment.yml: found"

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
step "Assembling staging directory"

APP_OPT="${STAGE_DIR}/opt/nemo-headunit"
mkdir -p "${APP_OPT}"

log "  Copying environment.yml (Conda env will be built on target)"
cp "${ENV_YML}" "${APP_OPT}/environment.yml"

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

if [ -f "${BOOTSTRAP_CONDA}" ]; then
    log "  Copying bootstrap_conda.sh"
    cp "${BOOTSTRAP_CONDA}" "${APP_OPT}/bootstrap_conda.sh"
    chmod +x "${APP_OPT}/bootstrap_conda.sh"
fi

# Prune bytecode / tests
log "  Pruning bytecode and test files"
find "${APP_OPT}/v2" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "${APP_OPT}/v2" -name '*.pyc' -delete 2>/dev/null || true
find "${APP_OPT}/v2" -name '*.pyo' -delete 2>/dev/null || true
rm -rf "${APP_OPT}/v2/tests" 2>/dev/null || true

# —— /usr/lib/systemd/system/ ——
SYSTEMD_STAGE="${STAGE_DIR}/usr/lib/systemd/system"
mkdir -p "${SYSTEMD_STAGE}"
log "  Copying systemd unit"
cp "${SERVICES_SRC}/org.nemo.APManager.service" "${SYSTEMD_STAGE}/"
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
# Step 6 — Verify
# ---------------------------------------------------------------------------
step "Verifying package"

echo
echo "--- dpkg-deb --info ---"
dpkg-deb --info "${OUTPUT_DIR}/${DEB_FILENAME}"

echo
echo "--- dpkg-deb --contents (first 40 lines) ---"
# Usa una subshell con set +o pipefail per evitare il "Broken pipe"
# che dpkg-deb emette quando head chiude la pipe prima della fine.
{ dpkg-deb --contents "${OUTPUT_DIR}/${DEB_FILENAME}" || true; } | head -n 40

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
log ""
log "NOTE: postinst creerà il Conda env su /opt/nemo-headunit/env (~3-5 min)."
log "      Assicurati che la macchina target abbia accesso a internet."
echo
