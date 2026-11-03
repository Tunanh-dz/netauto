#!/usr/bin/env bash
# Đo thời gian OSPF mất và phục hồi khi tắt/bật eth2 của r1
c() { echo "clab-entlab-$1"; }
full() { docker exec "$(c r1)" vtysh -c "show ip ospf neighbor" 2>/dev/null | grep -q Full; }

full || { echo "Neighbor chưa Full. Hãy chạy verify trước."; exit 1; }

t0=$(date +%s.%N)
docker exec "$(c r1)" ip link set eth2 down
for _ in $(seq 1 300); do full || break; sleep 0.2; done
t1=$(date +%s.%N)

docker exec "$(c r1)" ip link set eth2 up
t2=$(date +%s.%N)
for _ in $(seq 1 240); do full && break; sleep 0.5; done
t3=$(date +%s.%N)

awk -v a="$t0" -v b="$t1" -v c="$t2" -v d="$t3" 'BEGIN {
  printf "Phát hiện mất neighbor sau: %.1f giây\n", b - a
  printf "Phục hồi Full sau khi bật lại: %.1f giây\n", d - c }'
