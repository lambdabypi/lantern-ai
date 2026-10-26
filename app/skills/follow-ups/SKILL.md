---
name: follow-ups
description: "Check in by push until a recall is fixed: an urgent recall with nothing booked, no reply after contacting them, and 'was it repaired?' after booking."
metadata:
  order: 6
  blurb: "Checks in by notification until a recall is actually fixed."
  title: "Follow-ups until it's fixed"
  tools: []
  uses: ["Browser push notifications"]
  sends: "A short title and line to your browser's push service, never your list of things."
  required: false
---
# Follow-ups until it's fixed

Announcing a recall is where most tools stop. The user records progress (contacted, booked, fixed); the daily watch
checks in, at most once a day and the same reminder at most once a week:
- an urgent recall with nothing booked after 3 days,
- no booking 7 days after contacting them,
- "Was it repaired?" 14 days after booking.

Only sent if the user turned on alerts on a device. Marking a recall fixed ends the check-ins.
