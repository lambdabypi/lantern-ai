---
name: check-before-buying
description: "Before buying something secondhand, check it for recalls (and, for cars, owner complaints) without adding it to the list."
metadata:
  order: 8
  blurb: "Checks something secondhand for recalls before you buy it."
  title: "Check before you buy"
  tools: ["check_before_buying"]
  uses: ["Tavily Extract (listing links)", "Nemotron Nano", "NHTSA", "CPSC"]
  sends: "A listing link you paste, to Tavily. What's for sale to NHTSA or matched against the CPSC list."
  required: false
---
# Check before you buy

The user pastes a listing link or says what they're looking at ("a used 2014 Jeep Cherokee").

## How it works
- A link is read with Tavily Extract. Sites that need a login aren't fetched.
- Nemotron Nano picks out what's for sale. Only what the message or listing names counts: an unreadable link on its
  own is never guessed at, and VINs are kept only if they appear.
- Recalls are checked like the user's own things; nothing is added to their list.

## Advice (fixed text, not model output)
- Selling a recalled consumer product is illegal in the U.S.
- Used cars can be sold with open recalls: check the seller's VIN on NHTSA's site before paying.
