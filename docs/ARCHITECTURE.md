# 架构设计（ARCHITECTURE）

> kaomoji-assistant · Win11 风格颜文字输入辅助器
> 本文只讲**结构与决策**。所有"看起来能用实则运行时失效"的坑见
> [`docs/PITFALLS.md`](./PITFALLS.md)；当前代码的完整梳理见
> [`PROJECT_ARCHITECTURE.md`](../PROJECT_ARCHITECTURE.md)。

---

## 1. 定位

常驻托盘的小工具：在**任意程序**里打字时，按热键 / 情绪关键词 / 快捷短语弹出
悬浮候选条，选中后把颜文字注入回原光标位置 —— 不抢焦点、不改输入法状态。

设计目标：**无侵入**、**系统级一致**（Win11 观感）、**稳健**（目标程序卡死不能拖死
本程序）、**渐进交付**（新功能默认在测试模式）。

---

## 2. 技术选型

| 维度 | 选型 | 理由 |
|------|------|------|
| GUI | **PySide6 6.11.x** | 自绘能力 + DWM 集成 + 自带异步网络 |
| 全局输入 | **pynput**（采样）+ Win32 `RegisterHotKey`（注册） | 注册与抑制语义分离，各用所长 |
| 文本读取 | **UI Automation (COM)**，纯 ctypes 手写 | 跨进程读焦点控件文本的唯一可靠手段，零第三方依赖 |
| 注入 | 剪贴板 / `WM_CHAR` 直投 / 模拟键入 | 覆盖"兼容性 / 不碰剪贴板 / 不碰输入法"三种诉求 |
| 配置 | JSON | 人可读、可手改（因此需要类型校验，见 §6） |
| 打包 | **PyInstaller onedir --windowed** | 托盘程序无控制台；onedir 便于替换资源 |

依赖刻意很少：无 requests / numpy / ML 库。情绪识别是纯关键词匹配。

---

## 3. 分层

```
main.py            入口薄壳（54 行）：装日志钩子 → QApplication → AppRuntime
      │
app_runtime.py     ★ 装配层：组件创建、信号接线、配置应用、注入编排、托盘
      │
      ├─ ui/       呈现层：unified_window（外壳+导航）、picker_window、pages/*
      │            依赖方向只能是 ui → core
      │
      ├─ core/     业务层：emotion_monitor、injector、plugins、hotkey、user_*
      │            Qt 只出现在少数文件（plugins/hotkey 的 QObject 信号）
      │            ⚠ 不 import ui.*（由 tests/test_plugins.py 强制）
      │
      ├─ 平台抽象   core/win_utils、core/uia_text、core/uia_elevation
      │            所有 ctypes/COM 收敛于此，上层只见普通函数
      │
      └─ 基础层     core/runtime（路径）、config_store、applog、version
                   无 Qt 依赖、可无界面单测
```

### 3.1 `ui/` 内部再分层

| 层 | 模块 | 职责 |
|----|------|------|
| 外壳 | `frameless_shell.py` | 无边框窗口机械逻辑：圆角遮罩、八向缩放、最大化贴工作区、隐藏重开状态 |
| 调度 | `unified_nav.py` | 侧边栏导航、页面栈切换、开发者页/插件页增删 |
| 组装 | `unified_window.py` | 搭骨架 + 建页 + 主题广播（组合层，不再塞细节） |
| 独立窗口 | `picker_window.py`、`log_viewer.py`、`plugin_dialog.py` | 各自复用 `frameless_panel` 笔法 |
| 通用件 | `win11_theme`、`frameless_panel`、`fluent_*`、`toggle_switch`、`animated_stack` | 从 `Theme` 取色，禁止硬编码 |
| 业务页 | `pages/*` | 页面之间**互不 import**，只经信号通信 |

> **拆分的收益**：`unified_window.py` 从 1172 行降到约 500 行；窗口机械逻辑与
> 导航调度可各自单测；新增页面只需一行 `NAV_ITEMS` + 一次 `register_page()`。

---

## 4. 数据流

### 4.1 三条唤起链路 → 唯一注入出口

