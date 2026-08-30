"""清理 OCR / Vision 模型返回的可检索 Markdown。

模型输出属于不可信外部数据。即使 Prompt 要求只返回正文，OCR 仍可能用 Markdown 围栏包裹
结果，或把示意图背景误判成数百行空表格。本模块只做确定性、保守的格式清理：不改写正文，
只移除 Markdown 包装、空表格行和明显重复于正文的单单元格伪表格。
"""

from __future__ import annotations

import re

_MARKDOWN_FENCE = re.compile(r"^```(?:markdown|md)\s*$", re.IGNORECASE)
_TABLE_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
NO_RETRIEVABLE_CONTENT = "NO_RETRIEVABLE_CONTENT"


def normalize_model_markdown(value: str) -> str:
    """返回适合进入领域 Block 的模型文本，并过滤无信息的 Markdown 表格噪声。

    真实表格至少需要一个有效表头或两个有内容的数据行。只有一个非空单元格、且该文字已在
    普通正文出现的“表格”通常是 OCR 对图形边框的误判，删除它可以避免无意义 Chunk 污染召回。
    """

    canonical = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if canonical.upper().strip("` .\n") == NO_RETRIEVABLE_CONTENT:
        return ""
    lines = _remove_markdown_fences(canonical.split("\n"))
    plain_text = "\n".join(line for line in lines if not _is_table_line(line))
    normalized_plain = _search_key(plain_text)

    cleaned: list[str] = []
    index = 0
    while index < len(lines):
        if not _is_table_line(lines[index]):
            cleaned.append(lines[index].rstrip())
            index += 1
            continue

        # OCR 偶尔在每个表格行之间插入空行；这些空行仍属于当前表格区域，不能把一个伪表格
        # 拆成数百个独立片段后绕过清理。
        table_lines: list[str] = []
        cursor = index
        while cursor < len(lines):
            line = lines[cursor]
            if _is_table_line(line):
                table_lines.append(line.strip())
                cursor += 1
                continue
            if not line.strip() and _next_nonblank_is_table(lines, cursor + 1):
                cursor += 1
                continue
            break
        cleaned.extend(_clean_table(table_lines, normalized_plain))
        index = cursor

    result = "\n".join(cleaned).strip()
    return re.sub(r"\n{3,}", "\n\n", result)


def combine_ocr_and_vision(ocr_text: str, vision_text: str) -> str:
    """以显式标签融合精确 OCR 与视觉关系描述，避免下游误解两种证据来源。"""

    ocr = normalize_model_markdown(ocr_text)
    vision = normalize_model_markdown(vision_text)
    if ocr and vision:
        return f"## OCR 文本\n\n{ocr}\n\n## 视觉结构\n\n{vision}"
    return ocr or vision


def _remove_markdown_fences(lines: list[str]) -> list[str]:
    """仅删除模型用于包装 Markdown 的围栏，不触碰正文中的其他语言代码块。"""

    cleaned: list[str] = []
    inside_markdown_fence = False
    for line in lines:
        stripped = line.strip()
        if _MARKDOWN_FENCE.fullmatch(stripped):
            inside_markdown_fence = True
            continue
        if inside_markdown_fence and stripped == "```":
            inside_markdown_fence = False
            continue
        cleaned.append(line)
    return cleaned


def _clean_table(lines: list[str], normalized_plain: str) -> list[str]:
    """删除空行并判断表格是否携带足够独立信息。"""

    rows = [line for line in lines if _is_separator_row(line) or any(_table_cells(line))]
    if not rows:
        return []

    separator_indexes = [index for index, line in enumerate(rows) if _is_separator_row(line)]
    has_valid_header = any(
        index > 0 and any(_table_cells(rows[index - 1])) for index in separator_indexes
    )
    semantic_rows = [line for line in rows if not _is_separator_row(line)]
    unique_cells = {
        _search_key(cell)
        for row in semantic_rows
        for cell in _table_cells(row)
        if _search_key(cell)
    }

    if not has_valid_header:
        # 没有有效表头时不能声称保留了行列结构。把仍有价值的单元格降为普通文本，并删除已经
        # 出现在正文中的碎片；这既保住无表头矩阵中的独立值，也能清掉示意图边框产生的伪表格。
        flattened_rows: list[str] = []
        seen: set[str] = set()
        for row in semantic_rows:
            flattened = " ".join(cell for cell in _table_cells(row) if cell).strip()
            key = _search_key(flattened)
            if not key or key in normalized_plain or key in seen:
                continue
            seen.add(key)
            flattened_rows.append(flattened)
        return flattened_rows
    if len(unique_cells) == 1 and next(iter(unique_cells)) in normalized_plain:
        return []

    # 分隔行没有前置表头时会生成非法 Markdown；保留语义行比保留损坏格式更有价值。
    return [
        line
        for index, line in enumerate(rows)
        if not _is_separator_row(line) or (index > 0 and any(_table_cells(rows[index - 1])))
    ]


def _table_cells(line: str) -> list[str]:
    """返回去除边界竖线和空白后的 Markdown 单元格。"""

    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(line: str) -> bool:
    """判断一行是否为 Markdown 表头分隔行。"""

    cells = [cell for cell in _table_cells(line) if cell]
    return bool(cells) and all(_TABLE_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def _is_table_line(line: str) -> bool:
    """使用严格边界识别模型生成的 Markdown 表格行。"""

    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _next_nonblank_is_table(lines: list[str], start: int) -> bool:
    """判断后续第一个非空行是否仍属于表格。"""

    for line in lines[start:]:
        if line.strip():
            return _is_table_line(line)
    return False


def _search_key(value: str) -> str:
    """生成只用于重复判断的大小写与空白无关键。"""

    return "".join(value.casefold().split())
