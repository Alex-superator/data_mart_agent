"""Ядро harness: цикл агента, инструменты, guardrails, бюджеты, human-in-the-loop, трассировка."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Budget
from .llm import LLM, parse_action
from .stages import INPUTS, STAGES, Stage, system_prompt
from .tools import ToolError, ToolRegistry
from .tracing import Tracer


class StageFailed(Exception):
    pass


class BudgetExceeded(StageFailed):
    pass


@dataclass
class StageRecord:
    name: str
    status: str = "pending"          # ok | failed
    attempts: int = 1
    tool_calls: int = 0
    llm_calls: int = 0
    violations: list[list[str]] = field(default_factory=list)
    error: str = ""
    path: str = ""


@dataclass
class RunResult:
    ok: bool
    records: list[StageRecord]
    artifacts: dict[str, str]
    llm_calls: int
    error: str = ""


Approver = Callable[[str, str], tuple[bool, str]]


def auto_approve(stage: str, doc: str) -> tuple[bool, str]:
    return True, ""


class Orchestrator:
    def __init__(self, llm: LLM, tools: ToolRegistry, tracer: Tracer, out_dir: Path,
                 budget: Budget = Budget(), approve: Approver = auto_approve):
        self.llm, self.tools, self.tracer = llm, tools, tracer
        self.out_dir, self.budget, self.approve = Path(out_dir), budget, approve
        self.llm_calls = 0

    def run(self, feature_text: str, params: dict) -> RunResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        artifacts: dict[str, str] = {}
        records: list[StageRecord] = []
        self.tracer.emit("run_start", title=params["title"], provider=self.llm.name)
        for idx, stage in enumerate(STAGES, 1):
            rec = StageRecord(stage.name)
            records.append(rec)
            try:
                doc = self._run_stage(stage, rec, feature_text, params, artifacts)
            except StageFailed as exc:
                rec.status, rec.error = "failed", str(exc)
                self.tracer.emit("stage_failed", stage=stage.name, error=str(exc))
                return RunResult(False, records, artifacts, self.llm_calls, str(exc))
            rec.status = "ok"
            artifacts[stage.name] = doc
            path = self.out_dir / f"{idx:02d}_{stage.name}.md"
            path.write_text(doc + "\n", encoding="utf-8")
            rec.path = str(path)
            self.tracer.emit("stage_done", stage=stage.name, attempts=rec.attempts, tool_calls=rec.tool_calls)
        self.tracer.emit("run_done", llm_calls=self.llm_calls)
        return RunResult(True, records, artifacts, self.llm_calls)

    # ------------------------------------------------------------------
    def _run_stage(self, stage: Stage, rec: StageRecord, feature_text: str, params: dict,
                   artifacts: dict[str, str]) -> str:
        self.tracer.emit("stage_start", stage=stage.name)
        system = system_prompt(stage, self.tools.specs(stage.tools))
        context = "".join(f'<artifact name="{n}">{artifacts[n]}</artifact>\n'
                          for n in INPUTS[stage.name] if n in artifacts)
        first = (f"<request>{json.dumps(params, ensure_ascii=False)}</request>\n"
                 f"<feature_text>{feature_text}</feature_text>\n"
                 f"<artifacts>\n{context}</artifacts>\nВыполни этап «{stage.title}».")
        messages = [{"role": "user", "content": first}]
        steps = 0
        while True:
            if self.llm_calls >= self.budget.max_total_llm_calls:
                raise BudgetExceeded(f"исчерпан общий лимит вызовов LLM ({self.budget.max_total_llm_calls})")
            if steps >= self.budget.max_steps_per_stage:
                raise BudgetExceeded(f"этап '{stage.name}': превышен лимит ходов ({self.budget.max_steps_per_stage})")
            steps += 1
            raw = self.llm.complete(system, messages)
            self.llm_calls += 1
            rec.llm_calls += 1
            messages.append({"role": "assistant", "content": raw})
            kind, payload = parse_action(raw)
            self.tracer.emit("llm_response", stage=stage.name, action=kind, preview=raw[:160])

            if kind == "invalid":
                messages.append({"role": "user", "content": f"GUARDRAIL_FEEDBACK: формат ответа неверен — {payload}"})
                continue

            if kind == "tool":
                name, args = payload["name"], payload["args"]
                try:
                    result = self.tools.call(name, args, stage.tools)
                    obs = f'<observation name="{name}">{json.dumps(result, ensure_ascii=False)}</observation>'
                    self.tracer.emit("tool_call", stage=stage.name, tool=name, args=args, ok=True)
                    self.tracer.emit("tool_result", stage=stage.name, tool=name, result=result)
                except ToolError as exc:
                    obs = f'<observation name="{name}" status="error">{exc}</observation>'
                    self.tracer.emit("tool_call", stage=stage.name, tool=name, args=args, ok=False, error=str(exc))
                rec.tool_calls += 1
                messages.append({"role": "user", "content": obs})
                continue

            # итоговый документ → guardrails
            doc = payload
            ctx = dict(artifacts)
            violations = [v for check in stage.validators for v in check(doc, ctx)]
            self.tracer.emit("guardrails", stage=stage.name, attempt=rec.attempts, violations=violations)
            if violations:
                rec.violations.append(violations)
                if rec.attempts > self.budget.max_retries_per_stage:
                    raise StageFailed(f"этап '{stage.name}': guardrails не пройдены после {rec.attempts} попыток: "
                                      + "; ".join(violations))
                rec.attempts += 1
                bullets = "\n".join(f"- {v}" for v in violations)
                messages.append({"role": "user", "content":
                                 f"GUARDRAIL_FEEDBACK: документ отклонён автоматическими проверками:\n{bullets}\n"
                                 "Исправь и верни документ целиком."})
                continue

            ok, comment = self.approve(stage.name, doc)   # human-in-the-loop
            self.tracer.emit("human_gate", stage=stage.name, approved=ok, comment=comment)
            if not ok:
                if rec.attempts > self.budget.max_retries_per_stage:
                    raise StageFailed(f"этап '{stage.name}': отклонён человеком: {comment}")
                rec.attempts += 1
                messages.append({"role": "user", "content": f"HUMAN_FEEDBACK: {comment}. Доработай документ."})
                continue
            return doc
