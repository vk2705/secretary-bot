# Technology Stack

**Analysis Date:** 2026-08-01

## Languages

**Primary:**
- Python 3.12+ - All application code (`bot.py`, `mcp_server.py`, `tests/test_bot.py`)

## Runtime

**Environment:**
- Python 3.12 or later (verified by README)

**Package Manager:**
- pip (Python)
- Lockfile: `requirements.txt` present

## Frameworks

**Core:**
- `python-telegram-bot` 21.6+ - Telegram bot with job queue for scheduled tasks
- `mcp` 1.0.0+ - Model Context Protocol server for Claude integration

**Async/LLM:**
- `openai` 1.0.0+ - OpenAI AsyncOpenAI client for LLM calls with function calling support; also used for Groq API via compatible endpoint

**Testing:**
- `pytest` - Test runner (referenced in README and test file)
- `pytest-asyncio` - Async test support (referenced in README)

**Build/Dev:**
- None detected - single-file bot architecture, no build system

## Key Dependencies

**Critical:**
- `python-telegram-bot[job-queue]` 21.6+ - Telegram bot framework with APScheduler job queue integration for persistent, timezone-aware scheduled jobs (check-ins, reminders, alerts)
- `openai` 1.0.0+ - Async OpenAI client, used for both OpenAI and Groq (via OpenAI-compatible API) LLM calls; enables function calling (tool use)
- `cryptography` 42.0.0+ - Fernet symmetric encryption for user API keys stored in SQLite

**Infrastructure:**
- `tzdata` - IANA timezone database for zoneinfo module
- `timezonefinder` 6.0.0+ - GPS coordinate → IANA timezone conversion (location-based timezone auto-detection)
- `mcp` 1.0.0+ - Model Context Protocol server library (FastMCP) for Claude Desktop/Code/claude.ai integration

## Configuration

**Environment:**
Environment variables required (loaded from `env` file per README, `.gitignore`d):
- `TELEGRAM_TOKEN` - Telegram bot token (required)
- `OPENAI_API_KEY` - OpenAI API key for fallback LLM (required)
- `GROQ_API_KEY` - Groq API key for free-tier LLM (optional; if set, keyless users use Groq instead of OpenAI)
- `MASTER_KEY` - Fernet encryption key for API keys stored in SQLite (optional; auto-generated ephemeral if unset, printed to stderr)
- `MY_CHAT_ID` - Single user's chat_id for one-time state.json migration (optional)

Remote MCP server only (via `mcp_remote.env`, systemd service):
- `MCP_REMOTE_TOKEN` - Shared secret for `/mcp?key=` auth (required for remote mode)
- `MCP_REMOTE_DOMAIN` - Public hostname for Host-header DNS-rebinding check (required for remote mode)
- `MCP_REMOTE_HOST` - Local bind address (default `127.0.0.1`)
- `MCP_REMOTE_PORT` - Local bind port (default `8545`)
- `MCP_TRANSPORT` - Set to `"remote"` to enable HTTP server mode (default: stdio for Claude Desktop)
- `BOT_STATE_FILE` - Override `state.json` path (default: relative to `mcp_server.py`)
- `BOT_DB_FILE` - Override `bot_memory.db` path (default: relative to `mcp_server.py`)

**Build:**
- No build configuration; single-file architecture
- Deployment: `nohup python3 bot.py &` (background) or systemd service for MCP server

## Platform Requirements

**Development:**
- Python 3.12+
- pip
- SQLite3 (standard library)
- POSIX-compatible filesystem for state.json atomic writes (`tempfile.mkstemp` + `os.replace`)
- Timezone database via `tzdata` package

**Production (bot.py):**
- Python 3.12+
- Linux (Amazon Linux 2 verified in `/etc/systemd/`)
- Writable filesystem for `state.json` and `bot_memory.db` (SQLite WAL)
- Telegram API connectivity (outbound HTTPS)
- OpenAI or Groq API connectivity (outbound HTTPS)

**Production (mcp_server.py remote HTTP):**
- Python 3.12+
- uvicorn ASGI server (imported at runtime in `_run_remote()`, not listed in requirements.txt — must be installed separately)
- Linux with systemd (unit: `secretary-mcp.service`)
- nginx reverse proxy on port 443 with TLS certificates (`/etc/pki/nginx/mcp-sbot.alteon.help.*`)
- Let's Encrypt TLS via certbot (`/etc/letsencrypt/renewal-hooks/deploy/`)
- Local bind to 127.0.0.1:8545 (reverse proxy only, no direct public port)

## External API Endpoints

| Service | Endpoint | Purpose | Auth |
|---------|----------|---------|------|
| Telegram | `api.telegram.org` | Bot polling/updates, sending messages | `TELEGRAM_TOKEN` |
| OpenAI | `https://api.openai.com/v1` | LLM requests, function calling | `OPENAI_API_KEY` (sk-...) |
| Groq | `https://api.groq.com/openai/v1` | LLM requests, free tier | `GROQ_API_KEY` (gsk_...) |

---

*Stack analysis: 2026-08-01*
