# 插件开发指南

> 面向想给 kaomoji-assistant 写插件的人。
> 本文覆盖：插件结构、能力清单、**界面修改 API（`api.ui`）**、以及一个必读的反面案例。

---

## 1. 最小插件

```
plugins/my_plugin/
├── plugin.json
└── main.py
```

`plugin.json`：

```json
{
  "name": "我的插件",
  "version": "1.0.0",
  "entry": "main.py",
  "author": "you",
  "description": "一句话说明",
  "api_version": 2
}
```

`main.py`：

```python
def setup(api):
    api.log("插件已加载")
    api.add_source("我的分类", ["(￣▽￣)", "ヽ(°〇°)ﾉ"])
```

**要点**：

- 入口必须提供 `setup(api)`，加载时调用一次；
- `api_version: 2` 才能用 UI 能力（含 `api.ui`），v1 插件调用会直接抛 `PluginError`；
- 可选 `"default_enabled": true` —— 用户从未手动切过开关时默认启用；
- 源码态插件放项目 `plugins/`，安装版放 `%APPDATA%/KaomojiAssistant/plugins/`。

---

## 2. 能力清单

| 能力 | API | 说明 |
|------|-----|------|
| 数据源 | `add_source(分类, 条目)` | 追加颜文字，只进内存，**不写盘** |
| 输出转换 | `register_transform(fn, priority)` | 上屏前改写文本，priority 小者先 |
| 事件 | `on(event, fn)` | `startup` / `shutdown` / `before_insert` / `after_insert` / `config_changed` |
| 配置 | `settings`（活字典）+ `save_settings()` / `get_config(key)` | 私有配置落 `plugin_settings/<id>.json` |
| 日志 | `log(msg)` | 写入全局日志（托盘「查看日志」可见） |
| 侧边栏页 | `register_page(page_id, title, factory)` | 新增一页（不改动内置页） |
| 卡片设置区 | `register_settings_widget(factory, title)` | 插件卡片下方可折叠设置区 |
| 弹窗 | `show_dialog` / `show_message` / `ask` | 主题化无边框弹窗 |
| 通知 | `notify(title, msg)` | 托盘气泡 |
| 主题 | `theme_tokens()` | 只读颜色字典 |
| **界面修改** | **`api.ui`** | **见下一节，可改任意内置界面** |

---

## 3. 界面修改 API（`api.ui`）

### 3.1 设计原则

**所有修改都会被宿主记录，插件停用 / 卸载 / 崩溃时自动完整回滚。**

这是本 API 与"自己爬对象树"最根本的区别：你不需要写任何清理代码。

### 3.2 定位元素

两套寻址，可混用：

```python
# ① 手工 id（推荐）——宿主在构造界面时登记的稳定标识
api.ui.find("home.greeting.title")     # -> QLabel

# ② 自动路径（兜底）——按「类型[序号]」逐级定位，能触达任何控件
api.ui.find("home/GreetingCard[0]/QLabel[1]")
```

**优先用 id。** id 是契约，宿主重构时保证不变；路径只是"当前结构的快照"，
布局层级一变就失配。

探查可用节点：

```python
api.ui.ids()                  # 所有已登记的有效 id
for path, w in api.ui.walk():  # 遍历控件树
    api.log("%s -> %s" % (path, type(w).__name__))
api.ui.path_of(some_widget)    # 反查控件路径
api.ui.exists("home.greeting") # 判断是否存在
api.ui.text_of("home.greeting.title")   # 读文字
```

### 3.3 已登记的内置元素 id

| id | 说明 |
|----|------|
| `shell.window` | 主窗口 |
| `shell.sidebar` | 左侧导航栏 |
| `shell.titlebar` | 标题栏 |
| `shell.content` | 右侧内容区（页面栈） |
| `home.greeting` | 主页问候卡片 |
| `home.greeting.title` | 问候标题（如"中午好"） |
| `home.greeting.emoji` | 问候 emoji |
| `home.greeting.sub` | 问候副标题（时间 + 提示） |
| `home.welcome` | 欢迎卡片 |
| `home.welcome.title` / `.sub` | 欢迎卡片的两行文字 |
| `home.stats` | 统计行 |
| `home.actions.search` / `.custom` / `.triggers` / `.settings` | 快捷入口卡片 |
| `home.tip` | 使用提示卡片 |
| `home.plugin_slot` | 插件卡片插槽 |

> 内建 id 常量见 `core/ui_ids.py`。**未登记的元素仍可用路径触达**。

### 3.4 修改属性

```python
api.ui.set_text("home.greeting.title", "欢迎回来！")
api.ui.set_visible("home.welcome", False)      # 隐藏欢迎卡片
api.ui.set_style("home.tip", "border: 1px solid #ff6b6b;")
api.ui.set_enabled("home.actions.settings", False)
api.ui.set_property("home.stats", "pluginAccent", True)
```

