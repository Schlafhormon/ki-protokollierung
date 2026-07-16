#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Deploying GremienPilot to Kubernetes ==="

# 1. Create namespace
echo "[1/4] Creating namespace..."
kubectl apply -f namespace.yaml

# 2. Apply secrets (if present)
if [ -f "secrets/secret.yaml" ]; then
  echo "[2/4] Applying secrets..."
  kubectl apply -f secrets/secret.yaml
else
  echo "[2/4] No secrets/secret.yaml found — skipping (LLM_API_KEY will default to 'ollama')"
fi

# 3. Apply all resources via Kustomize
echo "[3/4] Applying Kustomize manifests..."
kubectl apply -k .

# 4. Wait for pods
echo "[4/4] Waiting for pods to be ready..."

echo "  Waiting for frontend (should be quick)..."
kubectl wait --for=condition=ready pod -l app=tops-frontend -n tops --timeout=60s

echo "  Waiting for backend (loading ML models — may take 3-5 minutes)..."
kubectl wait --for=condition=ready pod -l app=tops-backend -n tops --timeout=600s

echo ""
echo "=== Deployment complete! ==="
echo ""
echo "Verify:"
echo "  kubectl get pods -n tops"
echo "  kubectl logs -f -n tops -l app=tops-backend"
echo ""
echo "Port-forward to test locally:"
echo "  kubectl port-forward -n tops svc/tops-frontend 3000:80"
echo "  Open http://localhost:3000"
