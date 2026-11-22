"""主题系统性能基准。

## 为什么单独做一个工具

主题切换耗时是**用户直接感知**的指标，但单元测试里测不准——测试进程
里的控件数量、页面构建状态都和真实使用不同。

而且历史上有过误导：某个函数"单独测 1ms"，实际换肤时是 1300ms，
因为单独测时样式已经贴过（幂等短路），根本没走真正的工作路径。

所以这里做**接近真实**的基准：完整启动 AppRuntime、把页面都建出来、
按真实路径切换主题，输出各段耗时。

用法：
    .venv\\Scripts\\python.exe tools\\bench_theme.py
    .venv\\Scripts\\python.exe tools\\bench_theme.py --pages   # 只建首页（更快）
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="bench_theme_")

from core import runtime  # noqa: E402

runtime.app_data_dir = lambda: os.path.join(os.environ["APPDATA"], "K")

from core import version as ver  # noqa: E402

ver.should_show_welcome = lambda force=False: False
ver.should_show_update_popup = lambda: False

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
app.setQuitOnLastWindowClosed(False)

from ui.fluent_icons import ensure_icon_font  # noqa: E402
ensure_icon_font()

from core.config_store import load_config  # noqa: E402
from app_runtime import AppRuntime  # noqa: E402

ALL_PAGES = ("library", "custom", "triggers", "search",
             "settings", "about", "plugin")


def main():
    build_pages = "--pages" not in sys.argv

    t0 = time.perf_counter()
    rt = AppRuntime(app, load_config())
    app.processEvents()
    boot_ms = (time.perf_counter() - t0) * 1000
    print("启动: %.0f ms  控件=%d" % (boot_ms, len(app.allWidgets())))

    u = rt.unified
    u.show()
    app.processEvents()

    if build_pages:
        for k in ALL_PAGES:
            try:
                u._page(k)
            except Exception:
                pass
        app.processEvents()
    print("页面就绪: 控件=%d%s" % (
        len(app.allWidgets()), "" if build_pages else "（仅首页）"))

    # ---- 单步成本 ----
    print()
    print("单步成本（直接调用，不含动画）:")

    def _t(fn, *a):
        t = time.perf_counter()
        fn(*a)
        return (time.perf_counter() - t) * 1000

    mode = "dark" if rt.theme_obj.name != "dark" else "light"
    print("  _apply_app_theme     %7.0f ms" % _t(rt._apply_app_theme, mode))
    cfg = dict(rt.config)
    cfg["theme_mode"] = "dark" if mode == "dark" else "light"
    print("  unified.apply_config %7.0f ms" % _t(u._apply_config_now, cfg))
    print("  picker.apply_config  %7.0f ms" % _t(rt.window.apply_config, cfg))

    # ---- 端到端 ----
    print()
    print("端到端（含淡出/淡入动画）:")
    for i in range(3):
        c = dict(rt.config)
        c["theme_mode"] = ("dark" if u.theme.name != "dark" else "light")
        t = time.perf_counter()
        rt.apply_settings(c)
        while (u._theme_fader._anim is not None
               and time.perf_counter() - t < 20):
            app.processEvents()
            time.sleep(0.004)
        print("  第%d次  %7.0f ms" % (i + 1, (time.perf_counter() - t) * 1000))

    # ---- 预设切换（明暗不变，应该快得多）----
    print()
    print("只换配色预设（明暗不变）:")
    for preset in ("midnight", "warm", "classic"):
        c = dict(rt.config)
        c["theme_preset"] = preset
        t = time.perf_counter()
        rt.apply_settings(c)
        while (u._theme_fader._anim is not None
               and time.perf_counter() - t < 20):
            app.processEvents()
            time.sleep(0.004)
        print("  %-9s %7.0f ms" % (preset, (time.perf_counter() - t) * 1000))

    print()
    print("说明：换肤主体是 Qt 给每个控件重新匹配 QSS 选择器，"
          "成本随控件数超线性增长（本项目实测 162 控件 321ms、"
          "427 控件 1166ms）。压缩 QSS 字符串只快 2~7%，"
          "因为瓶颈在选择器匹配而非解析。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
