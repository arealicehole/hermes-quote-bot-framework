# Hermes Agent Accelerated Business Hackathon — Final Submission Writeup

---

## Submission Writeup (~180 words)

We built a **replicable Hermes Agent framework for service businesses** — not a one-off tool, but a deployable system that turns messy customer intake into structured quotes, tracks vendor costs, and manages real Stripe payments.

**Live reference implementation:** Print Junkie AZ, a real print shop in Arizona. The agent has processed 195 quotes across 247 products, tracked 99 vendor invoices, and recorded 135 payments — all through deterministic MCP tools with safety gates on every financial write.

**The earn → spend loop:** A customer sends a raw SMS. The agent parses it, prices it from JSON rules, builds a quote, generates a Stripe payment link, records the payment, and tracks the vendor invoice. One real example: Quote #187 — 15,000 flyers for CnB Reptiles. The agent earned $724.95 via Stripe, tracked $423.30 in vendor costs to 4Over, and netted $301.65 at 41.6% margin. The full loop ran autonomously.

**Replicability:** Swap the pricing rules JSON. Seed new products. Connect Stripe. Any service business — landscaping, catering, repair — goes live in 15 minutes. Zero code changes. The framework is the product; Print Junkie AZ is the proof it works.

---

## X Post Text

```
Entered the @NousResearch Agent Accelerated Hackathon with a Replicable Quote Bot Framework for Service Businesses.

Live at Print Junkie AZ: 195 quotes, 247 products, real Stripe payments.
One earn→spend loop: $724.95 earned → $423.30 vendor cost tracked → $301.65 netted.

Swap pricing rules. Seed products. Any service business goes live in 15 minutes.

Code: https://github.com/arealicehole/hermes-quote-bot-framework
Demo: [link to video on X]
#HermesAgent #BuildAgents
```

---

## Submission Checklist

- [ ] Record 1–3 min demo video using `hackathon/demo-video-script.md`
- [ ] Upload video to X/Twitter
- [ ] Post X text above tagging @NousResearch
- [ ] Drop X post link in Nous Discord #submissions channel
- [ ] Fill out Typeform (link from Nous Discord)
- [ ] All materials saved in `/home/ice/quote-bot-mcp/hackathon/`

---

## Deliverables Index

| File | Description |
|------|-------------|
| `hackathon-architecture.md` | Full architecture document — framework core vs client config, onboarding steps |
| `stripe-spend-loop.md` | Earn → spend loop documentation with live Quote #187 case study |
| `replicability-demo.md` | Non-print business onboarding demo (Desert Landscaping Co.) |
| `demo-video-script.md` | 2-minute video script with 9 shots, recording instructions |
| `final-writeup.md` | This file — submission writeup, X post, checklist |

---

## What the Human Needs to Do Now

The agent has produced all written deliverables. The remaining steps require human action:

1. **Record the demo video** — follow `demo-video-script.md`. Use pre-staged terminal output (no live Stripe calls). Slide-deck alternative available if screen recording is too complex.
2. **Upload video to X** — post the video with the X post text above (tags @NousResearch).
3. **Drop the tweet link** in the Nous Discord submissions channel.
4. **Fill the Typeform** — link is in the Nous Discord.
5. **Done.** All written materials are in `/home/ice/quote-bot-mcp/hackathon/`.