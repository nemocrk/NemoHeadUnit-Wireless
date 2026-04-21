#!/usr/bin/env bash
# compile_protos.sh
# Recursively finds all .proto files under third_party/open-android-auto,
# compiles them with grpc_tools.protoc, and mirrors the directory tree
# into v2/protos/ as *_pb2.py files.
#
# Usage:
#   bash scripts/compile_protos.sh
#
# Requirements:
#   pip install grpcio-tools

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_SRC="${REPO_ROOT}/third_party/open-android-auto"
PROTO_OUT="${REPO_ROOT}/v2/protos"

if [ ! -d "${PROTO_SRC}" ]; then
  echo "[ERROR] Submodule not found at ${PROTO_SRC}"
  echo "        Run: git submodule update --init --recursive"
  exit 1
fi

if ! python3 -c "import grpc_tools.protoc" 2>/dev/null; then
  echo "[ERROR] grpcio-tools not installed."
  echo "        Run: pip install grpcio-tools"
  exit 1
fi

echo "[INFO] Scanning: ${PROTO_SRC}"
echo "[INFO] Output:   ${PROTO_OUT}"
echo ""

COMPILED=0
FAILED=0

# Find every .proto file recursively
while IFS= read -r proto_file; do
  # Relative path from PROTO_SRC (e.g. oaa/wifi/WifiInfoResponse.proto)
  rel_path="${proto_file#${PROTO_SRC}/}"
  # Mirror directory in output (e.g. v2/protos/oaa/wifi/)
  out_dir="${PROTO_OUT}/$(dirname "${rel_path}")"
  mkdir -p "${out_dir}"

  echo -n "  Compiling ${rel_path} ... "

  if python3 -m grpc_tools.protoc \
      --proto_path="${PROTO_SRC}" \
      --python_out="${PROTO_OUT}" \
      "${rel_path}" 2>/tmp/protoc_err; then
    echo "OK"
    COMPILED=$((COMPILED + 1))
  else
    echo "FAILED"
    cat /tmp/protoc_err | sed 's/^/    /'
    FAILED=$((FAILED + 1))
  fi

done < <(find "${PROTO_SRC}" -name "*.proto" | sort)

echo ""
echo "[DONE] Compiled: ${COMPILED}  Failed: ${FAILED}"

# Create __init__.py in every output directory so Python can import them
find "${PROTO_OUT}" -type d | while read -r d; do
  touch "${d}/__init__.py"
done

echo "[INFO] __init__.py created in all output directories."
echo "[INFO] Import example:"
echo "         from v2.protos.oaa.wifi.WifiInfoResponse_pb2 import WifiInfoResponse"
