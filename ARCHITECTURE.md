# kaomoji-assistant 架构设计文档

> Win11 风格颜文字输入辅助器（PySide6 + pynput 全局钩子）
> 文档版本：2026-08-08 ｜ 适用范围：源码形态与 PyInstaller 打包形态

---

## 1. 概述

kaomoji-assistant 是一款常驻于系统托盘的桌面工具，在用户于任意编辑器/输入框打字时，根据**焦点文本、情绪识别、触发短语**自动弹出颜文字候选栏，并通过剪贴板或模拟输入把颜文字注入到当前光标处。其设计目标是：

- **无侵入**：不打断用户原有输入流程，仅作"悬浮候选"；
- **系统级一致**：视觉贴近 Windows 11 设置（无边框、亚克力、圆角、WinUI 控件）；
- **稳健**：目标程序卡死（浏览器/Electron）时不能冻结本程序 GUI；
- **渐进交付**：新功能默认进入"测试模式"，确认上线后才对正式用户开放。

> **经验提示**：本程序横跨"全局钩子 / COM-UIA / 无边框窗口 / 亚克力合成 / 多形态打包"四类极易踩坑的领域。任何一项的"看起来能用"都不足以判定正确——必须以**真实 Windows 行为**或**离屏断言**验证，单纯 `py_compile` 通过或代码审查无法覆盖 ctypes 常量错误、QSS 级联断裂、UIA 超时缺失等运行时缺陷。

---

## 2. 技术栈选型

| 维度 | 选型 | 理由 / 注意事项 |
|------|------|----------------|
| 语言 | Python 3.12 / 3.13 | 沙箱用托管运行时；打包机需与运行环境 ABI 一致。 |
| GUI 框架 | **PySide6 6.11.x** | Qt6 的 `QColor("rgba(...)")` 不识别浮点 alpha，需正则兜底；`QComboBox` 弹出列表在 Win10 上跟随**系统**主题而非应用主题，不能只靠 DWM。 |
| 全局输入 | pynput（键盘录制）+ Win32 `RegisterHotKey`（注册） | `RegisterHotKey` 在系统卡顿时偶发失败，**必须加重试**（3 次 + 50ms）。 |
| 文本读取 | UI Automation（COM） | 跨进程读"焦点控件文本"的唯一可靠手段；但同步调用会**无限挂起**，必须带超时独立线程。 |
| 注入 | 剪贴板 / `WM_CHAR` 直接投递 / type 模式 | 微软拼音下 `ImmSetOpenStatus` 与 `SendInput+KEYEVENTF_UNICODE` 都是死路；type 模式需先切英文布局再打。 |
| 打包 | PyInstaller 6.x（onedir） | 数据文件在 `dist/KaomojiAssistant/_internal/`；`--add-data` 目标必须写**目录**（写文件名会嵌深一层导致资源注册失败）。 |
| 配置 | JSON（`config.json`） | 运行时本地配置，**不入库**；缺省项由 `DEFAULT_CONFIG` 兜底合并。 |

> **经验提示（混合渲染陷阱）**：QSS 的 `font-family` 优先级高于 `setFont()`，图标字体必须显式 `setStyleSheet("font-family:...")` 才生效。另外 `QRawFont.supportsCharacter` 在 Windows 上对印度语系字符（ಠ/દ）会返回**假阳性**，判断字形是否可用只能"真画一遍比对像素"。

---

## 3. 目录结构与模块划分