```
① 全局热键 hotkey ──┐
② 打字监听 emotion_monitor ──┤──► PickerWindow ──► selected(text, ctx)
    ├ 情绪关键词命中         │     候选条
    └ 快捷短语命中           │
③ 主面板搜索/库页 ───────────┘
                              │
                              ▼
                  AppRuntime.on_selected()   ★ 唯一注入出口
                  1. plugins.run_transforms   插件改写
                  2. monitor.notify_injected  告知"这是自己写的"
                  3. emit before_insert
                  4. delete_trigger?          可选：安全删触发词
                  5. injector.inject(method)
                  6. emit after_insert
                  7. state.record_usage       统计
```

**必须收敛到一个出口**：注入前要告知监听器（否则自激重弹）、插件要拿上屏前后事件、
统计要覆盖所有来源。任何绕过 `on_selected` 的旁路都会破坏这三条不变式。

### 4.2 配置变更：单向、唯一落盘口

```
各页 config_applied(dict)
      ▼
UnifiedSettingsWindow → AppRuntime.apply_settings(new_cfg)
      ├─ config.clear()+update()     原地更新（dict 身份不变，plugins 持有引用）
      ├─ diff 出 changed             只对真正变化的键动作
      ├─ save_config → 门禁归位 → 写盘
      ├─ 主题变化 → 重贴 QSS（幂等短路）+ 日志窗换肤 + 重挂轮询
      ├─ 热键变化 → 重新 RegisterHotKey
      └─ auto_popup 变化 → 启停监听
```

### 4.3 打字监听

```
pynput 钩子（只记字符 + 置事件，微秒级）
      ▼
emotion-sampler 线程（wait 阻塞，无输入零 CPU）
      ├ 静默期检查 → 驻留轮询（连采 6 次 / 60ms，覆盖 IME 提交滞后）
      ├ 读取：原生编辑框 WM_GETTEXT → UIA(0.2s 超时) → 字符缓冲
      ├ 剔除已注入文本
      ├ 只看尾部 30 字符
      └ 触发词优先 → 否则 detect_last（取最靠右命中）
      ▼
emotion_detected / trigger_detected 信号
```

### 4.4 候选条交互

候选条**不抢焦点**（只 `raise_()`），选字靠低层键盘钩子：

| 键类 | 行为 |
|------|------|
| A：`1-9` / `-=` / 方向键 / `Esc` | 面板开着就吞 |
| B：`Enter` / `Space` | 仅用户已表达选字意图时吞 |
| C：其它键 | 放行 + 关面板（"继续打字即关闭"） |
| 修饰键按下 | 一律不吞 |

再叠加鼠标钩子（点外部即关）+ 350ms 焦点轮询守卫。

---

## 5. 关键机制

### 5.1 插件系统（测试功能）

`<plugins_dir>/<id>/{plugin.json, main.py}`，入口 `setup(api)`。

| 能力 | API | 说明 |
|------|-----|------|
| 数据源 | `add_source` | 只进 `KaomojiData.external` 内存视图，**永不落盘** |
| 输出转换 | `register_transform` | 注入前改写，priority 小者先 |
| 事件 | `on` | startup / shutdown / before_insert / after_insert / config_changed |
| 配置 | `settings` / `save_settings` / `get_config` | 私有配置落 `plugin_settings/<id>.json` |
| UI (v2) | `register_settings_widget` / `register_page` / `show_dialog` / `notify` / `theme_tokens` | 需清单声明 `api_version >= 2` |

**四条约定**：异常隔离（单插件故障只标记自己 error）、所有权分离（manager 不持 QWidget）、
路径约束（`entry` 必须在校验过的插件目录内）、数据不落盘。

### 5.2 UI 钩子分层

插件需要弹窗/消息框/主题令牌，但这些是 `ui/` 的实现。原先 `core/plugins.py`
在函数内延迟 `from ui.plugin_dialog import ...` —— 虽避开循环依赖，
但 `core → ui` 的**依赖方向**仍然违反分层。

现在的做法：

```
core/ui_hooks.py      声明协议：NullUiHooks（安全空实现）/ UiHooks（回调集合）
ui/plugin_ui_hooks.py 实现协议：build_ui_hooks() 组装真实 Qt 实现
app_runtime._build_core  注入：PluginManager(..., ui_hooks=build_ui_hooks(...))
```

好处：core 拿不到实现时（离屏单测、纯 core 使用）自动降级为
"弹窗返回 None、消息框返回 False、令牌返回空 dict"，插件代码无需判断宿主是否存在 UI；
分层由 `tests/test_plugins.py::test_core_does_not_import_ui` 静态扫描强制。

