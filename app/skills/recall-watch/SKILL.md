---
name: recall-watch
description: "Every day, check the user's cars and household products against the official U.S. recall lists and explain each recall in plain words."
metadata:
  order: 1
  blurb: "Checks your cars and things against the official U.S. recall lists every day, in plain words."
  title: "Recall watch"
  tools: ["recall_check"]
  uses: ["NHTSA (cars, child car seats, tires)", "CPSC (household products)", "Nemotron Nano", "Nemotron Super"]
  sends: "A car's make, model and year (or its VIN, to decode it) to NHTSA. The CPSC list is fetched for everyone and matched here."
  required: true
---
# Recall watch

The core of Lantern. It can't be turned off: without it there's nothing to watch.

## What it does
- Keeps the user's things as files in `memory/vehicles/` and `memory/products/`.
- Every day (Cloud Scheduler, 00:10 UTC) checks NHTSA for each car by make, model and year, NHTSA's child seat and
  tire recalls, and the CPSC's product recalls (fetched once a day for everyone, matched locally).
- Nemotron Nano confirms that a product recall really covers the user's product; Nemotron Super rewrites each notice
  in plain English, once per recall for everyone.
- Puts "Do not drive", "Park outside" and "Stop using" first.

## Rules
- Recall facts come only from the official lists, never from the model's memory.
- Car recalls are by model: never say a specific car was or wasn't repaired; point to NHTSA's VIN check.
- Only the user marks a recall fixed or "doesn't apply". The assistant has no tool for it.
