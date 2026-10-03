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
│   ├── policy/
│   │   ├── broker.py           # the ONLY path from agent to tools: checks grants, asks confirmation, audits
│   │   └── grants.py           # per-user, per-skill permissions (read/write scopes, domains, tools)
│   ├── sandbox/
│   │   ├── base.py             # Sandbox interface: run(code, files_in) -> files_out, stdout, exit code
│   │   ├── local.py            # subprocess backend for dev and tests (NOT a security boundary)
│   │   └── daytona.py          # Daytona backend (ephemeral, no secrets, egress allowlist)
│   ├── scheduler/
│   │   └── jobs.py             # background and scheduled tasks
│   ├── auth/
│   │   └── users.py            # simple per-user accounts and isolation
│   └── web/                    # templates and static files
├── skills/                     # one folder per skill
│   └── _template/
│       ├── skill.yaml          # name, description, tools needed, triggers, permissions
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
│       ├── grants.yaml         # what each enabled skill may read/write/reach (user-editable)
│       └── logs/               # includes audit.jsonl: every tool call, allowed or blocked
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
CACHE_ENABLED=true             # dev cache for identical LLM and Tavily calls
CACHE_DIR=./.cache
SANDBOX_BACKEND=local          # local (dev only) | daytona
DAYTONA_API_KEY=
```

## Design principles

- **Memory is visible.** Each memory is a small Markdown file with front matter (`created`, `updated`, `source`, `confidence`). The UI has a "What I remember about you" page where users can view, edit, delete, and export everything as a zip.
- **Skills are pluggable.** The core must work with zero skills enabled. Adding a skill means adding a folder, with no changes to the core code.
- **Users are isolated.** Every memory, skill setting, and log path is scoped to `data/users/<user_id>/`. Write a test that proves one user can't read another's data.
- **Route models cheaply.** Default to Nano. Escalate to Ultra only for multi-step planning or complex reasoning. Log which model each call used, along with its token counts.
- **Ask before acting.** Anything that sends, buys, deletes, or posts on the user's behalf needs explicit confirmation in the UI. Read-only actions don't.
- **Fail visibly.** If a model or API call fails, show a clear message rather than silently degrading.

## Isolation and permissions

Threat model: skills can be written by anyone, web pages and emails the agent reads can carry prompt injections, and the model can make mistakes. Assume any tool call may be hostile. There are three layers, plus a rule about secrets:

1. **Permission broker (always on, in-process).** The agent never calls a tool directly. Every call goes through `app/policy/broker.py`, which:
   - checks the call against the skill's declared permissions (`skill.yaml`) **and** the user's grants (`grants.yaml`). Both must allow it.
   - separates **read** from **write**: reads run if granted; writes, sends, deletes and purchases also need the UI confirmation.
   - logs every call, allowed or blocked, to `data/users/<id>/logs/audit.jsonl`. The UI shows this as "What your assistant did".
   - default-deny: anything not declared is blocked.
2. **Path and data scoping (always on).** File tools resolve every path inside `data/users/<id>/` and reject `..`, absolute paths and symlinks pointing elsewhere. Memory read/write runs in-process through this layer. **Memory never goes into a remote sandbox.**
3. **Execution sandbox (only for running code).** Only these run in a sandbox: a skill's `tools.py` if the skill isn't built in, code the agent writes, and parsing of untrusted files. Sandbox rules:
   - ephemeral: one sandbox per task, destroyed afterwards
   - copy in only the files the task needs, and copy out only declared outputs
   - egress allowlist from the skill's declared domains, otherwise no network
   - CPU and time limits

**Secrets never enter a sandbox.** If sandboxed code needs an API (LLM, Tavily, email), it asks the broker, which makes the call with the core's credentials.

Backends sit behind `app/sandbox/base.py`: `local` (subprocess, dev/tests only, not a security boundary) and `daytona`. **NVIDIA OpenShell** (a suggested tool for this track: kernel-level filesystem, network and syscall policy) is a candidate third backend, to trial on a Nebius Ubuntu 24.04 VM. Per its official docs it needs Linux kernel ≥ 6.2 and Docker ≥ 28 (or Podman 5); Windows works only through experimental WSL 2; running it inside Daytona is undocumented. Its Python SDK is `openshell` (gRPC to a gateway). The `nvidia_agent_toolkit` SDK mentioned in a third-party blog does not exist. See `docs/track-tools-evaluation.md`. The core must never depend on OpenShell.

`skill.yaml` permissions shape (draft):
```yaml
permissions:
  read: [memory, calendar]            # scopes the skill may read
  write: [memory]                     # scopes it may change (always confirmed in the UI)
  network: [api.example.com]          # egress allowlist when its code runs in the sandbox
  tools: [web_search]                 # core tools it may call through the broker
  runs_code: false                    # true -> tools.py executes in the sandbox
