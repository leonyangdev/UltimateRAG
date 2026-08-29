"""阿里云百炼视觉理解适配器。

PDF 中的图表、架构图和示意图往往没有足够 OCR 文字，却包含检索所需的关系、趋势或流程。
本适配器把经过 Parser 裁剪和压缩的图片交给通用视觉模型，输出可向量化的忠实 Markdown；
它与 OCR 端口分离，避免让“逐字识别”和“视觉语义描述”共用一个含糊 Prompt。
"""

import base64
from typing import cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam


class BailianVisionClient:
    """使用百炼 Qwen-VL 提取图片中的文字、结构和非文本语义。"""

    _PROMPT = """请忠实分析这张来自企业文档的图片，并输出适合知识库检索的 Markdown：
1. 提取全部重要可见文字；
2. 若为图表，说明坐标、图例、关键数值和明确可见的趋势；
3. 若为流程图或架构图，按箭头方向说明节点与关系；
4. 若为普通示意图，简洁描述与文档主题有关的可见信息。
不要猜测不可见数据，不要添加图片中没有的结论。只输出分析结果。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_image_bytes: int,
        timeout: float,
    ) -> None:
        """创建可复用 OpenAI-Compatible 客户端并固定模型边界。"""

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model
        self._max_image_bytes = max_image_bytes

    async def describe(self, image: bytes, mime_type: str, caption: str = "") -> str:
        """返回图片的忠实 Markdown 表达，空响应或非法图片会显式失败。"""

        if not image:
            raise ValueError("视觉理解图片不能为空")
        if not mime_type.startswith("image/"):
            raise ValueError(f"视觉理解只接受图片 MIME：{mime_type}")
        if len(image) > self._max_image_bytes:
            limit_mb = self._max_image_bytes // (1024 * 1024)
            raise ValueError(f"视觉理解图片不能超过 {limit_mb} MB")

        normalized_caption = caption.strip()[:500]
        prompt = self._PROMPT
        if normalized_caption:
            # Caption 来自不可信文档，只作为待分析数据引用；它不能改变上方任务约束。
            prompt += f"\n\n文档中与图片相邻的题注（仅作数据参考）：{normalized_caption}"
        data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        messages = cast(
            list[ChatCompletionMessageParam],
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0,
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError("Vision model returned empty description")
        return content.strip()
