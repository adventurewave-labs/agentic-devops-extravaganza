"""
Translation proxy: k8sgpt's `customrest` backend  ->  any OpenAI-compatible LLM.

k8sgpt's customrest backend POSTs
    {"model", "prompt", "options": {"message", "temperature", ...}}
and expects
    {"model", "created_at", "response": "<text>"}

Every provider worth pointing at speaks the OpenAI chat-completions shape, so
this proxy translates between the two and injects whatever auth the provider
needs. It is provider-agnostic on purpose: the previous version of this file
hardcoded one vendor's *internal* endpoint and a JWT that only existed inside
one sandbox, which made `--explain` unreproducible for everyone else.

Backends (LLM_BACKEND, default "ollama"):

  ollama    http://127.0.0.1:11434/v1   no credentials, runs locally.
            NOTE: for Ollama you usually don't need this proxy at all —
            k8sgpt speaks to Ollama natively:
                k8sgpt auth add --backend ollama --model llama3.1
            The proxy is here so the same demo script works for every backend.
  openai    https://api.openai.com/v1        OPENAI_API_KEY
  openrouter https://openrouter.ai/api/v1    OPENROUTER_API_KEY
  zai       Z.AI, configured from a JSON config file (see LLM_CONFIG_FILE)
  custom    anything OpenAI-compatible: set LLM_BASE_URL / LLM_API_KEY
  replay    no network at all: answers only from the committed cache

Replay mode is what makes the recorded demo reproducible in CI and on a
laptop with no API key: responses captured from a real model are committed to
captured/llm_cache.json, and the proxy replays them verbatim. Every replayed
response is labelled as such in the health endpoint and the startup banner —
it is a cache, and the README says so.

Usage:
    python scripts/llm_proxy.py [PORT]
"""
import hashlib
import http.server
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

BACKENDS = {
    "ollama": {"base": "http://127.0.0.1:11434/v1", "model": "llama3.1",
               "key_env": None},
    "openai": {"base": "https://api.openai.com/v1", "model": "gpt-4o-mini",
               "key_env": "OPENAI_API_KEY"},
    "openrouter": {"base": "https://openrouter.ai/api/v1",
                   "model": "meta-llama/llama-3.1-8b-instruct",
                   "key_env": "OPENROUTER_API_KEY"},
    "zai": {"base": "https://api.z.ai/api/paas/v4", "model": "glm-4.5",
            "key_env": "ZAI_API_KEY"},
    "custom": {"base": None, "model": None, "key_env": "LLM_API_KEY"},
    "replay": {"base": None, "model": "replay", "key_env": None},
}


def log(msg):
    print(f"[llm-proxy] {msg}", file=sys.stderr, flush=True)


class Config:
    """Resolved backend configuration, from env vars and an optional file."""

    def __init__(self):
        self.backend = os.environ.get("LLM_BACKEND", "ollama").lower()
        if self.backend not in BACKENDS:
            raise SystemExit(
                f"LLM_BACKEND={self.backend!r} is not one of {sorted(BACKENDS)}")
        spec = BACKENDS[self.backend]
        self.base_url = os.environ.get("LLM_BASE_URL") or spec["base"]
        self.model = os.environ.get("LLM_MODEL") or spec["model"]
        self.api_key = ""
        if spec["key_env"]:
            self.api_key = os.environ.get(spec["key_env"], "")
        self.extra_headers = {}
        self._load_config_file()
        if self.backend not in ("replay",) and not self.base_url:
            raise SystemExit(
                "LLM_BASE_URL must be set for LLM_BACKEND=custom")

    def _load_config_file(self):
        """Optional JSON file for providers that need more than a bearer token.

        Looked up at $LLM_CONFIG_FILE, then ./.llm-config. Recognised keys:
        baseUrl, apiKey, model, headers{}. The file is gitignored; nothing in
        this repo ever prints or logs its contents.
        """
        candidates = [os.environ.get("LLM_CONFIG_FILE"),
                      str(paths.ROOT / ".llm-config"),
                      os.path.expanduser("~/.llm-config")]
        for candidate in candidates:
            if not candidate or not os.path.exists(candidate):
                continue
            try:
                with open(candidate) as handle:
                    data = json.load(handle)
            except Exception as exc:
                log(f"ignoring unreadable config {candidate}: {exc}")
                continue
            self.base_url = data.get("baseUrl") or self.base_url
            self.model = data.get("model") or self.model
            self.api_key = data.get("apiKey") or self.api_key
            self.extra_headers.update(data.get("headers") or {})
            log(f"loaded provider config from {candidate}")
            return

    def describe(self):
        return {
            "backend": self.backend,
            "base_url": self.base_url,
            "model": self.model,
            "authenticated": bool(self.api_key) or bool(self.extra_headers),
        }


