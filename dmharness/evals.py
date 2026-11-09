"""Оценка прогона: детерминированные проверки качества + сводка по этапам."""
from __future__ import annotations

from . import guardrails
from .orchestrator import RunResult
from .stages import STAGES


def evaluate(result: RunResult, events: list[dict]) -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("Все этапы конвейера завершены", result.ok and len(result.records) == len(STAGES),
        f"{sum(r.status == 'ok' for r in result.records)}/{len(STAGES)}")
    for rec in result.records:
        add(f"Этап «{rec.name}» прошёл guardrails", rec.status == "ok",
            f"попыток: {rec.attempts}, вызовов инструментов: {rec.tool_calls}")
    if result.ok:
        add("ТЗ трассируется на все ФТ/НФТ", not guardrails.traceability(result.artifacts["tz"], result.artifacts))
        engines = [e["result"]["serving"] for e in events if e["kind"] == "tool_result" and e.get("tool") == "select_engine"]
        if engines:
            eng = engines[-1]
            add("Выбранный движок согласован между HLD и ТЗ",
                eng in result.artifacts["architecture"] and eng in result.artifacts["tz"], eng)
        add("Все НФТ измеримы", not guardrails.measurable_nfr(result.artifacts["requirements"], {}))
    passed = sum(c["ok"] for c in checks)
    return {"checks": checks, "passed": passed, "total": len(checks),
            "score": round(100 * passed / len(checks)) if checks else 0}


def render_report(result: RunResult, ev: dict, run_id: str) -> str:
    lines = [f"# Отчёт о прогоне {run_id}", "",
             f"Итог: **{'успех' if result.ok else 'сбой'}**, оценка {ev['score']} % ({ev['passed']}/{ev['total']}), "
             f"вызовов LLM: {result.llm_calls}", "", "## Этапы", "",
             "| Этап | Статус | Попыток | Инструментов | Нарушения guardrails |", "|---|---|---|---|---|"]
    for r in result.records:
        viol = "<br>".join("; ".join(v) for v in r.violations) or "—"
        lines.append(f"| {r.name} | {r.status} | {r.attempts} | {r.tool_calls} | {viol} |")
    lines += ["", "## Проверки", "", "| Проверка | Результат | Детали |", "|---|---|---|"]
    for c in ev["checks"]:
        lines.append(f"| {c['name']} | {'✓' if c['ok'] else '✗'} | {c['detail']} |")
    if result.error:
        lines += ["", f"**Причина сбоя:** {result.error}"]
    return "\n".join(lines) + "\n"
