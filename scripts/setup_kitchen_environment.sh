#!/usr/bin/env bash
# Base simulation remains image/ML independent; RL is explicitly opt-in.
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_ROOT"
WITH_RL=0
for arg in "$@"; do
  case "$arg" in
    --with-rl) WITH_RL=1 ;;
    --help) echo 'Usage: bash scripts/setup_kitchen_environment.sh [--with-rl]'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
if [[ ! -x .venv/bin/python ]]; then
  echo 'Create the base simulation .venv using the Linux setup guide first.' >&2
  exit 1
fi
if [[ "$WITH_RL" == 0 ]]; then
  .venv/bin/python -B -c 'import mujoco,numpy,h5py; print("Base simulation ready; RL dependencies were not installed.")'
else
  # Preserve the exact physics version; only opt-in installs learning dependencies.
  uv pip install --python .venv/bin/python --torch-backend cu128 \
    -r robot_arm/requirements-kitchen-rl.txt
  .venv/bin/python -B thirdparty/bootstrap.py discodemo
fi
