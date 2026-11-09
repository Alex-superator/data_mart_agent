from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_MODEL, Budget
from .evals import evaluate, render_report
from .llm import AnthropicLLM, MockLLM
from .orchestrator import Orchestrator, auto_approve
from .request import parse_request
from .stages import STAGES
from .tools import build_registry
from .tracing import Tracer


def _interactive_approve(stage: str, doc: str) -> tuple[bool, str]:
    print(f"\n--- [{stage}] предпросмотр ---")
    print("\n".join(doc.splitlines()[:25]))
    answer = input("\nПринять этап? [Enter = да, либо текст замечания]: ").strip()
    return (True, "") if not answer else (False, answer)


def _run(args) -> int:
    text = Path(args.request).read_text(encoding="utf-8")
    overrides = dict(kv.split("=", 1) for kv in args.set)
    params = parse_request(text, overrides)
    out = Path(args.out)
    tracer = Tracer(out / "trace.jsonl")
    llm = AnthropicLLM(args.model) if args.provider == "anthropic" else MockLLM(
        inject_fault_stage="requirements" if args.inject_fault else None)
    orch = Orchestrator(llm, build_registry(args.catalog), tracer, out,
                        Budget(max_steps_per_stage=args.max_steps),
                        _interactive_approve if args.interactive else auto_approve)

    print(f"== dmharness: {params['title']}\n   provider={llm.name}  run_id={tracer.run_id}  out={out}/")
    result = orch.run(text, params)
    for i, rec in enumerate(result.records, 1):
        mark = "✓" if rec.status == "ok" else "✗"
        retry = f"  (повторов этапа: {rec.attempts - 1})" if rec.attempts > 1 else ""
        print(f"[{i}/{len(STAGES)}] {mark} {rec.name:<13} инструментов={rec.tool_calls} ходов LLM={rec.llm_calls}{retry}")
        for v in rec.violations:
            print("        ↳ guardrails: " + "; ".join(v))
    ev = evaluate(result, tracer.events)
    (out / "EVAL_REPORT.md").write_text(render_report(result, ev, tracer.run_id), encoding="utf-8")
    print(f"\nОценка прогона: {ev['score']} % ({ev['passed']}/{ev['total']} проверок)")
    if result.error:
        print(f"Сбой: {result.error}")
    print(f"Артефакты: {out}/*.md · трейс: {out}/trace.jsonl · отчёт: {out}/EVAL_REPORT.md")
    return 0 if result.ok and ev["score"] == 100 else 1


def _tools(args) -> int:
    reg = build_registry(args.catalog)
    for spec in reg.specs(reg.names()):
        print(f"- {spec['name']}({', '.join(f'{k}: {v}' for k, v in spec['params'].items())})\n    {spec['description']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dmharness", description="Harness AI-агента системного аналитика")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="прогнать конвейер: запрос → Research → CJM → HLD → ФТ/НФТ → ТЗ")
    r.add_argument("request", help="markdown-файл с запросом на фичу")
    r.add_argument("--catalog", default="examples/data_catalog.json")
    r.add_argument("--out", default="out")
    r.add_argument("--provider", choices=["mock", "anthropic"], default="mock")
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--inject-fault", action="store_true", help="mock: сломать НФТ, чтобы показать ретрай по guardrails")
    r.add_argument("--interactive", action="store_true", help="human-in-the-loop: подтверждать каждый этап")
    r.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="переопределить параметр запроса")
    r.add_argument("--max-steps", type=int, default=Budget().max_steps_per_stage, help="лимит ходов на этап")
    r.set_defaults(fn=_run)
    t = sub.add_parser("tools", help="показать реестр инструментов")
    t.add_argument("--catalog", default="examples/data_catalog.json")
    t.set_defaults(fn=_tools)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
