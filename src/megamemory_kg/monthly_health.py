#!/usr/bin/env python3
"""Tulis ringkasan kg_health bulanan ke file markdown (default: docs/kg-health-YYYY-MM.md).

Stdlib only, READ-ONLY terhadap knowledge.db. Ditemani automation cron bulanan.
Output sengaja tidak di-commit otomatis — user me-review dulu.

Usage:
  mmkg monthly [--db PATH] [--rules PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from . import reasoning as kg
except ImportError:  # eksekusi langsung sebagai script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import reasoning as kg  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ringkasan kg_health bulanan")
    ap.add_argument("--db", action="append", default=None, help="path knowledge.db (bisa diulang)")
    ap.add_argument("--rules", default=None, help="path mode_rules.json (atau KG_MODE_RULES)")
    ap.add_argument("--out", default=None, help="file output; default docs/kg-health-YYYY-MM.md")
    a = ap.parse_args(argv)

    paths = a.db if a.db is not None else kg.default_db_paths(False)
    if not paths:
        print("Tidak ada knowledge.db yang terbaca", file=sys.stderr)
        return 1

    now = datetime.now(timezone(timedelta(hours=7)))  # Asia/Jakarta
    rules = kg.load_mode_rules(a.rules)
    h = kg.health(kg.load_graph(paths), rules)
    path = Path(a.out) if a.out else Path("docs") / f"kg-health-{now:%Y-%m}.md"

    lines = [
        f"# KG Health — {now:%Y-%m}",
        "",
        f"Generated: {now:%Y-%m-%d %H:%M} WIB",
        f"Sumber: `{', '.join(str(p) for p in paths)}` (project-only).",
        "",
        "| Metrik | Nilai |",
        "|---|---:|",
        f"| Node | {h['node_count']} |",
        f"| Edge | {h['edge_count']} |",
        f"| Orphan | {h['orphan_count']} |",
        f"| Kontradiksi terbuka | {h['contradiction_count']} |",
        f"| Kontradiksi resolved | {h['resolved_contradiction_count']} |",
        f"| Cluster | {h['cluster_count']} |",
        f"| `file_refs` coverage | {h['file_refs_coverage']} |",
        f"| Verdict | {h['verdict']} |",
        f"| Orphan ratio | {h['orphan_ratio']} |",
        f"| Typed edge share | {h['typed_edge_share']} |",
        "",
    ]
    if "modes" in h:
        lines += ["## Per mode", "", "| Mode | Node | Edge | Orphan | Coverage |",
                  "|---|---:|---:|---:|---:|"]
        lines += [f"| {name} | {m['node_count']} | {m['edge_count']} | "
                  f"{m['orphan_count']} | {m['file_refs_coverage']} |"
                  for name, m in h["modes"].items()]
        lines += [""]
    lines += ["## Alasan verdict", ""]
    lines += [f"- {r}" for r in h["verdict_reasons"]] or ["- (tidak ada)"]
    lines += ["", "## Kontradiksi resolved", ""]
    lines += [f"- `{nid}`" for nid in h["resolved_contradiction_nodes"]] or ["- (tidak ada)"]
    lines += ["", "## Hub teratas", ""]
    lines += [f"- `{x['id']}` — degree {x['degree']}" for x in h["hub_nodes"]] or ["- (tidak ada)"]
    lines += [
        "",
        "Orphan perlu triage lewat `kg_suggest_edges` (dry-run) + persetujuan user sebelum `megamemory_link`.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"{path} | node={h['node_count']} edge={h['edge_count']} "
          f"orphan={h['orphan_count']} resolved={h['resolved_contradiction_count']} "
          f"verdict={h['verdict']} ({'; '.join(h['verdict_reasons']) or 'bersih'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
