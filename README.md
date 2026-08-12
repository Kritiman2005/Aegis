<div align="center">

# Aegis

### Local-First AI Agent Platform — Your Data Never Leaves Your Machine

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Next.js](https://img.shields.io/badge/Next.js-15-black?style=flat-square&logo=next.js)](https://nextjs.org/)
[![Electron](https://img.shields.io/badge/Electron-31-47848F?style=flat-square&logo=electron&logoColor=white)](https://www.electronjs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

</div>

---

Aegis is an **open-source, privacy-first AI agent desktop app** that lets you automate complex workflows across your tools — GitHub, Gmail, Google Drive, Slack, Notion, Jira, and more — using natural language, with **zero data sent to the cloud**.

It runs entirely on your local machine: local LLM inference, local vector memory, local SQLite storage. No subscriptions. No telemetry. No data leakage.

---

## ✨ Features

### 🤖 Dual Mode Interface
| Mode | What it does |
|---|---|
| **Chat Mode** | Conversational AI powered by a local LLM. Ask questions, get summaries, brainstorm ideas. |
| **Agent Mode** | Multi-step planning + execution across your connected tools. The agent reads your intent, builds a plan you review, then executes it step by step. |

### 🧠 Agent Mode — How It Works
1. **Planner** — A local LLM reads your query and generates a structured, multi-step plan with a tool-call for each step.
2. **Human-in-the-Loop Confirmation** — The plan is displayed in the UI before anything executes. You see every step and can edit or cancel.
3. **Executor** — Each step calls the right MCP tool with the exact arguments extracted from prior results. No hallucinated IDs.
4. **Automatic Pagination** — For queries like "how many…" or "list all…", the agent auto-loops through paginated API responses (`exhaustive` scope). Cap-hit pauses ask you to continue or stop.
5. **Synthesis** — A final LLM pass reads all raw tool outputs and writes a coherent, natural-language answer.

### 🔌 Connectors (MCP-based)
Connect to your tools with one click via OAuth or API key. All tokens are persisted locally and **auto-restored** on every restart.

| Connector | Auth Type |
|---|---|
| Google Drive & Gmail | OAuth 2.0 |
| GitHub | OAuth 2.0 |
| Slack | OAuth 2.0 |
| Notion | OAuth 2.0 |
| Jira (Atlassian) | OAuth 2.0 |
| Linear | OAuth 2.0 |
| HubSpot | OAuth 2.0 |
| Airtable | OAuth 2.0 + PKCE |
| Salesforce | OAuth 2.0 |
| Stripe | API Key |
| Brave Search | API Key |
| Google Maps | API Key |
| Zendesk | API Key |
| Sentry | API Key |
| Elasticsearch | API Key |
| Custom stdio MCP | Any |

### 📚 Hybrid RAG (Retrieval-Augmented Generation)
Upload PDFs, Word docs, PowerPoints, and images. Aegis extracts, chunks, embeds, and stores them in a local **Qdrant** vector database. When you ask a question, it retrieves the most relevant passages using **hybrid search** (dense semantic vectors + sparse BM25 keyword matching), then re-ranks results with a cross-encoder before answering.

### 🧬 Entity Memory
The agent automatically extracts important entities (people, projects, repos, decisions) from conversations and asks if you want to remember them. Saved entities are injected back into future prompts — giving the LLM persistent, evolving context about your work.

### 🖥️ Local Model Hub
Browse and download models directly from Hugging Face model hub inside the app. Runs `llama-cpp-python` for fully offline GGUF inference.

### 🔒 Privacy Guarantee
- **No cloud inference.** All LLM calls run on your local hardware via `llama-cpp-python`.
- **No telemetry.** The app makes no analytics or tracking calls.
- **All storage is local.** SQLite (`aegis.db`) + Qdrant (`qdrant_db/`) are files on your disk.
- **Tokens saved locally.** OAuth tokens are stored in SQLite, never in a cloud keychain.

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Electron Shell                         │
│  ┌────────────────────────────────────────────────────┐  │
│  │           Next.js 15 Frontend (React 19)           │  │
│  │   ChatView · WorkflowCanvas · ConnectorGallery     │  │
│  └─────────────────────┬──────────────────────────────┘  │
│                        │  WebSocket + REST                │
│  ┌─────────────────────▼──────────────────────────────┐  │
│  │         FastAPI Backend (Python 3.11)               │  │
│  │                                                    │  │
│  │  ┌──────────┐  ┌──────────┐  ┌─────────────────┐  │  │
│  │  │  Planner │  │ Executor │  │    Synthesizer  │  │  │
│  │  │   LLM    │  │   LLM    │  │       LLM       │  │  │
│  │  └────┬─────┘  └────┬─────┘  └────────┬────────┘  │  │
│  │       │             │                 │            │  │
│  │  ┌────▼─────────────▼─────────────────▼────────┐  │  │
│  │  │           MCP Server Registry               │  │  │
│  │  │  (stdio subprocesses per connected tool)    │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  │                                                    │  │
│  │  ┌──────────────┐   ┌──────────────────────────┐  │  │
│  │  │  SQLite DB   │   │   Qdrant Vector Store    │  │  │
│  │  │  (aegis.db)  │   │    (local, embedded)     │  │  │
│  │  └──────────────┘   └──────────────────────────┘  │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## 🚀 Getting Started

### Prerequisites
- **Node.js** 18+ and **npm**
- **Python** 3.11+
- **Git**

### 1. Clone the repository
```bash
git clone https://github.com/Kritiman2005/Aegis.git
cd Aegis
```

### 2. Install frontend dependencies
```bash
npm install
```

### 3. Set up the Python backend
```bash
cd backend
python3.11 -m venv ../venv
source ../venv/bin/activate   # Windows: ..\venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configure environment variables
```bash
cp .env.example .env
```
Open `.env` and fill in the credentials for the services you want to connect. At minimum you need nothing — the app works fully offline. Add OAuth credentials only for the connectors you plan to use.

### 5. Download a local model

Place a GGUF model in `backend/models/`. The default expected path is:
```
backend/models/qwen2.5-3b-instruct-q4_k_m.gguf
```
You can download one directly from the app's **Model Hub** tab, or manually from [Hugging Face](https://huggingface.co/models?search=gguf).

### 6. Run in development mode
```bash
# From the project root — starts Next.js + FastAPI concurrently
npm run dev
```

To also open the Electron shell:
```bash
npm run dev:electron
```

> **Tip:** The backend hot-reloads automatically on Python file changes (excluding `.db` files).

---

## 📁 Project Structure

```
Aegis/
├── backend/                  # FastAPI Python sidecar
│   ├── main.py               # App entry point, startup auto-restore
│   └── app/
│       ├── api/              # REST + WebSocket routes
│       │   ├── oauth_routes.py       # Generic OAuth callback (GitHub, Slack, etc.)
│       │   ├── connectors.py         # Connector management API
│       │   └── websocket.py          # Real-time streaming
│       ├── core/
│       │   ├── agents/
│       │   │   └── chat.py           # ChatAgent state machine (Planner → Executor → Synthesis)
│       │   └── rag/
│       │       └── processor.py      # Hybrid RAG pipeline (Qdrant + FastEmbed)
│       ├── mcp/
│       │   ├── registry.py           # MCP server registry (spawns stdio subprocesses)
│       │   ├── pagination_registry.py # Deterministic cursor extraction for paginated APIs
│       │   └── response_shapers.py   # Tool output formatters (display vs executor)
│       ├── prompts/
│       │   ├── planner.py            # Planner LLM system prompt (fetch_scope, plan schema)
│       │   └── executor.py           # Executor LLM system prompt (argument generation)
│       ├── auth/
│       │   └── oauth_service.py      # OAuth 2.0 engine (PKCE, token exchange, env extraction)
│       └── db/
│           ├── database.py           # SQLAlchemy + SQLite setup
│           ├── models.py             # ORM models (MCPServer, MCPTool, User, Memory…)
│           └── crud.py               # Database operations
├── src/                      # Next.js frontend (React 19)
│   └── components/
│       ├── ChatView.tsx              # Main chat UI + plan confirmation card
│       └── WorkflowCanvas.tsx        # Agent execution workflow visualizer
├── electron/
│   └── main.ts               # Electron shell (spawns backend, loads app)
├── .env.example              # All supported environment variables with setup hints
├── requirements.txt          # Python dependencies
└── package.json              # Node scripts + Electron builder config
```

---

## 🔧 Development Scripts

| Command | Description |
|---|---|
| `npm run dev` | Start Next.js + FastAPI together |
| `npm run dev:next` | Start Next.js only (port 3000) |
| `npm run dev:backend` | Start FastAPI only (port 8000) |
| `npm run dev:electron` | Launch Electron shell |
| `npm run build` | Production build (Next.js + Electron) |
| `npm run package:mac` | Package as macOS .dmg |
| `npm run package:win` | Package as Windows .exe (NSIS) |
| `npm run package:linux` | Package as Linux AppImage + .deb |

---

## 🔐 OAuth Setup Guide

Each connector requires an OAuth app registered at the respective developer console. The redirect URI is always:

```
http://localhost:8000/auth/{service_name}/callback
```

| Service | Developer Console | Redirect URI |
|---|---|---|
| Google | [console.cloud.google.com](https://console.cloud.google.com/apis/credentials) | `/auth/google/callback` |
| GitHub | [github.com/settings/applications/new](https://github.com/settings/applications/new) | `/auth/github/callback` |
| Slack | [api.slack.com/apps](https://api.slack.com/apps) | `/auth/slack/callback` |
| Notion | [notion.so/my-integrations](https://www.notion.so/my-integrations) | `/auth/notion/callback` |
| Jira | [developer.atlassian.com](https://developer.atlassian.com/console/myapps/) | `/auth/jira/callback` |
| Linear | [linear.app/settings/api](https://linear.app/settings/api) | `/auth/linear/callback` |
| HubSpot | [app.hubspot.com/developer-docs](https://developers.hubspot.com/) | `/auth/hubspot/callback` |
| Airtable | [airtable.com/create/oauth](https://airtable.com/create/oauth) | `/auth/airtable/callback` |
| Salesforce | Salesforce Setup → App Manager | `/auth/salesforce/callback` |

After registering, copy the Client ID and Secret into your `.env` file. OAuth tokens are **persisted in SQLite** and auto-restored when Uvicorn restarts — you only authenticate once per service.

---

## 🧩 Adding a Custom MCP Server

Aegis supports any `stdio`-based MCP server. Connect one from the UI under **Connectors → Custom**, or via the API:

```bash
curl -X POST http://localhost:8000/api/connectors/connect \
  -H "Content-Type: application/json" \
  -d '{
    "server_name": "my-tool",
    "command": ["npx", "-y", "@myorg/my-mcp-server"],
    "env": {"MY_API_KEY": "sk-..."}
  }'
```

The server is registered, its tools are discovered and indexed, and it will auto-restore on restart.

---

## 🤝 Contributing

Contributions are welcome! Please open an issue first to discuss what you'd like to change.

1. Fork the repo
2. Create your feature branch (`git checkout -b feat/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feat/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

MIT © [Kritiman Talukdar](https://github.com/Kritiman2005)

---

<div align="center">
  <sub>Built with ❤️ for privacy-first AI · No cloud required</sub>
</div>