# API 与前端

## 第一部分：后端 API（Interface 层）

代码位置：`apps/api/`

### 1. 这一层是什么

API 是**进程启动 + HTTP 边界**：验证输入、调用应用服务、把领域结果映射为 JSON。它**不包含**数据库、对象存储、向量检索或 Prompt 业务逻辑。

```text
Validate Request
      ↓
Application Service
      ↓
Response
```

### 2. 文件职责

| 文件 | 职责 |
|---|---|
| `app.py` | FastAPI 应用、Lifespan 装配、中间件、统一异常映射 |
| `routes.py` | 全部 HTTP 路由（薄 Controller） |
| `schemas.py` | Pydantic 请求/响应模型（外部数据验证边界） |
| `container.py` | 进程级依赖容器 |

### 3. app.py —— 应用入口与 Lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 阶段 1：与 Worker 共用同一 Composition Root，防止"API 校验支持某格式、
    #         Worker 却无法解析"的配置不一致
    runtime = create_processing_runtime(settings)

    # 阶段 2：LLM 只属于 HTTP 问答进程；后台 Worker 不创建生成模型客户端
    llm = BailianLLMClient(...)
    retrieval = RetrievalService(
        runtime.embedder, runtime.vector_store, runtime.repository,
        query_rewriter=BailianQueryRewriter(...),
        reranker=BailianReranker(...),
        default_options=RetrievalOptions(mode=RetrievalMode.HYBRID, ...),
    )

    # 阶段 3：集中放入进程级 Container，Route 只从 app.state 取服务
    app.state.container = Container(
        engine=runtime.engine,
        repository=runtime.repository,
        ingestion=runtime.ingestion,
        retrieval=retrieval,
        rag=RAGService(retrieval, ContextBuilder(...), llm),
        lifecycle=DocumentLifecycleService(...),
    )

    # 阶段 4：幂等准备 MinIO Bucket + Milvus Collection。
    # 任一失败 → 不执行 yield → 应用不进入服务阶段
    await runtime.initialize()
    yield
    await runtime.close()   # 释放数据库连接池
```

### 4. 统一异常映射

| 业务异常 | HTTP 状态 | 含义 |
|---|---|---|
| `ResourceNotFoundError` | 404 | 知识库/文档不存在 |
| `InvalidDocumentError` | 400 | 输入错误（类型/大小/格式） |
| `DocumentBusyError` | 409 | 文档正在后台处理，稍后重试 |
| `UltimateRAGError`（其他已知） | 502 | 外部处理故障，不暴露 Stack Trace |

### 5. routes.py —— 全部端点

**知识库**

```text
POST   /api/knowledge-bases                      → 201 创建
GET    /api/knowledge-bases                      → 列表
GET    /api/knowledge-bases/{id}                 → 单个
DELETE /api/knowledge-bases/{id}                 → 204 删除
```

**文档**

```text
POST   /api/knowledge-bases/{kb_id}/documents    → 202 上传入队（不等待处理）
GET    /api/knowledge-bases/{kb_id}/documents    → 列表 + 实时状态
GET    /api/documents/{doc_id}                   → 单个元数据
POST   /api/documents/{doc_id}/reindex           → 202 复用原文件重新解析/重建
DELETE /api/documents/{doc_id}                   → 204 删除
GET    /api/chunks/{chunk_id}/preview             → PDF 命中区域 JPEG（按需渲染）
GET    /api/assets/{asset_id}/content              → 摄取期抽取图片（MinIO）
```

**问答**

```text
POST   /api/retrieval/search                     → 纯检索（不依赖 LLM，可独立调试）
POST   /api/retrieval/explain                    → 结果 + V3 阶段 Trace
POST   /api/chat                                 → 完整问答（答案 + 引用 + 召回证据）
POST   /api/chat/stream                          → SSE 流式问答（AI SDK UI Message Stream）
GET    /api/health                               → 存活检查（不触发模型调用）
```

**持久化会话**

```text
POST   /api/knowledge-bases/{kb_id}/chat-sessions                → Draft 首次 Chat 发送前按需持久化会话
GET    /api/knowledge-bases/{kb_id}/chat-sessions                → 当前知识库历史列表
GET    /api/chat-sessions/{session_id}                           → 会话正文 + 当轮证据快照
DELETE /api/knowledge-bases/{kb_id}/chat-sessions/{session_id}   → 204 级联删除会话消息
```

页面中的“新对话”不是一条数据库记录，而是没有 `session_id` 的本地 Draft。进入知识库或从历史会话
点击“新建对话”只清空当前对话视图；已经处于 Draft 时重复点击是幂等操作，不发送写请求。只有首次
提交 Chat 问题时，前端才先创建持久化会话，再把返回的 ID 传给流式问答：

```text
进入知识库 / 点击“新建对话”
        ↓ 浏览器 Draft，不调用 POST
