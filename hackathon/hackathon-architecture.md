# Replicable Quote Bot Framework — Architecture Document

> **Hermes Agent Accelerated Business Hackathon** — NVIDIA × Stripe × Nous Research
> Submission: Replicable Hermes Agent framework for service-business operations

---

## Overview

This framework transforms messy customer intake (SMS, voice transcripts, notes) into structured quotes, manages revenue and payments, tracks vendor costs, and enables agent-initiated vendor spending via Stripe. It is **not** a one-off print shop tool — it is a **replicable Hermes Agent framework** that can be deployed for any service business.

**Print Junkie AZ** (a real print shop in Arizona) serves as the **live reference implementation** — proof that the framework handles real operations at production scale.

---

## Live Reference Implementation Stats

| Metric | Value |
|--------|-------|
| Quotes processed | 195 |
| Products in catalog | 247 |
| Vendor invoices tracked | 99 |
| Payments recorded | 135 |
| Customers in CRM | 59 |
| Active vendors | 36 |
| MCP tools available | 62 |
| DB size | Single SQLite file (WAL mode) |

---

## Framework Core (Immutable — Shared Across All Deployments)

These modules are the framework's backbone. They work identically regardless of business type.

### 1. Intake Parser
Converts raw text (SMS, voice transcript, notes) into structured quote data.

```
Input:  "Need 10 banners 4x8 grommets by Friday for the grand opening"
Output: {
  "customer": "the grand",
  "items": [{
    "product_name": "Banner",
    "quantity": 50,
    "variants": {"color": "Black", "dimensions": "4x8 ft", "finishing": "grommets"}
  }],
  "deadline": "by Friday",
  "special_requests": ["logo on front"]
}
```

**Tool:** `intake_parse(raw_text)` — NLP-based extraction of products, quantities, specs, deadlines, and special requests from unstructured text.

### 2. Quote Builder
Line-item-based quoting with charges, cost allocation, and status workflow.

- `quote_create(customer_id)` → Draft
- `quote_add_item(quote_id, product_id, quantity, quoted_cost, quoted_price, variants_json?)`
- `quote_add_charge(quote_id, description, amount, charge_type, notes?)` — rush, shipping, discount, internal
- `quote_verify_totals(quote_id)` — pre-send math verification
- `quote_cost_worksheet(quote_id)` — billable vs internal cost separation
- `quote_allocate_total(quote_id, target_customer_total)` — deterministic allocation

### 3. Payment Tracking
Records payments, tracks outstanding balances, generates customer-facing summaries.

- `payment_record(quote_id, amount, method?, payment_date?, notes?)`
- `payment_outstanding(customer_id)` — outstanding balances only
- `quote_payment_summary(quote_id)` — deterministic financial snapshot

### 4. Vendor Cost Ledger
Tracks vendor invoices tied to quotes, enabling cost-basis analysis and vendor spend.

- `vendor_create(name, contact_info?)`
- `vendor_invoice_create(vendor_id, amount, quote_id?, invoice_number?)`
- `vendor_invoice_update(invoice_id, ...)`
- `vendor_order_capture_from_receipt(raw_text, vendor_name?, quote_id?)` — normalize receipt text

### 5. Status State Machine
Configurable status pipeline with enforced transitions.

**Default pipeline:**
```
Draft → Sent → Approved → In Progress → Completed
                    ↘ Declined (terminal)
         ↘ Declined (terminal)
```

`quote_update_status(quote_id, new_status)` enforces valid transitions. `Completed` and `Declined` are terminal states.

### 6. Stripe Spend Router
Agent-initiated financial actions via Stripe.

- `stripe_health_check()` — verify config
- `stripe_create_product_for_quote(quote_id)` — create Stripe product from quote
- `stripe_create_payment_link_for_quote(quote_id)` — generate payment link from verified balance
- `stripe_record_payment_link_metadata(quote_id, payment_link_url, ...)` — append metadata

### 7. Agent Memory System
Persistent memory scoped to customers, orders, and products.

- `memory_store(scope_type, scope_id, memory_type, content, source)`
- `memory_recall_customer(customer_id)` — customer + global + behavioral memories
- `scope_customer(customer_id)` / `scope_order(quote_id)` / `scope_product(product_id)` — layered scope

### 8. Reporting Engine
Deterministic operational reports.

- `open_orders_report()` — grouped by status
- `production_queue_report()` — approved/in-progress
- `unpaid_orders_report()` — balance due
- `customer_history_report(customer_id)`
- `order_detail_report(quote_id)`

---

## Client Configuration (Per-Shop — Customizable)

These modules are configured per business deployment.

### 1. Product Catalog
Each shop defines its own products with cost and list price.

```python
product_add(name="Banner 4x8 Vinyl", description="Standard vinyl banner", standard_cost=8.72, list_price=26.16)
```

