# Agentic DevOps Extravaganza
#
# Two backends, one demo:
#   mock  — a Python Kubernetes API server. No Docker, no cluster, 2 seconds.
#   kind  — a real Kubernetes cluster with real Robusta. Needs Docker.
#
# Both are driven by the same scripts and the same remediation commands.

SHELL := /bin/bash
ROOT  := $(shell pwd)
KIND_CLUSTER := agentic-devops
KUBECTL ?= kubectl
NS := payment-prod

.DEFAULT_GOAL := help

.PHONY: help
help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'

# ---------------------------------------------------------------- mock path

.PHONY: demo
demo: ## start the mock stack (mock K8s API + LLM proxy + site)
	./run.sh demo

.PHONY: scan
scan: ## k8sgpt analyze against the mock (no LLM)
	./run.sh scan

.PHONY: explain
explain: ## k8sgpt analyze --explain against the mock (needs an LLM backend)
	./run.sh explain

.PHONY: remediate
remediate: ## apply the real kubectl fixes, then re-scan
	./run.sh remediate

.PHONY: uat
uat: ## run the acceptance suite
	./run.sh uat

.PHONY: stop
stop: ## stop the mock stack
	./run.sh stop

# ---------------------------------------------------------------- kind path

.PHONY: kind-up
kind-up: ## create the kind cluster
	kind create cluster --config kind/kind-config.yaml --wait 120s
	$(KUBECTL) --context kind-$(KIND_CLUSTER) get nodes

.PHONY: kind-broken
kind-broken: ## deploy the genuinely-broken workloads to kind
	$(KUBECTL) --context kind-$(KIND_CLUSTER) apply -f kind/manifests/broken-cluster.yaml
	@echo "waiting for the breakage to actually happen (image pull backoff, OOM)..."
	sleep 60
	$(KUBECTL) --context kind-$(KIND_CLUSTER) -n $(NS) get pods

.PHONY: kind-disk-pressure
kind-disk-pressure: ## mark a real node DiskPressure=True (status subresource)
	$(KUBECTL) --context kind-$(KIND_CLUSTER) patch node $(KIND_CLUSTER)-worker2 \
	  --subresource=status --type=merge -p \
	  '{"status":{"conditions":[{"type":"DiskPressure","status":"True","reason":"KubeletHasNoDiskSpace","message":"kubelet has disk pressure","lastHeartbeatTime":"2026-01-01T00:00:00Z","lastTransitionTime":"2026-01-01T00:00:00Z"}]}}'
	@echo "NOTE: the kubelet owns this condition and will reassert it within ~10s."
	@echo "      Run k8sgpt promptly, or skip this target and accept 6 findings."

.PHONY: kind-scan
kind-scan: ## k8sgpt analyze against the real kind cluster
	k8sgpt analyze --kubecontext kind-$(KIND_CLUSTER) --no-cache -n $(NS)

.PHONY: kind-robusta
kind-robusta: ## install REAL Robusta (helm) with the Prometheus stack
	helm repo add robusta https://robusta-charts.storage.googleapis.com --force-update
	helm repo update
	helm install robusta robusta/robusta \
	  --kube-context kind-$(KIND_CLUSTER) \
	  -f kind/robusta-values.yaml \
	  --set clusterName=$(KIND_CLUSTER) \
	  --wait --timeout 10m
	$(KUBECTL) --context kind-$(KIND_CLUSTER) apply -f kind/prometheus-rule.yaml
	$(KUBECTL) --context kind-$(KIND_CLUSTER) get pods -l app=robusta-runner

.PHONY: kind-fire-alert
kind-fire-alert: ## make Prometheus fire PaymentAPIHighErrorRate for real
	$(KUBECTL) --context kind-$(KIND_CLUSTER) -n $(NS) scale deploy/payment-api --replicas=2
	@echo "alert rule needs 1m of unavailable replicas; watching Robusta..."
	sleep 120
	$(MAKE) kind-findings

.PHONY: kind-findings
kind-findings: ## show what Robusta actually did
	$(KUBECTL) --context kind-$(KIND_CLUSTER) logs \
	  -l app=robusta-runner --tail=200 | grep -iE "finding|alert|playbook" || true

.PHONY: kind-remediate
kind-remediate: ## run the same remediation script against the real cluster
	TARGET=kind KUBE_CONTEXT=kind-$(KIND_CLUSTER) \
	  FIXED_API_IMAGE=nginx:1.27-alpine bash ./scripts/remediate.sh
	sleep 45
	$(MAKE) kind-scan

.PHONY: kind-down
kind-down: ## delete the kind cluster
	kind delete cluster --name $(KIND_CLUSTER)