全部返回 `bool`（目标不存在返回 `False`，**不抛异常**）。

`set_style` 默认 `append=True`，把样式**追加**在原样式之后。
这很重要：直接覆盖会切断祖先 QSS 级联，导致控件降级成原生样式（详见
`docs/PITFALLS.md` §2.1）。

### 3.5 修改结构

```python
from PySide6.QtWidgets import QLabel

# 插入自己的控件
lbl = QLabel("来自插件的一行字")
api.ui.insert("home.greeting", lbl, index=0)

# 摘掉内置控件（可回滚，卸载后自动插回原位）
api.ui.remove("home.tip")
```

### 3.6 替换整段渲染

自由度最高的能力，适合"整块重绘"：

```python
def build(parent):
    w = QWidget(parent)
    lay = QVBoxLayout(w)
    lay.addWidget(QLabel("我把欢迎卡片换成了自己的内容"))
    return w

api.ui.replace_render("home.welcome", build)
```

回滚时宿主销毁你的产物并**按原顺序还原**被替换的内容。

### 3.7 绑定事件

```python
def on_search():
    api.notify("插件", "你点了搜索")

api.ui.on("home.actions.search", "clicked", on_search)
```

插件卸载时连接自动断开。

### 3.8 修改其他插件的界面

默认允许（本项目插件是同进程全权限代码，与其假装隔离，不如**记录 + 可回滚**）。

```python
api.ui.set_text("plugin:other_plugin:page_id.some_id", "被改掉了")
```

跨作用域的修改会在日志里标注「（跨插件）」：

```
[info] (plugin:my_plugin) 修改界面（跨插件） plugin:other:xxx [text]
```

查自己还有多少未回滚的修改：

```python
api.ui.pending()      # 条数
api.ui.rollback()     # 立即回滚（通常不用手动调，宿主会做）
```

### 3.9 时序：`setup()` 里能直接改界面

装配顺序是「**先加载插件（执行 `setup`）→ 再建窗口登记元素**」，因此
`setup()` 执行时元素表还是空的。

**你不需要关心这个。** 所有**修改类**方法会自动排队，等窗口建好后统一重放：

```python
def setup(api):
    # 此刻界面还没建好，但下面这些调用会被排队、稍后生效
    api.ui.set_text("home.greeting.title", "你好")
    api.ui.set_visible("home.tip", False)
    api.ui.insert("home.greeting", my_widget, 0)
```

⚠ **读取类方法（`find` / `text_of`）不会排队** —— 它们需要立即返回值，
在 `setup()` 阶段调用会拿到 `None` / 空串。

所以「**先读原值再改**」的写法要放进 `startup` 事件（此时界面已就绪）：

```python
def setup(api):
    api.ui.set_text("home.greeting.title", "固定文案")     # ✅ setup 里可以

    def on_startup():                                      # ✅ 读原值要放这里
        old = api.ui.text_of("home.greeting.title", default="")
        if old:
            api.ui.set_text("home.greeting.title", old + "（插件）")
    api.on("startup", on_startup)
```

> `startup` 正常只发一次。若你自己手动 `emit` 调试，注意「基于原值拼接」
> 会叠加——加个幂等标志保护自己。

### 3.10 自由度的边界

`api.ui` 能改**已存在**的界面元素。它**不能**：

- 新建一个内置页面（用 `register_page`）；
- 改宿主的业务逻辑本身（如热键注册、注入流程）；
- 越过 Qt 对象树碰非控件对象。

真想做到这些，你需要的是改主程序而不是写插件。

---

## 4. ⚠ 反面案例：邪修注入

`plugins/home_greeting/` 是一个**教学用的错误示范**，默认停用。

当时官方 API 还没有 `api.ui`，它用"爬 Qt 对象树"的方式往主页塞卡片：

```python
# ❌ 不要学
def build_anchor(parent):
    holder = QWidget(parent)
    host = holder.window()                    # 爬到 UnifiedSettingsWindow
    host.home_page.layout().insertWidget(1, my_card)   # 直接改私有结构
    return holder

api.register_page("anchor", "__anchor__", build_anchor)

# 还得偷偷撤销这个无意义的锚点页
api._manager._plugin_pages.pop("plugin:home_greeting:anchor")
api._manager.ui_changed.emit()
```

### 为什么这是错的

| 代价 | 具体表现 |
|------|---------|
| **依附私有结构** | `home_page` 是私有属性，主程序重构一次就静默失效 |
| **失败是静默的** | 不报错、不崩溃，卡片就是不见了 —— 用户只会说"插件坏了" |
| **必须自己收尾** | 宿主只销毁自己创建的 widget，你插的得自己摘 |
| **官方 API 只进不出** | `register_page` 没有注销接口，撤销只能改私有注册表 |
| **调试成本极高** | 该插件为"自己收尾"试错 **7 轮**才稳定 |