**Reference:** Print Junkie AZ has 247 products (apparel, banners, business cards, flyers, labels, signs, etc.)

### 2. Pricing Rules (`pricing_rules.json`)
JSON-based deterministic pricing engine. Each shop defines its own rules.

```json
{
  "banner": {
    "base_sqft_price": 3.27,
    "base_sqft_cost": 1.09,
    "minimum_price": 45.0,
    "pricing_tiers": {
      "under_20sqft": {"multiplier": 3.0},
      "20_to_50sqft": {"multiplier": 2.5},
      "over_50sqft": {"multiplier": 2.0}
    }
  }
}
```

**Tool:** `pricing_rules_preview(product_type, quantity, options_json?)` — deterministic price estimate from rules.

### 3. Vendor List
Each shop configures its own suppliers.

```python
vendor_create(name="B2Sign", contact_info="orders@b2sign.com")
```

### 4. Status Pipeline Labels
Status labels can be customized per business type while keeping the same state machine.

| Print Shop | Landscaping | Cafe Catering |
|------------|-------------|---------------|
| Draft | Draft | Draft |
| Sent | Sent | Sent |
| Approved | Approved | Approved |
| In Progress | Scheduled | Prep |
| In Production | On Site | Ready |
| Completed | Completed | Completed |

### 5. CRM Fields
Customer records are extensible — name, email, phone, address, contact_info.

### 6. Memory Templates
Agent memory stores business-specific knowledge: vendor pricing rules, workflow conventions, product mappings, operational notes.

---

## Onboarding a New Shop (5 Steps)

### Step 1: Connect Database & MCP
```bash
export QUOTES_DB_PATH="/path/to/new-shop/quotes.db"
python3 server_http.py  # starts MCP on :3207
```

### Step 2: Load Pricing Rules
Create `pricing_rules.json` with the new shop's product types and pricing logic.

### Step 3: Seed Products & Vendors
```python
product_add(name="Native Tree Planting", description="15-gallon tree, labor included", standard_cost=45.0, list_price=120.0)
vendor_create(name="Desert Nursery", contact_info="orders@desertnursery.com")
```

### Step 4: Connect Stripe
Set `STRIPE_API_KEY` in the shop's `.env` file. The agent uses this for payment links and vendor payouts.

### Step 5: Configure Status Pipeline & Go Live
Map status labels to the state machine. The agent is now ready to accept intake, generate quotes, process payments, and track vendor costs.

**Time to onboard a new shop: ~15 minutes** (create DB, edit JSON, seed products, connect Stripe).

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    Hermes Agent                          │
│  (Telegram / Discord / Voice → intake → quote → payment) │
└──────────────────────┬──────────────────────────────────┘
                       │ MCP Protocol (stdio/HTTP :3207)
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Quote Bot MCP Server                        │
│  62 tools across 8 domains                               │
│  ┌─────────┬─────────┬──────────┬──────────┬──────────┐ │
│  │ Intake  │ Quote   │ Payment  │ Vendor   │ Stripe   │ │
│  │ Parser  │ Builder │ Tracking │ Ledger   │ Router   │ │
│  ├─────────┼─────────┼──────────┼──────────┼──────────┤ │
│  │ Product │ Pricing │ Status   │ Memory   │ Reports  │ │
│  │ Catalog │ Engine  │ Machine  │ System   │ Engine   │ │
│  └─────────┴─────────┴──────────┴──────────┴──────────┘ │
└──────────────────────┬──────────────────────────────────┘
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
    ┌──────────┐ ┌──────────┐ ┌──────────────┐
    │ SQLite   │ │ pricing_ │ │ Stripe API   │
    │ quotes.db│ │ rules.json│ │ (live/test)  │
    │ (WAL)    │ │ (per-shop)│ │              │
    └──────────┘ └──────────┘ └──────────────┘
```

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Agent Framework | Hermes Agent (Nous Research) |
| MCP Protocol | FastMCP (stdio + HTTP on :3207) |
| Database | SQLite (WAL mode, busy_timeout=5000) |
| Pricing Engine | JSON rules file + deterministic preview |
| Payments | Stripe API (payment links, products) |
| User Interface | Telegram (operator-facing) |
| Language | Python 3.13+ |

---

## Why This Framework Wins

1. **Real Business Proof** — 195 quotes, $X revenue processed, live vendor invoices. Not a demo.
2. **Replicable** — 5-step onboarding, JSON config, any service business.
3. **Agent Earns & Spends** — Full earn→spend loop: quote → payment → vendor invoice → Stripe payout.
4. **Deterministic** — All financial tools are rule-based, not LLM-guessed. Safety gates on writes.
5. **Open Architecture** — SQLite, MCP, standard Python. No proprietary lock-in.