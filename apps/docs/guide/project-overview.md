# 项目定位与版本演进

## 1. 项目是什么

**UltimateRAG** 是一个「从最小可用 RAG 持续演进为企业级知识平台」的**学习型工程**，同时追求企业级可落地。

它有两个身份：

- **教学 / 面试项目**：帮助你系统学习 RAG 原理和企业工程实践，代码注释承担「教材」职责
- **企业级平台**：架构分层清晰、数据边界明确、可替换能力强，可以作为真实系统的参考架构

### 项目的长期技术目标

```text
Parser 可插拔        → 新增文档格式不影响主流程
Chunk 策略可替换
Embedding 可替换
向量数据库可替换
Retriever 可组合
Reranker 可插拔
LLM 可替换
Workflow 可演进
```

这些目标不是「现在就全实现」，而是**架构边界合理、未来能平滑接入**。

## 2. 版本演进路线

项目按大版本逐步演进，每个版本必须独立可运行、可演示：

```text
V1.0  Naive RAG              基础 RAG 闭环：上传 → 解析 → 切块 → 向量化 → 检索 → 生成
V2.0  Document Intelligence  文档智能：多格式解析、异步可靠摄取、来源定位
V3.0  Advanced Retrieval     高级检索：Hybrid、Reranker、Query Rewrite
V4.0  Enterprise RAG         企业化：ACL、审计、任务平台、大规模调度
V5.0  RAGOps                 评估与运营：Golden Dataset、指标、回归报告
V6.0  Intelligent RAG        智能 RAG：Agent、Tool Calling、工作流
```

::: warning 版本纪律
开发时必须遵守当前版本的范围，**不要因为未来可能需要某项能力，就提前实现未来版本的功能**。
例如 V1 不应提前引入 Kafka、Kubernetes、GraphRAG 等。
:::

## 3. 当前版本：V3.0 Advanced Retrieval

仓库当前实现 **V3.0**。它完整保留 V2 的多格式文档智能与异步摄取，重点解决“能搜到，但精确词
容易遗漏、排序不够准、上下文太碎且过程不可解释”的问题。

### V3 能做什么（用户视角）

1. 上传 Markdown、PDF、DOCX、XLSX、PPTX、HTML 或图片，并在后台可靠处理
2. 使用本地 Docling 解析复杂 PDF，扫描页和图片按需调用百炼 OCR/Vision
3. 在 Dense、Milvus BM25 Sparse、Hybrid 三种召回模式之间切换
4. 用 RRF 融合不同查询变体和召回通道，再用 `qwen3-rerank` 重排有限候选
5. 保留原查询并可生成一个保守改写，模型故障时明确降级
6. 按最多 50 个文档 ID 过滤，并以 PostgreSQL `READY` 状态约束所有命中
7. 命中小 Child 后扩展同 Parent 的有限相邻内容，兼顾检索精度与回答上下文
8. 查看每阶段分数、查询变体、来源通道、降级原因与最终引用
9. 用离线指标脚本比较 Dense/Sparse/Hybrid 和 Rewrite/Rerank 消融实验

### V3 明确不包含（属于后续版本）

```text
ACL / 审计 / DLQ 控制台 / 认证          → V4
Golden Dataset 管理 / 在线 RAGOps       → V5
Agent / Tool Calling / LangGraph        → V6
```

## 4. 技术栈速览

| 层 | 技术 |
|---|---|
| 前端 | Next.js 16、React 19、TypeScript、Tailwind CSS 4、shadcn/ui、AI SDK |
| 接口 | FastAPI、Pydantic v2 |
| 核心库 | Python 3.12、SQLAlchemy 2（async）、Alembic、tiktoken |
| 文档解析 | Docling（PDF 版面/表格）、PDFium、python-docx、openpyxl、python-pptx、BeautifulSoup、markdown-it-py、Pillow |
| 数据存储 | PostgreSQL 16（事实）、MinIO（原始文件）、Milvus 2.5（Dense + 本地 BM25 派生索引）、Attu（调试） |
| 模型（阿里云百炼） | Embedding `text-embedding-v4` / Rewrite 与 LLM `qwen-plus` / Rerank `qwen3-rerank` / OCR `qwen3.5-ocr` / 视觉 `qwen3-vl-flash` |
| 工程 | uv、pytest、Ruff、Mypy、Docker Compose |

## 5. 架构一句话总结

```text
Next.js Web → FastAPI Interface → Application Service
                                      ├── 入库：提交任务 → MinIO + PostgreSQL → Worker 后台解析/切块/向量化/索引
                                      └── 问答：过滤/改写 → Dense+BM25 → RRF/重排 → Small2Big → LLM

PostgreSQL(事实) + MinIO(原文件)    ← 可重建来源
Milvus(派生索引)                    ← 可重建产物
```

## 6. 快速上手

```bash
# 1. 在仓库根目录准备 .env（参考 .env.example，填入百炼 API Key）
# 2. 一键启动全部服务
docker compose up -d --build

# 3. 打开服务
#    Web:        http://localhost:3000
#    API 文档:    http://localhost:8000/docs
#    MinIO:      http://localhost:9001
#    Attu:       http://localhost:8001
```

详细的启动说明见仓库根目录 `README.md`。

## 下一步

- 想知道 V3 的高级检索能力和限制 → [V3 能力与限制](/guide/v3-capabilities)
- 想回顾 V2 支持的文档格式 → [V2 能力与限制](/guide/v2-capabilities)
- 想从架构分层看起 → [整体架构与分层](/architecture/overview)
