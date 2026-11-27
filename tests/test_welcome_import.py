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
