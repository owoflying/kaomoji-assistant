# 踩坑手册（PITFALLS）

> kaomoji-assistant 的"血泪账本"。每条都对应一次真实的运行时故障：
> **根因 → 现象 → 修复**。设计说明见 `docs/ARCHITECTURE.md`。
> 新增条目请附上"怎么验证它真的修好了"，否则下一个人会重蹈覆辙。

## 阅读方式

- **正在写某块代码** → 按下面的分类跳到你碰的那一块；
- **遇到一个诡异现象** → 先查 §9 症状索引表，再顺藤摸瓜；
- **准备提交** → 过一遍 §10 提交前自检清单。

---

## 1. ctypes / Win32

### 1.1 凭记忆写常量 = 静默失效

| 常量/调用 | 错误写法 | 后果 |
|-----------|---------|------|
| `TokenSessionId` | 误用 **28**（实为 `TokenLogonSid`） | `ERROR_INSUFFICIENT_BUFFER`，UIA 提权静默失败 |
| `EnumProcesses` | 首参传 `byref(array)` | 拿不到进程列表，找不到 winlogon |
| `CloseHandle` | 从 `advapi32` 取 | `AttributeError` 被外层 `except` 吞掉 |
| `CreateProcessAsUserW` | `dwCreationFlags=0` | 传宽字符环境块却按 ANSI 解析 → `ERROR_INVALID_PARAMETER(0x57)`，令牌已生成但**重启永不发生** |
| `CreateProcessAsUserW` | 漏 `CREATE_UNICODE_ENVIRONMENT`(0x400) | 同上 |
| `OpenProcessToken(winlogon)` | 未先启用 `SeDebugPrivilege` | `ACCESS_DENIED`（该特权"拥有但禁用"） |

**正确做法**：所有 `ctypes` 调用显式设置 `argtypes`/`restype`，常量以
`ctypes.windll` **实跑并检查 `GetLastError`** 为准，不要凭记忆或抄博客。

```python
# ✅ 导入期就把签名钉死（多显示器下句柄超过 2^31 会被默认 c_int 截断）
u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
u.MonitorFromWindow.restype = ctypes.c_void_p
```

**验证**：离屏实跑 + 断言 `GetLastError()==0`。`py_compile` 与 `import` 都发现不了。

### 1.2 64 位句柄截断

ctypes 默认 `restype=c_int`。HWND/HMONITOR 超过 2^31 会被截断成负数——
**单显示器永远看不出问题**，多显示器/高句柄值时才表现为"最大化贴错屏幕"。
见 `ui/frameless_shell.py::_init_user32_signatures`。

### 1.3 UIAccess 是进程级令牌属性

- **运行中改不了**：只能另起进程（`SetTokenInformation` + `CreateProcessAsUser` 重启自身）。
  曾尝试 `NtSetInformationProcess` 给当前进程打补丁 —— 死路。
- **关闭也要重启去令牌**：否则窗口仍留在 `ZBID_UIACCESS` 高 Z 序带、一直置顶。
- **防无限重启守卫要按"目标"编码**（环境变量存 `"0"`/`"1"`），
  否则"先开→重启带 UIAccess，再关→需再重启去 UIAccess"会被上一次守卫误拦。
- **状态要反映真实 UIAccess 而非配置位**：用 `has_uiaccess()` 显示
  "✓ 已生效 / ⚠ 已开启但未生效"，否则会出现"重启后开关亮着却没生效"。

---

## 2. Qt / QSS

### 2.1 QSS 级联切割（踩过 3 次）

**规则**：控件一旦有自身 `setStyleSheet`，其**派生控件不再继承更上层祖先的样式**。

| 场景 | 错误做法 | 正确做法 |
|------|---------|---------|
| 内容区/中间容器要透明 | `setStyleSheet("background:transparent")` | 先应用**完整** `Theme.style_sheet()`，再追加自身规则 |
| `QScrollArea` 透明 | 同上 | 依赖全局 QSS 已有的规则，不要局部覆盖 |
| 插件卡片 QLabel 设颜色 | 只写 `color:xxx` | 连 `background`/`font-family` 一起写完整 |

