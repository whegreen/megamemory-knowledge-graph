#!/usr/bin/env python3
"""Audit klasifikasi mode megamemory (read-only).

Node yang jatuh ke mode default tetapi seluruh `file_refs`-nya mengarah ke
mode lain disebut *suspicious* — biasanya perlu `overrides` di file aturan.

Usage:
  mmkg modes --check
  mmkg modes --list coding
  mmkg modes --json --suggest
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:
    from . import reasoning as kg
except ImportError:  # eksekusi langsung sebagai script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import reasoning as kg  # noqa: E402


def parse_refs(raw) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def suspicious(node: dict, mode: str, rules: dict) -> bool:
    """Flag node mode-default yang semua ref-nya milik mode lain.

    Arah sebaliknya sengaja tidak dicek: node non-default memang lazim
    merujuk dokumen/tool lain. `file_ref_hints` berasal dari file aturan.
    """
    if mode != rules["default"]:
        return False
    refs = parse_refs(node.get("file_refs"))
    if not refs:
        return False
    own = tuple(rules["modes"][mode].get("file_ref_hints", []))
    if own and any(r.startswith(own) for r in refs):
        return False
    return any(
        spec.get("file_ref_hints") and any(r.startswith(tuple(spec["file_ref_hints"])) for r in refs)
        for name, spec in rules["modes"].items() if name != mode
    )


def suggested_mode(node: dict, rules: dict) -> str | None:
    refs = parse_refs(node.get("file_refs"))
    for name, spec in rules["modes"].items():
        if name == rules["default"]:
            continue
        hints = tuple(spec.get("file_ref_hints", []))
        if hints and any(r.startswith(hints) for r in refs):
            return name
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", action="append", default=None,
                    help="path knowledge.db (bisa diulang); default cwd/.megamemory/knowledge.db")
    ap.add_argument("--rules", default=None, help="path mode_rules.json (atau KG_MODE_RULES)")
    ap.add_argument("--check", action="store_true", help="exit 1 bila ada node suspicious")
    ap.add_argument("--list", metavar="MODE", help="tampilkan node pada mode ini")
    ap.add_argument("--json", action="store_true", help="output JSON ringkas")
    ap.add_argument("--suggest", action="store_true", help="usulkan overrides untuk node suspicious")
    args = ap.parse_args(argv)

    rules = kg.load_mode_rules(args.rules)
    if rules is None:
        ap.error("KG_MODE_RULES tidak di-set dan --rules tidak diberikan")
    if args.list and args.list not in rules["modes"]:
        ap.error(f"mode tidak dikenal: {args.list}; tersedia: {', '.join(rules['modes'])}")

    paths = [Path(x) for x in args.db] if args.db else kg.default_db_paths(False)
    graph = kg.load_graph(paths)
    classified = [(n, *kg.classify_mode(n, rules)) for n in kg.active_nodes(graph)]

    if args.list:
        for node, mode, reason in classified:
            if mode == args.list:
                print(f"{node['id']}\t{reason}\t{len(parse_refs(node.get('file_refs')))}")
        return 0

    counts = Counter(mode for _, mode, _ in classified)
    reasons = Counter((mode, reason) for _, mode, reason in classified)
    flagged = [(n, mode) for n, mode, _ in classified if suspicious(n, mode, rules)]

    suggestions = {}
    if args.suggest:
        for node, mode in flagged:
            target = suggested_mode(node, rules)
            if target:
                suggestions[node["id"]] = {"mode": target,
                                            "note": "file_refs mengarah ke mode lain"}
    payload = {
        "total": len(classified),
        "counts": {name: counts.get(name, 0) for name in rules["modes"]},
        "reasons": {f"{m}/{r}": v for (m, r), v in sorted(reasons.items())},
        "suspicious": [n["id"] for n, _ in flagged],
    }
    if args.suggest:
        payload["suggested_overrides"] = suggestions

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=None))
        return 1 if args.check and flagged else 0

    width = max(len(m) for m in rules["modes"])
    print(f"total   : {len(classified)}")
    for mode in rules["modes"]:
        print(f"{mode:<{width}} : {counts.get(mode, 0)}")
    print("reason  : " + ", ".join(
        f"{m}/{r}={v}" for (m, r), v in sorted(reasons.items()))) or "reason  : -"
    if flagged:
        print(f"suspicious ({len(flagged)}):")
        for node, mode in flagged:
            suffix = f" → {suggestions[node['id']]['mode']}" if node["id"] in suggestions else ""
            print(f"  {node['id']}{suffix}")
    else:
        print("suspicious: 0")
    return 1 if args.check and flagged else 0


if __name__ == "__main__":
    sys.exit(main())
