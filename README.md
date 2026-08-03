# 颜文字输入辅助器 (Kaomoji Assistant)

> 本项目由 AI 辅助生成。

Win11 风格的颜文字输入辅助工具。按全局热键唤起候选面板，搜索并选择颜文字，回车即输入到当前光标位置。

## 功能
- 全局热键唤起（默认 `Ctrl+Shift+K`）
- Win11 风格面板：半透明、圆角、亚克力模糊、浅/深双主题
- 实时搜索、`1`–`9` 直接选字、最近使用与收藏夹
- 失焦自动关闭，行为与原生输入法一致
- 系统托盘常驻，设置可视化修改

## 环境
- Windows 10 / 11
- Python 3.10+
- 依赖：`PySide6`、`pynput`

## 运行
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## 键位
| 操作 | 按键 |
| --- | --- |
| 唤起 | `Ctrl+Shift+K` |
| 选字 | `1`–`9` |
| 移动选择 | `←` / `→` |
| 翻页 | `-` / `=` |
| 上屏 | `Enter` / `Space` |
| 关闭 | `Esc` |
| 收藏 | `Ctrl+D` |

## 配置
编辑 `config.json`，或托盘右键「设置」可视化修改（即时生效）：
- `hotkey` 热键 · `theme` 主题 · `opacity` 不透明度 · `acrylic` 亚克力 · `input_method` 输入方式（`clipboard` 剪贴板粘贴为默认，发 Ctrl+V 不被中文输入法拦截；`type` 模拟键入在微软拼音等中文输入法下可能产生乱码，仅推荐英文输入模式使用）

## 自定义颜文字
编辑 `data/kaomoji.json`，在对应分类的 `items` 中增删即可：
```json
{ "categories": [ { "name": "开心", "items": ["(｡･ω･｡)", "(◕‿◕)"] } ] }
```

## 编译 EXE（发布版）
把项目打包成 Windows 可执行程序（onedir 模式，无控制台窗口，托盘常驻）：

```bat
.venv\Scripts\activate
pip install -r requirements.txt
python build.py
```

- 产物在 `dist\KaomojiAssistant\`（含 `KaomojiAssistant.exe` 与依赖 DLL、data 资源）。
- 打包时会**自动生成 `app.ico`**（与系统托盘同款的「白底圆角 + 颜文字 (◕‿◕)」图标），作为 exe 文件图标，无需手动准备图片。
- 只读资源 `data/kaomoji.json` 由 `core/runtime.py` 在运行时自动定位（源码态取项目目录，冻结态取 `_internal`），两种形态互不干扰。

## 开机自启动
- 在「设置 → 系统 → 开机自动启动」中开启。
- 通过写入当前用户注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 实现，**无需管理员权限**。
- 已安装版本的用户配置位于 `%APPDATA%\KaomojiAssistant\`（含 `config.json`、`user_state.json`），重装不丢失。

## 协议
Apache-2.0。
