#!/usr/bin/env bash
# Bảo đảm lab dùng được: hạ tầng hỏng thì dựng lại, cấu hình lệch thì áp dụng intent.
# Dựa vào mã thoát của apply: 0 = khỏe, 2 = lệch cấu hình, 3 = hạ tầng hỏng (mục CHẶN).
set -u
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src"
missing=$(python3 - << 'PY'
import yaml
from netauto.intent import load_intent
from netauto.inventory import inventory_from_intent
nodes = yaml.safe_load(open("lab/topology.clab.yml"))["topology"]["nodes"]
print(" ".join(n for n in inventory_from_intent(load_intent()) if n not in nodes))
PY
)
if [ -n "$missing" ]; then
  echo "Có trong intent nhưng chưa có trong lab/topology.clab.yml: $missing"
  echo "Dựng lại lab cũng không tạo ra chúng. Hãy thêm vào topology rồi chạy lại."
  exit 3
fi

python3 -m netauto.apply > /dev/null 2>&1
code=$?
if [ "$code" -eq 3 ]; then
  echo "Hạ tầng hỏng (kế hoạch có mục CHẶN). Dựng lại lab..."
  bash scripts/lab.sh reset
  echo "Chờ 20 giây cho OSPF hội tụ..."
  sleep 20
  python3 -m netauto.apply --yes
elif [ "$code" -eq 2 ]; then
  echo "Cấu hình lệch intent. Áp dụng..."
  python3 -m netauto.apply --yes
else
  echo "Lab khỏe, không cần làm gì."
fi
python3 -m netauto.plan > /dev/null
echo "Kiểm tra cuối (plan), mã thoát: $?"