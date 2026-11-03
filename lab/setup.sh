#!/usr/bin/env bash
set -euo pipefail
LAB=entlab
cd "$(dirname "$0")"
c() { echo "clab-$LAB-$1"; }

wait_frr() {
  for _ in $(seq 1 30); do
    if docker exec "$(c "$1")" vtysh -c "show version" 2>/dev/null | grep -q FRRouting; then return 0; fi
    sleep 1
  done
  echo "FRR trên $1 chưa sẵn sàng" >&2; return 1
}

for n in sw1 sw2 r1 r2; do
  echo ">> Cấu hình $n"
  docker exec -i "$(c $n)" sh < "configs/$n.sh"
done

host() {   # host <tên> <ip/mask> <gateway>
  echo ">> Cấu hình $1"
  docker exec "$(c "$1")" sh -c "ip link set eth1 up; ip addr add $2 dev eth1 2>/dev/null || true; ip route replace default via $3"
}
host h-sales 192.168.10.10/24 192.168.10.1
host h-guest 192.168.30.10/24 192.168.30.1
host h-eng   192.168.20.10/24 192.168.20.1
host h-mgmt  192.168.99.10/24 192.168.99.1

for r in r1 r2; do
  echo ">> FRR $r"
  wait_frr "$r"
  docker cp "configs/$r.vtysh" "$(c $r):/tmp/$r.vtysh"
  docker exec "$(c $r)" vtysh -f "/tmp/$r.vtysh" 2>&1 | grep -vE "vtysh.conf|frr.conf" || true
done
echo "Xong."