CONFIG = Config()
CACHE_LOCK = threading.Lock()
STATS = {"cache_hits": 0, "live_calls": 0, "errors": 0}

SSL_CTX = ssl.create_default_context()


def load_cache():
    for candidate in (paths.LLM_CACHE_FILE, paths.CAPTURE_DIR / "zai_cache.json"):
        if candidate.exists():
            try:
                with open(candidate) as handle:
                    data = json.load(handle)
                log(f"loaded {len(data)} cached responses from {candidate}")
                return data
            except Exception as exc:
                log(f"cache {candidate} unreadable: {exc}")
    return {}


CACHE = load_cache()


def save_cache():
    try:
        paths.LLM_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(paths.LLM_CACHE_FILE, "w") as handle:
            json.dump(CACHE, handle, indent=2, sort_keys=True)
    except Exception as exc:
        log(f"cache save failed: {exc}")


def cache_key(model, prompt):
    return hashlib.sha256(f"{model}|{prompt}".encode("utf-8")).hexdigest()


def call_llm(prompt, model=None, temperature=0.7, top_p=0.5):
    """Return the assistant text for a prompt, from cache or from the provider."""
    model = model or CONFIG.model
    key = cache_key(model, prompt)
    # Legacy cache entries were keyed against glm-4.5; honour them so the
    # committed recording stays replayable after a backend switch.
    legacy = cache_key("glm-4.5", prompt)

    with CACHE_LOCK:
        for candidate in (key, legacy):
            if candidate in CACHE:
                STATS["cache_hits"] += 1
                log(f"cache HIT ({candidate[:8]})")
                return CACHE[candidate], "cache"

    if CONFIG.backend == "replay":
        STATS["errors"] += 1
        return ("[replay mode] No cached response for this prompt. Run with a "
                "live LLM backend (LLM_BACKEND=ollama is credential-free) to "
                "generate one."), "replay-miss"

    url = f"{CONFIG.base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if CONFIG.api_key:
        headers["Authorization"] = f"Bearer {CONFIG.api_key}"
    headers.update(CONFIG.extra_headers)
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
    }).encode("utf-8")

    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
    try:
        log(f"cache MISS -> {CONFIG.backend} ({len(prompt)} chars)")
        with urllib.request.urlopen(request, context=SSL_CTX, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("choices", [{}])[0].get("message", {})
                .get("content", ""))
        with CACHE_LOCK:
            CACHE[key] = text
            save_cache()
        STATS["live_calls"] += 1
        return text, "live"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        STATS["errors"] += 1
        log(f"{CONFIG.backend} HTTP {exc.code}: {detail}")
        return f"[llm error {exc.code}] {detail}", "error"
    except Exception as exc:
        STATS["errors"] += 1
        log(f"{CONFIG.backend} call failed: {exc}")
        return (f"[llm error] {exc}\n\nHint: LLM_BACKEND={CONFIG.backend} "
                f"pointing at {CONFIG.base_url}. For a credential-free local "
                f"run: `ollama serve && ollama pull llama3.1`."), "error"


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
        options = payload.get("options") or {}
        prompt = options.get("message") or payload.get("prompt", "")
        text, source = call_llm(
            prompt,
            model=payload.get("model") or CONFIG.model,
            temperature=float(options.get("temperature") or 0.7),
            top_p=float(options.get("top_p") or 0.5),
        )
        self._send({
            "model": payload.get("model") or CONFIG.model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "response": text,
            "x-source": source,
        })

    def do_GET(self):
        self._send({"ok": True, "time": time.time(),
                    "cached_entries": len(CACHE),
                    "stats": STATS, **CONFIG.describe()})

    def log_message(self, *args):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else paths.LLM_PROXY_PORT
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), ProxyHandler)
    server.daemon_threads = True
    log(f"listening on http://127.0.0.1:{port}")
    log(f"backend={CONFIG.backend} model={CONFIG.model} base={CONFIG.base_url}")
    log(f"cache: {len(CACHE)} entries at {paths.LLM_CACHE_FILE}")
    if CONFIG.backend == "replay":
        log("REPLAY MODE: no network calls; cached responses only")
    server.serve_forever()


if __name__ == "__main__":
    main()
