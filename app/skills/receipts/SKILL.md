---
name: receipts
description: "Build the list from order emails, order-history files, photos of paper receipts and photos of a product's label or box, keeping only things that can be recalled."
metadata:
  order: 7
  blurb: "Adds things from your order emails, order history, or a photo of a receipt or a label."
  title: "Add from receipts and photos"
  tools: []
  uses: ["Nemotron Super", "Nemotron Super VL (photos)"]
  sends: "The pasted text (a CSV keeps only product names) to Nebius Token Factory. A photo once, never stored."
  required: false
---
# Add from receipts and photos

Nobody types an inventory of their home, but most people have order emails.

## How it works
- Files are read in the browser first: a CSV keeps only its product-name column; HTML and .eml emails only their text.
- A receipt photo's item lines (no prices or card details) go into the text box for the user to check first.
- The camera button in "What do you own?" reads a photo of the product, its label or box, or a VIN plate into the
  box (brand, kind of product, model number as printed); nothing is added until the user checks it and sends it.
- Nemotron Super decides keep or skip for every numbered line, and each thing must be named in its own line.
- Skips food, medicine, books, gift cards and services; saves up to 25 things at once, merging with what's on the list.
