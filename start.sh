#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
# uv-created environments may not include pip. Bootstrap it when installation is needed.
if ! .venv/bin/python -c 'from importlib.metadata import version; from pathlib import Path; pins = (line.split("==") for line in Path("requirements.txt").read_text().splitlines() if line and not line.startswith("#")); raise SystemExit(any(version(name) != wanted for name, wanted in pins))' >/dev/null 2>&1; then
  if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    .venv/bin/python -m ensurepip --upgrade
  fi
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python server.py "$@"