```
kaomoji-assistant/
├── main.py                 # 入口：配置加载、事件总线、系统托盘、热键/监控启动、apply_settings
├── build.py / build.bat    # PyInstaller 打包脚本
├── KaomojiAssistant.spec   # PyInstaller spec
├── requirements.txt        # 依赖
├── config.json             # 运行时本地配置（不入库）
├── app.ico / start.bat
├── data/
│   └── kaomoji.json        # 内置颜文字库
├── core/                   # 平台无关 + Windows 核心逻辑（无 Qt 依赖）
│   ├── runtime.py          # 运行态路径解析（frozen/source 双形态）
│   ├── win_utils.py        # Windows API 封装（热键格式/Vk 互转/焦点控件类/键盘布局）
│   ├── hotkey.py           # 全局热键注册（RegisterHotKey + pynput 兜底）
│   ├── global_keys.py      # 全局键盘钩子（采集按键轨迹）
│   ├── global_mouse.py     # 全局鼠标钩子（失焦/点击外部关闭候选栏）
│   ├── emotion.py          # 情绪识别算法
│   ├── emotion_monitor.py  # 焦点文本监控、情绪/触发词命中
│   ├── injector.py         # 三种注入方式（clipboard/direct/type）
│   ├── uia_text.py         # 经 UIA 读焦点文本（带超时，防卡死 GUI）
│   ├── uia_elevation.py    # UIAccess 顶级窗口提权（重启自身）
│   ├── autostart.py        # 开机自启（注册表，仅 exe 形态）
│   ├── kaomoji_data.py     # 颜文字数据访问
│   ├── user_kaomoji.py     # 用户颜文字库
│   ├── user_triggers.py    # 触发词管理
│   ├── user_state.py       # 用户状态（最近使用等）
│   ├── version.py          # 版本号（取当前 commit 短哈希，供关于页显示）
│   ├── github_release.py    # 关于页「更新与下载」：拉取/下载 GitHub 发行包（QNetworkAccessManager 异步）
│   └── app_icon.py         # 运行时生成/提取应用图标
└── ui/
    ├── unified_window.py   # 统一设置窗口（无边框+亚克力+动画栈+导航）
    ├── update_dialog.py    # 首次启动/版本更新弹窗（欢迎更新 vX(commit)）
    ├── picker_window.py    # 候选栏窗口（颜文字候选条）
    ├── win11_theme.py      # 主题令牌 + 全局 QSS
    ├── toggle_switch.py    # WinUI 风格开关
    ├── fluent_checkbox.py  # 共享 Fluent 复选框
    ├── fluent_combobox.py  # Fluent 下拉框（修复深色弹窗）
    ├── fluent_icons.py     # Fluent 图标字体注册
    ├── animated_stack.py   # 栈式页面容器（淡入淡出）
    ├── log_viewer.py       # 运行日志查看器
    ├── fonts/              # 图标字体文件
    ├── dev/
    │   └── dev_page.py     # 开发者模式页（事件流/诊断/测试功能开关）
    └── pages/              # 业务页面
        ├── home_page.py / library_page.py / custom_kaomoji_page.py
        ├── search_page.py / trigger_page.py / settings_page.py / about_page.py
```

### 3.1 分层原则

- **core/ 与 Qt 解耦**：所有 Windows API、数据、算法都在 `core/`，`ui/` 只负责呈现与交互；这使得核心逻辑可在离屏/无 Qt 环境下做单元测试。
- **ui/ 内聚自绘控件**：`toggle_switch`、`fluent_*` 等为可复用自绘控件，统一从 `win11_theme.Theme` 取色，避免散落硬编码色值。
- **配置单一权威源**：`main.apply_settings` 是配置变更的唯一落盘+广播入口；各页通过信号链 `config_applied → main.apply_settings` 同步。

> **经验提示（配置字典引用陷阱）**：`SettingsPage` 在构造/`refresh_from_config` 期间存的是 `config` 的**副本**，用户交互时 `_on_apply` 会替换成新副本。若多页共享同一字典引用而中途被替换，会造成"开发者模式"等状态在页间读不一致。统一窗口在 `apply_config` 里把 `settings_page.config`/`about_page.config` **重新指向同一权威字典**来规避。

---

## 4. 架构设计

### 4.1 分层与数据流

```
[全局钩子/UIA 采样] → emotion_monitor ──┐
                                        ├─→ 命中判定 ─→ PickerWindow 弹出候选
[用户热键] ────────────────────────────┘
        │
        ▼
SettingsPage / AboutPage / DevPage ──(config_applied)──▶ UnifiedWindow._on_settings_applied
                                                          │
                                                          ▼
                                              main.apply_settings(new_cfg)
                                          （落盘 + 主题刷新 + 重注册热键 + 提权尝试 + 监控启停）
```

### 4.2 关键设计机制

**无边框窗口与亚克力**
`UnifiedSettingsWindow` 使用 `FramelessWindowHint + WA_TranslucentBackground`，`paintEvent` 手绘圆角/1px 边框/多层外阴影；`nativeEvent` 处理 `WM_GETMINMAXINFO`（最大化贴工作区不含任务栏）与 `WM_NCHITTEST`（拖拽/八向缩放/标题按钮命中）。最大化时取消圆角/阴影/留白并清 mask。

> **经验提示（亚克力/不透明度语义）**：面板基底 alpha 必须**直接以 `panel_alpha`（=不透明度）为基底**——亚克力开 `a=max(0.6, min(1.0, 0.55+0.45*panel_alpha))`、关 `a=max(0.4, min(1.0, panel_alpha))`。切勿再乘 `window_tint` 固有 alpha(≤0.85)，否则拉满 100% 面板仍半透，且比关亚克力时更透（旧 bug）。

