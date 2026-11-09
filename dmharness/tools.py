"""Реестр инструментов: типизированные, с allowlist по этапам и валидацией аргументов."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class ToolError(Exception):
    """Ошибка вызова инструмента — возвращается агенту как observation, а не роняет прогон."""


_TYPES = {"str": str, "int": int, "float": (int, float), "list": list, "dict": dict}


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, str]  # имя -> тип; суффикс "?" = необязательный
    fn: Callable[..., dict]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, description: str, params: dict[str, str]):
        def deco(fn):
            self._tools[name] = Tool(name, description, params, fn)
            return fn

        return deco

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self, names) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "params": t.params}
            for n in names
            if (t := self._tools.get(n))
        ]

    def call(self, name: str, args: dict, allowed) -> dict:
        if name not in allowed:
            raise ToolError(f"инструмент '{name}' не разрешён на этом этапе (allowlist: {sorted(allowed)})")
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"неизвестный инструмент '{name}'")
        if not isinstance(args, dict):
            raise ToolError("args должен быть JSON-объектом")

        clean: dict = {}
        for pname, ptype in tool.params.items():
            optional = ptype.endswith("?")
            base = ptype.rstrip("?")
            if pname not in args:
                if optional:
                    continue
                raise ToolError(f"{name}: отсутствует обязательный аргумент '{pname}'")
            value = args[pname]
            if isinstance(value, bool) or not isinstance(value, _TYPES[base]):
                raise ToolError(f"{name}: аргумент '{pname}' должен быть типа {base}")
            clean[pname] = value
        extra = set(args) - set(tool.params)
        if extra:
            raise ToolError(f"{name}: неизвестные аргументы {sorted(extra)}")
        try:
            return tool.fn(**clean)
        except ToolError:
            raise
        except Exception as exc:  # инструмент не должен ронять harness
            raise ToolError(f"{name}: {type(exc).__name__}: {exc}") from exc


def build_registry(catalog_path: str | Path) -> ToolRegistry:
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    reg = ToolRegistry()

    @reg.register("catalog_lookup", "Паспорт источника из каталога данных: система, объём, схема, владелец, PII.",
                  {"source": "str"})
    def catalog_lookup(source):
        src = catalog["sources"].get(source)
        if src is None:
            raise ToolError(f"источник '{source}' не найден; доступны: {sorted(catalog['sources'])}")
        return {"source": source, **src}

    @reg.register("estimate_load", "Оценка нагрузки: events/s (средн./пик), ГБ/сутки, хранение (сырое и сжатое).",
                  {"rows_per_day": "int", "avg_row_bytes": "int", "peak_factor": "float", "retention_days": "int"})
    def estimate_load(rows_per_day, avg_row_bytes, peak_factor, retention_days):
        avg_eps = rows_per_day / 86400
        gb_day = rows_per_day * avg_row_bytes / 1e9
        raw_tb = gb_day * retention_days / 1000
        return {
            "avg_eps": round(avg_eps, 1),
            "peak_eps": round(avg_eps * peak_factor, 1),
            "gb_per_day": round(gb_day, 2),
            "raw_tb": round(raw_tb, 2),
            "stored_tb_compressed": round(raw_tb / 4, 2),
            "assumption": "сжатие колоночного хранилища 4:1; репликация не учтена",
        }

    @reg.register("select_engine", "Выбор стека по SLA свежести, паттерну обновлений, объёму и конкурентности.",
                  {"latency_sla_s": "int", "update_pattern": "str", "data_volume_tb": "float", "concurrency": "int"})
    def select_engine(latency_sla_s, update_pattern, data_volume_tb, concurrency):
        why = []
        if latency_sla_s <= 300:
            ingest = "Flink"
            why.append(f"SLA свежести {latency_sla_s} с ≤ 300 с → потоковая обработка во Flink "
                       "(checkpoint-и, exactly-once, event-time, watermarks).")
        else:
            ingest = "Spark"
            why.append(f"SLA свежести {latency_sla_s} с > 300 с → достаточно микробатчей Spark под Airflow; "
                       "стриминг — лишняя операционная сложность.")
        if update_pattern == "upsert" and concurrency >= 100:
            serving, detail = "StarRocks", "Primary Key таблицы (upsert «на лету»)"
            why.append(f"Upsert + {concurrency} одновременных запросов (≥ 100) → StarRocks: PK-модель и высокая "
                       "конкурентность без FINAL-запросов.")
        elif update_pattern == "upsert":
            serving, detail = "ClickHouse", "ReplacingMergeTree + argMax/FINAL по версии"
            why.append(f"Upsert при {concurrency} одновременных запросах (< 100) → ClickHouse ReplacingMergeTree; "
                       "дедупликация по версии на чтении/слиянии.")
        else:
            serving, detail = "ClickHouse", "MergeTree (append-only), партиции по дате"
            why.append("Append-only → ClickHouse MergeTree, партиционирование по дате, TTL по retention.")
        cold = "HDFS/Hadoop (Parquet)" if data_volume_tb >= 50 else "не требуется"
        why.append(f"Объём {data_volume_tb} ТБ (сжатый): холодный слой — {cold}.")
        why.append("Backfill и тяжёлые пересчёты истории — Spark-джобы с идемпотентной записью.")
        return {"ingest": ingest, "serving": serving, "serving_detail": detail, "batch": "Spark",
                "cold_tier": cold, "rationale": why}

    @reg.register("suggest_dq_rules", "Подобрать DQ-правила по списку колонок [{name,type}]. Первая колонка — бизнес-ключ.",
                  {"columns": "list"})
    def suggest_dq_rules(columns):
        rules = []
        for i, col in enumerate(columns):
            if not isinstance(col, dict) or "name" not in col:
                raise ToolError("columns: ожидается список объектов {name, type}")
            name, low = col["name"], col["name"].lower()
            typ = str(col.get("type", "string")).lower()
            if low.endswith("_id"):
                rules.append({"column": name, "rule": "not_null", "severity": "critical"})
            if i == 0:
                rules.append({"column": name, "rule": "unique_per_version", "severity": "critical"})
            if any(k in low for k in ("amount", "price", "sum")):
                rules.append({"column": name, "rule": "value >= 0", "severity": "critical"})
            if low.endswith("_ts") or typ == "timestamp":
                rules.append({"column": name, "rule": "not_in_future", "severity": "critical"})
                rules.append({"column": name, "rule": "freshness_lag_within_sla", "severity": "warning"})
            if "email" in low:
                rules.append({"column": name, "rule": "regex_email", "severity": "warning"})
                rules.append({"column": name, "rule": "pii_masking_required", "severity": "critical"})
            if low == "status":
                rules.append({"column": name, "rule": "accepted_values (словарь из каталога)", "severity": "critical"})
        return {"rules": rules, "count": len(rules)}

    @reg.register("mermaid_flow", "Построить Mermaid flowchart по рёбрам [[from, to, label], ...].",
                  {"edges": "list"})
    def mermaid_flow(edges):
        ids: dict[str, str] = {}

        def nid(label: str) -> str:
            return ids.setdefault(label, f"N{len(ids) + 1}")

        lines = ["flowchart LR"]
        for edge in edges:
            if not (isinstance(edge, list) and len(edge) == 3):
                raise ToolError("edges: ожидается список троек [from, to, label]")
            a, b, label = (str(x).replace('"', "'") for x in edge)
            lines.append(f'    {nid(a)}["{a}"] -->|{label}| {nid(b)}["{b}"]')
        return {"mermaid": "\n".join(lines), "nodes": len(ids), "edges": len(edges)}

    @reg.register("list_personas", "Персоны пользователей фреймворка и их боли (из исследования).", {})
    def list_personas():
        return {"personas": catalog["personas"]}

    return reg
