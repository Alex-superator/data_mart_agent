import unittest
from pathlib import Path

from dmharness.tools import ToolError, build_registry

CATALOG = Path(__file__).resolve().parent.parent / "examples" / "data_catalog.json"


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.reg = build_registry(CATALOG)

    def test_allowlist_blocks_foreign_tool(self):
        with self.assertRaises(ToolError):
            self.reg.call("select_engine", {}, allowed=("catalog_lookup",))

    def test_argument_validation(self):
        with self.assertRaises(ToolError):
            self.reg.call("catalog_lookup", {}, allowed=("catalog_lookup",))
        with self.assertRaises(ToolError):
            self.reg.call("catalog_lookup", {"source": 1}, allowed=("catalog_lookup",))
        with self.assertRaises(ToolError):
            self.reg.call("catalog_lookup", {"source": "x", "extra": 1}, allowed=("catalog_lookup",))

    def test_unknown_source_is_tool_error_not_crash(self):
        with self.assertRaises(ToolError):
            self.reg.call("catalog_lookup", {"source": "nope"}, allowed=("catalog_lookup",))

    def test_estimate_load_math(self):
        r = self.reg.call("estimate_load", {"rows_per_day": 86_400_000, "avg_row_bytes": 100,
                                            "peak_factor": 2.0, "retention_days": 10}, allowed=("estimate_load",))
        self.assertEqual(r["avg_eps"], 1000.0)
        self.assertEqual(r["peak_eps"], 2000.0)
        self.assertEqual(r["gb_per_day"], 8.64)

    def test_engine_selection_rules(self):
        call = lambda **kw: self.reg.call("select_engine", kw, allowed=("select_engine",))
        base = dict(latency_sla_s=60, update_pattern="upsert", data_volume_tb=3.0, concurrency=50)
        self.assertEqual((call(**base)["ingest"], call(**base)["serving"]), ("Flink", "ClickHouse"))
        self.assertEqual(call(**{**base, "concurrency": 300})["serving"], "StarRocks")
        self.assertEqual(call(**{**base, "latency_sla_s": 900})["ingest"], "Spark")
        self.assertEqual(call(**{**base, "data_volume_tb": 80.0})["cold_tier"], "HDFS/Hadoop (Parquet)")

    def test_dq_rules_for_key_columns(self):
        cols = [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "decimal"},
                {"name": "email", "type": "string"}]
        rules = self.reg.call("suggest_dq_rules", {"columns": cols}, allowed=("suggest_dq_rules",))["rules"]
        kinds = {(r["column"], r["rule"]) for r in rules}
        self.assertIn(("order_id", "unique_per_version"), kinds)
        self.assertIn(("amount", "value >= 0"), kinds)
        self.assertIn(("email", "pii_masking_required"), kinds)


if __name__ == "__main__":
    unittest.main()
