# AI Runtime Security Demo

A web app for demoing LLM chat with **runtime security guardrails** (Zscaler AI Guard),
agentic tool use, multi-agent orchestration, a request **flow graph**, and a usage
**dashboard**. Inspired by `zscalerzoltanorg/ai-runtime-security-demo`, with three
deliberate differences:

1. **No hardcoded localhost — uses the host IP.** The server binds to `0.0.0.0`, the app
   auto-detects the machine's LAN IP for display, and the browser UI makes only relative
   API calls, so it works from any host on your network.
2. **No bundled Ollama.** The app never installs or runs Ollama. A first-run **Setup Wizard**
   prompts you for your own Ollama **host/IP and port**, with a live "Test connection" check.
3. **Config reachable by host IP.** Settings/config endpoints are **not** restricted to
   localhost. An optional `ADMIN_TOKEN` guards saving config.

## Features

- 💬 **Chat** — streaming responses, markdown + code rendering, provider/model switching.
- 🛡️ **Zscaler AI Guard** — optional prompt/response inspection (DAS or Proxy mode); blocked
  content is surfaced inline. Degrades gracefully with no credentials.
- 🤖 **Agentic** — single-agent tool loop with bundled tools (DuckDuckGo, Wikipedia, arXiv,
  bounded local-workspace read).
- 👥 **Multi-agent** — orchestrator → researcher → auditor → reviewer.
- 🕸️ **Flow graph** — per-request hop tracing (UI → AI Guard → provider → tools) with latency.
- 📊 **Dashboard** — token usage and estimated cost (SQLite-backed).
- ⚔️ **Adversarial presets** — prompt-injection / jailbreak / PII test prompts.
- 🔌 **MCP** — bundled MCP stdio server exposing the same toolset.

## Providers

- **Ollama** (external, required URL via Setup) · **Anthropic** · **OpenAI** (cloud, optional keys).

## Quick start (Docker)

> You need an **Ollama** instance running somewhere reachable (this app does **not** provide one).

```bash
cp .env.example .env.local
docker compose up -d --build
```

Then open the app on your **host IP** (printed in the container logs), e.g.
`http://<host-ip>:5000`. On first run you'll be redirected to the **Setup Wizard**:

1. Enter your Ollama **host/IP** and **port** → click **Test connection**.
   - Ollama on the same machine as Docker? Use `host.docker.internal` (compose maps it) or
     the host's LAN IP — **not** `localhost`.
   - Ollama on another machine? Use its IP, e.g. `http://192.168.1.50:11434`.
2. (Optional) add Anthropic/OpenAI keys.
3. (Optional) add a Zscaler AI Guard key + mode.
4. **Finish & launch** → start chatting.

Setup and metrics persist in the `ai_lab_data` Docker volume across `docker compose down/up`.

## Deploy on Proxmox (LXC)

Spin the app up in a Proxmox **LXC container**, running natively (gunicorn +
systemd, no Docker). On your Proxmox VE host, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/H0ppo/ai-lab/main/deploy/proxmox-lxc.sh -o proxmox-lxc.sh
bash proxmox-lxc.sh
```

The interactive script creates an unprivileged Debian 12 container (DHCP),
deploys the app, and prints `http://<lxc-ip>:5000/setup`. See
[`deploy/README.md`](deploy/README.md) for details, updates, and management.

## Configuration

Layered, lowest to highest precedence:

1. **Environment** (`.env.local`, see `.env.example`)
2. **`setup.json`** (written by the Setup Wizard, stored in the `/data` volume)
3. **In-app Settings** (also persisted to `setup.json`)

Key variables: `HOST` (default `0.0.0.0`), `PORT`, `OLLAMA_URL` (blank → wizard prompts),
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `ZS_GUARDRAILS_*`, `ADMIN_TOKEN`.

## Security notes

- Secrets live in git-ignored `.env.local` / `setup.json`; the API masks secret values
  (only "is it set?" is exposed to the browser).
- Config is reachable from the host IP by design. **Set `ADMIN_TOKEN`** to require an
  `X-Admin-Token` header for saving config when exposing the app on an untrusted network.
- The local-workspace tool is confined to `LOCAL_TASKS_BASE_DIR`.

## Architecture

```
app.py            Flask entry — 0.0.0.0 bind, first-run gate, blueprint registration
config.py         Layered config + host-IP detection + setup.json persistence
providers.py      Ollama / Anthropic / OpenAI adapters (streaming)
guardrails.py     Zscaler AI Guard (DAS / Proxy), optional & graceful
agentic.py        Single-agent tool loop
multi_agent.py    Orchestrator + specialists
tooling.py        Bundled tool registry (web/wiki/arxiv/workspace)
mcp_tool_server.py / mcp_client.py   Bundled MCP stdio server + client
tracing.py        Per-request hop traces → flow graph
metrics.py        SQLite usage/cost tracking
blueprints/       chat, setup, settings, agents, dashboard routes
templates/ static/  Jinja2 UI + design-token CSS + vanilla JS (relative API calls)
```

## Local development (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py        # binds 0.0.0.0:5000, prints the host-IP URL
```
