# 颜文字输入辅助器 (Kaomoji Assistant)

> ⚠️ **AI 生成声明 / AI-Generated Disclaimer**
> 本项目由 **AI（WorkBuddy 大语言模型）辅助生成并迭代**：代码、结构、文档均主要由 AI 编写，仅经人类少量审阅与测试。
> 项目按 **Apache-2.0 协议**开源，仅供学习、研究与个人使用。**作者不对使用本软件造成的任何后果负责**；使用即表示你理解并同意这一点。
> 欢迎提交 Issue / Pull Request 改进本项目。

一个用 Python 编写的、**现代化类 Win11 风格**的颜文字输入辅助工具。按下全局热键即可唤起候选面板，实时搜索并选择颜文字，回车即自动输入到当前光标位置。

## 功能特性

- **全局热键唤起**：默认 `Ctrl + Shift + K`，在任意可输入位置随时唤起
- **原生全局热键**：基于 Windows `RegisterHotKey`，无全局键盘钩子，不卡顿
- **Win11 风格面板**：无边框、半透明、圆角、柔和阴影、系统背景模糊（Acrylic）、浅/深双主题
- **唤起即聚焦**：热键唤起后自动抢前台并聚焦搜索框，无需鼠标点击
- **失焦自动关闭**：焦点离开面板即隐藏，与系统输入法行为一致
- **实时搜索过滤**：边输入边筛选，输入即搜
- **最近使用 / 收藏夹**：`全部 / 最近 / 收藏` 分段切换，每条一键收藏（★）
- **键盘导航**：`↑` / `↓` 选择，`Enter` 输入，`Esc` 关闭，`Ctrl+D` 收藏
- **可视化配置面板**：托盘右键「设置」改热键 / 主题 / 不透明度 / 亚克力 / 输入方式，即时生效
- **分类颜文字库**：按情绪/类型整理，易于扩展
- **系统托盘常驻**：可随时唤起面板或退出程序

## 环境要求

- Windows 10 / 11
- Python 3.10+（已在 3.13 验证）
- 依赖：`PySide6`、`pynput`（见 `requirements.txt`）

## 运行方式

建议使用项目内的虚拟环境：

```bat
:: 1. 创建并激活虚拟环境（Windows）
python -m venv .venv
.venv\Scripts\activate

:: 2. 安装依赖
pip install -r requirements.txt

:: 3. 启动
python main.py
```

启动后程序最小化到系统托盘。在任意可输入位置按 `Ctrl + Shift + K` 唤起面板，  
选择颜文字后按 `Enter` 即粘贴到当前光标处。

## 配置

编辑 `config.json`：

| 字段               | 说明                                              | 示例                   |
| ---------------- | ----------------------------------------------- | -------------------- |
| `hotkey`         | 全局热键（pynput 格式，基于原生 `RegisterHotKey`）       | `"<ctrl>+<shift>+k"` |
| `theme`          | 主题：`"light"` / `"dark"`                         | `"light"`            |
| `opacity`        | 面板不透明度 0.5~1                                  | `0.98`               |
| `acrylic`        | 是否启用 Windows 11 系统模糊（Acrylic 材质）            | `true`               |
| `input_method`   | 输入方式：`"type"` 直接键入 / `"clipboard"` 剪贴板粘贴 | `"type"`             |
| `max_recent`     | 最近使用最多保留条数                                  | `30`                 |
| `auto_hide_on_blur` | 焦点离开窗口时自动关闭（与输入法一致）                   | `true`               |

> 以上所有项均可在托盘右键「设置」里可视化修改并即时生效，无需手编 JSON。

## 自定义颜文字

编辑 `data/kaomoji.json`，在对应分类的 `items` 中增删颜文字即可，结构如下：

```json
{
  "categories": [
    { "name": "开心", "items": ["(｡･ω･｡)", "(◕‿◕)"] }
  ]
}
```

## 项目结构

```
kaomoji-assistant/
├── main.py                 # 入口：组装 UI / 热键 / 托盘 / 设置
├── config.json             # 配置
├── requirements.txt        # 依赖
├── core/
│   ├── kaomoji_data.py     # 颜文字数据加载与搜索
│   ├── hotkey.py           # 全局热键（原生 RegisterHotKey + nativeEvent）
│   ├── injector.py         # 颜文字输入注入（键入 / 剪贴板）
│   ├── user_state.py       # 最近使用 / 收藏夹 持久化
│   ├── win_utils.py        # Win32 焦点控制、热键解析、DWM 常量
│   ├── emotion.py          # 情绪关键词 -> 分类名 匹配
│   ├── emotion_monitor.py  # 打字时自动识别情绪并弹出候选（UIA 采样）
│   ├── global_keys.py      # 面板可见时的无焦点全局按键拦截
│   └── uia_text.py         # 零依赖 UIA：读焦点控件文本 / 包围盒
├── ui/
│   ├── picker_window.py    # Win11 风格候选面板（分段/收藏/失焦关闭）
│   ├── settings_dialog.py  # 可视化配置面板
│   └── win_style.py        # Windows 11 模糊 / 深色 / 阴影
└── data/
    ├── kaomoji.json        # 颜文字库
    └── user_state.json     # 最近使用与收藏（自动生成）
```

## 交互说明

- **唤起**：全局热键（默认 `Ctrl+Shift+K`）。唤起后窗口会自动抢到前台并聚焦搜索框，无需再用鼠标点一下。
- **导航**：`↑` / `↓` 选择，`Enter` 输入，`Esc` 关闭；`Ctrl+D` 收藏当前项。
- **失焦自动关闭**：焦点一旦离开面板（点其它程序 / 切走），面板自动隐藏，与系统输入法行为一致。
- **分段**：`全部` / `最近` / `收藏`，每条右侧 ★ 可一键收藏。
- **设置**：托盘右键「设置」可可视化改热键、主题、不透明度、亚克力、输入方式，并即时生效。

## 已知限制

- 全局热键基于 Windows 原生 `RegisterHotKey`，需程序在后台运行（系统托盘常驻）；此方式不挂全局键盘钩子，对日常输入零干扰、不卡顿。
- 当中文输入法处于“中文”模式时，直接键入（`type`）偶发可能被输入法拦截；可在设置里切换为「剪贴板粘贴」输入方式规避。
- 系统模糊（Acrylic）仅在 Windows 11 上生效；其他系统会回退为半透明背景。
