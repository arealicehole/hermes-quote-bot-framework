# Replicable Quote Bot Framework

> A Hermes Agent framework that turns messy customer intake into structured quotes, tracks vendor costs, and manages Stripe payments — for **any service business**.

**Live reference implementation:** [Print Junkie AZ](https://github.com/) — 195 quotes, 247 products, 99 vendor invoices, 135 payments processed through real Stripe transactions.

Built for the **Hermes Agent Accelerated Business Hackathon** (NVIDIA × Stripe × Nous Research).

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create a fresh database
python3 init_db.py

# 3. Seed demo data (landscaping business example)
python3 seed_demo.py

# 4. Run the demo (shows earn → spend loop + replicability)
python3 run_demo.py

# 5. Start the MCP server
python3 server_http.py  # → http://localhost:3207
```

## What It Does

```
Raw SMS → Intake Parser → Pricing Engine → Quote Builder → Stripe Payment Link → Payment Recorded → Vendor Invoice Tracked
```

The agent earns (customer pays via Stripe), spends (tracks vendor costs), and reports (deterministic financial summaries) — all through 62 MCP tools with safety gates on every financial write.

## Architecture

| Module | Framework Core (shared) | Client Config (per-shop) |
|--------|------------------------|--------------------------|
| Intake Parser | ✅ NLP extraction | Product keywords |
| Pricing Engine | ✅ JSON rules engine | `pricing_rules.json` |
| Product Catalog | ✅ CRUD tools | Product entries |
| Quote Builder | ✅ Line items + charges | Charge types |
| Payment Tracking | ✅ Stripe + manual | Stripe account |
| Vendor Ledger | ✅ Invoice tracking | Vendor list |
| Status Machine | ✅ State transitions | Status labels |
| Reports | ✅ Deterministic | Format prefs |

**Onboarding a new shop:** Swap `pricing_rules.json`, seed products, connect Stripe. 15 minutes, zero code changes.

See [hackathon-architecture.md](hackathon/hackathon-architecture.md) for the full architecture document.

## Earn → Spend Loop (Live Example)

Quote #187 — CnB Reptiles, 15,000 flyers:

| Phase | Amount | Tool |
|-------|--------|------|
| Earn (Stripe payment) | $724.95 | `stripe_create_payment_link_for_quote()` |
| Spend (vendor invoice) | $423.30 | `vendor_invoice_create()` |
| Net profit | $301.65 | `quote_cost_worksheet()` |
| Margin | 41.6% | `order_detail_report()` |

## Replicability

Same framework, different business — swap one JSON file:

```bash
# Print shop pricing
cp pricing_rules.example.json pricing_rules.json

# Landscaping pricing
cp examples/pricing_rules.landscaping.json pricing_rules.json
```

See [replicability-demo.md](hackathon/replicability-demo.md) for the full landscaping onboarding walkthrough.

## Stripe Setup

1. Copy `.env.example` to `.env`
2. Set `STRIPE_API_KEY=sk_test_xxx` (use test key for development)
3. The agent can create payment links and products via Stripe API

**⚠️ Never commit `.env` or `stripe_integration.py` — both are in `.gitignore`.**

## Project Structure

```
framework-template/
├── server.py                  # MCP server — 62 tools (FastMCP)
├── server_http.py             # HTTP wrapper (port 3207)
├── stripe_integration.py      # Stripe helpers (gitignored — contains key logic)
├── intake_parser.py           # NLP intake parsing
├── scopes.py                  # Customer/order/product scoping
├── init_db.py                 # Bootstrap fresh database
├── seed_demo.py               # Seed landscaping demo data
├── run_demo.py                # One-command demo script
├── requirements.txt           # Python dependencies
├── pricing_rules.example.json # Print shop pricing rules (reference)
├── examples/
│   └── pricing_rules.landscaping.json  # Landscaping pricing rules
├── .env.example               # Environment template
├── .gitignore                 # Protects secrets + DB
└── hackathon/                 # Submission materials
    ├── hackathon-architecture.md
    ├── stripe-spend-loop.md
    ├── replicability-demo.md
    ├── demo-video-script.md
    └── final-writeup.md
```

## Tech Stack

- **Agent Framework:** Hermes Agent (Nous Research)
- **MCP Protocol:** FastMCP (stdio + HTTP)
- **Database:** SQLite (WAL mode)
- **Payments:** Stripe API
- **Language:** Python 3.13+

## License

MIT — use it, fork it, deploy it for any service business.

---

*Built for the Hermes Agent Accelerated Business Hackathon. The framework is the product; Print Junkie AZ is the proof it works.*