#!/usr/bin/env bash
# Kiểm tra nhanh môi trường sau Ngày 1-2. Chỉ đọc, không thay đổi gì.
LAB=entlab
PASS=0; FAIL=0; WARN=0
ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1"; FAIL=$((FAIL+1)); }
warn() { echo "WARN  $1"; WARN=$((WARN+1)); }
chk()  { local d=$1; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }

echo "== 1. Công cụ và Docker =="
for t in git make curl jq tcpdump traceroute docker containerlab; do chk "có lệnh $t" command -v $t; done
chk "docker chạy không cần sudo" docker info
chk "containerlab chạy được" containerlab version
for img in quay.io/frrouting/frr:10.4.5 nicolaka/netshoot:latest; do
  chk "đã tải image $img" docker image inspect "$img"
done
lsmod | grep -q 8021q && ok "module 8021q đã nạp" || bad "module 8021q chưa nạp (chạy: sudo modprobe 8021q)"
[ -f /etc/modules-load.d/8021q.conf ] && ok "8021q tự nạp khi khởi động" || warn "chưa có /etc/modules-load.d/8021q.conf"
if [ -n "$(git config --global user.name)" ] && [ -n "$(git config --global user.email)" ]; then
  ok "git user.name và user.email đã đặt"
else bad "chưa đặt git user.name / user.email"; fi

echo; echo "== 2. Project ~/netauto =="
cd ~/netauto 2>/dev/null || { bad "không có thư mục ~/netauto"; exit 1; }
[ -d ~/netauro ] && warn "còn thư mục thừa ~/netauro (xóa: rmdir ~/netauro)" || ok "không có thư mục thừa netauro"
for f in .gitignore lab/topology.clab.yml lab/configs/daemons docs/notebook.md; do
  [ -f "$f" ] && ok "có file $f" || bad "thiếu file $f"
done
grep -q '^name: entlab' lab/topology.clab.yml && ok "topology đặt name: entlab" || bad "topology không có dòng name: entlab"
n=$(grep -cE '^    (r1|r2|sw1|sw2|h-sales|h-guest|h-eng|h-mgmt):' lab/topology.clab.yml)
[ "$n" -eq 8 ] && ok "topology khai báo đủ 8 node" || bad "topology chỉ thấy $n/8 node"
l=$(grep -c 'endpoints:' lab/topology.clab.yml)
[ "$l" -eq 7 ] && ok "topology có đủ 7 dây (links)" || bad "topology có $l/7 dây"
grep -q '^ospfd=yes' lab/configs/daemons && ok "file daemons bật ospfd" || bad "file daemons chưa có ospfd=yes"
grep -q 'clab-entlab-' ~/.bashrc && ok ".bashrc có lệnh gõ tắt dx/dxq (clab-entlab)" || bad ".bashrc chưa có dx/dxq đúng tên lab"
grep -q 'enterprise_lan' ~/.bashrc && bad ".bashrc còn tên lab cũ enterprise_lan" || ok ".bashrc không còn tên lab cũ"

echo; echo "== 3. Git =="
git rev-parse --is-inside-work-tree >/dev/null 2>&1 && ok "~/netauto là repo Git" || bad "~/netauto chưa phải repo Git"
c=$(git rev-list --count HEAD 2>/dev/null || echo 0)
[ "$c" -ge 1 ] && ok "đã có $c commit" || bad "chưa có commit nào"
[ -z "$(git status --porcelain)" ] && ok "không còn thay đổi chưa commit" || { warn "còn thay đổi chưa commit:"; git status --short; }
[ -n "$(git remote -v)" ] && ok "đã gắn remote GitHub" || warn "chưa gắn remote GitHub (chưa sao lưu ra ngoài VM)"
echo "   Các commit:"; git log --oneline | head -5 | sed 's/^/   /'

echo; echo "== 4. Lab Containerlab =="
for node in r1 r2 sw1 sw2 h-sales h-guest h-eng h-mgmt; do
  [ "$(docker inspect -f '{{.State.Running}}' clab-$LAB-$node 2>/dev/null)" = "true" ] \
    && ok "container $node đang chạy" || bad "container $node KHÔNG chạy"
done
hasif() { docker exec "clab-$LAB-$1" ip link show "$2" >/dev/null 2>&1; }
for node in r1 r2; do for i in eth1 eth2; do hasif $node $i && ok "$node có $i" || bad "$node thiếu $i"; done; done
for node in sw1 sw2; do for i in eth1 eth2 eth3; do hasif $node $i && ok "$node có $i" || bad "$node thiếu $i"; done; done
for node in h-sales h-guest h-eng h-mgmt; do hasif $node eth1 && ok "$node có eth1" || bad "$node thiếu eth1"; done
for r in r1 r2; do
  d=$(docker exec clab-$LAB-$r vtysh -c "show daemons" 2>/dev/null)
  echo "$d" | grep -q ospfd && ok "$r: FRR đang chạy ospfd" || { warn "$r: chưa thấy ospfd. Kết quả show daemons: [$d]"; }
done
docker exec clab-$LAB-r1 ip -V 2>/dev/null | grep -q iproute2 && ok "r1 có iproute2 đầy đủ" || warn "r1: công cụ ip có thể là bản rút gọn"

echo; echo "== 5. Tài nguyên VM =="
echo "   IP VM:"; ip -br addr | grep -v '^lo' | sed 's/^/   /'
echo "   Đĩa:  $(df -h / | tail -1)"
echo "   RAM:  $(free -h | sed -n 2p)"

echo; echo "== Tổng kết: PASS=$PASS  WARN=$WARN  FAIL=$FAIL =="
