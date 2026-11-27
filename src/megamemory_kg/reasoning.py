#!/usr/bin/env python3
"""
megamemory_kg.reasoning — deterministic graph reasoning over megamemory knowledge.db.

READ-ONLY. Stdlib only. Tidak pernah menulis ke knowledge.db — semua write tetap
lewat megamemory MCP (lihat NOTICE untuk atribusi).

Usage:
  python3 -m megamemory_kg.cli health [--db PATH] [--no-root] [--text]
  python3 -m megamemory_kg.cli validate --entity E --claim C [--source S] [--kind K]
  python3 -m megamemory_kg.cli query search Q | neighbors ID [--hops N] \\
                                  | path A B [--undirected] | hubs [--top N] | clusters | mode [MODE]
  python3 -m megamemory_kg.cli reason "pertanyaan"
  python3 -m megamemory_kg.cli suggest-edges [--limit N]
  python3 -m megamemory_kg.cli export [--out FILE]
  python3 -m megamemory_kg.cli --self-test

Default DB: cwd/.megamemory/knowledge.db. Root shared hanya bila KG_ROOT_DB di-set.
Override: env KG_DB_PATHS (dipisah ':') atau argumen --db (bisa diulang).
Config: KG_LABEL (label sumber), KG_PROJECT (nama project).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path

STOPWORDS = {
    "yang", "dan", "dengan", "untuk", "dari", "pada", "ini", "itu", "atau", "tidak",
    "the", "and", "for", "with", "from", "this", "that", "are", "was", "were", "has",
    "have", "not", "none", "bagi", "oleh", "dalam", "ke", "di", "sebuah", "adalah",
    "karena", "jika", "maka", "serta", "namun", "agar", "dapat", "sudah", "akan",
}

ANTONYMS = {
    "increase": "decrease", "improve": "worsen", "enable": "disable",
    "create": "destroy", "support": "oppose", "accelerate": "decelerate",
    "strengthen": "weaken", "expand": "contract", "advance": "retreat",
    "cause": "prevent", "require": "exclude", "allow": "forbid",
    "faster": "slower", "better": "worse", "higher": "lower", "more": "less",
    "above": "below", "before": "after", "start": "end", "active": "inactive",
    "live": "draft", "apply": "skip", "naik": "turun", "aktif": "nonaktif",
    "tambah": "kurang", "setuju": "tolak", "menambah": "mengurangi",
}
ANTONYMS.update({v: k for k, v in list(ANTONYMS.items())})

NEGATION_RE = re.compile(
    r"\b(not|never|no|none|does not|doesn't|cannot|can't|without|fails to|lacks?"
    r"|absence|tidak|bukan|tanpa|belum)\b", re.I
)
# Kata setelah "menggantikan" adalah istilah lama yang sudah ditinggalkan,
# bukan pernyataan yang masih berlaku (konvensi kg-review §4).
REPLACEMENT_RE = re.compile(r"\b(menggantikan|mengganti|replaces|replaced|supersedes|superseded)\b", re.I)
STATUS_RE = re.compile(r"^\[([A-Z_]+)\]\s*")
RESOLVED_RE = re.compile(r"^\[RESOLVED[^\]]*\]\s*")


# --------------------------------------------------------------------------- #
# Loading (WAL-safe via SQLite backup API)
# --------------------------------------------------------------------------- #

def default_db_paths(include_root: bool = False) -> list[Path]:
    env = os.environ.get("KG_DB_PATHS")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    paths = [Path.cwd() / ".megamemory" / "knowledge.db"]
    root = os.environ.get("KG_ROOT_DB")
    if include_root and root:
        p = Path(root).expanduser()
        if p.exists():
            paths.append(p)
    return [p for p in paths if p.exists()]


def source_labels(paths: list[Path]) -> list[str]:
    """Label sumber unik. Multi-DB dengan basename sama memakai nama parent.

    KG_LABEL meng-override semua; KG_ROOT_DB menandai DB root sebagai 'root'.
    """
    override = os.environ.get("KG_LABEL")
    if override:
        return [override] * len(paths)
    names = [p.name for p in paths]
    dupes = {n for n in names if names.count(n) > 1}
    labels = [p.parent.name if p.name in dupes else p.name for p in paths]
    root = os.environ.get("KG_ROOT_DB")
    if root:
        try:
            rp = Path(root).expanduser().resolve()
            labels = ["root" if p.expanduser().resolve() == rp else lab
                      for p, lab in zip(paths, labels)]
        except OSError:
            pass
    return labels


def _label(path: Path) -> str:
    return source_labels([path])[0]


def _collect(con: sqlite3.Connection, label: str, nodes: list, edges: list) -> None:
    node_cols = [r[1] for r in con.execute("PRAGMA table_info(nodes)")]
    edge_cols = [r[1] for r in con.execute("PRAGMA table_info(edges)")]
    nfields = ["id", "name", "kind", "summary", "why", "file_refs",
               "parent_id", "created_at", "updated_at", "removed_at"]
    efields = ["from_id", "to_id", "relation", "description"]
    if not set(nfields[:2]).issubset(node_cols) or not set(efields[:3]).issubset(edge_cols):
        raise ValueError(f"skema knowledge.db tidak dikenali ({label})")
    nsel = [f for f in nfields if f in node_cols]
    esel = [f for f in efields if f in edge_cols]
    for row in con.execute(f"SELECT {','.join(nsel)} FROM nodes"):
        n = dict(zip(nsel, row))
        n["source"] = label
        nodes.append(n)
    for row in con.execute(f"SELECT {','.join(esel)} FROM edges"):
        e = dict(zip(esel, row))
        e["source"] = label
        edges.append(e)


def load_graph(paths: list[Path] | None = None, include_root: bool = True) -> dict:
    paths = paths if paths is not None else default_db_paths(include_root)
    paths = [Path(p).expanduser() for p in paths]
    nodes: list[dict] = []
    edges: list[dict] = []
    used: list[str] = []
    labels = source_labels(paths)
    for p, label in zip(paths, labels):
        if not p.exists():
            continue
        src = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        snap = sqlite3.connect(":memory:")
        try:
            src.backup(snap)          # konsisten terhadap WAL, tanpa menyentuh file asli
            _collect(snap, label, nodes, edges)
            used.append(str(p))
        finally:
            snap.close()
            src.close()
    # dedup id: vault menang atas root
    seen: dict[str, dict] = {}
    for n in nodes:
        seen.setdefault(n["id"], n)
    enode = {n["id"] for n in seen.values()}
    dedup_edges: dict[tuple, dict] = {}
    for e in edges:
        if e.get("from_id") in enode and e.get("to_id") in enode:
            key = (e.get("from_id"), e.get("to_id"), e.get("relation"), e.get("description"))
            dedup_edges.setdefault(key, e)
    return {
        "nodes": list(seen.values()),
        "edges": list(dedup_edges.values()),
        "sources": used,
    }


def active_nodes(graph: dict) -> list[dict]:
    return [n for n in graph["nodes"] if not n.get("removed_at")]


def index_graph(graph: dict) -> dict:
    nodes = active_nodes(graph)
    by_id = {n["id"]: n for n in nodes}
    adj: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    radj: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for e in graph["edges"]:
        a, b = e.get("from_id"), e.get("to_id")
        adj[a].append((b, e))
        radj[b].append((a, e))
    return {"nodes": nodes, "by_id": by_id, "adj": adj, "radj": radj}


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) >= 4 and t not in STOPWORDS and not t.isdigit()}


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #

def clusters(idx: dict) -> list[list[str]]:
    parent = {n["id"]: n["id"] for n in idx["nodes"]}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for e in idx_nodes_edges(idx):
        union(e[0], e[1])
    groups: dict[str, list[str]] = defaultdict(list)
    for nid in parent:
        groups[find(nid)].append(nid)
    return sorted(groups.values(), key=len, reverse=True)


def idx_nodes_edges(idx: dict):
    seen = set()
    for a, lst in idx["adj"].items():
        for b, e in lst:
            key = tuple(sorted((a, b))) + (e.get("relation"),)
            if key not in seen:
                seen.add(key)
                yield a, b


# --------------------------------------------------------------------------- #
# Mode rules (opsional) — nama mode berasal dari file aturan, bukan dari kode
# --------------------------------------------------------------------------- #

def load_mode_rules(path: str | None = None) -> dict | None:
    """Muat aturan mode dari argumen atau env KG_MODE_RULES. None = mode mati.

    Skema v2/v3: {"version": 2|3, "default": <nama>, "modes": {<nama>: {...}},
    "overrides": {...}}. v3 menambah ref_prefixes, source_labels, thresholds,
    dan typed_edge_prefixes. File contoh: mode_rules.example.json.
    """
    p = path or os.environ.get("KG_MODE_RULES")
    if not p:
        return None
    f = Path(p).expanduser()
    if not f.exists():
        raise FileNotFoundError(f"KG_MODE_RULES tidak ditemukan: {f}")
    data = json.loads(f.read_text(encoding="utf-8"))
    for key in ("version", "default", "modes"):
        if key not in data:
            raise ValueError(f"mode_rules kehilangan field: {key}")
    if data["version"] not in (2, 3):
        raise ValueError(f"mode_rules versi {data['version']} tidak didukung (butuh 2 atau 3)")
    modes = data["modes"]
    if not isinstance(modes, dict) or not modes:
        raise ValueError("mode_rules.modes harus berisi minimal satu mode")
    if data["default"] not in modes:
        raise ValueError(f"mode default {data['default']!r} tidak ada di modes")
    for name, spec in modes.items():
        if not isinstance(spec, dict):
            raise ValueError(f"mode {name!r} harus berupa objek")
        for field in ("ids", "id_prefixes", "ref_prefixes", "source_labels"):
            if field in spec and not isinstance(spec[field], list):
                raise ValueError(f"mode {name!r}.{field} harus berupa list")
        if "thresholds" in spec:
            th = spec["thresholds"]
            if not isinstance(th, dict):
                raise ValueError(f"mode {name!r}.thresholds harus berupa objek")
            for key in ("file_refs_coverage_min", "orphan_ratio_max"):
                if key in th and not isinstance(th[key], (int, float)):
                    raise ValueError(f"mode {name!r}.thresholds.{key} harus angka")
    data.setdefault("overrides", {})
    for nid, ov in data["overrides"].items():
        mode = ov.get("mode") if isinstance(ov, dict) else ov
        if mode not in modes:
            raise ValueError(f"overrides.{nid} memilih mode tak dikenal: {mode}")
    if "typed_edge_prefixes" in data and not isinstance(data["typed_edge_prefixes"], list):
        raise ValueError("typed_edge_prefixes harus berupa list")
    return data


def _node_refs(node: dict) -> list[str]:
    raw = node.get("file_refs")
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def classify_mode(node: dict, rules: dict) -> tuple[str, str]:
    """Return (mode, reason).

    Precedence: override > exact id > id_prefixes > ref_prefixes >
    source_labels > default. Tiap tingkat memakai urutan deklarasi `modes`
    (first match menang). `file_ref_hints` tidak dipakai di sini — itu hanya
    untuk audit (mmkg modes --check).
    """
    nid = node.get("id", "")
    override = (rules.get("overrides") or {}).get(nid)
    if override:
        mode = override.get("mode") if isinstance(override, dict) else override
        return mode, "override"
    for name, spec in rules["modes"].items():
        if nid in spec.get("ids", []):
            return name, "id"
    for name, spec in rules["modes"].items():
        if nid.startswith(tuple(spec.get("id_prefixes", []))):
            return name, "prefix"
    refs = _node_refs(node)
    for name, spec in rules["modes"].items():
        prefixes = tuple(spec.get("ref_prefixes", []))
        if prefixes and any(r.startswith(prefixes) for r in refs):
            return name, "ref"
    for name, spec in rules["modes"].items():
        if node.get("source") in spec.get("source_labels", []):
            return name, "source"
    return rules["default"], "default"


def mode_stats(idx: dict, edges: list[dict], rules: dict) -> dict:
    """Statistik per mode; cross_edges sengaja dihitung di kedua mode.

    Mode yang dideklarasikan tapi belum punya node tetap muncul dengan count 0.
    """
    buckets: dict[str, set[str]] = defaultdict(set)
    for n in idx["nodes"]:
        mode, _ = classify_mode(n, rules)
        buckets[mode].add(n["id"])
    connected = {x for e in edges for x in (e.get("from_id"), e.get("to_id"))}
    out: dict[str, dict] = {
        name: {"node_count": 0, "edge_count": 0, "intra_edges": 0,
               "cross_edges": 0, "orphan_count": 0, "file_refs_coverage": 0.0}
        for name in rules["modes"]
    }
    for mode, ids in buckets.items():
        intra = sum(1 for e in edges
                    if e.get("from_id") in ids and e.get("to_id") in ids)
        cross = sum(1 for e in edges
                    if (e.get("from_id") in ids) != (e.get("to_id") in ids))
        with_refs = sum(1 for n in idx["nodes"]
                        if n["id"] in ids and n.get("file_refs") not in (None, "", "null", "[]"))
        out[mode] = {
            "node_count": len(ids),
            "edge_count": intra + cross,
            "intra_edges": intra,
            "cross_edges": cross,
            "orphan_count": sum(1 for i in ids if i not in connected),
            "file_refs_coverage": round(with_refs / len(ids), 3) if ids else 0.0,
        }
    return out


def health(graph: dict, mode_rules: dict | None = None) -> dict:
    idx = index_graph(graph)
    nodes, edges = idx["nodes"], graph["edges"]
    n, e = len(nodes), len(edges)
    connected = {x for edge in edges for x in (edge.get("from_id"), edge.get("to_id"))}
    orphans = [x["id"] for x in nodes if x["id"] not in connected]
    contradictions = [x["id"] for x in nodes
                      if (STATUS_RE.match(x.get("summary") or "") or [None, ""])[1] == "CONTRADICTION"]
    # Hanya node contra-* yang merupakan kontradiksi; dec-* berstatus RESOLVED
    # adalah keputusan, bukan node kontradiksi (konvensi kg-review §4).
    resolved = [x["id"] for x in nodes
                if x["id"].startswith("contra-") and RESOLVED_RE.match(x.get("summary") or "")]
    with_refs = sum(1 for x in nodes if x.get("file_refs") not in (None, "", "null", "[]"))
    groups = clusters(idx)
    degree = defaultdict(int)
    for edge in edges:
        degree[edge.get("from_id")] += 1
        degree[edge.get("to_id")] += 1
    hubs = sorted(degree.items(), key=lambda kv: kv[1], reverse=True)[:5]
    per_source = defaultdict(lambda: {"nodes": 0, "edges": 0})
    for x in nodes:
        per_source[x.get("source")]["nodes"] += 1
    for edge in edges:
        per_source[edge.get("source")]["edges"] += 1
    orphan_ratio = round(len(orphans) / n, 3) if n else 0.0
    coverage = round(with_refs / n, 3) if n else 0.0
    typed_prefixes = tuple(str(p) for p in
                           ((mode_rules or {}).get("typed_edge_prefixes") or []))
    typed_re = (re.compile(r"^(?:" + "|".join(re.escape(p) for p in typed_prefixes)
                           + r")\b", re.I) if typed_prefixes else None)
    verdict_reasons = []
    if contradictions:
        verdict_reasons.append("kontradiksi terbuka: %d" % len(contradictions))
    if orphan_ratio > 0.5:
        verdict_reasons.append("orphan %.0f%% > 50%%" % (orphan_ratio * 100))
    if coverage < 0.6:
        verdict_reasons.append("file_refs coverage %.3f < 0,6" % coverage)
    result = {
        "sources": graph["sources"],
        "node_count": n,
        "edge_count": e,
        "orphan_count": len(orphans),
        "orphan_nodes": orphans[:10],
        "orphan_truncated": len(orphans) > 10,
        "contradiction_count": len(contradictions),
        "contradiction_nodes": contradictions[:20],
        "resolved_contradiction_count": len(resolved),
        "resolved_contradiction_nodes": resolved[:20],
        "cluster_count": len(groups),
        "largest_clusters": [len(g) for g in groups[:5]],
        "hub_nodes": [{"id": k, "degree": v,
                       "label": idx["by_id"].get(k, {}).get("name", k)} for k, v in hubs],
        "file_refs_coverage": coverage,
        "orphan_ratio": orphan_ratio,
        "verdict_reasons": verdict_reasons,
        "relation_distribution": dict(sorted(
            defaultdict(int, {r: sum(1 for x in edges if x.get("relation") == r)
                              for r in {x.get("relation") for x in edges}}).items())),
        "typed_edge_share": round(sum(
            1 for x in edges
            if x.get("relation") != "connects_to"
            or (typed_re and typed_re.match(x.get("description") or ""))
        ) / e, 3) if e else 0.0,
        "density": round(e / (n * (n - 1)), 4) if n > 1 else 0.0,
        "avg_connectivity": round(2 * e / n, 2) if n else 0.0,
        "per_source": dict(per_source),
        "verdict": "GOOD" if not verdict_reasons else "NEEDS_ATTENTION",
    }
    if mode_rules:
        modes = mode_stats(idx, edges, mode_rules)
        result["modes"] = modes
        for name, spec in mode_rules["modes"].items():
            m = modes.get(name)
            th = spec.get("thresholds") or {}
            if not m or not m["node_count"]:
                continue
            cov_min = th.get("file_refs_coverage_min")
            if cov_min is not None and m["file_refs_coverage"] < cov_min:
                result["verdict_reasons"].append(
                    f"{name}.file_refs coverage {m['file_refs_coverage']:.3f} < "
                    f"{str(cov_min).replace('.', ',')}")
            orphan_max = th.get("orphan_ratio_max")
            if orphan_max is not None:
                ratio = m["orphan_count"] / m["node_count"]
                if ratio > orphan_max:
                    result["verdict_reasons"].append(
                        f"{name}.orphan {ratio:.0%} > {orphan_max:.0%}")
        result["verdict"] = "GOOD" if not result["verdict_reasons"] else "NEEDS_ATTENTION"
    return result


# --------------------------------------------------------------------------- #
# Adversarial validation
# --------------------------------------------------------------------------- #

def _evidence_for(idx: dict, entity: str) -> tuple[dict | None, list[dict]]:
    target = entity.strip().lower()
    node = idx["by_id"].get(entity)
    if node is None:
        for n in idx["nodes"]:
            nid = (n.get("id") or "").lower()
            name = (n.get("name") or "").lower()
            if nid.endswith(target) or target in nid or (target and target in name):
                node = n
                break
    if node is None:
        return None, []
    nid = node["id"]
    related = [b for b, _ in idx["adj"].get(nid, [])] + [a for a, _ in idx["radj"].get(nid, [])]
    items = [{"id": nid, "summary": node.get("summary", ""), "why": node.get("why", "")}]
    for rid in related[:8]:
        r = idx["by_id"].get(rid)
        if r:
            items.append({"id": rid, "summary": r.get("summary", ""), "why": r.get("why", "")})
    return node, items


def check_antonyms(claim: str, evidence: list[dict]) -> dict:
    ct = tokens(claim)
    for item in evidence:
        et = tokens((item.get("summary") or "") + " " + (item.get("why") or ""))
        for word in ct:
            anti = ANTONYMS.get(word)
            if anti and anti in et and len(ct & et) >= 1:
                return {"check_type": "antonym", "passed": False,
                        "details": f"claim memakai '{word}', bukti lama memakai antonim '{anti}'",
                        "conflicting_fact": item}
    return {"check_type": "antonym", "passed": True, "details": "tidak ada konflik antonim"}


def check_negation(claim: str, evidence: list[dict]) -> dict:
    cneg = bool(NEGATION_RE.search(claim or ""))
    ct = tokens(claim)
    for item in evidence:
        # Polaritas diukur dari fakta, bukan `why`: catatan "X tidak dipakai" di `why`
        # menerangkan istilah lama, bukan menyangkal klaim baru (false positive F1).
        fact = item.get("summary") or ""
        ft = tokens(fact)
        eneg = bool(NEGATION_RE.search(fact))
        if cneg != eneg and len(ct & ft) >= 2:
            return {"check_type": "negation", "passed": False,
                    "details": f"claim {'menyangkal' if cneg else 'menegaskan'}, "
                               f"bukti lama {'menyangkal' if eneg else 'menegaskan'}",
                    "conflicting_fact": item}
        if not cneg:
            m = REPLACEMENT_RE.search(fact)
            if m:
                retired = tokens(fact[m.end():])
                old = retired & ct
                if old and (ft & ct):
                    return {"check_type": "negation", "passed": False,
                            "details": f"claim memakai istilah lama '{sorted(old)[0]}' "
                                       f"yang sudah digantikan",
                            "conflicting_fact": item}
    return {"check_type": "negation", "passed": True, "details": "tidak ada konflik negasi"}


def check_consistency(entity: str, claim: str, node: dict | None, kind: str | None) -> dict:
    issues = []
    if " " in entity or not entity.strip():
        issues.append("format entity tidak valid (harus <tabel>:<id> atau <prefix>-<id>)")
    if node and kind and node.get("kind") != kind:
        issues.append(f"kind mismatch: entity={node.get('kind')}, klaim={kind}")
    kv = dict(re.findall(r"\b([a-z_]+)\s*=\s*(\d+(?:\.\d+)?)", claim.lower()))
    if node:
        okv = dict(re.findall(r"\b([a-z_]+)\s*=\s*(\d+(?:\.\d+)?)",
                              (node.get("summary", "") + " " + node.get("why", "")).lower()))
        for key, val in kv.items():
            if key in okv and float(okv[key]):
                drift = abs(float(val) - float(okv[key])) / abs(float(okv[key]))
                if drift > 1.0:
                    issues.append(f"nilai '{key}' berubah {drift * 100:.0f}% ({okv[key]} -> {val})")
    cy = set(re.findall(r"\b20\d{2}\b", claim))
    oy = set(re.findall(r"\b20\d{2}\b", (node or {}).get("why", "") or ""))
    if cy and oy and cy != oy:
        issues.append(f"potential temporal conflict: klaim {sorted(cy)} vs node {sorted(oy)}")
    return {"check_type": "consistency", "passed": not issues,
            "details": "; ".join(issues) if issues else "konsisten dengan node existing"}


def check_source(source: str | None) -> dict:
    if not source or not source.strip():
        return {"check_type": "source", "passed": False,
                "details": "klaim tidak punya sumber; semua bukti harus punya atribusi"}
    return {"check_type": "source", "passed": True, "details": f"sumber: {source}"}


def validate(graph: dict, entity: str, claim: str, source: str | None = None,
             kind: str | None = None) -> dict:
    idx = index_graph(graph)
    node, evidence = _evidence_for(idx, entity)
    checks = [check_antonyms(claim, evidence), check_negation(claim, evidence),
              check_consistency(entity, claim, node, kind), check_source(source)]
    hard = [c for c in checks if not c["passed"] and c["check_type"] in ("antonym", "negation")]
    soft = [c for c in checks if not c["passed"]]
    result = "contradiction" if hard else ("needs_review" if soft else "accepted")
    if result == "contradiction":
        rec = (f"Kontradiksi: {hard[0]['details']}. JANGAN auto-resolve — buat node contra-* "
               "dengan dua sisi dan minta keputusan user.")
    elif result == "needs_review":
        rec = f"Flagged: {soft[0]['details']}. Tampilkan ke user, tambahkan confidence rendah."
    else:
        rec = "Semua check lolos. Aman ditulis ke graph."
    return {
        "validation_result": result,
        "resolution": result,
        "entity": entity,
        "claim": claim,
        "existing_node": ({"id": node["id"], "summary": node.get("summary", "")} if node else None),
        "checks": checks,
        "both_sides": {"existing": evidence, "new": {"claim": claim, "source": source}},
        "recommendation": rec,
    }


# --------------------------------------------------------------------------- #
# Query / reasoning
# --------------------------------------------------------------------------- #

def query(graph: dict, sub: str, **kw) -> dict:
    idx = index_graph(graph)
    rules = kw.get("mode_rules")
    if sub == "mode":
        if not rules:
            return {"error": "KG_MODE_RULES tidak di-set"}
        wanted = kw.get("mode") or rules["default"]
        names = set(rules["modes"])
        if wanted not in names:
            return {"error": f"mode tidak dikenali: {wanted}", "tersedia": sorted(names)}
        limit = int(kw.get("limit", 50))
        items = []
        for n in idx["nodes"]:
            mode, reason = classify_mode(n, rules)
            if mode != wanted:
                continue
            item = {"id": n["id"], "name": n.get("name"),
                    "kind": n.get("kind"), "reason": reason}
            if kw.get("include_summary"):
                item["summary"] = n.get("summary")
            items.append(item)
        stats = mode_stats(idx, graph["edges"], rules).get(wanted, {})
        return {"mode": wanted, **stats,
                "nodes": items[:limit], "truncated": len(items) > limit}
    if sub == "search":
        q = (kw.get("query") or "").lower()
        hits = [n for n in idx["nodes"]
                if q in (n.get("id", "") + " " + n.get("name", "") + " " + n.get("summary", "")).lower()]
        if kw.get("mode"):
            if not rules:
                return {"error": "KG_MODE_RULES tidak di-set"}
            hits = [n for n in hits if classify_mode(n, rules)[0] == kw["mode"]]
        limit = int(kw.get("limit", 10))
        return {"count": len(hits), "results": [{k: h.get(k) for k in
                ("id", "name", "kind", "summary", "source")} for h in hits[:limit]]}
    if sub == "neighbors":
        start, hops = kw.get("id"), int(kw.get("hops", 1))
        start = _resolve(idx, start)
        if not start:
            return {"error": "entity tidak ditemukan"}
        seen, queue, out = {start}, deque([(start, 0)]), []
        while queue:
            cur, d = queue.popleft()
            if d >= hops:
                continue
            for nxt, edge in idx["adj"].get(cur, []) + idx["radj"].get(cur, []):
                if nxt in seen:
                    continue
                seen.add(nxt)
                out.append({"id": nxt, "hops": d + 1, "relation": edge.get("relation")})
                queue.append((nxt, d + 1))
        total = len(out)
        return {"entity": start, "hops": hops, "count": total,
                "neighbors": out[:50], "truncated": total > 50}
    if sub == "path":
        a, b = _resolve(idx, kw.get("from")), _resolve(idx, kw.get("to"))
        if not a or not b:
            return {"found": False, "error": "entity tidak ditemukan", "from": a, "to": b}
        undirected = bool(kw.get("undirected"))
        adj = defaultdict(list)
        for e in graph["edges"]:
            adj[e["from_id"]].append((e["to_id"], e))
            if undirected:
                adj[e["to_id"]].append((e["from_id"], e))
        seen, queue = {a}, deque([(a, [], 1.0)])
        while queue:
            cur, path, conf = queue.popleft()
            if cur == b:
                return {"found": True, "path": path + [cur], "hops": len(path),
                        "confidence": round(conf, 4), "undirected": undirected}
            for nxt, e in adj.get(cur, []):
                if nxt in seen:
                    continue
                seen.add(nxt)
                w = float(e.get("weight") or e.get("kg:weight") or 1.0)
                queue.append((nxt, path + [cur], conf * w))
        return {"found": False, "from": a, "to": b}
    if sub == "hubs":
        return {"hubs": health(graph)["hub_nodes"][:int(kw.get("top", 5))]}
    if sub == "clusters":
        groups = clusters(idx)
        return {"count": len(groups),
                "clusters": [{"size": len(g), "sample": g[:5]} for g in groups[:20]]}
    return {"error": f"subcommand tidak dikenali: {sub}"}


def _resolve(idx: dict, value: str | None) -> str | None:
    if not value:
        return None
    if value in idx["by_id"]:
        return value
    vl = value.lower()
    for n in idx["nodes"]:
        if vl in n["id"].lower() or vl in (n.get("name") or "").lower():
            return n["id"]
    return None


def reason(graph: dict, question: str) -> dict:
    idx = index_graph(graph)
    qtok = tokens(question)
    scored = []
    for n in idx["nodes"]:
        hay = tokens(n.get("id", "") + " " + n.get("name", "") + " " + n.get("summary", ""))
        s = len(qtok & hay)
        if s:
            scored.append((s, n))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [n for _, n in scored[:12]]
    ids = {n["id"] for n in picked}
    internal = [e for e in graph["edges"] if e["from_id"] in ids and e["to_id"] in ids]
    kinds: dict[str, int] = defaultdict(int)
    for n in picked:
        kinds[n.get("kind", "unknown")] += 1
    rels: dict[str, int] = defaultdict(int)
    for e in internal:
        rels[e.get("relation", "unknown")] += 1
    bridge = [n["id"] for n in picked if
              len([e for e in graph["edges"] if n["id"] in (e["from_id"], e["to_id"])]) >= 3]
    return {
        "question": question,
        "observe": [{"id": n["id"], "kind": n.get("kind"), "summary": n.get("summary")}
                    for n in picked],
        "classify": {"kinds": dict(kinds), "relations": dict(rels),
                     "node_count": len(picked), "edge_count": len(internal)},
        "hypothesize": [
            f"Node dengan koneksi >=3 (kandidat bridge/hub): {', '.join(bridge)}" if bridge
            else "Tidak ada hub jelas dalam subgraph — kemungkinan topik terfragmentasi",
            f"Cluster dominan: {health(graph)['largest_clusters'][:3]}",
        ],
        "verify": [f"Periksa {len(internal)} edge internal sebelum menyimpulkan.",
                   "Jalankan kg_validate untuk setiap klaim baru yang akan ditulis."],
        "refine": "Pertahankan hanya hipotesis dengan bukti edge eksplisit; "
                  "tandai sisanya sebagai needs_observasi.",
        "decompose": ["Apakah ada node contra-* untuk topik ini?",
                      "Apakah orphan node terkait perlu dihubungkan (kg_suggest_edges)?",
                      "Apakah keputusan lama sudah diresolusi?"],
        "node_count_total": len(idx["nodes"]),
        "keyword_match_count": len(scored),
    }


def suggest_edges(graph: dict, limit: int = 25) -> dict:
    idx = index_graph(graph)
    connected = {x for e in graph["edges"] for x in (e.get("from_id"), e.get("to_id"))}
    orphans = [n for n in idx["nodes"] if n["id"] not in connected]
    out = []
    for n in orphans:
        nt = tokens(n.get("id", "") + " " + n.get("name", "") + " " + n.get("summary", ""))
        nrefs = set(re.findall(r"[\w./:-]+", n.get("file_refs") or ""))
        candidates = []
        for m in idx["nodes"]:
            if m["id"] == n["id"] or m["id"] in connected:
                continue
            mt = tokens(m.get("id", "") + " " + m.get("name", "") + " " + m.get("summary", ""))
            mrefs = set(re.findall(r"[\w./:-]+", m.get("file_refs") or ""))
            score = 3.0 * len(nrefs & mrefs) + 0.5 * len(nt & mt)
            if n.get("kind") == m.get("kind"):
                score += 0.5
            if score >= 2.0:
                candidates.append({"to": m["id"], "score": round(score, 2),
                                   "shared_tokens": sorted((nt & mt))[:8],
                                   "shared_refs": sorted((nrefs & mrefs))[:3]})
        candidates.sort(key=lambda c: c["score"], reverse=True)
        if candidates:
            out.append({"from": n["id"], "kind": n.get("kind"),
                        "summary": (n.get("summary") or "")[:120],
                        "candidates": candidates[:3]})
    out.sort(key=lambda x: x["candidates"][0]["score"], reverse=True)
    return {"dry_run": True, "orphan_count": len(orphans),
            "suggestion_count": len(out), "suggestions": out[:limit],
            "note": "Tidak ada write. Apply via megamemory_link setelah review user."}


# --------------------------------------------------------------------------- #
# Self-test (no repo writes)
# --------------------------------------------------------------------------- #

def self_test() -> None:
    con = sqlite3.connect(":memory:")
    con.executescript("""
      CREATE TABLE nodes(id TEXT PRIMARY KEY, name TEXT, kind TEXT, summary TEXT, why TEXT,
        file_refs TEXT, parent_id TEXT, created_at TEXT, updated_at TEXT, removed_at TEXT);
      CREATE TABLE edges(id INTEGER PRIMARY KEY AUTOINCREMENT, from_id TEXT, to_id TEXT,
        relation TEXT, description TEXT);
    """)
    con.executemany("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?)", [
        ("a", "A", "decision", "[APPLIED] INV-14 preventive-only (menggantikan override manual)", "Basis: audit 2024", "doc:a", "", "", "", None),
        ("b", "B", "feature", "order aktif", None, "orders:INV-14", "", "", "", None),
        ("c", "C", "feature", "orphan", "tanpa edge", "", "", "", "", None),
        ("d", "D", "decision", "[CONTRADICTION] lama vs baru", "dua sisi", "", "", "", "", None),
        ("contra-e", "E", "decision", "[RESOLVED: X] kontradiksi lama sudah diputuskan", "verdict user: Check me tidak dipakai", "", "", "", "", None),
    ])
    con.executemany("INSERT INTO edges(from_id,to_id,relation,description) VALUES(?,?,?,?)", [
        ("a", "b", "depends_on", "review mengevaluasi order"),
        ("d", "a", "connects_to", "sisi lama"),
        ("d", "b", "connects_to", "sisi baru"),
        ("contra-e", "a", "connects_to", "kontradiksi terselesaikan"),
    ])
    nodes, edges = [], []
    _collect(con, "test", nodes, edges)
    con.close()
    g = {"nodes": nodes, "edges": edges, "sources": ["test"]}
    h = health(g)
    assert h["node_count"] == 5, h
    assert h["edge_count"] == 4, h
    assert h["orphan_count"] == 1 and h["orphan_nodes"] == ["c"], h
    assert h["contradiction_count"] == 1, h
    assert h["resolved_contradiction_count"] == 1, h
    assert h["resolved_contradiction_nodes"] == ["contra-e"], h
    assert h["verdict"] == "NEEDS_ATTENTION", h
    v = validate(g, "a", "INV-14 tidak preventive-only", "studi-X")
    assert v["validation_result"] == "contradiction", v
    assert v["checks"][1]["check_type"] == "negation" and not v["checks"][1]["passed"], v
    v2 = validate(g, "a", "INV-14 preventive-only dengan kriteria eksplisit", "studi-Y")
    assert v2["validation_result"] == "accepted", v2
    # F1: "tidak dipakai" di `why` tidak boleh mengubah klaim yang benar jadi kontradiksi.
    v3 = validate(g, "contra-e", "kontradiksi lama sudah diputuskan", "studi-Z")
    assert v3["validation_result"] == "accepted", v3
    # Istilah setelah "menggantikan" tetap terdeteksi sebagai sisi lama.
    v4 = validate(g, "a", "INV-14 override manual", "studi-Z")
    assert v4["validation_result"] == "contradiction", v4
    q = query(g, "path", **{"from": "c", "to": "a"})
    assert not q["found"], q
    q2 = query(g, "path", **{"from": "d", "to": "b"})
    assert q2["found"] and q2["hops"] == 1, q2
    r = reason(g, "INV-14 preventive-only")
    assert r["observe"] and r["classify"]["node_count"] > 0, r
    s = suggest_edges(g)
    assert s["dry_run"] and s["orphan_count"] == 1, s
    print("SELF-TEST PASS — 16 assertions (health, resolved-contradiction, validate incl. F1/F2, query, reason, suggest-edges)")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _emit(obj: dict, text: bool = False) -> None:
    if text:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(obj, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Graph reasoning over megamemory (read-only)")
    ap.add_argument("--db", action="append", default=None, help="path knowledge.db (bisa diulang)")
    ap.add_argument("--no-root", action="store_true", help="jangan baca root shared")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--rules", help="path mode_rules.json (atau set KG_MODE_RULES)")
    sub = ap.add_subparsers(dest="cmd")

    p_h = sub.add_parser("health"); p_h.add_argument("--text", action="store_true")
    p_v = sub.add_parser("validate")
    p_v.add_argument("--entity", required=True); p_v.add_argument("--claim", required=True)
    p_v.add_argument("--source"); p_v.add_argument("--kind"); p_v.add_argument("--text", action="store_true")
    p_q = sub.add_parser("query")
    p_q.add_argument("sub", choices=["search", "neighbors", "path", "hubs", "clusters", "mode"])
    p_q.add_argument("args", nargs="*"); p_q.add_argument("--hops", type=int, default=1)
    p_q.add_argument("--undirected", action="store_true"); p_q.add_argument("--top", type=int, default=5)
    p_q.add_argument("--mode"); p_q.add_argument("--limit", type=int, default=50)
    p_q.add_argument("--include-summary", action="store_true")
    p_q.add_argument("--text", action="store_true")
    p_r = sub.add_parser("reason"); p_r.add_argument("question"); p_r.add_argument("--text", action="store_true")
    p_s = sub.add_parser("suggest-edges"); p_s.add_argument("--limit", type=int, default=25)
    p_s.add_argument("--text", action="store_true")
    p_e = sub.add_parser("export"); p_e.add_argument("--out")

    a = ap.parse_args(argv)
    if a.self_test:
        self_test()
        return 0
    paths = [Path(x).expanduser() for x in a.db] if a.db is not None else default_db_paths(not a.no_root)
    rules = load_mode_rules(a.rules)
    graph = load_graph(paths)
    if not graph["nodes"]:
        _emit({"error": "tidak ada knowledge.db yang terbaca", "paths": [str(p) for p in paths]})
        return 1
    if a.cmd == "health":
        _emit(health(graph, rules), a.text)
    elif a.cmd == "validate":
        _emit(validate(graph, a.entity, a.claim, a.source, a.kind), a.text)
    elif a.cmd == "query":
        kw = {"hops": a.hops, "undirected": a.undirected, "top": a.top,
              "mode": a.mode, "mode_rules": rules, "limit": a.limit,
              "include_summary": a.include_summary}
        if a.sub == "search":
            kw["query"] = " ".join(a.args)
        elif a.sub == "mode":
            kw["mode"] = a.args[0] if a.args else a.mode
        elif a.sub == "neighbors":
            kw["id"] = a.args[0] if a.args else None
        elif a.sub == "path":
            kw["from"], kw["to"] = (a.args + [None, None])[:2]
        _emit(query(graph, a.sub, **kw), a.text)
    elif a.cmd == "reason":
        _emit(reason(graph, a.question), a.text)
    elif a.cmd == "suggest-edges":
        _emit(suggest_edges(graph, a.limit), a.text)
    elif a.cmd == "export":
        out = a.out or "kg-graph.json"
        Path(out).write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        _emit({"written": out, "nodes": len(graph["nodes"]), "edges": len(graph["edges"])}, a.text)
    else:
        ap.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
