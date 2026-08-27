"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, KnowledgeBase } from "./lib";

/** 知识库入口页：展示现有知识库并提供最小创建表单。 */
export default function Home() {
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  /** 重新读取事实数据，使创建后的界面不依赖本地乐观状态。 */
  const load = useCallback(async () => {
    try {
      setError("");
      setItems(await api<KnowledgeBase[]>("/api/knowledge-bases"));
    } catch (value) {
      setError(value instanceof Error ? value.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let isActive = true;
    void api<KnowledgeBase[]>("/api/knowledge-bases")
      .then((values) => {
        if (isActive) setItems(values);
      })
      .catch((value: unknown) => {
        if (isActive) setError(value instanceof Error ? value.message : "加载失败");
      })
      .finally(() => {
        if (isActive) setLoading(false);
      });
    // 开发模式 Strict Mode 会重新执行 Effect；清理标记防止旧请求覆盖新页面状态。
    return () => {
      isActive = false;
    };
  }, []);

  /** 提交创建请求，成功后清空表单并从服务端刷新列表。 */
  async function create(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await api<KnowledgeBase>("/api/knowledge-bases", {
        method: "POST",
        body: JSON.stringify({ name, description }),
      });
      setName("");
      setDescription("");
      await load();
    } catch (value) {
      setError(value instanceof Error ? value.message : "创建失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <section className="hero">
        <p className="eyebrow">ENTERPRISE KNOWLEDGE, MADE RETRIEVABLE</p>
        <h1>把 Markdown 知识变成<br />可追溯的智能回答</h1>
        <p>创建知识库、上传文档，然后通过 Dense Retrieval 与百炼模型完成可信问答。</p>
      </section>

      {error && <div className="alert">{error}</div>}

      <div className="twoColumns">
        <section className="panel">
          <div className="panelHeader"><h2>知识库</h2><span>{items.length} 个</span></div>
          {loading ? <p className="muted">正在加载…</p> : items.length === 0 ? (
            <div className="empty"><strong>还没有知识库</strong><p>从右侧创建第一个知识库。</p></div>
          ) : (
            <div className="cards">
              {items.map((item) => (
                <Link className="kbCard" href={`/knowledge-bases/${item.id}`} key={item.id}>
                  <div className="kbIcon">KB</div>
                  <div><h3>{item.name}</h3><p>{item.description || "暂无描述"}</p></div>
                  <span className="arrow">→</span>
                </Link>
              ))}
            </div>
          )}
        </section>

        <section className="panel createPanel">
          <div className="panelHeader"><h2>创建知识库</h2></div>
          <form onSubmit={create}>
            <label>名称<input value={name} onChange={(event) => setName(event.target.value)} maxLength={200} required placeholder="例如：RAG 工程手册" /></label>
            <label>描述<textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={2000} rows={4} placeholder="这个知识库包含什么？" /></label>
            <button disabled={saving}>{saving ? "创建中…" : "创建知识库"}</button>
          </form>
        </section>
      </div>
    </>
  );
}
