"""V3 高级检索应用编排。

模块职责：
    显式执行 ``Validate/Filter → Rewrite → Dense + Sparse → RRF → Rerank → Small2Big``，
    返回最终证据与可解释的阶段信息。

架构边界：
    本模块依赖 UltimateRAG 自有领域端口和 PostgreSQL Repository，不知道百炼或 PyMilvus 的
    JSON/Hit 格式；不构造最终 Prompt，也不调用答案生成模型。RRF 与 Parent 扩展是确定性规则，
    Retrieval 因而可以脱离生成阶段单独评估。

设计背景：
    Dense 擅长语义近似，BM25 擅长型号、缩写和精确词。两者原始分数不可直接相加，因此多列表
    使用 RRF；随后 Reranker 只处理有限候选。查询改写始终保留原查询，辅助模型或某一检索通道
    故障时会记录降级原因并尽量继续，而不是让可用的基础召回一起失败。

默认基线：
    候选宽度 30、RRF ``k=60``、最终 ``top_k=5``、Parent 相邻窗口 1。它们来自公开实践与原始
    RRF 论文，只是工程起点；仓库提供离线指标工具，生产参数必须由真实文档和查询集验证。
"""

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from dataclasses import replace

from ultimate_rag.application.summary_retrieval import (
    SummaryEvidenceSelector,
    detect_retrieval_intent,
)
from ultimate_rag.domain.models import (
    BlockType,
    Chunk,
    RetrievalIntent,
    RetrievalMode,
    RetrievalOptions,
    RetrievalResult,
    RetrievalRun,
    RetrievalTrace,
)
from ultimate_rag.domain.ports import Embedder, QueryRewriter, Reranker, VectorStore
from ultimate_rag.infrastructure.database.repository import Repository
from ultimate_rag.retrieval.fusion import reciprocal_rank_fusion

logger = logging.getLogger(__name__)


