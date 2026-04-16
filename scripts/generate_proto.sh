#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python -m grpc_tools.protoc \
  -I "${ROOT_DIR}/proto" \
  --python_out="${ROOT_DIR}/generated" \
  --grpc_python_out="${ROOT_DIR}/generated" \
  "${ROOT_DIR}/proto/memory_game.proto"

echo "Protobuf generado en ${ROOT_DIR}/generated"
