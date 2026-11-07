# kaomoji-assistant 项目架构说明

> 颜文字输入辅助器 · Win11 风格常驻托盘工具
> 技术栈：Python 3.13 + PySide6 + pynput + Win32/COM (ctypes)
> 规模：61 个 Python 文件 / 约 15k 行（core 5.2k · ui 7.4k · 装配层 0.65k · tests 1.0k）
> 本文档基于当前代码实测梳理。**设计说明**见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，
> **踩坑手册**见 [`docs/PITFALLS.md`](docs/PITFALLS.md)（本文已被拆分，定位是"现状梳理"）。

---

## 1. 一句话定位

在**任意程序**里打字时，程序通过全局钩子与 UI Automation 感知焦点输入，按**热键 / 情绪关键词 / 快捷短语**三种方式弹出悬浮候选条，用户选中后把颜文字**注入回原光标位置**——全程不抢焦点、不改输入法状态、不打断输入流。

```
打字的永远是别的程序，本程序只做"旁路观察 + 悬浮建议 + 一次性回写"
```

---

## 2. 技术栈与依赖

| 维度 | 选型 | 用在哪 |
|------|------|--------|
| 语言 | Python 3.12/3.13 | 全项目 |
| GUI | **PySide6 6.11.x** | 全部窗口、控件、QSS 主题、异步网络（`QNetworkAccessManager`） |
| 全局输入 | **pynput** + Win32 `RegisterHotKey` + 低层钩子 | 热键注册、按键采样、候选条选字拦截 |
| 文本读取 | **UI Automation (COM)**，纯 ctypes 手写 | 读浏览器/Electron/WPF 等自绘控件的焦点文本 |
| 文本注入 | 剪贴板 / `WM_CHAR` 直投 / 模拟键入 | 三种注入方式 |
| 打包 | **PyInstaller 6.x onedir --windowed** | 发布 exe |
| 依赖 | `PySide6>=6.5`、`pynput>=1.7.6`、`pyinstaller>=6.0` | `requirements.txt` |

> 依赖极少：**没有** requests / numpy / 任何 ML 库。情绪识别是纯关键词匹配，更新检查走 Qt 自带网络栈。

---

## 3. 目录结构全景

