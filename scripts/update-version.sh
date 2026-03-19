#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

VERSION=$(grep '^version = ' "$PROJECT_DIR/pyproject.toml" | sed 's/version = "//;s/"//')

echo "Updating version to $VERSION"

sed -i "s/\*\*版本\*\*: .*/\*\*版本\*\*: $VERSION  <!请在 pyproject.toml 中修改版本号 -->/" "$PROJECT_DIR/README.md"
sed -i "s/| 版本 | .*/| 版本 | $VERSION |/" "$PROJECT_DIR/AGENTS.md"
sed -i "s|zpk.idc.w7.com/w7panel/umrd:[0-9.]*|zpk.idc.w7.com/w7panel/umrd:$VERSION|g" "$PROJECT_DIR/AGENTS.md"

echo "Done!"
