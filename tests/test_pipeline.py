import tempfile
import unittest
from pathlib import Path

from dmharness.config import Budget
from dmharness.evals import evaluate
from dmharness.llm import MockLLM, parse_action
from dmharness.orchestrator import Orchestrator
from dmharness.request import parse_request
from dmharness.tools import build_registry
from dmharness.tracing import Tracer

ROOT = Path(__file__).resolve().parent.parent
REQUEST = (ROOT / "examples" / "feature_request.md").read_text(encoding="utf-8")
CATALOG = ROOT / "examples" / "data_catalog.json"


def run(llm=None, budget=Budget(), overrides=None):
    tmp = tempfile.mkdtemp()
    tracer = Tracer(Path(tmp) / "trace.jsonl")
    orch = Orchestrator(llm or MockLLM(), build_registry(CATALOG), tracer, Path(tmp), budget)
    result = orch.run(REQUEST, parse_request(REQUEST, overrides))
    return result, tracer, Path(tmp)


class PipelineTest(unittest.TestCase):
    def test_happy_path_creates_all_artifacts(self):
        result, tracer, out = run()
        self.assertTrue(result.ok)
        self.assertEqual(len(list(out.glob("0*_*.md"))), 5)
        self.assertEqual(evaluate(result, tracer.events)["score"], 100)

    def test_guardrails_force_self_correction(self):
        result, _, _ = run(MockLLM(inject_fault_stage="requirements"))
        rec = next(r for r in result.records if r.name == "requirements")
        self.assertTrue(result.ok)
        self.assertEqual(rec.attempts, 2)
        self.assertIn("НФТ-03", rec.violations[0][0])

    def test_step_budget_stops_the_agent(self):
        result, _, _ = run(budget=Budget(max_steps_per_stage=2))
        self.assertFalse(result.ok)
        self.assertIn("лимит ходов", result.error)

    def test_total_llm_budget(self):
        result, _, _ = run(budget=Budget(max_total_llm_calls=3))
        self.assertFalse(result.ok)

    def test_whatif_changes_architecture(self):
        result, _, _ = run(overrides={"concurrent_users": "300", "freshness_sla_seconds": "900"})
        self.assertIn("serving=StarRocks", result.artifacts["architecture"])
        self.assertIn("ingest=Spark", result.artifacts["architecture"])
        self.assertIn("StarRocks", result.artifacts["tz"])

    def test_tz_traces_every_requirement(self):
        result, _, _ = run()
        for rid in ("ФТ-01", "ФТ-10", "НФТ-01", "НФТ-08"):
            self.assertIn(rid, result.artifacts["tz"])

    def test_parse_action(self):
        self.assertEqual(parse_action('<tool>{"name": "x"}</tool>')[0], "tool")
        self.assertEqual(parse_action("<final>doc</final>"), ("final", "doc"))
        self.assertEqual(parse_action("просто текст")[0], "invalid")
        self.assertEqual(parse_action("<tool>{broken</tool>")[0], "invalid")

    def test_request_validation(self):
        with self.assertRaises(ValueError):
            parse_request("# t\n\n## Параметры\n- freshness_sla_seconds: 10\n")
        with self.assertRaises(ValueError):
            parse_request(REQUEST, {"update_pattern": "merge"})


if __name__ == "__main__":
    unittest.main()
