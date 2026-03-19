#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VERSION=$(grep '^version = ' "$PROJECT_DIR/pyproject.toml" | sed 's/version = "//;s/"//')
REGISTRY=${REGISTRY:-zpk.idc.w7.com}
IMAGE_NAME=${IMAGE_NAME:-zpk.idc.w7.com/w7panel/umrd}
CTNR_NAME="umrd-build-$$"

echo "Building UMRD v${VERSION}..."

cd "$PROJECT_DIR"
./scripts/update-version.sh

cat > src/umrd/_version.py << EOF
__version__ = "$VERSION"
EOF

rm -rf build/ dist/

echo ""
echo "=== Building Python packages ==="
python3 -m build --wheel
python3 -m build --sdist

WHL_FILE=$(ls dist/umrd-*.whl)

echo ""
echo "=== Building OCI image with buildah ==="

buildah rm "$CTNR_NAME" 2>/dev/null || true

CTR=$(buildah from python:3.11-slim)

buildah config --label maintainer="w7panel" "$CTR"
buildah config --label description="Userspace Memory Reclaimer Daemon" "$CTR"
buildah config --env PYTHONUNBUFFERED=1 "$CTR"

buildah run "$CTR" -- bash -c 'apt-get update && apt-get install -y --no-install-recommends kmod && rm -rf /var/lib/apt/lists/*'

MOUNT=$(buildah mount "$CTR")
mkdir -p "$MOUNT/app"

cp "$WHL_FILE" "$MOUNT/app/"
buildah run "$CTR" -- pip install --no-cache-dir "/app/$(basename "$WHL_FILE")"

buildah commit "$CTR" "${IMAGE_NAME}:${VERSION}"
buildah commit "$CTR" "${IMAGE_NAME}:latest"

buildah rm "$CTNR_NAME" 2>/dev/null || true

echo ""
echo "=== Build complete! ==="
echo ""
echo "Python packages:"
ls -lh dist/

echo ""
echo "OCI images:"
buildah images "${IMAGE_NAME}"

echo ""
echo "To push to registry:"
echo "  buildah push ${IMAGE_NAME}:${VERSION} docker://${IMAGE_NAME}:${VERSION}"
echo "  buildah push ${IMAGE_NAME}:latest docker://${IMAGE_NAME}:latest"
