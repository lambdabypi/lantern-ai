# Personal AI

An open-source, always-on personal assistant that anyone can make their own: memory you can read and edit, skills you can switch on and off, and data that stays yours.

Built for the **Nebius x NVIDIA Global AI Hackathon** (Personal AI track).

> Work in progress. Built in public.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ".[desktop]"   # optional: the desktop companion (PySide6)
cp .env.example .env   # then fill in your keys and model IDs
```

Check the Token Factory connection:

```bash
python scripts/smoke_llm.py
```

Run the app, then open http://127.0.0.1:8000:

```bash
uvicorn app.main:create_app --factory --reload
# or: docker build -t personal-ai . && docker run --env-file .env -p 8000:8000 personal-ai
```

Check that native tool calling works on your models:

```bash
python scripts/smoke_tools.py
```

Run the research sprint (Tavily search → Nano extraction → Ultra clustering → `research/findings.md`):

```bash
python scripts/research_sprint.py
```

Run the tests (mocked, no tokens spent). The live test is opt-in:

```bash
pytest
RUN_LIVE_TESTS=1 pytest tests/test_live.py
```

## Accounts and privacy

- **Try it as a guest:** one click, no sign-up. Each guest gets their own isolated space, deleted automatically after 7 days.
- **Accounts:** username and password (hashed with scrypt). There's no email address and no tracking.
- **Isolation:** every user's data lives only in `data/users/<id>/`, and the tests in `tests/test_isolation.py` prove users can't see each other's chats or logs.
- **Desktop companion:** it signs in with a personal access token you create and can revoke.
- **Where your data goes:** your messages go to Nebius Token Factory to generate replies, and web searches go to Tavily. Nothing else leaves the app.

## How the agent works

Each message runs a loop: Nemotron Nano picks tools via native tool calling, every tool call goes through a single `ToolRegistry` checkpoint, and results go back to the model until it answers.

- **Guardrails:** at most 6 tool calls and 40k tokens per message, plus a timeout on every tool. When a limit is hit, you get a clear message instead of a silent failure.
- **Confirmation:** tools that would act on your behalf are flagged `requires_confirmation` and refused until the confirmation UI exists.
- **Audit log:** every tool call is logged to `data/users/<id>/logs/audit.jsonl` with secrets redacted and result sizes only.
- **Starter tools:** `calculator` (the model never does arithmetic itself) and `web_search` (Tavily, basic depth).
- **Think harder:** sends the turn to Nemotron Ultra instead.

## How we use NVIDIA Nemotron and Nebius Token Factory

All LLM calls go through the **Nebius Token Factory** OpenAI-compatible API.

| Model | Used for |
|---|---|
| Nemotron 3 Nano (`MODEL_FAST`) | Default for fast, everyday calls (e.g. extraction) |
| Nemotron 3 Ultra (`MODEL_REASONING`) | Multi-step planning and heavy reasoning only |

Every call is logged with the model used and its token counts (`data/logs/llm_usage.jsonl`).

## Web search

Web search uses the **Tavily** API (basic search by default).

## License

MIT
