#!/bin/bash
#
# Build OCI image using buildah
#

set -e

VERSION=${1:-2.0.0}
REGISTRY=${REGISTRY:-docker.io}
IMAGE_NAME=${IMAGE_NAME:-w7panel/umrd}
CTNR_NAME="umrd-build"

echo "Building UMRD OCI image v${VERSION}..."

rm -rf build/ dist/

echo "Building Python package..."
python3 -m build --wheel

echo "Building OCI image..."
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
echo "Build complete!"
echo ""
echo "Image: ${IMAGE_NAME}:${VERSION}"
echo ""
echo "To push to registry:"
echo "  buildah login ${REGISTRY}"
echo "  buildah push ${IMAGE_NAME}:${VERSION} docker://${IMAGE_NAME}:${VERSION}"
echo "  buildah push ${IMAGE_NAME}:latest docker://${IMAGE_NAME}:latest"
