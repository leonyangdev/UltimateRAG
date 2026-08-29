# Embedding 向量化

代码位置：`src/ultimate_rag/embeddings/bailian.py`

## 1. 这一层是什么

Embedding 把文本转成**稠密向量**，是实现语义检索的基础。本项目通过 `BailianEmbedder` 适配阿里云百炼的 OpenAI 兼容 Embedding API。

## 2. 核心类：BailianEmbedder

实现 `Embedder` 端口：

```python
class Embedder(Protocol):
    async def embed_documents(self, texts) -> list[list[float]]: ...   # 批量
    async def embed_query(self, query) -> list[float]: ...             # 单条查询
```

实例固定了模型、维度和 Batch 大小：

```python
BailianEmbedder(
    model="text-embedding-v4",   # 默认模型
    dimension=1024,              # 向量维度
    batch_size=10,               # 每批文本数
)
```

## 3. 三个职责边界

本模块**只负责**：

- 模型协议（把文本发给百炼）
- Batch 分片（供应商限制单批数量）
- 响应校验（数量、维度必须符合契约）

本模块**不负责**：

- 如何切块（那是 Chunker 的事）
- 写入 Milvus（那是 VectorStore 的事）
- 编排文档状态（那是 Application 的事）

## 4. 关键设计：批量 + 顺序恢复

```python
async def embed_documents(self, texts):
    if not texts:
        return []                       # 空输入不调用付费 API

    embeddings = []
    # 阶段 1：按配置上限分批，串行调用外部 API
    for start in range(0, len(texts), self._batch_size):
        batch = list(texts[start:start + self._batch_size])
        response = await self._client.embeddings.create(
            model=self._model, input=batch, dimensions=self._dimension, ...
        )

        # 阶段 2：协议用 index 关联向量与输入，必须按 index 排序恢复顺序
        ordered = sorted(response.data, key=lambda item: item.index)
        embeddings.extend([list(item.embedding) for item in ordered])

    # 阶段 3：校验总数与维度，出错必须在进入 Milvus 前失败
    self._validate(embeddings, len(texts))
    return embeddings
```

关键点：

- **输入顺序 = 输出顺序**是硬契约。用 `index` 排序而不是依赖服务端返回顺序
- 串行执行 Batch（V1 简化），费用和失败位置可预测
- 空输入本地直接返回，不产生无意义的计费请求

## 5. 形状校验（防数据污染）

```python
def _validate(self, embeddings, expected_count):
    if len(embeddings) != expected_count:
        raise RuntimeError("Embedding service returned wrong vector count")
    if any(len(v) != self._dimension for v in embeddings):
        raise RuntimeError("Embedding service returned unexpected vector dimension")
```

- 数量或维度异常直接失败，**阻止错误向量进入 Milvus**
- 否则可能发生「Chunk 绑错向量」或「Milvus Schema 拒绝写入」

## 6. 查询向量化

```python
async def embed_query(self, query):
    result = await self.embed_documents([query])
    return result[0]
```

复用同一个 `embed_documents`，保证**查询与文档使用完全相同的模型和维度**——这是向量空间一致性的前提。

## 7. 失败行为

- 网络、超时、协议异常**原样上抛**
- Adapter 内**不自动重试**（避免对计费请求隐式重复调用，也避免对非临时错误反复尝试）
- 重试决策交给上层（Application/Worker）

## 8. 费用与限制

- 调用百炼 Embedding API 会**产生费用**
- 空输入在本地返回，不花钱
- 批量调用（默认每批 10 条）减少请求次数

## 下一步

- 向量存到哪里、怎么检索 → [VectorStore 向量库](/modules/vectorstore)
