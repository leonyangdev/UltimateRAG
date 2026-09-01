"""阿里云百炼文本生成适配器。

模块职责：
    把领域层 ``LLMClient`` 端口映射到百炼的 OpenAI-Compatible Chat Completions API，
    同时提供完整答案与真实增量流两种消费方式。

架构边界：
    本模块只处理模型协议、超时和响应内容校验。检索、上下文预算、Prompt Injection 防护、
    Citation 构造与 AI SDK UI Stream 编码均由上层负责。

设计背景：
    非流式接口保留给测试和后台任务；聊天界面使用模型原生 ``stream=True``，让首个 token
    可以尽早到达浏览器。相比生成完整答案后再由前端逐字展示，这种实现既降低感知延迟，
    也能如实反映上游模型的失败与中断。

外部约束：
    百炼接口是远程服务，可能超时、限流或中途断流。客户端使用 Settings 提供的有界超时，
    本适配器不做无限重试；调用方必须把流式阶段异常转换为不泄露凭据的用户错误。
"""

from collections.abc import AsyncIterator

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam


class BailianLLMClient:
    """使用可配置百炼模型生成最终 RAG 答案。

    客户端在 FastAPI Lifespan 中创建一次并跨请求复用，避免每次问答重新建立连接池。
    本类不拥有检索状态，也不缓存对话，因而不会让不同知识库之间发生上下文串扰。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        """创建可复用异步客户端，并绑定单个可配置文本模型。"""
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> str:
        """执行一次低温度完整生成，并拒绝模型返回空答案。

        ``temperature=0.1`` 用于减少企业知识问答中的随机发挥，使相同证据下的回答更稳定；
        它不是安全边界，事实约束仍由系统 Prompt、检索证据和 Citation 共同保证。
        """
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if max_tokens is None:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.1,
            )
        else:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.1,
                max_tokens=max_tokens,
            )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content:
            raise RuntimeError("LLM returned an empty answer")
        return content

    async def stream(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        """通过百炼原生 Streaming API 逐段产出答案文本。

        Yields:
            模型返回的非空文本增量；保持供应商原始顺序，不在适配器内重新分词或合并。

        Raises:
            RuntimeError: 流正常结束但从未返回文本时抛出。
            openai.OpenAIError: 网络、鉴权、限流或上游协议失败时保留 SDK 异常。

        Notes:
            响应流在 ``finally`` 中显式关闭。浏览器取消请求时，上层异步迭代会被取消，
            这里仍会释放底层 HTTP 连接，避免连接长期占用连接池。
        """

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            stream=True,
        )
        has_content = False
        try:
            async for chunk in response:
                # OpenAI-Compatible 流可能包含只携带角色、结束原因或 usage 的 Chunk。
                # 这些事件没有可展示文本，直接跳过可避免向 HTTP 层发送大量空 SSE 帧。
                content = chunk.choices[0].delta.content if chunk.choices else None
                if content:
                    has_content = True
                    yield content
        finally:
            await response.close()

        if not has_content:
            raise RuntimeError("LLM returned an empty answer stream")
