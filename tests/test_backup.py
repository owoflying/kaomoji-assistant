"""数据备份：导出 / 导入的正确性与安全性。

## 为什么单独测

导出导入看起来"无非是打压缩包"，但有几个点一旦错了后果很重：

    * 漏文件 → 用户换机器后数据不全，而且**当场发现不了**
    * 带了不该带的 → 把崩溃日志/本机标记带到新机器
    * zip slip → 恶意备份包把文件写到数据目录之外
    * 导入没有退路 → 覆盖掉用户当前的记录和训练数据

这些都不是"界面能不能点"能覆盖的，所以直接用临时目录测核心逻辑。
"""
import json
import os
import zipfile

import pytest

from core import backup


def _make_dataset(root):
    """造一份带各种文件的数据目录（含不该导出的）。"""
    os.makedirs(os.path.join(root, "plugins", "demo"), exist_ok=True)
    os.makedirs(os.path.join(root, "crashes"), exist_ok=True)
    _write_json(os.path.join(root, "config.json"),
                {"theme_mode": "dark", "panel_alpha": 0.8})
    _write_json(os.path.join(root, "user_state.json"), {"recent": ["(A)"]})
    _write_json(os.path.join(root, "emotion_model_user.json"), {"n": 42})
    _write_json(os.path.join(root, "user_kaomoji.json"), {"mine": ["(B)"]})
    _write_json(os.path.join(root, "plugins", "demo", "settings.json"),
                {"k": "v"})
    with open(os.path.join(root, "background.png"), "wb") as f:
        f.write(b"\x89PNG" + b"0" * 200)
    # 不该被导出的
    with open(os.path.join(root, "welcome_shown.txt"), "w") as f:
        f.write("1")
    with open(os.path.join(root, "crashes", "last-crash.log"), "w") as f:
        f.write("trace")


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_export_writes_expected_files(tmp_path):
    """导出包含全部白名单文件，且分类正确。"""
    root = str(tmp_path / "data")
    _make_dataset(root)
    out = str(tmp_path / "b.zip")

    ok, msg, m = backup.export_all(root, out, app_version="9.9.9")
    assert ok, msg
    paths = {f["path"] for f in m["files"]}
    assert "config.json" in paths
    assert "user_state.json" in paths
    assert "emotion_model_user.json" in paths
    assert "background.png" in paths
    assert "plugins/demo/settings.json" in paths

    cats = {f["path"]: f["category"] for f in m["files"]}
    assert cats["config.json"] == "settings"
    assert cats["emotion_model_user.json"] == "learning"
    assert cats["user_state.json"] == "usage"
    assert cats["background.png"] == "appearance"


def test_export_excludes_machine_local_files(tmp_path):
    """崩溃日志、本机一次性标记不能被导出。

    带过去不仅臃肿，还会让新机器误以为"欢迎页已经看过了"。
    """
    root = str(tmp_path / "data")
    _make_dataset(root)
    out = str(tmp_path / "b.zip")
    ok, _msg, _m = backup.export_all(root, out)
    assert ok
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert not any("crashes" in n for n in names), names
    assert "welcome_shown.txt" not in names
    assert "last_seen_version.txt" not in names


def test_export_fails_cleanly_on_empty_dir(tmp_path):
    """空目录不该生成一个"看起来成功"的空包。"""
    root = str(tmp_path / "empty")
    os.makedirs(root, exist_ok=True)
    ok, msg, m = backup.export_all(root, str(tmp_path / "b.zip"))
    assert not ok
    assert m is None
    assert not os.path.exists(str(tmp_path / "b.zip"))


def test_roundtrip_restores_everything(tmp_path):
    """导出 → 破坏 → 导入，数据必须完整回来。"""
    root = str(tmp_path / "data")
    _make_dataset(root)
    out = str(tmp_path / "b.zip")
    ok, _m, _man = backup.export_all(root, out)
    assert ok

    # 破坏当前数据
    _write_json(os.path.join(root, "config.json"), {"theme_mode": "light"})
    _write_json(os.path.join(root, "user_state.json"), {})
    os.remove(os.path.join(root, "plugins", "demo", "settings.json"))

    ok, msg, detail = backup.import_all(out, root)
    assert ok, msg
    assert _read_json(os.path.join(root, "config.json"))["theme_mode"] == "dark"
    assert _read_json(os.path.join(root, "user_state.json"))["recent"] == ["(A)"]
    assert os.path.isfile(
        os.path.join(root, "plugins", "demo", "settings.json"))


