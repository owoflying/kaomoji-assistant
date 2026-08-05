"""运行时路径与冻结态判断。

打包后（PyInstaller）程序从临时目录 / 安装目录运行：
- 只读资源（data/kaomoji.json）随包分发，用 resource_path 定位；
- 可写数据（config.json、user_state.json）必须放到用户可写的
  %APPDATA%/KaomojiAssistant，否则装在 C:\\Program Files 下普通用户无写权限；
- 源码模式则沿用项目目录，保持原有行为。
"""
import os
import sys

APP_NAME = "KaomojiAssistant"


def is_frozen():
    """是否以 PyInstaller 打包后的 exe 形态运行。"""
    return bool(getattr(sys, "frozen", False))


def source_base_dir():
    # 本文件位于 core/runtime.py，项目根目录在两级之上
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_data_dir():
    """用户可写的应用数据目录（%APPDATA%/KaomojiAssistant）。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def resource_path(*parts):
    """只读资源（如颜文字库）的运行时路径。

    兼容 PyInstaller 的不同打包形态：
    - onefile：解压到临时目录 sys._MEIPASS
    - onedir（6.x）：数据放在 <exe 目录>/_internal
    - 旧版 onedir：与 exe 同目录
    依次尝试这些候选目录，返回首个真实存在的路径。
    """
    if is_frozen():
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(meipass)
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "_internal"))
        candidates.append(exe_dir)
        for base in candidates:
            p = os.path.join(base, *parts)
            if os.path.exists(p):
                return p
        # 都不存在则回退到最可能的位置，由上层报错提示
        return os.path.join(candidates[0], *parts)
    return os.path.join(source_base_dir(), *parts)


def config_path():
    if is_frozen():
        return os.path.join(app_data_dir(), "config.json")
    return os.path.join(source_base_dir(), "config.json")


def state_path():
    if is_frozen():
        return os.path.join(app_data_dir(), "user_state.json")
    return os.path.join(source_base_dir(), "data", "user_state.json")


def kaomoji_path():
    return resource_path("data", "kaomoji.json")


def user_kaomoji_path():
    """用户自定义颜文字（可写，与只读 kaomoji.json 分离）。"""
    if is_frozen():
        return os.path.join(app_data_dir(), "user_kaomoji.json")
    return os.path.join(source_base_dir(), "data", "user_kaomoji.json")


def user_triggers_path():
    """用户快捷短语（触发词 -> 输出，可写）。"""
    if is_frozen():
        return os.path.join(app_data_dir(), "user_triggers.json")
    return os.path.join(source_base_dir(), "data", "user_triggers.json")
