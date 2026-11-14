"""性能基线：测最影响体感的几处，作为优化的对照。

## 为什么先测

不测就优化等于瞎猜。这里先量出真实数字，改完再量一次对比。

## 用户能感知的慢，按优先级

1. **启动时间** —— 双击图标到能用
2. **候选条弹出** —— 按热键到看见面板
3. **设置页切换** —— 点侧边栏到页面出现
4. **主题切换 / 拖动滑块** —— 之前修过一次卡顿
5. **情绪判定** —— 打字时每次采样都跑

测量方式：真实构造 AppRuntime，用 `processEvents` 驱动，取多轮最小值。
"""
import os
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APPDATA = tempfile.mkdtemp(prefix="perf_")
os.environ["APPDATA"] = APPDATA
os.environ["KAOMOJI_SKIP_WELCOME"] = "1"

_orig_data_dir = None


def _patch_appdata():
    """让 app_data_dir 指向临时目录，避免污染真实配置。"""
    global _orig_data_dir
    from core import runtime
    _orig_data_dir = runtime.app_data_dir
    runtime.app_data_dir = lambda: os.path.join(APPDATA, "KaomojiAssistant")


_patch_appdata()

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
app.setQuitOnLastWindowClosed(False)

# ⚠ 必须屏蔽两个**启动弹窗**，否则基准脚本会卡死等用户点击：
#   * 欢迎页 / 更新弹窗（模态 exec）
#   * startup_notice 插件的提示（默认曾是模态弹窗）
from core import version as _ver  # noqa: E402

_ver.should_show_welcome = lambda force=False: False
_ver.should_show_update_popup = lambda: False

from ui.fluent_icons import ensure_icon_font  # noqa: E402
ensure_icon_font()

from core.config_store import load_config  # noqa: E402


def bench(fn, n=5, warmup=1):
    """跑 n 次取最小值（排除首次缓存预热的影响）。"""
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000)
    return min(ts), statistics.median(ts)


def main():
    print("=" * 60)
    print("性能基线")
    print("=" * 60)

    # ---------- 1. 启动（构造 AppRuntime） ----------
    from app_runtime import AppRuntime

    cfg = load_config()
    cfg["plugins_enabled"] = {}

    t0 = time.perf_counter()
    rt = AppRuntime(app, cfg)
    boot = (time.perf_counter() - t0) * 1000
    app.processEvents()
    print("\n[启动]")
    print("  AppRuntime 构造      : %7.1f ms" % boot)
    print("  进程内存（RSS）      : %7.0f MB" % _rss_mb())

    unified = rt.unified
    unified.show()
    app.processEvents()

    # ---------- 2. 页面切换 ----------
    print("\n[页面切换]")
    pages = ["home", "library", "custom", "triggers", "search",
             "settings", "about"]
    for name in pages:
        ms, _med = bench(lambda n=name: (_set_page(rt, n),
                                         app.processEvents()), n=3)
        print("  -> %-10s        : %7.1f ms" % (name, ms))

    # 全部跑一遍（切回来切回去）
    def cycle():
        for name in pages:
            _set_page(rt, name)
            app.processEvents()

    ms, _ = bench(cycle, n=3)
    print("  完整一轮（7 页）     : %7.1f ms" % ms)

    # ---------- 3. 设置应用（改配置） ----------
    print("\n[应用设置]")

    def change_theme():
        c = dict(rt.config)
        c["theme"] = "dark" if c.get("theme") == "light" else "light"
        rt.apply_settings(c)
        app.processEvents()

    ms, _ = bench(change_theme, n=3)
    print("  切换主题             : %7.1f ms" % ms)

    def change_alpha():
        c = dict(rt.config)
        c["panel_alpha"] = 0.8 if c.get("panel_alpha", 1) != 0.8 else 0.7
        rt.apply_settings(c)
        app.processEvents()

    ms, _ = bench(change_alpha, n=5)
    print("  改不透明度           : %7.1f ms" % ms)

    # ---------- 4. 候选条 ----------
    print("\n[候选条]")
    picker = rt.window

    def manual():
        picker._manual_items()

    ms, _ = bench(manual, n=10)
    print("  计算候选列表         : %7.2f ms" % ms)

    def show_hide():
        picker.show()
        app.processEvents()
        picker.hide()
        app.processEvents()

    ms, _ = bench(show_hide, n=5)
    print("  显示+隐藏            : %7.1f ms" % ms)

    # ---------- 5. 情绪判定 ----------
    print("\n[情绪判定]")
    from core import emotion_classifier
    clf = emotion_classifier.shared()
    texts = ["今天一点都不开心", "好累啊", "哈哈哈哈哈", "好萌", "吃饭了吗"]
    for t in texts:
        clf.detect(t)
    ms, _ = bench(lambda: [clf.detect(t) for t in texts], n=20)
    print("  5 句判定             : %7.2f ms  (单句 %.2f)"
          % (ms, ms / len(texts)))

    print()
    print("=" * 60)
    return 0


def _rss_mb():
    """当前进程常驻内存（MB）。

    用 Windows 的 psapi.GetProcessMemoryInfo —— 项目零第三方依赖，
    不一定有 psutil，所以走系统 API。

    ⚠ 两个坑：
      * `GetProcessMemoryInfo` 在 **psapi.dll**，不在 kernel32；
      * 必须声明 argtypes，否则 64 位句柄会被截断成 int32 而失败。
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        k32.GetCurrentProcess.restype = wt.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
        psapi.GetProcessMemoryInfo.restype = wt.BOOL

        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(),
                                      ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / 1e6
    except Exception:
        pass
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        return 0.0


def _set_page(rt, name):
    try:
        rt.unified._nav.set_page(name)
    except Exception:
        try:
            rt.unified._set_page(name)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
