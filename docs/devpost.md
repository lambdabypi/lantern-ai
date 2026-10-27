# Devpost submission draft

**Track:** Personal AI. Also entering: Best Use of Tavily.

**Name:** Lantern

**Tagline:** A personal AI that knows what you own and watches for safety recalls.

**Links:**
- Live demo: https://lantern-976584717995.us-central1.run.app (no sign-up; tap "See a demo household")
- Code: https://github.com/lambdabypi/lantern-ai (MIT)
- Video: <add link>

## Inspiration

About one in five cars on U.S. roads has a safety recall that nobody fixed. That's 55 to 58 million cars. The repair is free, and the owner usually just never heard about it, because the car was bought second-hand or the letter went to an old address. Household products are worse. When a recall only goes out as a press release, about 6% of the products get fixed. When the owner is told directly, it's closer to 50%.

So the problem isn't the fix. It's the notice. And a personal AI that already knows what you own is the notice.

## What it does

You tell Lantern what you own, by typing, talking, or taking a photo of a label or a receipt. Every day it checks the official U.S. recall lists for all of it: NHTSA for cars, child car seats and tires, the CPSC for everything at home. Then it does four things recall apps don't.

1. **It tells you what it means, in plain words.** "Don't drive this truck until it's repaired" comes first, written by NVIDIA Nemotron from the notice itself, with the official text one tap away.
2. **It tells you if yours is the one.** A recall covers a model, and you own one unit. Take a photo of the label and Lantern compares it with the notice and quotes the line that decides it. It only ever says "not included" if it can quote the notice word for word.
3. **It warns you early.** Owners complain to NHTSA before recalls happen. Lantern reads those complaints for your car's model and flags problems no recall covers yet.
4. **It helps you get it fixed.** It reads the maker's own recall page and turns it into steps, finds authorized dealers near you, drafts the booking message (you send it, Lantern never does), and checks in until it's repaired.

You can also check something secondhand before you buy it, watch your mom's car in her name, and ask questions that get answered from the maker's own pages, with sources.

Everything it remembers is a plain Markdown file you can read, edit, delete or download. Every feature is a skill with its own SKILL.md, and you can switch any of them off.

## How we built it

- **NVIDIA Nemotron on Nebius Token Factory** for every model call, each job on the smallest model that does it well. Nano reads each message and confirms product matches. Super writes the plain-English summaries, the answers and the "is mine affected?" verdicts. Super VL reads label and receipt photos. Ultra only runs when you tick "Take more time to think".
- **Tavily** reads the makers' own pages. Search with `include_domains` finds the carmaker's recall page, Extract pulls the remedy passages, and the answers cite them. It also reads secondhand listings and finds dealers.
- **Free government data:** NHTSA's recalls, VIN decoder, complaints and recall dataset, and the CPSC recall API.
- FastAPI and one plain HTML page, no build step, installable on a phone. Hosted on Google Cloud Run. Cloud Run only serves the app; all inference runs on Token Factory.

## Challenges we ran into

Three of them, all about trust.

1. **Models guess.** An unreadable listing link came back as a "2014 Ford F-150" that nobody mentioned. So now everything the model names has to appear in your words or the page, model numbers have to match whole words, and the advice text is fixed, not generated.
2. **Asking for a list loses things.** On a receipt photo, Super left out the car seat on 3 runs of 4. Making it decide keep or skip for every single line fixed that, 4 of 4.
3. **It's a public demo on my own card.** So there's a daily dollar cap on every model call, caps on everything else, and a kill switch that takes the site offline if the hosting bill reaches $5.

## Accomplishments that we're proud of

The "is mine affected?" check. Every recall tool I found stops at "your model has a recall". Lantern reads your label and quotes the notice back to you, and it refuses to say you're safe unless the notice says so in those exact words.

## What we learned

A bigger model wasn't the fix most of the time. Better retrieval, and making the model account for every line, was.

## What's next

Food and drug recalls (FDA, USDA), barcode scanning, and getting the list built for you from more places you already have, like order history.

**Built with:** Python, FastAPI, NVIDIA Nemotron (Nano, Super, Super VL, Ultra), Nebius Token Factory, Tavily, NHTSA APIs, CPSC API, Google Cloud Run, HTML, JavaScript.

## Sponsor questions

**Which model(s) did you use, and why did you choose that size/variant?**

