# 文档索引

> 面向项目读者：从架构设计到量化证据的完整技术文档。
> **最后整理**：2026-10-26

---

## 一、建议阅读顺序

| 顺序 | 文档 | 什么时候读 |
|---|---|---|
| 1 | [`../README.md`](../README.md) | 项目做什么、技术栈、怎么跑起来 |
| 2 | [`showcase.md`](showcase.md) | 30 秒速览：三个核心差异化 + 关键数字 |
| 3 | [`architecture.md`](architecture.md) | 改代码前：分层、数据流、关键设计取舍 |
| 4 | [`configuration.md`](configuration.md) | 配环境变量、切数据库 / Redis / AI 模型 |
| 5 | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | 跑测试、提交规范、CI |

---

## 二、文档地图

### 技术与设计

| 文档 | 内容 |
|---|---|
| [`architecture.md`](architecture.md) | 系统架构、后端分层、接口设计，及与 Java 原版的对照 |
| [`configuration.md`](configuration.md) | 全量环境变量说明（含 `*_BACKEND` Redis 多副本开关、模型名陷阱） |
| [`migration/java-to-python.md`](migration/java-to-python.md) | Java → Python 重构：动机、范围、逐模块迁移 |
| [`migration/java-vs-python.md`](migration/java-vs-python.md) | 两版实现与能力的对照表 |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | 贡献指南：环境准备、测试命令、提交规范 |

### 量化证据（评审 / 面试重点）

| 文档 | 内容 |
|---|---|
| [`reports/rag-evaluation.md`](reports/rag-evaluation.md) | RAG 三策略 Hit@K / MRR / P@K / R@K（含不同 embedding 模型的对照口径） |
| [`reports/local-model-benchmark.md`](reports/local-model-benchmark.md) | 本地模型分工基准：为什么「对话用大模型、工具识别用小模型」 |
| [`showcase.md`](showcase.md) | 30 秒速览 + 关键数字 + 面试讲法 |

### 素材与其它

| 路径 | 内容 |
|---|---|
| [`assets/`](assets/) | 截图、UI 验收素材 |
| [`../CHANGELOG.md`](../CHANGELOG.md) | 版本变更记录 |

---

## 三、文档约定

1. **数据必须可复现**：任何「效果 / 性能」数字，都要写清**生成时间、使用的模型或版本、复现命令**。
   > 反例：`reports/rag-evaluation.md` 早期版本没写 embedding 模型名，
   > 导致读者把 43.8% 误当成当前默认配置的效果（已于 2026-10-23 补注口径）。
2. **交叉引用用相对链接**，新增文档时在本文档的「文档地图」补一行。
3. **按主题归位**：迁移类进 `migration/`，评估类进 `reports/`，工程流程类留在 `docs/` 顶层。
