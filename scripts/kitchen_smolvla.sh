#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_ROOT"
export PYTHONPATH="$TASK_ROOT"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONUNBUFFERED=1
export MUJOCO_GL="${MUJOCO_GL:-egl}"
export LP_NUM_THREADS="${LP_NUM_THREADS:-8}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-$TASK_ROOT/.cache/kitchen_vla/datasets}"
export HF_LEROBOT_HOME="${HF_LEROBOT_HOME:-$TASK_ROOT/.cache/kitchen_vla/lerobot}"
export TOKENIZERS_PARALLELISM=false
VLA_COMMAND="${1:?render, replay, export, train, serve, rollout, evaluate}"
shift
case "$VLA_COMMAND" in
  render) exec .venv/bin/python -B -m integration.kitchen.vla.dataset render "$@" ;;
  replay) exec .venv/bin/python -B -m integration.kitchen.vla.dataset replay "$@" ;;
  export) exec thirdparty/smolvla/.venv/bin/python -B -m integration.kitchen.vla.dataset export "$@" ;;
  train) exec thirdparty/smolvla/.venv/bin/python -B thirdparty/smolvla/train.py "$@" ;;
  serve) exec thirdparty/smolvla/.venv/bin/python -B -m integration.kitchen.vla.server "$@" ;;
  rollout) exec .venv/bin/python -B -m integration.kitchen.vla.rollout "$@" ;;
  evaluate) exec .venv/bin/python -B -m integration.kitchen.vla.evaluate "$@" ;;
  *) echo 'Unknown SmolVLA command' >&2; exit 2 ;;
esac
