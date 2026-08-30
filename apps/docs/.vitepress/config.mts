import { defineConfig } from 'vitepress'

// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: "UltimateRAG",
  description: "企业级 RAG 平台学习指南：从 RAG 原理到项目实现",
  lang: "zh-CN",
  // 站点部署在项目 Pages 子路径（leonyangdev.github.io/UltimateRAG/），
  // 必须设置 base，否则静态资源与页面链接会相对根路径解析导致 404。
  base: "/UltimateRAG/",
  lastUpdated: true,

  themeConfig: {
    logo: "📚",
    // 顶栏导航
    nav: [
      { text: "首页", link: "/" },
      { text: "RAG 入门", link: "/guide/what-is-rag" },
      { text: "项目概览", link: "/guide/project-overview" },
      { text: "架构", link: "/architecture/overview" },
      { text: "模块详解", link: "/modules/index" },
      { text: "核心流程", link: "/workflows/ingestion" },
      { text: "API 参考", link: "/reference/api" },
    ],

    // 侧边栏：按路径匹配
    sidebar: {
      "/guide/": [
        {
          text: "RAG 入门",
          items: [
            { text: "什么是 RAG", link: "/guide/what-is-rag" },
            { text: "RAG 的核心环节", link: "/guide/rag-pipeline" },
          ],
        },
        {
          text: "UltimateRAG 概览",
          items: [
            { text: "项目定位与版本演进", link: "/guide/project-overview" },
            { text: "V2 能力与限制", link: "/guide/v2-capabilities" },
          ],
        },
      ],

      "/architecture/": [
        {
          text: "架构",
          items: [
            { text: "整体架构与分层", link: "/architecture/overview" },
            { text: "核心设计原则", link: "/architecture/principles" },
            { text: "项目目录结构", link: "/architecture/directory" },
            { text: "核心领域模型", link: "/architecture/data-model" },
            { text: "三大存储职责", link: "/architecture/data-stores" },
            { text: "配置系统", link: "/architecture/config" },
          ],
        },
      ],

      "/modules/": [
        {
          text: "模块详解",
          items: [
            { text: "模块总览", link: "/modules/index" },
            { text: "Domain 领域层", link: "/modules/domain" },
            { text: "Application 应用层", link: "/modules/application" },
            { text: "Parser 解析器", link: "/modules/parsers" },
            { text: "Chunker 切块器", link: "/modules/chunker" },
            { text: "Embedding 向量化", link: "/modules/embeddings" },
            { text: "VectorStore 向量库", link: "/modules/vectorstore" },
            { text: "Generation 生成", link: "/modules/generation" },
            { text: "Worker 后台任务", link: "/modules/worker" },
            { text: "Infrastructure 基础设施", link: "/modules/infrastructure" },
            { text: "API 与 Web", link: "/modules/api-web" },
          ],
        },
      ],

      "/workflows/": [
        {
          text: "核心流程",
          items: [
            { text: "文档摄取全流程", link: "/workflows/ingestion" },
            { text: "检索问答全流程", link: "/workflows/query" },
            { text: "状态机与数据一致性", link: "/workflows/state-machine" },
            { text: "核心代码导读", link: "/workflows/code-tour" },
          ],
        },
      ],

      "/reference/": [
        {
          text: "参考",
          items: [
            { text: "REST API 参考", link: "/reference/api" },
            { text: "配置项速查", link: "/reference/config" },
            { text: "目录速查表", link: "/reference/cheatsheet" },
          ],
        },
      ],
    },

    // 页脚
    footer: {
      message: "UltimateRAG · 从最小可用 RAG 演进为企业级知识平台",
      copyright: "MIT License",
    },

    socialLinks: [
      { icon: "github", link: "https://github.com/leonyangdev/UltimateRAG" },
    ],

    // 文档最后更新时间
    lastUpdatedText: "最后更新",

    // 返回顶部
    returnToTopLabel: "回到顶部",

    // 搜索
    search: {
      provider: "local",
    },
  },
})
