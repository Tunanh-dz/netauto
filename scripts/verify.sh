#!/usr/bin/env bash
LAB=entlab
FAIL=0
c() { echo "clab-$LAB-$1"; }
check() { local d=$1; shift
  if "$@" >/dev/null 2>&1; then echo "PASS  $d"; else echo "FAIL  $d"; FAIL=1; fi; }
# Như check nhưng thử lại tối đa 30 giây (cho các mục cần chờ OSPF hội tụ)
check_retry() { local d=$1; shift
  for _ in $(seq 1 15); do
    if "$@" >/dev/null 2>&1; then echo "PASS  $d"; return; fi
    sleep 2
  done
  echo "FAIL  $d"; FAIL=1; }
ospf_full() { docker exec "$(c "$1")" vtysh -c "show ip ospf neighbor" 2>/dev/null | grep -q Full; }

echo "Chờ OSPF hội tụ (tối đa 60 giây)..."
for _ in $(seq 1 30); do
  if ospf_full r1 && ospf_full r2; then break; fi
  sleep 2
done

echo "== Lớp 2: VLAN trên switch =="
check "sw1 eth2 là access VLAN 10" sh -c "docker exec $(c sw1) bridge vlan show dev eth2 | grep -q '10 PVID'"
check "sw1 eth3 là access VLAN 30" sh -c "docker exec $(c sw1) bridge vlan show dev eth3 | grep -q '30 PVID'"
check "sw2 eth2 là access VLAN 20" sh -c "docker exec $(c sw2) bridge vlan show dev eth2 | grep -q '20 PVID'"
check "sw2 eth3 là access VLAN 99" sh -c "docker exec $(c sw2) bridge vlan show dev eth3 | grep -q '99 PVID'"
check "sw1 eth1 là trunk 10 và 30" sh -c "docker exec $(c sw1) bridge vlan show dev eth1 | grep -qw 10 && docker exec $(c sw1) bridge vlan show dev eth1 | grep -qw 30"
check "sw2 eth1 là trunk 20 và 99" sh -c "docker exec $(c sw2) bridge vlan show dev eth1 | grep -qw 20 && docker exec $(c sw2) bridge vlan show dev eth1 | grep -qw 99"

echo "== Lớp 3: gateway =="
for h in "h-sales 192.168.10.1" "h-guest 192.168.30.1" "h-eng 192.168.20.1" "h-mgmt 192.168.99.1"; do
  set -- $h
  check "$1 ping gateway $2" docker exec "$(c $1)" ping -c1 -W2 "$2"
done
check "r1 ping r2 (10.0.12.2)" docker exec "$(c r1)" ping -c1 -W2 10.0.12.2

echo "== OSPF =="
check_retry "r1 có neighbor Full" ospf_full r1
check_retry "r2 có neighbor Full" ospf_full r2
check_retry "r1 có route tới 192.168.20.0/24" sh -c "docker exec $(c r1) ip route | grep -q '192.168.20.0/24'"
check_retry "r2 có route tới 192.168.10.0/24" sh -c "docker exec $(c r2) ip route | grep -q '192.168.10.0/24'"

echo "== Đầu-cuối: ping xuyên qua hai router =="
check_retry "h-sales ping h-eng"  docker exec "$(c h-sales)" ping -c1 -W2 192.168.20.10
check_retry "h-guest ping h-mgmt" docker exec "$(c h-guest)" ping -c1 -W2 192.168.99.10
check_retry "h-eng ping h-sales"  docker exec "$(c h-eng)" ping -c1 -W2 192.168.10.10

echo
[ "$FAIL" -eq 0 ] && echo "KẾT QUẢ: TẤT CẢ ĐẠT" || echo "KẾT QUẢ: CÓ MỤC KHÔNG ĐẠT"
exit $FAIL
