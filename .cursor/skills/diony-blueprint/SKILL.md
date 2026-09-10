---
name: diony-blueprint
description: Writes and updates Diony numbered Blueprints as operational constitution (checklists, commands, failure modes). Use when adding or revising files under blueprints/ or when product rules change.
---

# Diony Blueprint

## Instructions

1. Open `blueprints/README.md` and keep the tables in sync (cloud era 25+, foundations, legacy lane).
2. Style: short sentences, checklists, command blocks, failure-mode tables. No marketing.
3. Cross-link instead of duplicating serving flags or BOM lines.
4. If quality, SLA, or gates change, update `00` and `10` in the same PR as code.
5. Day 1 vs Week 2+ must stay explicit (dry-run vs live fabric).

## Examples

- New audio model pin: update `04` and `07`, then `infra/models/manifest.yaml`.
- Measured GB10 seconds-per-5s-clip: replace the order-of-magnitude table in `07`.
