#!/bin/bash
#
# Build all distribution artifacts using buildah
#

set -e

VERSION=$(grep '^version = ' pyproject.toml | cut -d'"' -f2)
REGISTRY=${REGISTRY:-docker.io}
IMAGE_NAME=${IMAGE_NAME:-w7panel/umrd}
CTNR_NAME="umrd-build-$$"

echo "Building UMRD v${VERSION}..."

rm -rf build/ dist/

echo ""
echo "=== Building Python packages ==="
python3 -m build --wheel
python3 -m build --sdist

echo ""
echo "=== Building OCI image with buildah ==="

buildah rm "$CTNR_NAME" 2>/dev/null || true

CTR=$(buildah from python:3.11-slim)

buildah config --label maintainer="w7panel" "$CTR"
buildah config --label description="Userspace Memory Reclaimer Daemon" "$CTR"
buildah config --env PYTHONUNBUFFERED=1 "$CTR"

MOUNT=$(buildah mount "$CTR")
mkdir -p "$MOUNT/app"

cp dist/umrd-*.whl "$MOUNT/app/"
buildah run "$CTR" -- pip install --no-cache-dir /app/umrd-*.whl

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
echo "Next steps:"
echo "  PyPI:      twine upload dist/*"
echo "  Registry:   buildah push ${IMAGE_NAME}:${VERSION} docker://${IMAGE_NAME}:${VERSION}"