首次提交 Chat 问题
        ↓ POST /api/knowledge-bases/{kb_id}/chat-sessions
获得 session_id
        ↓ POST /api/chat/stream（携带 session_id）
会话进入历史列表，后续轮次复用同一 session_id
```

删除端点保留知识库父资源路径，并在事务内锁定会话、校验归属。有效的 PENDING 生成会返回 409，
避免流式回答结束时向已删除会话提交消息；会话不存在或跨知识库访问统一返回 404。

### 6. 上传端点的关键设计

```python
async def _read_bounded_upload(file, max_upload_bytes) -> bytes:
    content = await file.read(max_upload_bytes + 1)   # 只多读一个字节
    if len(content) > max_upload_bytes:
        raise InvalidDocumentError("文件不能超过 N MB")
    return content
```

- **有界读取**：不在内存中完整加载任意大请求才拒绝
- 应用服务保留同样校验：CLI / 测试 / 未来 Worker 直接调用时依然安全

### 7. 流式端点（`/chat/stream`）的协议设计

使用 **AI SDK Data Stream Protocol** 的 SSE 表示：

```text
start → start-step → data-retrieval（引用+证据+Trace 随同一条消息）→ text-start
     → text-delta × N → text-end → finish-step → finish → [DONE]
```

关键点：

- **检索在 StreamingResponse 建立前完成**：知识库不存在、Embedding 失败、Milvus 不可用时，仍能返回结构化 HTTP 状态码
- **Citation/RetrievalResult/RetrievalTrace 通过有类型的 `data-retrieval` Part 同消息返回**：前端不需要在流结束后再发请求补取证据
- PDF 结果携带 `content_types + assets + preview_url`；答案内 `asset://ID` 只在当前证据白名单内
  映射为图片，GFM 表格直接渲染，普通区域仍可懒加载原 PDF 局部截图
- `[来源 N](citation://N)` 是前端交互协议，不发网络请求；点击后按 Citation 顺序打开右侧来源栏
- LLM 在响应开始后的故障只能编码为 `error` Part（此时 200 已发出），日志保留完整堆栈，浏览器只收到稳定文案
- 响应头：
  - `X-Accel-Buffering: no`：关闭 Nginx 缓冲，让 token 及时抵达
  - `x-vercel-ai-ui-message-stream: v1`：AI SDK 识别标记，CORS 中显式暴露

### 8. schemas.py —— 外部验证边界

Pydantic 模型与内部领域 dataclass 分离，**防止基础设施字段泄漏给客户端**：

```python
ChatRequest(RetrievalRequest):
    query: str = Field(min_length=1, max_length=4000, alias="question")
```

- 外部字段用产品语义的 `question`，内部统一 `query`
- `from_domain()` 类方法负责领域对象 → API 结构的显式映射
- `RetrievalRequest.top_k`：`ge=1, le=20` 边界校验
- `candidate_k` 最大 100，`document_ids` 最大 50；空白 Query/ID 在 HTTP 边界拒绝
- 模式与可选阶段可逐请求覆盖部署默认值

## 第二部分：前端（Next.js）

代码位置：`apps/web/`

### 9. 技术栈

```text
Next.js 16 + React 19 + TypeScript
AI SDK（@ai-sdk/react, ai v7）— 消费流式消息
Tailwind CSS + shadcn/ui 风格组件
react-markdown + remark-gfm — 渲染答案 Markdown
```

### 10. 页面

