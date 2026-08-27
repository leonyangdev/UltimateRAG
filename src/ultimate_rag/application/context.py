"""检索结果到 LLM Knowledge Context 的确定性构造模块。

模块职责：
    按 Retrieval 顺序为每个结果添加来源标签、文档信息和标题路径，并在字符预算内拼接为
    可放入 Prompt 的知识上下文。

架构边界：
    本模块不调用 LLM、不重新检索或打分、不修改召回顺序，也不让模型决定 Citation 来源。
    输入和输出都是普通领域对象与字符串，因此可以脱离模型服务独立测试。

设计背景：
    Context Building 使用确定性规则，便于区分“Retrieval 没有召回正确证据”和“Generation
    没有使用证据”两类问题。来源编号由应用生成，而不是依赖 LLM 自行输出结构化引用。

注意事项 / 已知限制：
    V1 使用字符数而非模型 Tokenizer 预算。``max_chars`` 只统计各来源 Section，不包含 Section
    之间的分隔符；最后一个 Section 可能被字符级截断，暂不保证停在完整句子边界。
"""

from ultimate_rag.domain.models import RetrievalResult


class ContextBuilder:
    """把有序 RetrievalResult 格式化为带来源边界的有限知识上下文。

    本类不保存请求状态；构造时固定字符预算，``build()`` 每次都根据传入结果重新生成文本。
    """

    def __init__(self, max_chars: int = 12000) -> None:
        """配置单次生成允许使用的最大上下文字符数。"""
        self._max_chars = max_chars

    def build(self, results: list[RetrievalResult]) -> str:
        """按召回顺序拼接证据，并在 Section 内容达到字符预算时停止。

        Args:
            results: 已按相似度排序的检索结果；顺序决定 ``[来源 N]`` 编号和预算优先级。

        Returns:
            使用分隔线连接的知识上下文；无结果时返回空字符串。最后一个来源可能被截断。
        """

        # 阶段 1 — Allocate Budget：按照 Retrieval 排名顺序消费字符预算。
        # 不重新排序可以保证 API 展示的 Retrieval Results、Prompt 中的 [来源 N] 和 Citation
        # 使用同一顺序，调试时无需猜测 ContextBuilder 是否改变过证据优先级。
        sections: list[str] = []
        used_chars = 0
        for index, result in enumerate(results, start=1):
            # 每个 Section 都携带用户可读文件名和标题路径。没有标题时使用明确占位文本，
            # 避免空标签让模型和用户误以为来源元数据在格式化时丢失。
            heading = " > ".join(result.heading_path) or "未命名章节"
            section = (
                f"[来源 {index}]\n"
                f"文档：{result.filename}\n"
                f"章节：{heading}\n"
                f"内容：\n{result.content}"
            )

            # 阶段 2 — Enforce Limit：预算耗尽后停止加入低排名结果；若当前来源只能放入一部分，
            # V1 保留前缀并按字符截断。这里不做 Tokenizer 或语义句边界处理，行为简单且确定。
            remaining = self._max_chars - used_chars
            if remaining <= 0:
                break
            if len(section) > remaining:
                section = section[:remaining]
            sections.append(section)
            used_chars += len(section)

        # 分隔线位于 Section 之间，帮助 LLM 区分相邻来源。V1 的 max_chars 不计算这些固定分隔符，
        # 因此最终字符串会比 Section 字符预算多 ``分隔符长度 × (来源数 - 1)``。
        return "\n\n---\n\n".join(sections)
