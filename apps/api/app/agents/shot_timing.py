from __future__ import annotations

import re


_NUMBER = r"\d+(?:\.\d+)?"
_RANGE = re.compile(
    rf"(?<!\d){_NUMBER}\s*(?:-|–|—|~|～|至|到)\s*{_NUMBER}\s*(?:秒|s)\s*[：:，,、]?\s*",
    re.IGNORECASE,
)
_ORDINAL_SECOND = re.compile(
    rf"第\s*{_NUMBER}\s*秒\s*(?:时|后)?\s*[：:，,、]?\s*",
    re.IGNORECASE,
)
_SECOND_MARKER = re.compile(
    rf"(?<!\d){_NUMBER}\s*(?:秒|s)\s*(?:后|时|处)\s*[：:，,、]?\s*",
    re.IGNORECASE,
)


def strip_internal_timing(value: object) -> str:
    """Remove absolute beat timing while preserving action order and wording."""

    if not isinstance(value, str):
        return ""
    cleaned = _RANGE.sub("", value)
    cleaned = _ORDINAL_SECOND.sub("", cleaned)
    cleaned = _SECOND_MARKER.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s*([；;])\s*", r"\1", cleaned)
    cleaned = re.sub(r"^[\s：:，,、；;]+", "", cleaned)
    return cleaned.strip()
