---
name: book-the-fix
description: "Find authorized dealers near the user and draft the booking message (car, VIN, every recall number) for the user to send themselves."
metadata:
  order: 5
  blurb: "Finds authorized dealers near you and drafts the booking message for you to send."
  title: "Book the free fix"
  tools: []
  uses: ["Tavily Search", "Google Maps link"]
  sends: "The car's make and the city or ZIP you type, to Tavily."
  required: false
---
# Book the free fix

Recall repairs are free only at an authorized dealer of the brand. This skill finds dealers near the user (one
Tavily search, plus a Google Maps link) and drafts the booking message.

## Rules
- Lantern never sends anything. The message opens in the user's own email, text or WhatsApp app.
