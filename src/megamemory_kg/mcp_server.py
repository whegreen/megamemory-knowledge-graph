#!/usr/bin/env python3
"""
megamemory_kg.mcp_server — stdio MCP server: megamemory-knowledge-graph.

READ-ONLY by design. Tools hanya membaca/menganalisis knowledge.db; semua write
tetap lewat megamemory MCP (create_concept / update_concept / link).

Default: project-only (cwd), bukan root shared.

Config: <project>/.opencode/opencode.json · env KG_DB_PATHS, KG_PROJECT, KG_INCLUDE_ROOT
Env:
  KG_DB_PATHS     - daftar path knowledge.db dipisah ':' (override default)
  KG_INCLUDE_ROOT - '1' untuk menyertakan root shared (default '0' = project-only)
Cache graph diinvalidasi otomatis saat metadata knowledge.db **atau** file `-wal`/`-shm`-nya berubah — write SQLite masuk WAL lebih dulu dan mtime file `.db` tidak berubah selama writer masih membuka koneksi.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from . import reasoning as kg
except ImportError:  # eksekusi langsung sebagai script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import reasoning as kg  # noqa: E402

SERVER_NAME = "megamemory-knowledge-graph"
try:
    from . import __version__ as VERSION
except ImportError:  # eksekusi langsung sebagai script
    VERSION = "0.1.0"
PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "kg_health",
        "description": "Health check graph megamemory project-only (vault): orphan node, kontradiksi, cluster, hub, coverage file_refs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_root": {"type": "boolean", "default": False,
                                 "description": "Set true untuk menyertakan root shared; default project-only (vault)"}
            },
        },
    },
    {
        "name": "kg_validate",
        "description": "Adversarial validation sebelum write: antonym, negasi, konsistensi, sumber. Kontradiksi tidak auto-resolve — dua sisi ditampilkan.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "ID/entity yang direview, mis. orders:INV-14"},
                "claim": {"type": "string", "description": "Klaim baru yang akan ditulis"},
                "source": {"type": "string", "description": "Sumber bukti klaim (wajib untuk lolos check sumber)"},
                "kind": {"type": "string", "description": "Opsional: kind node yang diharapkan"}
            },
            "required": ["entity", "claim"],
        },
    },
    {
        "name": "kg_query",
        "description": "Traversal graph: search, neighbors, path, hubs, clusters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subcommand": {"type": "string", "enum": ["search", "neighbors", "path", "hubs", "clusters"]},
                "query": {"type": "string", "description": "Untuk search"},
                "id": {"type": "string", "description": "Untuk neighbors"},
                "from": {"type": "string", "description": "Untuk path"},
                "to": {"type": "string", "description": "Untuk path"},
                "hops": {"type": "integer", "default": 1},
                "undirected": {"type": "boolean", "default": False},
                "top": {"type": "integer", "default": 5}
            },
            "required": ["subcommand"],
        },
    },
    {
        "name": "kg_reason",
        "description": "Susun konteks reasoning 6 langkah (OBSERVE > CLASSIFY > HYPOTHESIZE > VERIFY > REFINE > DECOMPOSE) dari graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Pertanyaan yang ingin dianalisis"}
            },
            "required": ["question"],
        },
    },
    {
        "name": "kg_suggest_edges",
        "description": "Dry-run: usulkan edge untuk orphan node dari kesamaan token dan file_refs. Tidak menulis; apply via megamemory_link.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25}
            },
        },
    },
]

_CACHE: dict[tuple, dict] = {}


def _db_signature(paths: list) -> tuple:
    """Signature cache: .db + -wal + -shm.

    Write SQLite yang masih di WAL tidak mengubah mtime/size file .db utama,
    jadi hanya memantau .db membuat graph basi setelah megamemory menulis.
    """
    sig = []
    for p in paths:
        for f in (Path(p), Path(str(p) + "-wal"), Path(str(p) + "-shm")):
            try:
                st = os.stat(f)
                sig.append((str(f), st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((str(f), None, None))
    return tuple(sig)


def _graph(include_root: bool | None = None) -> dict:
    root = os.environ.get("KG_INCLUDE_ROOT", "0") == "1" if include_root is None else include_root
    paths = kg.default_db_paths(root)
    key = (tuple(str(p) for p in paths), root, _db_signature(paths))
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = kg.load_graph(paths)
    return _CACHE[key]


def call_tool(name: str, args: dict) -> dict:
    try:
        if name == "kg_health":
            graph = _graph(args.get("include_root"))
            data = kg.health(graph)
            data["project"] = os.environ.get("KG_PROJECT") or Path.cwd().name
            data["server_version"] = VERSION
            data["db_paths"] = graph["sources"]
        elif name == "kg_validate":
            if not args.get("entity") or not args.get("claim"):
                raise ValueError("entity dan claim wajib diisi")
            data = kg.validate(_graph(), args["entity"], args["claim"],
                               args.get("source"), args.get("kind"))
        elif name == "kg_query":
            sub = args.get("subcommand")
            kw = {"hops": args.get("hops", 1), "undirected": args.get("undirected", False),
                  "top": args.get("top", 5), "query": args.get("query"),
                  "id": args.get("id"), "from": args.get("from"), "to": args.get("to")}
            data = kg.query(_graph(), sub, **kw)
        elif name == "kg_reason":
            if not args.get("question"):
                raise ValueError("question wajib diisi")
            data = kg.reason(_graph(), args["question"])
        elif name == "kg_suggest_edges":
            data = kg.suggest_edges(_graph(), int(args.get("limit", 25)))
        else:
            raise ValueError(f"tool tidak dikenali: {name}")
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}]}
    except Exception as exc:  # noqa: BLE001 — MCP harus balas error, bukan crash
        return {"isError": True,
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}]}


def handle(msg: dict) -> dict | None:
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None  # notification
    if method == "initialize":
        requested = msg.get("params", {}).get("protocolVersion", PROTOCOL)
        result = {
            "protocolVersion": requested if requested.startswith("20") else PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params", {})
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> int:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
