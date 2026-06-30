# Earn → Spend Loop: Agent-Initiated Vendor Payment Path

> **Task 2 — Stripe Vendor Spend Integration (Minimal Viable)**
> Demonstrates the complete earn → spend loop using live data from Quote #187

---

## The Loop: How an Agent Earns and Spends

```
EARN ────────────────── RECEIVE ────────────────── SPEND ────────────────── VERIFY
 │                        │                         │                        │
 │ 1. Quote created       │ 3. Customer pays        │ 5. Vendor invoice      │ 7. Payment summary
 │ 2. Payment link sent   │ 4. Payment recorded     │ 6. Stripe payout       │    shows closed loop
```

---

## Live Case Study: Quote #187 — CnB Reptiles

### Phase 1: EARN

**Customer request (raw intake):**
> "Bill from CnB Reptiles — need 15,000 flyers, 4×5, double-sided, same image both sides"

**Agent actions:**
1. `intake_parse(raw_text)` → structured data: product=Flyer, qty=15000, size=4×5, finish=double-sided
2. `customer_lookup("CnB Reptiles")` → customer_id=5
3. `quote_create(customer_id=5)` → Quote #187 (Draft)
4. `quote_add_item(quote_id=187, product_id=27, quantity=15000, quoted_cost=0.02822, quoted_price=0.04833)` → line item added
5. `quote_verify_totals(quote_id=187)` → verified: $724.95 customer total
6. `quote_update_status(quote_id=187, new_status="Sent")` → quote sent to customer

### Phase 2: RECEIVE (Stripe Payment)

**Agent actions:**
7. `stripe_create_product_for_quote(quote_id=187)` → Stripe product `prod_UbkOixRlr8hEYE` created
8. `stripe_create_payment_link_for_quote(quote_id=187)` → Payment link generated:
   ```
   https://buy.stripe.com/eVq4gy9z9cyO42M9gOc7u1F
   ```
9. `stripe_record_payment_link_metadata(quote_id=187, payment_link_url=...)` → metadata appended to quote notes
10. Customer pays via Stripe link → $724.95 received
11. `payment_record(quote_id=187, amount=724.95)` → Payment #149 recorded
12. `quote_update_status(quote_id=187, new_status="Approved")` → quote approved
13. `quote_update_status(quote_id=187, new_status="In Progress")` → production begins

### Phase 3: SPEND (Vendor Cost Tracking)

**Agent actions:**
14. `vendor_invoice_create(vendor_id=3, amount=423.30, quote_id=187, invoice_number="4Over-187")` → vendor invoice created
    - Vendor: 4Over (print supplier)
    - Amount: $423.30 (cost of 15,000 flyers)
    - Tied to Quote #187 for cost allocation
15. `quote_cost_worksheet(quote_id=187)` → shows billable vs internal costs:
    - Revenue: $724.95
    - Vendor cost: $423.30
    - Gross profit: $301.65
    - Margin: 41.6%

### Phase 4: VERIFY (Closed Loop)

**Agent actions:**
16. `quote_payment_summary(quote_id=187)` → customer-facing financial snapshot:
    ```json
    {
      "customer_total": 724.95,
      "paid": 724.95,
      "balance_due": 0.0,
      "status": "Completed"
    }
    ```
17. `order_detail_report(quote_id=187)` → full operational detail:
    - Items: 15,000 × Flyer 4×5 (10k run) @ $0.04833 = $724.95
    - Vendor cost: $423.30 (4Over)
    - Payment: $724.95 (Payment #149)
    - Margin: 41.6%
    - Status: Completed

---

## Stripe Vendor Spend: The "Spend" Side

The framework currently supports two Stripe-powered spend paths:

### Path A: Payment Link (Customer → Business) — ✅ LIVE
The agent creates Stripe payment links for customers to pay quotes. This is the **earn** mechanism.

```
stripe_create_payment_link_for_quote(quote_id=187)
→ https://buy.stripe.com/eVq4gy9z9cyO42M9gOc7u1F
→ Customer pays $724.95
→ payment_record(quote_id=187, amount=724.95)
```

### Path B: Vendor Invoice Recording (Business → Vendor) — ✅ LIVE
The agent records vendor invoices tied to quotes. This is the **spend tracking** mechanism.

```
vendor_invoice_create(vendor_id=3, amount=423.30, quote_id=187)
→ Vendor invoice #XX created, status: Unpaid
→ quote_cost_worksheet(quote_id=187) shows cost allocation
```

### Path C: Agent-Initiated Vendor Payout (Stripe Transfer) — 📋 DOCUMENTED
The next module in the framework: agent initiates a Stripe Transfer to pay a vendor invoice.

**Proposed tool:** `stripe_initiate_vendor_payout(invoice_id)`
```python
# Proposed implementation (not yet live — documented for hackathon)
def stripe_initiate_vendor_payout(invoice_id: int) -> str:
    """Pay a vendor invoice via Stripe Transfer."""
    # 1. Look up vendor invoice
    # 2. Verify invoice status = Unpaid
    # 3. Get vendor's Stripe connected account ID
    # 4. Create Stripe Transfer: stripe.Transfer.create(
    #        amount=int(amount * 100),  # cents
    #        currency="usd",
    #        destination=vendor_stripe_account_id,
    #        transfer_group=f"quote-{quote_id}"
    #    )
    # 5. Update vendor invoice status to "Paid"
    # 6. Record in agent memory
```

**Why not live yet:** Vendor Stripe Connect accounts require onboarding each vendor (KYC, bank verification). The framework documents this path but the live Print Junkie instance records vendor invoices manually (vendor pays via traditional invoice/PO).

---

## Complete Earn → Spend Loop Summary

| Step | Phase | Tool | Result |
|------|-------|------|--------|
| 1 | EARN | `intake_parse()` | Raw SMS → structured quote data |
| 2 | EARN | `quote_create()` + `quote_add_item()` | Draft quote with line items |
| 3 | EARN | `quote_verify_totals()` | Math verified: $724.95 |
| 4 | EARN | `stripe_create_payment_link_for_quote()` | Payment link sent to customer |
| 5 | RECEIVE | `payment_record()` | $724.95 payment recorded |
| 6 | RECEIVE | `quote_update_status()` | Approved → In Progress |
| 7 | SPEND | `vendor_invoice_create()` | $423.30 vendor invoice (4Over) |
| 8 | SPEND | `quote_cost_worksheet()` | Cost allocation: $301.65 profit, 41.6% margin |
| 9 | VERIFY | `quote_payment_summary()` | Balance $0, fully paid |
| 10 | VERIFY | `order_detail_report()` | Complete operational snapshot |

**The agent earned $724.95, tracked $423.30 in vendor costs, and netted $301.65 — all through deterministic MCP tools with safety gates.**

---

## Safety Architecture

- **Propose-before-commit**: All financial writes require agent to show proposal before executing
- **Approval gates**: DB changes require user confirmation
- **Deterministic tools first**: All pricing from rules, not LLM guessing
- **No vendor data in customer-facing output**: Vendor names, costs, margins never appear in customer quotes
- **Stripe live key protection**: Agent can create payment links but cannot execute transfers without explicit approval flow