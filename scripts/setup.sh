#!/usr/bin/env bash
# Thin wrapper -- bcrypt hashing and interactive prompts are awkward in
# pure bash, so the real wizard is scripts/setup.py. This just checks
# for Python 3 and hands off to it, run from the repo root either way.
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but wasn't found on PATH. Install Python 3.10+ and re-run." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$REPO_ROOT/scripts/setup.py"
