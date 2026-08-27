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

export interface ChatResult {
  answer: string;
  citations: Array<{
    document_id: string;
    filename: string;
    chunk_id: string;
    heading_path: string[];
  }>;
  retrieval_results: RetrievalResult[];
}

/**
 * 调用 FastAPI 并统一解析结构化错误。
 * FormData 请求必须由浏览器自动生成 multipart boundary，因此不会手工设置 Content-Type。
 */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(payload.detail ?? `请求失败 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
