# CLAUDE.md: Project Brief

> Put this file at the root of the repo. Claude Code reads it automatically at the start of every session.
> Keep it updated as decisions are made (especially after the idea is locked on Sat 3 Oct).

## What we're building

**A personal AI anyone can make their own.** Each user gets their own always-on assistant with:
- **Transparent memory:** everything the assistant remembers is stored as plain, human-readable files the user can open, edit, delete, and export.
- **Reusable skills:** each skill is a self-contained folder that can be switched on or off per user, and written or shared by anyone.
- **Chosen tools and data:** the user decides which tools and data sources the assistant can access.
- **Real task execution:** it carries out multi-step tasks, including in the background on a schedule.
- **User-owned data:** data is isolated per user, never mixed, and never leaves the user's control except for model and search API calls.

The developer (Shreyas) is user #1. The project is open source and built in public.

**Status:** the core idea is locked on **Sat 3 Oct 2026**. The first 2–3 skills are TBD. Build the foundation so skills can be plugged in later. Don't hard-code any specific skill into the core.

## Hackathon context (hard requirements)

Entry for the **Nebius x NVIDIA Global AI Hackathon, Personal AI Track**. Submit on 28 Oct 2026; the official deadline is 30 Oct 2026 at 10:00 am PT. These are non-negotiable:

1. **Runtime calls to Nebius Token Factory** inference API for all LLM calls.
2. Use **NVIDIA Nemotron** open models (Nano for fast everyday calls, Ultra for heavy reasoning; Super is optional).
3. **Public repo with an open source license** (MIT) visible at the top of the repo page.
4. **README** with setup instructions, clear run guidance, and a section on how Nemotron, Token Factory, and any other Nebius tools are used.
5. A **hosted demo** that judges can access free of charge **until 15 Dec 2026**.
6. **Tavily API** runtime calls for web search (needed for the Best Use of Tavily bonus prize).
7. Track tools to evaluate on Day 7–8: **NVIDIA NemoClaw, OpenShell, Hermes Agent, Nebius Serverless**. **Don't invent APIs for these.** Read their actual docs before integrating, and ask me if you're unsure.

## Tech stack (default; ask before changing)

- **Language:** Python 3.11+
- **Backend:** FastAPI
- **LLM client:** the `openai` Python SDK pointed at the Token Factory OpenAI-compatible endpoint (base URL and model IDs come from env vars; confirm both in the Token Factory console)
- **Search:** `tavily-python`
- **Memory and skills storage:** plain Markdown/YAML files on disk (see layout below). SQLite only for indexes and metadata, never as the only copy of memory.
- **Background tasks:** APScheduler (later possibly Nebius Serverless Jobs)
- **Frontend:** a simple web UI served by FastAPI (Jinja2 + HTMX, or one small React page). Clean and mobile-friendly. Design is a judging criterion.
- **Packaging:** Docker, so it runs the same locally, on a Nebius CPU VM, or in a Daytona sandbox
- **Tests:** pytest

## Repo layout (target)

```
/
├── CLAUDE.md
├── README.md
├── LICENSE                     # MIT
├── .env.example                # every variable, no real values
├── .gitignore                  # must ignore .env, data/, research/raw/
├── Dockerfile
├── pyproject.toml
├── app/
│   ├── main.py                 # FastAPI entrypoint
│   ├── config.py               # env var loading and validation
│   ├── llm/
│   │   ├── client.py           # Token Factory client
│   │   └── router.py           # chooses Nano vs Ultra per call
│   ├── memory/
│   │   ├── store.py            # read/write/delete/export memory files
│   │   └── extractor.py        # pulls durable facts out of conversations
│   ├── skills/
│   │   ├── loader.py           # discovers and loads skill folders
│   │   └── registry.py         # per-user enabled/disabled state
│   ├── tools/
│   │   └── web_search.py       # Tavily wrapper
│   ├── agent/
│   │   └── loop.py             # plan → act → observe loop with tool calls
│   ├── scheduler/
│   │   └── jobs.py             # background and scheduled tasks
│   ├── auth/
│   │   └── users.py            # simple per-user accounts and isolation
│   └── web/                    # templates and static files
├── skills/                     # one folder per skill
│   └── _template/
│       ├── skill.yaml          # name, description, tools needed, triggers
│       ├── SKILL.md            # instructions the agent follows
│       └── tools.py            # optional Python tools for this skill
├── scripts/
│   └── research_sprint.py      # see Task 0
├── research/
│   ├── raw/                    # gitignored: scraped text, never committed
│   └── findings.md             # committed: aggregated themes only
├── data/                       # gitignored: runtime user data
│   └── users/<user_id>/
│       ├── memory/             # *.md memory files, human-readable
│       ├── skills.yaml         # which skills this user has enabled
│       └── logs/
└── tests/
```

