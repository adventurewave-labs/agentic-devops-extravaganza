"""
POST /api/scan — run k8sgpt-style analyzers against the broken cluster.

Returns the PRD §6.4 response: findings[], duration_ms, cluster_state.
"""
from __future__ import annotations
import json
import time
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _lib  # type: ignore  # noqa: E402


def _build_response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(body, default=str),
    }


def handler(request=None):
    started_at = time.perf_counter()
    cold_start = _lib.detect_cold_start()
    try:
        response = _lib.build_scan_response(
            cluster_state="broken",
            duration_ms=0,
            cold_start=cold_start,
        )
        response["duration_ms"] = int((time.perf_counter() - started_at) * 1000)
        return _build_response(200, response)
    except Exception as e:
        # PRD §6.6 case 1: k8sgpt unavailable → fall back to cached 8 findings.
        # We construct the cached payload from a fresh ClusterState so the
        # touchpoint still works even if the analyzers themselves fail.
        try:
            state = _lib.reset_state()
            findings = _lib.run_analyzers(state)
            cached = {
                "findings": findings,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "cluster_state": "broken",
                "findings_count": len(findings),
                "findings_by_severity": {
                    "critical": sum(1 for f in findings if f["severity"] == "critical"),
                    "warning": sum(1 for f in findings if f["severity"] == "warning"),
                    "info": sum(1 for f in findings if f["severity"] == "info"),
                },
                "cached": True,
                "cached_reason": f"scan failed: {e}",
                "scan_id": "cached",
                "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            return _build_response(200, cached)
        except Exception as fallback_err:
            return _build_response(500, {
                "error": f"scan failed and fallback also failed: {fallback_err}",
                "original_error": str(e),
            })


# Vercel Python SDK shape
try:
    from vercel_functions import Request, Response  # type: ignore

    async def POST(request: Request) -> Response:
        result = handler(request)
        return Response(
            status_code=result["statusCode"],
            headers=result["headers"],
            body=result["body"],
            media_type="application/json",
        )

    async def GET(request: Request) -> Response:
        return await POST(request)

    async def OPTIONS(request: Request) -> Response:
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )
except ImportError:
    pass
