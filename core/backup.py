"""一键导出 / 导入全部用户数据（换机迁移用）。

## 导出什么

用户数据目录（`app_data_dir()`）里的**全部内容**，按用途分类：

    settings      config.json               所有设置（含外观主题包）
    appearance    user_theme.json           自定义配色的色板
                  background.png            主窗口背景图
                  picker_background.*       候选条背景图
    learning      emotion_model_user.json   情绪模型的自训练数据
                  rank_net.json             推荐网络的训练数据
    usage         user_state.json           使用记录 / 收藏 / 时段统计
    content       user_kaomoji.json         自定义颜文字
                  user_triggers.json        自定义快捷短语
    plugins       plugins/**                各插件的私有设置

**不导出**的东西（明确排除，避免把隐私和无用信息带过去）：

    crashes/                崩溃转储，只对当前机器有诊断价值
    last_seen_version.txt   本机版本记录
    last_update_check.txt   本机检查缓存
    update_pending.txt      本机待更新标记
    welcome_shown.txt       本机「是否看过欢迎页」
    risk_*_shown.txt        本机的一次性提示标记

## 包格式

就是一个 **zip**，里面有 `manifest.json` + 各文件（保持原始相对路径）。

选 zip 而不是自定义格式，理由：
  * 用户能直接用资源管理器打开看里面有什么（不必信任我们的界面）
  * 跨平台、无依赖
  * 出问题可以只取其中几个文件手动放回去

## 导入的安全考虑

导入会覆盖本机数据，属于**破坏性操作**，所以：

  1. 先解析并校验 manifest，包不对就整个拒绝，不做"部分导入"
  2. 导入前把现有数据**自动备份**到 `backup-before-import-<时间>/`
  3. 只写清单里出现的文件，不会把包里多出来的内容乱塞进数据目录
"""
import datetime
import json
import os
import shutil
import zipfile

#: 导出清单：(相对路径, 分类, 中文说明)
#:
#: 用显式白名单而不是"整个目录打包"——数据目录里混着崩溃转储、
#: 版本标记这类只对本机有意义的东西，全打进去既臃肿又容易泄露，
#: 换机后还可能干扰新机器（比如"欢迎页已看过"的标记被带过去）。
EXPORT_FILES = (
    ("config.json", "settings", "所有设置（含外观主题）"),
    ("user_theme.json", "appearance", "自定义配色"),
    ("background.png", "appearance", "主窗口背景图"),
    ("picker_background.jpeg", "appearance", "候选条背景图"),
    ("picker_background.png", "appearance", "候选条背景图"),
    ("emotion_model_user.json", "learning", "AI 情绪训练数据"),
    ("rank_net.json", "learning", "推荐网络训练数据"),
    ("user_state.json", "usage", "使用记录与统计"),
    ("user_kaomoji.json", "content", "自定义颜文字"),
    ("user_triggers.json", "content", "自定义快捷短语"),
)

#: 需要整目录递归导出的子目录（相对数据目录）
EXPORT_DIRS = (
    ("plugins", "plugins", "插件私有设置"),
)

#: 包里清单文件的名字
MANIFEST = "manifest.json"

#: 包的格式版本（将来结构变了靠它判断兼容性）
FORMAT_VERSION = 1


def _now():
    return datetime.datetime.now()


def _stamp():
    return _now().strftime("%Y%m%d-%H%M%S")


def default_name():
    """默认导出文件名（带时间戳，避免覆盖）。"""
    return "kaomoji-backup-%s.zip" % _stamp()


def collect(data_dir):
    """扫描数据目录，返回实际存在的可导出项。

    :return: `[(绝对路径, 相对路径, 分类, 说明), ...]`

    ## 为什么要先收集再打包

    用户的数据目录里不一定每个文件都存在（比如没设过候选条背景图）。
    先收集能拿到"这次到底导出了什么"，写进 manifest 让用户看得见，
    也方便导入时精确还原。
    """
    out = []
    seen = set()
    for rel, cat, desc in EXPORT_FILES:
        path = os.path.join(data_dir, rel)
        if rel in seen or not os.path.isfile(path):
            continue
        seen.add(rel)
        out.append((path, rel, cat, desc))

    for sub, cat, desc in EXPORT_DIRS:
        base = os.path.join(data_dir, sub)
        if not os.path.isdir(base):
            continue
        for dp, _dns, fs in os.walk(base):
            for f in fs:
                full = os.path.join(dp, f)
                rel = os.path.relpath(full, data_dir).replace("\\", "/")
                if rel in seen or not os.path.isfile(full):
                    continue
                seen.add(rel)
                out.append((full, rel, cat, desc))
    return out