**症状**：浅色模式下 `QPushButton#AccentButton` 白底白字、与卡片融为一体不可见。

### 2.2 `QColor("rgba(r,g,b,0.88)")` 在 Qt6 是 invalid，静默变黑

浮点 alpha **不认**，`QColor` 返回 invalid 颜色，绘制出来是纯黑。
踩过两次：① 插件弹窗整窗发黑；② 日志窗"永远是深色模式"。

**正确做法**：主题令牌取色一律走 `win11_theme.parse_color()`
（正则兜底，alpha≤1 时 ×255）。`FluentCheckBox` 的勾选框黑线同理。

### 2.3 自绘 `QAbstractButton` 的原生黑条

`ToggleSwitch` 在 hover/focus 时 Windows 会画原生黑条。

**修复**：在**全局 QSS** 里显式覆盖 `:hover`/`:focus` 为透明 + 让行容器
`QWidget#SettingsRow` 透明。只改控件自身属性无效（`WA_StyledBackground=False`
会让本地 `background:transparent` 失效）。

### 2.4 `setStyleSheet` 很贵：必须幂等短路

实测：`app.setStyleSheet()` ≈500ms（全量控件重新 polish），统一窗口
`setStyleSheet` ≈170ms + 内容区 ≈130ms，子页全量重贴 ≈50ms。

设置页任何改动都走 `apply_settings`，若无条件重贴，**拖一下滑块就白卡近一秒**。

**做法**：缓存上一份样式串，`ss == applied` 时直接 return
（见 `AppRuntime._apply_app_theme`、`UnifiedSettingsWindow._apply_theme`）。

### 2.5 页面切换动画：绝不能动透明度

主窗体是 `WA_TranslucentBackground`，**任何页面在 opacity<1 的瞬间都会透出背后的桌面**。

试过且都错的方案：
1. 新页 0→1 淡入 —— 双半透叠加透桌；
2. 旧页 `QGraphicsOpacityEffect` 淡出 —— 仍透桌，且该效果**每帧把整页渲染到离屏
   缓冲再合成**，大页面（关于页）直接卡成 PPT；
3. 动画期间 `setStyleSheet` 临时改背景 —— 触发整棵内容树（几百控件）样式重算，
   又一巨大卡顿源，且没真正消除透明度问题。

**最终方案**（`ui/animated_stack.py`）：旧页在 `setCurrentWidget` 时直接隐藏，
仅新页做一次 `pos` 上浮就位，**不透明度恒为 1**，全程无 `GraphicsEffect`、
无 `setStyleSheet`。`_busy` 标志防重入。

### 2.6 混合渲染：`font-family` 优先级高于 `setFont()`

QSS 的 `font-family` 会盖掉 `setFont()`。图标字体必须显式
`setStyleSheet("font-family:...")` 才生效。

另：`QRawFont.supportsCharacter` 对印度语系字符（ಠ/દ)返回**假阳性**，
判断字形是否可用只能"真画一遍比对像素"。

### 2.7 `setParent(None)` 会让控件变成顶层窗口并闪一下

Qt 语义：**没有父级的 QWidget 就是独立顶层窗口**。所以这段"销毁旧控件"的
常规写法会出问题：

```python
for old in self._cards:
    layout.removeWidget(old)   # 只移出布局，不隐藏
    old.setParent(None)        # ← 此刻 old 变成顶层窗口并显示出来
    old.deleteLater()          # 销毁排到下一轮事件循环
```

`removeWidget` 只把控件移出布局，**不改可见性**；紧接着 `setParent(None)`
让它失去父级、成为顶层窗口，而 `deleteLater()` 要等事件循环才执行 ——
中间这一帧控件真实地显示在屏幕上。

**症状**：启用/停用一个插件，屏幕上闪过好几个小窗口（卡片有几张就闪几个）。

