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
cp .env.example .env   # then fill in your keys and model IDs
```

Check the Token Factory connection:

```bash
python scripts/smoke_llm.py
```

Run the tests:

```bash
pytest
```

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
