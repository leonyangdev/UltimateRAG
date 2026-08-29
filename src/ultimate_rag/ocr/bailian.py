"""阿里云百炼 Qwen-OCR 适配器。

模块职责：
    把图片字节编码为 Data URL，通过百炼 OpenAI-Compatible Chat Completions API 提取文本。

架构边界：
    本模块只实现 ``OCRClient`` 外部协议，不判断文件扩展名、不渲染 PDF，也不创建领域 Block。
    图片验证、页码定位和 Parser 选择分别由 Image/PDF Parser 与 Parser Registry 负责。

外部约束：
    百炼 OpenAI 兼容接口的 Base64 图片原文件必须小于 7 MB。调用使用配置提供的有界超时，
    不做无限重试；空响应和超限输入都显式失败，避免把空 OCR 文本继续送入 Embedding。
"""

import base64
from typing import cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam


class BailianOCRClient:
    """使用 Qwen-OCR 把单张图片识别为保持阅读顺序的纯文本。"""

    _PROMPT = (
        "请按自然阅读顺序提取图片中的全部可见文字。保留标题、段落、列表和表格的行列关系，"
        "表格使用 Markdown 表格表示。只输出识别结果，不要解释、总结或编造不可见内容。"
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_image_bytes: int,
        timeout: float,
    ) -> None:
        """创建可复用客户端并固定 OCR 模型与单图字节上限。"""

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model
        self._max_image_bytes = max_image_bytes

    async def extract_text(self, image: bytes, mime_type: str) -> str:
        """识别图片并拒绝空内容或超过百炼 Base64 接口限制的输入。

        Args:
            image: 已经由 Parser 验证过格式的图片原始字节。
            mime_type: Data URL 使用的标准图片 MIME 类型。

        Returns:
            去除首尾空白但保留内部换行的 OCR 文本。

        Raises:
            ValueError: 图片为空、MIME 非图片或超过配置上限。
            RuntimeError: 模型没有返回可索引文字。
        """

        if not image:
            raise ValueError("OCR 图片不能为空")
        if not mime_type.startswith("image/"):
            raise ValueError(f"OCR 只接受图片 MIME：{mime_type}")
        if len(image) > self._max_image_bytes:
            limit_mb = self._max_image_bytes // (1024 * 1024)
            raise ValueError(f"OCR 图片不能超过 {limit_mb} MB")

        data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
        # OpenAI SDK 的 TypedDict 联合类型无法从嵌套字面量稳定推导；cast 只隔离 SDK 类型，
        # 请求结构仍严格遵循百炼 image_url + text 的 OpenAI-Compatible 协议。
        messages = cast(
            list[ChatCompletionMessageParam],
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": self._PROMPT},
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
            raise RuntimeError("OCR model returned empty text")
        return content.strip()
