# Replicability Demo: Onboarding a Non-Print Business

> **Task 3 — Proving the framework works for any service business**
> Live demo: "Desert Landscaping Co." — a completely different industry, same framework

---

## Why This Matters

Judges will ask: "Is this just a print shop tool?" The answer is no. The framework's core logic (intake parsing, quote building, payment tracking, vendor cost ledger, Stripe integration, status state machine) is **business-agnostic**. Only the configuration changes.

---

## Demo: Onboarding "Desert Landscaping Co." in 5 Steps

### Step 1: Fresh Database (Zero Code Changes)

```bash
export QUOTES_DB_PATH="/opt/landscaping/quotes.db"
python3 server_http.py  # MCP server starts on :3207 with empty DB
```

The SQLite schema, MCP protocol, and all 62 tools work identically. No code changes.

### Step 2: Pricing Rules (`pricing_rules.json`)

Replace print shop rules with landscaping-specific pricing:

```json
{
  "version": 1,
  "currency": "usd",
  "rules": {
    "tree-planting": {
      "base_unit_price": 120.0,
      "default_unit_cost": 45.0,
      "description": "15-gallon tree, planting labor included",
      "pricing_tiers": {
        "1-5":   {"multiplier": 1.0, "description": "Small job — standard pricing"},
        "6-20":  {"multiplier": 0.9, "description": "Mid-volume — 10% discount"},
        "21+":   {"multiplier": 0.8, "description": "Large job — 20% discount"}
      }
    },
    "mulch-delivery": {
      "base_cubic_yard_price": 65.0,
      "default_unit_cost": 22.0,
      "delivery_minimum": 2.0,
      "mileage_rate": 2.5,
      "description": "Premium mulch, delivered and spread"
    },
    "irrigation-repair": {
      "base_unit_price": 85.0,
      "default_unit_cost": 15.0,
      "description": "Per-zone irrigation repair, parts + labor"
    },
    "generic": {
      "base_unit_price": 50.0,
      "default_unit_cost": 20.0,
      "description": "Fallback for unclassified services"
    }
  }
}
```

### Step 3: Seed Products & Vendors

```python
# Products
product_add(name="15-Gallon Olive Tree", description="Includes planting labor", standard_cost=45.0, list_price=120.0)
product_add(name="Premium Mulch (per cubic yard)", description="Delivered and spread", standard_cost=22.0, list_price=65.0)
product_add(name="Irrigation Zone Repair", description="Per-zone, parts + labor", standard_cost=15.0, list_price=85.0)

# Vendors
vendor_create(name="Desert Nursery", contact_info="orders@desertnursery.com")
vendor_create(name="Mulch Supply Co.", contact_info="sales@mulchsupply.com")
vendor_create(name="Irrigation Parts Plus", contact_info="wholesale@irrigparts.com")
```

### Step 4: Connect Stripe

```bash
# In the shop's .env file
STRIPE_API_KEY=sk_live_xxx  # or sk_test_xxx for sandbox
```

### Step 5: Go Live — Agent Handles First Intake

**Raw SMS from customer:**
> "Need 8 olive trees planted at 4200 N Scottsdale Rd by Thursday. Also 3 yards of mulch. What's the total?"

**Agent executes the full loop:**

```python
# 1. Parse intake
intake_parse("Need 8 olive trees planted at 4200 N Scottsdale Rd by Thursday. Also 3 yards of mulch. What's the total?")
# → {"items": [{"product_name": "olive tree", "quantity": 8}, {"product_name": "mulch", "quantity": 3}], "deadline": "by Thursday"}

# 2. Preview pricing from rules
pricing_rules_preview("tree-planting", 8, "{}")
# → {"unit_price": 108.00, "extended_price": 864.00}  (6-20 tier = 0.9x)

pricing_rules_preview("mulch-delivery", 3, "{}")
# → {"unit_price": 65.00, "extended_price": 195.00}

# 3. Create customer
customer_create(name="Scottsdale Residence", phone="480-555-0142", address="4200 N Scottsdale Rd")

# 4. Build quote
quote_create(customer_id=1)  # → Quote #1 (Draft)
quote_add_item(quote_id=1, product_id=1, quantity=8, quoted_cost=45.0, quoted_price=108.0)
quote_add_item(quote_id=1, product_id=2, quantity=3, quoted_cost=22.0, quoted_price=65.0)
quote_verify_totals(quote_id=1)
# → customer_total: $1,059.00

# 5. Send payment link
stripe_create_payment_link_for_quote(quote_id=1)
# → https://buy.stripe.com/xxx

# 6. Track vendor cost
vendor_invoice_create(vendor_id=1, amount=360.00, quote_id=1)
# → Desert Nursery invoice: $360 (8 trees @ $45)

# 7. Cost worksheet
quote_cost_worksheet(quote_id=1)
# → Revenue: $1,059 | Vendor cost: $360 | Profit: $699 | Margin: 66%
```

**Same framework. Zero code changes. Different industry. 15-minute onboarding.**

---

## Status Pipeline Mapping

The state machine is identical — only labels change:

| Framework State | Print Shop | Landscaping | Cafe Catering |
|----------------|------------|-------------|---------------|
| Draft | Draft | Draft | Draft |
| Sent | Sent | Proposal Sent | Quote Sent |
| Approved | Approved | Booked | Confirmed |
| In Progress | In Progress | Scheduled | Prep |
| In Production | In Production | On Site | Cooking |
| Completed | Completed | Job Complete | Delivered |
| Declined | Declined | Declined | Cancelled |

---

## What's Client-Specific vs Framework-Core

| Module | Framework Core (shared) | Client Config (per-shop) |
|--------|------------------------|--------------------------|
| Intake Parser | ✅ NLP extraction logic | Product names/keywords in intake |
| Pricing Engine | ✅ JSON rules engine | `pricing_rules.json` content |
| Product Catalog | ✅ CRUD tools | Product entries |
| Customer CRM | ✅ Schema + tools | Customer data |
| Quote Builder | ✅ Line items + charges | Charge types, quote templates |
| Vendor Ledger | ✅ Invoice tracking | Vendor list |
| Stripe Router | ✅ Payment link + product creation | Stripe account (live/test key) |
| Status Machine | ✅ State transition logic | Status label mapping |
| Memory System | ✅ Scoped storage | Business-specific knowledge |
| Reports | ✅ Deterministic generators | Report formatting preferences |

---

## Replicability Proof Points for Demo Video

1. **Live customer creation** — `customer_create(name="Hackathon Demo")` shows the CRM is not hardcoded
2. **Live product addition** — `product_add(name="Demo Service", standard_cost=10, list_price=30)` shows the catalog is extensible
3. **Pricing rules swap** — Show `pricing_rules.json` for landscaping next to the print shop version
4. **Same tool calls** — The exact same `intake_parse()`, `quote_create()`, `quote_add_item()`, `stripe_create_payment_link_for_quote()` calls work for both businesses
5. **Zero code changes** — The `server.py` file is identical. Only the JSON config and DB contents change.