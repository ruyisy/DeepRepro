#!/bin/zsh
set -eu
cd "$(dirname "$0")"
export PATH="$PWD/.venv/bin:$PATH"
if ! command -v node >/dev/null; then
  for node_bin in "$HOME"/.local/share/node-*/bin(N); do
    export PATH="$node_bin:$PATH"
  done
fi
exec .venv/bin/python deeprepro.py --local
