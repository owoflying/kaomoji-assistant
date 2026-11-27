"""欢迎向导里的「从备份导入」入口。

## 为什么单独测

这是新用户第一次启动就能看到的入口，一旦坏了影响面很大，
而且**用户当场没有别的地方可去**（还没进设置页）。

重点验证两件事：

    1. 导入后界面上的热键/外观确实更新了（用户能看见生效）
    2. 更新后的值会经 `accept()` 交给宿主（**当前会话就生效**，
       不需要重启）

第 2 点曾经有 bug：`accept()` 拿 `self._config` 当基准比较，
而 `_sync_after_import()` 已经把导入值合并进 `_config` 了，
于是所有变化都被判成"没变"，`chosen` 为空——界面显示了新值，
实际不生效。
"""
import json
import os

import pytest

from core import backup, runtime


@pytest.fixture()
def isolated_data(tmp_path, monkeypatch):
    """把数据目录和配置路径都指向临时目录。"""
    root = str(tmp_path / "data")
    os.makedirs(root, exist_ok=True)
    monkeypatch.setattr(runtime, "app_data_dir", lambda: root)
    monkeypatch.setattr(
        runtime, "config_path",
        lambda: os.path.join(root, "config.json"))
    return root


def _write_cfg(root, obj):
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _make_backup(root, src_cfg, tmp_path):
    """按给定的 config 造一个备份包。"""
    src = os.path.join(str(tmp_path), "src")
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(src, "config.json"), "w", encoding="utf-8") as f:
        json.dump(src_cfg, f, ensure_ascii=False)
    pkg = os.path.join(str(tmp_path), "b.zip")
    ok, msg, _m = backup.export_all(src, pkg, app_version="1.0")
    assert ok, msg
    return pkg


def test_welcome_has_import_button(qapp):
    """最后一步必须有「从备份导入」入口。"""
    from PySide6.QtWidgets import QPushButton

    from ui.welcome_dialog import WelcomeDialog

    dlg = WelcomeDialog(theme_name="light", version="1.0", config={})
    try:
        labels = [b.text() for b in dlg.findChildren(QPushButton)]
        assert any("备份" in t for t in labels), labels
        assert hasattr(dlg, "_on_import_backup")
        assert hasattr(dlg, "_sync_after_import")
    finally:
        dlg.close()


def test_import_updates_combos_and_is_submitted(isolated_data, tmp_path, qapp):
    """导入后：下拉框更新 + 变化会被提交给宿主。

    只更新界面不提交 = 用户看到新值但当前会话不生效。
    """
    from core.config_store import load_config
    from ui.welcome_dialog import WelcomeDialog

    # 备份里：热键 Ctrl+Alt+K，深色
    pkg = _make_backup(isolated_data, {
        "hotkey": "<ctrl>+<alt>+k",
        "theme_mode": "dark",
        "theme": "dark",
    }, tmp_path)

    # 当前（全新）配置：默认热键 + 浅色
    _write_cfg(isolated_data, {
        "hotkey": "<ctrl>+<shift>+k",
        "theme_mode": "light",
        "theme": "light",
    })

    dlg = WelcomeDialog(theme_name="light", version="1.0",
                        config=load_config())
    try:
        assert dlg._hotkey_combo.currentData() == "<ctrl>+<shift>+k"
        assert dlg._theme_combo.currentData() == "light"

        ok, msg, _detail = backup.import_all(pkg, isolated_data)
        assert ok, msg
        dlg._sync_after_import()

        # 1. 界面确实更新了
        assert dlg._hotkey_combo.currentData() == "<ctrl>+<alt>+k"
        assert dlg._theme_combo.currentData() == "dark"

        # 2. 变化会被提交（关键：不能因为 _config 已更新就判成"没变"）
        dlg.accept()
        assert dlg.chosen.get("hotkey") == "<ctrl>+<alt>+k", dlg.chosen
        assert dlg.chosen.get("theme") == "dark", dlg.chosen
        assert dlg.chosen.get("theme_mode") == "dark", dlg.chosen
    finally:
        dlg.close()


def test_no_import_means_no_spurious_changes(isolated_data, qapp):
    """没导入、也没改任何东西时，`chosen` 应该是空的。

    防止反向问题：把默认值当成"用户改动"重复提交。
    """
    from core.config_store import load_config
    from ui.welcome_dialog import WelcomeDialog

    _write_cfg(isolated_data, {
        "hotkey": "<ctrl>+<shift>+k",
        "theme_mode": "light",
        "theme": "light",
    })
    dlg = WelcomeDialog(theme_name="light", version="1.0",
                        config=load_config())
    try:
        dlg.accept()
        assert dlg.chosen == {}, dlg.chosen
    finally:
        dlg.close()


def test_user_manual_change_still_works(isolated_data, qapp):
    """用户手动改下拉框，仍然要被提交。"""
    from core.config_store import load_config
    from ui.welcome_dialog import WelcomeDialog

    _write_cfg(isolated_data, {
        "hotkey": "<ctrl>+<shift>+k",
        "theme_mode": "light",
        "theme": "light",
    })
    dlg = WelcomeDialog(theme_name="light", version="1.0",
                        config=load_config())
    try:
        idx = dlg._hotkey_combo.findData("<alt>+`")
        dlg._hotkey_combo.setCurrentIndex(idx)
        dlg.accept()
        assert dlg.chosen.get("hotkey") == "<alt>+`", dlg.chosen
    finally:
        dlg.close()


