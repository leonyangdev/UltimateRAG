---
layout: home

hero:
  name: "UltimateRAG"
  text: "企业级 RAG 平台学习指南"
  tagline: 从零理解 RAG 原理，彻底看懂 UltimateRAG 的架构、模块与核心流程
  actions:
    - theme: brand
      text: 开始学习 RAG
      link: /guide/what-is-rag
    - theme: alt
      text: 查看整体架构
      link: /architecture/overview

features:
  - title: 📖 RAG 入门
    details: 从问题背景讲起，用通俗语言解释什么是 RAG、为什么需要它，以及检索增强生成的完整链路。
    link: /guide/what-is-rag
  - title: 🏗️ 整体架构
    details: 四层架构（Interface → Application → Domain → Infrastructure）、依赖方向、三大存储职责与版本演进路线。
    link: /architecture/overview
  - title: 🧩 模块详解
    details: 逐个拆解 Parser、Chunker、Embedding、VectorStore、Worker、Generation 等每个模块的职责与代码。
    link: /modules/index
  - title: 🔄 核心流程
    details: 文档摄取（Parse → Chunk → Embed → Index）与检索问答（Retrieve → Context → Generate）两条主链路的端到端追踪。
    link: /workflows/ingestion
  - title: 💻 代码导读
    details: 挑出最有代表性的核心代码，逐段解释设计意图、关键约束与失败行为。
    link: /workflows/code-tour
  - title: 📡 API 参考
    details: 知识库、文档、检索、问答的全部 REST 接口、请求响应格式与错误处理。
    link: /reference/api
---

## 这份文档是写给谁的

如果你是 **UltimateRAG 的开发者（或学习者）**，但面对这个仓库时感觉：

- 项目很大，不知道从哪看起
- 模块很多，不明白它们之间怎么协作
- 术语很多（Domain / Parser / Chunk / Embedding / Milvus / Worker…），概念模糊
- 想弄明白一个文档从上传到能被检索、被引用，中间到底发生了什么

那么这份指南就是为你写的。

它**不假设你已经懂 RAG**，而是从概念讲起；也**不只看目录结构**，而是带你顺着真实的数据流，把每个模块的职责和它们之间的关系讲清楚。

> 💡 阅读建议：按左侧边栏的顺序从上到下阅读即可。如果时间有限，至少读完「RAG 入门」和「核心流程」两条主链路。