```
kaomoji-assistant/
├── main.py                    # 入口薄壳（54 行）
├── app_runtime.py             # ★ 装配层 AppRuntime：唯二的中枢（649 行）
├── config.json                # 运行时本地配置（不入 git）
├── build.py / build.bat       # PyInstaller 打包脚本（onedir + 版本烘焙）
├── KaomojiAssistant.spec      # PyInstaller spec
├── app.ico                    # 构建期程序化生成
│
├── core/                      # 业务与平台层（≈5.1k 行，仅少量模块触碰 Qt）
│   ├── runtime.py             #   运行态路径解析（frozen / source 双形态）
│   ├── config_store.py        #   默认配置 + 读写 + 「测试功能」门禁
│   ├── applog.py              #   日志环形缓冲 + Qt 日志过滤 + 未捕获异常
│   ├── version.py             #   版本号（源码取 git commit / 打包读烘焙文件）
│   ├── autostart.py           #   开机自启（HKCU\...\Run 注册表）
│   ├── restart.py             #   重启自身（模拟升级用）
│   ├── app_icon.py            #   程序化绘制托盘/exe 图标
│   │
│   ├── win_utils.py           #   ★ Win32 底层封装（热键解析/焦点控件/键盘布局/WM_CHAR）
│   ├── uia_text.py            #   ★ UIA 读焦点文本（独立线程 + 超时回退）
│   ├── uia_elevation.py       #   ★ UIAccess 提权（冒充 winlogon → 重启自身）
│   ├── hotkey.py              #   全局热键（RegisterHotKey）+ pynput 兜底
│   ├── global_keys.py         #   低层键盘钩子：候选条选字/翻页/关闭拦截
│   ├── global_mouse.py        #   低层鼠标钩子：点外部关闭面板
│   │
│   ├── emotion.py             #   情绪关键词字典 + detect_last（取最靠右命中）
│   ├── emotion_monitor.py     #   ★ 打字监听：钩子→采样线程→情绪/触发词判定
│   ├── injector.py            #   ★ 三种注入方式（clipboard / direct / type）
│   │
│   ├── kaomoji_data.py        #   内置颜文字库 + 插件数据源内存合并
│   ├── user_kaomoji.py        #   用户自定义颜文字（分组/标签/拖拽排序/导入导出）
│   ├── user_triggers.py       #   快捷短语（触发词 → 输出）
│   ├── user_state.py          #   最近使用 / 收藏 / 使用统计（带白名单自净）
│   │
│   ├── plugins.py             #   ★ 插件系统（发现/加载/能力注册/异常隔离）
│   ├── ui_hooks.py            #   ★ UI 钩子协议：core 声明"需要什么界面能力"，实现由 ui/ 注入
│   └── github_release.py      #   GitHub 发行包查询 + 下载 + SHA-256 校验
│
├── ui/                        # 呈现层（≈7.4k 行）
│   ├── frameless_shell.py     #   ★ 无边框窗口机械逻辑（圆角/缩放/最大化/状态保持）
│   ├── unified_nav.py         #   ★ 侧边栏导航与页面栈调度、开发者页/插件页增删
│   ├── unified_window.py      #   ★ 统一主面板组装层（559 行，原 1172 行）
│   ├── picker_window.py       #   ★ 悬浮候选条（794 行）
│   ├── plugin_ui_hooks.py     #   ★ ui_hooks 协议的真实实现（build_ui_hooks）
│   ├── win11_theme.py         #   ★ 主题令牌 + 全局 QSS（534 行）
│   ├── win_style.py           #   DWM 背景模糊 / 深色标题栏
│   ├── frameless_panel.py     #   无边框窗口共用笔法（阴影+基底+渐变+描边）
│   ├── animated_stack.py      #   页面切换动画栈（只做位移，不做透明度）
│   ├── fluent_icons.py        #   Fluent System Icons 字体封装
│   ├── fluent_checkbox.py     #   Fluent 复选框
│   ├── fluent_combobox.py     #   Fluent 下拉框（修深色弹窗）
│   ├── toggle_switch.py       #   WinUI 开关（自绘）
│   ├── plugin_dialog.py       #   插件用主题化无边框弹窗
│   ├── log_viewer.py          #   运行日志查看器
│   ├── update_dialog.py       #   「欢迎更新 vX(commit)」弹窗
│   ├── diagnostics.py         #   `--diag` 自检（字体/图标落盘到 diag.txt）
│   ├── fonts/                 #   FluentSystemIcons-Regular.ttf（2.8 MB）
│   ├── pages/                 #   主面板业务页
│   │   ├── _scaffold.py       #     页面脚手架函数（page_layout/card/page_title…）
│   │   ├── home_page.py       #     主页仪表盘
│   │   ├── library_page.py    #     颜文字库（分类浏览）
│   │   ├── custom_kaomoji_page.py  # 我的颜文字（增删改/分组/标签/拖拽）
│   │   ├── trigger_page.py    #     快捷短语
│   │   ├── search_page.py     #     搜索（库 + 我的）
│   │   ├── settings_page.py   #     设置（热键/主题/注入方式/权限）
│   │   ├── about_page.py      #     关于 + 检查更新/下载
│   │   └── plugin_page.py     #     插件管理
│   └── dev/
│       └── dev_page.py        #   开发者模式页（事件流/诊断/模拟触发/A-B 对比）
│
├── plugins/                   # 插件目录（源码态）
│   ├── example_kaomoji/       #   API v2 全能力演示插件
│   └── startup_notice/        #   启动提示示例插件
│
├── tests/                     # ★ pytest 离屏用例（100 个，约 10s）
│   ├── conftest.py            #   把 APPDATA 重定向到临时目录，绝不写工作区
│   ├── test_config_store.py   #   类型/枚举/范围校验 + 测试功能门禁
│   ├── test_plugins.py        #   插件生命周期、异常隔离、分层不变式
│   ├── test_emotion.py        #   情绪识别（最靠右命中优先）
│   ├── test_data_stores.py    #   四类 store 的脏数据容错
│   └── test_unified_window.py #   窗口拆分边界、主题链路、门控
│
├── docs/
│   ├── ARCHITECTURE.md        # 设计说明（分层/数据流/机制/流程）
│   ├── PITFALLS.md            # 踩坑手册（19 类根因 + 症状索引 + 提交自检）
│   └── archive/               # 拆分前的历史文档（仅供追溯）
│
└── data/
    ├── kaomoji.json           # 内置颜文字库：10 分类 / 109 条
    ├── user_kaomoji.json      # 用户颜文字（可写）
    ├── user_triggers.json     # 快捷短语（可写）
    ├── user_state.json        # 最近/收藏/统计（可写）
    └── plugin_settings/*.json # 各插件私有配置
```

---

## 4. 分层架构

