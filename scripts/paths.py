"""
Single source of truth for every path in this project.

Nothing in this repo may hardcode an absolute path. Everything derives from
the repo root, which is derived from this file's own location, and every
value is overridable by an environment variable so the same code runs from a
git clone, from /app inside the container, and from CI.
"""
import os
import shutil
from pathlib import Path

# scripts/paths.py -> scripts/ -> <repo root>
ROOT = Path(os.environ.get("AOPS_ROOT", Path(__file__).resolve().parents[1]))

SCRIPTS_DIR = ROOT / "scripts"
CAPTURE_DIR = Path(os.environ.get("AOPS_CAPTURE_DIR", ROOT / "captured"))
RECORDING_DIR = Path(os.environ.get("AOPS_RECORDING_DIR", ROOT / "recordings"))
GIF_DIR = Path(os.environ.get("AOPS_GIF_DIR", ROOT / "gifs"))
OUTPUT_DIR = Path(os.environ.get("AOPS_OUTPUT_DIR", ROOT / "outputs"))
MOCK_K8S_DIR = Path(os.environ.get("AOPS_MOCK_K8S_DIR", ROOT / "mock-k8s"))
KIND_DIR = ROOT / "kind"

KUBECONFIG = Path(os.environ.get("KUBECONFIG", MOCK_K8S_DIR / "kubeconfig.yaml"))
CERT_FILE = Path(os.environ.get("AOPS_MOCK_CERT", MOCK_K8S_DIR / "cert.pem"))
KEY_FILE = Path(os.environ.get("AOPS_MOCK_KEY", MOCK_K8S_DIR / "key.pem"))

LLM_CACHE_FILE = Path(os.environ.get("AOPS_LLM_CACHE", CAPTURE_DIR / "llm_cache.json"))

MOCK_K8S_PORT = int(os.environ.get("AOPS_MOCK_K8S_PORT", "8443"))
LLM_PROXY_PORT = int(os.environ.get("AOPS_LLM_PROXY_PORT", "8081"))
SITE_PORT = int(os.environ.get("AOPS_SITE_PORT", "8080"))

MOCK_K8S_URL = f"https://127.0.0.1:{MOCK_K8S_PORT}"
LLM_PROXY_URL = f"http://127.0.0.1:{LLM_PROXY_PORT}"


def _find_binary(name: str, env_var: str) -> str:
    """Resolve a CLI binary: $ENV_VAR, then ./bin/, then $PATH."""
    override = os.environ.get(env_var)
    if override:
        return override
    local = ROOT / "bin" / name
    if local.exists():
        return str(local)
    found = shutil.which(name)
    return found or name  # let the caller fail with a clear "not found"


def kubectl_bin() -> str:
    return _find_binary("kubectl", "KUBECTL")


def k8sgpt_bin() -> str:
    return _find_binary("k8sgpt", "K8SGPT")


def ensure_dirs() -> None:
    for d in (CAPTURE_DIR, RECORDING_DIR, GIF_DIR, OUTPUT_DIR, MOCK_K8S_DIR):
        d.mkdir(parents=True, exist_ok=True)