**正确姿势**：`hide()` 必须在 `setParent(None)` **之前**。

```python
old.hide()                 # 关键
old.setParent(None)
old.deleteLater()
```

**已修位置**：`PluginPage.refresh`（重建卡片）、`PluginPage.build_settings`
（复用缓存面板）、`forget_settings` / `discard_settings`、
`NavController.drop_page`（移除页面）、`remove_nav_item`（移除导航项）、
`PickerWindow._rebuild_chips`（重建候选 chip）、`UiMutator.insert/remove/swap_widget`。

> 相关：`tests/test_stray_windows.py` 遍历真实操作路径，断言过程中可见顶层
> 窗口数量不增加 —— 以后任何"脱离父级的控件清理"写错，这套用例都会拦下。

### 2.8 `removeWidget` 会留下空布局项，清空容器要用 `takeAt`

想把一个容器的内容全部换掉时，这样写是错的：

```python
for i in range(lay.count()):
    w = lay.itemAt(i).widget()
    if w: lay.removeWidget(w)     # ❌ 只摘控件，布局项仍在
```

`removeWidget` 只把控件摘走，**布局项本身留在原地**（`itemAt` 之后返回
`None`，`count()` 不减少）。容器里会残留一排"看不见的占位"，
后续插入的控件位置和间距都会错乱。

**正确姿势**：用 `takeAt(0)` 循环，连布局项一起摘走。

```python
items = []
while lay.count():
    items.append(lay.takeAt(0))
```

回滚时按原顺序把控件、嵌套布局、spacer 分别还原（`addWidget` /
`addLayout` / `addStretch`）。

**踩到的位置**：`UiMutator.replace_render` 第一版就踩了这个 ——
测试断言"容器内容应只剩插件产物"时才发现 `itemAt(0)` 是 `None`。

---

## 3. 无边框窗口 / DWM

### 3.1 亚克力与不透明度语义

面板基底 alpha 必须**直接以 `panel_alpha` 为基底**：

```
亚克力开：a = max(0.6, min(1.0, 0.55 + 0.45 * panel_alpha))
亚克力关：a = max(0.4, min(1.0, panel_alpha))
```

**切勿再乘 `window_tint` 的固有 alpha（≤0.85）** —— 否则拉满 100% 面板仍半透，
且比关亚克力时**更透**（旧 bug）。

### 3.2 `QWindowsWindow::setGeometry: Unable to set geometry` 警告

- **瞬时出现**：最大化↔还原过渡一帧内自动恢复，良性噪音；
- **持续出现 + 窗口卡住无法缩小**：**真 bug**。

根因：无边框窗口在**隐藏态**调 `setWindowState(NoState)` 只清 Qt 侧状态、
**不清原生 `WS_MAXIMIZE`**。重新 `show()` 时原生仍带最大化样式 →
`setGeometry` 被钳到工作区，且 `WM_NCHITTEST` 在 maximized 下禁用缩放热区 → 卡死。

**修复**：`_clear_native_maximized()` 用 `SetWindowLongW` 清 `WS_MAXIMIZE` +
`SetWindowPos(FRAMECHANGED)`，接线在 `_ensure_normal_geometry()` 与
`show()` 的普通几何恢复分支**两处**。

### 3.3 `ptMaxPosition` 是相对所在显示器的坐标

主屏原点 (0,0) 时与绝对坐标恰好相同，**单屏看不出问题**；
副屏上用绝对坐标会把窗口最大化到错误位置。

### 3.4 隐藏重开后"第一次点还原被吞掉"

Qt 隐藏窗口时**不清** `WindowMaximized` 标志，而 `setWindowState()` 在
old==new 时直接 return。于是 `showMaximized()` 里的状态设置早退，原生侧从未收到，
Qt 状态与 Windows `WINDOWPLACEMENT` 失配。

**修复**：`_remember_and_reset_state()` 在隐藏时把 Qt 状态清零
（隐藏态调 `setWindowState` 有 `visible` 守卫，无视觉副作用）。