> **经验提示（页面切换透出桌面 / 卡顿，最终正确方案）**：`ui/animated_stack.py` 的 `AnimatedStackedWidget.slide_to` 做页面过渡。主窗体是 `WA_TranslucentBackground`（亚克力/半透明），**任何页面只要 opacity<1 的瞬间，都会透过半透窗体露出背后桌面**——这是「切换时短暂消失并透入画面」的唯一根因。反例（都试过、都错）：① 给新页做 0→1 淡入（双半透叠加透桌）；② 给旧页加 `QGraphicsOpacityEffect` 淡出——不仅仍会透桌，该效果**每帧把整页渲染到离屏缓冲再合成**，大页面（如关于页）下直接卡成 PPT；③ 动画期间用 `setStyleSheet` 临时改内容区背景——触发整棵内容树（几百控件）样式重算，又一巨大卡顿源，且没真正消除透明度问题。**最终方案**：切换时**任何页面都不施加透明度、绝不动样式表**——旧页在 `setCurrentWidget` 时直接隐藏，仅「新页」做一次轻微上浮就位（`pos` 动画，不透明度恒为 1）。全程无 `GraphicsEffect`、无 `setStyleSheet` 调用 → 零卡顿且绝不透桌；顶部几像素留白是内容区亚克力背景（与常态一致）。`_busy` 标志防重入。
> **经验提示（几何警告 / 「卡住无法缩小」真 bug）**：最大化↔还原过渡时可能打印 `QWindowsWindow::setGeometry: Unable to set geometry WxH ... Resulting geometry: <工作区尺寸>`。成因：`WM_GETMINMAXINFO` 把 `ptMaxSize` 设为工作区尺寸，而过渡瞬间窗口仍带 `WS_MAXIMIZE` 原生样式，Qt 下发普通尺寸 `setGeometry` 时被 Windows 钳到工作区。
> - **瞬时出现的**：过渡一帧内自动恢复，属良性噪音（仅终端可见）。
> - **持续出现且伴随「窗口卡住、鼠标拖不动边框、点还原无效」**：是**真 bug**。根因——无边框窗口在**隐藏态**（如收进托盘）调 `setWindowState(NoState)` 只清 Qt 侧状态、**不清原生 `WS_MAXIMIZE`**；重新 `show()` 为普通态时原生仍带最大化样式，`setGeometry` 被钳到工作区，且 `WM_NCHITTEST` 在 maximized 下禁用缩放热区 → 卡死无法缩小。
> - **修复**：`_clear_native_maximized()` 用 `SetWindowLongW` 清 `WS_MAXIMIZE` + `SetWindowPos(FRAMECHANGED)` 刷新样式，已在**两处**接线：① `_ensure_normal_geometry()`（标题栏「还原」按钮路径）；② `show()` 的普通几何恢复分支（`setGeometry(_normal_geometry)` 之前）。这样重新显示为普通态时不会残留最大化样式，警告消失、窗口可正常缩放。

**主题系统**
令牌集中在 `ui/win11_theme.py` 的 `Theme` 类，`Theme(name).style_sheet()` 生成全局 QSS，由 `app.setStyleSheet(...)` 统一驱动。`Theme(name).menu_style()` 单独覆盖无父 `QMenu`（托盘菜单不继承 app QSS）。

> **经验提示（QSS 级联割裂，已踩三次）**：带自身 `setStyleSheet` 的控件，其**派生控件不再继承更上层祖先的样式规则**（Qt 行为）。若中间容器只设 `background:transparent`，内部 `QPushButton#AccentButton`/滑块/开关会"降级"为原生样式（浅色下白底白字不可见）。正确做法：中间容器要应用**完整** `Theme.style_sheet()` 再追加自身规则（如 `DevPage._apply_content_style`、`UnifiedWindow._refresh_content_sheet`）。

**全局钩子与健壮性**
热键/键盘/鼠标钩子均在独立线程或系统级回调中运行。UIA 文本读取（`core/uia_text.py`）走 `_UIAWorker` 独立线程 + `join(timeout)` + 忙碌守护，默认超时 0.2~0.35s，超时回退（文本→''、可编辑→True、矩形→None）。

> **经验提示（卡死即静默失效）**：目标程序（浏览器/Electron）卡死时 UIA 同步调用会**无限挂起**——在主线程调用会冻结整个 GUI（候选条定位/失焦检测），在采样线程调用会拖死自动弹出。带超时回退是底线，验证手段：用慢函数断言调用方返回时间 < 超时且拿到回退默认值。

