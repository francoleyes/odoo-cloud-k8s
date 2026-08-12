#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CTX="${CONTEXT:-minikube}"
KUBECTL="kubectl --context $CTX"
BASE_DOMAIN="${BASE_DOMAIN:-19.localhost}"

echo "[1/6] minikube + ingress"
minikube status >/dev/null 2>&1 || minikube start --cpus=4 --memory=5120 --driver=docker
minikube addons enable ingress >/dev/null
$KUBECTL -n ingress-nginx wait --for=condition=available deployment/ingress-nginx-controller --timeout=180s 2>/dev/null || true

echo "[2/6] CloudNativePG operator"
if ! $KUBECTL get ns cnpg-system >/dev/null 2>&1; then
  CNPG_TAG=$(gh api repos/cloudnative-pg/cloudnative-pg/releases/latest --jq .tag_name 2>/dev/null || echo "v1.30.0")
  MINOR=$(echo "$CNPG_TAG" | sed -E 's/^v([0-9]+\.[0-9]+)\..*/\1/')
  VER=$(echo "$CNPG_TAG" | sed 's/^v//')
  $KUBECTL apply --server-side -f "https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-${MINOR}/releases/cnpg-${VER}.yaml"
fi
$KUBECTL -n cnpg-system rollout status deploy/cnpg-controller-manager --timeout=180s

echo "[3/6] build image"
minikube image build --file "$ROOT/image/Dockerfile" -t odoo-cloud:19 "$ROOT"

echo "[4/6] deploy the main from the shared chart (isMain=true)"
helm --kube-context "$CTX" upgrade --install main "$ROOT/infra/charts/tenant" \
  --namespace odoo --create-namespace \
  --set isMain=true \
  --set name=odoo \
  --set baseDomain="$BASE_DOMAIN" \
  --set minio.endpoint=http://minio:9000 \
  --set-string minio.user=odoo-minio \
  --set-string minio.password=minio_secret_change_me \
  --set postgres.storage=2Gi

echo "[5/6] wait postgres + minio"
$KUBECTL -n odoo wait --for=condition=Ready cluster/postgres --timeout=300s
$KUBECTL -n odoo rollout status deployment/minio --timeout=180s

echo "[6/6] wait odoo"
$KUBECTL -n odoo rollout status deployment/odoo --timeout=300s
$KUBECTL -n odoo get pods,svc,cluster

cat <<'EOF'

Odoo ready.

  make proxy                              # expose the ingress on :8090 (keep running)
  open http://19.localhost:8090           # main   (admin / admin)
  open http://<tenant>.19.localhost:8090  # e.g. http://acme.19.localhost:8090
EOF
