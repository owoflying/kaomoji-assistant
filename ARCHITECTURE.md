# 文档索引

> 本文件原是 49 KB 的「架构设计 + 踩坑经验」合集，设计与经验混在一起，
> 新人读完仍分不清"哪个是规则、哪个是历史教训"。现拆为多份，各司其职。

| 文档 | 内容 | 什么时候读 |
|------|------|-----------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | **设计**：分层、数据流、关键机制、线程模型、开发流程 | 接手项目、改架构、加功能前 |
| [docs/PLUGIN_GUIDE.md](docs/PLUGIN_GUIDE.md) | **插件开发**：能力清单、`api.ui` 界面修改 API、反面案例 | 写插件、想改内置界面时 |
| [docs/PITFALLS.md](docs/PITFALLS.md) | **踩坑手册**：20 类根因、症状索引表、提交前自检 | 写代码时、排查诡异 bug 时、提交前 |
| [PROJECT_ARCHITECTURE.md](PROJECT_ARCHITECTURE.md) | **现状梳理**：逐模块职责、信号接线表、依赖图 | 想快速建立全局认识 |
| [README.md](README.md) | 用户向：功能、键位、运行与打包 | 第一次接触项目 |

## 30 秒建立认知

```
main.py (54 行)  →  AppRuntime (装配层，唯一中枢)
                        ├─ ui/    呈现：unified_window(外壳+导航) / picker_window 候选条 / pages 八页
                        ├─ core/  业务：emotion_monitor 监听 / injector 注入 / plugins 插件 / user_* 数据
                        │               ui_registry 元素寻址 · ui_mutation 修改记录与回滚
                        └─ 平台    win_utils · uia_text · uia_elevation（ctypes/COM 全收敛在此）
```

三条单向收敛点：
- 配置只有 `AppRuntime.apply_settings` 一个落盘口
- 注入只有 `AppRuntime.on_selected` 一个出口
- **界面修改只有 `ui_mutation` 一个入口，且全部可回滚**

## 插件能改界面吗？

**能，而且是官方支持的。**

通过 `api.ui` 可以按 id 或路径定位任意已登记元素（含其他插件的界面），
修改文字/图标/颜色/样式、增删控件、替换整段渲染。
所有修改由宿主记录，插件停用 / 卸载 / 崩溃时**自动完整回滚**。

```python
def setup(api):
    api.ui.set_text("home.greeting.title", "欢迎回来")
    api.ui.set_visible("home.tip", False)
    api.ui.on("home.actions.search", "clicked", lambda: api.notify("插件", "点了搜索"))
```

完整用法见 [docs/PLUGIN_GUIDE.md](docs/PLUGIN_GUIDE.md)。

> `plugins/home_greeting/` 是"没有 API 时人们怎么邪修"的反面教材（默认停用），
> 与上面的正规写法对照着看，能明白为什么值得专门做这套 API。

## 跑测试

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

## 历史版本

拆分前的完整文档保留在 [docs/archive/](docs/archive/)（仅供追溯，内容已过时，
例如其中描述的 1172 行单一 `unified_window.py` 现已拆分）。