def test_preview_mode_flag_is_set(qapp):
    """开发者页用的预览模式必须能被识别。"""
    from ui.welcome_dialog import WelcomeDialog

    dlg = WelcomeDialog(theme_name="light", version="1.0",
                        config={}, preview=True)
    try:
        assert dlg._preview is True
    finally:
        dlg.close()

    dlg2 = WelcomeDialog(theme_name="light", version="1.0", config={})
    try:
        assert dlg2._preview is False
    finally:
        dlg2.close()


def test_preview_import_never_writes(isolated_data, tmp_path, qapp,
                                     monkeypatch):
    """**预览模式下导入不能写盘**。

    这是用户明确要求的：开发者页点「首次使用引导」只是看看界面，
    如果在预览里真的导入，点一下就把自己真实数据覆盖了，
    而且是在"只想看看"的意图下发生的。

    这里不点真实按钮（会弹窗阻塞），而是验证那条分支的核心事实：
    走到预览分支时，`backup.import_all` **一次都不会被调用**，
    磁盘上的文件与调用前完全一致。
    """
    from core import backup
    from core.config_store import load_config
    from ui.welcome_dialog import WelcomeDialog

    pkg = _make_backup(isolated_data, {
        "hotkey": "<ctrl>+<alt>+k",
        "theme_mode": "dark",
        "theme": "dark",
    }, tmp_path)

    _write_cfg(isolated_data, {
        "hotkey": "<ctrl>+<shift>+k",
        "theme_mode": "light",
        "theme": "light",
    })

    def _snapshot():
        out = {}
        for dp, _dns, fs in os.walk(isolated_data):
            for f in fs:
                p = os.path.join(dp, f)
                out[os.path.relpath(p, isolated_data)] = os.path.getmtime(p)
        return out

    calls = {"n": 0}
    real_import = backup.import_all

    def _spy(*a, **kw):
        calls["n"] += 1
        return real_import(*a, **kw)

    monkeypatch.setattr(backup, "import_all", _spy)

    before = _snapshot()
    dlg = WelcomeDialog(theme_name="light", version="1.0",
                        config=load_config(), preview=True)
    try:
        ok, _msg, manifest = backup.inspect(pkg)
        assert ok
        # 预览分支应当只报告、不写盘
        will = dlg._preview_import_files(manifest)
        assert will, "预览应列出会还原的文件"
    finally:
        dlg.close()

    after = _snapshot()
    assert calls["n"] == 0, "预览模式调用了 import_all，会写盘！"
    assert before == after, "预览模式改动了磁盘文件"
    # 配置也没被改写
    with open(os.path.join(isolated_data, "config.json"),
              encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["hotkey"] == "<ctrl>+<shift>+k"


def test_setup_block_rows_do_not_overlap(qapp):
    """最后一页的三行控件不能重叠。

    ## 为什么需要这条

    窗口的 `setMinimumHeight` 曾经是 420，而最后一页要放
    「热键 / 外观 / 从备份导入」三行 + 分割线 + 提示，装不下。
    布局就把它们压扁后叠在一起——截图里能看到文字互相覆盖，
    用户反馈"几个按钮挤在一起"。

    这类问题**不会报错**，只能靠看图发现。所以这里直接量控件坐标：
    相邻两行的纵向区间不能相交。
    """
    from ui.welcome_dialog import WelcomeDialog

    dlg = WelcomeDialog(theme_name="light", version="1.0", config={})
    try:
        # 切到最后一页并让布局跑完
        dlg.show()
        for _ in range(10):
            qapp.processEvents()
        dlg._show_step(len(dlg._pages) - 1, forward=True)
        for _ in range(30):
            qapp.processEvents()

        hk = dlg._hotkey_combo
        th = dlg._theme_combo
        imp = dlg._import_btn
        for w in (hk, th, imp):
            assert w is not None

        def _bottom(w):
            return w.mapTo(dlg, w.rect().bottomLeft()).y()

        def _top(w):
            return w.mapTo(dlg, w.rect().topLeft()).y()

        assert _top(th) >= _bottom(hk), (
            "热键与外观两行重叠：热键底=%d，外观顶=%d"
            % (_bottom(hk), _top(th)))
        assert _top(imp) >= _bottom(th), (
            "外观与「从备份导入」两行重叠：外观底=%d，导入顶=%d"
            % (_bottom(th), _top(imp)))
    finally:
        dlg.close()


def test_window_tall_enough_for_setup_page(qapp):
    """窗口最小高度必须装得下最满的那一页。

    直接盯住根因：如果以后又往最后一页加东西，而最小高度没跟着涨，
    布局就会开始压扁控件。这条测试会在压扁之前就红。
    """
    from ui.welcome_dialog import WelcomeDialog

    dlg = WelcomeDialog(theme_name="light", version="1.0", config={})
    try:
        dlg.show()
        for _ in range(10):
            qapp.processEvents()
        setup_page = dlg._pages[-1]
        need = setup_page.minimumSizeHint().height()
        # 页面区实际能拿到的高度
        avail = dlg._body.height()
        assert avail >= need, (
            "最后一页至少需要 %d 像素高，但页面区只有 %d。\n"
            "控件会被压扁后重叠——请调大 setMinimumHeight。"
            % (need, avail))
    finally:
        dlg.close()
