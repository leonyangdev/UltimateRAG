# ADR-003：总结感知检索与持久化会话记忆

## Context / 背景

对《Attention Is All You Need》提问“总结文档的核心内容”时，旧链路把它当作普通事实问答：

```text
泛化 Query → Dense/BM25 → RRF → Rerank → Top 5
```

数据库中实际已有摘要、引言、模型架构、实验和结论等 61 个 Chunk，但最终 Top 5 被参考文献、
Regularization 和局部 Attention 定义占据。问题不在 PDF 解析，也不在模型拒答，而在检索任务与
证据组织方式不匹配：局部相关性排名无法保证全文覆盖。

标准 RAG 只取少量连续片段时难以回答跨全文主题问题；RAPTOR 通过递归聚类与摘要建立不同抽象
层级。Microsoft 的 Advanced RAG 指南也建议用“文档摘要索引 → 详细 Chunk”分层导航。另一方面，
长会话不能简单把全部消息永久塞进 Prompt：即便模型窗口足够大，成本和干扰仍随历史增长。

参考：

- [RAPTOR 原始论文](https://arxiv.org/html/2401.18059)
- [Microsoft Advanced RAG](https://learn.microsoft.com/en-us/azure/developer/ai/advanced-retrieval-augmented-generation)
- [Anthropic Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Recursive Summarization for Long-Term Dialogue Memory](https://arxiv.org/html/2308.15022v3)

## Decision / 决策

### 1. 显式区分检索意图

使用保守、确定性的规则识别明确全文总结请求。普通事实问题继续使用 V3 Hybrid Retrieval；全文总结
从 PostgreSQL 读取 READY 文档全部 Chunk，再按最深章节标题选择代表块，过滤 References、
Bibliography 和 Acknowledgements，使用独立 Chunk/Token/Context 预算。

```text
FACT             → Rewrite → Hybrid → RRF → Rerank → Small2Big
DOCUMENT_SUMMARY → READY Chunk facts → Section Coverage → Context
```

Trace 新增 `intent` 和 `strategy`，避免把结构覆盖的顺序分数误解成相似度。当前不立即构建 RAPTOR
树：现有 Parser 已提供高质量章节结构，确定性覆盖即可修复真实问题，且不增加摄取时模型费用、
摘要一致性状态和重建流程。超大文档的持久化分层摘要索引作为后续评估项。

### 2. 会话原文是事实，摘要是缓存

新增 `chat_sessions` 与 `chat_messages`：每次进入知识库创建新会话，用户可以选择历史会话继续。
所有原始消息保留在 PostgreSQL；`memory_summary` 和 `memory_through_sequence` 只记录可重建的递归
摘要。模型输入由“长期摘要 + 最近消息原文”组成，最近消息按本地 Tokenizer 预算倒序保留。

摘要只用于用户偏好、指代、决定和未决问题，不可作为知识事实来源。知识事实仍必须由本轮
`knowledge_context` 支撑。摘要失败时降级为最近原文，不删除数据，也不阻断 RAG。

### 3. 流式消息使用提交状态

一次轮次在同一事务中写入 COMPLETE 用户消息和 PENDING 助手占位。正常流结束后提交完整助手
消息；异常或客户端取消则标记 FAILED。同一会话存在有效 PENDING 时返回 409，防止并发回答交叉
写入。进程崩溃留下的 PENDING 超时后可恢复为 FAILED，避免会话永久锁死。

## Alternatives / 备选方案

- 只增大 `top_k`：不能保证章节覆盖，还会引入更多噪声，拒绝。
- 所有问题都加载整篇文档：普通事实问答成本和噪声显著增加，拒绝。
- 每轮携带全部历史：上下文、延迟和费用无限增长，拒绝。
- 只保存递归摘要：摘要可能遗漏或误写关系，无法审计和重建，拒绝。
- 当前立即实现 RAPTOR：能力有价值，但对现有结构化 PDF 问题成本过高，延后到离线评估证明需要时。

## Consequences / 影响

- “总结文档”可以稳定覆盖摘要、方法、实验与结论，且 References 不再挤占核心预算。
- 历史会话跨刷新、跨进程存在，后续问题可用会话消解指代。
- 新增 Alembic `0003_chat_sessions`，部署前必须执行迁移。
- 长会话偶尔产生一次额外百炼摘要调用；通过阈值、最大输出和失败降级控制成本。
- 历史消息暂不保存每轮完整 Retrieval Evidence；会话正文可恢复，证据仍在当前流中展示。
