# 核心设计原则

这一页汇总 UltimateRAG 反复强调的设计原则。**理解这些原则，比记住某个具体类更重要**——它们解释了代码里每一个「为什么这么写」。

## 1. 简单优先，拒绝炫技

项目最核心的价值观：

> **简单优于复杂，明确优于隐式，可维护优于炫技，解决当前问题优于预测未来。**

- 能用普通 `if` 就不用设计模式
- 能用普通 Python Service 编排流程，就不引入 LangGraph / Event Bus / DI 框架
- 不为了「代码看起来高级」而引入复杂抽象

```python
# 好的写法：直接、可读
if document.status == DocumentStatus.READY:
    return document

# 坏的写法：为缩短代码而写晦涩表达式
# return next((d for d in [document] if d.status == DocumentStatus.READY), None)
```

## 2. 每个抽象必须证明自己的价值

新增 `Interface / Adapter / Factory / Manager / Service / Repository` 之前，先问：

> 它到底解决了什么实际问题？

合理的抽象：

- `DocumentParser`——因为格式确实有多种（Markdown/PDF/Office…）
- `Embedder`、`VectorStore`、`LLMClient`——因为这些能力明确存在多实现

不合理的抽象：只为了「分层完整」套一层没有逻辑的 Wrapper。

## 3. 领域层保持独立（不绑定框架）

**核心领域模型必须是项目自有的，不能绑定任何框架。**

```text
❌ from langchain_core.documents import Document   ← 作为核心数据结构
✅ 自己的 Document / ParsedDocument / Block / Chunk / RetrievalResult
```

如果需要 LangChain，方向是：

```text
UltimateRAG 领域模型 → LangChain Adapter → LangChain
```

而不是把项目变成 LangChain 的数据模型。

## 4. 数据职责必须清晰

**三类存储各司其职，这是整个项目最重要的数据边界：**

| 存储 | 保存什么 | 角色 |
|---|---|---|
| **PostgreSQL** | 业务事实：知识库、文档、状态、Chunk 元数据 | 事实来源（Source of Truth） |
| **MinIO** | 原始文件（PDF/Markdown/Office/图片…） | 对象存储 |
| **Milvus** | 向量索引 | **派生索引**（可重建） |

推论：理论上 `MinIO + PostgreSQL` 可以重建 Milvus 索引；业务状态绝不能只存在 Milvus。

## 5. 统一文档模型（RAG Core 不感知格式）

RAG 核心不应该关心原始格式。所有格式都先转成统一模型：

```text
任何格式 → DocumentParser → ParsedDocument → Block[]
```

后续 Chunk / Embedding / Index / Retrieval 只依赖统一模型。

禁止在业务代码里到处写：

```python
if extension == ".pdf":
    ...
elif extension == ".docx":
    ...
```

而应该：

```python
parser = parser_registry.resolve(source)
parsed_document = await parser.parse(source)
```

## 6. 状态一致性：未完成的文档绝不可用

文档只有 **Parse → Chunk → Embed → Index 全部成功**后，才进入 `READY`：

```text
PENDING → PARSING → CHUNKING → EMBEDDING → INDEXING → READY
                                    任一环节失败 → FAILED
```

- 失败时保留**可理解的状态**和原文件，便于排查与重建
- 不做复杂分布式事务，而是「明确失败状态 + 补偿操作」

## 7. 幂等性

可能被重试的操作都要尽量幂等：

- 稳定 ID（Chunk ID 由内容生成，重试结果一致）
- 向量 Upsert 按稳定 ID 覆盖，不产生重复
- 删除文档按 document_id 幂等

## 8. 外部服务默认不可靠

PostgreSQL、MinIO、Milvus、Embedding、LLM、OCR 都可能失败。处理原则：

- 设置超时
- 有限重试（只重试「很可能是临时故障」的问题，如网络超时）
- 禁止无限重试
- 失败留下可追踪状态

```text
✅ 重试：Network Timeout、Temporary Service Unavailable
❌ 不重试：参数错误、文件损坏、不支持格式
```

## 9. 安全边界

- 所有上传文件视为**不可信**：校验扩展名、大小、MIME、文件名；不执行上传内容
- 原始文件用**系统生成的 Object Key**保存，不用用户文件名拼路径（防路径穿越）
- 知识库文档内容和 LLM 输出都视为**不可信输入**：文档中的文字不能覆盖系统 Prompt
- 不提交 API Key / Secret；`.env.example` 只放占位值

## 10. 性能：先正确，后优化

优先级：

```text
正确性 > 清晰度 > 可维护性 > 健壮性 > 可测试性 > 性能
```

但避免明显问题：

- 不产生 N+1 查询（检索命中后直接带文件名，不再逐条查库）
- 不做「每个 Chunk 单独发一次 Embedding 请求」（按 Batch 批量）
- 不在每次请求时重新加载模型（复用客户端）
- 大文件不无脑全部读入内存（有界读取、流式处理）

## 11. 版本纪律

开发时必须遵守当前版本范围，**不要因为未来可能需要就提前实现未来功能**。

例如 V2 已经实现了异步 Worker（因为产品明确要求上传立即返回），但**不**引入：

```text
Kafka / 分布式调度器 / ACL / Reranker / LangGraph
```

因为这些没有当前真实需求。

## 12. 每一个重要行为都应该可测试

- 重要模块必须有单元测试
- 测试验证**行为**，而不是为了覆盖率
- Retrieval 必须能不依赖 LLM 单独测试

## 小结

如果只记一句话，记住这句：

> **使用能够正确解决当前问题的最简单架构，同时保留项目已经明确需要的长期扩展能力。**
