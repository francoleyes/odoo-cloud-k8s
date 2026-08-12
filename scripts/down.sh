#!/usr/bin/env bash
set -euo pipefail

CTX="${CONTEXT:-minikube}"
KUBECTL="kubectl --context $CTX"

if ! kubectl config get-contexts -o name | grep -qx "$CTX"; then
  echo "ERROR: context '$CTX' does not exist. Refusing to run." >&2
  exit 1
fi

helm --kube-context "$CTX" uninstall main -n odoo 2>/dev/null || true

$KUBECTL delete clusterrole,clusterrolebinding odoo-provisioner --ignore-not-found

if [[ "${1:-}" == "--purge" ]]; then
  $KUBECTL delete namespace odoo --ignore-not-found
else
  echo "Main uninstalled. PVCs kept (postgres, minio). Use --purge to delete the namespace + data too."
fi
