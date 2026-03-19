#!/bin/bash
#
# Update version references in documentation
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

VERSION=$(grep '__version__' "$PROJECT_DIR/src/umrd/_version.py" | cut -d'"' -f2)

if [ -z "$VERSION" ]; then
    echo "Error: Could not read version from src/umrd/_version.py"
    exit 1
fi

echo "Updating version to $VERSION"

# Update README.md
sed -i "s/\*\*版本\*\*: .*/\*\*版本\*\*: $VERSION  <!-- 请在 src\/umrd\/_version.py 中修改版本号 -->/" "$PROJECT_DIR/README.md"

# Update AGENTS.md
sed -i "s/| 版本 | .*/| 版本 | $VERSION |/" "$PROJECT_DIR/AGENTS.md"

# Update image tags in AGENTS.md
sed -i "s|zpk.idc.w7.com/w7panel/umrd:[0-9.]*|zpk.idc.w7.com/w7panel/umrd:$VERSION|g" "$PROJECT_DIR/AGENTS.md"

echo "Done!"
