# Deploying the Agentic DevOps landing page to Vercel

The repo root contains a single-file static landing page (`index.html`) plus
the `gifs/` and `outputs/` directories it references. The `vercel.json`
at the repo root tells Vercel to serve the repo root as a static site.

## Option A — Vercel CLI (one-shot)

```bash
npm i -g vercel
cd agentic-devops-extravaganza
vercel              # links the project, deploys to a preview URL
vercel --prod       # deploys to production
```

## Option B — GitHub + Vercel auto-deploy (recommended)

1. Push the repo to GitHub (already done — `creandotumatrix-labs/agentic-devops-extravaganza`).
2. Go to https://vercel.com/new
3. Import the `agentic-devops-extravaganza` repo.
4. Vercel auto-detects the `vercel.json` and serves the repo root.
5. Click **Deploy**. The site is live in ~30 seconds.

## Option C — One-click "Deploy to Vercel" button

The landing page has a button that links to:

```
https://vercel.com/new/clone?repository-url=https://github.com/creandotumatrix-labs/agentic-devops-extravaganza
```

This clones the repo into your GitHub and deploys it to your Vercel account in one click.

## What gets served

| Path | Asset | Size |
|---|---|---|
| `/` | `index.html` | ~80 KB |
| `/gifs/k8sgpt_scan.gif` | the 25-second cluster triage demo | ~1.0 MB |
| `/gifs/k8sgpt_explain.gif` | the 25-second AI diagnosis demo | ~0.6 MB |
| `/gifs/robusta.gif` | the 25-second Robusta autonomous SRE demo | ~0.9 MB |
| `/outputs/*.txt` | verbatim captured outputs from the binaries | ~60 KB total |

## Custom domain

In the Vercel dashboard → Settings → Domains → add your domain. The site
has no server-side state so it scales to global edge with zero config.

## Docker alternative

If you prefer Docker over Vercel:

```bash
docker compose up site     # serves the same static site on http://localhost:8080
```

For the full demo stack (mock K8s API + Z.AI proxy + site):

```bash
echo '{"baseUrl": "...", "apiKey": "Z.ai", "chatId": "...", "token": "...", "userId": "..."}' > .z-ai-config
docker compose up demo
```