def test_import_creates_backup_of_existing_data(tmp_path):
    """导入前必须备份现有数据——否则用户试错一次就永久丢数据。"""
    root = str(tmp_path / "data")
    _make_dataset(root)
    out = str(tmp_path / "b.zip")
    backup.export_all(root, out)

    # 当前数据改成别的值
    _write_json(os.path.join(root, "config.json"), {"theme_mode": "light"})
    ok, _msg, detail = backup.import_all(out, root, make_backup=True)
    assert ok
    bk = detail.get("backup_dir")
    assert bk and os.path.isdir(bk)
    # 备份里存的是**导入前**的值
    saved = _read_json(os.path.join(bk, "config.json"))
    assert saved["theme_mode"] == "light"


def test_import_rejects_zip_slip(tmp_path):
    """恶意路径不能逃出数据目录。

    备份包可能来自别人的机器，`../../` 这类路径借助
    `os.path.join` 能把文件写到数据目录外面。
    """
    root = str(tmp_path / "data")
    os.makedirs(root, exist_ok=True)
    bad = str(tmp_path / "evil.zip")
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr(backup.MANIFEST, json.dumps({
            "format": "kaomoji-backup", "format_version": 1, "files": []}))
        zf.writestr("../../../evil.txt", "pwned")
        zf.writestr("config.json", "{}")

    ok, _msg, detail = backup.import_all(bad, root)
    assert "../../../evil.txt" in detail.get("skipped", [])
    assert not os.path.exists(str(tmp_path / "evil.txt"))


def test_import_rejects_non_backup_zip(tmp_path):
    """随便一个 zip 不能被当成备份导入。"""
    root = str(tmp_path / "data")
    os.makedirs(root, exist_ok=True)
    z = str(tmp_path / "random.zip")
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("hello.txt", "hi")
    ok, msg, _m = backup.inspect(z)
    assert not ok
    assert "备份包" in msg


def test_import_rejects_newer_format(tmp_path):
    """来自更高版本的包要明确拒绝，而不是半吊子导入。"""
    root = str(tmp_path / "data")
    os.makedirs(root, exist_ok=True)
    z = str(tmp_path / "future.zip")
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(backup.MANIFEST, json.dumps({
            "format": "kaomoji-backup",
            "format_version": backup.FORMAT_VERSION + 5,
            "files": []}))
    ok, msg, _m = backup.inspect(z)
    assert not ok
    assert "更新" in msg


def test_import_ignores_unknown_members(tmp_path):
    """包里白名单之外的文件不落盘。

    防止有人往备份包里塞东西，导入时被写进数据目录。
    """
    root = str(tmp_path / "data")
    os.makedirs(root, exist_ok=True)
    z = str(tmp_path / "extra.zip")
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(backup.MANIFEST, json.dumps({
            "format": "kaomoji-backup", "format_version": 1, "files": []}))
        zf.writestr("config.json", '{"a": 1}')
        zf.writestr("sneaky.exe", "MZ")
    ok, _msg, detail = backup.import_all(z, root)
    assert ok
    assert "sneaky.exe" in detail.get("skipped", [])
    assert not os.path.exists(os.path.join(root, "sneaky.exe"))


def test_summarize_groups_by_category(tmp_path):
    """摘要按分类聚合，方便界面展示"将要导出什么"。"""
    root = str(tmp_path / "data")
    _make_dataset(root)
    _ok, _msg, m = backup.export_all(root, str(tmp_path / "b.zip"))
    rows = backup.summarize(m)
    labels = [r[0] for r in rows]
    assert "设置" in labels
    assert "AI 训练数据" in labels
    total = sum(r[1] for r in rows)
    assert total == len(m["files"])


@pytest.mark.parametrize("n,expect", [
    (512, "512 B"),
    (2048, "2.0 KB"),
    (5 * 1024 * 1024, "5.0 MB"),
])
def test_human_size(n, expect):
    assert backup.human_size(n) == expect
