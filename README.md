# Lantern

**A personal AI that knows what you own and watches for safety recalls.** Tell Lantern about your car, car seat, crib or space heater. Every day it checks the official U.S. recall lists, tells you in plain words what's wrong and how serious it is, and helps you get the free fix. Everything it remembers is a plain file you can see, edit, delete and download.

Built for the **Nebius x NVIDIA Global AI Hackathon**. All AI runs on **NVIDIA Nemotron** models through **Nebius Token Factory**.

> **Work in progress, built in public.** Lantern pivoted from a gaming assistant to a recall watch on 24 Oct 2026. The recall data clients are done; the features marked *(in progress)* are being built now. Backlog: [GitHub Project](https://github.com/users/lambdabypi/projects/12).

## Why

- **About 1 in 5 vehicles on U.S. roads (55–58 million) has a safety recall that hasn't been fixed** ([CARFAX, via ConsumerAffairs](https://www.consumeraffairs.com/news/how-safe-is-your-car-one-in-five-have-an-open-recall-020525.html)). The repair is free, but the owner often never heard about it: the car was bought second-hand, or the letter went to an old address.
- **For household products, owner response to recalls averages under 10%.** When a recall is only announced in a press release, about **6%** of products get fixed; when owners are told directly, about **50%** ([CPSC figures, via Registria](https://www.registria.com/2018/08/31/registria-cpsc-request-responses/)).

Telling owners directly makes them roughly 8× more likely to act. A personal AI that knows what you own *is* that direct notice.

## What it does

| | Feature | Status |
|---|---|---|
| 1 | **Your things, as visible memory.** Cars (by VIN, or make, model and year) and household products, one Markdown file each. Saved from chat ("we have a 2018 Civic and a crib") or added in the app. | *(in progress)* |
| 2 | **Daily recall watch.** Checks NHTSA (vehicles) and the CPSC (household products) every day. "Do Not Drive" and "park outside" recalls come first. Mark each one fixed or doesn't apply. | *(in progress)* |
| 3 | **Book the free fix.** Recall repairs are free at any authorized dealer for your car's brand. Lantern finds dealers near you and drafts a booking message with your VIN and the recall numbers. Nothing is sent without your confirmation. | *(in progress)* |
| 4 | **Ask about your stuff.** Answers from the manufacturer's own support and manual pages, with citations. | *(in progress)* |
| – | Recall data clients for NHTSA, VIN decoding and the CPSC ([app/tools/recalls.py](app/tools/recalls.py)) | ✅ done |
| – | Accounts, guests, per-user isolation, streaming chat, daily usage caps, hosting on Cloud Run | ✅ done (from the earlier version) |

**What Lantern can't know:** NHTSA lists recalls by make, model and year. Whether *your* car's recall has already been repaired is only shown on [nhtsa.gov/recalls](https://www.nhtsa.gov/recalls) by VIN, which has no public API. Lantern says "your model is covered by this recall", links that official check, and never claims a specific car is fixed or unfixed.

## Data sources

All free and public, with no API key needed (checked live on 24 Oct 2026):

| Source | Used for |
|---|---|
| NHTSA Recalls API (`api.nhtsa.gov/recalls/recallsByVehicle`) | Vehicle recalls by make, model and year, including "park it" and "park outside" flags |
| NHTSA vPIC (`vpic.nhtsa.dot.gov/api`) | Decoding a VIN into make, model and year |
| CPSC Recalls API (`saferproducts.gov/RestWebServices/Recall`) | Household product recalls. Lantern fetches the recent list once a day for everyone and matches it against each person's things locally |
| **Tavily** | Manufacturer support pages for "ask about your stuff", finding authorized dealers, recall news |

## How we use NVIDIA Nemotron and Nebius Token Factory

Every LLM call goes to the **Nebius Token Factory** OpenAI-compatible API. Lantern routes each job to the smallest model that does it well:

| Model | Used for |
|---|---|
| Nemotron 3 Nano (`MODEL_FAST`), thinking off | Deciding whether a turn needs a lookup, saving things from chat, confirming that a recall matches something you own |
| Nemotron 3 Super (`MODEL_BALANCED`), thinking off | Writing answers from looked-up sources |
| Nemotron 3 Ultra (`MODEL_REASONING`) | "Think harder" only |

Reasoning is switched per call with `chat_template_kwargs.enable_thinking`. Every call is logged with its model and token counts (`data/logs/llm_usage.jsonl`).

**Hosting:** the web app runs on Google Cloud Run. Cloud Run only serves the app; **all inference runs on Nebius Token Factory.**

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in your keys and model IDs
```

Check the Token Factory connection, then run the app and open http://127.0.0.1:8000:

```bash
python scripts/smoke_llm.py
uvicorn app.main:create_app --factory --reload
# or: docker build -t lantern . && docker run --env-file .env -p 8000:8000 lantern
```

Run the tests (mocked, no tokens or credits spent). Live tests are opt-in:

```bash
pytest
RUN_LIVE_TESTS=1 pytest tests/test_live.py
```

Try the recall clients against the live government APIs:

```bash
python -c "from app.tools.recalls import RecallClient; c=RecallClient(); v=c.decode_vin('1HGCM82633A004352'); print(v, len(c.vehicle_recalls(v.make, v.model, v.year)), 'recalls')"
```

Deployment (Cloud Run, Cloud Storage for user data, Secret Manager): see [deploy/README.md](deploy/README.md).

## Accounts and privacy

- **Try it as a guest:** one click, no sign-up. Each guest gets their own isolated space, deleted automatically after 7 days.
- **Accounts:** username and password (hashed with scrypt). No email address and no tracking.
- **Isolation:** every user's data lives only in `data/users/<id>/`, and `tests/test_isolation.py` proves users can't see each other's data.
- **Visible memory:** everything Lantern remembers is a Markdown file you can view, edit, delete and download as a zip.
- **Ask before acting:** Lantern never sends a message or books anything without your explicit confirmation.
- **Where your data goes:** messages go to Nebius Token Factory to generate replies; searches go to Tavily; recall lookups send only a make, model and year (or a VIN, to decode it) to NHTSA. Nothing else leaves the app.

## How the agent works

- A **lookup planner** (Nano) decides whether a message needs a lookup; the loop then runs the tool itself, so the model can't skip it and guess.
- **Every tool call goes through one checkpoint** (`ToolRegistry`) with a timeout and an audit log entry in `data/users/<id>/logs/audit.jsonl`.
- **Guardrails:** at most 6 tool calls and 40k tokens per message, and daily caps per guest, per user and site-wide, so a public demo can't run away with credits.
- **Answers stream** over server-sent events with live status lines.
- **Web content is untrusted:** the model is told never to follow instructions found in fetched pages.

## Track tools we evaluated

NVIDIA OpenShell, NemoClaw, Hermes Agent and Nebius Serverless were each checked against their official docs: see [docs/track-tools-evaluation.md](docs/track-tools-evaluation.md). We kept our own agent loop (multi-user isolation and per-fact visible memory) and borrowed Hermes's memory actions (add, replace, remove).

## License

MIT