## Environment variables (`.env.example`)

```
TOKEN_FACTORY_BASE_URL=        # confirm in the Token Factory console
TOKEN_FACTORY_API_KEY=
MODEL_FAST=                    # Nemotron Nano model ID
MODEL_REASONING=               # Nemotron Ultra model ID
TAVILY_API_KEY=
APP_SECRET_KEY=
DATA_DIR=./data
```

## Design principles

- **Memory is visible.** Each memory is a small Markdown file with front matter (`created`, `updated`, `source`, `confidence`). The UI has a "What I remember about you" page where users can view, edit, delete, and export everything as a zip.
- **Skills are pluggable.** The core must work with zero skills enabled. Adding a skill means adding a folder, with no changes to the core code.
- **Users are isolated.** Every memory, skill setting, and log path is scoped to `data/users/<user_id>/`. Write a test that proves one user can't read another's data.
- **Route models cheaply.** Default to Nano. Escalate to Ultra only for multi-step planning or complex reasoning. Log which model each call used, along with its token counts.
- **Ask before acting.** Anything that sends, buys, deletes, or posts on the user's behalf needs explicit confirmation in the UI. Read-only actions don't.
- **Fail visibly.** If a model or API call fails, show a clear message rather than silently degrading.

## Budget constraints (important)

Credits are limited: about $125 of Nebius Cloud, plus Token Factory credits, 9,000 Tavily credits, and $2,000 of Daytona credits.
- **Never** add GPU infrastructure or self-hosted models. All inference goes through Token Factory.
- Use Tavily **basic** search (1 credit) by default. Use advanced (2 credits) only when asked. **Never** call the Tavily Research endpoint in loops.
- Cache identical LLM and search calls during development.
- Keep the Docker image small enough to run on the smallest CPU VM.

## Privacy and repo hygiene

- **Never commit** `.env`, API keys, `data/`, `research/raw/`, or any personal data.
- Use a **demo user with fake data** for screenshots, videos, and the hosted demo.
- `research/findings.md` contains only aggregated themes. No usernames, quotes with names, or personal details.
- Before every commit, check the diff for secrets.

## Build order

**Task 0 (today, Fri 2 Oct): research sprint script**
`scripts/research_sprint.py`:
1. Run a set of Tavily basic searches (queries in a config list), e.g. "Meta Muse review", "personal AI assistant complaints", "what I want from an AI assistant reddit", "AI assistant privacy concerns".
2. Save the raw results to `research/raw/` (gitignored).
3. Send them in batches to **Nemotron Nano** via Token Factory to extract complaints and wishes as JSON.
4. Use **Nemotron Ultra** once to cluster everything into 5–10 themes, each with a count and a one-line summary.
5. Write the themes to `research/findings.md`.
6. Print the total Tavily credits and tokens used.

**Then, in order (Phase 2 starts Mon 5 Oct):**
1. Repo skeleton, LICENSE (MIT), `.gitignore`, `.env.example`, README stub, Dockerfile
2. Config plus a Token Factory client, with one test call that works
3. Model router (Nano/Ultra) with usage logging
4. A basic chat agent loop end to end in the web UI
5. Per-user auth and data isolation, plus the isolation test
6. Memory store and the "What I remember about you" page
7. Memory extractor (automatically saves durable facts from chats)
8. Skill loader and registry, plus the `_template` skill
9. Tavily web search tool, available to the agent
10. Scheduler for background tasks
11. Skills 1–3 (defined after Sat 3 Oct)
12. Deployment (Nebius CPU VM or Daytona), from Day 20 onward

## How to work with me

- Work in small, reviewable steps. One feature per commit, with a clear commit message.
- Ask me before adding new dependencies, changing the stack, or making architectural decisions not covered here.
- After each task, tell me how to run and test it in one or two commands.
- Keep this file updated when we make decisions. Add them to the decision log below.

## Decision log

| Date | Decision |
|---|---|
| 2 Oct 2026 | Stack: Python/FastAPI, file-based memory, Token Factory via an OpenAI-compatible client |
| 3 Oct 2026 | *(idea lock: skills 1–3 go here)* |
