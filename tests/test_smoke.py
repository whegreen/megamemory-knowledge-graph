"""Smoke test paket: self-test reasoning, MCP tools/list, guard, sanitasi."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def run(cmd: list[str], *, inp: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, env=ENV, input=inp,
                          capture_output=True, text=True, timeout=60)


class SmokeTest(unittest.TestCase):
    def test_reasoning_self_test(self):
        p = run([sys.executable, "-m", "megamemory_kg.cli", "--self-test"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("SELF-TEST PASS", p.stdout)

    def test_version(self):
        p = run([sys.executable, "-m", "megamemory_kg.cli", "--version"])
        self.assertEqual(p.stdout.strip(), "0.2.0")

    def test_mcp_tools(self):
        req = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n' \
              '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        p = run([sys.executable, "-m", "megamemory_kg.mcp_server"], inp=req)
        self.assertEqual(p.returncode, 0, p.stderr)
        replies = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        names = [t["name"] for r in replies if r.get("id") == 2
                 for t in r["result"]["tools"]]
        self.assertEqual(names, ["kg_health", "kg_validate", "kg_query",
                                 "kg_reason", "kg_suggest_edges"])

    def test_guard_self_test(self):
        p = run(["node", "--no-warnings", str(ROOT / "guard" / "guard.mjs"), "self-test"])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_cli_db_path_as_string(self):
        """--db dari argparse adalah str; load_graph harus menerimanya."""
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "knowledge.db"
            con = sqlite3.connect(db)
            con.executescript("""
              CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, kind TEXT, summary TEXT,
                why TEXT, file_refs TEXT, parent_id TEXT, created_at TEXT,
                updated_at TEXT, removed_at TEXT);
              CREATE TABLE edges(id INTEGER PRIMARY KEY AUTOINCREMENT, from_id TEXT,
                to_id TEXT, relation TEXT, description TEXT);
              INSERT INTO nodes VALUES('n','N','decision','[APPLIED] keputusan',NULL,'doc:a','','','',NULL);
            """)
            con.close()
            p = run([sys.executable, "-m", "megamemory_kg.cli",
                     "--db", str(db), "health", "--text"])
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn('"node_count": 1', p.stdout)

    def test_sanitization(self):
        p = run(["sh", str(ROOT / "scripts" / "check_sanitization.sh")])
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
