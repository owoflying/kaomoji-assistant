"""开机自启（计划任务）的回归测试。

## 保护什么

用户报「开机自启时会弹一个命令行窗口」。

排查发现 exe 本身是 **GUI 子系统**（PE 头 Subsystem=2，不会弹控制台），
窗口来自 `schtasks /create` 默认生成的任务定义——它**缺少 `<Hidden>`**，
而且带 `DisallowStartIfOnBatteries=true`。登录时任务宿主拉起 GUI 程序
之前会短暂显示一个控制台窗口。

改用 `/xml` 方式给出完整任务定义后，显式 `<Hidden>true</Hidden>` +
电源策略全 false，黑框消失、笔记本用电池也能启动。

这里把这几条关键属性固定住，防止以后又改回简化参数。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def test_task_xml_is_hidden():
    """任务必须标 Hidden，否则登录时会闪控制台窗口。"""
    from core import autostart

    xml = autostart._TASK_XML.format(user="S-1-5-21-0", exe=r"C:\a\b.exe")
    assert "<Hidden>true</Hidden>" in xml, \
        "任务 XML 缺少 <Hidden>true</Hidden>，登录时会弹出命令行窗口"


def test_task_xml_allows_battery():
    """笔记本用电池开机时也要能启动。"""
    from core import autostart

    xml = autostart._TASK_XML.format(user="S-1-5-21-0", exe=r"C:\a\b.exe")
    assert ("<DisallowStartIfOnBatteries>false"
            "</DisallowStartIfOnBatteries>") in xml, \
        "用电池时任务不会启动"
    assert ("<StopIfGoingOnBatteries>false"
            "</StopIfGoingOnBatteries>") in xml


def test_task_xml_keeps_highest_runlevel():
    """仍要以最高权限运行（UIA 提权功能依赖它）。"""
    from core import autostart

    xml = autostart._TASK_XML.format(user="S-1-5-21-0", exe=r"C:\a\b.exe")
    assert "<RunLevel>HighestAvailable</RunLevel>" in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml


def test_task_xml_points_at_exe():
    """任务动作直接执行 exe，中间不套 cmd。

    套一层 `cmd /c` 是黑框的另一大来源——cmd.exe 是控制台程序，
    必然显示窗口。
    """
    from core import autostart

    exe = r"C:\Program Files\K\KaomojiAssistant.exe"
    xml = autostart._TASK_XML.format(user="S-1-5-21-0", exe=exe)
    assert "<Command>%s</Command>" % exe in xml
    assert "cmd" not in xml.lower(), "任务里不该出现 cmd"


def test_task_xml_is_wellformed():
    """XML 必须能被解析（否则 schtasks /create /xml 直接报错）。"""
    import xml.etree.ElementTree as ET

    from core import autostart

    xml = autostart._TASK_XML.format(user="S-1-5-21-0", exe=r"C:\a\b.exe")
    root = ET.fromstring(xml)
    assert root.tag.endswith("Task")


def test_current_user_sid_is_resolvable():
    """能解析出当前用户标识（否则任务 XML 的 UserId 会填错）。

    ⚠ 不能用 `%USERDOMAIN%\\%USERNAME%` 这种环境变量写法——任务计划
    服务不展开它，会当成字面量用户名。
    """
    from core import autostart

    sid = autostart._current_user_sid()
    assert sid, "拿不到当前用户标识"
    assert "%%USERDOMAIN%%" not in sid, "不该返回未展开的环境变量占位符"
    assert sid.startswith("S-1-") or "\\" in sid, \
        "格式不对：%r" % sid


def test_xml_file_uses_utf16(tmp_path, monkeypatch):
    """写的临时文件必须是 UTF-16 带 BOM。

    `schtasks /create /xml` 硬性要求 UTF-16，写 UTF-8 会报解析失败。
    """
    from core import autostart

    captured = {}

    def fake_run(args):
        # 抓取 /xml 后面的路径，检查文件编码
        try:
            path = args[args.index("/xml") + 1]
            with open(path, "rb") as f:
                captured["head"] = f.read(4)
        except Exception as e:
            captured["err"] = repr(e)
        return True, ""

    monkeypatch.setattr(autostart, "_run_schtasks", fake_run)
    xml = autostart._TASK_XML.format(user="S-1-5-21-0", exe=r"C:\a\b.exe")
    autostart._create_task_from_xml(xml)

    head = captured.get("head") or b""
    # UTF-16 LE BOM: FF FE
    assert head[:2] in (b"\xff\xfe", b"\xfe\xff"), \
        "临时 XML 文件不是 UTF-16 编码（schtasks 不接受）：%r" % head