**注入链路**
`injector.py` 三种方式：剪贴板（最稳）、`WM_CHAR` 直接投递（绕过 IME/剪贴板）、type 模式（先 `PostMessageW(WM_INPUTLANGCHANGEREQUEST)` 切英文布局、轮询生效再打、打完切回）。type 模式专属"不想碰剪贴板"的用户。

**UIAccess 顶级窗口提权**
`core/uia_elevation.py`：管理员下冒充同会话 `winlogon.exe`(SYSTEM) → `DuplicateTokenEx` 复制自身令牌并 `SetTokenInformation(TokenUIAccess=1)` → `CreateProcessAsUser` **重启自身**。`ensure_uiaccess(config)` 仅当 `use_uia_elevation=True` 才尝试；返回结构化 `status` 字典（`attempted/granted/needs_admin/relaunched/message`），并在"未重启"分支通过 `on_result` 回调把失败/需管理员等信息回传给 UI 弹窗，杜绝"静默失败、无任何提示"。新增 `is_user_admin()` / `has_uiaccess()` 供设置页做**管理员权限检查**与**真实状态反馈**。

> **经验提示（UIA 提权三大坑）**：
> 1. **运行中改 UIAccess 无效**——只能另起进程（开源共识）。最初用 `NtSetInformationProcess` 给当前进程打补丁是死路。
> 2. **ctypes 常量必须真实核对**：`TokenSessionId` 枚举值是 **12**（误用 28 实为 `TokenLogonSid`，返回 `ERROR_INSUFFICIENT_BUFFER` 让提权静默失效）；`EnumProcesses` 首参传数组本身而非 `byref`；`CloseHandle` 在 `kernel32` 而非 `advapi32`。这些在真实 Windows 上会抛 `AttributeError` 被外层吞掉，表现成"开了开关也没用"。
> 3. **`CreateProcessAsUserW` 必须带 `CREATE_UNICODE_ENVIRONMENT`（0x400）**——本程序传入的是宽字符（`create_unicode_buffer`）环境块，若 `dwCreationFlags` 为 `0`，Windows 会按 ANSI 解析而报 **`ERROR_INVALID_PARAMETER`(0x57)**，导致"令牌已生成但重启永不发生、毫无提示"。这是"管理员下开启 UIA 无反应/无重启"的**直接根因**（离屏实跑复现并修复）。
> 4. **打开 winlogon 令牌前要先 `AdjustTokenPrivileges` 启用 `SeDebugPrivilege`**——管理员令牌默认"拥有但禁用"该特权，不启用则 `OpenProcessToken(winlogon, TOKEN_DUPLICATE)` 直接 `ACCESS_DENIED`，同样让提权静默失败。
> 5. **状态要反映真实 UIAccess，而非配置位**——`use_uia_elevation` 一旦勾选就持久化为 `True`，但 UIAccess 是否真拿到并不回写。务必用 `has_uiaccess()` 在设置页展示"✓ 已生效 / ⚠ 已开启但未生效"的真实状态，否则会出现"重启后开关亮着却没生效"的假象。
> 6. **`QThreadStorage: entry N destroyed before end of thread` 是良性噪音**——UIA 提权成功后旧进程用 `ExitProcess(0)` 强杀自身，Qt 来不及按序拆线程而临终打印此提示。它只出现在被替换的旧进程、不影响功能与新进程；从终端启动才看得见。若要求命令行干净，可改为 `app.quit()` 优雅退出（代价：旧进程非瞬时消失）。
> 7. **关闭 UIA 也要重启去令牌，否则「置顶」不消失**——UIAccess 是进程级令牌属性，运行中改不了。开启时另起带 `TokenUIAccess=1` 的进程；**关闭时若当前进程仍带 UIAccess（`has_uiaccess()==True`），必须再复制自身令牌并 `SetTokenInformation(TokenUIAccess=0)` 后 `CreateProcessAsUser` 重启自身**，窗口才会退出 `ZBID_UIACCESS` 高 Z 序带、取消置顶。否则配置已关、窗口却仍一直置顶（用户原话"关闭 UIA 提权后置顶不会马上消除"）。防无限重启守卫需**按「目标」编码**（`KAOMOJI_UIA_RELAUNCH` 环境变量存目标值 `"0"`/`"1"`），否则"先开→重启带 UIAccess，再关→需再重启去 UIAccess"会被上一次守卫误拦。

