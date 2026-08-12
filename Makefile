NS ?= odoo
CONTEXT ?= minikube
KUBECTL := kubectl --context $(CONTEXT)

.DEFAULT_GOAL := help
.PHONY: help up down open logs status restart shell context proxy tenants

help: ## List the available commands
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "} {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

context: ## Show which cluster these commands target
	@echo "Targeting context: $(CONTEXT)   (namespace: $(NS))"

up: ## Bring up the whole stack
	CONTEXT=$(CONTEXT) ./scripts/up.sh

down: ## Tear down the stack (keeps the data)
	CONTEXT=$(CONTEXT) ./scripts/down.sh

open: ## Open Odoo at http://localhost:8069 (admin / admin)
	@echo "Odoo -> http://localhost:8069   (admin / admin)"
	$(KUBECTL) -n $(NS) port-forward svc/odoo 8069:8069

logs: ## Tail Odoo logs
	$(KUBECTL) -n $(NS) logs -f deploy/odoo -c odoo

status: ## Show whether everything is running
	$(KUBECTL) -n $(NS) get pods

restart: ## Restart Odoo
	$(KUBECTL) -n $(NS) rollout restart deploy/odoo
	$(KUBECTL) -n $(NS) rollout status deploy/odoo

shell: ## Open a shell inside the Odoo pod
	$(KUBECTL) -n $(NS) exec -it deploy/odoo -c odoo -- bash

PORT ?= 8090

proxy: ## Expose the ingress for *.localhost hosts (keep running). Use PORT=80 for clean URLs (needs sudo)
	@echo "Ingress -> http://<host>:$(PORT)   (main: http://19.localhost:$(PORT))"
	@if [ "$(PORT)" -lt 1024 ]; then \
		echo "port $(PORT) < 1024 -> using sudo"; \
		sudo env "KUBECONFIG=$$HOME/.kube/config" $(KUBECTL) -n ingress-nginx port-forward --address 127.0.0.1 svc/ingress-nginx-controller $(PORT):80; \
	else \
		$(KUBECTL) -n ingress-nginx port-forward svc/ingress-nginx-controller $(PORT):80; \
	fi

tenants: ## List provisioned tenants (helm releases)
	helm list -A --kube-context $(CONTEXT)