class RetrievalService:
    """组合可替换召回/重排端口，并以 PostgreSQL 状态约束最终结果。"""

    def __init__(
        self,
        embedder: Embedder,
        vector_store: VectorStore,
        repository: Repository,
        *,
        query_rewriter: QueryRewriter | None = None,
        reranker: Reranker | None = None,
        default_options: RetrievalOptions | None = None,
        rrf_k: int = 60,
        parent_window: int = 1,
        parent_max_tokens: int = 1536,
        summary_max_chunks: int = 24,
        summary_max_tokens: int = 16_000,
    ) -> None:
        """注入高级检索组件并验证不会绕过 API 的部署级边界。"""

        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        if not 0 <= parent_window <= 3:
            raise ValueError("parent_window must be between 0 and 3")
        if parent_max_tokens < 64:
            raise ValueError("parent_max_tokens must be at least 64")
        self._embedder = embedder
        self._vector_store = vector_store
        self._repository = repository
        self._query_rewriter = query_rewriter
        self._reranker = reranker
        # 直接构造 Service 的旧调用方继续得到 Dense 行为；V3 Composition Root 会显式传入
        # Hybrid 默认值，避免一个隐式默认在库升级后改变所有嵌入式调用方的费用与延迟。
        self._default_options = default_options or RetrievalOptions(
            mode=RetrievalMode.DENSE,
            enable_query_rewrite=False,
            enable_rerank=False,
            enable_parent_expansion=False,
        )
        self._rrf_k = rrf_k
        self._parent_window = parent_window
        self._parent_max_tokens = parent_max_tokens
        self._summary_selector = SummaryEvidenceSelector(
            max_chunks=summary_max_chunks,
            max_tokens=summary_max_tokens,
        )

    async def search(
        self,
        knowledge_base_id: str,
        query: str,
        top_k: int,
        options: RetrievalOptions | None = None,
    ) -> list[RetrievalResult]:
        """兼容 V1/V2 的列表返回接口；高级调用方应使用 :meth:`retrieve` 查看 Trace。"""

        run = await self.retrieve(knowledge_base_id, query, top_k, options)
        return list(run.results)

    async def retrieve(
        self,
        knowledge_base_id: str,
        query: str,
        top_k: int,
        options: RetrievalOptions | None = None,
        *,
        conversation_context: str | None = None,
    ) -> RetrievalRun:
        """执行一次可降级、可解释的高级检索。

        Args:
            knowledge_base_id: 强制写入每次 Milvus Search 的知识库隔离条件。
            query: 用户原始问题；Query Rewrite 只能补充它，不能替代它。
            top_k: 最终进入 Context 的最大命中数，范围与公开 API 一致为 1～20。
            options: 模式、候选宽度、可选阶段和文档 ID 白名单。

        Returns:
            最终有序结果和轻量 Trace。无 READY 文档或过滤交集为空时，不调用任何模型服务。

        Raises:
            ValueError: 直接调用时传入空查询或越界 ``top_k``。
            Exception: 所有请求的召回通道都失败，或 PostgreSQL 事实读取失败。
        """

        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("query cannot be empty")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        selected = options or self._default_options
        candidate_k = max(top_k, selected.candidate_k)
        fallback_reasons: list[str] = []

        # 阶段 1 — Fact Filter：READY 状态必须由 PostgreSQL 决定。显式 document_ids 先与
        # 当前知识库 READY 集合求交，再下推给两个 Milvus Collection，避免召回后才过滤浪费配额。
        ready_document_ids = await self._repository.list_ready_document_ids(
            knowledge_base_id,
            selected.document_ids,
        )
        if not ready_document_ids:
            return self._empty_run(normalized_query, selected, fallback_reasons)
        pushed_document_ids = tuple(sorted(ready_document_ids)) if selected.document_ids else ()

        # 全文总结不依赖一句泛化 Query 与局部 Chunk 的相似度。直接覆盖 READY 文档结构，
        # 可稳定带回摘要、方法、实验和结论，并避免参考文献被 Reranker 当作关键词密集证据。
        intent = detect_retrieval_intent(normalized_query)
        if intent is RetrievalIntent.DOCUMENT_SUMMARY:
            chunks = await self._repository.list_ready_chunks(
                knowledge_base_id,
                tuple(sorted(ready_document_ids)),
            )
            results = self._summary_selector.select(chunks)
            try:
                results = await self._attach_evidence_metadata(results)
            except Exception:
                # 全文总结仍以文本覆盖为核心；Asset 元数据不可用时保留可回答的结构证据。
                logger.warning("Summary evidence metadata enrichment failed", exc_info=True)
            return RetrievalRun(
                results=tuple(results),
                trace=RetrievalTrace(
                    original_query=normalized_query,
                    query_variants=(normalized_query,),
                    mode=selected.mode,
                    candidate_count=len(chunks),
                    result_count=len(results),
                    rewrite_applied=False,
                    rerank_applied=False,
                    parent_expansion_applied=False,
                    intent=intent,
                    strategy="structural_coverage",
                ),
            )

        # 阶段 2 — Query Rewrite：原查询永远位于 variants[0]。辅助模型返回相同文本、无输出或
        # 调用失败时都不会丢失基础查询；异常被记录为显式降级，而不是静默吞掉。
        query_variants = [normalized_query]
        if selected.enable_query_rewrite:
            if self._query_rewriter is None:
                fallback_reasons.append("query_rewriter_unavailable")
            else:
                try:
                    if conversation_context:
                        rewritten = await self._query_rewriter.rewrite(
                            normalized_query,
                            conversation_context,
                        )
                    else:
                        # 不携带会话时沿用原端口调用形状，避免要求已有自定义 Rewriter
                        # 为一个永远为空的可选参数同步升级。
                        rewritten = await self._query_rewriter.rewrite(normalized_query)
                    if rewritten:
                        normalized_rewritten = " ".join(rewritten.split())
                        if len(normalized_rewritten) > 4000:
                            raise ValueError("rewritten query exceeds the request limit")
                        if (
                            normalized_rewritten
                            and normalized_rewritten.casefold() != normalized_query.casefold()
                        ):
                            query_variants.append(normalized_rewritten)
                except Exception:
                    logger.warning(
                        "Query rewrite failed; continuing with the original query",
                        exc_info=True,
                    )
                    fallback_reasons.append("query_rewrite_failed")

        # 阶段 3 — Broad Recall：Dense/Sparse 及至多两个查询变体彼此独立，使用并发减少串行
        # 网络延迟。gather(return_exceptions=True) 让 Hybrid 在单通道故障时仍能使用另一通道。
        tasks = self._recall_tasks(
            knowledge_base_id,
            query_variants,
            candidate_k,
            pushed_document_ids,
            selected.mode,
        )
        task_results = await asyncio.gather(
            *(task for _, task in tasks),
            return_exceptions=True,
        )
        rankings: list[tuple[str, list[RetrievalResult]]] = []
        failures: list[BaseException] = []
        for (source, _), value in zip(tasks, task_results, strict=True):
            # 任务取消属于上层生命周期控制，不能被误判为某一路召回失败后继续执行。
            # 否则客户端断开或服务关闭时，流水线可能仍在访问外部依赖并产生无效工作。
            if isinstance(value, asyncio.CancelledError):
                raise value
            if isinstance(value, BaseException):
                failures.append(value)
                reason = (
                    "dense_retrieval_failed"
                    if source.startswith("dense")
                    else "sparse_retrieval_failed"
                )
                if reason not in fallback_reasons:
                    fallback_reasons.append(reason)
                logger.warning("Retrieval channel %s failed", source, exc_info=value)
                continue
            # Milvus 与 PostgreSQL 无跨存储事务。即使没有显式文档过滤，所有 Hit 仍要按
            # READY 事实二次过滤，避免 Worker 崩溃窗口中的半成品索引参与回答。
            rankings.append(
                (
                    source,
                    [result for result in value if result.document_id in ready_document_ids],
                )
            )
        if not rankings and failures:
            raise failures[0]

        non_empty_rankings = [(source, values) for source, values in rankings if values]
        if not non_empty_rankings:
            return self._empty_run(
                normalized_query,
                selected,
                fallback_reasons,
                query_variants=query_variants,
            )

        # 阶段 4 — Fusion：单列表保留原始分数；两个及以上列表使用 RRF，绝不直接相加
        # COSINE 与 BM25。融合后先截断候选宽度，避免异常查询把无界正文交给 Reranker。
        if len(non_empty_rankings) == 1:
            source, values = non_empty_rankings[0]
            candidates = [self._annotate_single_source(value, source) for value in values]
        else:
            candidates = reciprocal_rank_fusion(non_empty_rankings, rank_constant=self._rrf_k)
        candidates = candidates[:candidate_k]
        candidate_count = len(candidates)

        # 阶段 5 — Rerank：只让模型返回最终 top_k。失败时保持 RRF/单通道顺序，既可用又能
        # 从 Trace 看出没有实际执行重排。相关性分数仅在本请求候选集合内排序。
        rerank_applied = False
        if selected.enable_rerank and candidates:
            if self._reranker is None:
                fallback_reasons.append("reranker_unavailable")
            else:
                try:
                    candidates = await self._rerank(normalized_query, candidates, top_k)
                    rerank_applied = True
                except Exception:
                    logger.warning("Rerank failed; keeping fused ranking", exc_info=True)
                    fallback_reasons.append("rerank_failed")
        candidates = candidates[:top_k]

        # 阶段 6 — Small2Big：命中仍指向精确 Child Chunk，只把同一语义 Parent 的有限邻居
        # 放进 Context。扩展失败不应丢掉已经完成的检索结果，但必须留下可解释降级原因。
        parent_expansion_applied = False
        if selected.enable_parent_expansion and candidates and self._parent_window > 0:
            try:
                candidates, parent_expansion_applied = await self._expand_parent_context(candidates)
            except Exception:
                logger.warning(
                    "Parent context expansion failed; using matched chunks",
                    exc_info=True,
                )
                fallback_reasons.append("parent_expansion_failed")

        # Milvus 只保存检索必需字段；Block 类型与图片 Asset 属于 PostgreSQL/MinIO 事实。
        # 最终结果在此批量补齐，避免迁移现有向量集合或把内部 Object Key 放入派生索引。
        if candidates:
            try:
                candidates = await self._attach_evidence_metadata(candidates)
            except Exception:
                # 类型标签与预览属于解释层增强，读取失败不能让已完成的核心问答不可用。
                logger.warning("Chunk evidence metadata enrichment failed", exc_info=True)

        trace = RetrievalTrace(
            original_query=normalized_query,
            query_variants=tuple(query_variants),
            mode=selected.mode,
            candidate_count=candidate_count,
            result_count=len(candidates),
            rewrite_applied=len(query_variants) > 1,
            rerank_applied=rerank_applied,
            parent_expansion_applied=parent_expansion_applied,
            fallback_reasons=tuple(fallback_reasons),
        )
        return RetrievalRun(results=tuple(candidates), trace=trace)

    def _recall_tasks(
        self,
        knowledge_base_id: str,
        query_variants: Sequence[str],
        candidate_k: int,
        document_ids: Sequence[str],
        mode: RetrievalMode,
    ) -> list[tuple[str, Awaitable[list[RetrievalResult]]]]:
        """建立有来源标签的召回任务；标签随后用于分数解释和 RRF。"""

        tasks: list[tuple[str, Awaitable[list[RetrievalResult]]]] = []
        for index, variant in enumerate(query_variants):
            variant_name = "original" if index == 0 else "rewrite"
            if mode in {RetrievalMode.DENSE, RetrievalMode.HYBRID}:
                tasks.append(
                    (
                        f"dense:{variant_name}",
                        self._dense_search(
                            knowledge_base_id,
                            variant,
                            candidate_k,
                            document_ids,
                        ),
                    )
                )
            if mode in {RetrievalMode.SPARSE, RetrievalMode.HYBRID}:
                tasks.append(
                    (
                        f"sparse:{variant_name}",
                        self._vector_store.search_sparse(
                            variant,
                            knowledge_base_id,
                            candidate_k,
                            document_ids,
                        ),
                    )
                )
        return tasks

    async def _dense_search(
        self,
        knowledge_base_id: str,
        query: str,
        candidate_k: int,
        document_ids: Sequence[str],
    ) -> list[RetrievalResult]:
        """保证 Query 与入库文档沿用同一 Embedder，再执行 Dense Search。"""

        query_vector = await self._embedder.embed_query(query)
        return await self._vector_store.search(
            query_vector,
            knowledge_base_id,
            candidate_k,
            document_ids,
        )

    async def _rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        top_k: int,
    ) -> list[RetrievalResult]:
        """校验端口输出并把请求内 Rerank 分数映射回不可变候选。"""

        assert self._reranker is not None
        ranked = await self._reranker.rerank(query, candidates, top_k)
        candidates_by_id = {candidate.chunk_id: candidate for candidate in candidates}
        seen_ids: set[str] = set()
        results: list[RetrievalResult] = []
        for item in ranked:
            if item.chunk_id in seen_ids or item.chunk_id not in candidates_by_id:
                raise RuntimeError("Reranker returned an unknown or duplicate chunk ID")
            seen_ids.add(item.chunk_id)
            results.append(
                replace(
                    candidates_by_id[item.chunk_id],
                    score=item.score,
                    rerank_score=item.score,
                )
            )
        if not results:
            raise RuntimeError("Reranker returned no results")
        return results

    async def _expand_parent_context(
        self,
        results: list[RetrievalResult],
    ) -> tuple[list[RetrievalResult], bool]:
        """把每个 Child 命中扩展为同 Parent 的有界相邻内容。"""

        contexts = await self._repository.get_chunks_with_neighbors(
            [result.chunk_id for result in results],
            window=self._parent_window,
        )
        expanded: list[RetrievalResult] = []
        has_expansion = False
        for result in results:
            neighbors = contexts.get(result.chunk_id, [])
            matched = next((chunk for chunk in neighbors if chunk.id == result.chunk_id), None)
            if matched is None:
                expanded.append(result)
                continue
            siblings = [chunk for chunk in neighbors if self._same_parent(matched, chunk)]
            selected = self._select_parent_window(matched, siblings)
            if len(selected) <= 1:
                expanded.append(
                    replace(
                        result,
                        context_chunk_ids=(result.chunk_id,),
                        content_types=self._content_types(matched),
                    )
                )
                continue
            has_expansion = True
            expanded.append(
                replace(
                    result,
                    content="\n\n".join(chunk.content for chunk in selected),
                    matched_content=result.content,
                    context_chunk_ids=tuple(chunk.id for chunk in selected),
                    # 视觉证据锚定真正命中的 Child；邻居含图片不能把正文命中误标为图片。
                    content_types=self._content_types(matched),
                )
            )
        return expanded, has_expansion

    async def _attach_evidence_metadata(
        self,
        results: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        """批量补齐命中块类型与 Asset，避免 PostgreSQL N+1 查询。

        Chunk metadata 只保存稳定 Asset ID；图片 Object Key 等事实从 ``document_assets``
        读取。不存在的旧 Chunk/Asset 保持兼容空值，普通文本回答不受影响。
        """

        contexts = await self._repository.get_chunks_with_neighbors(
            [result.chunk_id for result in results],
            window=0,
        )
        matched_chunks: dict[str, Chunk] = {}
        all_asset_ids: list[str] = []
        for result in results:
            chunks = contexts.get(result.chunk_id, [])
            matched = next((chunk for chunk in chunks if chunk.id == result.chunk_id), None)
            if matched is None:
                continue
            matched_chunks[result.chunk_id] = matched
            all_asset_ids.extend(self._asset_ids(matched))
        assets_by_id = await self._repository.get_document_assets(all_asset_ids)

        enriched: list[RetrievalResult] = []
        for result in results:
            matched = matched_chunks.get(result.chunk_id)
            if matched is None:
                enriched.append(result)
                continue
            assets = tuple(
                asset
                for asset_id in self._asset_ids(matched)
                if (asset := assets_by_id.get(asset_id)) is not None
                and asset.document_id == result.document_id
            )
            enriched.append(
                replace(
                    result,
                    content_types=self._content_types(matched),
                    assets=assets,
                )
            )
        return enriched

    @staticmethod
    def _asset_ids(chunk: Chunk) -> tuple[str, ...]:
        """从 JSONB 恢复去重且保持 Parser 顺序的 Asset ID。"""

        raw_values = chunk.metadata.get("asset_ids")
        if not isinstance(raw_values, list):
            return ()
        return tuple(
            dict.fromkeys(value for value in raw_values if isinstance(value, str) and value.strip())
        )

    @staticmethod
    def _content_types(chunk: Chunk) -> tuple[BlockType, ...]:
        """把不可信 JSONB 字符串恢复为去重、稳定排序的领域枚举。"""

        raw_values = chunk.metadata.get("block_types")
        if not isinstance(raw_values, list):
            return ()
        values: set[BlockType] = set()
        for raw_value in raw_values:
            if not isinstance(raw_value, str):
                continue
            try:
                values.add(BlockType(raw_value))
            except ValueError:
                continue
        return tuple(sorted(values, key=lambda value: value.value))

    def _select_parent_window(self, matched: Chunk, siblings: Sequence[Chunk]) -> list[Chunk]:
        """优先保留命中，再按距离加入邻居，并以原文顺序输出。"""

        selected = [matched]
        used_tokens = matched.token_count
        for chunk in sorted(
            (value for value in siblings if value.id != matched.id),
            key=lambda value: (abs(value.index - matched.index), value.index),
        ):
            if used_tokens + chunk.token_count > self._parent_max_tokens:
                continue
            selected.append(chunk)
            used_tokens += chunk.token_count
        return sorted(selected, key=lambda value: value.index)

    @staticmethod
    def _same_parent(matched: Chunk, candidate: Chunk) -> bool:
        """优先使用 V3 Parent ID；旧 Chunk 回退到严格的来源边界。"""

        parent_id = matched.metadata.get("parent_id")
        candidate_parent_id = candidate.metadata.get("parent_id")
        if isinstance(parent_id, str):
            return candidate_parent_id == parent_id
        if matched.heading_path != candidate.heading_path:
            return False
        matched_locator = matched.locator
        candidate_locator = candidate.locator
        if matched_locator is None or candidate_locator is None:
            return matched_locator is candidate_locator
        return (
            matched_locator.page,
            matched_locator.sheet,
            matched_locator.cell_range,
            matched_locator.slide,
        ) == (
            candidate_locator.page,
            candidate_locator.sheet,
            candidate_locator.cell_range,
            candidate_locator.slide,
        )

    @staticmethod
    def _annotate_single_source(result: RetrievalResult, source: str) -> RetrievalResult:
        """为 Fake/第三方 VectorStore 补齐单通道解释字段。"""

        return replace(
            result,
            dense_score=result.score if source.startswith("dense") else result.dense_score,
            sparse_score=result.score if source.startswith("sparse") else result.sparse_score,
            retrieval_sources=(source,),
            context_chunk_ids=result.context_chunk_ids or (result.chunk_id,),
        )

    @staticmethod
    def _empty_run(
        query: str,
        options: RetrievalOptions,
        fallback_reasons: Sequence[str],
        *,
        query_variants: Sequence[str] | None = None,
    ) -> RetrievalRun:
        """统一构造无结果 Trace，确保空知识库和真实零召回行为可预测。"""

        variants = tuple(query_variants or (query,))
        return RetrievalRun(
            results=(),
            trace=RetrievalTrace(
                original_query=query,
                query_variants=variants,
                mode=options.mode,
                candidate_count=0,
                result_count=0,
                rewrite_applied=len(variants) > 1,
                rerank_applied=False,
                parent_expansion_applied=False,
                fallback_reasons=tuple(fallback_reasons),
            ),
        )
