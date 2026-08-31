"""可预期业务异常。

接口层依据这些类型生成稳定 HTTP 错误；未知异常仍交由框架记录和处理，避免静默吞错。
"""


class UltimateRAGError(Exception):
    """Base exception for expected application errors."""


class ResourceNotFoundError(UltimateRAGError):
    """请求的知识库或文档不存在。"""

    pass


class InvalidDocumentError(UltimateRAGError):
    """上传文档未通过类型、大小、编码或内容校验。"""

    pass


class UnsupportedDocumentTypeError(InvalidDocumentError):
    """解析器注册表中没有能够处理该来源的解析器。"""

    pass


class DocumentProcessingError(UltimateRAGError):
    """文档已持久化，但解析、切块、向量化或索引阶段失败。"""

    pass


class DocumentBusyError(UltimateRAGError):
    """文档仍由后台 Worker 处理，当前操作会破坏状态一致性。"""

    pass


class ChatSessionBusyError(UltimateRAGError):
    """同一会话已有生成进行中，拒绝并发写入破坏消息顺序。"""

    pass


class ExternalServiceError(UltimateRAGError):
    """外部基础设施或模型服务发生可识别故障。"""

    pass
