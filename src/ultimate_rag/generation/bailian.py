"""阿里云百炼文本生成适配器。

本模块仅封装 OpenAI 兼容 Chat Completions；检索、上下文预算和引用构造属于应用层职责。
"""

from openai import AsyncOpenAI


class BailianLLMClient:
    """使用可配置百炼模型生成最终 RAG 答案。"""

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

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """执行一次低温度生成，并拒绝模型返回的空答案。"""
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
