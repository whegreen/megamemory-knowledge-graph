# megamemory-knowledge-graph

Read-only graph reasoning companion for a [megamemory](https://github.com/0xK3vin/MegaMemory)
`knowledge.db`. Exposes five MCP tools and an equivalent CLI; **never writes to SQLite**
(all mutations belong to the megamemory MCP).

Works with Python 3.10+, standard library only.

## Install

From a pinned git tag (available now — no clone, no venv):

```bash
uvx --from git+https://github.com/whegreen/megamemory-knowledge-graph@v0.2.0 megamemory-knowledge-graph
```

From PyPI (after release):

```bash
uvx megamemory-knowledge-graph@0.2.0
```

Alternatives: `pipx install megamemory-knowledge-graph`, or install from a git tag:

```bash
uvx --from git+https://github.com/whegreen/megamemory-knowledge-graph@v0.2.0 megamemory-knowledge-graph
```

## Configure OpenCode

```jsonc
{
  "mcp": {
    "megamemory-knowledge-graph": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/whegreen/megamemory-knowledge-graph@v0.2.0", "megamemory-knowledge-graph"],
      "enabled": true,
      "environment": { "KG_INCLUDE_ROOT": "0" }
    }
  }
}
```

Default database is `cwd/.megamemory/knowledge.db` — no configuration needed in a
project that already has a graph.

| Env | Purpose | Default |
|-----|---------|---------|
| `KG_DB_PATHS` | colon-separated DB paths (overrides discovery) | unset |
| `KG_ROOT_DB` | optional shared DB, only read when root inclusion is requested | unset |
| `KG_INCLUDE_ROOT` | allow root inclusion (`1`) | `0` |
| `KG_LABEL` | source label for loaded nodes | `path.name` |
| `KG_PROJECT` | value returned as `project` by `kg_health` | `cwd.name` |

## Tools

| Tool | Purpose |
|------|---------|
| `kg_health` | orphans, contradictions, clusters, hubs, `file_refs` coverage, `server_version` |
| `kg_validate` | adversarial check of a claim against existing knowledge |
| `kg_query` | `search`, `neighbors`, `path`, `hubs`, `clusters` |
| `kg_reason` | six-step reasoning context |
| `kg_suggest_edges` | dry-run edge suggestions (apply via megamemory MCP) |

CLI equivalent:

```bash
mmkg --self-test
mmkg --db .megamemory/knowledge.db health --text
mmkg --db .megamemory/knowledge.db query neighbors <id> --hops 2
python3 -m megamemory_kg.monthly_health --out docs/kg-health-2026-11.md
```

## Mode rules (optional)

Node can be grouped into **modes** for reporting. The mode is derived from node
IDs at read time — no schema change, no database per mode, no `KG_MODE` env var.
Set `KG_MODE_RULES` to a JSON file (see `mode_rules.example.json`):

```json
{
  "version": 2,
  "default": "general",
  "modes": {
    "code": {"id_prefixes": ["mcp-", "kg-"], "ids": [], "file_ref_hints": ["src/"]},
    "general": {}
  },
  "overrides": {}
}
```

- Mode names are arbitrary; `default` must appear in `modes`.
- Precedence: `overrides` → exact `ids` across modes → `id_prefixes` in
  declaration order (first match) → `default`.
- `file_ref_hints` is optional and only used by external audit scripts.
- Without `KG_MODE_RULES`, behaviour is unchanged: `kg_health` has no `modes`
  field and `kg_query mode` returns an error listing available modes.
- CLI: `python3 -m megamemory_kg.cli --rules mode_rules.example.json health`
  or `... query mode code`.

## Guard

`guard/` contains the generic pre-commit guard and four hooks copied from the
public repository's earlier fork. Config stays per project and is **never**
published; only `guard/guard.example.json` and `guard/pii-blocklist.example.txt`
are shipped.

```bash
sh guard/sync.sh /path/to/project
git -C /path/to/project config core.hooksPath .githooks
node --no-warnings /path/to/project/.githooks/guard.mjs self-test
```

## Verify a checkout

```bash
sh scripts/check_sanitization.sh   # no project-internal tokens in published files
python3 -m unittest discover -s tests -v
```

When re-building the *same* version locally, `uvx` may reuse a cached install.
Use `uv cache clean megamemory-knowledge-graph` or install the wheel into a fresh venv when
verifying a rebuilt artifact.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for upstream attribution.