**原生控件黑条**
自绘 `QAbstractButton`（ToggleSwitch）在 hover/focus 时 Windows 会画原生黑条。修复套路（参考 `QComboBox::drop-down` 黑条）：在**全局 QSS** 显式覆盖 `QAbstractButton#ToggleSwitch` 的 `:hover`/`:focus` 状态为透明，并让行容器 `QWidget#SettingsRow` 透明——仅改控件自身属性不生效，因为 `WA_StyledBackground=False` 会让本地 `background:transparent` 失效。

### 4.3 状态与信号联动

- `UnifiedSettingsWindow` 持有唯一权威 `config` 字典与各页引用；
- 设置项改动经 `SettingsPage.config_applied` → `UnifiedWindow._on_settings_applied` → `main.apply_settings`（落盘+广播）；
- 开发者页「测试功能」开关经 `DevPage.test_features_changed` → `UnifiedWindow._on_test_features_changed` → 仅刷新设置页可见性（轻量，不触发整页重排）。

---

## 5. 核心功能清单

| 模块 | 功能 | 测试模式 |
|------|------|----------|
| 候选栏 | 颜文字候选条悬浮、选择、插入 | 否 |
| 唤起 | 全局热键 + 自动弹出（焦点文本/情绪/触发词） | 否 |
| 注入 | 直接字符投递（默认常驻）；剪贴板粘贴 / 模拟键入为测试功能，仅测试模式可选 | 部分（clipboard/type 是） |
| 情绪 | 情绪识别与触发短语 | 否 |
| 数据 | 自定义颜文字库、标签、导入导出 | 否 |
| 设置 | 主题/不透明度/输入方式/热键/开机自启 | 否 |
| 提权 | UIAccess 顶级窗口（与屏幕键盘互相覆盖） | **是**（高级分区） |
| 外观实验 | 亚克力模糊背景（窗口与候选栏） | **是**（高级分区） |
| 开发者 | 事件流/诊断/模拟触发/A-B 注入对比 | 否（需先开开发者模式） |
| 测试模式 | 总开关控制测试功能可见性 | — |

---

## 6. 开发流程：从需求到上线

### 6.1 需求与隔离（测试模式优先）

任何新功能默认归入**测试模式**：
- 配置 `DEFAULT_CONFIG` 增加开关（如 `use_uia_elevation`）；
- 开发者页「测试功能」卡片提供总开关；
- 设置页用 `_add_row(..., test_feature=True)` 登记测试项，`apply_test_feature_visibility()` 按 `use_test_features` 整体显隐（连分区标题一起隐藏，避免空卡片）；
- `use_test_features` 关闭时，**正式环境看不到该功能的设置 UI**；但仅靠隐藏 UI 拦不住功能——若配置位仍为真（例如曾在测试模式开启后关闭、或直接改 config.json），功能代码仍会读取并生效（"测试功能逃逸"）。

> **功能门禁=保存时归位**：`main.save_config()` 在落盘前检查 `use_test_features`；为 `False` 时按 `_TEST_FEATURE_DEFAULTS`（如 `use_uia_elevation: False`、`acrylic: False`）把各测试项强制归位为默认值。这样「关闭测试模式」会真正把未毕业功能关掉，覆盖设置页保存、开发者页开关测试模式等所有写入路径，杜绝逃逸。新测试功能只需往 `_TEST_FEATURE_DEFAULTS` 加一项即可获得该保护。亚克力模糊即此例：`DEFAULT_CONFIG["acrylic"]` 默认 `True`（测试模式用户默认仍开模糊），`_TEST_FEATURE_DEFAULTS` 加 `"acrylic": False` 后测试关时归位关闭、并随「高级」分区整段隐藏（设置页用 `_add_row(..., test_feature=True)` 登记）。

> **输入方式门禁（特例：多值设置）**：`input_method` 默认 `direct`（直接字符投递，始终可用）；`clipboard`/`type` 属测试功能——`settings_page._apply_input_method_availability()` 在 `use_test_features=False` 时禁用这两项的下拉条目（仅 `direct` 可选），并在当前选中了被禁用项时回退到 `direct`；`save_config` 另设 `_ALWAYS_AVAILABLE_INPUT_METHODS={"direct"}`，测试模式关闭时把 `input_method` 归位为 `direct`。即"默认=直接字符输入，其他模式仅测试模式可选"。

> **经验提示**：新功能在 `use_test_features=False` 下必须"零副作用"——函数入口先读开关，关闭/缺省/非 win32/None 立即返回、零系统调用；任何失败只记日志不抛异常。这样默认行为永远不受影响。

### 6.2 实现与编码规范

