import unittest

from dmharness import guardrails as g


class GuardrailsTest(unittest.TestCase):
    def test_nfr_must_be_measurable(self):
        bad = "| НФТ-01 | Производительность | высокая |\n| НФТ-02 | Свежесть | ≤ 60 с |"
        self.assertEqual(len(g.measurable_nfr(bad, {})), 1)
        self.assertIn("НФТ-01", g.measurable_nfr(bad, {})[0])

    def test_traceability_detects_gaps_and_ghosts(self):
        ctx = {"requirements": "ФТ-01 ФТ-02 НФТ-01"}
        self.assertEqual(g.traceability("ФТ-01 ФТ-02 НФТ-01", ctx), [])
        self.assertTrue(any("не покрывает" in v for v in g.traceability("ФТ-01", ctx)))
        self.assertTrue(any("несуществующие" in v for v in g.traceability("ФТ-01 ФТ-02 НФТ-01 ФТ-99", ctx)))

    def test_mermaid_required(self):
        self.assertTrue(g.require_mermaid("text", {}))
        self.assertEqual(g.require_mermaid("```mermaid\nflowchart LR\n  A-->B\n```", {}), [])

    def test_pii_detection(self):
        self.assertTrue(g.no_pii("пишите на ivan@example.com", {}))
        self.assertTrue(g.no_pii("тел. +7 (495) 123-45-67", {}))
        self.assertEqual(g.no_pii("колонка email маскируется", {}), [])

    def test_required_sections(self):
        self.assertEqual(g.require_sections("## A")("## A\ntext", {}), [])
        self.assertEqual(len(g.require_sections("## A", "## B")("## A", {})), 1)


if __name__ == "__main__":
    unittest.main()
