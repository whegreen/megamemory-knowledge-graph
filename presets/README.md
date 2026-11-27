# Preset mode rules

Salin satu file ke repo Anda, lalu ubah sesuai konvensi ID/file repo tersebut.
Nama mode **arbitrary** — engine tidak menanamkan nama apa pun di kode.

| Kategori repo | Preset | Mode | Default |
|---|---|---|---|
| Vault / knowledge management (ber-git) | `vault.mode_rules.json` | `coding`, `km` | `km` |
| Project source code (ber-git) | `project.mode_rules.json` | `coding`, `knowledge` | `coding` |
| Root multi-project (tanpa git, read-through beberapa DB) | `root.mode_rules.json` | `tool`, `index` | `index` |

## Cara pakai

```bash
cp presets/project.mode_rules.json /path/to/repo/.opencode/kg/mode_rules.json
# di .opencode/opencode.json:
#   "KG_MODE_RULES": ".opencode/kg/mode_rules.json"
```

Verifikasi:

```bash
mmkg modes --check --rules .opencode/kg/mode_rules.json
kg_health  # kolom modes muncul bila KG_MODE_RULES terbaca
```

## Precedence klasifikasi

```text
overrides → exact ids → id_prefixes → ref_prefixes → source_labels → default
```

Tiap tingkat memakai **urutan deklarasi `modes`** (first match menang).
`ref_prefixes` mencocokkan awal setiap entri `file_refs`.
`source_labels` mencocokkan label DB (`source`) — berguna untuk root multi-DB.
`file_ref_hints` **tidak** menentukan mode; hanya dipakai `mmkg modes --check`
untuk menandai node default yang semua ref-nya mengarah ke mode lain.

## Knowledge dari nilai database

Mode `knowledge` mencakup dokumen **dan** node yang bersumber dari baris tabel.
Preset memakai placeholder `table_name:` — ganti dengan prefix tabel nyata
misalnya `orders:` atau `invoice:`. Konvensinya: taruh `table:primary_key`
di `file_refs` node, karena writer megamemory tidak memiliki field `mode`.

## Root multi-DB

- `per_source` sudah memisahkan tiap DB. Label unik otomatis diambil dari nama
  direktori induk bila beberapa DB punya basename sama (`knowledge.db`).
- Jangan set `KG_LABEL` di root — itu akan menyamakan semua label dan membuat
  `source_labels` tidak berguna.
- Mode `tool` dipakai untuk node alat lintas proyek; `index` untuk node proyek.

## Threshold

`thresholds` per mode bersifat opsional. Dua kunci yang dikenal:

- `file_refs_coverage_min` — minimum rasio node dengan `file_refs`
- `orphan_ratio_max` — maksimum rasio node tanpa edge

Mode tanpa node tidak pernah memicu threshold. Tanpa `thresholds`, perilaku
sama seperti paket v0.3.0.
