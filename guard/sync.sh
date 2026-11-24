#!/bin/sh
# Salin guard generik ke repo target. Config/project-specific TIDAK disalin.
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET=${1:-.}
[ -d "$TARGET/.git" ] || { echo "sync-guard: bukan git repo: $TARGET" >&2; exit 1; }

mkdir -p "$TARGET/.githooks" "$TARGET/.megamemory"
for f in guard.mjs pre-commit pre-push post-merge post-rewrite; do
  cp "$SRC/guard/$f" "$TARGET/.githooks/$f"
done
[ -f "$TARGET/.megamemory/guard.json" ] || cp "$SRC/guard/guard.example.json" "$TARGET/.megamemory/guard.json"
[ -f "$TARGET/.megamemory/pii-blocklist.txt" ] || cp "$SRC/guard/pii-blocklist.example.txt" "$TARGET/.megamemory/pii-blocklist.txt"

echo "sync-guard: tersalin ke $TARGET/.githooks"
echo "sync-guard: jalankan  git -C '$TARGET' config core.hooksPath .githooks"
echo "sync-guard: verifikasi  node --no-warnings '$TARGET/.githooks/guard.mjs' self-test"