那 7 轮是：`destroyed` 信号 → 加标志位区分 → 断开连接 → `QTimer` 轮询 →
最后才落到"监听 manager `changed` + 状态自检"。

### 同一件事，正规写法

```python
# ✅ 推荐
def build_card(parent):
    return QLabel("中午好", parent)

api.ui.insert("home.greeting", build_card(None), index=0)

# 或者干脆改内置问候卡片的文字
api.ui.set_text("home.greeting.title", "中午好")
```

没有锚点页、没有私有属性、没有清理代码。卸载时宿主自动回滚。

> 保留这个案例，是为了让人看清**没有正规扩展点时会发生什么**。
> 如果你是插件作者，看到需要 `host.home_page` 或 `_manager._xxx` 才能实现的功能，
> 那说明该给我们提需求，而不是自己爬。

---

## 5. 生命周期与清理

`api.ui` 的修改由宿主自动回滚，但**你自己创建的资源仍需自己管理**：

| 资源 | 谁来清理 |
|------|---------|
| `api.ui.*` 的修改 | ✅ 宿主自动回滚 |
| `register_page` 注册的页面 | ✅ 宿主销毁 |
| `register_settings_widget` 的面板 | ✅ 宿主销毁 |
| `show_dialog` 打开的弹窗 | ✅ 主程序退出时统一关 |
| 你自己起的 `QThread` / `QTimer` | ❌ **自己停** |
| 你自己开的文件 / socket | ❌ **自己关** |
| 你持有的外部引用 | ❌ **自己释放** |

监听 `shutdown` 事件做收尾：

```python
def setup(api):
    timer = QTimer()
    timer.start(5000)
    api.on("shutdown", lambda: timer.stop())
```

> ⚠ **`shutdown` 只在应用退出时发**，插件被停用/卸载时**不会**触发。
> 如果你的后台资源必须随停用释放，请把清理挂在 `api.ui` 的修改上
> （宿主回滚时会带走），或使用 `QTimer` 自检 `api.ui.pending()`。

### 5.1 崩溃时也会回滚

插件回调（`startup` / `transform` / 事件处理）抛异常时，宿主会：

1. 记一条 error 日志；
2. **回滚该插件的全部界面修改**；
3. 但**不禁用插件**（一次回调异常不该让整个插件下线）。

这样即使你"改了一半界面然后崩溃"，用户也不会看到一个半残的界面。

`setup()` 期间崩溃同样会回滚（加载失败路径本来就会 `_detach`）。

---

## 6. 调试

1. **日志**：`api.log("...")`，在托盘「查看日志」里看（需开开发者模式）；
2. **开发者模式页**：主面板 → 开发者（连点关于页版本号解锁），可看事件流；
3. **探查界面**：`api.ui.ids()` 与 `api.ui.walk()` 会告诉你当前有哪些可改的节点；
4. **改了没生效**？先确认 `api.ui.find(target)` 返回非 `None`；
5. **卸载后界面没复原**？理论上不会——回滚由 `_detach` 保证，
   若出现请检查是否用了 `api.ui` 之外的直接控件操作。

---

## 7. 参考实现

| 插件 | 演示了什么 |
|------|-----------|
| `plugins/ui_tweaks_example/` | ✅ **正面示例**：`api.ui` 全套用法（改文字/样式/可见性/插控件/绑事件），**零清理代码** |
| `plugins/example_kaomoji/` | API v2 全能力：数据源、转换、事件、设置面板、独立页、弹窗、通知 |
| `plugins/startup_notice/` | `startup` 事件 + 弹窗/通知两种提示方式 + 设置面板 |
| `plugins/home_greeting/` | ⚠ **反面教材**：邪修注入（默认停用，仅供学习错误姿势） |

**建议先读 `ui_tweaks_example`。** 它与 `home_greeting` 是同一件事的两种做法，
对照着看能直观理解为什么值得专门做 `api.ui`：

| 事项 | `ui_tweaks_example`（正规） | `home_greeting`（邪修） |
|------|---------------------------|------------------------|
| 定位界面 | `api.ui.set_text("home.greeting.title", ...)` | `register_page` + `window()` 爬树 |
| 清理代码 | **0 行** | destroyed/标志位/断开/轮询，试错 7 轮 |
| 宿主重构后 | id 契约保证不碎 | 静默失效 |
| 崩溃后 | 宿主自动回滚 | 界面可能半残 |

测试参考：`tests/test_ui_api.py`（`api.ui` 全套能力 26 例）、
`tests/test_ui_tweaks_example.py`（正面示例 8 例）、
`tests/test_plugins.py`（生命周期与隔离）、
`tests/test_home_greeting_plugin.py`（邪修路径的边界）。
