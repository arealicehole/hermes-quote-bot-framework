# Demo Video Script & Shot List

> **Target length:** 1:45–2:15
> **Format:** Screen recording with voiceover narration
> **Recording tool:** OBS Studio or screenrecorder (any)
> **Note:** Human operator records these steps. Do NOT attempt live tool calls during recording — pre-stage terminal output or use cursor overlays on screenshots.

---

## Pre-Recording Setup

1. Open two terminal windows side by side:
   - **Left:** Telegram chat with the Print Junkie bot (or terminal showing the agent session)
   - **Right:** Python REPL with `import server` loaded (for showing tool output)
2. Open `pricing_rules.json` in a text editor (minimized, ready to show)
3. Have these files open in browser tabs:
   - `hackathon-architecture.md`
   - `replicability-demo.md` (landscaping pricing rules section)
4. Have the Stripe payment link for Quote #187 ready: `https://buy.stripe.com/eVq4gy9z9cyO42M9gOc7u1F`

---

## Script

### ACT 1: The Problem (0:00 – 0:25)

**SHOT 1** (0:00–0:10)
- **Visual:** Screen shows a messy SMS screenshot or text block:
  ```
  "hey need 10 banners 4x8 grommets by Friday for the grand opening. 
   also 50 black tees XL with logo on front. what u charge?"
  ```
- **Narration:** "Service businesses run on messy intake. SMS, voice transcripts, scribbled notes. Every shop owner knows this chaos."

**SHOT 2** (0:10–0:25)
- **Visual:** Zoom out to show the architecture diagram (`hackathon-architecture.md`)
- **Narration:** "We built a replicable Hermes Agent framework that turns that chaos into structured quotes, tracks vendor costs, and manages Stripe payments. It's running live right now at Print Junkie AZ — 195 quotes processed, 247 products, real money moving through Stripe."

---

### ACT 2: The Earn → Spend Loop (0:25 – 1:15)

**SHOT 3** (0:25–0:40) — INTAKE PARSE
- **Visual:** Terminal showing:
  ```python
  >>> server.intake_parse("Need 10 banners 4x8 grommets by Friday for the grand opening")
  {"status": "ready", "customer": "the grand", "items": [{"product_name": "Banner", 
   "quantity": 50, "variants": {"dimensions": "4x8 ft", "finishing": "grommets"}}], 
   "deadline": "by Friday"}
  ```
- **Narration:** "The agent parses raw intake into structured data — product, quantity, specs, deadline. No manual data entry."

**SHOT 4** (0:40–0:55) — QUOTE BUILD + PRICING
- **Visual:** Terminal showing:
  ```python
  >>> server.pricing_rules_preview("banner", 10, '{"width": 4, "height": 8, "grommets": true}')
  {"unit_price": 8.27, "extended_price": 82.70, "rules_version": 5}
  ```
- **Narration:** "Pricing comes from deterministic JSON rules — not LLM guessing. The agent builds a quote with verified math."

**SHOT 5** (0:55–1:05) — STRIPE PAYMENT (EARN)
- **Visual:** Show the real Stripe payment link from Quote #187:
  ```
  Quote #187 — CnB Reptiles
  Stripe Payment Link: https://buy.stripe.com/eVq4gy9z9cyO42M9gOc7u1F
  Amount: $724.95
  Status: Paid ✓
  ```
- **Narration:** "The agent creates a Stripe payment link and sends it to the customer. Customer pays $724.95. The agent records the payment automatically."

**SHOT 6** (1:05–1:15) — VENDOR COST (SPEND)
- **Visual:** Terminal showing:
  ```python
  >>> server.order_detail_report(187)
  Quote #187 | CnB Reptiles | Completed
  Revenue: $724.95 | Vendor Cost: $423.30 | Profit: $301.65 | Margin: 41.6%
  Vendor: 4Over | Invoice: $423.30 | Status: Recorded
  Payment: $724.95 (Payment #149) | Balance: $0.00
  ```
- **Narration:** "The agent tracks the vendor cost — $423.30 to 4Over. The earn → spend loop is closed: earned $724.95, spent $423.30, netted $301.65. All deterministic, all auditable."

