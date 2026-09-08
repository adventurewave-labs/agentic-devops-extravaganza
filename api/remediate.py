"""
POST /api/remediate — apply the 7 kubectl remediation steps and re-scan.

Returns the PRD §6.4 response with cluster_state="fixed" and the list of
applied steps. Expected findings_count after remediation: 0.
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
    try:
        response = _lib.build_remediate_response(
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
        return _build_response(200, response)
    except Exception as e:
        return _build_response(500, {
            "error": f"remediate failed: {e}",
            "exception_type": type(e).__name__,
        })


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

    async def OPTIONS(request: Request) -> Response:
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )
except ImportError:
    pass