---

## 4. 全局钩子 / 线程

### 4.1 钩子回调里做重活 = 全系统打字卡顿

键盘/鼠标低级钩子回调必须**微秒级返回**：只记一个字符、置一个事件，
任何 UIA/COM/磁盘调用都会拖慢全系统输入。

### 4.2 UIA 同步调用会无限挂起

目标程序（浏览器/Electron）卡死时，`IUIAutomation` 同步调用**没有超时**。
在主线程调 → 冻结整个 GUI；在采样线程调 → 自动弹出彻底失效。

**修复**：`core/uia_text.py` 用 `_UIAWorker` 常驻线程 + `join(timeout)` + 忙碌守护，
默认超时 0.2~0.35s，超时回退（文本→`''`、可编辑→`True`、矩形→`None`）。

### 4.3 复用同一个 Event 会让上一轮线程"复活"

`start()` 里 `clear()` 一个共用的 `Event`，若上一轮线程还没退出就被 clear，
它会继续跑 —— **两条采样线程并存，同一次按键被判定两遍**。

**修复**：每轮新建 `Event` 并作为参数传给线程；`stop()` 里 `join(timeout)`。
COM 实例也要**按线程缓存**（换线程就漏）。

### 4.4 pynput 的"抑制"语义

- `win32_event_filter` 返回 `False` **只表示不派发给 `on_press`**，事件仍送达目标程序 —— **不是拦截**；
- 真正的系统级拦截只有 `listener.suppress_event()`；
- `Listener(suppress=True)` 会对**每一个**按键调 `suppress_event()` = 全局吞键 ——
  曾导致"开启后无法输入任何文字"，**绝不可用**。

### 4.5 注入方式与竞态

| 方式 | 坑 |
|------|-----|
| `direct` (`WM_CHAR`) | 依赖目标控件处理 `WM_CHAR`；拿不到焦点控件时需退回剪贴板 |
| `clipboard` | 每次重新快照会快照到"自己上一次的颜文字" → 恢复窗口内第二次注入**沿用上一轮快照** |
| `type` | 不先轮询确认英文布局生效就开打 → 被微软拼音吞成「哦哦」（**电脑越快越容易发生**）；打完也不能立刻切回布局，要给尾部键事件留"落字"时间，且必须 `QTimer.singleShot` 异步推迟，**绝不在主线程 sleep** |

### 4.6 注入后自我重触发（三重保险，缺一不可）

1. **注入静默期**：`notify_injected()` 后 1.5s 完全不采样；
2. **剔除已注入文本**：采样结果删掉最近上屏的颜文字（里面常含 `?`「哇」→ 命中「思考」）；
3. **情绪锁定**：某情绪弹过即锁定，直到它滚出扫描窗口或换了别的情绪。

---

## 5. Python 语言陷阱

### 5.1 模块级标记在函数里赋值，必须 `global`

只要函数体内**看到赋值**，整个函数内该名字就被判为局部变量，此前的一切读取抛
`UnboundLocalError`。

真实故障：`apply_settings` 里漏写 `global uia_warn_shown`，异常从函数中途抛出，
把其后的「自动弹出监听启停」整段跳过 —— 表现为"开过 UIA 提权后，改自动弹出开关不生效"。

**防线**：pyflakes 的 `referenced before assignment` 必须清零。

### 5.2 同名方法静默遮蔽

同类里定义两个同名方法，Python 以**最后一个**为准，前面的永不执行且不报错。

真实故障：`PickerWindow` 有两个 `apply_config`，靠后的那个重置了用户选择、
丢失了隐藏态亚克力防闪逻辑 —— 表现为"改个不透明度就跳回第一页 + 亚克力闪烁"。

### 5.3 删 import 的终极校验是"构造 + 跑一遍"

`py_compile` 只查语法；`import xxx` 只执行模块顶层，**不执行类体内的
`__init__`/`set_theme`/`apply_config`**。若某符号在方法里用到却被误判"未用"而删除，
只有**构造该对象或调用其方法时**才抛 `NameError`。

