"""阿里云百炼 Qwen-OCR 适配器。

模块职责：
    把图片字节编码为 Data URL，通过百炼 OpenAI-Compatible Chat Completions API 提取文本。

架构边界：
    本模块只实现 ``OCRClient`` 外部协议，不判断文件扩展名、不渲染 PDF，也不创建领域 Block。
    图片验证、页码定位和 Parser 选择分别由 Image/PDF Parser 与 Parser Registry 负责。

外部约束：
    百炼 OpenAI 兼容接口的 Base64 图片原文件必须小于 7 MB。调用使用配置提供的有界超时，
    不做无限重试；空响应和超限输入都显式失败，避免把空 OCR 文本继续送入 Embedding。

手动验证：
    本模块附带命令行入口，可直接对单张图片调用百炼 OCR 验证连通性：
    ``uv run python src/ultimate_rag/ocr/bailian.py <图片路径>``
    密钥、模型与超时读取自集中配置（环境变量或 ``.env``），会真实产生少量模型用量。
"""

import argparse
import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from ultimate_rag.config import get_settings


class BailianOCRClient:
    """使用 Qwen-OCR 把单张图片识别为保持阅读顺序的纯文本。"""

    _PROMPT = (
        "请按自然阅读顺序提取图片中的全部可见文字。保留标题、段落、列表和表格的行列关系，"
        "只有原图明确存在行列结构时才使用 Markdown 表格，禁止输出空行、空单元格组成的伪表格。"
        "示意图只提取文字，不要猜测图形关系。只输出识别结果，不要解释、总结或编造不可见内容。"
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_image_bytes: int,
        max_output_tokens: int,
        timeout: float,
    ) -> None:
        """创建可复用客户端并固定 OCR 模型与单图字节上限。"""

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model
        self._max_image_bytes = max_image_bytes
        self._max_output_tokens = max_output_tokens

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
            # 有界输出可防止模型把图形边框误识别为数千行空表格并污染后续 Chunk。
            max_tokens=self._max_output_tokens,
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError("OCR model returned empty text")
        return content.strip()




# uv run python src/ultimate_rag/ocr/bailian.py ./data/1.png
def main() -> None:
    """命令行验证入口：对单张本地图片调用百炼 OCR 并打印识别文本。

    该入口只用于开发期人工验证模型连通性与识别效果，不属于生产调用链；
    生产路径统一经过 runtime.py 装配的 Parser。

    Raises:
        SystemExit: 扩展名无法映射为图片 MIME 类型时终止。
        ValueError: 图片为空或超过配置的字节上限。
        RuntimeError: 百炼返回空文本，或网络 / 鉴权失败（由 OpenAI SDK 异常上抛）。

    Side Effects:
        读取本地图片文件，真实调用百炼 Chat Completions 接口并产生模型用量。
    """

    parser = argparse.ArgumentParser(description="用百炼 Qwen-OCR 识别一张本地图片")
    parser.add_argument("image", help="待识别的图片文件路径，例如 ./sample.png")
    arguments = parser.parse_args()

    # 扩展名到 MIME 的映射交给标准库；识别失败直接终止，避免把错误 MIME 送进 Data URL。
    # 这里的扩展名判断仅服务于命令行入口，生产上传链路仍由 IngestionService 统一校验。
    mime_type, _ = mimetypes.guess_type(arguments.image)
    if not mime_type or not mime_type.startswith("image/"):
        raise SystemExit(f"无法从扩展名识别图片 MIME 类型：{arguments.image}")

    # 客户端参数与 runtime.py 的生产装配保持同源，验证结论才能代表 Worker 的真实行为。
    settings = get_settings()
    client = BailianOCRClient(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.ocr_model,
        max_image_bytes=settings.ocr_max_image_bytes,
        max_output_tokens=settings.ocr_max_output_tokens,
        timeout=settings.model_timeout_seconds,
    )

    # extract_text 是异步方法；脚本只发起一次调用，用 asyncio.run 驱动即可，无需常驻事件循环。
    image = Path(arguments.image).read_bytes()
    text = asyncio.run(client.extract_text(image, mime_type))
    print(text)


if __name__ == "__main__":
    main()