### 5.2b 通用界面修改 API（`api.ui`）

**需求**：插件要能自由修改内置界面（含其他插件的界面），而不是只有固定的
"加一页/加一张卡"槽位。

**三个模块**：

```
core/ui_registry.py    元素登记 + 双轨寻址（手工 id / 自动路径）
core/ui_mutation.py    修改记录与回滚（UiMutator / MutatorHub）
core/plugin_ui.py      插件侧门面（PluginUi -> api.ui）
```

**双轨寻址**（关键设计）：

| 轨道 | 形式 | 优点 | 代价 |
|------|------|------|------|
| 手工 id | `home.greeting.title` | 稳定，宿主重构不碎 | 只能触达主动登记的元素 |
| 自动路径 | `home/GreetingCard[0]/QLabel[1]` | 能触达任何控件 | 布局层级一变就失配 |

之所以需要两轨：实测主页里 `objectName` 严重重复（`Card` × 12、`BodyText` × 8），
光靠名字定位不到唯一节点；而全部手工登记又会让 id 表变成控件树的镜像，失去契约意义。

**回滚机制**：所有修改都经过 `UiMutator`，每条记录带一个 `undo()` 闭包：

```
读原值 -> 记录 -> 施加修改
回滚 = 倒序执行所有 undo
```

`PluginManager._detach` 里调 `MutatorHub.rollback(plugin_id)`，
所以插件停用/卸载/重载都会自动还原。四类修改各有还原策略：

| 类型 | 还原方式 |
|------|---------|
| 属性（文字/样式/可见/属性） | 写回记录的原值 |
| 插入控件 | 摘出 + 销毁 |
| 移除控件 | 按原索引插回（不销毁，只是隐藏） |
| 替换渲染 | 按原顺序重建布局项（控件/嵌套布局/spacer 分别还原） |
| 信号绑定 | `disconnect` |

**权限模型**：默认允许修改任意元素（含跨插件），但每次修改都记审计日志，
跨插件标注「（跨插件）」。这是刻意取舍——插件是同进程全权限代码，
与其假装隔离，不如**把修改全部记下来、可回滚、可审计**。

**时序重放**（关键坑）：装配顺序是 `_build_core`（加载插件、执行 `setup`）
先于 `_build_windows`（建窗口、登记元素）。插件在 `setup()` 里调
`api.ui.set_text(...)` 时元素表还是空的，会静默失败。

解法：`UiRegistry` 有 `ready` 标志与 `when_ready(fn)` 队列；
`PluginUi` 的**修改类**方法在未就绪时排队，窗口登记完元素后
`mark_ready()` 统一重放。插件作者无需关心时序。

代价：**读取类方法不排队**（需要立即返回值），所以"先读原值再改"必须放进
`startup` 事件。这一点在 `docs/PLUGIN_GUIDE.md` §3.9 有明确说明。

**崩溃回滚**：插件回调（transform / 事件）抛异常时，`_timed_call` 会调
`_rollback_on_crash()` 回滚该插件的全部界面修改，但**不禁用插件**——
一次回调异常不该让整个插件下线。`setup()` 崩溃则走加载失败路径
（本来就会 `_detach` + 回滚）。

**元素 id 契约**：`core/ui_ids.py` 集中定义 id 常量与登记函数。
已发布的 id 不应改名（等于破坏 API），控件重构时若必须删除，
应先标记 `@deprecated` 并保留一个版本。

### 5.3 测试功能门控（三层）

| 层 | 机制 |
|----|------|
| UI | `_add_row(..., test_feature=True)` 按 `use_test_features` 整段显隐 |
| 导航 | 插件页/开发者页仅在对应开关为真时创建 |
| **落盘** | `enforce_test_feature_gate()` 在 `save_config` 前把测试项归位默认 |

第三层是关键：只隐藏 UI 拦不住功能（"测试功能逃逸"）。

### 5.4 主题与视觉

令牌集中在 `ui/win11_theme.py::Theme`，`Theme(name).style_sheet()` 生成全局 QSS 由
`app.setStyleSheet` 统一驱动；`menu_style()` 单独覆盖无父 `QMenu`。
四个自绘窗口共用 `ui/frameless_panel.py` 的笔法（阴影+基底+渐变+描边）。

---

## 6. 配置校验