**正确做法**：离屏把相关窗口真建出来，调用 `apply_config` / `set_theme` 确认无 `NameError`。

---

## 6. 配置与状态

### 6.1 配置字典的引用身份不能变

`PluginManager` 持有 `config` 的引用。`AppRuntime.apply_settings` 必须
**原地 `clear()` + `update()`**，替换对象会让它继续读写一份已被丢弃的旧字典。

### 6.2 页面间的字典副本替换

`SettingsPage` 在 `refresh_from_config` 期间存的是 `config` 的**副本**，
用户交互时 `_on_apply` 又换成新副本。若多页共享同一引用而中途被替换，
会造成"开发者模式"等状态在页间读不一致。

**修复**：`UnifiedSettingsWindow.apply_config` 里把 `settings_page.config` /
`about_page.config` 重新指向**同一份权威字典**。

### 6.3 只隐藏 UI 拦不住功能（测试功能逃逸）

配置位仍为真时，功能代码照样读它生效（例如曾在测试模式开启后关闭，
或直接手改 `config.json`）。

**修复**：`enforce_test_feature_gate()` 挂在 `save_config` **落盘前**，
覆盖设置页保存、开发者页开关等**所有**写入路径。

### 6.4 关测试模式要真的卸载插件，不能只收起 UI

**这是 6.3 的孪生坑，出现在"运行时"而非"落盘时"。**

关闭开发者模式时，代码只把 `use_test_features` 置为 `False`：

```python
self.config["use_test_features"] = False   # ❌ 只是配置层门控
self._sync_plugin_tab()                    # ❌ 只是把「插件」页收起来
```

但插件**已经在内存里加载**了，状态仍是 `loaded`：
它的数据源/转换/事件回调继续生效，而它通过 `api.ui` 做的界面修改
**不会回滚**（回滚挂在 `_detach` → `MutatorHub.rollback` 上）。

**症状**：关掉开发者模式后，插件插的卡片、改过的样式/文字仍然留在界面上。

**修复**：关闭测试模式时调用 `_unload_all_plugins()`，逐个 `unload()`
（内部走 `_detach`，连带回滚界面修改）。

**两条路径都要覆盖**——它们不会自动互相兜底：

| 路径 | 入口 |
|------|------|
| 开发者页点「关闭开发者模式」 | `_disable_dev_tab()` |
| 开发者页点「关闭测试功能」 | `DevPage._on_toggle_test()` → `_on_test_features_changed()` |

修复点放在 `_disable_dev_tab()` 与 `_on_test_features_changed()` 两处，
后者是两条路径的公共汇合点。

> 相关：`tests/test_disable_dev_mode.py` 覆盖两条路径 + "插件插入的控件被移除"
> + "重新启用后效果能恢复"。已验证去掉修复后 4 个用例立刻变红。

### 6.5 「连点解锁」这类隐藏交互的三个人手感陷阱

用户反馈"打开开发者模式的手感很差"。实测定位到三个独立问题：

| 问题 | 原因 | 修复 |
|------|------|------|
| 点不准 | QLabel 默认尺寸贴文字，实测高度只有 **13px** | `setMinimumHeight(24)` + 内边距（触控最小点击目标） |
| 容易多计数 | 用 `MouseButtonPress` 判定，按住拖开也算点击 | 改 `MouseButtonRelease` + 释放位置在标签内才算 |
| **连点失灵** | 每次点击都 `setStyleSheet` 做闪烁反馈 | **点击时完全不碰样式/字体/几何**，只 `update()` |
| 第 8 次点击卡住 | DevPage 构建一次性做完约 **710ms** | 拆成三帧（构造/套主题/挂载），单帧降到 ~210ms |

### 连点失灵的根因（最隐蔽的一个）

```python
self._ver_label.setStyleSheet("color:#0a84ff;font-weight:bold;")   # ❌
```

