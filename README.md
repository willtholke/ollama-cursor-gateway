# Ollama Cursor Gateway

A minimal OpenAI-compatible proxy that sits between Cursor and Ollama Cloud, rewriting model names so Cursor's built-in model entries stop colliding with Ollama's slugs.

## The problem

Cursor ships built-in entries for models that also exist on Ollama Cloud (GLM 5.2, Kimi K3, Kimi K2.7 Code). When a built-in entry exists, Cursor refuses to let you add a custom model with the same name. Cursor's built-in entries send mangled model strings that don't exist on Ollama, producing 404s.

This gateway exposes the same models under short alias names (`k3`, `glm`, `k27`) that Cursor has no built-in for, and rewrites them to the real Ollama slugs server-side.

## Quick start

```bash
# Clone
git clone https://github.com/willtholke/ollama-cursor-gateway.git
cd ollama-cursor-gateway

# Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Fill in OLLAMA_API_KEY from https://ollama.com/settings/api-keys
# Generate GATEWAY_SECRET: openssl rand -hex 32

# Run
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Deploy to Railway

1. Push this repo to GitHub
2. Create a new Railway project, point it at the repo
3. Add environment variables: `OLLAMA_API_KEY`, `GATEWAY_SECRET`
4. Railway auto-detects the Python app and deploys

## Cursor setup

1. Settings > Models > API Keys
2. **OpenAI API Key**: paste your `GATEWAY_SECRET`. Ensure the toggle beside it is **on**.
3. **Override OpenAI Base URL**: on, set to `https://<your-domain>/v1`
4. Click Verify
5. Settings > Models > Add Custom Model, add `k3`, `glm`, `k27` as separate entries
6. Disable every other model in the list (the base URL override is global)
7. Fully quit and relaunch Cursor

## Alias map

| Alias | Ollama model |
|---|---|
| `k3` | `kimi-k3` |
| `glm` | `glm-5.2` |
| `k27` | `kimi-k2.7-code` |

Any model name not in the map passes through unchanged, so `deepseek-v4-pro` and other custom models keep working through the same gateway.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `OLLAMA_API_KEY` | Yes | Your Ollama Cloud API key |
| `GATEWAY_SECRET` | Yes | Secret Cursor sends as its API key |
| `UPSTREAM_BASE` | No | Defaults to `https://ollama.com/v1` |
| `REWRITE_STREAM_MODEL` | No | Rewrite model name in streaming chunks (default: false) |

## Verification

```bash
# Health
curl https://<domain>/health

# Models list
curl https://<domain>/v1/models -H "Authorization: Bearer $GATEWAY_SECRET"

# Non-streaming
curl https://<domain>/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"model":"k3","messages":[{"role":"user","content":"say hi"}]}'

# Streaming (should arrive incrementally, not all at once)
curl -N https://<domain>/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"model":"glm","messages":[{"role":"user","content":"count to 20 slowly"}],"stream":true}'

# Bad secret
curl https://<domain>/v1/models -H "Authorization: Bearer wrong"
```

## Known constraints

- **Tab autocomplete** will never work (Cursor Tab is a proprietary model, not a chat completion call)
- **Codebase indexing and background agents** are Cursor-cloud-only
- **Agent / Composer mode** is unreliable with custom providers (Cursor's `/v1` path has weaker tool-calling than Ollama's native `/api/chat`)
- **The base URL override is global**, not per-model. Running Cursor's built-in Claude/GPT models alongside this setup requires toggling the override off

## License

[MIT](https://opensource.org/licenses/MIT)
