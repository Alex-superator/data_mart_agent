"""Guardrails: детерминированные проверки артефактов. Не доверяем модели — проверяем."""
from __future__ import annotations

import re
from typing import Callable

Validator = Callable[[str, dict], list[str]]
REQ_ID = re.compile(r"(?:НФТ|ФТ)-\d+")


def require_sections(*sections: str) -> Validator:
    def check(doc: str, ctx: dict) -> list[str]:
        return [f"отсутствует обязательный раздел '{s}'" for s in sections if s not in doc]

    return check


def require_mermaid(doc: str, ctx: dict) -> list[str]:
    return [] if re.search(r"```mermaid\n.+?```", doc, re.S) else ["в HLD нет диаграммы в блоке ```mermaid```"]


def has_functional_requirements(doc: str, ctx: dict) -> list[str]:
    n = len(re.findall(r"^\| ФТ-\d+", doc, re.M))
    return [] if n >= 5 else [f"найдено ФТ: {n}, нужно минимум 5"]


def measurable_nfr(doc: str, ctx: dict) -> list[str]:
    """Каждое НФТ обязано содержать числовой порог — иначе его нельзя проверить."""
    bad = []
    for line in doc.splitlines():
        if line.startswith("| НФТ-"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not re.search(r"\d", " ".join(cells[1:])):
                bad.append(f"{cells[0]} не измеримо: нет числового порога (нужны значения, SLO, лимиты)")
    return bad


def traceability(doc: str, ctx: dict) -> list[str]:
    """ТЗ должно покрывать все ФТ/НФТ и не ссылаться на несуществующие."""
    reqs = set(REQ_ID.findall(ctx.get("requirements", "")))
    in_tz = set(REQ_ID.findall(doc))
    problems = []
    if reqs - in_tz:
        problems.append(f"ТЗ не покрывает требования: {', '.join(sorted(reqs - in_tz))}")
    if in_tz - reqs:
        problems.append(f"ТЗ ссылается на несуществующие требования: {', '.join(sorted(in_tz - reqs))}")
    return problems


def no_pii(doc: str, ctx: dict) -> list[str]:
    problems = []
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", doc):
        problems.append("в документе найден e-mail (PII) — используйте маски/примеры без реальных данных")
    if re.search(r"\+\d[\d\s\-()]{9,}\d", doc):
        problems.append("в документе найден номер телефона (PII)")
    return problems
