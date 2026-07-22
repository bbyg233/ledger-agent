#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$project_dir/scripts/runtime.sh"
python_bin="$(ledger_python)"
cd "$project_dir"
exec env PYTHONPATH=src "$python_bin" -m pytest -q -m 'not integration'
