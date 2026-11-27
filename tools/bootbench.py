"""稳定的启动测量：独立进程跑 N 次，取最小值。

## 为什么要独立进程

同一进程里反复构造 AppRuntime 会累积状态（信号、定时器、Qt 缓存），
测出来的第二轮往往比第一轮快很多，不能反映真实启动成本。
每次开新进程测才准。

## 为什么要多轮

单次测量波动很大（实测 1.7s ~ 6.9s），受系统负载、磁盘缓存影响。
取 N 次的最小值最接近"用户双击后最理想的情况"，
中位数反映"典型情况"，两个都报出来。
"""
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CHILD = r'''
import os, sys, time, tempfile
sys.path.insert(0, r"%ROOT%")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="boot_")
from core import runtime
runtime.app_data_dir = lambda: os.path.join(
    os.environ["APPDATA"], "KaomojiAssistant")
from core import version as ver
ver.should_show_welcome = lambda force=False: False
ver.should_show_update_popup = lambda: False

t_import = time.perf_counter()
from PySide6.QtWidgets import QApplication
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
t_qt = time.perf_counter()

from ui.fluent_icons import ensure_icon_font
ensure_icon_font()
from core.config_store import load_config
from app_runtime import AppRuntime

cfg = load_config(); cfg['developer_mode'] = False; cfg['plugins_enabled'] = {}
t0 = time.perf_counter()
rt = AppRuntime(app, cfg)
t1 = time.perf_counter()
app.processEvents()
t2 = time.perf_counter()

print("IMPORT %.0f" % ((t_qt - t_import) * 1000))
print("BUILD %.0f" % ((t1 - t0) * 1000))
print("EVENTS %.0f" % ((t2 - t1) * 1000))
print("TOTAL %.0f" % ((t2 - t_import) * 1000))
'''


def run_once():
    code = CHILD.replace("%ROOT%", ROOT)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=180, cwd=ROOT)
    out = {}
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def main():
    n = 5
    print("=" * 56)
    print("启动耗时（%d 个独立进程，单位 ms）" % n)
    print("=" * 56)

    results = []
    for i in range(n):
        r = run_once()
        if not r:
            print("  第 %d 次：失败" % (i + 1))
            continue
        results.append(r)
        print("  第 %d 次: import=%4d  build=%5d  events=%3d  total=%5d"
              % (i + 1, r.get("IMPORT", 0), r.get("BUILD", 0),
                 r.get("EVENTS", 0), r.get("TOTAL", 0)))

    if not results:
        print("没有成功的测量")
        return 1

    print()
    keys = ["IMPORT", "BUILD", "EVENTS", "TOTAL"]
    for k in keys:
        vals = [r.get(k, 0) for r in results]
        print("  %-8s 最小 %4d 中位 %4d 最大 %4d"
              % (k, min(vals), int(statistics.median(vals)), max(vals)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
