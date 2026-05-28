#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Building DeepRepro UI..."
cd "$UI_DIR/frontend"

if [ ! -d "node_modules" ]; then
  npm install
fi

npm run build
echo "DeepRepro UI build complete: $UI_DIR/frontend/dist"
