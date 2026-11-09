"""Разбор запроса на фичу (markdown → структурированные параметры)."""
from __future__ import annotations

import re

DEFAULTS = {
    "freshness_sla_seconds": 60,
    "update_pattern": "upsert",
    "concurrent_users": 50,
    "peak_factor": 3.0,
    "retention_days": 365,
}
REQUIRED = ("sources", "key_columns")
INT_KEYS = {"freshness_sla_seconds", "concurrent_users", "retention_days"}
FLOAT_KEYS = {"peak_factor"}


def coerce(key: str, value: str):
    if key in INT_KEYS:
        return int(value)
    if key in FLOAT_KEYS:
        return float(value)
    if key == "sources":
        return [s.strip() for s in value.split(",") if s.strip()]
    if key == "key_columns":
        cols = []
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            name, _, typ = item.partition(":")
            cols.append({"name": name.strip(), "type": typ.strip() or "string"})
        return cols
    return value.strip()


def parse_request(text: str, overrides: dict[str, str] | None = None) -> dict:
    params: dict = dict(DEFAULTS)
    title = re.search(r"^#\s*(.+)$", text, re.M)
    params["title"] = title.group(1).strip() if title else "Без названия"

    section = re.search(r"^##\s*Параметры\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    body = section.group(1) if section else text
    for key, value in re.findall(r"^-\s*(\w+):\s*(.+)$", body, re.M):
        params[key] = coerce(key, value)
    for key, value in (overrides or {}).items():
        params[key] = coerce(key, value)

    missing = [k for k in REQUIRED if not params.get(k)]
    if missing:
        raise ValueError(f"В запросе не заданы обязательные параметры: {', '.join(missing)}")
    if params["update_pattern"] not in {"append", "upsert"}:
        raise ValueError("update_pattern должен быть 'append' или 'upsert'")
    return params
