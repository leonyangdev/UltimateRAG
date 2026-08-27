"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ChatResult, DocumentItem, KnowledgeBase, RetrievalResult } from "../../lib";

type Mode = "chat" | "retrieval";

/**
 * 单知识库工作区。
 * 文档管理、独立检索调试和完整 RAG 问答并列展示，方便学习者观察完整数据链路。
 */
export default function KnowledgeBasePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<ChatResult | null>(null);
  const [retrieval, setRetrieval] = useState<RetrievalResult[]>([]);
  const [mode, setMode] = useState<Mode>("chat");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  /** 并行刷新知识库元数据和文档处理状态。 */
  const load = useCallback(async () => {
    try {
      const [kb, docs] = await Promise.all([
        api<KnowledgeBase>(`/api/knowledge-bases/${id}`),
        api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`),
      ]);
      setKnowledgeBase(kb);
      setDocuments(docs);
    } catch (value) {
      setError(value instanceof Error ? value.message : "加载失败");
    }
  }, [id]);

  useEffect(() => {
    let isActive = true;
    void Promise.all([
      api<KnowledgeBase>(`/api/knowledge-bases/${id}`),
      api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`),
    ])
      .then(([kb, docs]) => {
        if (!isActive) return;
        setKnowledgeBase(kb);
        setDocuments(docs);
      })
      .catch((value: unknown) => {
        if (isActive) setError(value instanceof Error ? value.message : "加载失败");
      });
    // 路由 ID 变化时忽略旧请求，避免把上一知识库数据写入当前页面。
    return () => {
      isActive = false;
    };
  }, [id]);

  /** 上传 Markdown；同步 API 返回时文档已经 READY，失败状态仍会从服务端刷新显示。 */
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = event.currentTarget.elements.namedItem("document") as HTMLInputElement;
    if (!input.files?.[0]) return;
    setWorking(true);
    setError("");
    const data = new FormData();
    data.append("file", input.files[0]);
    try {
      await api<DocumentItem>(`/api/knowledge-bases/${id}/documents`, { method: "POST", body: data });
      input.value = "";
      await load();
    } catch (value) {
      setError(value instanceof Error ? value.message : "上传失败");
      await load();
    } finally {
      setWorking(false);
    }
  }

  /** 根据当前标签执行纯检索或带 LLM 的完整问答。 */
  async function ask(event: FormEvent) {
    event.preventDefault();
    setWorking(true);
    setError("");
    setAnswer(null);
    setRetrieval([]);
    try {
      if (mode === "chat") {
        setAnswer(await api<ChatResult>("/api/chat", {
          method: "POST",
          body: JSON.stringify({ knowledge_base_id: id, question, top_k: 5 }),
        }));
      } else {
        setRetrieval(await api<RetrievalResult[]>("/api/retrieval/search", {
          method: "POST",
          body: JSON.stringify({ knowledge_base_id: id, query: question, top_k: 5 }),
        }));
      }
    } catch (value) {
      setError(value instanceof Error ? value.message : "请求失败");
    } finally {
      setWorking(false);
    }
  }

  /** 经用户确认后同步清理文档在三个存储中的数据。 */
  async function removeDocument(documentId: string) {
    if (!window.confirm("确认删除这个文档及其向量索引？")) return;
    try {
      await api<void>(`/api/documents/${documentId}`, { method: "DELETE" });
      await load();
    } catch (value) {
      setError(value instanceof Error ? value.message : "删除失败");
    }
  }

  const results = answer?.retrieval_results ?? retrieval;

  return (
    <>
      <Link href="/" className="back">← 返回知识库</Link>
      <section className="pageTitle">
        <div><p className="eyebrow">KNOWLEDGE BASE</p><h1>{knowledgeBase?.name ?? "加载中…"}</h1><p>{knowledgeBase?.description}</p></div>
        <span className="readyDot"><i /> V1 工作区</span>
      </section>
      {error && <div className="alert">{error}</div>}

      <div className="workspace">
        <aside className="panel documentsPanel">
          <div className="panelHeader"><h2>文档</h2><span>{documents.length}</span></div>
          <form onSubmit={upload} className="uploadBox">
            <input type="file" name="document" accept=".md,.markdown,text/markdown" required />
            <button disabled={working}>上传并索引</button>
            <small>仅支持 UTF-8 Markdown，最大 10 MB</small>
          </form>
          <div className="documentList">
            {documents.length === 0 && <p className="muted">上传 Markdown 后即可开始问答。</p>}
            {documents.map((document) => (
              <article className="document" key={document.id}>
                <div><strong>{document.filename}</strong><span className={`status status${document.status}`}>{document.status}</span></div>
                <p>{document.error_message || `${document.parser_name ?? "等待解析"} · ${new Date(document.created_at).toLocaleString("zh-CN")}`}</p>
                <button className="linkButton danger" onClick={() => removeDocument(document.id)}>删除</button>
              </article>
            ))}
          </div>
        </aside>

        <section className="panel qaPanel">
          <div className="tabs">
            <button className={mode === "chat" ? "active" : ""} onClick={() => setMode("chat")}>RAG 问答</button>
            <button className={mode === "retrieval" ? "active" : ""} onClick={() => setMode("retrieval")}>检索调试</button>
          </div>
          <form onSubmit={ask} className="askForm">
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} required rows={3} placeholder={mode === "chat" ? "基于知识库提一个问题…" : "输入查询，查看 Milvus 召回结果…"} />
            <button disabled={working || documents.every((document) => document.status !== "READY")}>{working ? "处理中…" : mode === "chat" ? "发送问题" : "执行检索"}</button>
          </form>

          {answer && <article className="answer"><p className="eyebrow">ANSWER</p><div>{answer.answer}</div>{answer.citations.length > 0 && <div className="citationRow">{answer.citations.map((citation, index) => <span key={citation.chunk_id}>[{index + 1}] {citation.filename} · {citation.heading_path.join(" > ") || "未命名章节"}</span>)}</div>}</article>}

          {results.length > 0 && <section className="results"><div className="panelHeader"><h2>检索证据</h2><span>Top {results.length}</span></div>{results.map((result, index) => <article className="result" key={result.chunk_id}><header><strong>#{index + 1} {result.filename}</strong><span>{result.score.toFixed(4)}</span></header><p className="path">{result.heading_path.join(" > ") || "未命名章节"}</p><pre>{result.content}</pre></article>)}</section>}
        </section>
      </div>
    </>
  );
}
