#!/bin/sh
# Gate publikasi: tidak boleh ada token/path internal di file yangtdirilis.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

PATTERN='walesadanto|kisahtumbuh|merge-intools|prod-intools|prod-academic|prod-landing|vercel-|habit_sessions|KAK-RF|agresi|habit-plan|/Users/wales'

if grep -rniE "$PATTERN" --exclude=LICENSE --exclude=NOTICE --exclude=check_sanitization.sh --exclude-dir=.git --exclude-dir=__pycache__ .; then
  echo "check_sanitization: GAGAL — temukan token internal di atas" >&2
  exit 1
fi
echo "check_sanitization: OK"
