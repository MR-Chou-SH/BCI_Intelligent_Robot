#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export LP_NUM_THREADS=8
export PYTHONUNBUFFERED=1
export MUJOCO_GL=egl
exec "$ROOT_DIR/.venv/bin/python" -B -m integration.kitchen.tools.render_dataset "$@"
