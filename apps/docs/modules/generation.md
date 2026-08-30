# Generation 文本生成

代码位置：`src/ultimate_rag/generation/bailian.py`

## 1. 这一层是什么

Generation 把「检索到的证据」交给大模型生成最终答案。本项目的 `BailianLLMClient` 是百炼（DashScope）的 OpenAI-Compatible Chat Completions 适配器，同时支持**完整答案**和**真实增量流**两种消费方式。

## 2. 核心类：BailianLLMClient

```python
BailianLLMClient(
    api_key="...",                # 百炼 API Key
    base_url="...",               # DashScope OpenAI 兼容端点
    model="qwen-plus",            # 默认模型
    timeout=60.0,                 # 有界超时
)
```

客户端在 FastAPI Lifespan 中创建一次并**跨请求复用**，避免每次问答重建连接池。

## 3. 两种方法

### generate —— 完整答案

```python
async def generate(self, system_prompt: str, user_prompt: str) -> str:
    response = await self._client.chat.completions.create(
        model=self._model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("LLM returned an empty answer")
    return content
```

- **`temperature=0.1`**：降低企业知识问答的随机发挥，让相同证据下回答更稳定
  - 注意：它是「稳定性参数」，**不是安全边界**。事实约束仍由系统 Prompt、检索证据和 Citation 共同保证
- 空答案显式报错，不静默返回

### stream —— 真实增量流

```python
async def stream(self, system_prompt, user_prompt) -> AsyncIterator[str]:
    response = await self._client.chat.completions.create(..., stream=True)
    has_content = False
    try:
        async for chunk in response:
            content = chunk.choices[0].delta.content if chunk.choices else None
            if content:
                has_content = True
                yield content
    finally:
        await response.close()

    if not has_content:
        raise RuntimeError("LLM returned an empty answer stream")
```

- **原生 `stream=True`**：首个 token 尽早到达浏览器，感知延迟更低，也能如实反映上游失败与中断
- 跳过只携带角色/结束原因/usage 的**空 Chunk**，避免向 HTTP 层发送大量空 SSE 帧
- `finally` 中显式 `close()`：浏览器取消请求时仍释放底层 HTTP 连接

## 4. 边界：什么不在这一层

| 本模块负责 | 本模块不负责 |
|---|---|
| 模型协议（请求/响应格式） | 检索（RetrievalService） |
| 有界超时 | 上下文预算（ContextBuilder） |
| 响应内容校验（空答案拒绝） | Prompt Injection 防护（系统 Prompt 由 RAGService 组装） |
| 连接池复用 | Citation 构造（由应用层从 RetrievalResult 构造） |
| | AI SDK UI Stream 编码（前端） |

## 5. 失败行为

- 网络、鉴权、限流、上游协议失败：**保留 SDK 异常原样上抛**（`openai.OpenAIError`）
- **不做无限重试**（百炼是远程服务，可能超时、限流、中途断流）
- 调用方把流式阶段异常转换为**不泄露凭据**的用户错误
- 完整答案为空 / 流全程无文本 → `RuntimeError`，上层可感知并降级

## 6. 为什么用百炼而不是自建模型

V2 当前接入百炼 `qwen-plus`：

- 推理质量稳定，免去模型部署运维
- 提供 OpenAI 兼容协议，SDK 生态成熟
- 只改 `model` / `base_url` / `api_key` 即可切换为其他 OpenAI 兼容服务

## 下一步

- 生成之前如何准备 Prompt → [Application 应用层](/modules/application)
- 看检索 → 生成全流程 → [检索问答全流程](/workflows/query)
