#!/usr/bin/env bash
# Kiểm tra Ngày 9. Chỉ đọc, không thay đổi cấu hình lab hay code.
cd ~/netauto || exit 1
export PYTHONPATH=src
PASS=0; FAIL=0; WARN=0
ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1"; FAIL=$((FAIL+1)); }
warn() { echo "WARN  $1"; WARN=$((WARN+1)); }

echo "== 1. File Python =="
for f in src/netauto/__init__.py src/netauto/transport.py src/netauto/models.py \
         src/netauto/inventory.py src/netauto/parsers.py src/netauto/collector.py \
         src/netauto/cli.py src/netauto/diff.py \
         tests/test_transport.py tests/test_parsers.py tests/test_collect_lab.py tests/test_diff.py; do
  [ -f "$f" ] && ok "có $f" || bad "thiếu $f"
done
python3 -m compileall -q src tests >/dev/null 2>&1 && ok "mọi file Python không lỗi cú pháp" || bad "có file Python lỗi cú pháp"

echo; echo "== 2. File cấu hình vtysh =="
for r in r1 r2; do
  f=lab/configs/$r.vtysh
  [ -f "$f" ] || { bad "thiếu $f"; continue; }
  grep -q "configure terminal" "$f" && bad "$f còn dòng 'configure terminal' (gây Unknown command)" || ok "$f không còn 'configure terminal'"
  grep -q "^ passive-interface" "$f" && warn "$f còn passive-interface (đã lỗi thời)" || ok "$f không dùng passive-interface"
  grep -q "ip ospf passive" "$f" && ok "$f dùng ip ospf passive" || warn "$f chưa có ip ospf passive"
done

echo; echo "== 3. .gitignore =="
for pat in 'clab-\*/' '__pycache__/' '\.venv/' '^data/'; do
  grep -q "$pat" .gitignore && ok ".gitignore có $pat" || warn ".gitignore thiếu $pat"
done

echo; echo "== 4. Test =="
out=$(python3 -m unittest discover -s tests 2>&1)
echo "$out" | tail -3 | sed 's/^/   /'
echo "$out" | tail -1 | grep -q "^OK" && ok "toàn bộ test đạt" || bad "có test lỗi (chạy: python3 -m unittest discover -s tests -v)"

echo; echo "== 5. Thu thập từ lab thật =="
python3 - << 'PYEOF' && ok "collector đọc đủ 8 thiết bị, reachable, không lỗi" || bad "collector có thiết bị lỗi hoặc không reachable"
import sys
from netauto.collector import collect_all
s = collect_all()
bad = [d for d in s.devices if not d.reachable or d.errors]
for d in s.devices:
    print(f"   {d.node:<8} reach={d.reachable} if={len(d.interfaces)} route={len(d.routes)} "
          f"vlanport={len(d.vlans)} ospf={[n.state for n in d.ospf_neighbors]} errors={d.errors}")
sys.exit(1 if bad or len(s.devices) != 8 else 0)
PYEOF

echo; echo "== 6. Snapshot và diff =="
n=$(ls data/snapshots/*.json 2>/dev/null | wc -l)
[ "$n" -ge 1 ] && ok "có $n file snapshot" || warn "chưa có snapshot nào (chạy: python3 -m netauto.cli collect --save)"
if [ "$n" -ge 1 ]; then
  A=$(ls -t data/snapshots/*.json | head -1)
  python3 -m netauto.cli diff "$A" "$A" 2>&1 | grep -q "Không có thay đổi" && ok "diff của một snapshot với chính nó = không có thay đổi" || bad "diff cùng một file lại báo có thay đổi"
fi

echo; echo "== 7. Git =="
git log --oneline | head -5 | sed 's/^/   /'
[ -z "$(git status --porcelain)" ] && ok "không còn thay đổi chưa commit" || { warn "còn thay đổi chưa commit:"; git status --short | sed 's/^/   /'; }
git log --oneline | grep -q "day 9" && ok "đã có commit Ngày 9" || warn "chưa thấy commit có chữ 'day 9'"
[ -n "$(git remote -v)" ] && ok "đã gắn remote GitHub" || warn "chưa gắn remote GitHub"

echo; echo "== 8. Lab =="
bash scripts/verify.sh 2>&1 | grep -E "FAIL|KẾT QUẢ" | sed 's/^/   /'

echo; echo "== Tổng kết: PASS=$PASS  WARN=$WARN  FAIL=$FAIL =="