看起来只是改个颜色，实测触发的事件链是：

```
MouseButtonPress → MouseButtonRelease
  → StyleChange → FontChange ×2 → PaletteChange → Paint
```

这些内部重算事件**打断了 Qt 的鼠标事件序列**，导致后续 press/release
根本到不了 eventFilter。用户表现就是"必须点一下停一下"。

修复后事件链干净了：

```
MouseButtonPress → MouseButtonRelease → Paint
```

**验证方式**：用 `QTest.mouseClick()` 发真实鼠标事件（而不是直接调
`_on_version_clicked()`），并装 eventFilter 断言没有 `StyleChange`/`FontChange`。

> ⚠ offscreen 平台**不会**复现这个打断现象（它不模拟真实事件派发），
> 所以此 bug 只能靠真实 Windows 平台验证。已实测连点 12 次稳定。

另有一个连点闪烁的小坑：每次点击都 `singleShot(150, 清空样式)`，
连点时前一次的定时器会把后一次的闪烁清掉，表现为"闪两下就没了"。
用自增 token 判断"是不是最后一次"即可。

**关于性能测试**：不要用"耗时 < N ms"做断言。同一份代码在 standalone
脚本里单帧 105~180ms，全量测试（CPU 更忙）能到 460ms，**绝对时间阈值
必然误报**。改断言**结构性事实**（如"构造与挂载必须在不同帧"），
既稳定又能在有人合并回单步时立刻失败。

> 相关：`tests/test_dev_mode_unlock.py` 共 10 例，覆盖热区、计数、
> 事件语义（press 不计数、拖出不算）、分帧结构、连点闪烁。

### 6.4 手改 config.json 的类型错误

`"max_recent": "30"` 这类错误以前会一路流进业务代码，
`int("三十")` 直接在启动期抛异常。

**修复**：`validate_config()` 按默认值做类型/枚举/范围校验，非法项回退默认并记日志；
未知键保留（向前兼容）；数值项允许 int/float 互通但**拒绝 bool**。

---

## 7. 插件系统

### 7.1 `discover()` 不得清空已加载插件的 `module`/`api`

`discover` 不执行入口，而 `load()` 见到 `state=loaded` 会直接 return。
若重扫时把这两个引用置 None 就再也补不回来 —— 表现为"重新扫描后插件卡片上的
『提供：N 个分类』消失"，且卸载时摘不掉 `sys.modules` 里的模块。

### 7.2 能力回收要覆盖三条路径

停用（`unload`）、目录被删（重扫时 `pid not in found`）、清单被改坏（校验失败）。
后两者原先只更新记录、不回收能力 —— 插件的数据源/转换/监听器继续生效，
页面却已看不到它；重装同名插件还会叠加。现集中在 `discover()` 末尾统一 `_detach`。

### 7.3 UI 扩展的所有权

**manager 只登记 spec/factory，绝不持有 QWidget**。widget 由宿主
（`PluginPage` / `UnifiedSettingsWindow` / `PluginDialog`）创建与销毁 ——
避免 core 层持 Qt 对象导致悬垂。

### 7.4 `setup()` 期间 emit `ui_changed` 会被过滤掉

`setup()` 执行期间 `state` 还不是 `loaded`，宿主按"已加载"过滤会把刚注册的 UI
全部滤掉 —— 表现为"新启用插件的页面不出现、重载一次才出现"。

**修复**：`load()` 置 `state=loaded` 之后**补发一次** `ui_changed`；
setup 半途抛异常时 `_detach` 清理半注册的脏能力。

### 7.5 宿主自建的页面必须 `deleteLater`

`QStackedWidget.removeWidget()` 只是移出栈，widget 仍挂在父容器上。
反复开关「测试模式」/「开发者模式」若不销毁，会越积越多，
每次插件变动都被多个僵尸页面重复刷新。

### 7.6 状态机不要混装两种 error

