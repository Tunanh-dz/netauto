#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TOPO=lab/topology.clab.yml
export PYTHONPATH="$PWD/src"

# Báo cho monitor biết ta sắp dựng lại lab, để nó không báo động giả.
# Cửa sổ tự hết hạn sau 240 giây; lỗi ở đây không được cản việc dựng lab.
WID=""
window_start() {
  WID=$(python3 -m netauto.maintenance start --reason "lab.sh $1" --ttl 240 --print-id \
        2>/dev/null) || WID=""
  trap window_end EXIT
}
window_end() {
  [ -n "$WID" ] && python3 -m netauto.maintenance end "$WID" > /dev/null 2>&1 || true
}

case "${1:-}" in
  up)     window_start up
          sudo containerlab deploy -t "$TOPO"; bash lab/setup.sh ;;
  down)   window_start down
          sudo containerlab destroy -t "$TOPO" --cleanup ;;
  reset)  window_start reset
          sudo containerlab destroy -t "$TOPO" --cleanup
          sudo containerlab deploy -t "$TOPO"
          bash lab/setup.sh ;;
  config) bash lab/setup.sh ;;
  verify) bash scripts/verify.sh ;;
  *)      echo "Dùng: bash scripts/lab.sh {up|down|reset|config|verify}" ;;
esac