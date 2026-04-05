"""Reverse proxy for Pipecat WebRTC signaling.

Proxies HTTP requests from the unified MCP server port (9090) to the
internal Pipecat WebRTC server (7860), so the browser only needs to
talk to a single port.
"""

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

PIPECAT_BACKEND = "http://localhost:7860"


async def _proxy(request: Request) -> Response:
    """Forward a request to the Pipecat backend and return its response."""
    url = f"{PIPECAT_BACKEND}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    body = await request.body()
    headers = dict(request.headers)
    # Remove hop-by-hop headers
    for h in ("host", "transfer-encoding"):
        headers.pop(h, None)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method=request.method,
                url=url,
                content=body,
                headers=headers,
                timeout=30.0,
            )
    except httpx.ConnectError:
        return JSONResponse(
            {"error": "Voice pipeline not running. Call start_convo() first."},
            status_code=502,
        )

    # Strip headers that uvicorn will add itself to avoid duplicates
    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in ("date", "server", "transfer-encoding")
    }

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=resp_headers,
    )


# Routes to mount on the unified server
proxy_routes = [
    Route("/start", _proxy, methods=["POST"]),
    Route("/api/offer", _proxy, methods=["POST", "PATCH"]),
    # Pipecat Cloud compat: /sessions/{id}/api/offer
    Route("/sessions/{session_id:path}/api/offer", _proxy, methods=["POST", "PATCH"]),
]