用同一个 `state=error` 混装「清单校验失败」与「入口执行抛异常」时，
前者重试无意义、后者可以重试。**记录 `error_kind`**：`manifest` / `runtime`；
`unload` 只清 runtime 错误。

### 7.7 插件回调无超时保护

所有插件回调在 **Qt 主线程同步执行**，恶意/劣质插件能直接卡死界面
（与 VS Code/浏览器扩展同信任模型：插件代码 = 用户自装代码）。

**缓解**：`PluginManager._timed_call` 对超过 `SLOW_CALL_WARN_SEC`(50ms) 的回调
记 warn 日志（带插件 id 与耗时），让"界面偶尔卡一下"能在日志里定位元凶。

### 7.8 官方 UI 能力只"能加不能改"的时代结束了

> **状态：已解决。** 主程序现提供 `api.ui`（见 `docs/PLUGIN_GUIDE.md`），
> 可按 id/路径定位任意已登记元素、改属性、增删控件、替换渲染，
> 且全部**自动回滚**。下面保留历史记录，供理解"为什么值得专门做这套 API"。

插件的早期官方 UI 能力全是**新增**：`register_page`、
`register_settings_widget`、`show_dialog`/`notify`、`theme_tokens`（只读）。
**没有任何 API 能修改自带页面**。

但插件是进程内全权限 Python 代码，Qt 的 `QWidget.window()` 会沿父子链爬到
顶层窗口。实测：`register_page` 的 factory 收到的 parent 是 `AnimatedStackedWidget`，
`.window()` 直达 `UnifiedSettingsWindow`，随后 `.home_page` / `.settings_page` /
`._set_page()` / `.theme` 全部可及。

`plugins/home_greeting/` 是这条路的完整示例，但它是**反面教材**：

| 成本 | 具体表现 |
|------|---------|
| 依附私有结构 | `home_page` 一旦重构（哪怕多包一层容器）就静默失效 |
| 无版本契约 | API v1/v2 从未承诺过这些属性存在 |
| 失败是静默的 | 不报错不崩溃，就是卡片不见了 |
| 必须自己收尾 | 宿主只销毁自己创建的 widget，插进去的得自己摘 |
| API 只进不出 | `register_page` 没有注销接口，撤销只能改 `manager._plugin_pages` |

该插件为"自己收尾"试错 **7 轮**：`destroyed` 信号 → 加标志位 → 断开连接 →
`QTimer` 轮询 → 最后才落到"监听 `changed` 信号 + 状态自检"。

**结论**：遇到"必须爬对象树才能实现"的需求，正确做法是**给主程序提需求**，
而不是每个插件各爬各的。`api.ui` 就是这么长出来的。

---

## 8. 打包与部署

| 坑 | 现象 | 修复 |
|----|------|------|
| `--add-data` 目标写成**文件名** | 资源嵌深一层 → 字体注册失败 → **图标豆腐块** | 目标必须写**目录**（`ui/fonts`） |
| 冻结态与源码态路径混用 | 产生两份配置，排查很久 | 只读资源走 `_internal`，可写数据走 `%APPDATA%` |
| 打包后每次启动弹**3 次黑框** | `git rev-parse` 在 windowed 程序里 spawn console 子进程 | 打包态优先读烘焙文件 + `CREATE_NO_WINDOW` + 进程内缓存 |
| `build.py` 污染源码态版本号 | 跑过一次 build，源码态版本变成烘焙的 `v1.2(xxx)` | 源码态一律走 git；build 结束 `finally` 删 `_build_version.py` |
| 下载 SHA-256 在主线程同步算 | "进度 100% 但界面假死 1~2 秒" | `HashWorker(QThread)` 后台算 |
| 下载进度用 `downloadProgress.total` 作分母 | 进度冲到 ~50% 卡住 | GitHub 302 到 CDN 后 total 不可靠，用资产的真实 `size` |
| 下载请求设了 `setTransferTimeout` | 慢速网络下大包被中断 | 只给**元数据**请求设超时 |

---

## 9. 症状索引

