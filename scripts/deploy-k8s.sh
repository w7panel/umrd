#!/bin/bash
#
# Deploy UMRD to Kubernetes
#

set -e

NAMESPACE=${NAMESPACE:-kube-system}
IMAGE=${IMAGE:-zpk.idc.w7.com/w7panel/umrd:latest}

echo "Deploying UMRD to Kubernetes..."

kubectl apply -f k8s/daemonset.yaml

echo ""
echo "UMRD deployed to namespace: ${NAMESPACE}"
echo ""
echo "Check status:"
echo "  kubectl get daemonset -n ${NAMESPACE}"
echo "  kubectl get pods -n ${NAMESPACE}"
echo ""
echo "View logs:"
echo "  kubectl logs -n ${NAMESPACE} -l app=umrd"
