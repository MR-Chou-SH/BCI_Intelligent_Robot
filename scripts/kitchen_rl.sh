#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TASK_ROOT"
export PYTHONPATH="$TASK_ROOT:$TASK_ROOT/thirdparty/discodemo/upstream"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONUNBUFFERED=1
export TORCHINDUCTOR_CACHE_DIR="$TASK_ROOT/.cache/kitchen_rl/inductor"
RL_COMMAND="${1:?prepare, benchmark, smoke, train, collect}"
shift
case "$RL_COMMAND" in
  prepare) exec .venv/bin/python -B -m integration.kitchen.rl.anchors "$@" ;;
  benchmark) exec .venv/bin/python -B -m integration.kitchen.rl.benchmark "$@" ;;
  smoke) exec .venv/bin/python -B -m integration.kitchen.rl.smoke "$@" ;;
  train) exec .venv/bin/python -B thirdparty/discodemo/train.py "$@" ;;
  collect) exec .venv/bin/python -B -m integration.kitchen.rl.collect "$@" ;;
  *) echo 'Unknown RL command' >&2; exit 2 ;;
esac