- 自绘控件统一从 `Theme` 取色，禁用硬编码色值；
- 但凡 `ctypes` 调用：显式设置 `argtypes`/`restype`，常量用**真实枚举值**（以 `ctypes.windll` 实跑验证 `GetLastError`），不要凭记忆；
- 跨页状态不要依赖"恰好没被替换"的共享字典，走信号链同步。

### 6.3 验证策略（离屏优先）

沙箱已可 `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe` 真实构造 PySide6 组件做断言（状态机、主题同步、可见性等），无需真机即可覆盖大量逻辑。

> **经验提示**：
> - **真实 Win32 调用要在离屏里真跑**，不要只 `py_compile`。本项目的三处 ctypes bug（TokenSessionId=12、EnumProcesses 数组直传、CloseHandle 归属）正是离屏实跑才暴露。
> - **offscreen 下 `isHidden()`/`isVisible()` 不可靠**：未 `show()` 的控件子级不随祖先隐藏态传播，应断言**已登记的父容器** `isHidden()`，而非深层子控件。
> - **黑条类"看不见的样式 bug"**：offscreen 无字体时控件尺寸偏小（仅 padding）属假象；可 `ok.grab()` 转 `QImage` 取色，比对是否为预期强调色（浅色 #0067c0 / 深色 #60cdff）。

### 6.4 提交与评审

- 采用 **conventional commits**（feat/fix/docs…），**每次改动按逻辑分组提交并 push**（不再逐次确认）；
- 版本号统一为当前 git commit 短哈希（`core/version.py` 的 `get_app_version()`）：**源码态始终以 `git rev-parse --short HEAD` 为准**；仅**打包态（`sys.frozen` 为真、onedir 内无 `.git`）**才回退到 `build.py` 构建期烘焙的 `core/_build_version.py`。`build.py` 构建结束后会**主动删除**源码树里的该临时文件，避免污染源码态版本号；
- `dist/` 不进 git、`config.json`（运行时本地配置）不进 commit；
- 本机 push；GitHub Release API 受限（401），**只能 Web UI 发布**。

> **经验提示（行尾与协作）**：Windows 上 Git 常把 `LF` 自动转 `CRLF`（提交时出现 `LF will be replaced by CRLF` 警告）。团队协作者需统一 `.gitattributes` 或编辑器行尾，避免 diff 噪声与"看似改动实则仅行尾"的乌龙。

### 6.5 打包与部署

