"""LLM-слой. Протокол harness: модель отвечает <tool>{json}</tool> или <final>markdown</final>.

MockLLM — детерминированная «модель-заглушка»: проходит тот же цикл (инструменты → итог → ретрай по
guardrails), поэтому весь harness можно демонстрировать и тестировать без API-ключа.
AnthropicLLM — реальная модель через HTTPS (stdlib, без зависимостей).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Protocol

TOOL_RE = re.compile(r"<tool>(.*?)</tool>", re.S)
FINAL_RE = re.compile(r"<final>(.*?)</final>", re.S)


class LLM(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict]) -> str: ...


def parse_action(text: str) -> tuple[str, object]:
    """→ ("tool", {name,args}) | ("final", markdown) | ("invalid", причина)."""
    tool, final = TOOL_RE.search(text), FINAL_RE.search(text)
    if tool and (not final or tool.start() < final.start()):
        try:
            payload = json.loads(tool.group(1))
        except json.JSONDecodeError as exc:
            return "invalid", f"JSON внутри <tool> невалиден: {exc}"
        if not isinstance(payload, dict) or "name" not in payload:
            return "invalid", "внутри <tool> нужен объект {\"name\": ..., \"args\": {...}}"
        payload.setdefault("args", {})
        return "tool", payload
    if final:
        return "final", final.group(1).strip()
    return "invalid", "ответ должен содержать либо <tool>{...}</tool>, либо <final>...</final>"


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 4000, timeout: int = 120):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("Нужна переменная окружения ANTHROPIC_API_KEY (или используйте --provider mock)")
        self.max_tokens, self.timeout = max_tokens, timeout

    def complete(self, system: str, messages: list[dict]) -> str:
        body = json.dumps({"model": self.model, "max_tokens": self.max_tokens,
                           "system": system, "messages": messages}).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json", "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.load(resp)
                return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 529) and attempt < 2:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise
        raise RuntimeError("не удалось получить ответ модели")


# ----------------------------------------------------------------------------------------------
# MockLLM
# ----------------------------------------------------------------------------------------------
def _observations(messages: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for m in messages:
        if m["role"] != "user":
            continue
        for name, payload in re.findall(r'<observation name="(\w+)">(.*?)</observation>', m["content"], re.S):
            out.setdefault(name, []).append(json.loads(payload))
    return out


def _plan(stage: str, p: dict, obs: dict, artifacts: dict) -> dict | None:
    """Следующий вызов инструмента или None, если пора выдавать итог."""
    srcs = p["sources"]
    if stage == "research":
        cats, ests = obs.get("catalog_lookup", []), obs.get("estimate_load", [])
        if len(cats) < len(srcs):
            return {"name": "catalog_lookup", "args": {"source": srcs[len(cats)]}}
        if len(ests) < len(srcs):
            c = cats[len(ests)]
            return {"name": "estimate_load", "args": {
                "rows_per_day": c["rows_per_day"], "avg_row_bytes": c["avg_row_bytes"],
                "peak_factor": float(p["peak_factor"]), "retention_days": p["retention_days"]}}
    elif stage == "cjm":
        if not obs.get("list_personas"):
            return {"name": "list_personas", "args": {}}
    elif stage == "architecture":
        if not obs.get("select_engine"):
            m = re.search(r"Суммарное хранение \(сжатое\): ([\d.]+) ТБ", artifacts.get("research", ""))
            return {"name": "select_engine", "args": {
                "latency_sla_s": p["freshness_sla_seconds"], "update_pattern": p["update_pattern"],
                "data_volume_tb": float(m.group(1)) if m else 1.0, "concurrency": p["concurrent_users"]}}
        if not obs.get("mermaid_flow"):
            e = obs["select_engine"][0]
            edges = [[s, e["ingest"], "events/CDC"] for s in srcs]
            edges += [[e["ingest"], "DQ-gate", "поток"], ["DQ-gate", e["serving"], "валидные строки"],
                      ["DQ-gate", "Quarantine (HDFS)", "отклонённые"],
                      [e["batch"], e["serving"], "backfill"], [e["serving"], "Витрина для BI/ML", "SQL"]]
            return {"name": "mermaid_flow", "args": {"edges": edges}}
    elif stage == "requirements":
        if not obs.get("suggest_dq_rules"):
            return {"name": "suggest_dq_rules", "args": {"columns": p["key_columns"]}}
    return None


class MockLLM:
    name = "mock"

    def __init__(self, inject_fault_stage: str | None = None):
        self.inject_fault_stage = inject_fault_stage
        self.calls = 0

    def complete(self, system: str, messages: list[dict]) -> str:
        from . import mock_docs  # отложенный импорт: шаблоны документов

        self.calls += 1
        stage = re.search(r"\[stage=(\w+)\]", system).group(1)
        first = messages[0]["content"]
        params = json.loads(re.search(r"<request>(.*?)</request>", first, re.S).group(1))
        artifacts = dict(re.findall(r'<artifact name="(\w+)">(.*?)</artifact>', first, re.S))
        obs = _observations(messages)
        action = _plan(stage, params, obs, artifacts)
        if action:
            return "<tool>" + json.dumps(action, ensure_ascii=False) + "</tool>"
        got_feedback = any("GUARDRAIL_FEEDBACK" in m["content"] for m in messages if m["role"] == "user")
        faulty = stage == self.inject_fault_stage and not got_feedback
        doc = mock_docs.render(stage, params, obs, artifacts, faulty)
        return "<final>\n" + doc + "\n</final>"
