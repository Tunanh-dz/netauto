#!/usr/bin/env bash
set -euo pipefail
LAB=entlab
cd "$(dirname "$0")"
c() { echo "clab-$LAB-$1"; }

for n in sw1 sw2; do
  echo ">> Cấu hình $n"
  docker exec -i "$(c $n)" sh < "configs/$n.sh"
done

for h in h-sales h-guest h-eng h-mgmt; do
  docker exec "$(c $h)" ip link set eth1 up
done
echo "Xong."