| 症状 | 最可能的原因 |
|------|-------------|
| 图标变豆腐块 | 打包 `--add-data` 写错；字体未注册（`--diag` 自检） |
| 整窗发黑 / 浅色主题也黑 | `QColor("rgba(...,0.88)")` invalid → 用 `parse_color()` |
| 某个按钮白底白字不可见 | QSS 级联被中间容器的 `setStyleSheet` 切断 |
| 切页面时闪一下桌面 | 页面动画用了透明度（半透窗体） |
| 启用/停用插件时闪过好几个小窗口 | 销毁控件前没 `hide()` 就 `setParent(None)`（§2.7） |
| 换掉容器内容后位置/间距错乱 | `removeWidget` 留了空布局项，该用 `takeAt`（§2.8） |
| 切页面卡成 PPT | `QGraphicsOpacityEffect` 每帧离屏渲染 |
| 改设置卡近一秒 | QSS 重贴没做幂等短路 |
| 窗口卡住拖不动/缩不小 | 原生 `WS_MAXIMIZE` 残留 → `_clear_native_maximized()` |
| 最大化后盖住任务栏 / 贴错屏 | `WM_GETMINMAXINFO` 未处理，或 `restype` 被截断 |
| 面板拉满不透明度还是很透 | 叠加了 `window_tint` 的固有 alpha |
| 上屏后又弹一次候选 | 三重防重触发保险缺了一环 |
| 打完关键词还得多按一键才弹 | 采样未覆盖 IME 提交滞后 → 驻留轮询 |
| 开启后无法输入任何文字 | `Listener(suppress=True)` 全局吞键 |
| 手打状态被判定成两次 | 复用同一个 `Event` 导致采样线程复活 |
| 开过 UIA 后改自动弹出开关不生效 | 漏写 `global` → `UnboundLocalError` 中断 `apply_settings` |
| 改不透明度跳回第一页 | 同名方法遮蔽（后定义者覆盖前者） |
| 重新扫描后插件能力摘要消失 | `discover()` 清空了已加载插件的 `module`/`api` |
| 插件删了但还在生效 | 能力回收未覆盖"目录被删/清单损坏"路径 |
| 新启用插件的页面不出现 | `setup()` 期间 emit `ui_changed` 被"已加载"过滤 |
| 关闭测试模式后测试功能仍生效 | 只在 UI 层隐藏，没在 `save_config` 落盘前归位 |
| 关闭开发者模式后插件插的东西还在 | 只改配置没卸载插件，界面修改未回滚（§6.4） |
| 隐藏的连点解锁点不准 / 卡顿 / 连点失灵 | 热区过小、用 press 计数、点击时改样式、构建未分帧（§6.5） |
| 启动即崩、`int("三十")` 之类 | `config.json` 手改的类型错误（现有 `validate_config` 兜底） |

---

## 10. 提交前自检

- [ ] 动过 `ctypes`？离屏实跑，断言 `GetLastError()==0`，不凭记忆写常量。
- [ ] 写了 `setStyleSheet`？确认不会切断上层级联；中间容器先垫**完整** `Theme.style_sheet()`。
- [ ] 删了 import？离屏把相关窗口**真建出来**并调用 `apply_config`/`set_theme`。
- [ ] 加了模块级标记？函数内赋值记得 `global`；pyflakes 的 `referenced before assignment` 清零。
- [ ] 新增了同名方法？`grep` 该类内定义，防静默遮蔽。
- [ ] 主题广播？遍历 `_nav.pages.values()` + `hasattr(page,"set_theme")`，**不要**硬编码页名列表。
- [ ] 新功能归测试模式？UI 登记 + 导航不建 + `TEST_FEATURE_DEFAULTS` 落盘归位，三步齐全。
- [ ] 测试跑过？`.venv\Scripts\python.exe -m pytest tests -q`，并确认**没改到工作区的 config.json**。
- [ ] 改了分层？`tests/test_plugins.py::test_core_does_not_import_ui` 必须仍然通过。
