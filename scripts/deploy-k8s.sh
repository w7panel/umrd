#!/bin/bash
#
# Deploy UMRD to Kubernetes
#

set -e

NAMESPACE=${NAMESPACE:-umrd-system}
IMAGE=${IMAGE:-w7panel/umrd:latest}

echo "Deploying UMRD to Kubernetes..."

# Build and load image (for kind/minikube)
if [[ "$1" == "--kind" ]]; then
    echo "Building image for kind..."
    ./scripts/build-docker.sh
    kind load docker-image ${IMAGE}
fi

# Apply manifests
echo "Creating namespace..."
kubectl apply -f k8s/daemonset.yaml

# Patch image if custom
if [[ "$IMAGE" != "w7panel/umrd:latest" ]]; then
    echo "Patching image to ${IMAGE}..."
    kubectl set image daemonset/umrd umrd=${IMAGE} -n ${NAMESPACE}
fi

echo ""
echo "UMRD deployed to namespace: ${NAMESPACE}"
echo ""
echo "Check status:"
echo "  kubectl get daemonset -n ${NAMESPACE}"
echo "  kubectl get pods -n ${NAMESPACE}"
echo ""
echo "View logs:"
echo "  kubectl logs -n ${NAMESPACE} -l app=umrd"
