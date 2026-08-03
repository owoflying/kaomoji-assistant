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
- `hotkey` 热键 · `theme` 主题 · `opacity` 不透明度 · `acrylic` 亚克力 · `input_method` 输入方式（`type` 直接键入 / `clipboard` 剪贴板粘贴，后者可规避中文输入法拦截）

## 自定义颜文字
编辑 `data/kaomoji.json`，在对应分类的 `items` 中增删即可：
```json
{ "categories": [ { "name": "开心", "items": ["(｡･ω･｡)", "(◕‿◕)"] } ] }
```

## 协议
Apache-2.0。
