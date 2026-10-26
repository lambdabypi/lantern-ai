# Lantern

**A personal AI that knows what you own and watches for safety recalls.** Tell Lantern about your car, car seat, crib or space heater. Every day it checks the official U.S. recall lists, tells you in plain words what's wrong and how serious it is, and helps you get the free fix. Everything it remembers is a plain file you can see, edit, delete and download.

Built for the **Nebius x NVIDIA Global AI Hackathon**. All AI runs on **NVIDIA Nemotron** models through **Nebius Token Factory**.

**Try it:** https://lantern-976584717995.us-central1.run.app. Tap **See a demo household** (made-up people, real recall data), or type your own car. No sign-up. On a phone, add it to your Home Screen to use it like an app.

> Built in public. Lantern pivoted from a gaming assistant to a recall watch on 24 Oct 2026; the earlier code is in the git history. Backlog: [GitHub Project](https://github.com/users/lambdabypi/projects/12).

## Why

- **About 1 in 5 vehicles on U.S. roads (55–58 million) has a safety recall that hasn't been fixed** ([CARFAX, via ConsumerAffairs](https://www.consumeraffairs.com/news/how-safe-is-your-car-one-in-five-have-an-open-recall-020525.html)). The repair is free, but the owner often never heard about it: the car was bought second-hand, or the letter went to an old address.
- **For household products, owner response to recalls averages under 10%.** When a recall is only announced in a press release, about **6%** of products get fixed; when owners are told directly, about **50%** ([CPSC figures, via Registria](https://www.registria.com/2018/08/31/registria-cpsc-request-responses/)).

Telling owners directly makes them roughly 8× more likely to act. A personal AI that knows what you own *is* that direct notice.

## What it does

| | Feature |
|---|---|
| 1 | **Your things, as visible memory.** Cars (by VIN, or year, make and model) and household products, one Markdown file each. Say or type everything at once in the **What do you own?** box ("our 2018 Civic, a Graco stroller and a crib"): Nemotron Nano picks out each car and product. Also saved from chat, or added one at a time in the app. |
| 2 | **Build the list from receipts.** Nobody types an inventory of their home, but most people have order emails. In My stuff, **Add from receipts or order emails**: paste order confirmations, upload an order-history file (a CSV keeps only its product-name column; HTML and .eml emails keep only their text, all in your browser), or **take a photo of a paper receipt** (read by Nemotron Super VL, which copies the item lines and skips prices and card details). Nemotron Super picks out the things that can be recalled (baby gear, toys, appliances, heaters, chargers, car seats…) and skips food, medicine, books and gift cards; up to 25 at once. A brand, model number or product the text never mentions is dropped. |
| 3 | **Watch things for someone you look after.** "My mom drives a 2012 Toyota Camry", typed or said, or "Whose is it?" in the add forms. It gets its own file with `owner: Mom`, and Lantern says "Mom's 2012 Toyota Camry" everywhere: on Home, in alerts and check-ins, and in chat ("Is my mom's car safe to drive?"). **Tell Mom** shares the recall as a message written to her ("Hi Mom, Lantern found a safety recall that may affect your 2012 Toyota Camry…"). The person who will act on a recall is the one who hears about it. The demo household's Tucson belongs to Mom. |
| 4 | **Daily recall watch.** Every day Lantern checks NHTSA (vehicles, child car seats and tires) and the CPSC (household products) for everything you own. **Home** shows what needs attention in plain words: "Don't drive the 2006 Ford Ranger until it's repaired" comes first. Mark each recall *fixed* or *not mine*. |
| 5 | **Is mine affected?** A recall covers *models*; you own one *unit*. Tap **Is mine affected?** and Lantern says where to look (from the notice: "Model BYL001 on a label inside the mattress cover"). Type what your label says, or **take a photo of it**: NVIDIA Nemotron Super VL reads the label, and Nemotron Super compares it with the official notice and answers *yours is included*, *yours doesn't look included* or *can't tell*, quoting the sentence of the notice that decides it. "Not included" is only ever said with a quote that appears word for word in the notice. For cars, it walks you through NHTSA's own VIN check (with your VIN ready to copy) and records what it showed. The result is saved in the item's file; you still close the recall yourself. |
| 6 | **What other owners report (early warning).** Recalls come late: owners complain to NHTSA first, and NHTSA opens investigations from those complaints. For each car, Lantern reads NHTSA's complaint database for that model, groups the complaints by part, and flags the ones **no recall covers**: "2019 Hyundai Tucson: engine stalls or loses power. 201 complaints, 43 in the last year, 8 fires. No recall for this." Counts, crashes, fires, injuries and whether a recall covers it are computed from the data; Nemotron Super writes the plain title and one sentence from the latest reports. Labelled as owners' reports, not verified findings, with a link to report a problem to NHTSA. |
| 7 | **Check before you buy.** Buying secondhand? Paste the listing link or say what it is ("a used 2014 Jeep Cherokee", "a Snuga swing from a yard sale"), in the Home box or in chat. Lantern reads the listing with Tavily, Nemotron Nano picks out what's for sale (only what the message or listing names: an unreadable link alone is never guessed at), and it gets the same recall check and owner-complaint summary, **without being added to your list**. The advice is fixed text: in the U.S. it's illegal to sell a recalled product; used cars can legally be sold with open recalls, so check the seller's VIN before you pay. "I bought it: watch it" adds it in one tap. |
| 8 | **Alerts on your phone or computer.** Turn on notifications and Lantern tells you when a new recall affects your things. Standard browser push: no phone number, no app store. Appearance follows your device (light or dark), or pick one in the menu. |
| 9 | **Follow-through until it's fixed.** Announcing a recall is where most recall tools stop, and most recalls then stay unrepaired. On each recall, tap **I've contacted them** or **Repair booked** (or "I've contacted them" after sending the booking message). The daily watch then checks in by push, at most once a day and the same reminder at most once a week: an urgent recall with nothing booked after 3 days ("The fix is free: tap to see how to get it"), no reply 7 days after contacting them, and "Was it repaired?" 14 days after booking. Progress is saved in the item's file (`fix_progress: booked 2026-10-26`); only you record it, and marking a recall fixed ends the check-ins. |
| 10 | **Book the free fix.** Recall repairs are free at any authorized dealer for your car's brand. Lantern finds dealers near you and drafts the booking message (car, VIN, every recall number, "please confirm the parts are in stock"). **You send it yourself** by email, text or WhatsApp; Lantern never sends anything for you. |
| 11 | **Ask, by typing or talking.** "Is my car safe to drive?" Answers come only from the official recall data, with links. "Where is the VIN on my Ranger?" or "How do I fold the stroller?" is answered from the maker's own website (and nhtsa.gov / cpsc.gov), with links to the pages used. If that thing has an urgent recall, the answer says so first. Tap the mic to talk, and turn on "Read answers aloud". |
| 12 | **How to actually get the fix.** "Show me how to get the fix" on any recall reads the maker's own recall page and turns it into numbered steps (where to register, what they send, what to do meanwhile), with the source linked. Notices say a remedy exists; this says how. |
| 13 | **Plain English, with the official text one tap away.** NVIDIA Nemotron rewrites each recall notice in everyday words ("Driver air bag may explode", "Park outside and away from buildings until the free repair is done"), once per recall for everyone, using only what the notice says. |
| 14 | **Learn.** Short, sourced answers: what a safety recall is, whether the fix is free (and the 15-year rule), what "Do not drive", "Park outside" and "Stop using" mean, where to find your VIN, what happens to your data. |

**What Lantern can't know:** NHTSA lists recalls by make, model and year. Whether *your* car's recall has already been repaired is only shown on [nhtsa.gov/recalls](https://www.nhtsa.gov/recalls) by VIN, which has no public API. Lantern says "your model is covered by this recall", links that official check, and never claims a specific car is fixed or unfixed.

## Data sources

All free and public, with no API key needed (checked live on 24 Oct 2026):

| Source | Used for |
|---|---|
| NHTSA Recalls API (`api.nhtsa.gov/recalls/recallsByVehicle`) | Vehicle recalls by make, model and year, including "park it" and "park outside" flags |
| NHTSA vPIC (`vpic.nhtsa.dot.gov/api`) | Decoding a VIN into make, model and year |
| NHTSA Complaints API (`api.nhtsa.gov/complaints/complaintsByVehicle`) | Owners' safety complaints by make, model and year (with crash, fire, injury and death flags), for "What other owners report". Shared per model and refreshed weekly |
| NHTSA recall dataset (`data.transportation.gov/resource/6axg-epim.json`) | **Child car seat and tire recalls** (NHTSA equipment recalls aren't in the vehicle API): the last 10 years, fetched once a day for everyone and matched like household products. Includes NHTSA's completion rate ("24% of these have been fixed so far") |
| CPSC Recalls API (`saferproducts.gov/RestWebServices/Recall`) | Household product recalls. Lantern fetches the recent list once a day for everyone and matches it against each person's things locally |
| **Tavily** | Reading the makers' own pages (see below), and finding authorized dealers near the user for "Book the free fix" |

### How Lantern uses Tavily

Official recall notices say *that* there is a free remedy. The maker's own pages say *how* to get it, and answer everyday questions about the things you own. Lantern reads those pages with Tavily (`app/maker_pages.py`):

- **How to get the fix.** For household products, the CPSC notice names the maker's recall page in its contact details (1,272 of 1,289 notices since 2023 had a web address). Lantern reads it, plus the CPSC's own page, with **Tavily Extract** (a query about the remedy, the 3 most relevant passages per page). For cars, NHTSA gives no link, so a **Tavily Search restricted with `include_domains`** to the carmaker's sites and nhtsa.gov (which hosts the owner letters) finds the page first. Nemotron Super then writes numbered steps using only what the pages say, with the sources linked. Each recall's steps are shared by everyone and kept for 14 days.
- **Ask about your stuff.** A question about one of your things is searched only on that maker's sites plus nhtsa.gov or cpsc.gov (`include_domains`), then the best two pages are read with Extract, and the answer cites them. A product's maker site is found once with one search and saved in that product's file as `sources`, where you can see and change it. If the maker's sites have nothing, Lantern searches the wider web and says so.
- **Dealers.** One basic search finds authorized dealers near the city or ZIP you type.
- **Listings.** "Check before you buy" reads a listing link you paste with Tavily Extract (one page). Sites that need a login (Facebook Marketplace, Nextdoor) are not fetched; Lantern asks you to type what's for sale instead.
- **Budget.** About 3 credits for a new remedy or question (search 1, advanced extract 2); answers are cached, and the whole site has a daily Tavily credit cap. Web pages are treated as untrusted data: the prompts tell the model never to follow instructions inside them.

## How we use NVIDIA Nemotron and Nebius Token Factory

Every LLM call goes to the **Nebius Token Factory** OpenAI-compatible API. Lantern routes each job to the smallest model that does it well:

| Model | Used for |
|---|---|
| Nemotron 3 Nano (`MODEL_FAST`), thinking off | Reading each message first: pulling out the things you say you own and deciding whether to check recalls; confirming that a CPSC recall really matches a product you own; small talk |
| Nemotron 3 Super (`MODEL_BALANCED`), thinking off | Writing answers from the official recall data it was given; rewriting each recall notice in plain English (once per recall, shared by everyone, cached); for "Is mine affected?", working out from each notice how to tell recalled units apart (shared per recall) and comparing your label with it; a plain title and sentence for each group of owner complaints (shared per model, weekly); picking out the recallable things in pasted receipts and order emails |
| Nemotron 3 Ultra (`MODEL_REASONING`) | "Think harder" only |
| Nemotron 3.5 Super VL (`MODEL_VISION`), thinking off | Reading the photos you take: a product label for "Is mine affected?" (model numbers, dates, lot codes), or a paper receipt for "Add from receipts" (item lines only, no prices or card details). Photos are sent once and never stored or cached |

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

Turn on push notifications locally by generating a key: `python -m app.push`, then put the output in `.env` as `VAPID_PRIVATE_KEY`.

Deployment (Cloud Run, Cloud Storage for user data, Secret Manager, a daily Cloud Scheduler job): see [deploy/README.md](deploy/README.md).

## Accounts and privacy

- **Try it as a guest:** one click, no sign-up. Each guest gets their own isolated space, deleted automatically after 7 days, unless they tap **Keep my list** to turn it into a free account (username and password only).
- **Accounts:** username and password (hashed with scrypt). No email address and no tracking.
- **Isolation:** every user's data lives only in `data/users/<id>/`, and `tests/test_isolation.py` proves users can't see each other's data.
- **Visible memory:** everything Lantern remembers is a Markdown file you can view, edit, delete and download as a zip.
- **Ask before acting:** Lantern never sends a message or books anything. Booking messages open in your own email, text or WhatsApp app for you to send.
- **Only you mark recalls fixed:** the assistant has no tool for it, so a web page or a confused model can't hide a recall from you.
- **Where your data goes:** messages go to Nebius Token Factory to generate replies; searches go to Tavily (for dealers, only your car's make and the city or ZIP you type; for maker pages, the thing's name and your question, never your VIN; for "check before you buy", the listing link you paste); recall lookups send only a make, model and year (or a VIN, to decode it) to NHTSA. VINs are never put in model prompts.
- **Voice** uses your browser's built-in speech recognition and speech synthesis. In Chrome, speech recognition is done by Google's servers; Lantern says so the first time you tap the mic. Edge uses Microsoft's online speech service (Windows can switch it off: Settings › Privacy & security › Speech); Brave and Firefox have no voice typing. Lantern names the problem when voice can't start. Typing works everywhere.
- **Receipts and order emails** you paste or upload are read in your browser first (a CSV keeps only product names), then sent to Nebius Token Factory once to pick out the products; only the products are saved, as files in your list.
- **Label photos** (for "Is mine affected?") are shrunk in your browser, sent once to Nebius Token Factory for Nemotron Super VL to read, and never stored. What it read is shown to you, and saved in that item's file only as a line of text with the verdict.
- **Push notifications** go through your browser's push service (Google, Mozilla, Apple or Microsoft). Lantern only sends a short title and line, never your list of things.

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

The fonts in `app/web/fonts/` (Geist and Instrument Serif) are under the SIL Open Font License 1.1; their licence files are next to them. Lantern serves them itself, so no third-party font service is contacted.
