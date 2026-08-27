"""检索结果到模型上下文的确定性构造逻辑。

该模块不调用模型，也不改变召回顺序；它只负责来源标签、格式化和保守字符预算。
"""

from ultimate_rag.domain.models import RetrievalResult


class ContextBuilder:
    """把有序检索结果格式化为带来源边界的有限上下文。"""

    def __init__(self, max_chars: int = 12000) -> None:
        """配置单次生成允许使用的最大上下文字符数。"""
        self._max_chars = max_chars

    def build(self, results: list[RetrievalResult]) -> str:
        """按召回顺序拼接结果，超出预算时截断末尾而不重排证据。"""
        sections: list[str] = []
        used_chars = 0
        for index, result in enumerate(results, start=1):
            heading = " > ".join(result.heading_path) or "未命名章节"
            section = (
                f"[来源 {index}]\n"
                f"文档：{result.filename}\n"
                f"章节：{heading}\n"
                f"内容：\n{result.content}"
            )
            remaining = self._max_chars - used_chars
            if remaining <= 0:
                break
            if len(section) > remaining:
                section = section[:remaining]
            sections.append(section)
            used_chars += len(section)
        return "\n\n---\n\n".join(sections)
