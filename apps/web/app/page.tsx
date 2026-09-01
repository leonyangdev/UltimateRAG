import { redirect } from "next/navigation";

/**
 * 把根路径直接交给核心聊天工作区。
 *
 * 产品不再保留营销型首页。服务端 redirect 不会先下载一个仅用于跳转的 Client Bundle，
 * 浏览器历史也不会留下无业务价值的中间页；知识库管理仍可从聊天侧栏进入。
 */
export default function Home() {
  redirect("/chat");
}
