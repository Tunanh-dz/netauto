#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TOPO=lab/topology.clab.yml

case "${1:-}" in
  up)     sudo containerlab deploy -t "$TOPO"; bash lab/setup.sh ;;
  down)   sudo containerlab destroy -t "$TOPO" --cleanup ;;
  reset)  sudo containerlab destroy -t "$TOPO" --cleanup
          sudo containerlab deploy -t "$TOPO"
          bash lab/setup.sh ;;
  config) bash lab/setup.sh ;;
  *)      echo "Dùng: bash scripts/lab.sh {up|down|reset|config}" ;;
esac
