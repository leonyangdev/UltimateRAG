"""验证模型 Markdown 清理不会让 OCR 伪表格污染检索。"""

from ultimate_rag.parsers._model_output import combine_ocr_and_vision, normalize_model_markdown


def test_normalizer_removes_empty_ocr_table_repeated_from_plain_text() -> None:
    """背景边框误识别出的空表格应删除，已正确提取的正文必须保留。"""

    value = """深度学习
Deep Learning

```markdown
|  |  |

| --- | --- |

|  | Deep Learning |

|  |  |

|  |  |
```
"""

    assert normalize_model_markdown(value) == "深度学习\nDeep Learning"


def test_normalizer_preserves_real_table_and_removes_empty_rows() -> None:
    """具有表头和数据的真实表格只删除全空数据行。"""

    value = """| Metric | Value |
| --- | --- |
| Recall | 0.92 |
|  |  |
"""

    assert normalize_model_markdown(value) == (
        "| Metric | Value |\n| --- | --- |\n| Recall | 0.92 |"
    )


def test_normalizer_drops_headerless_table_fragments_already_in_prose() -> None:
    """多行单单元格伪表格即使非空，也不应重复污染已正确提取的正文。"""

    value = """机器学习是人工智能的一个子领域，主要关注多层神经网络。

|  |  |
| --- | --- |
|  | 机器学习是人工智能的一个子领域 |
| 主要关注多层神经网络 |  |
|  |  |
"""

    assert normalize_model_markdown(value) == (
        "机器学习是人工智能的一个子领域，主要关注多层神经网络。"
    )


def test_combiner_labels_ocr_and_visual_evidence() -> None:
    """融合内容应明确区分原文字与图形关系，便于检索和调试。"""

    result = combine_ocr_and_vision("Encoder", "Encoder 通过箭头连接 Decoder。")

    assert result == (
        "## OCR 文本\n\nEncoder\n\n## 视觉结构\n\nEncoder 通过箭头连接 Decoder。"
    )


def test_normalizer_drops_explicit_no_retrievable_content_sentinel() -> None:
    """视觉模型确认无检索价值的装饰图不得生成空洞向量。"""

    assert normalize_model_markdown("`NO_RETRIEVABLE_CONTENT`") == ""
