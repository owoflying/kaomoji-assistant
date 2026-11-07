# 文档索引

> 本文件原是 49 KB 的「架构设计 + 踩坑经验」合集，设计与经验混在一起，
> 新人读完仍分不清"哪个是规则、哪个是历史教训"。现拆为两份，各司其职。

| 文档 | 内容 | 什么时候读 |
|------|------|-----------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | **设计**：分层、数据流、关键机制、线程模型、开发流程 | 接手项目、改架构、加功能前 |
| [docs/PITFALLS.md](docs/PITFALLS.md) | **踩坑手册**：19 类根因、症状索引表、提交前自检 | 写代码时、排查诡异 bug 时、提交前 |
| [PROJECT_ARCHITECTURE.md](PROJECT_ARCHITECTURE.md) | **现状梳理**：逐模块职责、信号接线表、依赖图、评价 | 想快速建立全局认识 |
| [README.md](README.md) | 用户向：功能、键位、运行与打包 | 第一次接触项目 |

## 30 秒建立认知

```
main.py (54 行)  →  AppRuntime (装配层，唯一中枢)
                        ├─ ui/    呈现：unified_window(外壳+导航) / picker_window 候选条 / pages 七页
                        ├─ core/  业务：emotion_monitor 监听 / injector 注入 / plugins 插件 / user_* 数据
                        └─ 平台    win_utils · uia_text · uia_elevation（ctypes/COM 全收敛在此）
```

三条单向收敛点：
- 配置只有 `AppRuntime.apply_settings` 一个落盘口
- 注入只有 `AppRuntime.on_selected` 一个出口
- 结构设计只有 `ui/win11_theme.Theme` 一份令牌

## 跑测试

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

## 历史版本

拆分前的完整文档保留在 [docs/archive/](docs/archive/)（仅供追溯，内容已过时，
例如其中描述的 1172 行单一 `unified_window.py` 现已拆分）。
