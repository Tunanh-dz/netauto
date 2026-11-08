"""Đo thời gian một vòng thu thập khi số thiết bị tăng, tuần tự (1 luồng) so với song song.

Chạy: python3 scripts/scale_test.py [số thiết bị thêm tối đa]
Cần tạo sẵn các container clab-entlab-sc1, sc2, ... (xem hướng dẫn Ngày 21).
"""
import statistics
import sys
import time

from netauto.collector import collect_all
from netauto.intent import load_intent
from netauto.inventory import inventory_from_intent

def measure(inventory: dict, workers: int, repeats: int = 3) -> float:
    collect_all(inventory, workers=workers)      # chạy làm nóng, không tính thời gian
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        collect_all(inventory, workers=workers)
        times.append(time.perf_counter() - start)
    return statistics.mean(times)

def measure(inventory: dict, workers: int, repeats: int = 3) -> float:
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        collect_all(inventory, workers=workers)
        times.append(time.perf_counter() - start)
    return statistics.mean(times)


def main() -> None:
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    base = inventory_from_intent(load_intent())
    print(f"{'số thiết bị':>12} {'tuần tự (s)':>13} {'song song (s)':>14}")
    for extra in range(0, top + 1, max(1, top // 4)):
        inventory = dict(base)
        inventory.update({f"sc{i}": "host" for i in range(1, extra + 1)})
        sequential = measure(inventory, workers=1)
        parallel = measure(inventory, workers=8)
        print(f"{len(inventory):>12} {sequential:>13.2f} {parallel:>14.2f}")


if __name__ == "__main__":
    main()