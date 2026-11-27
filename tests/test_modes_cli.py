"""Test CLI audit mode: mmkg modes / mmkg monthly."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

RULES = {
    "version": 3,
    "default": "general",
    "modes": {
        "code": {"id_prefixes": ["kg-"], "file_ref_hints": ["src/"]},
        "general": {"file_ref_hints": ["docs/"]},
    },
    "overrides": {},
}


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "megamemory_kg.cli", *args],
                          cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=60)


class ModesCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "knowledge.db"
        con = sqlite3.connect(self.db)
        con.executescript(
            "CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, kind TEXT, summary TEXT, "
            "why TEXT, file_refs TEXT, parent_id TEXT, created_at TEXT, "
            "updated_at TEXT, removed_at TEXT);"
            "CREATE TABLE edges(from_id TEXT, to_id TEXT, relation TEXT, description TEXT);"
        )
        con.execute("INSERT INTO nodes(id,name,kind,summary,file_refs) VALUES(?,?,?,?,?)",
                    ("kg-a", "A", "feature", "s", '["src/a.ts"]'))
        con.execute("INSERT INTO nodes(id,name,kind,summary,file_refs) VALUES(?,?,?,?,?)",
                    ("plain", "P", "feature", "s", '["src/b.ts"]'))
        con.commit()
        con.close()
        self.rules = Path(self.tmp.name) / "mode_rules.json"
        self.rules.write_text(json.dumps(RULES), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_modes_json(self):
        p = run(["modes", "--json", "--db", str(self.db), "--rules", str(self.rules)])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["counts"], {"code": 1, "general": 1})
        self.assertEqual(data["suspicious"], ["plain"])

    def test_modes_check_exit_code(self):
        p = run(["modes", "--check", "--db", str(self.db), "--rules", str(self.rules)])
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("suspicious", p.stdout)

    def test_modes_list_and_suggest(self):
        listed = run(["modes", "--list", "code", "--db", str(self.db),
                      "--rules", str(self.rules)])
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        self.assertIn("kg-a", listed.stdout)
        suggested = run(["modes", "--json", "--suggest", "--db", str(self.db),
                         "--rules", str(self.rules)])
        data = json.loads(suggested.stdout)
        self.assertEqual(data["suggested_overrides"]["plain"]["mode"], "code")

    def test_monthly_subcommand_is_available(self):
        p = run(["monthly", "--help"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--rules", p.stdout)


if __name__ == "__main__":
    unittest.main()
