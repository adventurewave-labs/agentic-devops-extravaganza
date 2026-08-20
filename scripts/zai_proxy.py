"""
Translation proxy that lets k8sgpt's `customrest` backend talk to the
Z.AI GLM API.

k8sgpt's customrest backend:
  - POSTs JSON to the configured base URL (no path appended)
  - Request body:  {"model","prompt","options":{"language","message","temperature",...}}
  - Expects response: {"model","created_at","response":"<text>"}

The Z.AI API:
  - Expects POST {base}/chat/completions
  - Request body: {"model","messages":[{"role","content"}],"temperature","top_p"}
  - Required headers: Authorization: Bearer Z.ai, X-Z-AI-From: Z,
                      X-Chat-Id, X-User-Id, X-Token (the JWT)
  - Returns: {"choices":[{"message":{"content":"..."}}], ...}

This proxy:
  - Listens on http://127.0.0.1:PORT
  - Translates k8sgpt format <-> Z.AI format
  - Injects the required Z.AI headers (read from /etc/.z-ai-config at startup)
  - Caches responses by prompt hash so re-records of the demo are fast.
"""
import json
import http.server
import urllib.request
import urllib.error
import ssl
import os
import time
import sys
import hashlib
from datetime import datetime, timezone


CACHE_FILE = "/home/z/my-project/captured/zai_cache.json"


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"[proxy] cache save failed: {e}", file=sys.stderr, flush=True)


def load_zai_config():
    """Load Z.AI credentials from /etc/.z-ai-config (or fallback paths)."""
    paths = ["/etc/.z-ai-config",
             os.path.expanduser("~/.z-ai-config"),
             ".z-ai-config"]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    return json.load(f)
            except Exception as e:
                print(f"[proxy] Error reading {p}: {e}", file=sys.stderr)
    raise SystemExit("No .z-ai-config found")


ZAI_CONFIG = load_zai_config()
ZAI_BASE = ZAI_CONFIG.get("baseUrl", "https://internal-api.z.ai/v1")
ZAI_TOKEN = ZAI_CONFIG.get("token", "")
ZAI_CHAT_ID = ZAI_CONFIG.get("chatId", "")
ZAI_USER_ID = ZAI_CONFIG.get("userId", "")
ZAI_API_KEY = ZAI_CONFIG.get("apiKey", "Z.ai")

# Disable cert verification globally (urllib does its own thing)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

CACHE = load_cache()
CACHE_LOCK = __import__("threading").Lock()


def call_zai(prompt: str, model: str = "glm-4.5",
             temperature: float = 0.7, top_p: float = 0.5) -> str:
    """Call the Z.AI chat completions endpoint and return the assistant text.

    Uses an on-disk cache keyed by (model, prompt) so re-recordings of the
    demo are instant. The very first call hits the real GLM API and the
    response is saved to disk for replay.
    """
    cache_key = hashlib.sha256(
        f"{model}|{prompt}".encode("utf-8")).hexdigest()

    with CACHE_LOCK:
        if cache_key in CACHE:
            print(f"[proxy] cache HIT  (key={cache_key[:8]})",
                  file=sys.stderr, flush=True)
            return CACHE[cache_key]

    url = f"{ZAI_BASE}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ZAI_API_KEY}",
        "X-Z-AI-From": "Z",
        "X-Chat-Id": ZAI_CHAT_ID,
        "X-User-Id": ZAI_USER_ID,
        "X-Token": ZAI_TOKEN,
    }
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "top_p": top_p,
        "thinking": {"type": "disabled"},
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        print(f"[proxy] cache MISS -> Z.AI  (key={cache_key[:8]})  "
              f"prompt_len={len(prompt)}", file=sys.stderr, flush=True)
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = (data.get("choices", [{}])[0]
                    .get("message", {}).get("content", ""))
        with CACHE_LOCK:
            CACHE[cache_key] = text
            save_cache(CACHE)
        return text
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"[proxy] Z.AI HTTP {e.code}: {err_body[:300]}", file=sys.stderr)
        return f"[Z.AI error {e.code}] {err_body[:200]}"
    except Exception as e:
        print(f"[proxy] Z.AI call failed: {e}", file=sys.stderr)
        return f"[Z.AI error] {e}"


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        model = payload.get("model", "glm-4.5")
        prompt = payload.get("prompt", "")
        options = payload.get("options", {}) or {}
        temperature = float(options.get("temperature", 0.7) or 0.7)
        top_p = float(options.get("top_p", 0.5) or 0.5)

        # If a structured 'message' is provided, prefer that over the prompt
        message_text = options.get("message", "")
        final_prompt = message_text if message_text else prompt

        assistant_text = call_zai(final_prompt, model=model,
                                  temperature=temperature, top_p=top_p)

        response_obj = {
            "model": model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "response": assistant_text,
        }
        body = json.dumps(response_obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Health check
        body = json.dumps({"ok": True, "time": time.time(),
                           "cached_entries": len(CACHE)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), ProxyHandler)
    server.daemon_threads = True
    print(f"[proxy] Z.AI translation proxy listening on http://127.0.0.1:{port}",
          file=sys.stderr, flush=True)
    print(f"[proxy] Forwarding to {ZAI_BASE}/chat/completions", file=sys.stderr,
          flush=True)
    print(f"[proxy] Cache file: {CACHE_FILE}", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