`build.py` 调用 PyInstaller 生成 onedir：
- 可写配置落 `%APPDATA%/KaomojiAssistant`，只读资源从 `_internal` 取（`runtime.resource_path` 多候选回退）；
- `--add-data` 目标写**目录**（`ui/fonts`），否则资源会嵌深一层导致字体注册失败、图标变豆腐块；
- 验证：`QT_QPA_PLATFORM=offscreen` 可让源码态与 exe 形态都常驻运行。
- 版本与更新弹窗：`build.py` 支持 `--version v1.2`（烘焙人工版本标签）与 `--notes`/`--notes-file`（更新说明）；三者写入 `core/_build_version.py`（`BUILD_COMMIT`/`BUILD_VERSION`/`BUILD_NOTES`，已加入 `.gitignore`）。**该文件仅用于打包态回退**：`core/version.py._build_meta()` 在 `sys.frozen` 为真（打包态）时读它，否则（源码态）一律走 `git` 取 commit，且 `build.py` 在 PyInstaller 跑完后立即 `os.remove` 该临时文件——因此**跑 build 不会改到源码态程序的版本号**（旧版设计曾优先读该文件，导致源码态被污染）。`core/version.py` 的 `get_app_version()` 在带标签时组合显示为 `v1.2(bdb300a)`，关于页与更新弹窗统一从此取；**开发构建（无标签）仅显示 commit 短哈希**。`main.py` 启动后仅当**正式发布构建**（`is_release_build()` 为真）且本次版本与上次已见版本（存 `%APPDATA%/KaomojiAssistant/last_seen_version.txt`）不同，才弹一次「欢迎更新 v1.2(bdb300a)」对话框（`ui/update_dialog.py`）；开发构建与同版本重开均不弹。`build.py` 无命令行参数启动时进入**交互模式**：逐项 input 提示版本标签/更新说明（支持多行或 `@文件`，可全部留空做开发构建），最后确认再构建；传入任意命令行参数则跳过交互、按 argparse 直接编译（保留原路径）。
- **开发者模式「模拟升级」**：开发者页新增「模拟升级」卡片，按钮「关闭应用并模拟升级」调用 `core.restart.restart_with_update_popup()`——给新进程注入 `KAOMOJI_FORCE_UPDATE_POPUP=1` 环境变量、以 `CREATE_NO_WINDOW` 重启当前进程（`sys.frozen` 用 exe、否则用 `python + 原 argv`），再 `QApplication.quit()` 退出旧进程。`should_show_update_popup()` 见该标志即返回 `True`（绕过「必须正式发布构建」与「版本已见」两道门槛），使开发构建也能弹更新框；弹完后 `UpdateDialog.maybe_show` 顺手清掉该标志。用于不重新打包即测试更新弹窗流程。
- **关于页「更新与下载」**：`ui/pages/about_page.py` 新增「更新与下载」卡片——「检查更新」按钮经 `core/github_release.ReleasesAPI`（底层 `QNetworkAccessManager`，异步非阻塞、带 `User-Agent` 与超时）拉取公开仓库 `owoflying/kaomoji-assistant` 的 `/releases/latest`；拿到后显示**当前版本**（`get_app_version()`）与**最新版本号** + 发布说明摘要，并据 `pick_windows_asset()`（优先 `.zip` 再 `.exe`）给出「下载 {资产名}」主按钮。为避免长资产名把按钮撑得过宽，文件名超过 28 字符时截断显示并保留完整名在 `toolTip`；按钮最大宽度限制为 260px。点按后**直接下载到用户 Downloads 目录**（`downloads_dir()` 取 `QStandardPaths.DownloadLocation`，带进度信号），完成后提供「打开下载文件夹」（不自动运行，避免安全风险）。`parse_release()` 为纯函数（便于离线测试）；网络错误/无资产/下载失败均有友好提示。该卡片为常驻功能（不归测试模式门控）。**下载自动校验 SHA-256**：`parse_release()` 从 GitHub 资产对象的 `digest` 字段（形如 `sha256:<hex>`，2023-02 起所有上传资产均提供，无需手工维护清单）提取权威哈希并随资产带出；`ReleasesAPI.download_asset(url, dest, expected_sha256)` 下载完成后，优先取响应头 `x-checksum-sha256`（可能因重定向被剥离），否则回退 `expected_sha256`，再在**后台 `HashWorker` 线程**计算本地文件 SHA-256（避免大文件阻塞主线程）并比对——匹配才 `download_finished`、不匹配则删除损坏文件并报 `download_error`；若服务端完全未提供哈希则跳过校验、直接完成。UI 在「校验中…」「✓ 校验通过」两态给出反馈。**下载进度百分比用资产真实大小作分母**：`ReleasesAPI.download_asset(url, dest, expected_sha256, expected_size)` 的 `expected_size` 来自 `parse_release` 解析出的 `assets[].size`（releases API 的权威字节数）。原因——GitHub 发行资产 URL 会 302 跳转到 CDN，`downloadProgress` 信号给到的 `total` 经常不可靠（偏小或仅反映重定向响应），直接用它作分母会让进度条提前冲到 ~50% 后卡住、直到 `finished` 才跳 100%；以已知真实大小作分母后进度准确线性增长。`about_page._on_latest_ready` 把 `asset["size"]` 存为 `self._dl_size` 并随下载请求传入。下载请求**不设 `setTransferTimeout`**（仅元数据请求保留 15s），避免慢速网络下大发行包因超时被中断。

> **经验提示（QSS 级联与 QScrollArea）**：`about_page` 曾在 `QScrollArea` 上调用 `setStyleSheet("QScrollArea{background:transparent;border:none}")`，意图让滚动区透明。但带自身样式表的控件会切断上层 QSS 级联，其内部子控件的 `QPushButton#AccentButton` 等规则因此失效，导致「下载」按钮变成原生白底、文字不可见（或过宽）。**修复**：删除滚动区自身的 `setStyleSheet`，依赖全局 QSS 中已有的 `QScrollArea { background: transparent; border: none; }` 规则；如需额外规则，应在完整 `Theme.style_sheet()` 基础上追加，而不是只写局部规则。

> **经验提示（GUI 程序启动弹黑框）**：打包为 `--windowed` 后，任何 spawn 的 console 子进程（如 `git`）都会弹一个命令行黑框。原 `_build_meta` 每次无条件先调 `git`，而启动期 `get_app_version`/`is_release_build`/`should_show_update_popup` 至少被调 3 次（关于页、更新弹窗判断、发布构建判断），故每次启动弹 **3 次黑框**。源码/offscreen 测试因父进程本身有控制台而复用了控制台不暴露。**修复**：打包态优先读 `core/_build_version.py`（构建期烘焙）且不调 `git`，结果加进程内缓存（`_META_CACHE`）；`_git_commit` 也加 `CREATE_NO_WINDOW` 双保险。