```

## Budget constraints (important)

Credits are limited: about $125 of Nebius Cloud, plus Token Factory credits, 9,000 Tavily credits, and $2,000 of Daytona credits.
- **Never** add GPU infrastructure or self-hosted models. All inference goes through Token Factory.
- Use Tavily **basic** search (1 credit) by default. Use advanced (2 credits) only when asked. **Never** call the Tavily Research endpoint in loops.
- Cache identical LLM and search calls during development.
- Keep the Docker image small enough to run on the smallest CPU VM.
- Daytona: smallest resources (1 vCPU / 1 GB), ephemeral per-task sandboxes, short auto-stop, and always delete sandboxes after use. No always-on sandboxes per user.

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
8. Skill loader and registry, plus the `_template` skill (with `permissions:` in `skill.yaml`)
8a. Permission broker, grants and audit log; path scoping for file tools; tests proving undeclared and cross-user calls are blocked
8b. Sandbox interface with the `local` backend, then the `daytona` backend (ask before adding the `daytona` dependency)
9. Tavily web search tool, available to the agent through the broker
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
| 3 Oct 2026 | Identical LLM and Tavily calls are cached on disk in `.cache/` (gitignored, toggle with `CACHE_ENABLED`). LLM usage log lives at `data/logs/llm_usage.jsonl` until per-user logs exist. `openai` SDK is v3.x |
| 3 Oct 2026 | Isolation is three layers: an in-process permission broker (default-deny, read/write split, audit log) → per-user path scoping → an execution sandbox only for running code. Daytona is the first sandbox backend; memory and secrets never enter it. OpenShell gets evaluated on Day 7–8 |
| 3 Oct 2026 | Track confirmed as Personal AI, backed by the research findings (memory, privacy/data control, and portability are the top themes) |
| 3 Oct 2026 | Positioning is "open, inspectable, user-owned" compared with big tech assistants like Meta's Muse. Never claim to be "more private" than a named competitor. No third-party logos or screenshots in the demo video or README |
| 3 Oct 2026 | Nemotron reasoning is switchable per call via `chat_template_kwargs.enable_thinking` (on/off only; `reasoning_budget` and `reasoning_effort` had no reliable effect on Token Factory). Router: `complete(..., thinking=False)`. One extraction batch: 7,257 → 507 output tokens, 36.7 s → 3.1 s, coarser items. Use thinking off for simple calls |
| 3 Oct 2026 | Track tools evaluated (`docs/track-tools-evaluation.md`): keep our own agent loop; skip NemoClaw and Hermes as dependencies, but borrow Hermes formats (agentskills.io-compatible `SKILL.md` front matter, memory tool actions add/replace/remove); OpenShell is a "maybe" sandbox backend; skip Serverless Jobs (keep APScheduler) |
| 3 Oct 2026 | Native OpenAI-style tool calling works on Token Factory for Nano and Ultra, with thinking on or off: correct `tool_calls`, valid JSON args, uses the tool result, no spurious calls (`scripts/smoke_tools.py`). But Nano with thinking **off** answered 12×7 = 72 (reproducible), so the loop keeps thinking on (Nano default) for any turn that needs reasoning, and uses thinking off only for formatting/extraction. Hosting: Nebius VM is primary; Daytona hosting needs Tier 3+ (Tier 1–2 egress blocks Token Factory/Tavily) |
| 3 Oct 2026 | Skills 1–3: still pending until the form closes Sunday evening |