---

### ACT 3: Replicability (1:15 – 1:50)

**SHOT 7** (1:15–1:30) — PRICING RULES SWAP
- **Visual:** Split screen — left shows `pricing_rules.json` (print shop: banners, apparel, brochures), right shows the landscaping version (tree-planting, mulch-delivery, irrigation-repair)
- **Narration:** "This is not a print shop tool. Swap the JSON pricing rules, seed new products, and the same framework runs a landscaping company. Zero code changes."

**SHOT 8** (1:30–1:45) — NEW BUSINESS ONBOARDING
- **Visual:** Terminal showing the 5-step onboarding:
  ```python
  # Step 1: Fresh DB
  # Step 2: Load landscaping pricing_rules.json
  # Step 3: Seed products
  >>> server.product_add(name="15-Gallon Olive Tree", standard_cost=45.0, list_price=120.0)
  {"id": 1, "status": "created"}
  # Step 4: Connect Stripe
  # Step 5: Go live — agent handles first intake
  >>> server.intake_parse("Need 8 olive trees planted by Thursday")
  {"status": "ready", "items": [{"product_name": "olive tree", "quantity": 8}]}
  ```
- **Narration:** "Five steps. Fifteen minutes. Any service business. The framework is the product — Print Junkie AZ is just the proof it works."

---

### ACT 4: Close (1:50 – 2:00)

**SHOT 9** (1:50–2:00)
- **Visual:** Full screen text overlay:
  ```
  Replicable Hermes Agent Framework for Service Businesses
  Live at Print Junkie AZ — 195 quotes, real Stripe payments
  
  Earn → Spend → Track → Repeat
  
  Built with Hermes Agent × Stripe × SQLite
  @NousResearch #HermesHackathon
  ```
- **Narration:** "Agents that earn, spend, and run real operations. That's the framework. Built on Hermes Agent, powered by Stripe, running live today."

---

## Shot List Summary

| Shot | Time | What's on Screen | Key Tool/Concept |
|------|------|-----------------|------------------|
| 1 | 0:00–0:10 | Messy SMS text | The problem |
| 2 | 0:10–0:25 | Architecture diagram | Framework overview |
| 3 | 0:25–0:40 | `intake_parse()` output | Intake parser |
| 4 | 0:40–0:55 | `pricing_rules_preview()` output | Deterministic pricing |
| 5 | 0:55–1:05 | Stripe payment link (Quote #187) | Earn (Stripe) |
| 6 | 1:05–1:15 | `order_detail_report(187)` output | Spend (vendor cost) |
| 7 | 1:15–1:30 | Split: print vs landscaping JSON | Replicability |
| 8 | 1:30–1:45 | Onboarding steps terminal | 5-step deployment |
| 9 | 1:50–2:00 | Closing text overlay | Call to action |

---

## Recording Instructions

1. **Pre-stage all terminal output** — run the commands beforehand, copy results into a script file, and use `cat` or a text editor to display them during recording. This avoids latency and accidental writes.
2. **Record at 1080p minimum** — text must be readable on mobile (most X viewers are on phone).
3. **Voiceover:** Record narration separately in a quiet room. Sync to screen recording in editing.
4. **No live Stripe calls** — all Stripe content shown is from historical Quote #187. Do not create new payment links during recording.
5. **Export as MP4, 16:9 or 1:1** — X supports both. Keep under 3 minutes.
6. **Upload to X** directly as video post (not a link to YouTube).

---

## Alternative: Slide-Based Demo

If screen recording is too complex, create a slide deck (Google Slides / Keynote) with:
- Slide 1: Problem (messy intake)
- Slide 2: Architecture diagram
- Slide 3: Intake parse before/after
- Slide 4: Pricing rules preview
- Slide 5: Stripe payment link (real screenshot)
- Slide 6: Order detail report (real data)
- Slide 7: Print vs landscaping pricing rules side by side
- Slide 8: 5-step onboarding
- Slide 9: Closing

Record screen while narrating through slides. Same script, different visual format.