> **经验提示（build 脚本污染源码态版本号）**：`write_build_version()` 会把版本信息写入**源码树**的 `core/_build_version.py`。旧版 `_build_meta()` 优先 `from core import _build_version`，于是只要 `build.py` 跑过一次、该文件残留在源码树，源码态程序就会误读它、版本号从 `git HEAD` 变成烘焙出来的 `v1.2(xxx)`——用户以为"源码态版本号也被改了"。**正确分层**：源码态（`sys.frozen` 为假）一律走 `git`，打包态才读 `_build_version`；并且 `build.py` 在 PyInstaller 跑完的 `finally` 里 `os.remove` 该临时文件，使其不再长期驻留源码树。

> **经验提示（打包形态陷阱）**：源码态与冻结态是**两套互不干扰**的路径——`resource_path()` 在 frozen 下指向 `_internal`、源码下指向项目目录。曾因"冻结态已写配置目录、源码态又写项目目录"产生两份配置，排查许久。冻结态可写区务必与只读资源区严格分离。

> **经验提示（下载校验用后台线程）**：发行包下载完成后的 SHA-256 校验是 CPU 密集活（几十 MB 文件分块 `hashlib`），若直接在主线程/下载完成信号槽里同步算，会卡住 GUI（表现为"下载进度 100% 但界面假死 1~2 秒"）。延续"重活不要放主线程"的一贯原则，用 `HashWorker(QThread)` 在后台算哈希，算完经信号回主线程比对。**哈希来源优先级**：下载响应头 `x-checksum-sha256` > 资产 `digest` 字段解析出的 `expected_sha256`（前者在 GitHub CDN 重定向到 `objects.githubusercontent.com` 后常被剥离，故以 digest 为可靠兜底）；两者皆空则跳过校验、不报错（兼容无 digest 的老 release）。不匹配时务必删除损坏文件，避免用户误用半截包。

### 6.6 发布

- 推送到 `main` 后，Release 走 GitHub Web UI（API 受限）；
- 变更说明写入 `release-notes-*.md`，重大修复附"根因 + 修复 + 验证"。

### 6.7 线上反馈与迭代

- 运行日志经 `qInstallMessageHandler` 收集进全局 `LOG_BUFFER`（白名单丢弃 `OpenType support missing` 等纯噪声），`ui/log_viewer.py` 提供查看/保存/复制，入口在托盘菜单「查看日志」（仅开发者模式可见）；
- 用户反馈优先在开发者模式下复现（事件流/诊断自检），再决定是否移出测试模式。

---

## 7. 关键注意事项速查（嵌入各节要点汇总）

| 环节 | 易错点 | 正确做法 |
|------|--------|----------|
| 技术选型 | 凭记忆写 ctypes 常量 | 离屏实跑验证 `GetLastError` |
| 主题/QSS | 中间容器只设 background | 应用"完整"全局 QSS 后再追加 |
| 无边框/亚克力 | 叠加 window_tint 固有 alpha | 直接以 panel_alpha 为基底 |
| 钩子/UIA | 主线程同步读 UIA | 独立线程 + 超时回退 |
| 提权 | 运行中改 UIAccess / advapi32.CloseHandle / 漏 `CREATE_UNICODE_ENVIRONMENT` / 未启用 SeDebugPrivilege / 只看配置位 | 重启自身 + kernel32.CloseHandle + 带 0x400 标志 + 启用 SeDebugPrivilege + `has_uiaccess()` 反映真实状态 |
| 黑条 | 只改控件自身属性 | 全局 QSS 覆盖 hover/focus + 容器透明 |
| 测试模式 | 关闭时仍有系统调用/异常 | 入口读开关，失败只记日志 |
| 打包 | --add-data 写文件名 | 写目录；冻结/源码路径分离 |
| 协作 | LF/CRLF 不一致 | 统一 .gitattributes |

> **总结**：本项目最大的迭代风险不在"功能写不出"，而在"**看起来能用实则运行时失效**"——ctypes 常量、QSS 级联、UIA 超时、原生背景黑条、打包资源路径，每一类都需要**贴近真实 Windows 行为的验证**而非代码审查。把"测试模式默认隔离 + 离屏断言验证 + 全局 QSS 兜底原生绘制"作为基线规范，可避免绝大多数回归。
