from __future__ import annotations

import json
import re

from app.database import connect


DEFAULT_SYNONYM_GROUPS = [
    ["微控制器", "单片机", "MCU"],
    ["物料清单", "BOM"],
    ["退货授权", "RMA", "退换货"],
    ["技术规格书", "数据手册", "datasheet"],
    ["应用笔记", "AN", "application note"],
    ["印制电路板", "PCB"],
    ["晶圆", "wafer"],
    ["芯片", "IC", "集成电路"],
    ["封装", "package"],
    ["良率", "yield"],
    ["车规", "汽车级", "AEC-Q100"],
    ["静电放电", "ESD"],
    ["湿敏等级", "MSL"],
    ["失效分析", "FA"],
    ["产品变更通知", "PCN"],
    ["物料停产", "EOL", "停产通知"],
    ["现场可编程门阵列", "FPGA"],
    ["模数转换器", "ADC"],
    ["数模转换器", "DAC"],
    ["低压差线性稳压器", "LDO"],
]

STOPWORDS = {
    "的", "了", "和", "与", "及", "或", "是", "在", "有", "请", "问", "一下",
    "怎么", "如何", "什么", "哪些", "这个", "那个", "the", "a", "an", "of", "and", "or",
}


def custom_synonym_groups() -> list[list[str]]:
    with connect() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key='knowledge_synonyms'").fetchone()
    if not row:
        return []
    try:
        value = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def all_synonym_groups() -> list[list[str]]:
    return [*DEFAULT_SYNONYM_GROUPS, *custom_synonym_groups()]


def save_custom_synonym_groups(groups: list[list[str]]) -> list[list[str]]:
    normalized: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for group in groups:
        values = list(dict.fromkeys(" ".join(value.split()) for value in group if value.strip()))
        if len(values) < 2:
            continue
        key = tuple(value.casefold() for value in values)
        if key not in seen:
            normalized.append(values)
            seen.add(key)
    with connect() as db:
        db.execute(
            "INSERT INTO app_settings(key,value) VALUES('knowledge_synonyms',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(normalized, ensure_ascii=False),),
        )
    return normalized


def expand_query(query: str, terms: list[str]) -> tuple[list[str], list[str]]:
    haystack = query.casefold()
    expanded = list(terms)
    matched_groups: list[str] = []
    for group in all_synonym_groups():
        def matches(alias: str) -> bool:
            value = alias.casefold()
            if re.fullmatch(r"[a-z0-9][a-z0-9 ._+-]*", value):
                return bool(re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", haystack))
            return value in haystack

        if any(matches(alias) for alias in group):
            matched_groups.append(" / ".join(group))
            expanded.extend(group)
    return list(dict.fromkeys(expanded)), matched_groups