`config_store.validate_config(raw) -> (清洗后 dict, 问题列表)` 是纯函数：

| 规则 | 处理 |
|------|------|
| 未知键 | 保留（向前兼容） |
| 类型不符 | 回退默认（**bool 不与 int 混用**） |
| 数值项 int/float 互通 | 允许 `"opacity": 1`；拒绝 `"opacity": true` |
| 枚举越界（theme/input_method） | 回退默认 |
| 范围越界（opacity/panel_alpha 等） | 回退默认 |
| 顶层不是对象 | 整体回退默认 |

`load_config()` 校验后用标准 `logging` 记录被回退的项 —— 配置层刻意不依赖 Qt，
不能 import `applog`（它依赖 PySide6）。

---

## 7. 线程模型

```
Qt 主线程            所有 QWidget、注入调用、插件回调、配置落盘
                     ⚠ 插件回调同步执行，超 50ms 记 warn
emotion-sampler      打字采样、情绪判定（daemon）
UIA worker           常驻单线程 + 任务队列（COM 按线程缓存）
pynput Listener      系统钩子回调（微秒级返回）
HashWorker(QThread)  下载后 SHA-256 校验
```

三条铁律：**重活不进钩子**、**重活不进主线程**、**线程生命周期显式收尾**。

---

## 8. 数据资产

| 数据 | 源码态 | 打包态 |
|------|--------|--------|
| 内置库（只读） | `data/kaomoji.json` | `_internal/data/kaomoji.json` |
| 配置 | `config.json` | `%APPDATA%/KaomojiAssistant/config.json` |
| 用户颜文字 / 短语 / 状态 | `data/user_*.json` | `%APPDATA%/.../` |
| 插件本体与私有配置 | `plugins/` · `data/plugin_settings/` | `%APPDATA%/.../plugins` · `plugin_settings/` |

**只读资源区与可写数据区严格分离**（程序可能装在 `C:\Program Files`）。

---

## 9. 测试

```bat
.venv\Scripts\python.exe -m pytest tests -q      :: 148 个用例，约 65s
```

- 全部走 `QT_QPA_PLATFORM=offscreen`，无需显示器；
- `tests/conftest.py` 把 `APPDATA` 重定向到临时目录，**绝不动工作区的 config.json**；
- 覆盖：配置校验与门禁、情绪识别、数据 store 的脏数据容错、插件生命周期与异常隔离、
  分层不变式（core 不 import ui）、统一窗口的拆分边界与主题链路、
  **`api.ui` 全套能力与回滚**（`test_ui_api.py`）、
  **正面示例插件含时序重放**（`test_ui_tweaks_example.py`）、
  **游离顶层窗口回归**（`test_stray_windows.py`）。

> ⚠ 测试必须覆盖**真实事件循环**。历史上出现过"9 个用例全绿、真机却看不到卡片"——
> 因为那些断言只查构造期状态，而撤销锚点页引发的连锁销毁要等 `processEvents()`
> 之后才发生。现在 `test_ui_api.py` 与 `test_home_greeting_plugin.py` 都会
> 显式 `processEvents()` 并走 `show()` 路径。

---

## 10. 开发流程

1. **新功能默认进测试模式**：`DEFAULT_CONFIG` 加开关 → `TEST_FEATURE_DEFAULTS` 加归位项
   → 设置页 `test_feature=True` 登记 → 入口先读开关，关闭时零副作用。
2. **编码规范**：自绘控件统一从 `Theme` 取色；ctypes 常量实跑核对；
   跨页状态走信号链，不依赖共享字典"恰好没被替换"。
3. **验证**：离屏构造 + 断言，而非 `py_compile`。
4. **提交**：conventional commits，按逻辑分组。
5. **发布**：`build.py`（交互式填版本标签与更新说明）→ GitHub Web UI 发 Release。

---

## 11. 已知改进点

| 问题 | 建议 |
|------|------|
| `plugins.py` 仍有 ~800 行 | 可再拆 `plugin_manifest` / `plugin_api` / `plugin_manager` |
| `win_utils.py` 557 行 | 可按"热键解析 / 焦点控件 / 键盘布局"三块拆 |
| 插件回调无超时 | 目前只有 50ms 耗时告警；UI 扩展可在 setup 上加看门狗 |
| 无 CI | 建议 GitHub Actions 跑 `pytest`（Windows runner + offscreen 即可） |