```
┌──────────────────────────────────────────────────────────────────────┐
│  L0  入口层        main.py                                            │
│      装日志钩子 → QApplication → 注册图标字体 → --diag? → AppRuntime    │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ 只做「建 → 交 → 跑事件循环」
┌───────────────────────────────▼──────────────────────────────────────┐
│  L1  装配层        AppRuntime (app_runtime.py)                        │
│      持有全部组件 · 信号接线 · 配置应用 · 主题广播 · 注入编排 · 托盘菜单  │
│      ⚠ 全项目唯一的"知道所有东西"的地方，其他地方都不允许交叉引用          │
└──────────────┬────────────────────────────────┬──────────────────────┘
               │ 向下调用                        │ 向上发信号
┌──────────────▼──────────────┐   ┌─────────────▼──────────────────────┐
│  L2  UI 呈现层   ui/         │   │  L2' 领域服务层   core/             │
│  unified_window 主面板       │   │  emotion_monitor 监听               │
│  picker_window  候选条       │   │  injector 注入                      │
│  pages/*        7 个业务页   │   │  hotkey / global_keys / global_mouse│
│  dev/*          开发者页     │   │  plugins 插件宿主                    │
│  win11_theme   主题/QSS      │   │  kaomoji_data / user_* 数据          │
└──────────────┬──────────────┘   └─────────────┬──────────────────────┘
               │                                │
┌──────────────▼────────────────────────────────▼──────────────────────┐
│  L3  平台抽象层    win_utils · uia_text · uia_elevation · win_style     │
│      所有 ctypes / Win32 / COM 调用都收敛在这里，上层只见普通函数         │
└──────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────┐
│  L4  基础层       runtime(路径) · config_store · applog · version       │
│                  零依赖、可无界面测试                                   │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.1 分层规约（实测的边界，不是理想口号）

| 层 | 允许依赖 | 实际遵守情况 |
|----|---------|-------------|
| `main.py` | 装配层 + 基础层 | ✅ 干净，无业务状态 |
| `app_runtime.py` | 全部 | ✅ 唯一中枢 |
| `ui/pages/*`、`ui/dev/*` | core 的**数据/服务类** + ui 通用件 | ✅ 页面之间**互不 import**，只经信号通信 |
| `ui/*`（通用件） | 仅 `ui/` 内部 | ✅ 无反向依赖 |
| `core/*` | 仅 `core/` + Qt 基础 | ⚠ **唯一例外**：`core/plugins.py` import 了 `ui/plugin_dialog` 与 `ui/win11_theme`（插件 UI 能力所需）；`core/kaomoji_data`、`core/user_*`、`core/version` 依赖 `core/runtime` 做路径解析 |

> **边界的收益**：`core/emotion.py`、`core/config_store.py`、`core/kaomoji_data.py` 等都能在 `QT_QPA_PLATFORM=offscreen` 或无 Qt 环境下直接单测——这正是 13.5k 行项目还能保持可测性的关键。

### 4.2 依赖图（实测 import 关系，节选主干）

```
main ──► app_runtime ──┬─► ui.unified_window ──┬─► ui.pages.*  ─┬─► ui.win11_theme ──► ui.fluent_icons
                       │                       │                └─► ui.pages._scaffold
                       │                       ├─► ui.animated_stack / ui.frameless_panel / ui.win_style
                       │                       └─► core.plugins (间接经 plugin_page)
                       ├─► ui.picker_window ──┬─► core.global_keys  (低层键盘钩子)
                       │                      ├─► core.global_mouse (低层鼠标钩子)
                       │                      ├─► core.kaomoji_data / core.user_state
                       │                      └─► core.win_utils / core.uia_text
                       ├─► core.emotion_monitor ──► core.emotion / core.win_utils / core.uia_text
                       ├─► core.hotkey ──► core.win_utils
                       ├─► core.injector ──► core.win_utils
                       ├─► core.plugins ──► ui.plugin_dialog / ui.win11_theme (⚠ 唯一 core→ui)
                       └─► core.user_* / core.kaomoji_data ──► core.runtime
```

---

## 5. 核心数据流

### 5.1 三条唤起链路（并发存在，最后汇入同一出口）

```
① 全局热键 ──────────┐
   hotkey.py         │
   RegisterHotKey    │
        │            │
② 打字监听 ──────────┤      ┌──────────────┐
   emotion_monitor   ├─────►│ PickerWindow │──► selected(text, ctx)
   ├ 情绪关键词命中 ──┤      │  候选条 v    │
   └ 快捷短语命中 ────┤      └──────────────┘
        │            │             │
③ 主面板搜索/库页 ───┘             │
   unified.output_selected         │
                                   ▼
                    ┌──────────────────────────────┐
                    │  AppRuntime.on_selected()    │  ★ 唯一注入出口
                    │  1. plugins.run_transforms   │  插件改写文本
                    │  2. monitor.notify_injected  │  告知监听器"这是自己写的"
                    │  3. emit before_insert       │
                    │  4. delete_trigger?          │  可选：安全删掉触发词
                    │  5. injector.inject(method)  │  clipboard/direct/type
                    │  6. emit after_insert        │
                    │  7. state.record_usage       │  使用统计
                    └──────────────────────────────┘
```

**为什么必须收敛到一个出口**：注入前要通知监听器"这段文字是我送进去的"（否则颜文字里的 `?`「哇」会被当成用户新输入，形成自激循环）；插件要拿到统一的上屏前后事件；统计要覆盖所有来源。任何绕过 `on_selected` 的旁路都会破坏这三条不变式。

### 5.2 打字监听内部机制（`core/emotion_monitor.py`）

```
pynput 键盘钩子（全系统）
   │  只做两件事：记 1 个可见字符 → 置 _typed_evt（微秒级返回）
   │  ⚠ 绝不在钩子里调 UIA/COM，否则全系统打字都卡
   ▼
"emotion-sampler" 后台线程（daemon）
   │  _typed_evt.wait() 阻塞，无输入时零 CPU
   ├─ 静默期检查（注入后 1.5s 不采样）
   ├─ 驻留轮询 _dwell_sample：连采 6 次 / 间隔 60ms，覆盖中文 IME 提交滞后
   │     每次采样前 _settle=40ms 让字符落进控件
   │     ┌─ 是原生编辑框(class=Edit/RichEdit) → WM_GETTEXT（亚毫秒）
   │     ├─ 否 → uia_text.get_focused_text(timeout=0.2)（浏览器/Electron/WPF/Qt）
   │     └─ 兜底 → 可见字符缓冲
   ├─ 剔除已注入文本（_strip_injected）→ 防自激
   ├─ 只看文本尾部 30 字符 → 防"情绪焊死"
   └─ _evaluate：
        优先 UserTriggers.match(tail) → trigger_detected 信号
        否则 detect_last(tail)       → emotion_detected 信号
                                    （取"最靠右"命中，跟着最新输入走）
```

**防重触发三重保险**（缺一就会"上屏后再弹一次"）：

| 保险 | 机制 |
|------|------|
| ① 注入静默期 | `notify_injected()` → 1.5s 内完全不采样，挡掉自己模拟的按键 |
| ② 文本剔除 | 采样结果把最近上屏的 8 条颜文字整段删掉再判定 |
| ③ 情绪锁定 | 某情绪弹过即锁定，直到它滚出扫描窗口或换了别的情绪才解锁 |

### 5.3 候选条交互（`ui/picker_window.py` + `core/global_keys.py`）

候选条**不抢焦点**（只 `raise_()`，不 `activateWindow()`），因此选字靠的是**低层键盘钩子**而非 Qt 焦点：

```
候选条可见 → GlobalKeyInterceptor 启用（is_active_cb 回调门控）
              │
   按键分类：A 类专属键（1-9 / -= / ←→ / Esc）→ 面板开着就吞
             B 类双关键（Enter / Space）→ 仅在"用户已表达选字意图"时吞
             C 类其它键 → 放行 + 关闭面板（"继续打字即关闭"）
             修饰键按下时一律不吞
             吞 keydown 同时吞配对 keyup（不留孤立 keyup）
```

再叠加两道守卫：`GlobalMouseInterceptor`（点面板外部即关）+ 350ms 焦点轮询定时器（焦点跑到别的编辑框即关）。

### 5.4 配置变更链路（单向、唯一落盘口）

```
SettingsPage / DevPage / AboutPage 用户操作
        │  config_applied(dict)  ← 各页发新 dict 副本
        ▼
UnifiedSettingsWindow._on_settings_applied
        │
        ▼
AppRuntime.apply_settings(new_cfg)          ★ 唯一落盘 + 广播入口
        ├─ self.config.clear() + update()   原地更新（保持 dict 身份不变！）
        ├─ diff 出真正变化的键集合 changed
        ├─ save_config() → enforce_test_feature_gate() → 写 JSON
        ├─ autostart 变化 → 写注册表
        ├─ theme 变化 → 重贴全局 QSS（幂等短路）+ 日志窗换肤 + 重挂轮询
        ├─ window.apply_config / unified.apply_config / plugins.set_theme_name
        ├─ hotkey 变化 → 重新 RegisterHotKey
        ├─ UIA 提权状态检查（一次性提示）
        └─ auto_popup 变化 → 启停监听
```

两个关键约束（代码注释与 git 历史都反复踩过）：

1. **`self.config` 必须原地更新**——`PluginManager` 持有该 dict 的引用，替换对象会让它继续读写一份已丢弃的旧字典；
2. **QSS 重贴必须幂等**——`app.setStyleSheet()` 在全量控件上重新 polish 实测 ≈500ms，无条件执行会让"改任何一项设置都卡近一秒"。

### 5.5 启动装配顺序（`AppRuntime._build`）

顺序不是随意排的，每一步都有依赖理由：

```
1. _ensure_uiaccess_at_startup   可能直接重启进程，后面全都白建 → 必须最早
2. _apply_initial_theme          后续窗口构造要用 QSS
3. _sync_autostart               注册表与配置对齐
4. _build_core                   KaomojiData → PluginManager(discover/load) → UserKaomoji
                                 → UserTriggers → UserState → Injector → DevEventBus
                                 ⚠ 插件必须先于 UserState：插件颜文字要在"最近/收藏"白名单里
5. _build_windows                PickerWindow / NativeHotkeyManager / EmotionMonitor
                                 / UnifiedSettingsWindow
6. _wire_components              全部信号接线
7. _build_tray                   托盘最后建 → 插件 notify 能力用 set_ui_hooks 后置注入
8. _update_system_theme_watcher  仅 theme=="system" 时启动 3s 轮询
9. _schedule_startup_hooks       QTimer.singleShot(0)：先弹更新框，再发插件 startup
10. _start_services              注册热键 + 启动监听
11. _install_quit_hooks          aboutToQuit 依序收尾
```

---

## 6. 关键设计机制

### 6.1 无边框窗口 + 亚克力 + 自绘

`UnifiedSettingsWindow` 继承 `QMainWindow`，`FramelessWindowHint + WA_TranslucentBackground`：

- `paintEvent` 手绘圆角 / 1px 描边 / 多层外阴影；
- `nativeEvent` 处理三个 Win32 消息：`WM_GETMINMAXINFO`（最大化贴工作区而非盖住任务栏）、`WM_NCHITTEST`（标题栏拖拽 + 八向缩放）、`WM_ERASEBKGND`；
- 最大化时取消圆角/阴影/留白并清 mask；
- 四个自绘窗口（主面板 / 日志窗 / 插件弹窗 / 候选条）共用 `ui/frameless_panel.py` 的同一套笔法，各自只传几何与配色。

> 这是全项目重复度风险最高、也最容易分叉的部分——抽出 `frameless_panel` 之前四处各抄一份，改一处漏三处。

### 6.2 主题系统（令牌 → QSS 单向驱动）

```
配置 theme: "light" | "dark" | "system"
        │
        ▼ resolve_theme_name()  ← system 走注册表 AppsUseLightTheme 解析
   Theme(name)                ← ui/win11_theme.py：一个类持有全部颜色令牌
        ├─ .style_sheet()      → app.setStyleSheet(...)  全局 QSS
        ├─ .menu_style()       → 无父 QMenu（托盘菜单不继承 app QSS）
        └─ .hex(key)           → paintEvent 里取色（必须走 parse_color，见下）
```

**两条硬规则**（git 历史里各踩过 2~3 次）：

1. **带自身 `setStyleSheet` 的控件会切断上层 QSS 级联**——中间容器必须应用**完整** `Theme.style_sheet()` 再追加规则，否则内部按钮会"降级"成原生样式（浅色下白底白字）；
2. **`QColor("rgba(r,g,b,0.88)")` 在 Qt6 里是 invalid，静默变黑**——paintEvent 取色一律走 `win11_theme.parse_color()`（正则兜底，alpha≤1 时 ×255）。曾导致日志窗"永远是深色"、插件弹窗整窗发黑。

主题切换广播：`UnifiedWindow.apply_config` 遍历 `self._pages.values()` 对 `hasattr(page, "set_theme")` 的页面广播——**不硬编码页面列表**，新增页面自动覆盖。

### 6.3 页面栈动画（`ui/animated_stack.py`）

`AnimatedStackedWidget.slide_to(page, duration, rise)`：切页时旧页**直接隐藏**，新页只做一次轻微上浮（`pos` 动画，不透明度恒为 1）。**全程无 `QGraphicsOpacityEffect`、无 `setStyleSheet`**——因为在半透明窗体上，任何 opacity<1 的瞬间都会透出背后的桌面（曾出现的"切页闪桌面"），而 `QGraphicsOpacityEffect` 每帧离屏渲染会让大页面卡成幻灯片。`_busy` 标志防重入。

### 6.4 三种注入方式（`core/injector.py`）

| 方式 | 原理 | 优点 | 限制 | 门控 |
|------|------|------|------|------|
| `direct`（默认） | `PostMessageW(WM_CHAR)` 逐字符投递到焦点控件 | 不走键盘、不走剪贴板、不经 IME，从根上杜绝乱码 | 依赖目标控件处理 `WM_CHAR`；失败自动退回剪贴板 | 始终可用 |
| `clipboard` | 写剪贴板 → 发 `Ctrl+V` → 恢复原剪贴板 | 兼容性最好 | 会短暂占用剪贴板（有快照/恢复机制） | 测试功能 |
| `type` | pynput 逐字符模拟键入，**先切英文键盘布局并轮询确认生效**再打，打完异步切回 | 不碰剪贴板 | 中文 IME 下易出乱码 | 测试功能 |

`type` 模式的竞态是典型坑：不轮询确认布局生效就开打，会被微软拼音吞成「哦哦」；打完也不能立刻切回布局，要给尾部键事件留"落字"时间——且必须用 `QTimer.singleShot` 异步推迟，**绝不在主线程 sleep**。

### 6.5 UIAccess 提权（`core/uia_elevation.py`，可选/测试功能）

目的：让候选条成为 `UIAccess` 顶级窗口，能与屏幕键盘等更高 Z 序窗口互相覆盖（否则会被压在下面看不到）。

```
管理员权限?
   └─ 是 → 找同会话 winlogon.exe(SYSTEM)
            ├─ AdjustTokenPrivileges 启用 SeDebugPrivilege（必须先做，否则 ACCESS_DENIED）
            ├─ OpenProcessToken + DuplicateTokenEx
            ├─ SetTokenInformation(TokenUIAccess=1)   ← TokenUIAccess 常量易错
            └─ CreateProcessAsUserW(..., CREATE_UNICODE_ENVIRONMENT) 重启自身
   └─ 否 → 返回 status{needs_admin: True} + 弹窗告知
```

**UIAccess 是进程级令牌属性，运行中改不了**——开关切换只能重启自身。`has_uiaccess()` 用于在设置页展示"✓ 已生效 / ⚠ 已开启但未生效"的真实状态，而不是只看配置位。

### 6.6 插件系统（`core/plugins.py`，测试功能）

**形态**：`<plugins_dir>/<plugin_id>/{plugin.json, main.py}`，入口必须提供 `setup(api)`。

**能力面（API v2）**：

| 类别 | API | 说明 |
|------|-----|------|
| 数据源 | `add_source(category, items)` | 合并进 `KaomojiData.external` 内存视图；**永不落盘**，不污染 `kaomoji.json` |
| 输出转换 | `register_transform(fn, priority)` | 注入前改写文本，priority 小者先执行 |
| 事件 | `on(event, fn)` | `startup` / `shutdown` / `before_insert` / `after_insert` / `config_changed` |
| 配置 | `settings`（活字典）+ `save_settings()` / `get_config(key)` | 私有配置落 `plugin_settings/<id>.json` |
| UI（v2） | `register_settings_widget` / `register_page` / `show_dialog` / `show_message` / `ask` / `notify` / `theme_tokens` | 插件页内嵌面板、侧边栏独立页、主题化弹窗、托盘气泡 |
| 日志 | `log(msg)` | 写入全局 `LOG_BUFFER` |

**状态机**：`disabled` → `loaded` / `error`（并记录 `error_kind`：`manifest` 校验失败 vs `runtime` 执行抛异常，后者可重试）。

**四条健壮性约定**：

1. **异常隔离**——清单校验、入口执行、每次 transform/事件回调都 try/except，单插件故障只标记该插件 error，绝不阻断主流程；
2. **所有权分离**——manager 只登记 factory 与元信息，**绝不持有 QWidget**；widget 由宿主（PluginPage / UnifiedWindow / PluginDialog）创建与销毁，避免 core 层持 Qt 对象导致悬垂；
3. **路径约束**——`entry` 必须位于插件目录内（realpath 前缀校验），防 manifest 写 `..` 越权加载；
4. **信任模型**——插件在同进程内以全权限执行（等同 VS Code / 浏览器扩展），约定 setup/transform 必须毫秒级返回。

### 6.7 「测试功能」门控（渐进交付的核心机制）

新功能默认进入测试模式，三层门控缺一不可：

| 层次 | 机制 | 位置 |
|------|------|------|
| UI 层 | `_add_row(..., test_feature=True)` 登记 → 按 `use_test_features` 整体显隐（连分区标题一起隐藏） | `settings_page` |
| 导航层 | 插件页 / 开发者页仅在 `use_test_features` / `developer_mode` 为真时创建 | `unified_window` |
| **落盘层** | `enforce_test_feature_gate()` 在 `save_config` 前把测试项强制归位默认值 | `config_store` |

第三层是关键：只隐藏 UI 拦不住功能——配置位仍为真时功能代码照样生效（"测试功能逃逸"）。放在保存前统一执行，可覆盖设置页保存、开发者页开关等**所有**写入路径。

当前测试功能项：`use_uia_elevation`（UIA 提权）、`input_method ∈ {clipboard, type}`（非 direct 的注入方式）、`acrylic`（亚克力）、插件系统。

### 6.8 版本与更新

```
core/version.py
  ├─ 源码态（sys.frozen 为假）→ git rev-parse --short HEAD（带 CREATE_NO_WINDOW，防黑框）
  └─ 打包态（frozen）        → core/_build_version.py（build.py 烘焙：BUILD_COMMIT/VERSION/NOTES）
     进程内缓存 _META_CACHE，避免启动期反复调 git 弹 3 次黑框
     显示格式：有标签 → v1.2(bdb300a)；无标签 → bdb300a
```

关于页「更新与下载」：`ReleasesAPI`（`QNetworkAccessManager`，异步非阻塞）拉 `owoflying/kaomoji-assistant` 的 `/releases/latest` → `parse_release()` 纯函数解析 → 下载到 Downloads → **后台 `HashWorker` 线程算 SHA-256 并与资产 `digest` 比对**（不匹配则删文件报错）。下载进度用资产真实 `size` 作分母（GitHub 302 到 CDN 后 `downloadProgress.total` 不可靠）。

---

## 7. 状态与数据资产

### 7.1 数据存储矩阵

| 数据 | 源码态路径 | 打包态路径 | 读写方 |
|------|-----------|-----------|--------|
| 内置颜文字库 | `data/kaomoji.json` | `_internal/data/kaomoji.json` | **只读**，`KaomojiData` |
| 主配置 | `config.json` | `%APPDATA%/KaomojiAssistant/config.json` | `config_store` |
| 用户颜文字 | `data/user_kaomoji.json` | `%APPDATA%/.../user_kaomoji.json` | `UserKaomoji` |
| 快捷短语 | `data/user_triggers.json` | `%APPDATA%/.../user_triggers.json` | `UserTriggers` |
| 最近/收藏/统计 | `data/user_state.json` | `%APPDATA%/.../user_state.json` | `UserState` |
| 插件本体 | `plugins/<id>/` | `%APPDATA%/.../plugins/<id>/` | `PluginManager` |
| 插件配置 | `data/plugin_settings/<id>.json` | `%APPDATA%/.../plugin_settings/` | `PluginManager` |
| 已见版本 | — | `%APPDATA%/.../last_seen_version.txt` | `version` |

**只读资源区与可写数据区严格分离**——因为程序可能装在 `C:\Program Files`（普通用户无写权限），且源码态与冻结态曾因混用产生两份配置。

### 7.2 内存中的权威对象（`AppRuntime` 持有）

| 属性 | 类型 | 职责 | 谁订阅它 |
|------|------|------|---------|
| `config` | `dict` | 权威配置（**原地更新，身份不变**） | 全项目传引用 |
| `data` | `KaomojiData` | 内置库 + 插件数据内存合并视图 | picker / 各页 |
| `plugins` | `PluginManager` | 插件宿主 | 库页 / 插件页 / 主面板 |
| `user_kao` | `UserKaomoji` | 用户颜文字（`changed` 信号） | picker / 主面板 |
| `triggers` | `UserTriggers` | 快捷短语 | monitor / 主页 |
| `state` | `UserState` | 最近/收藏/统计（带白名单自净） | picker / 主页 / 关于页 |
| `injector` | `KaomojiInjector` | 三种注入 | `on_selected` |
| `hotkey` | `NativeHotkeyManager` | 全局热键 | picker.toggle |
| `monitor` | `EmotionMonitor` | 打字监听 | picker / dev 页 |
| `window` | `PickerWindow` | 悬浮候选条 | 热键/监听/托盘 |
| `unified` | `UnifiedSettingsWindow` | 统一主面板 | 托盘/通知 |
| `bus` | `DevEventBus` | 开发者事件总线 | dev 页 |
| `tray` / `menu` | Qt 托盘 | 常驻入口 | — |

### 7.3 信号接线总表（`AppRuntime._wire_components`）

| 发出方 | 信号 | 接收方 |
|--------|------|--------|
| `hotkey` | `hotkey_pressed` | `window.toggle` |
| `monitor` | `emotion_detected(str)` | `window.show_for_emotion` |
| `monitor` | `trigger_detected(str,str,bool)` | `window.show_for_output` |
| `monitor` | `diagnostic(str)` | `bus.log`（开发者页） |
| `window` | `isVisibleChanged(bool)` | `_on_picker_visibility` → monitor pause/resume |
| `window` | `emotion_shown(str)` | `monitor.notify_shown`（锁定情绪） |
| `window` | `selected(str, ctx)` | **`on_selected`** |
| `window` | `settings_requested` | `open_unified("settings")` |
| `unified` | `config_applied(dict)` | **`apply_settings`** |
| `unified` | `finished` | `resume_global_hooks` |
| `unified` | `output_selected(str)` | `_on_unified_output` → `on_selected` |
| `settings_page` | `opacity_preview(float)` | `_preview_picker_opacity`（不落盘） |
| `settings_page` | `hotkey_capture_changed(bool)` | 录制期间临时停用热键 |
| `user_kao` / `plugins` | `changed` | 刷新白名单 / 刷新数据源 |
| `plugins` | `ui_changed` | `UnifiedWindow._sync_plugin_pages` |
| `app` | `aboutToQuit` | 热键/监听/候选条/各 store flush/插件 shutdown/关弹窗 |

---

## 8. 线程模型

```
┌─ Qt 主线程 ────────────────────────────────────────────────┐
│  全部 QWidget、QSS、注入调用、插件回调、配置落盘              │
│  ⚠ 插件回调在此同步执行（约定毫秒级返回，无超时保护）          │
└────────────────────────────────────────────────────────────┘
      ▲ 跨线程 Signal 自动排队投递
      │
┌─ emotion-sampler (daemon) ─┐  ┌─ UIA worker (常驻单线程) ─┐
│ 打字监听采样、情绪判定       │  │ COM 实例按线程缓存        │
│ 每轮新建 Event，stop 时 join │  │ 任务队列 + join(timeout)  │
└────────────────────────────┘  └───────────────────────────┘
┌─ pynput Listener（系统钩子回调）─┐  ┌─ HashWorker(QThread) ─┐
│ 只记字符+置事件，微秒级返回       │  │ 下载后 SHA-256 校验    │
└──────────────────────────────────┘  └───────────────────────┘
```

**三条铁律**（都对应过真实故障）：

1. **重活绝不进钩子**——键盘/鼠标钩子回调里做任何 COM/UIA/磁盘操作，都会让全系统打字变卡；
2. **重活绝不进主线程**——UIA 同步调用在目标程序卡死时**无限挂起**，必须独立线程 + `join(timeout)` + 超时回退（文本→`''`、可编辑→`True`、矩形→`None`）；
3. **线程生命周期要显式收尾**——每轮 `start` 用**全新** `Event` 并作为参数传给线程；复用同一个 Event 会让上一轮线程"复活"，造成两条采样线程并存、同一次按键被判定两遍。

---

## 9. 构建与发布

```
build.py
  ├─ 1. core.app_icon.save_ico()     程序化生成 app.ico（白底圆角 + (◕‿◕)）
  ├─ 2. 交互模式（无参数）：提示输入版本标签 / 更新说明（支持多行、@文件），可全空做开发构建
  │     有参数：argparse 直接构建
  ├─ 3. 写 core/_build_version.py    烘焙 BUILD_COMMIT / BUILD_VERSION / BUILD_NOTES
  ├─ 4. PyInstaller --onedir --windowed
  │     --add-data "data;<目标>data"        ⚠ 目标必须是目录，写文件名会嵌深一层
  │     --add-data "ui/fonts;<目标>ui/fonts"   否则图标字体注册失败 → 图标变豆腐块
  │     --hidden-import pynput.keyboard._win32 / pynput.mouse._win32 / pynput._util.win32
  └─ 5. finally: os.remove(core/_build_version.py)   ← 不污染源码态版本号
```

产物：`dist/KaomojiAssistant/{KaomojiAssistant.exe, _internal/...}`。

**自检与排障**：`KaomojiAssistant.exe --diag` 会把图标字体加载情况、字形覆盖、运行环境写到 exe 同目录 `diag.txt` 后退出（窗口程序没有控制台，这是唯一的现场证据）。运行时日志经 `qInstallMessageHandler` 收进 `LOG_BUFFER`（环形 2000 条，白名单丢弃 `OpenType support missing` 等噪声），托盘「查看日志」（仅开发者模式可见）可查看/复制/保存。

---

## 10. 架构评价：做得好的地方

| 维度 | 体现 |
|------|------|
| **入口极薄** | `main.py` 54 行只做装配交接；曾有 429 行 `main()` 装 40+ 个闭包与局部变量（漏写一个 `global` 就让后续初始化整段被跳过），重构为 `AppRuntime` 类后每个环节单独可测 |
| **单向数据流** | 配置只有 `apply_settings` 一个落盘口，注入只有 `on_selected` 一个出口，版本只有 `get_app_version` 一个来源 |
| **平台层收敛** | 所有 ctypes/COM 集中在 `win_utils` / `uia_text` / `uia_elevation` 三个文件，上层写的是普通函数调用 |
| **页面横向解耦** | 7 个业务页互不 import，只经信号；主题广播用 `hasattr` 遍历而非硬编码列表 |
| **渐进交付机制** | 三层测试门控（UI 隐藏 + 导航不建 + **落盘归位**），新功能默认隔离 |
| **插件权限边界清晰** | manager 只登记不持 widget、异常全隔离、路径前缀校验、数据不落盘 |
| **踩坑知识沉淀** | `docs/PITFALLS.md` 记录 19 类根因（ctypes 常量、QSS 级联、亚克力 alpha 语义、线程复活、插件生命周期…）+ 症状索引表 + 提交前自检清单 |
| **分层可被强制执行** | `tests/test_plugins.py::test_core_does_not_import_ui` 静态扫描 `core/`，任何人加回反向依赖都会立刻失败 |
| **测试隔离** | `tests/conftest.py` 把 `APPDATA` 重定向到临时目录；100 个用例跑完后工作区零污染 |

---

## 11. 已修复的架构缺陷

下表是首轮梳理时发现的问题，**现已全部修复并有用例守护**：

| # | 原问题 | 修复方式 | 守护 |
|---|--------|---------|------|
| 1 | `core → ui` 反向依赖（`plugins.py` import `ui.plugin_dialog`/`ui.win11_theme`） | 新增 `core/ui_hooks.py` 声明协议（`UiHooks`/`NullUiHooks`），`ui/plugin_ui_hooks.py` 提供实现，`app_runtime` 注入 | `test_core_does_not_import_ui` 静态扫描 + `test_null_ui_hooks_degrade_gracefully` |
| 2 | `unified_window.py` 1172 行、职责过多 | 拆出 `ui/frameless_shell.py`（窗口机械）与 `ui/unified_nav.py`（导航调度），原文件降至 559 行 | `test_unified_window.py` 覆盖拆分边界 |
| 3 | 插件回调无保护，卡界面难定位 | `PluginManager._timed_call` 对超 50ms 的回调记 warn（带插件 id 与耗时） | `test_slow_transform_emits_warning` |
| 4 | 无 `tests/` 目录 | 建 `tests/`，100 个离屏用例（配置/插件/情绪/数据/窗口） | 本身即守护 |
| 5 | 文档设计与经验混杂（49 KB 单文件） | 拆为 `docs/ARCHITECTURE.md`（设计）+ `docs/PITFALLS.md`（踩坑）+ 根 `ARCHITECTURE.md`（索引） | — |
| 6 | 配置无 schema 校验 | `config_store.validate_config()` 校验类型/枚举/范围并回退默认；未依赖默认值的键保留；数值项拒绝 bool | `test_config_store.py`（15 个用例） |
| 7 | 缺 `.gitattributes` | 新增：仓库存 LF、`.bat` 用 CRLF、二进制文件禁止转换 | — |

## 12. 仍可改进

| # | 问题 | 建议 |
|---|------|------|
| 1 | `plugins.py` 仍有 842 行、`picker_window.py` 794 行 | `plugins.py` 可再拆 `plugin_manifest` / `plugin_api` / `plugin_manager`；`picker_window` 可拆出定位与状态机 |
| 2 | `win_utils.py` 557 行 | 按"热键解析 / 焦点控件 / 键盘布局"三块拆 |
| 3 | 插件回调只有耗时告警、无硬超时 | 劣质插件仍可卡住界面（信任模型取舍）；UI 扩展可在 `setup` 上加看门狗 |
| 4 | 无 CI | 建议 GitHub Actions（Windows runner + offscreen 即可跑 `pytest`） |
| 5 | `build.py` 无自动化冒烟 | 可在构建后跑一次 `--diag` 并断言 `diag.txt` 无字体错误 |

---

## 12. 新手阅读路线（建议顺序）

```
1. README.md                    看它到底做什么
2. main.py（54 行）              看入口多薄
3. app_runtime.py 的 _build()    看装配顺序，这是全项目的骨架
4. app_runtime.on_selected()    看注入出口的 7 个步骤
5. core/emotion_monitor.py      看"打字→命中"的完整链路与三重保险
6. ui/picker_window.py 的
   _present / show_for_emotion  看候选条如何弹出与定位
7. core/config_store.py         看配置与测试门控
8. core/plugins.py 头部文档      看插件 API 全貌
9. ui/unified_window.py         看外壳如何与导航/页面组合（559 行）
10. ui/frameless_shell.py       看无边框窗口的 Win32 机械逻辑
11. ui/unified_nav.py           看页面增删与栈调度
```

### 修改前的自检

完整清单见 [`docs/PITFALLS.md` §10](docs/PITFALLS.md)。最要紧的三条：

1. **要动 ctypes？** 先用 `ctypes.windll` 实跑验证常量与 `GetLastError`，不要凭记忆（历史上有 `TokenSessionId=12`、`EnumProcesses` 首参、`CloseHandle` 归属三处静默失效）。
2. **要写 `setStyleSheet`？** 想想它会不会切断上层 QSS 级联；要自定义中间容器，就垫上**完整** `Theme.style_sheet()` 再追加。
3. **删了 import？** `py_compile` 和 `import` 都不够——必须离屏把相关窗口真正构造出来并调用 `apply_config` / `set_theme`，才能暴露类方法里的 `NameError`。

### 提交前跑什么

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

跑完请确认 `git status` 里**没有**多出 `config.json` 的改动——测试会真实落盘，
所以 `conftest.py` 已把 `APPDATA` 指向临时目录；若出现改动，说明有用例绕过了隔离。
