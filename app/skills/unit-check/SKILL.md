---
name: unit-check
description: "Work out whether the user's own unit is covered by a recall, from its label (typed or photographed) or NHTSA's VIN check, quoting the notice."
metadata:
  order: 2
  blurb: "Tells you whether YOUR one is in a recall, from its label (typed or photographed) or NHTSA's VIN check."
  title: "Is mine affected?"
  tools: []
  uses: ["Nemotron Super", "Nemotron Super VL (label photos)"]
  sends: "The official notice and what your label says to Nebius Token Factory. A label photo is sent once and never stored."
  required: false
---
# Is mine affected?

Recalls cover models; people own one unit. This skill turns "possible match" into a verdict.

## How it works
1. Nemotron Super reads the notice once (shared) and says where to look: "Model BYL001 on a label inside the cover".
2. The user types what their label says, or photographs it (Nemotron Super VL reads it; the photo isn't kept).
3. Nemotron Super answers *affected*, *not affected* or *unsure*, quoting the sentence of the notice that decides it.
4. Cars: NHTSA's VIN check is the only authority, so the user is walked through it and their answer is recorded.

## Safety rules
- "Not affected" needs a quote that appears word for word in the notice; otherwise the answer is "unsure".
- A listed model number or UPC found in what the user gave decides "affected" without the model.
- The verdict is saved in the item's file (`unit_check`); the user still closes the recall themselves.
