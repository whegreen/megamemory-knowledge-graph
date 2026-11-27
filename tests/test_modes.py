"""Test untuk dimensi mode generik (skema v2) di megamemory_kg.reasoning."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from megamemory_kg import reasoning as kg  # noqa: E402

RULES = {
    "version": 2,
    "default": "general",
    "modes": {
        "code": {"id_prefixes": ["kg-", "mcp-"], "ids": ["config-loader"]},
        "ops": {"id_prefixes": ["dec-", "mcp-"]},  # mcp- sengaja tabrakan: code menang
        "general": {},
    },
    "overrides": {"doc-special": {"mode": "code", "note": "override uji"}},
}


class ModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "knowledge.db"
        con = sqlite3.connect(self.db)
        con.executescript(
            """
            CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, kind TEXT, summary TEXT,
              why TEXT, file_refs TEXT, parent_id TEXT, created_at TEXT,
              updated_at TEXT, removed_at TEXT);
            CREATE TABLE edges(id INTEGER PRIMARY KEY AUTOINCREMENT, from_id TEXT,
              to_id TEXT, relation TEXT, description TEXT);
            """
        )
        for nid in ("kg-a", "mcp-b", "doc-c", "dec-d", "doc-special"):
            con.execute(
                "INSERT INTO nodes(id,name,kind,summary,file_refs) VALUES(?,?,?,?,?)",
                (nid, nid, "module" if nid.startswith(("kg-", "mcp-")) else "decision",
                 "uji", '["docs/x.md"]'),
            )
        con.execute("INSERT INTO edges(from_id,to_id,relation) VALUES(?,?,?)",
                    ("kg-a", "mcp-b", "implements"))
        con.execute("INSERT INTO edges(from_id,to_id,relation) VALUES(?,?,?)",
                    ("doc-c", "dec-d", "connects_to"))
        con.commit()
        con.close()
        self.rules_path = Path(self.tmp.name) / "mode_rules.json"
        self.rules_path.write_text(json.dumps(RULES), encoding="utf-8")
        self.graph = kg.load_graph([self.db], include_root=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_precedence_and_arbitrary_modes(self):
        rules = kg.load_mode_rules(str(self.rules_path))
        self.assertEqual(kg.classify_mode({"id": "kg-a"}, rules), ("code", "prefix"))
        self.assertEqual(kg.classify_mode({"id": "mcp-b"}, rules), ("code", "prefix"))
        self.assertEqual(kg.classify_mode({"id": "dec-d"}, rules), ("ops", "prefix"))
        self.assertEqual(
            kg.classify_mode({"id": "config-loader"}, rules), ("code", "id"))
        self.assertEqual(kg.classify_mode({"id": "doc-c"}, rules), ("general", "default"))
        self.assertEqual(
            kg.classify_mode({"id": "doc-special"}, rules), ("code", "override"))

    def test_health_by_mode(self):
        rules = kg.load_mode_rules(str(self.rules_path))
        health = kg.health(self.graph, rules)
        self.assertEqual(health["modes"]["code"]["node_count"], 3)
        self.assertEqual(health["modes"]["ops"]["node_count"], 1)
        self.assertEqual(health["modes"]["general"]["node_count"], 1)
        self.assertEqual(health["modes"]["code"]["intra_edges"], 1)
        self.assertNotIn("modes", kg.health(self.graph))

    def test_query_mode(self):
        rules = kg.load_mode_rules(str(self.rules_path))
        out = kg.query(self.graph, "mode", mode="code", mode_rules=rules)
        self.assertEqual(out["node_count"], 3)
        self.assertEqual(len(out["nodes"]), 3)
        bad = kg.query(self.graph, "mode", mode="tooling", mode_rules=rules)
        self.assertIn("error", bad)
        self.assertEqual(bad["tersedia"], ["code", "general", "ops"])
        filtered = kg.query(self.graph, "search", query="doc", mode="general", mode_rules=rules)
        self.assertEqual(filtered["count"], 1)

    def test_validation(self):
        bad = {**RULES, "version": 1}
        p = self.rules_path.with_name("v1.json")
        p.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ValueError):
            kg.load_mode_rules(str(p))
        bad = {**RULES, "default": "nope"}
        p.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ValueError):
            kg.load_mode_rules(str(p))

    def test_example_rules_are_valid(self):
        example = ROOT / "mode_rules.example.json"
        rules = kg.load_mode_rules(str(example))
        self.assertIn(rules["default"], rules["modes"])
        self.assertEqual(rules["version"], 2)

    def test_without_rules(self):
        self.assertIn("error", kg.query(self.graph, "mode"))
        self.assertNotIn("modes", kg.health(self.graph))


if __name__ == "__main__":
    unittest.main()