def export_all(data_dir, out_path, app_version=""):
    """把全部用户数据打成一个 zip。

    :param out_path: 目标 zip 路径
    :return: `(ok, message, manifest_dict)`

    ## 写包过程

    先写临时文件再改名，避免中途失败留下一个**半截包**——
    用户很可能拿着那个残包去新机器导入，然后困惑为什么少了东西。
    """
    data_dir = os.path.abspath(data_dir)
    if not os.path.isdir(data_dir):
        return (False, "数据目录不存在：%s" % data_dir, None)

    items = collect(data_dir)
    if not items:
        return (False, "没有可导出的数据。", None)

    manifest = {
        "format": "kaomoji-backup",
        "format_version": FORMAT_VERSION,
        "app_version": app_version or "",
        "created_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": [],
    }

    tmp = out_path + ".part"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for full, rel, cat, desc in items:
                try:
                    zf.write(full, arcname=rel)
                except Exception as e:
                    return (False, "打包 %s 失败：%s" % (rel, e), None)
                manifest["files"].append({
                    "path": rel,
                    "category": cat,
                    "desc": desc,
                    "size": os.path.getsize(full),
                })
            zf.writestr(MANIFEST,
                        json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(tmp, out_path)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return (False, "导出失败：%s" % e, None)

    return (True, "已导出 %d 个文件" % len(items), manifest)


def inspect(package_path):
    """只读检查一个备份包，不写任何东西。

    :return: `(ok, message, manifest_dict)`

    导入前先用它把包看清楚（多少文件、都是什么、什么时候导的），
    让用户确认后再落到磁盘上。
    """
    if not os.path.isfile(package_path):
        return (False, "文件不存在：%s" % package_path, None)
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            names = zf.namelist()
            if MANIFEST not in names:
                return (False, "这不是有效的备份包（缺少 %s）。" % MANIFEST,
                        None)
            try:
                m = json.loads(zf.read(MANIFEST).decode("utf-8"))
            except Exception as e:
                return (False, "备份包清单损坏：%s" % e, None)
            if not isinstance(m, dict) or m.get("format") != "kaomoji-backup":
                return (False, "备份包格式不正确。", None)
            ver = int(m.get("format_version", 0) or 0)
            if ver > FORMAT_VERSION:
                return (False,
                        "这个备份包来自更新的版本（格式 v%d，本程序支持到 v%d），"
                        "请先升级程序再导入。" % (ver, FORMAT_VERSION), None)
            return (True, "可以导入", m)
    except zipfile.BadZipFile:
        return (False, "文件不是有效的 zip 备份包。", None)
    except Exception as e:
        return (False, "读取备份包失败：%s" % e, None)


def _safe_relpath(rel):
    """校验包内路径，挡住 zip slip（`../` 逃逸）。

    备份包可能来自别人的机器，直接 `os.path.join` 一个含 `..` 的路径
    能把文件写到数据目录外面去——这是 zip 解压的经典漏洞。
    """
    if not rel or os.path.isabs(rel):
        return None
    norm = os.path.normpath(rel).replace("\\", "/")
    if norm.startswith("../") or norm == ".." or "/../" in norm:
        return None
    return norm


def import_all(package_path, data_dir, make_backup=True):
    """把备份包还原到数据目录。

    :param make_backup: 导入前是否先备份现有数据（默认 True）
    :return: `(ok, message, detail_dict)`

    ## 为什么必须先备份

    导入是**覆盖**操作。用户可能只是想试试某个旧备份，结果覆盖掉了
    当前所有使用记录和训练数据。自动备份能把这条路堵死。
    """
    ok, msg, manifest = inspect(package_path)
    if not ok:
        return (False, msg, {})

    data_dir = os.path.abspath(data_dir)
    os.makedirs(data_dir, exist_ok=True)

    # ---- 1. 导入前备份 ----
    backup_dir = ""
    if make_backup:
        try:
            backup_dir = os.path.join(
                data_dir, "backup-before-import-%s" % _stamp())
            os.makedirs(backup_dir, exist_ok=True)
            for rel, _cat, _desc in EXPORT_FILES:
                src = os.path.join(data_dir, rel)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(backup_dir, rel))
        except Exception:
            # 备份失败不阻断导入，但要在结果里说清楚
            backup_dir = ""

    # ---- 2. 解包 ----
    written = []
    skipped = []
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                if name == MANIFEST:
                    continue
                rel = _safe_relpath(name)
                if rel is None:
                    skipped.append(name)
                    continue
                # 只写白名单内的路径：包里多出来的东西不落盘
                if not _is_allowed(rel):
                    skipped.append(name)
                    continue
                dst = os.path.join(data_dir, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dst) or data_dir, exist_ok=True)
                with zf.open(info, "r") as src:
                    with open(dst, "wb") as out:
                        shutil.copyfileobj(src, out)
                written.append(rel)
    except Exception as e:
        return (False, "导入过程中出错：%s" % e,
                {"written": written, "skipped": skipped,
                 "backup_dir": backup_dir})

    detail = {"written": written, "skipped": skipped,
              "backup_dir": backup_dir}
    if not written:
        return (False, "备份包里没有可导入的文件。", detail)
    return (True, "已还原 %d 个文件" % len(written), detail)


def _is_allowed(rel):
    """rel（已规范化）是否在白名单内。"""
    rel = rel.replace("\\", "/")
    for name, _cat, _desc in EXPORT_FILES:
        if rel == name:
            return True
    for sub, _cat, _desc in EXPORT_DIRS:
        if rel.startswith(sub + "/"):
            return True
    return False


def summarize(manifest):
    """把 manifest 变成「分类 → 文件数/大小」的可读摘要。

    :return: `[(分类中文名, 文件数, 总字节), ...]`
    """
    labels = {
        "settings": "设置",
        "appearance": "外观",
        "learning": "AI 训练数据",
        "usage": "使用记录",
        "content": "自定义内容",
        "plugins": "插件设置",
    }
    buckets = {}
    for f in (manifest or {}).get("files", []):
        cat = f.get("category") or "other"
        n, size = buckets.get(cat, (0, 0))
        buckets[cat] = (n + 1, size + int(f.get("size", 0) or 0))
    order = ["settings", "appearance", "learning", "usage",
             "content", "plugins"]
    out = []
    for cat in order:
        if cat in buckets:
            n, size = buckets[cat]
            out.append((labels.get(cat, cat), n, size))
    for cat, (n, size) in buckets.items():
        if cat not in order:
            out.append((labels.get(cat, cat), n, size))
    return out


def human_size(n):
    """字节数 → 人类可读。"""
    try:
        n = float(n)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.0f %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f GB" % n


def read_text_member(package_path, member):
    """读取包内某个文本文件（调试/检查用）。"""
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            return zf.read(member).decode("utf-8", "replace")
    except Exception:
        return ""
