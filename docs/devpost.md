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
