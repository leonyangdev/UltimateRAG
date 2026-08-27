# BGE-M3

BGE-M3 是一种多语言、多功能文本嵌入模型。它可以同时支持稠密检索、稀疏检索和多向量检索。

## V1 中的使用

UltimateRAG V1 使用稠密向量检索。文档经过结构感知切块后，文本向量被写入 Milvus。

# 数据职责

PostgreSQL 保存业务事实和 Chunk 元数据，MinIO 保存原始 Markdown，Milvus 保存可重建的向量索引。

