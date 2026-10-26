---
name: owner-reports
description: "Early warning: group NHTSA's owner complaints for each car model by part and flag the problems no recall covers yet."
metadata:
  order: 3
  blurb: "Shows what other owners of your car model report to NHTSA, including problems no recall covers yet."
  title: "What other owners report"
  tools: []
  uses: ["NHTSA complaints", "Nemotron Super"]
  sends: "A car's make, model and year to NHTSA."
  required: false
---
# What other owners report

Recalls come late: owners complain to NHTSA first. For each car, this skill groups the complaints for that model by
part, counts crashes, fires, injuries and deaths, and checks whether one of the model's recalls covers each part.

## Rules
- Counts and recall coverage are computed from the data; Nemotron Super writes only a plain title and one
  "Owners report..." sentence per part.
- Always labelled as owners' reports, not verified findings, with a link to report a problem to NHTSA.
- Shared per model and refreshed weekly.
