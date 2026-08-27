import type { UIMessage } from "ai";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  created_at: string;
}

export interface DocumentItem {
  id: string;
  filename: string;
  status: string;
  error_message: string | null;
  parser_name: string | null;
  created_at: string;
}

export interface RetrievalResult {
  chunk_id: string;
  document_id: string;
  filename: string;
  content: string;
  heading_path: string[];
  score: number;
}

export interface Citation {
  document_id: string;
  filename: string;
  chunk_id: string;
  heading_path: string[];
}

export interface ChatResult {
  answer: string;
  citations: Citation[];
  retrieval_results: RetrievalResult[];
}

/**
 * 与 FastAPI ``data-retrieval`` Part 对应的 AI SDK 数据类型。
 * 引用和完整召回结果随 assistant message 一起到达，刷新 React 状态时不会与文本流错配。
 */
export type RAGDataParts = {
  retrieval: {
    citations: Citation[];
    retrieval_results: RetrievalResult[];
  };
};

export type RAGMessage = UIMessage<unknown, RAGDataParts>;

/**
 * 调用 FastAPI 并统一解析结构化错误。
 * FormData 请求必须由浏览器自动生成 multipart boundary，因此不会手工设置 Content-Type。
 */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? init.headers
        : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(payload.detail ?? `请求失败 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
