---
name: receipts
description: "Build the list from order emails, order-history files and photos of paper receipts, keeping only things that can be recalled."
metadata:
  order: 7
  blurb: "Adds things from your order emails, order history or a photo of a receipt."
  title: "Add from receipts"
  tools: []
  uses: ["Nemotron Super", "Nemotron Super VL (receipt photos)"]
  sends: "The pasted text (a CSV keeps only product names) to Nebius Token Factory. A receipt photo once, never stored."
  required: false
---
# Add from receipts

Nobody types an inventory of their home, but most people have order emails.

## How it works
- Files are read in the browser first: a CSV keeps only its product-name column; HTML and .eml emails only their text.
- A receipt photo's item lines (no prices or card details) go into the text box for the user to check first.
- Nemotron Super decides keep or skip for every numbered line, and each thing must be named in its own line.
- Skips food, medicine, books, gift cards and services; saves up to 25 things at once, merging with what's on the list.
