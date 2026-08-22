"""
Fail the build when the docs and the code disagree.

The scan and remediation numbers have been CI-asserted since the repo was
made honest, but nothing tied *the prose* to the code. That gap showed up
immediately: adding one acceptance check left index.html advertising "17
checks" while the suite ran 18, and a page section still linked an output
file deleted two PRs earlier. Both were true when written and went stale one
commit later.

Drift is not lying, but a reader cannot tell the difference. So the claims
that can be checked mechanically are checked mechanically:

  1. every repo-relative path the docs reference actually exists
  2. the acceptance-check count the docs advertise equals the number of
     checks run_uat.py really runs
  3. the acceptance table in index.html lists every check id, none missing
  4. the finding count the docs advertise equals the count in the committed
     k8sgpt output
  5. no retired claim or dead filename reappears anywhere

Usage:
    python scripts/check_docs.py          # exits non-zero on any mismatch
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "index.html"]

# Claims and filenames this repo retired. If one comes back, it is either a
# regression or a new file that needs a different name.
RETIRED = {
    "no mocks": "the cluster is a mock; the file is called mock_k8s_server.py",
    "0 mock": "same",
    "zero mocks": "same",
    "wow_demo": "the synthesized cinematic demo was deleted in favour of live recordings",
    "k8sgpt_scan.gif": "renamed to scan.gif",
    "k8sgpt_explain.gif": "renamed to explain.gif",
    "robusta.gif": "renamed to triage.gif",
    "zai_proxy": "replaced by the provider-agnostic llm_proxy.py",
    "robusta_demo.py": "renamed to alert_triage_agent.py",
    "build_casts.py": "replaced by record_demos.py",
    ".z-ai-config": "replaced by LLM_BACKEND env vars / .llm-config",
    "popeye": "never invoked by this repo",
    "15/15": "the unreproducible UAT badge",
}

PATH_RE = re.compile(
    r"(?<![\w./-])((?:scripts|gifs|outputs|captured|recordings|manifests|kind|alerts|"
    r"mock-k8s|site|\.github)/[A-Za-z0-9_./-]+?)(?=[)\s\"'<`,]|$)")

failures = []
notes = []


def fail(doc, message):
    failures.append(f"{doc}: {message}")


def check_paths():
    for doc in DOCS:
        text = (ROOT / doc).read_text()
        for ref in sorted(set(PATH_RE.findall(text))):
            if ref.endswith("/*") or "*" in ref:
                continue
            if not (ROOT / ref).exists():
                fail(doc, f"references {ref}, which does not exist")
        notes.append(f"{doc}: {len(set(PATH_RE.findall(text)))} path references checked")


def actual_check_ids():
    source = (ROOT / "scripts" / "run_uat.py").read_text()
    return sorted(set(re.findall(r'suite\.check\(\s*"([A-E]\d)"', source)))


# The landing page states the check count twice: once in prose ("18 checks
# you can run yourself") and once as a bare number in a stat element. An
# earlier version of this checker only knew about the prose form, so reverting
# the stat number alone slipped straight past it - the check looked stricter
# than it was. Both forms are matched now.
COUNT_PATTERNS = (
    r"(\d+)\s+checks",                                  # prose
    r'class="uat-big-num"[^>]*>\s*(\d+)\s*<',           # stat element
)


def check_counts():
    ids = actual_check_ids()
    if not ids:
        fail("scripts/run_uat.py", "found no suite.check() ids to count")
        return
    seen = 0
    for doc in DOCS:
        text = (ROOT / doc).read_text()
        for pattern in COUNT_PATTERNS:
            for claimed in re.findall(pattern, text):
                seen += 1
                if int(claimed) != len(ids):
                    fail(doc, f'advertises "{claimed}" as the check count but '
                              f"run_uat.py runs {len(ids)} "
                              f"({', '.join(ids)})")
    notes.append(f"acceptance suite: {len(ids)} checks ({ids[0]}..{ids[-1]}), "
                 f"{seen} count claim(s) in the docs")


def check_table_rows():
    """index.html renders a row per acceptance check; none may be missing."""
    html = (ROOT / "index.html").read_text()
    ids = actual_check_ids()
    rendered = set(re.findall(r">\s*([A-E]\d)\s*<", html))
    if not rendered:
        return  # no table on the page; nothing to keep in sync
    missing = [i for i in ids if i not in rendered]
    if missing:
        fail("index.html", "acceptance table is missing rows for "
                           f"{', '.join(missing)}")
    notes.append(f"index.html: {len(rendered)} acceptance rows rendered")


def check_finding_count():
    captured = ROOT / "outputs" / "k8sgpt_analyze.txt"
    if not captured.exists():
        fail("outputs/k8sgpt_analyze.txt", "missing; cannot verify the finding count")
        return
    real = len(re.findall(r"^\d+: ", captured.read_text(), re.M))
    for doc in DOCS:
        text = (ROOT / doc).read_text()
        for claimed in re.findall(r"(\d+)\s+(?:k8sgpt )?findings", text):
            if int(claimed) not in (real, 0):
                fail(doc, f'advertises "{claimed} findings" but the committed '
                          f"k8sgpt output has {real}")
    notes.append(f"committed k8sgpt output: {real} findings")


def check_retired_claims():
    for doc in DOCS + ["site/DEPLOY.md"]:
        path = ROOT / doc
        if not path.exists():
            continue
        lowered = path.read_text().lower()
        for phrase, why in RETIRED.items():
            if phrase.lower() in lowered:
                # The README's history note quotes what was removed on purpose.
                if doc == "README.md" and "history of this repo" in lowered:
                    continue
                fail(doc, f'contains retired claim "{phrase}" ({why})')


def main():
    check_paths()
    check_counts()
    check_table_rows()
    check_finding_count()
    check_retired_claims()

    for note in notes:
        print(f"  {note}")
    if failures:
        print(f"\n{len(failures)} docs/code disagreement(s):\n")
        for failure in failures:
            print(f"  FAIL  {failure}")
        print("\nThe docs make a claim the repo does not support. Fix the doc "
              "or fix the code - do not delete the check.")
        return 1
    print("\nDocs agree with the code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
