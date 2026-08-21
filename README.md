# Agentic DevOps Extravaganza

[![CI](https://github.com/adventurewave-labs/agentic-devops-extravaganza/actions/workflows/ci.yml/badge.svg)](https://github.com/adventurewave-labs/agentic-devops-extravaganza/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-326ce5.svg)](LICENSE)
[![K8sGPT: v0.4.36](https://img.shields.io/badge/K8sGPT-v0.4.36-00d9ff.svg)](https://github.com/k8sgpt-ai/k8sgpt)

**AI-assisted Kubernetes triage you can run yourself in one command — then watch the findings actually go away.**

Two ways to run it, both real:

| Path | What it is | Needs | Time |
|---|---|---|---|
| **mock** | A Python Kubernetes API server serving a deliberately broken `payment-prod`. Real `k8sgpt` and real `kubectl` talk to it over the real Kubernetes REST API, **including writes**. | Python 3.10+ | ~2 seconds |
| **kind** | A real Kubernetes cluster with genuinely broken workloads and **real [Robusta](https://github.com/robusta-dev/robusta)** installed by Helm, receiving a real Prometheus alert. | Docker, kind, helm | ~10 minutes |

![Real kubectl remediation: 8 findings, then 0](gifs/remediate.gif)

## What is and isn't real

This section is first on purpose. A demo that oversells itself is worth less than one that tells you exactly where the edges are.

**Real:**

- **k8sgpt is the real binary.** v0.4.36, 14 Go analyzers, no LLM involved in the scan. It finds **8 problems** in the broken cluster.
- **The Kubernetes API is real protocol.** Discovery, TLS, field selectors, label selectors, all five verbs. `kubectl get`, `set image`, `patch`, `cordon`, `drain`, `create`, `apply` all work against it. `kubectl` cannot tell it isn't a kube-apiserver.
- **Remediation genuinely changes state.** `scripts/remediate.sh` issues real kubectl writes. A reconciler models the control plane's response. Re-running k8sgpt then reports **0 findings** because the state it reads is different. Comment out one fix and that finding stays. This is not a scripted before/after.
- **Robusta is real in the `kind/` path.** The actual `robusta-dev/robusta` Helm chart, the actual kube-prometheus-stack, an actual `PrometheusRule` that fires an actual alert.
- **The GIFs are recorded from live terminal sessions** in a pty by [`scripts/record_demos.py`](scripts/record_demos.py). If a command fails, the failure is in the GIF.

**Not real, and labelled as such everywhere:**

- **The `payment-prod` cluster in the mock path is a simulation.** The file is called `mock_k8s_server.py`. No containers are scheduled; no kubelet exists. Its reconciler models six control-plane behaviours, [enumerated below](#what-the-reconciler-simulates) and served at `/_demo/rules`.
- **`scripts/alert_triage_agent.py` is not Robusta.** It is a ~250-line reference implementation of Robusta's alert → context → LLM → card loop, written to be readable in one sitting. For Robusta itself, use the kind path.
- **Slack posting is opt-in and off by default.** Set `SLACK_WEBHOOK_URL` and pass `--post-slack`. With neither, the card renders locally and the script says so.
- **The LLM is whatever you point it at.** There is no bundled model and no bundled key.

> **On the history of this repo.** Earlier versions claimed "real Kubernetes API", "no mocks", and a "6/6 remediated" demo that ran no commands; the alert script printed "Jira ticket PAY-1247 created" with no Jira client anywhere in the tree; the headline GIF was hand-authored rather than recorded; and every script hardcoded absolute paths from one developer machine, so the published 15/15 test suite could not run on any other machine. All of that is fixed, and this section exists so it stays fixed.

## Quick start

### Mock path — no Docker, no cluster

```bash
git clone https://github.com/adventurewave-labs/agentic-devops-extravaganza.git
cd agentic-devops-extravaganza

# k8sgpt (Linux; brew install k8sgpt on macOS)
curl -sL https://github.com/k8sgpt-ai/k8sgpt/releases/download/v0.4.36/k8sgpt_Linux_x86_64.tar.gz \
  | sudo tar xz -C /usr/local/bin k8sgpt

./run.sh demo        # mock K8s API :8443 + LLM proxy :8081 + site :8080
./run.sh scan        # 8 findings, no LLM
./run.sh remediate   # real kubectl fixes -> re-scan -> 0 findings
./run.sh reset       # put the cluster back
```

TLS certs are generated on first boot. There is nothing else to configure.

### Docker

```bash
docker compose run --rm scan        # k8sgpt analyze, no LLM
docker compose run --rm remediate   # the 8 -> 0 run
docker compose run --rm uat         # the acceptance suite
docker compose up demo              # full stack, stays running
docker compose up site              # just the showcase page
```

The image installs the real k8sgpt v0.4.36 and kubectl v1.30.0 binaries.

### kind path — a real cluster with real Robusta

```bash
make kind-up          # 3-node kind cluster
make kind-broken      # deploy genuinely-broken workloads, wait for them to break
make kind-scan        # k8sgpt against a real cluster
make kind-robusta     # helm install robusta + kube-prometheus-stack
make kind-fire-alert  # make Prometheus fire PaymentAPIHighErrorRate for real
make kind-findings    # what Robusta actually did
make kind-remediate   # the same remediate.sh, against the real cluster
make kind-down
```

`make help` lists every target.

## The eight findings

```
$ k8sgpt analyze --kubecontext mock-context --no-cache -n payment-prod

0: Node worker-3()
- Error: worker-3 has condition of type DiskPressure, reason KubeletHasNoDiskSpace
1: PersistentVolumeClaim payment-prod/payment-data-pvc()
- Error: storageclass.storage.k8s.io "standard" not found
2: Service payment-prod/payment-api-svc()
- Error: Service has no endpoints, expected label app=payment-api-frontend
3: Ingress payment-prod/payment-ingress()
- Error: Ingress uses the ingress class nginx which does not exist.
- Error: Ingress uses the service payment-prod/payment-frontend which does not exist.
4: Pod payment-prod/payment-api-7c4f5b-x9qkl(Deployment/payment-api)
- Error: the last termination reason is Error container=api
5: Pod payment-prod/payment-worker-6d8b2c-p3mnr(Deployment/payment-worker)
- Error: the last termination reason is OOMKilled container=worker
6: Deployment payment-prod/payment-api()
- Error: Deployment has 1 replicas but 0 are available with status running
7: Deployment payment-prod/payment-worker()
- Error: Deployment has 1 replicas but 0 are available with status running
```

Verbatim from `outputs/k8sgpt_analyze.txt`, regenerated by CI on every push.

## Remediation is a real write path

`./run.sh remediate` runs [`scripts/remediate.sh`](scripts/remediate.sh) — seven real kubectl commands:

```bash
kubectl set image deployment/payment-api api=registry.io/payments/api:1.4.3
kubectl patch deployment payment-worker --type=strategic -p '{...memory: 512Mi...}'
kubectl patch service payment-api-svc --type=merge -p '{"spec":{"selector":{"app":"payment-api"}}}'
kubectl cordon worker-3 && kubectl drain worker-3 --ignore-daemonsets --force
kubectl apply -f manifests/fix-ingressclass.yaml
kubectl create service clusterip payment-frontend --tcp=80:80
kubectl apply -f manifests/fix-storageclass.yaml
```

```
=== BEFORE ===   k8sgpt findings: 8
=== AFTER  ===   k8sgpt findings: 0
```

The same script runs against the kind cluster with `make kind-remediate`.

### What the reconciler simulates

The mock has no kubelet, so something has to decide what a write *means*. That is `cluster_state.py`'s reconciler, and here is every rule it applies — also served live at `GET /_demo/rules`:

| Finding | Trigger | Simulated effect |
|---|---|---|
| payment-api CrashLoopBackOff | image != the broken tag | Deployment 1/1, Pod Running/Ready |
| payment-worker OOMKilled | memory limit ≥ 256Mi | Deployment 1/1, Pod Running/Ready |
| worker-3 DiskPressure | node cordoned, or condition patched False | DiskPressure clears (models drain + disk reclaim) |
| Ingress dangling backend | Service `payment-frontend` created | backend resolves |
| Ingress missing class | IngressClass `nginx` created | class resolves |
| PVC Pending | StorageClass `standard` created | PVC binds |
| Service no endpoints | selector corrected to `app=payment-api` | Endpoints populate once the Pod is Ready |

The rules derive status purely from current spec, so they are order-independent and idempotent: fix things in any order, or only some of them, and the finding count follows.

In the kind path there is no reconciler — a real kubelet and real controllers do the work.

## LLM backends

`--explain` sends each finding to a model. The proxy ([`scripts/llm_proxy.py`](scripts/llm_proxy.py)) translates k8sgpt's `customrest` shape to any OpenAI-compatible API.

| `LLM_BACKEND` | Endpoint | Credentials |
|---|---|---|
| `ollama` *(default)* | `http://127.0.0.1:11434/v1` | none — runs on your machine |
| `openai` | `api.openai.com` | `OPENAI_API_KEY` |
| `openrouter` | `openrouter.ai` | `OPENROUTER_API_KEY` |
| `zai` | `api.z.ai` | `ZAI_API_KEY` |
| `custom` | `$LLM_BASE_URL` | `$LLM_API_KEY` |
| `replay` | none | none — replays `captured/llm_cache.json` |

```bash
ollama serve && ollama pull llama3.1     # credential-free path
./run.sh explain
```

For Ollama you don't strictly need the proxy — k8sgpt speaks to it natively via
`k8sgpt auth add --backend ollama --model llama3.1`. The proxy exists so one
command works for every backend.

**About `replay`:** `captured/llm_cache.json` holds 7 responses captured from GLM-4.5 during the original recording. Prompts that aren't in it return an explicit "no cached response" message rather than anything invented. Two of the current eight findings post-date that capture and will report a miss until you run a live backend once.

## Alert triage

```bash
./run.sh triage                 # render the card locally
SLACK_WEBHOOK_URL=... ./run.sh triage --post-slack
```

Reads [`alerts/payment-api-high-error-rate.json`](alerts/payment-api-high-error-rate.json) (a real Alertmanager webhook payload), queries the cluster for the objects it names, asks the model for an analysis grounded in what came back, and renders a Slack card. Every cluster read is live; nothing is pre-baked.

For the same loop performed by real Robusta with a real Prometheus alert, use `make kind-robusta && make kind-fire-alert`.

## Acceptance suite

```bash
./run.sh uat
```

17 checks across five groups: the mock serves a genuinely broken cluster (A), real binaries read *and write* to it (B), remediation actually drives findings to zero (C), the LLM proxy answers k8sgpt's request shape (D), and the repo has no hardcoded developer paths, no committed credentials, and no dangling file references (E).

Missing prerequisites report **skip**, never pass. The suite runs in CI on every push, so the badge at the top of this file reflects a run you can click into and read.

## Repo layout

```
├── run.sh                       # mock-path entrypoint
├── Makefile                     # kind-path entrypoint (make help)
├── index.html                   # showcase page
├── scripts/
│   ├── paths.py                 # every path in the repo derives from here
│   ├── cluster_fixtures.py      # the broken-cluster data (inert)
│   ├── cluster_state.py         # mutable state + patch semantics + reconciler
│   ├── mock_k8s_server.py       # the API server (TLS, discovery, all verbs)
│   ├── llm_proxy.py             # customrest -> any OpenAI-compatible backend
│   ├── alert_triage_agent.py    # reference impl of Robusta's loop
│   ├── remediate.sh             # the seven real kubectl fixes
│   ├── record_demos.py          # live pty recording -> .cast
│   ├── cast_to_gif.py           # .cast -> .gif, pure Python
│   ├── serve_site.py            # static server for index.html
│   └── run_uat.py               # the acceptance suite
├── kind/                        # real cluster: config, manifests, robusta values
├── manifests/                   # the fix manifests remediate.sh applies
├── alerts/                      # Alertmanager webhook fixtures
├── gifs/  recordings/           # live recordings and their rendered GIFs
├── captured/  outputs/          # command output, regenerated by CI
└── mock-k8s/kubeconfig.yaml     # certs are generated on first boot
```

## Rebuilding the demos

```bash
./run.sh record          # re-record all four GIFs from live sessions
```

No external binaries needed — `scripts/cast_to_gif.py` renders with Pillow. If `agg` is on PATH it is used instead.

## Known limitations

- **`kubectl apply -f` against the mock needs `--validate=false`.** kubectl validates manifests against the apiserver's OpenAPI schema bundle; the mock serves the REST API but not the full schema bundle. `remediate.sh` passes the flag automatically for the mock and omits it for kind. Imperative commands (`set image`, `patch`, `cordon`, `create service`) need no flag.
- **Node conditions in the kind path are kubelet-owned.** `make kind-disk-pressure` patches `DiskPressure=True` via the status subresource, and the real kubelet reasserts it within ~10s. Run k8sgpt promptly or accept one fewer finding on that path.
- **No pod logs in the mock.** `kubectl logs` is not implemented, so analyzers and enrichers that read logs have nothing to read. Real Robusta's `logs_enricher` works only on the kind path.

## License

MIT — see [LICENSE](LICENSE).
