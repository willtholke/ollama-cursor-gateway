import json
import logging
import os
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OLLAMA_API_KEY = os.environ["OLLAMA_API_KEY"]
GATEWAY_SECRET = os.environ["GATEWAY_SECRET"]
UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "https://ollama.com/v1")
REWRITE_STREAM_MODEL = os.environ.get("REWRITE_STREAM_MODEL", "false").lower() == "true"

ALIASES: dict[str, str] = {
    "k3": "kimi-k3",
    "glm": "glm-5.2",
    "k27": "kimi-k2.7-code",
}

# Reverse map for response rewriting
REVERSE_ALIASES: dict[str, str] = {v: k for k, v in ALIASES.items()}

# Known-good OpenAI-compat keys (used only if upstream rejects unknown fields)
PASSLIST = {
    "model", "messages", "stream", "temperature", "top_p",
    "max_tokens", "stop", "tools", "tool_choice",
    "presence_penalty", "frequency_penalty", "seed", "response_format",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("gateway")

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

client = httpx.AsyncClient(
    base_url=UPSTREAM_BASE,
    headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
    timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0),
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="ollama-cursor-gateway")


def check_auth(request: Request) -> None:
    """Validate inbound Authorization header against GATEWAY_SECRET."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth.removeprefix("Bearer ")
    if token != GATEWAY_SECRET:
        raise HTTPException(status_code=401, detail="Invalid gateway secret")


def rewrite_model(body: dict) -> str | None:
    """Rewrite alias -> real model name. Returns the alias if rewritten, else None."""
    model = body.get("model", "")
    if model in ALIASES:
        real = ALIASES[model]
        body["model"] = real
        return model
    return None


def rewrite_response_model(data: dict) -> None:
    """Rewrite real model name back to alias in a response body."""
    model = data.get("model", "")
    if model in REVERSE_ALIASES:
        data["model"] = REVERSE_ALIASES[model]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Models list
# ---------------------------------------------------------------------------

@app.get("/v1/models")
async def list_models(request: Request):
    check_auth(request)

    # Fetch real models from Ollama
    try:
        resp = await client.get("/models")
        resp.raise_for_status()
        upstream_data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch upstream models: %s", e)
        upstream_data = {"object": "list", "data": []}

    # Append alias entries
    alias_entries = [
        {"id": alias, "object": "model", "created": 1785164400, "owned_by": "gateway"}
        for alias in ALIASES
    ]

    existing = upstream_data.get("data", [])
    upstream_data["data"] = existing + alias_entries
    return upstream_data


# ---------------------------------------------------------------------------
# Chat completions
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    check_auth(request)

    body = await request.json()
    alias = rewrite_model(body)
    stream = body.get("stream", False)

    logger.info(
        "model=%s -> %s stream=%s",
        alias or body.get("model"),
        body.get("model"),
        stream,
    )

    if stream:
        return await _handle_stream(body, alias)
    else:
        return await _handle_non_stream(body, alias)


async def _handle_non_stream(body: dict, alias: str | None) -> JSONResponse:
    """Forward non-streaming request and rewrite model in response."""
    resp = await client.post("/chat/completions", json=body)

    if resp.status_code != 200:
        logger.error("upstream error %s: %s", resp.status_code, resp.text[:500])
        return JSONResponse(
            status_code=resp.status_code,
            content=resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"error": resp.text},
        )

    data = resp.json()
    if alias:
        rewrite_response_model(data)
    return JSONResponse(content=data)


async def _handle_stream(body: dict, alias: str | None) -> StreamingResponse:
    """Forward streaming request with true streaming (no buffering)."""
    req = client.build_request("POST", "/chat/completions", json=body)

    async def event_stream() -> AsyncIterator[bytes]:
        async with client.stream(req) as resp:
            if resp.status_code != 200:
                # Read error body and yield as a single SSE error
                error_body = await resp.aread()
                logger.error("upstream stream error %s: %s", resp.status_code, error_body[:500])
                yield f"data: {json.dumps({'error': f'upstream {resp.status_code}'})}\n\n".encode()
                yield b"data: [DONE]\n\n"
                return

            async for raw_line in resp.aiter_lines():
                if not raw_line:
                    continue

                if REWRITE_STREAM_MODEL and alias and raw_line.startswith("data:") and raw_line != "data: [DONE]":
                    try:
                        chunk = json.loads(raw_line.removeprefix("data:").strip())
                        rewrite_response_model(chunk)
                        raw_line = f"data: {json.dumps(chunk)}"
                    except json.JSONDecodeError:
                        pass

                yield f"{raw_line}\n".encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

@app.on_event("shutdown")
async def shutdown():
    await client.aclose()
