#!/usr/bin/env bash
# Octupus one-shot installer: venv + package + browser engine.
# Usage: ./install.sh [--no-browser]
set -euo pipefail
python3 -m venv .venv
.venv/bin/pip install -e '.[all]'
if [[ "${1:-}" != "--no-browser" ]]; then
  .venv/bin/playwright install chromium
fi
echo "OK: try .venv/bin/octupus --url https://example.com --out out.jsonl"