Four, each on the smallest job it does well.
- **Nemotron 3 Nano (30B-A3B), thinking off:** reading every message first (what do you own, is this a recall question, are you buying something) and confirming that a CPSC recall really matches your product. It's fast, about a second, and these are short, structured decisions.
- **Nemotron 3 Super (120B-A12B), thinking off:** anything a person reads. The plain-English recall summaries, chat answers written from the official data, the "is mine affected?" verdicts, and reading receipts. I moved receipts to Super after Nano kept 1 item out of 3 on the same receipt and changed its answer run to run. Super kept all 3, every time.
- **Nemotron 3.5 Super VL, thinking off:** reading photos of labels and receipts. It copies the text exactly, and then Super decides what's on it.
- **Nemotron 3 Ultra (550B-A55B):** only when someone ticks "Take more time to think". It's capped, because a normal answer doesn't need it.

**How would you rate Nemotron's output quality for your use case? (1-10)**

8. Super's plain-English summaries are honestly good, and it follows "use only what the notice says" well. It loses points on format: it sometimes wrote single-quoted JSON, and it added citation markers like 【url】 even when told not to, so I parse around both.

**Did you fine-tune, prompt-engineer, or use Nemotron out of the box? What was your approach?**

Out of the box with prompt engineering, no fine-tuning. Three things mattered more than the prompts themselves.
1. **Thinking on or off per call** (`chat_template_kwargs.enable_thinking`). Off made extraction about 12× faster (36.7 s to 3.1 s on one batch). But Nano with thinking off answered 12 × 7 = 72, reproducibly, so reasoning-heavy turns keep it on.
2. **Making the model account for everything.** Asked for a list of items on a receipt, Super left one out 3 runs in 4. Asked to decide keep or skip for every numbered line, it got all of them, 4 runs in 4.
3. **Checking its output against the source.** Every brand, model number and car it names has to appear in the person's words or the page, and "your one isn't included" needs a quote found word for word in the recall notice. After a listing link it couldn't read came back as a "2014 Ford F-150" nobody mentioned, that rule became non-negotiable.

**How did Nemotron's performance compare to other models you've used for similar tasks?**

I didn't run the same tasks on other providers' models for this project, so I won't rank it against them. Within the family, the clearest lesson was that size wasn't the fix most of the time. In an earlier version, Nano, Super and Ultra all gave the same confident wrong answer to a question, and better retrieval fixed it, not a bigger model. Where size did matter was extraction from messy text (receipts), where Super was clearly more reliable than Nano.

**Which Nebius platform capabilities were most valuable to your project and how? (GPU instances, deployment setup, API access, scaling)**

Token Factory's OpenAI-compatible API, and the fact that I didn't have to run any GPUs. Every model call goes through the standard `openai` Python SDK pointed at Token Factory, with the model IDs in environment variables, so switching a job from Nano to Super was a one-line change. No GPU instances, no deployment, no scaling to manage: Token Factory is serverless, and the web app itself is a small container on Google Cloud Run. The model list is how I found Super VL partway through and added photo reading in an afternoon. Cost has been tiny: about $0.0015 for a chat answer, and well under a dollar of logged spend through development.

**How likely are you to recommend running Nemotron on Nebius to other developers? (1-10)**

8. Going from an API key to four model sizes behind one familiar API is the main reason. Not having to think about GPUs for a hackathon is huge. Clearer docs on what each model supports would get it higher.

**How would you rate your experience running Nemotron inference on Nebius compared to previous cloud or local development environments? (1-10)**

8. Much less setup than hosting a model myself, and the OpenAI-compatible API meant no new client to learn. The friction was finding out what works by trial: `reasoning_budget` and `reasoning_effort` had no reliable effect, and thinking on Super VL used all its tokens and returned nothing.

**What additional features or improvements would have made the Nemotron on Nebius experience more effective for your project?**

Four.
1. **Spending caps per API key.** My demo is public and my card is on file, so I had to build a daily dollar cap inside the app myself.
2. **A speech-to-text model.** Voice had to use the browser's own speech service, which behaves differently in every browser.
3. **Prices and limits for every model in one place,** including Super VL and how image tokens are counted.
4. **A strict JSON output mode,** so I wouldn't have to parse around single quotes and stray markers.

**What do you most hope to see from the Nemotron team next?**

A speech model, and a small vision model. Reading a label is a small job, and Super VL is more than it needs.

**Did you use Tavily in your project?**

Yes, at runtime, in four places (`app/maker_pages.py`, `app/precheck.py`, `app/fix_helper.py`).
- **How to get the fix:** Tavily Search with `include_domains` set to the carmaker's own sites and nhtsa.gov finds the maker's recall page, then Tavily Extract pulls the passages about the remedy, and Nemotron turns them into numbered steps with the sources linked.
- **Ask about your stuff:** questions about something you own are searched only on that maker's sites (plus nhtsa.gov or cpsc.gov), and the answer cites the pages.
- **Check before you buy:** Extract reads a secondhand listing link you paste.
- **Book the free fix:** a basic search finds authorized dealers near you.
