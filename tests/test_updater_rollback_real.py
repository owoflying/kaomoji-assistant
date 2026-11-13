"""真实故障注入：在临时目录里跑完整替换脚本，验证回退真的有效。

## 为什么必须真跑

之前的"回退验证"只测了 Python 侧的异常处理，**没测脚本真正
替换文件时失败**。而用户遇到的正是这一层：
包有问题 → 脚本搬到一半失败 → 程序打不开。

这个文件会真的生成并执行批处理脚本，在假的 onedir 目录上验证。

## 覆盖的故障

| 场景 | 期望 |
|------|------|
| 正常更新 | 升级到新版本 |
| 暂存目录无 exe | 回退到旧版本 |
| 暂存缺 _internal | 回退到旧版本 |
| 暂存目录为空 | 回退到旧版本 |

⚠ 这些用例会启动子进程（假的"程序"是 notepad 副本），
在 CI 上跑需要 conda/Windows。非 Windows 自动跳过。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="替换脚本是 Windows 批处理")

EXE = "FakeApp.exe"


# ---------------------------------------------------------------- 工具
def _make_real_app(root, version):
    """造一个用**真 exe**（notepad 副本）的假 onedir 目录。

    为什么不用批处理当假 exe：`start` 拉起批处理时进程名是 cmd.exe，
    脚本里的进程检查查不到。用真 exe 才能反映真实情况。

    版本号写在 `_internal/version.txt`，用来事后判断是哪个版本。
    """
    os.makedirs(os.path.join(root, "_internal"), exist_ok=True)
    src = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                       "System32", "notepad.exe")
    shutil.copyfile(src, os.path.join(root, EXE))
    with open(os.path.join(root, "_internal", "version.txt"),
              "w", encoding="utf-8") as f:
        f.write(version)


def _read_marker(root):
    p = os.path.join(root, "_internal", "version.txt")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _run_script(install, staging):
    """生成并执行替换脚本，等它跑完。"""
    from core import updater_install as ui

    script = ui.build_script(install, EXE, staging)
    d = tempfile.mkdtemp(prefix="upd_script_")
    p = os.path.join(d, "apply.bat")
    with open(p, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(script)
    try:
        subprocess.run(["cmd.exe", "/c", p], capture_output=True,
                       text=True, timeout=120,
                       creationflags=getattr(subprocess,
                                             "CREATE_NO_WINDOW", 0))
        time.sleep(2)
        # 关掉脚本启动的假程序
        try:
            subprocess.run(["taskkill", "/F", "/IM", EXE],
                           capture_output=True,
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        time.sleep(1)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_note():
    """每个用例前后清理失败标记，避免互相影响。"""
    from core import updater_install
    updater_install.clear_failure_note()
    yield
    updater_install.clear_failure_note()


# ---------------------------------------------------------------- 用例
def test_normal_update_succeeds(tmp_path):
    """对照组：包正常时应该真正升级。"""
    ins = str(tmp_path / "app")
    stg = str(tmp_path / "stage")
    _make_real_app(ins, "1.0")
    _make_real_app(stg, "2.0")

    _run_script(ins, stg)

    assert _read_marker(ins) == "2.0", "应该已升级到新版本"
    assert os.path.isdir(os.path.join(ins, "_internal")), "_internal 应存在"
    # 成功后不应留下 .old 备份
    assert not os.path.exists(os.path.join(ins, EXE + ".old"))


def test_rollback_when_stage_has_no_exe(tmp_path):
    """暂存目录没有 exe（包不对）→ 必须回退。"""
    ins = str(tmp_path / "app")
    stg = str(tmp_path / "stage")
    _make_real_app(ins, "1.0")
    os.makedirs(stg, exist_ok=True)
    (tmp_path / "stage" / "junk.txt").write_text("x")

    _run_script(ins, stg)

    assert _read_marker(ins) == "1.0", "应回退到旧版本"
    assert os.path.isdir(os.path.join(ins, "_internal")), "_internal 应完好"


def test_rollback_when_stage_missing_internal(tmp_path):
    """暂存目录缺 _internal → 校验失败 → 回退。"""
    ins = str(tmp_path / "app")
    stg = str(tmp_path / "stage")
    _make_real_app(ins, "1.0")
    os.makedirs(stg, exist_ok=True)
    src = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                       "System32", "notepad.exe")
    shutil.copyfile(src, os.path.join(stg, EXE))
    # 故意不放 _internal

    _run_script(ins, stg)

    assert _read_marker(ins) == "1.0", "应回退到旧版本"
    assert os.path.isdir(os.path.join(ins, "_internal")), "_internal 应完好"


def test_rollback_when_stage_empty(tmp_path):
    """暂存目录为空 → 回退。"""
    ins = str(tmp_path / "app")
    stg = str(tmp_path / "stage")
    _make_real_app(ins, "1.0")
    os.makedirs(stg, exist_ok=True)

    _run_script(ins, stg)

    assert _read_marker(ins) == "1.0"
    assert os.path.isdir(os.path.join(ins, "_internal"))


def test_failure_is_detected_from_log(tmp_path):
    """回滚后要能被识别成"更新失败"，让下次启动提示用户。

    ## 为什么改成"读日志"而不是"读标记文件"

    原方案让批处理回滚后写 `update_failed.txt`。实测在长脚本里那一句
    **写不出文件**（同处的日志却能写，探针确认执行到了那一行）。
    试过换变量/换字面路径/换 `>` 与 `>>`/挪到 `endlocal` 之后，都没根治。

    日志一直可靠，就改成解析日志尾部判断结果。
    """
    from core import updater_install

    ins = str(tmp_path / "app")
    stg = str(tmp_path / "stage")
    _make_real_app(ins, "1.0")
    os.makedirs(stg, exist_ok=True)

    # 清掉旧日志，避免读到上次的
    try:
        os.remove(updater_install.log_path())
    except Exception:
        pass

    _run_script(ins, stg)

    note = updater_install.read_failure_note()
    assert note, "回滚后应能识别出失败"
    assert "恢复" in note or "回滚" in note, \
        "说明里应告诉用户已恢复原版本，实际是：%r" % note


def test_success_is_not_reported_as_failure(tmp_path):
    """正常更新成功时不该报失败。"""
    from core import updater_install

    ins = str(tmp_path / "app")
    stg = str(tmp_path / "stage")
    _make_real_app(ins, "1.0")
    _make_real_app(stg, "2.0")

    try:
        os.remove(updater_install.log_path())
    except Exception:
        pass

    _run_script(ins, stg)

    assert updater_install.read_failure_note() == "", \
        "更新成功不该报失败"