| 页面 | 功能 |
|---|---|
| `/` | 重定向到统一聊天工作区 |
| `/knowledge-bases` | 知识库列表、创建 |
| `/knowledge-bases/[id]` | 知识库详情：上传、状态轮询、重新解析、删除 |
| `/chat` | ChatGPT 风格聊天：会话导航、流式问答、可点击来源与证据侧栏 |

### 11. API 调用封装（`app/lib.ts`）

```typescript
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? init.headers                        // FormData 由浏览器生成 multipart boundary
        : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(payload.detail ?? `请求失败 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
```

### 12. API 地址解析

```typescript
function resolveApiUrl(): string {
  const configuredUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (configuredUrl) return configuredUrl.replace(/\/$/, "");
  if (typeof window !== "undefined")
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  return "http://localhost:8000";   // 服务端预渲染回退
}
```

- 默认沿用当前页面的主机名 + 8000 端口：`localhost` 开发与局域网访问都能连到同一台主机上的 API
- 服务端预渲染期间没有 `window`，回退值只用于生成静态页面

### 13. 流式消息类型

```typescript
export type RAGDataParts = {
  retrieval: {
    citations: Citation[];
    retrieval_results: RetrievalResult[];
  };
};
export type RAGMessage = UIMessage<unknown, RAGDataParts>;
```

- 与后端 `data-retrieval` Part 对应：引用和召回结果随 assistant message 一起到达，**刷新 React 状态时不会与文本流错配**
- 正常完成的助手消息还把同一证据快照写入 PostgreSQL；选择历史会话时恢复相同 Part，不重新检索

### 14. 富媒体安全边界

`react-markdown` 自定义渲染器只识别两种内部协议：

- `asset://<uuid>`：必须能在本消息 `retrieval_results[].assets` 中找到，才映射 API URL；
- `citation://N`：只改变本地侧栏状态，来源详情来自后端 Citation/Result。

模型输出的任意公网图片不会自动加载，避免恶意知识通过像素请求跟踪浏览器。MinIO Object Key、
Access Key 和 Secret 从不进入 API Schema 或模型 Prompt。

### 15. 会话导航、删除与来源侧栏

桌面左侧导航有两个稳定宽度：展开时为 260px，会显示历史会话；折叠时仍保留 64px 图标轨，
提供“展开侧栏 / 新建会话 / 管理知识库”三个入口。移动端不复用桌面折叠宽度，而是使用完整
260px 遮罩抽屉，避免在窄屏中只留下难以理解的图标列表。

新建入口切换到不含 `session_id` 的 Draft；如果当前已经是 Draft，重复点击不重置状态、不创建记录。
Draft 与 AI SDK 的视图身份独立于数据库 ID，首次发送完成持久化时不会因为 ID 从空值变成 UUID
而重建 Chat 实例或丢失正在流式合并的消息。

会话行把“打开”和“删除”实现为两个同级按钮，并使用确认 Dialog。删除非当前会话只更新列表；
删除当前会话后优先恢复最近历史，没有剩余会话时回到 Draft，确保后续请求不会继续携带已经删除的
`session_id`，同时不写入替代空会话。前端只在服务端返回 204 后更新本地列表，404/409 不做乐观删除。

答案中的 `[来源 N](citation://N)` 不是 HTTP URL。`AnswerMarkdown` 只允许 N 落在当前消息
Citation 快照范围内，再把点击交给右侧来源 Dialog。组合历史格式（如 `[来源 1, 2]`）会拆成
两个独立链接；越界编号只显示文本。侧栏随后按 Citation 的 `chunk_id` 精确关联
RetrievalResult，不使用数组下标猜测证据。Radix Dialog 提供 Escape、焦点陷阱和关闭后的焦点
恢复；桌面遮罩透明，移动端保留遮罩以维持清晰层级。

### 16. 文档状态轮询

前端轮询 `GET /knowledge-bases/{id}/documents` 获取实时处理状态（PENDING → … → READY/FAILED），把后台 Worker 的进度展示给用户。

## 下一步

- 完整请求从浏览器到 LLM 的旅程 → [检索问答全流程](/workflows/query)
- API 参考速查 → [API 参考](/reference/api)
