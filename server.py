"""
Print Junkie AZ Quote Bot — MCP Server
27 tools across 6 domains: Customer, Product, Quote, Financial, Intake, Agent Memory.
Uses FastMCP with sqlite3 (WAL mode, busy_timeout=5000).
"""

import os
import sys
import json
import sqlite3
import logging
import ast
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# ─── CONFIG ────────────────────────────────────────────────────────────────────

DB_PATH = os.environ.get(
    "QUOTES_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "quotes.db"),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("printjunkie-mcp")

# ─── DATABASE ──────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and busy_timeout."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict]:
    """Convert cursor rows to a list of JSON-serialisable dicts."""
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


MONEY_PLACES = Decimal("0.01")
INTERNAL_CHARGE_TYPES = {"vendor", "cogs", "cost", "internal", "supply"}
DISCOUNT_CHARGE_TYPES = {"discount", "credit"}
OPEN_ORDER_STATUS_BUCKETS = ["Lead", "Draft", "Sent", "Approved", "On Hold", "In Progress", "In Production"]
PRODUCTION_QUEUE_STATUSES = {"Approved", "In Progress", "In Production"}


def money(value) -> Decimal:
    """Normalize money-like inputs to Decimal cents precision."""
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        raw = value
    else:
        raw = Decimal(str(value))
    return raw.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


class DecimalExpressionEvaluator(ast.NodeVisitor):
    """Safe arithmetic evaluator for +, -, *, /, ** and parentheses using Decimal."""

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return left / right
        if isinstance(node.op, ast.Pow):
            if right != right.to_integral_value():
                raise ValueError("Exponent must be an integer")
            return left ** int(right)
        raise ValueError(f"Unsupported operator: {type(node.op).__name__}")

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

    def visit_Constant(self, node):
        if not isinstance(node.value, (int, float)):
            raise ValueError("Only numeric constants are allowed")
        return Decimal(str(node.value))

    def visit_Num(self, node):  # pragma: no cover - py<3.8 compatibility shape
        return Decimal(str(node.n))

    def generic_visit(self, node):
        raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def evaluate_decimal_expression(expression: str) -> Decimal:
    tree = ast.parse(expression, mode="eval")
    return money(DecimalExpressionEvaluator().visit(tree))


def is_internal_charge(charge_type: str | None) -> bool:
    return (charge_type or "").strip().lower() in INTERNAL_CHARGE_TYPES


def summarize_quote_financials(items: list[dict], charges: list[dict], payments: list[dict]) -> dict:
    items_subtotal = money(sum(Decimal(str(i.get("quantity", 0) or 0)) * Decimal(str(i.get("quoted_price", 0) or 0)) for i in items))
    item_cost_total = money(sum(Decimal(str(i.get("quantity", 0) or 0)) * Decimal(str(i.get("quoted_cost", 0) or 0)) for i in items))

    billable_charges_total = Decimal("0.00")
    internal_charges_total = Decimal("0.00")
    discount_total = Decimal("0.00")
    total_charges = Decimal("0.00")

    for charge in charges:
        amount = money(charge.get("amount", 0))
        total_charges += amount
        ctype = (charge.get("type") or "").strip().lower()
        if is_internal_charge(ctype):
            internal_charges_total += amount
        elif ctype in DISCOUNT_CHARGE_TYPES:
            normalized_discount = money(-abs(amount))
            discount_total += normalized_discount
            billable_charges_total += normalized_discount
        else:
            billable_charges_total += amount

    paid_total = money(sum(money(p.get("amount", 0)) for p in payments))
    customer_total = money(items_subtotal + billable_charges_total)
    customer_balance_due = money(customer_total - paid_total)
    internal_cost_total = money(item_cost_total + internal_charges_total)
    gross_margin = money(customer_total - internal_cost_total)

    return {
        "items_subtotal": items_subtotal,
        "item_cost_total": item_cost_total,
        "billable_charges_total": money(billable_charges_total),
        "internal_charges_total": money(internal_charges_total),
        "discount_total": money(discount_total),
        "charges_total": money(total_charges),
        "customer_total": customer_total,
        "paid": paid_total,
        "customer_balance_due": customer_balance_due,
        "internal_cost_total": internal_cost_total,
        "gross_margin": gross_margin,
    }


def recalc_quote_margin(quote_id: int) -> dict:
    """Recalculate and persist margin fields on the quotes table.
    Called after any item/charge mutation so margin data is always current."""
    conn = get_conn()
    try:
        items = rows_to_dicts(conn.execute(
            "SELECT * FROM quote_items WHERE quote_id = ?", (quote_id,)
        ))
        charges = rows_to_dicts(conn.execute(
            "SELECT * FROM quote_charges WHERE quote_id = ? ORDER BY id", (quote_id,)
        ))
        payments = rows_to_dicts(conn.execute(
            "SELECT * FROM payments WHERE quote_id = ? ORDER BY payment_date, id", (quote_id,)
        ))
        fin = summarize_quote_financials(items, charges, payments)
        total_sell = float(fin["customer_total"])
        total_cost = float(fin["internal_cost_total"])
        total_discount = float(fin.get("discount_total", 0))
        gross_profit = float(fin["gross_margin"])
        margin_pct = round(gross_profit / total_sell * 100, 1) if total_sell else 0.0
        conn.execute(
            "UPDATE quotes SET total_sell=?, total_cost=?, total_discount=?, gross_profit=?, margin_pct=? WHERE id=?",
            (total_sell, total_cost, total_discount, gross_profit, margin_pct, quote_id)
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "quote_id": quote_id,
        "total_sell": total_sell,
        "total_cost": total_cost,
        "total_discount": total_discount,
        "gross_profit": gross_profit,
        "margin_pct": margin_pct,
    }


def parse_receipt_money_tokens(raw_text: str) -> list[Decimal]:
    amounts = []
    for match in re.finditer(r"\$\s*(\d+(?:\.\d{1,2})?)", raw_text):
        amounts.append(money(match.group(1)))
    return amounts


def receipt_line_candidates(raw_text: str) -> list[dict]:
    candidates = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if len(line) < 3:
            continue
        amounts = re.findall(r"\$\s*(\d+(?:\.\d{1,2})?)", line)
        qty_match = re.search(r"\b(?:qty|quantity|x)\s*[:=-]?\s*(\d+)\b", line, re.IGNORECASE)
        quantity = int(qty_match.group(1)) if qty_match else 1
        if amounts:
            unit_amount = money(amounts[-1])
            candidates.append({
                "raw_line": line,
                "quantity": quantity,
                "unit_amount": float(unit_amount),
                "line_total": float(money(unit_amount * Decimal(str(quantity)))),
            })
    return candidates


def load_pricing_rules() -> dict:
    rules_path = Path(__file__).with_name("pricing_rules.json")
    if not rules_path.exists():
        raise FileNotFoundError(f"Missing pricing rules file: {rules_path}")
    return json.loads(rules_path.read_text())


from intake_parser import parse_intake
from scopes import load_customer_scope, load_order_scope, load_product_scope
try:
    from stripe_integration import get_stripe_health, get_stripe_module, quote_stripe_name
except ImportError:
    # Fallback when stripe_integration.py is not available (e.g. fresh clone without Stripe config)
    def get_stripe_health():
        return {"configured": False, "error": "stripe_integration.py not found — see .env.example to configure Stripe"}
    def get_stripe_module():
        raise RuntimeError("Stripe not configured — set STRIPE_API_KEY in .env and install stripe_integration.py")
    def quote_stripe_name(quote_id, customer_name=None):
        return f"Quote #{quote_id}" + (f" — {customer_name}" if customer_name else "")

# ─── MCP SERVER ────────────────────────────────────────────────────────────────

mcp = FastMCP("Print Junkie AZ Quote Bot", dependencies=["sqlite3"])

# ────────────────────────────────────────────────────────────────────────────────
#  CUSTOMER MANAGEMENT  (3 tools)
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def customer_lookup(name: str) -> str:
    """Find or fuzzy-match a customer by name.  Returns JSON list of matches."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM customers WHERE name LIKE ? ORDER BY name LIMIT 20",
            (f"%{name}%",),
        )
        rows = rows_to_dicts(cur)
    finally:
        conn.close()
    if not rows:
        return json.dumps({"error": "No customers found", "query": name})
    return json.dumps(rows, default=str)


@mcp.tool()
def customer_create(
    name: str,
    email: str = None,
    phone: str = None,
    address: str = None,
) -> str:
    """Create a new customer record. Returns the new customer_id."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO customers (name, email, phone, address) VALUES (?, ?, ?, ?)",
            (name, email, phone, address),
        )
        conn.commit()
        cid = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Customer created: id={cid}, name={name}")
    return json.dumps({"customer_id": cid, "name": name})


# ────────────────────────────────────────────────────────────────────────────────
#  VENDORS  (3 tools)
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def vendor_search(query: str) -> str:
    """Search vendors by name (LIKE match). Returns JSON list."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM vendors WHERE name LIKE ? ORDER BY name LIMIT 50",
            (f"%{query}%",),
        )
        rows = rows_to_dicts(cur)
    finally:
        conn.close()
    if not rows:
        return json.dumps({"error": "No vendors found", "query": query})
    return json.dumps(rows, default=str)


@mcp.tool()
def vendor_get(vendor_id: int) -> str:
    """Get a vendor by vendor_id."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return json.dumps({"error": f"Vendor {vendor_id} not found"})
    return json.dumps(dict(row), default=str)


@mcp.tool()
def vendor_create(name: str, contact_info: str = None) -> str:
    """Create a new vendor record. Returns the new vendor_id."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO vendors (name, contact_info) VALUES (?, ?)",
            (name, contact_info),
        )
        conn.commit()
        vid = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Vendor created: id={vid}, name={name}")
    return json.dumps({"vendor_id": vid, "name": name})


@mcp.tool()
def customer_history(customer_id: int) -> str:
    """All quotes, payments, and outstanding balance for a customer."""
    conn = get_conn()
    try:
        cur = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,))
        cust = cur.fetchone()
        if not cust:
            return json.dumps({"error": f"Customer {customer_id} not found"})

        cur = conn.execute(
            "SELECT * FROM quotes WHERE customer_id = ? ORDER BY created_at DESC",
            (customer_id,),
        )
        quotes = rows_to_dicts(cur)

        total_outstanding = Decimal("0.00")
        for q in quotes:
            qid = q["id"]
            items = rows_to_dicts(conn.execute(
                "SELECT quantity, quoted_cost, quoted_price FROM quote_items WHERE quote_id = ?",
                (qid,),
            ))
            charges = rows_to_dicts(conn.execute(
                "SELECT amount, type FROM quote_charges WHERE quote_id = ?",
                (qid,),
            ))
            payments = rows_to_dicts(conn.execute(
                "SELECT amount FROM payments WHERE quote_id = ?",
                (qid,),
            ))
            financials = summarize_quote_financials(items, charges, payments)
            q["customer_total"] = float(financials["customer_total"])
            q["internal_cost_total"] = float(financials["internal_cost_total"])
            q["total_paid"] = float(financials["paid"])
            q["balance_due"] = float(financials["customer_balance_due"])
            if q["status"] not in ("Completed", "Declined"):
                total_outstanding += financials["customer_balance_due"]

        result = {
            "customer": dict(cust),
            "quotes": quotes,
            "total_outstanding": float(money(total_outstanding)),
        }
    finally:
        conn.close()
    return json.dumps(result, default=str)


@mcp.tool()
def customer_update(
    customer_id: int,
    name: str = None,
    email: str = None,
    phone: str = None,
    address: str = None,
    contact_info: str = None,
) -> str:
    """Update an existing customer. Only provided fields are changed. Use to fix name, phone, email, or address without deleting and recreating."""
    conn = get_conn()
    try:
        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if email is not None:
            updates.append("email = ?")
            params.append(email)
        if phone is not None:
            updates.append("phone = ?")
            params.append(phone)
        if address is not None:
            updates.append("address = ?")
            params.append(address)
        if contact_info is not None:
            updates.append("contact_info = ?")
            params.append(contact_info)
        if not updates:
            return json.dumps({"error": "No fields provided to update"})
        params.append(customer_id)
        conn.execute(f"UPDATE customers SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        cur = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return json.dumps({"error": f"Customer {customer_id} not found"})
    logger.info(f"Customer updated: id={customer_id}")
    return json.dumps(dict(row), default=str)

# ────────────────────────────────────────────────────────────────────────────────
#  PRODUCT CATALOG  (3 tools)
# ────────────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def product_search(query: str) -> str:
    """Search products by name (LIKE match). Returns JSON list."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM products WHERE name LIKE ? ORDER BY name LIMIT 50",
            (f"%{query}%",),
        )
        rows = rows_to_dicts(cur)
    finally:
        conn.close()
    if not rows:
        return json.dumps({"error": "No products found", "query": query})
    return json.dumps(rows, default=str)


@mcp.tool()
def product_get(product_id: int) -> str:
    """Full product details with cost and price."""
    conn = get_conn()
    try:
        cur = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return json.dumps({"error": f"Product {product_id} not found"})
    return json.dumps(dict(row), default=str)


@mcp.tool()
def product_add(
    name: str,
    description: str,
    standard_cost: float,
    list_price: float,
) -> str:
    """Add a new product to the catalog. Returns the new product_id."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO products (name, description, standard_cost, list_price) VALUES (?, ?, ?, ?)",
            (name, description, standard_cost, list_price),
        )
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Product added: id={pid}, name={name}")
    return json.dumps({"product_id": pid, "name": name})



@mcp.tool()
def product_update(
    product_id: int,
    name: str = None,
    description: str = None,
    standard_cost: float = None,
    list_price: float = None,
) -> str:
    """Update an existing product. Only provided fields are changed. Use to fix standard_cost, list_price, name, or description without deleting and recreating."""
    conn = get_conn()
    try:
        # Build dynamic SET clause
        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if standard_cost is not None:
            updates.append("standard_cost = ?")
            params.append(standard_cost)
        if list_price is not None:
            updates.append("list_price = ?")
            params.append(list_price)
        if not updates:
            return json.dumps({"error": "No fields provided to update"})
        params.append(product_id)
        conn.execute(f"UPDATE products SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        # Return updated product
        cur = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return json.dumps({"error": f"Product {product_id} not found"})
    logger.info(f"Product updated: id={product_id}")
    return json.dumps(dict(row), default=str)


@mcp.tool()
def pricing_rules_health() -> str:
    """Check JSON pricing rules availability and top-level rule keys."""
    rules = load_pricing_rules()
    return json.dumps({
        "ok": True,
        "currency": rules.get("currency", "usd"),
        "rule_types": sorted((rules.get("rules") or {}).keys()),
    }, default=str)


@mcp.tool()
def pricing_rules_preview(
    product_type: str,
    quantity: int,
    options_json: str = None,
) -> str:
    """Preview a deterministic price from the JSON pricing rules file."""
    rules = load_pricing_rules().get("rules", {})
    opts = json.loads(options_json) if options_json else {}
    rule = rules.get(product_type) or rules.get("generic") or {}
    qty = Decimal(str(quantity or 0))
    if qty <= 0:
        return json.dumps({"error": "quantity must be positive", "quantity": quantity})

    unit_price = money(rule.get("base_unit_price", rule.get("base_price", 25)))
    unit_cost = money(rule.get("default_unit_cost", rule.get("base_cost", 10)))

    if product_type == "apparel":
        sides = int(opts.get("print_sides", 0) or 0)
        unit_price += money(rule.get("print_side_price", 0)) * Decimal(str(sides))
        for size in opts.get("sizes", []) or []:
            up = Decimal(str((rule.get("size_upcharges") or {}).get(str(size).upper(), 0) or 0))
            unit_price += up / qty
    elif product_type == "business_card":
        base_qty = Decimal(str(rule.get("base_quantity", 100) or 100))
        multiplier = qty / base_qty
        unit_price = money((Decimal(str(rule.get("base_price", 80))) * multiplier) / qty)
        unit_cost = money((Decimal(str(rule.get("base_cost", 39))) * multiplier) / qty)
        if opts.get("laminated"):
            unit_price += money(rule.get("lamination_upcharge", 0)) / qty
        if opts.get("double_sided"):
            unit_price += money(rule.get("double_sided_upcharge", 0)) / qty
    elif product_type == "banner":
        sqft = Decimal(str(opts.get("square_feet", 1) or 1))
        unit_price = money(Decimal(str(rule.get("base_sqft_price", 8))) * sqft)
        unit_cost = money(Decimal(str(rule.get("base_sqft_cost", 3.5))) * sqft)
        if opts.get("grommets"):
            unit_price += money(rule.get("grommet_upcharge", 0))
        if opts.get("hems"):
            unit_price += money(rule.get("hem_upcharge", 0))

    extended_price = money(unit_price * qty)
    extended_cost = money(unit_cost * qty)
    return json.dumps({
        "product_type": product_type,
        "quantity": int(qty),
        "unit_price": float(unit_price),
        "unit_cost": float(unit_cost),
        "extended_price": float(extended_price),
        "extended_cost": float(extended_cost),
        "options": opts,
        "rules_version": load_pricing_rules().get("version", 1),
    }, default=str)


@mcp.tool()
def pricing_history(
    query: str = None,
    product_type: str = None,
    customer_id: int = None,
    limit: int = 20,
) -> str:
    """Look up historical pricing from past quotes. Shows what was actually charged for similar items.
    
    Args:
        query: Search product names (partial match). E.g. "banner", "shirt", "business card"
        product_type: Filter by product type if known
        customer_id: Filter to a specific customer's history
        limit: Max results (default 20)
    """
    conn = get_conn()
    try:
        where_clauses = []
        params = []
        
        if query:
            where_clauses.append("p.name LIKE ?")
            params.append(f"%{query}%")
        if product_type:
            where_clauses.append("p.name LIKE ?")
            params.append(f"%{product_type}%")
        if customer_id:
            where_clauses.append("q.customer_id = ?")
            params.append(customer_id)
        
        where = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        rows = conn.execute(f"""
            SELECT 
                qi.quote_id,
                p.name AS product_name,
                p.id AS product_id,
                qi.quantity,
                qi.quoted_price,
                qi.quoted_cost,
                q.status,
                q.created_at,
                c.name AS customer_name
            FROM quote_items qi
            JOIN products p ON p.id = qi.product_id
            JOIN quotes q ON q.id = qi.quote_id
            LEFT JOIN customers c ON c.id = q.customer_id
            WHERE {where}
            ORDER BY q.created_at DESC
            LIMIT ?
        """, params + [limit]).fetchall()
        
        results = []
        for r in rows:
            qty = r[3] or 1
            total_price = r[4] or 0
            total_cost = r[5] or 0
            unit_price = total_price / qty if qty > 0 else total_price
            unit_cost = total_cost / qty if qty > 0 else total_cost
            margin = total_price - total_cost
            margin_pct = (margin / total_price * 100) if total_price > 0 else 0
            
            results.append({
                "quote_id": r[0],
                "product": r[1],
                "product_id": r[2],
                "quantity": qty,
                "total_price": round(total_price, 2),
                "total_cost": round(total_cost, 2),
                "unit_price": round(unit_price, 2),
                "unit_cost": round(unit_cost, 2),
                "margin": round(margin, 2),
                "margin_pct": round(margin_pct, 1),
                "status": r[6],
                "date": r[7],
                "customer": r[8],
            })
        
        if not results:
            return json.dumps({"message": "No historical pricing found", "query": query, "product_type": product_type})
        
        # Summary stats
        avg_unit_price = sum(r["unit_price"] for r in results) / len(results)
        avg_unit_cost = sum(r["unit_cost"] for r in results) / len(results)
        avg_margin_pct = sum(r["margin_pct"] for r in results) / len(results)
        
        return json.dumps({
            "count": len(results),
            "summary": {
                "avg_unit_price": round(avg_unit_price, 2),
                "avg_unit_cost": round(avg_unit_cost, 2),
                "avg_margin_pct": round(avg_margin_pct, 1),
            },
            "items": results,
        }, default=str)
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────────
#  QUOTE LIFECYCLE  (6 tools)
# ────────────────────────────────────────────────────────────────────────────────

# Flexible state machine — business-reason transitions only.
# Any status can move to any other status that makes business sense.
# Terminal states (Completed, Declined) can also be reopened.
VALID_TRANSITIONS = {
    "Lead": ("Draft", "On Hold", "Declined"),
    "Draft": ("Sent", "On Hold", "Completed", "Declined"),
    "Sent": ("Approved", "On Hold", "Draft", "Declined"),
    "Approved": ("In Progress", "On Hold", "Draft", "Declined"),
    "On Hold": ("Draft", "Sent", "Approved", "Declined"),
    "In Progress": ("Completed", "On Hold", "Sent", "Draft", "Declined"),
    "In Production": ("Completed", "On Hold", "In Progress", "Declined"),
    "Completed": ("Draft", "In Progress"),  # reopen if needed
    "Declined": ("Draft", "Lead"),  # reopen if customer returns
}


@mcp.tool()
def quote_create(customer_id: int, notes: str = None) -> str:
    """Create a new draft quote for a customer."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO quotes (customer_id, status, notes) VALUES (?, 'Draft', ?)",
            (customer_id, notes),
        )
        conn.commit()
        qid = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Quote created: id={qid}, customer_id={customer_id}")
    return json.dumps({"quote_id": qid, "status": "Draft"})


@mcp.tool()
def quote_add_item(
    quote_id: int,
    product_id: int,
    quantity: int,
    quoted_cost: float,
    quoted_price: float,
    variants_json: str = None,
) -> str:
    """Add a line item to a quote. Optional variants_json for variant data."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO quote_items
               (quote_id, product_id, quantity, quoted_cost, quoted_price, variants_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (quote_id, product_id, quantity, quoted_cost, quoted_price, variants_json),
        )
        conn.commit()
        item_id = cur.lastrowid
    finally:
        conn.close()
    recalc_quote_margin(quote_id)
    return json.dumps({"item_id": item_id, "quote_id": quote_id})


@mcp.tool()
def quote_add_charge(
    quote_id: int,
    description: str,
    amount: float,
    charge_type: str,
    notes: str = None,
    variants_json: str = None,
) -> str:
    """Add a charge to a quote (rush, shipping, discount, vendor, etc.). Use variants_json for structured variant data like banner stand options. charge_type can be: rush, shipping, discount, vendor, cogs, internal, supply, or custom."""
    conn = get_conn()
    try:
        # If variants_json provided, store in notes field as structured data
        effective_notes = notes or ""
        if variants_json:
            effective_notes = json.dumps({"variants": json.loads(variants_json), "original_notes": notes or ""})
        cur = conn.execute(
            "INSERT INTO quote_charges (quote_id, description, amount, type, notes) VALUES (?, ?, ?, ?, ?)",
            (quote_id, description, amount, charge_type, effective_notes),
        )
        conn.commit()
        charge_id = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Charge added: id={charge_id}, quote_id={quote_id}, type={charge_type}, amount={amount}")
    recalc_quote_margin(quote_id)
    return json.dumps({"charge_id": charge_id, "quote_id": quote_id, "charge_type": charge_type, "amount": amount})


@mcp.tool()
def quote_clone(quote_id: int, notes_suffix: str = None) -> str:
    """Clone a quote into a new Draft quote with copied items and charges."""
    conn = get_conn()
    try:
        source = conn.execute(
            "SELECT customer_id, notes FROM quotes WHERE id = ?",
            (quote_id,),
        ).fetchone()
        if not source:
            return json.dumps({"error": f"Quote {quote_id} not found"})
        source_items = rows_to_dicts(conn.execute(
            "SELECT product_id, quantity, quoted_cost, quoted_price, variants_json FROM quote_items WHERE quote_id = ? ORDER BY id",
            (quote_id,),
        ))
        source_charges = rows_to_dicts(conn.execute(
            "SELECT description, amount, type, notes FROM quote_charges WHERE quote_id = ? ORDER BY id",
            (quote_id,),
        ))
        notes = source["notes"] or ""
        if notes_suffix:
            notes = f"{notes}\n{notes_suffix}".strip() if notes else notes_suffix
        cur = conn.execute(
            "INSERT INTO quotes (customer_id, status, notes) VALUES (?, 'Draft', ?)",
            (source["customer_id"], notes),
        )
        new_quote_id = cur.lastrowid
        for item in source_items:
            conn.execute(
                """INSERT INTO quote_items (quote_id, product_id, quantity, quoted_cost, quoted_price, variants_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_quote_id, item["product_id"], item["quantity"], item["quoted_cost"], item["quoted_price"], item.get("variants_json")),
            )
        for charge in source_charges:
            conn.execute(
                "INSERT INTO quote_charges (quote_id, description, amount, type, notes) VALUES (?, ?, ?, ?, ?)",
                (new_quote_id, charge["description"], charge["amount"], charge["type"], charge.get("notes")),
            )
        conn.commit()
    finally:
        conn.close()
    return json.dumps({"source_quote_id": quote_id, "new_quote_id": new_quote_id, "status": "Draft"})


@mcp.tool()
def quote_itemized_list(quote_id: int) -> str:
    """Clean itemized list of quote items suitable for customer delivery. Returns JSON, not markdown."""
    try:
        raw = json.loads(quote_get(quote_id))
        if raw.get("error"):
            return json.dumps(raw)

        quote = raw["quote"]
        items = raw.get("items", [])
        charges = raw.get("charges", [])
        totals = raw.get("totals", {})

        itemized = []
        for item in items:
            quantity = int(item.get("quantity") or 0)
            unit_price = float(item.get("quoted_price", 0) or 0)
            line_total = round(unit_price * quantity, 2)
            itemized.append({
                "product_name": item.get("product_name", "Custom Item"),
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            })

        billable_charges = []
        for charge in charges:
            ctype = (charge.get("type") or "").lower()
            if not is_internal_charge(ctype):
                amount = float(charge.get("amount", 0) or 0)
                billable_charges.append({
                    "description": charge.get("description", ""),
                    "amount": amount,
                    "type": ctype or "surcharge",
                })

        return json.dumps({
            "quote_id": quote_id,
            "customer_name": quote.get("customer_name"),
            "phone": quote.get("phone"),
            "status": quote.get("status"),
            "notes": quote.get("notes"),
            "items": itemized,
            "billable_charges": billable_charges,
            "items_subtotal": totals.get("items_subtotal", 0),
            "charges_total": totals.get("billable_charges_total", 0),
            "grand_total": totals.get("customer_total", totals.get("revenue", 0)),
            "paid": totals.get("paid", 0),
            "balance_due": totals.get("balance_due", 0),
        }, default=str)
    except Exception as exc:
        logger.error(f"quote_itemized_list({quote_id}) failed: {exc}")
        return json.dumps({"error": str(exc), "quote_id": quote_id})


@mcp.tool()
def quote_replace_items(quote_id: int, items_json: str) -> str:
    """Replace all quote line items with a provided JSON array of item objects."""
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid items_json: {exc}"})
    if not isinstance(items, list):
        return json.dumps({"error": "items_json must decode to a list of item objects"})

    normalized = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            return json.dumps({"error": f"Item at index {idx} is not an object"})
        required = ["product_id", "quantity", "quoted_cost", "quoted_price"]
        missing = [key for key in required if key not in item]
        if missing:
            return json.dumps({"error": f"Item at index {idx} missing required fields", "missing": missing})
        normalized.append({
            "product_id": item["product_id"],
            "quantity": item["quantity"],
            "quoted_cost": item["quoted_cost"],
            "quoted_price": item["quoted_price"],
            "variants_json": item.get("variants_json"),
        })

    conn = get_conn()
    try:
        row = conn.execute("SELECT id FROM quotes WHERE id = ?", (quote_id,)).fetchone()
        if not row:
            return json.dumps({"error": f"Quote {quote_id} not found"})
        conn.execute("DELETE FROM quote_items WHERE quote_id = ?", (quote_id,))
        for item in normalized:
            conn.execute(
                """INSERT INTO quote_items (quote_id, product_id, quantity, quoted_cost, quoted_price, variants_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (quote_id, item["product_id"], item["quantity"], item["quoted_cost"], item["quoted_price"], item.get("variants_json")),
            )
        conn.commit()
    finally:
        conn.close()
    return json.dumps({"quote_id": quote_id, "replaced_item_count": len(normalized), "updated": True})


@mcp.tool()
def quote_update_item(
    item_id: int,
    quantity: int = None,
    quoted_cost: float = None,
    quoted_price: float = None,
    variants_json: str = None,
    product_id: int = None,
) -> str:
    """Update mutable fields on a quote line item. Only provided fields are changed."""
    sets = []
    params = []
    if product_id is not None:
        sets.append("product_id = ?")
        params.append(product_id)
    if quantity is not None:
        sets.append("quantity = ?")
        params.append(quantity)
    if quoted_cost is not None:
        sets.append("quoted_cost = ?")
        params.append(quoted_cost)
    if quoted_price is not None:
        sets.append("quoted_price = ?")
        params.append(quoted_price)
    if variants_json is not None:
        sets.append("variants_json = ?")
        params.append(variants_json)
    if not sets:
        return json.dumps({"error": "Nothing to update — pass at least one mutable field", "item_id": item_id})

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, quote_id FROM quote_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            return json.dumps({"error": f"Quote item {item_id} not found", "item_id": item_id})
        params.append(item_id)
        conn.execute(
            f"UPDATE quote_items SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
        quote_id = row["quote_id"]
    finally:
        conn.close()
    recalc_quote_margin(quote_id)
    return json.dumps({"item_id": item_id, "quote_id": quote_id, "updated": True})


@mcp.tool()
def quote_remove_item(item_id: int) -> str:
    """Remove a quote line item by item_id."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, quote_id FROM quote_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            return json.dumps({"error": f"Quote item {item_id} not found", "item_id": item_id})
        quote_id = row["quote_id"]
        conn.execute("DELETE FROM quote_items WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    recalc_quote_margin(quote_id)
    return json.dumps({"item_id": item_id, "quote_id": quote_id, "removed": True})


@mcp.tool()
def quote_update_charge(
    charge_id: int,
    description: str = None,
    amount: float = None,
    charge_type: str = None,
    notes: str = None,
) -> str:
    """Update mutable fields on a quote charge. Only provided fields are changed."""
    sets = []
    params = []
    if description is not None:
        sets.append("description = ?")
        params.append(description)
    if amount is not None:
        sets.append("amount = ?")
        params.append(amount)
    if charge_type is not None:
        sets.append("type = ?")
        params.append(charge_type)
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if not sets:
        return json.dumps({"error": "Nothing to update — pass at least one mutable field", "charge_id": charge_id})

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, quote_id FROM quote_charges WHERE id = ?",
            (charge_id,),
        ).fetchone()
        if not row:
            return json.dumps({"error": f"Quote charge {charge_id} not found", "charge_id": charge_id})
        params.append(charge_id)
        conn.execute(
            f"UPDATE quote_charges SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
        quote_id = row["quote_id"]
    finally:
        conn.close()
    recalc_quote_margin(quote_id)
    return json.dumps({"charge_id": charge_id, "quote_id": quote_id, "updated": True})


@mcp.tool()
def quote_remove_charge(charge_id: int) -> str:
    """Remove a quote charge by charge_id."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, quote_id FROM quote_charges WHERE id = ?",
            (charge_id,),
        ).fetchone()
        if not row:
            return json.dumps({"error": f"Quote charge {charge_id} not found", "charge_id": charge_id})
        quote_id = row["quote_id"]
        conn.execute("DELETE FROM quote_charges WHERE id = ?", (charge_id,))
        conn.commit()
    finally:
        conn.close()
    recalc_quote_margin(quote_id)
    return json.dumps({"charge_id": charge_id, "quote_id": quote_id, "removed": True})


@mcp.tool()
def quote_item_bulk_replace(
    quote_id: int,
    items_json: str,
) -> str:
    """Replace all items on a quote atomically. items_json is a JSON array of objects with keys: product_id, quantity, quoted_price, quoted_cost, variants_json (optional). Deletes all existing items and inserts the new set in one transaction."""
    new_items = json.loads(items_json)
    conn = get_conn()
    try:
        conn.execute("DELETE FROM quote_items WHERE quote_id = ?", (quote_id,))
        added = []
        for item in new_items:
            pid = item.get("product_id")
            qty = item.get("quantity", 1)
            qprice = item.get("quoted_price", 0)
            qcost = item.get("quoted_cost", 0)
            variants = item.get("variants", "")
            variants_json = item.get("variants_json", "")
            cur = conn.execute(
                "INSERT INTO quote_items (quote_id, product_id, quantity, quoted_price, quoted_cost, variants, variants_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (quote_id, pid, qty, qprice, qcost, variants, variants_json),
            )
            added.append({"item_id": cur.lastrowid, "product_id": pid, "quantity": qty, "quoted_price": qprice})
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Quote {quote_id}: bulk replaced items, {len(added)} new items")
    return json.dumps({"quote_id": quote_id, "items_replaced": len(added), "new_items": added}, default=str)


@mcp.tool()
def quote_get(quote_id: int) -> str:
    """Full quote with items, charges, payments, and customer info."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """SELECT q.*, c.name AS customer_name, c.email, c.phone
               FROM quotes q
               JOIN customers c ON c.id = q.customer_id
               WHERE q.id = ?""",
            (quote_id,),
        )
        quote = cur.fetchone()
        if not quote:
            return json.dumps({"error": f"Quote {quote_id} not found"})

        items = rows_to_dicts(
            conn.execute(
                """SELECT qi.*, p.name AS product_name
                   FROM quote_items qi
                   LEFT JOIN products p ON p.id = qi.product_id
                   WHERE qi.quote_id = ?
                   ORDER BY qi.id""",
                (quote_id,),
            )
        )
        charges = rows_to_dicts(
            conn.execute("SELECT * FROM quote_charges WHERE quote_id = ? ORDER BY id", (quote_id,))
        )
        payments = rows_to_dicts(
            conn.execute("SELECT * FROM payments WHERE quote_id = ? ORDER BY payment_date, id", (quote_id,))
        )
    finally:
        conn.close()

    financials = summarize_quote_financials(items, charges, payments)

    result = {
        "quote": dict(quote),
        "items": items,
        "charges": charges,
        "payments": payments,
        "totals": {
            "items_subtotal": float(financials["items_subtotal"]),
            "item_cost_total": float(financials["item_cost_total"]),
            "billable_charges_total": float(financials["billable_charges_total"]),
            "internal_charges_total": float(financials["internal_charges_total"]),
            "charges_total": float(financials["charges_total"]),
            "revenue": float(financials["customer_total"]),
            "customer_total": float(financials["customer_total"]),
            "paid": float(financials["paid"]),
            "balance_due": float(financials["customer_balance_due"]),
            "internal_cost_total": float(financials["internal_cost_total"]),
            "gross_margin": float(financials["gross_margin"]),
        },
    }
    return json.dumps(result, default=str)


@mcp.tool()
def calculate_expression(expression: str) -> str:
    """Safely evaluate an arithmetic expression with Decimal precision."""
    if not expression or not expression.strip():
        return json.dumps({"error": "Empty expression"})
    try:
        result = evaluate_decimal_expression(expression.strip())
    except (SyntaxError, ValueError, ZeroDivisionError, InvalidOperation) as exc:
        return json.dumps({"error": str(exc), "expression": expression})
    return json.dumps({"expression": expression, "result": format(result, "f")})


@mcp.tool()
def quote_verify_totals(quote_id: int) -> str:
    """Deterministic pre-send math verification for a quote with billable vs internal separation."""
    raw = json.loads(quote_get(quote_id))
    if raw.get("error"):
        return json.dumps(raw)

    quote = raw["quote"]
    items = raw["items"]
    charges = raw["charges"]
    payments = raw["payments"]
    totals = raw["totals"]

    warnings = []
    informational_notes = []
    if not items:
        warnings.append("Quote has no line items")
    for item in items:
        quantity = int(item.get("quantity") or 0)
        quoted_price = Decimal(str(item.get("quoted_price", 0) or 0))
        if quantity <= 0:
            warnings.append(f"Item {item.get('id')} has non-positive quantity")
        if quoted_price < 0:
            warnings.append(f"Item {item.get('id')} has negative quoted price")
    for charge in charges:
        ctype = (charge.get("type") or "").strip().lower()
        amount = money(charge.get("amount", 0))
        if amount < 0 and ctype not in DISCOUNT_CHARGE_TYPES:
            warnings.append(f"Charge {charge.get('id')} is negative but not typed as discount/credit")
        if ctype in DISCOUNT_CHARGE_TYPES and amount > 0:
            warnings.append(f"Charge {charge.get('id')} is typed as {ctype} but stored as a positive amount")
        if is_internal_charge(ctype):
            informational_notes.append(f"Charge {charge.get('id')} is internal ({ctype}) and excluded from customer total")
    if quote.get("status") in {"Declined", "Completed"}:
        warnings.append(f"Quote status is {quote.get('status')} — not a normal pre-send state")

    verification = {
        "quote_id": quote_id,
        "status": quote.get("status"),
        "customer_name": quote.get("customer_name"),
        "math_verified": True,
        "ready_for_customer_send": len(warnings) == 0,
        "breakdown": {
            "items_subtotal": totals.get("items_subtotal", 0),
            "billable_charges_total": totals.get("billable_charges_total", 0),
            "internal_charges_total": totals.get("internal_charges_total", 0),
            "customer_total": totals.get("customer_total", totals.get("revenue", 0)),
            "paid": totals.get("paid", 0),
            "balance_due": totals.get("balance_due", 0),
            "gross_margin": totals.get("gross_margin", 0),
        },
        "line_items": [
            {
                "item_id": item.get("id"),
                "product_name": item.get("product_name"),
                "quantity": item.get("quantity"),
                "quoted_price": item.get("quoted_price"),
                "extended_price": float(money(Decimal(str(item.get("quoted_price", 0) or 0)) * Decimal(str(item.get("quantity", 0) or 0)))),
            }
            for item in items
        ],
        "charges": [
            {
                "charge_id": charge.get("id"),
                "description": charge.get("description"),
                "type": charge.get("type"),
                "amount": charge.get("amount"),
                "included_in_customer_total": not is_internal_charge(charge.get("type")),
            }
            for charge in charges
        ],
        "payment_count": len(payments),
        "warnings": warnings,
        "informational_notes": informational_notes,
    }
    return json.dumps(verification, default=str)


@mcp.tool()
def quote_cost_worksheet(quote_id: int) -> str:
    """Deterministic worksheet showing billable totals, internal costs, and margin for a quote."""
    raw = json.loads(quote_get(quote_id))
    if raw.get("error"):
        return json.dumps(raw)

    quote = raw["quote"]
    items = raw["items"]
    charges = raw["charges"]
    totals = raw["totals"]

    worksheet_items = []
    for item in items:
        quantity = Decimal(str(item.get("quantity", 0) or 0))
        unit_cost = money(item.get("quoted_cost", 0))
        unit_price = money(item.get("quoted_price", 0))
        worksheet_items.append({
            "item_id": item.get("id"),
            "product_name": item.get("product_name"),
            "quantity": int(quantity),
            "unit_cost": float(unit_cost),
            "unit_price": float(unit_price),
            "extended_cost": float(money(unit_cost * quantity)),
            "extended_price": float(money(unit_price * quantity)),
        })

    worksheet = {
        "quote_id": quote_id,
        "status": quote.get("status"),
        "customer_name": quote.get("customer_name"),
        "items": worksheet_items,
        "billable_charges": [
            charge for charge in charges if not is_internal_charge(charge.get("type"))
        ],
        "internal_charges": [
            charge for charge in charges if is_internal_charge(charge.get("type"))
        ],
        "totals": {
            "items_subtotal": totals.get("items_subtotal", 0),
            "item_cost_total": totals.get("item_cost_total", 0),
            "billable_charges_total": totals.get("billable_charges_total", 0),
            "internal_charges_total": totals.get("internal_charges_total", 0),
            "customer_total": totals.get("customer_total", totals.get("revenue", 0)),
            "internal_cost_total": totals.get("internal_cost_total", 0),
            "gross_margin": totals.get("gross_margin", 0),
        },
    }
    return json.dumps(worksheet, default=str)


@mcp.tool()
def quote_allocate_total(quote_id: int, target_customer_total: float) -> str:
    """Allocate a desired customer total across existing quote items after billable charges."""
    raw = json.loads(quote_get(quote_id))
    if raw.get("error"):
        return json.dumps(raw)

    items = raw["items"]
    totals = raw["totals"]
    if not items:
        return json.dumps({"error": f"Quote {quote_id} has no items to allocate"})

    target_total = money(target_customer_total)
    billable_charges_total = money(totals.get("billable_charges_total", 0))
    allocatable_item_total = money(target_total - billable_charges_total)
    if allocatable_item_total < 0:
        return json.dumps({
            "error": "Target total is less than billable charges",
            "quote_id": quote_id,
            "target_customer_total": float(target_total),
            "billable_charges_total": float(billable_charges_total),
        })

    weights = []
    for item in items:
        quantity = Decimal(str(item.get("quantity", 0) or 0))
        current_extended = money(item.get("quoted_price", 0)) * quantity
        cost_extended = money(item.get("quoted_cost", 0)) * quantity
        weight = current_extended if current_extended > 0 else cost_extended if cost_extended > 0 else quantity
        weights.append(weight)

    total_weight = sum(weights, Decimal("0.00"))
    if total_weight <= 0:
        return json.dumps({"error": f"Quote {quote_id} has no positive allocation weights"})

    provisional = []
    allocated_sum = Decimal("0.00")
    for idx, item in enumerate(items):
        quantity = Decimal(str(item.get("quantity", 0) or 0))
        if idx == len(items) - 1:
            extended = money(allocatable_item_total - allocated_sum)
        else:
            extended = money((allocatable_item_total * weights[idx]) / total_weight)
            allocated_sum += extended
        unit_price = (extended / quantity) if quantity else Decimal("0.00")
        provisional.append({
            "item_id": item.get("id"),
            "product_name": item.get("product_name"),
            "quantity": int(quantity),
            "current_unit_price": float(money(item.get("quoted_price", 0))),
            "suggested_unit_price": float(unit_price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "suggested_extended_price": float(extended),
        })

    return json.dumps({
        "quote_id": quote_id,
        "target_customer_total": float(target_total),
        "billable_charges_total": float(billable_charges_total),
        "allocatable_item_total": float(allocatable_item_total),
        "allocation_basis": "current extended price, fallback to cost, fallback to quantity",
        "items": provisional,
        "notes": [
            "This tool does not write changes; it returns a deterministic allocation plan.",
            "If exact cent-perfect per-unit storage is impossible due to quantity rounding, apply the remainder via last-line adjustment or a dedicated correction step.",
        ],
    }, default=str)





def _infer_service(product_names: list) -> str:
    """Infer the service category from product names."""
    text = " ".join(product_names).lower()
    if any(w in text for w in ["dtf", "shirt", "t-shirt", "tshirt", "apparel", "hat", "cap"]):
        return "dtf_apparel"
    if any(w in text for w in ["window", "install", "graphic", "film", "perf"]):
        return "window_installation"
    if any(w in text for w in ["business card"]):
        return "business_cards"
    if any(w in text for w in ["flyer", "brochure"]):
        return "flyers"
    if any(w in text for w in ["banner", "vinyl", "step repeat"]):
        return "banners"
    if any(w in text for w in ["sign", "yard sign", "a-frame", "coroplast"]):
        return "signage"
    if any(w in text for w in ["label", "sticker", "decal"]):
        return "labels_stickers"
    if any(w in text for w in ["wrap", "vehicle"]):
        return "vehicle_wrap"
    return "other"


def _try_log_quote_outcome(quote_id: int, old_status: str, new_status: str):
    """Insert a quote outcome into quote_outcomes table on terminal status transitions."""
    accepted = {"completed", "approved"}
    declined = {"declined"}
    if new_status.lower() not in accepted and new_status.lower() not in declined:
        return
    outcome = "accepted" if new_status.lower() in accepted else "declined"
    try:
        conn = get_conn()
        try:
            quote = conn.execute(
                "SELECT c.name as customer_name FROM quotes q "
                "JOIN customers c ON q.customer_id = c.id WHERE q.id = ?",
                (quote_id,)
            ).fetchone()
            if not quote:
                return
            customer_name = quote["customer_name"]
            items = conn.execute(
                "SELECT qi.quantity, qi.quoted_price, p.name as product_name "
                "FROM quote_items qi LEFT JOIN products p ON qi.product_id = p.id "
                "WHERE qi.quote_id = ?",
                (quote_id,)
            ).fetchall()
            total = sum((i["quantity"] or 1) * (i["quoted_price"] or 0) for i in items)
            charges = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) as total FROM quote_charges WHERE quote_id = ?",
                (quote_id,)
            ).fetchone()
            total += charges["total"]
            product_names = [i["product_name"].lower() for i in items if i["product_name"]]
            service = _infer_service(product_names)
            decline_reason = None
            if outcome == "declined":
                notes = conn.execute("SELECT notes FROM quotes WHERE id = ?", (quote_id,)).fetchone()
                if notes and notes["notes"]:
                    nl = notes["notes"].lower()
                    for r in ["too expensive", "went with competitor", "not ready", "changed mind", "budget"]:
                        if r in nl:
                            decline_reason = r
                            break
            conn.execute(
                "INSERT INTO quote_outcomes (quote_id, customer_name, service, amount, outcome, decline_reason, old_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (quote_id, customer_name, service, round(total, 2), outcome, decline_reason, old_status)
            )
            conn.commit()
            logger.info(f"Logged outcome: Q-{quote_id} | {service} | ${round(total,2)} | {outcome}")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Auto-log failed for Q-{quote_id}: {e}")



@mcp.tool()
def quote_update_status(quote_id: int, new_status: str, notes: str = None) -> str:
    """Update a quote's status with flexible transition rules.
    Supports business-appropriate transitions:
    Lead → Draft, On Hold, Declined
    Draft → Sent, On Hold, Completed, Declined (Completed for delivered-unpaid orders)
    Sent → Approved, On Hold, Draft (re-edit), Declined
    Approved → In Progress, On Hold, Draft (re-edit), Declined
    On Hold → Draft (re-engage), Sent, Approved, Declined (close out)
    In Progress → Completed, On Hold, Sent (re-send), Draft, Declined
    Completed → Draft, In Progress (reopen)
    Declined → Draft, Lead (reopen)
    Optional notes will be appended to the quote."""
    conn = get_conn()
    try:
        cur = conn.execute("SELECT status FROM quotes WHERE id = ?", (quote_id,))
        row = cur.fetchone()
        if not row:
            return json.dumps({"error": f"Quote {quote_id} not found"})

        old_status = row["status"]
        allowed = VALID_TRANSITIONS.get(old_status, ())

        if new_status not in allowed:
            all_statuses = sorted(set(list(VALID_TRANSITIONS.keys()) + [s for ts in VALID_TRANSITIONS.values() for s in ts]))
            return json.dumps({
                "error": f"Invalid transition: {old_status} → {new_status}",
                "current_status": old_status,
                "allowed_from_current": list(allowed),
                "all_valid_statuses": all_statuses,
            })

        update_fields = ["status = ?"]
        update_params = [new_status]

        if notes:
            current = conn.execute("SELECT notes FROM quotes WHERE id = ?", (quote_id,)).fetchone()
            current_notes = current["notes"] or "" if current else ""
            new_notes = f"{current_notes}\n[{old_status} → {new_status}] {notes}".strip() if current_notes else f"[{old_status} → {new_status}] {notes}"
            update_fields.append("notes = ?")
            update_params.append(new_notes)

        update_params.append(quote_id)
        conn.execute(f"UPDATE quotes SET {', '.join(update_fields)} WHERE id = ?", update_params)
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Quote {quote_id}: {old_status} → {new_status}")
    
    # Auto-log to ledger on terminal status transitions
    _try_log_quote_outcome(quote_id, old_status, new_status)
    
    result = {
        "quote_id": quote_id,
        "old_status": old_status,
        "new_status": new_status,
    }
    if notes:
        result["note_appended"] = f"[{old_status} → {new_status}] {notes}"
    return json.dumps(result)


@mcp.tool()
def quote_update_notes(quote_id: int, notes: str, mode: str = "append") -> str:
    """Append to or replace quote notes. mode='append' or 'replace'."""
    if mode not in {"append", "replace"}:
        return json.dumps({"error": "mode must be 'append' or 'replace'", "mode": mode})
    conn = get_conn()
    try:
        row = conn.execute("SELECT id, notes FROM quotes WHERE id = ?", (quote_id,)).fetchone()
        if not row:
            return json.dumps({"error": f"Quote {quote_id} not found"})
        current_notes = row["notes"] or ""
        if mode == "replace":
            new_notes = notes
        else:
            new_notes = f"{current_notes}\n{notes}".strip() if current_notes else notes
        conn.execute("UPDATE quotes SET notes = ? WHERE id = ?", (new_notes, quote_id))
        conn.commit()
    finally:
        conn.close()
    return json.dumps({"quote_id": quote_id, "mode": mode, "updated": True, "notes": new_notes}, default=str)


@mcp.tool()
def quote_list_open() -> str:
    """All non-completed and non-declined quotes."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """SELECT q.*, c.name AS customer_name
               FROM quotes q
               JOIN customers c ON c.id = q.customer_id
               WHERE q.status NOT IN ('Completed', 'Declined')
               ORDER BY q.created_at DESC"""
        )
        rows = rows_to_dicts(cur)
    finally:
        conn.close()
    return json.dumps(rows, default=str)


@mcp.tool()
def open_orders_report() -> str:
    """Deterministic grouped report of all open quotes with complete status buckets."""
    try:
        raw_rows = json.loads(quote_list_open())
        if isinstance(raw_rows, dict) and raw_rows.get("error"):
            return json.dumps(raw_rows)
        status_buckets = {status: [] for status in OPEN_ORDER_STATUS_BUCKETS}
        counts = {key: 0 for key in status_buckets}

        for row in raw_rows:
            detail = json.loads(quote_get(row["id"]))
            if detail.get("error"):
                continue
            totals = detail.get("totals", {})
            status = row.get("status")
            balance_due = totals.get("balance_due", 0)
            # Exclude Delivered quotes that are fully paid (terminal)
            if status == "Delivered" and balance_due <= 0:
                continue
            item = {
                "quote_id": row["id"],
                "customer_name": row.get("customer_name"),
                "status": status,
                "customer_total": totals.get("customer_total", totals.get("revenue", 0)),
                "paid": totals.get("paid", 0),
                "balance_due": balance_due,
            }
            bucket = status or "Draft"
            if bucket not in status_buckets:
                status_buckets[bucket] = []
                counts[bucket] = 0
            status_buckets[bucket].append(item)
            counts[bucket] += 1

        return json.dumps({
            "total_open": sum(counts.values()),
            "counts_by_status": counts,
            "grouped_orders_by_status": status_buckets,
        }, default=str)
    except Exception as exc:
        logger.error(f"open_orders_report failed: {exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def order_detail_report(quote_id: int) -> str:
    """Detailed deterministic order report with facts, notes, payments, totals, and flags."""
    try:
        raw = json.loads(quote_get(quote_id))
        if raw.get("error"):
            return json.dumps(raw)

        quote = raw["quote"]
        totals = raw["totals"]
        operational_flags = []
        if quote.get("status") == "Draft":
            operational_flags.append("draft_quote")
        if totals.get("balance_due", 0) > 0:
            operational_flags.append("payment_outstanding")
        if totals.get("internal_charges_total", 0) > 0:
            operational_flags.append("internal_costs_logged")
        if "rush" in (quote.get("notes") or "").lower():
            operational_flags.append("rush_order")

        note_timeline = []
        if quote.get("notes"):
            note_timeline.append({"type": "quote_notes", "content": quote.get("notes")})

        return json.dumps({
            "quote_core": quote,
            "customer": {
                "customer_id": quote.get("customer_id"),
                "customer_name": quote.get("customer_name"),
                "email": quote.get("email"),
                "phone": quote.get("phone"),
            },
            "items": raw.get("items", []),
            "charges": raw.get("charges", []),
            "payments": raw.get("payments", []),
            "totals": totals,
            "note_timeline": note_timeline,
            "operational_flags": operational_flags,
        }, default=str)
    except Exception as exc:
        logger.error(f"order_detail_report({quote_id}) failed: {exc}")
        return json.dumps({"error": str(exc), "quote_id": quote_id})


@mcp.tool()
def customer_history_report(customer_id: int) -> str:
    """Deterministic customer history with active/completed splits and recent payments."""
    try:
        raw = json.loads(customer_history(customer_id))
        if raw.get("error"):
            return json.dumps(raw)

        quotes = raw.get("quotes", [])
        active_quotes = [q for q in quotes if q.get("status") not in ("Completed", "Declined")]
        completed_quotes = [q for q in quotes if q.get("status") == "Completed"]
        declined_quotes = [q for q in quotes if q.get("status") == "Declined"]

        conn = get_conn()
        try:
            recent_payments = rows_to_dicts(conn.execute(
                """SELECT p.*, q.customer_id
                   FROM payments p
                   JOIN quotes q ON q.id = p.quote_id
                   WHERE q.customer_id = ?
                   ORDER BY p.payment_date DESC, p.id DESC
                   LIMIT 10""",
                (customer_id,),
            ))
            recent_intake_logs = rows_to_dicts(conn.execute(
                "SELECT * FROM raw_intake_logs WHERE customer_id = ? ORDER BY created_at DESC LIMIT 10",
                (customer_id,),
            ))
        finally:
            conn.close()

        return json.dumps({
            "customer_core": raw.get("customer"),
            "all_quotes": quotes,
            "active_quotes": active_quotes,
            "completed_quotes": completed_quotes,
            "declined_quotes": declined_quotes,
            "outstanding_total": raw.get("total_outstanding", 0),
            "recent_payments": recent_payments,
            "recent_intake_logs": recent_intake_logs,
        }, default=str)
    except Exception as exc:
        logger.error(f"customer_history_report({customer_id}) failed: {exc}")
        return json.dumps({"error": str(exc), "customer_id": customer_id})


@mcp.tool()
def unpaid_orders_report() -> str:
    """All open quotes that still have customer-facing balance due."""
    try:
        open_report = json.loads(open_orders_report())
        if open_report.get("error"):
            return json.dumps(open_report)
        unpaid = []
        for status, rows in open_report.get("grouped_orders_by_status", {}).items():
            for row in rows:
                if float(row.get("balance_due", 0) or 0) > 0:
                    unpaid.append(row)
        return json.dumps({
            "count": len(unpaid),
            "orders": unpaid,
        }, default=str)
    except Exception as exc:
        logger.error(f"unpaid_orders_report failed: {exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def production_queue_report() -> str:
    """Operational queue for approved/in-progress work that is not yet completed."""
    try:
        open_report = json.loads(open_orders_report())
        if open_report.get("error"):
            return json.dumps(open_report)
        queue = []
        for status in PRODUCTION_QUEUE_STATUSES:
            queue.extend(open_report.get("grouped_orders_by_status", {}).get(status, []))
        return json.dumps({
            "count": len(queue),
            "orders": queue,
        }, default=str)
    except Exception as exc:
        logger.error(f"production_queue_report failed: {exc}")
        return json.dumps({"error": str(exc)})

# ────────────────────────────────────────────────────────────────────────────────
#  FINANCIAL  (3 tools)
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def payment_record(
    quote_id: int,
    amount: float,
    method: str = None,
    payment_date: str = None,
    notes: str = None,
) -> str:
    """Record a payment against a quote."""
    if payment_date is None:
        payment_date = now_iso()
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO payments (quote_id, amount, method, payment_date, notes) VALUES (?, ?, ?, ?, ?)",
            (quote_id, amount, method, payment_date, notes),
        )
        conn.commit()
        pid = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Payment recorded: id={pid}, quote={quote_id}, amount={amount}")
    return json.dumps({"payment_id": pid, "quote_id": quote_id, "amount": amount})


@mcp.tool()
def payment_outstanding(customer_id: int) -> str:
    """All outstanding balances for a customer (non-terminal quotes)."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """SELECT q.id AS quote_id, q.status, c.name AS customer_name
               FROM quotes q
               JOIN customers c ON c.id = q.customer_id
               WHERE q.customer_id = ? AND q.status NOT IN ('Completed', 'Declined')""",
            (customer_id,),
        )
        open_quotes = rows_to_dicts(cur)

        results = []
        for q in open_quotes:
            qid = q["quote_id"]
            items = rows_to_dicts(conn.execute(
                "SELECT quantity, quoted_cost, quoted_price FROM quote_items WHERE quote_id = ?",
                (qid,),
            ))
            charges = rows_to_dicts(conn.execute(
                "SELECT amount, type FROM quote_charges WHERE quote_id = ?",
                (qid,),
            ))
            payments = rows_to_dicts(conn.execute(
                "SELECT amount FROM payments WHERE quote_id = ?",
                (qid,),
            ))
            financials = summarize_quote_financials(items, charges, payments)
            balance = financials["customer_balance_due"]
            if balance > 0:
                q["balance_due"] = float(balance)
                results.append(q)
    finally:
        conn.close()
    return json.dumps(results, default=str)


@mcp.tool()
def quote_payment_summary(quote_id: int) -> str:
    """Deterministic financial snapshot for a quote: revenue, paid, and balance due."""
    raw = json.loads(quote_get(quote_id))
    if raw.get("error"):
        return json.dumps(raw)

    quote = raw["quote"]
    totals = raw["totals"]
    result = {
        "quote_id": quote["id"],
        "customer_id": quote["customer_id"],
        "customer_name": quote.get("customer_name"),
        "status": quote.get("status"),
        "currency": "usd",
        "items_subtotal": float(totals.get("items_subtotal", 0) or 0),
        "charges_total": float(totals.get("charges_total", 0) or 0),
        "billable_charges_total": float(totals.get("billable_charges_total", 0) or 0),
        "internal_charges_total": float(totals.get("internal_charges_total", 0) or 0),
        "revenue": float(totals.get("revenue", 0) or 0),
        "paid": float(totals.get("paid", 0) or 0),
        "balance_due": float(totals.get("balance_due", 0) or 0),
        "payable_now": max(float(totals.get("balance_due", 0) or 0), 0.0),
    }
    return json.dumps(result, default=str)


@mcp.tool()
def quote_integrity_check(quote_id: int) -> str:
    """Check a quote for missing fields, suspicious totals, and state mismatches."""
    try:
        raw = json.loads(quote_get(quote_id))
        if raw.get("error"):
            return json.dumps(raw)

        quote = raw["quote"]
        items = raw.get("items", [])
        charges = raw.get("charges", [])
        totals = raw.get("totals", {})
        issues = []
        notes = []

        if not items:
            issues.append("Quote has no items")
        if not quote.get("customer_name"):
            issues.append("Quote has no customer name")
        if totals.get("customer_total", totals.get("revenue", 0)) <= 0:
            issues.append("Customer total is zero or negative")
        if quote.get("status") in {"Approved", "In Progress"} and totals.get("paid", 0) <= 0:
            notes.append("Approved/In Progress quote has no payment recorded")
        if quote.get("status") == "Sent" and totals.get("balance_due", 0) <= 0:
            notes.append("Sent quote has no balance due")
        if totals.get("internal_charges_total", 0) > 0:
            notes.append("Internal charges are present and excluded from customer total")

        for item in items:
            if int(item.get("quantity") or 0) <= 0:
                issues.append(f"Item {item.get('id')} has non-positive quantity")
            if Decimal(str(item.get("quoted_price", 0) or 0)) < 0:
                issues.append(f"Item {item.get('id')} has negative quoted price")
            if not item.get("product_name"):
                notes.append(f"Item {item.get('id')} has no product name attached")
        for charge in charges:
            ctype = (charge.get("type") or "").lower()
            amount = Decimal(str(charge.get("amount", 0) or 0))
            if amount < 0 and ctype not in DISCOUNT_CHARGE_TYPES:
                issues.append(f"Charge {charge.get('id')} is negative but not typed as discount/credit")
            if ctype in DISCOUNT_CHARGE_TYPES and amount > 0:
                issues.append(f"Charge {charge.get('id')} is a positive {ctype}; expected negative discount/credit")
            if amount == 0:
                notes.append(f"Charge {charge.get('id')} has zero amount")

        return json.dumps({
            "quote_id": quote_id,
            "status": quote.get("status"),
            "customer_name": quote.get("customer_name"),
            "ok": len(issues) == 0,
            "issues": issues,
            "notes": notes,
            "totals": totals,
        }, default=str)
    except Exception as exc:
        logger.error(f"quote_integrity_check({quote_id}) failed: {exc}")
        return json.dumps({"error": str(exc), "quote_id": quote_id})


@mcp.tool()
def stripe_health_check() -> str:
    """Check whether Stripe runtime dependencies and API credentials are configured."""
    return json.dumps(get_stripe_health(), default=str)


@mcp.tool()
def stripe_create_product_for_quote(
    quote_id: int,
    name: str = None,
    description: str = None,
) -> str:
    """Create a Stripe product for a quote using the quote/customer context."""
    summary = json.loads(quote_payment_summary(quote_id))
    if summary.get("error"):
        return json.dumps(summary)

    stripe = get_stripe_module()
    product_name = name or quote_stripe_name(quote_id, summary.get("customer_name"))
    product_description = description or (
        f"Customer: {summary.get('customer_name') or 'Unknown'} | Quote #{quote_id}"
    )

    product = stripe.Product.create(
        name=product_name,
        description=product_description,
        metadata={
            "quote_id": str(quote_id),
            "customer_id": str(summary.get("customer_id")),
            "status": str(summary.get("status")),
        },
    )
    return json.dumps(
        {
            "quote_id": quote_id,
            "stripe_product_id": product.id,
            "name": product.name,
            "description": product.description,
        },
        default=str,
    )


@mcp.tool()
def stripe_create_payment_link_for_quote(
    quote_id: int,
    product_name: str = None,
    collect_phone_number: bool = True,
) -> str:
    """Create a Stripe payment link for the current balance due on a quote."""
    summary = json.loads(quote_payment_summary(quote_id))
    if summary.get("error"):
        return json.dumps(summary)

    amount_due = float(summary.get("payable_now", 0) or 0)
    if amount_due <= 0:
        return json.dumps({
            "error": f"Quote {quote_id} has no positive balance due",
            "quote_id": quote_id,
            "payable_now": amount_due,
        })

    stripe = get_stripe_module()
    product = stripe.Product.create(
        name=product_name or quote_stripe_name(quote_id, summary.get("customer_name")),
        description=f"Balance due for quote #{quote_id}",
        metadata={
            "quote_id": str(quote_id),
            "customer_id": str(summary.get("customer_id")),
            "status": str(summary.get("status")),
        },
    )
    unit_amount = int(round(amount_due * 100))
    price = stripe.Price.create(
        product=product.id,
        unit_amount=unit_amount,
        currency="usd",
    )
    payment_link = stripe.PaymentLink.create(
        line_items=[{"price": price.id, "quantity": 1}],
        metadata={
            "quote_id": str(quote_id),
            "customer_id": str(summary.get("customer_id")),
        },
        after_completion={"type": "hosted_confirmation"},
        phone_number_collection={"enabled": bool(collect_phone_number)},
    )

    return json.dumps(
        {
            "quote_id": quote_id,
            "customer_name": summary.get("customer_name"),
            "amount_due": amount_due,
            "amount_cents": unit_amount,
            "currency": "usd",
            "stripe_product_id": product.id,
            "stripe_price_id": price.id,
            "payment_link_id": payment_link.id,
            "payment_link_url": payment_link.url,
        },
        default=str,
    )


@mcp.tool()
def stripe_record_payment_link_metadata(
    quote_id: int,
    payment_link_url: str,
    stripe_product_id: str = None,
    stripe_price_id: str = None,
    payment_link_id: str = None,
) -> str:
    """Append Stripe payment-link metadata to quote notes for auditability."""
    parts = [f"Stripe payment link: {payment_link_url}"]
    if payment_link_id:
        parts.append(f"payment_link_id={payment_link_id}")
    if stripe_product_id:
        parts.append(f"product_id={stripe_product_id}")
    if stripe_price_id:
        parts.append(f"price_id={stripe_price_id}")
    note = " | ".join(parts)
    return quote_update_notes(quote_id=quote_id, notes=note, mode="append")


@mcp.tool()
def vendor_invoice_create(
    vendor_id: int,
    amount: float,
    quote_id: int = None,
    invoice_number: str = None,
) -> str:
    """Create a vendor invoice (accounts payable)."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO vendor_invoices (vendor_id, amount, related_quote_id, invoice_number)
               VALUES (?, ?, ?, ?)""",
            (vendor_id, amount, quote_id, invoice_number),
        )
        conn.commit()
        iid = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Vendor invoice created: id={iid}, vendor={vendor_id}, amount={amount}")
    return json.dumps({"invoice_id": iid, "vendor_id": vendor_id, "amount": amount})


@mcp.tool()
def vendor_invoice_update(
    invoice_id: int,
    vendor_id: int = None,
    amount: float = None,
    quote_id: int = None,
    invoice_number: str = None,
    due_date: str = None,
    status: str = None,
    notes: str = None,
) -> str:
    """Update mutable fields on a vendor invoice. Only provided fields are changed."""
    sets = []
    params = []
    if vendor_id is not None:
        sets.append("vendor_id = ?")
        params.append(vendor_id)
    if amount is not None:
        sets.append("amount = ?")
        params.append(amount)
    if quote_id is not None:
        sets.append("related_quote_id = ?")
        params.append(quote_id)
    if invoice_number is not None:
        sets.append("invoice_number = ?")
        params.append(invoice_number)
    if due_date is not None:
        sets.append("due_date = ?")
        params.append(due_date)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if not sets:
        return json.dumps({"error": "Nothing to update — pass at least one mutable field", "invoice_id": invoice_id})

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM vendor_invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
        if not row:
            return json.dumps({"error": f"Vendor invoice {invoice_id} not found", "invoice_id": invoice_id})
        params.append(invoice_id)
        conn.execute(
            f"UPDATE vendor_invoices SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()
    return json.dumps({"invoice_id": invoice_id, "updated": True})


@mcp.tool()
def vendor_invoice_delete(invoice_id: int) -> str:
    """Delete a vendor invoice by invoice_id."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM vendor_invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
        if not row:
            return json.dumps({"error": f"Vendor invoice {invoice_id} not found", "invoice_id": invoice_id})
        conn.execute("DELETE FROM vendor_invoices WHERE id = ?", (invoice_id,))
        conn.commit()
    finally:
        conn.close()
    return json.dumps({"invoice_id": invoice_id, "deleted": True})

# ────────────────────────────────────────────────────────────────────────────────
#  INTAKE  (4 tools)
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def vendor_order_capture_from_receipt(
    raw_text: str,
    vendor_name: str = None,
    quote_id: int = None,
) -> str:
    """Normalize receipt/order text into vendor-order candidate data before logging."""
    text = (raw_text or "").strip()
    if not text:
        return json.dumps({"error": "Empty receipt/order text"})

    money_tokens = parse_receipt_money_tokens(text)
    line_candidates = receipt_line_candidates(text)
    detected_vendor = vendor_name
    if not detected_vendor:
        vendor_patterns = [
            r"sold by[:\s]+([^\n]+)",
            r"vendor[:\s]+([^\n]+)",
            r"merchant[:\s]+([^\n]+)",
            r"from[:\s]+([^\n]+)",
        ]
        lowered = text.lower()
        if "amazon" in lowered:
            detected_vendor = "Amazon"
        else:
            for pattern in vendor_patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    detected_vendor = m.group(1).strip()[:120]
                    break

    invoice_number = None
    for pattern in [r"invoice[#:\s-]*([A-Z0-9-]+)", r"order[#:\s-]*([A-Z0-9-]+)", r"transaction[#:\s-]*([A-Z0-9-]+)"]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            invoice_number = m.group(1).strip()
            break

    extracted_total = money(sum(Decimal(str(c["line_total"])) for c in line_candidates)) if line_candidates else Decimal("0.00")
    max_money = max(money_tokens) if money_tokens else Decimal("0.00")
    total_source = "line_candidates" if line_candidates else "largest_money_token"
    total_amount = extracted_total if extracted_total > 0 else max_money

    summary = {
        "vendor_name": detected_vendor,
        "quote_id": quote_id,
        "invoice_number": invoice_number,
        "currency": "usd",
        "line_candidates": line_candidates,
        "money_tokens": [float(v) for v in money_tokens],
        "suggested_total": float(total_amount),
        "total_source": total_source,
        "raw_text_preview": text[:500],
        "warnings": [],
    }
    if not detected_vendor:
        summary["warnings"].append("Vendor name not confidently detected")
    if not line_candidates:
        summary["warnings"].append("No line-level receipt candidates found; total derived from money tokens only")
    if len(money_tokens) == 0:
        summary["warnings"].append("No money values detected")

    return json.dumps(summary, default=str)


@mcp.tool()
def intake_log(
    customer_id: int,
    raw_text: str,
    quote_id: int = None,
    source_type: str = "manual",
) -> str:
    """Save raw intake text to raw_intake_logs.  Links to customer and optionally a quote."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO raw_intake_logs (customer_id, quote_id, raw_text, source_type)
               VALUES (?, ?, ?, ?)""",
            (customer_id, quote_id, raw_text, source_type),
        )
        conn.commit()
        log_id = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Intake log created: id={log_id}, customer={customer_id}")
    return json.dumps({"log_id": log_id, "status": "pending"})


@mcp.tool()
def intake_list_pending() -> str:
    """All pending intake logs (status='pending')."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """SELECT r.*, c.name AS customer_name
               FROM raw_intake_logs r
               LEFT JOIN customers c ON c.id = r.customer_id
               WHERE r.status = 'pending'
               ORDER BY r.created_at DESC"""
        )
        rows = rows_to_dicts(cur)
    finally:
        conn.close()
    return json.dumps(rows, default=str)


@mcp.tool()
def intake_update(
    intake_id: int,
    status: str = None,
    quote_id: int = None,
    notes: str = None,
) -> str:
    """Update an intake log — change status, link to a quote, or add notes.
    
    Args:
        intake_id: The intake log ID to update
        status: New status — 'completed', 'cancelled', 'converted', 'pending'
        quote_id: Link this intake to a quote (e.g., after converting lead to quote)
        notes: Add notes to parsed_data
    """
    conn = get_conn()
    try:
        # Verify intake exists
        existing = conn.execute("SELECT id, status FROM raw_intake_logs WHERE id = ?", (intake_id,)).fetchone()
        if not existing:
            return json.dumps({"error": f"Intake {intake_id} not found"})
        
        updates = []
        params = []
        
        if status:
            valid_statuses = ("pending", "completed", "cancelled", "converted")
            if status not in valid_statuses:
                return json.dumps({"error": f"Invalid status '{status}'. Valid: {valid_statuses}"})
            updates.append("status = ?")
            params.append(status)
        
        if quote_id is not None:
            updates.append("quote_id = ?")
            params.append(quote_id)
        
        if notes:
            # Append to existing parsed_data or create new
            existing_data = conn.execute("SELECT parsed_data FROM raw_intake_logs WHERE id = ?", (intake_id,)).fetchone()
            try:
                data = json.loads(existing_data[0]) if existing_data[0] else {}
            except (json.JSONDecodeError, TypeError):
                data = {}
            data["notes"] = notes
            updates.append("parsed_data = ?")
            params.append(json.dumps(data))
        
        if not updates:
            return json.dumps({"error": "No updates specified. Provide status, quote_id, or notes."})
        
        params.append(intake_id)
        conn.execute(f"UPDATE raw_intake_logs SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        
        # Return updated record
        updated = conn.execute(
            """SELECT r.*, c.name AS customer_name
               FROM raw_intake_logs r
               LEFT JOIN customers c ON c.id = r.customer_id
               WHERE r.id = ?""", (intake_id,)
        ).fetchone()
        
        result = {
            "id": updated[0],
            "quote_id": updated[1],
            "customer_id": updated[2],
            "status": updated[4],
            "source_type": updated[6],
            "customer_name": updated[8] if len(updated) > 8 else None,
        }
    finally:
        conn.close()
    
    logger.info(f"Intake {intake_id} updated: {result}")
    return json.dumps(result, default=str)


@mcp.tool()
def intake_parse(raw_text: str) -> str:
    """Parse raw intake text (SMS, voice note) into structured quote data. Returns ready-to-create quote or needs_clarification with questions."""
    result = parse_intake(raw_text)
    # If ready, also save to raw_intake_logs
    if result.get("status") == "ready":
        conn = get_conn()
        try:
            customer_name = result.get("customer")
            customer_id = None
            if customer_name:
                cur = conn.execute(
                    "SELECT id FROM customers WHERE name LIKE ?", (f"%{customer_name}%",)
                )
                row = cur.fetchone()
                if row:
                    customer_id = row[0]
            conn.execute(
                """INSERT INTO raw_intake_logs (customer_id, raw_text, source_type, parsed_data, parsed_confidence, status)
                   VALUES (?, ?, 'manual', ?, 0.8, 'parsed')""",
                (customer_id, raw_text, json.dumps(result)),
            )
            conn.commit()
        finally:
            conn.close()
    return json.dumps(result, default=str)


@mcp.tool()
def intake_clarification_report(raw_text: str = None, quote_id: int = None) -> str:
    """Summarize what is missing from intake text or quote-linked notes before quoting."""
    if not raw_text and quote_id is None:
        return json.dumps({"error": "Provide raw_text or quote_id"})

    source_text = raw_text
    source = "raw_text"
    if quote_id is not None and not source_text:
        quote = json.loads(quote_get(quote_id))
        if quote.get("error"):
            return json.dumps(quote)
        pieces = []
        if quote.get("quote", {}).get("notes"):
            pieces.append(quote["quote"]["notes"])
        for item in quote.get("items", []):
            if item.get("variants_json"):
                pieces.append(str(item.get("variants_json")))
        source_text = "\n".join(pieces).strip()
        source = f"quote:{quote_id}"
    if not source_text:
        return json.dumps({"error": "No intake text available to analyze", "source": source})

    parsed = parse_intake(source_text)
    missing = parsed.get("missing", [])
    question = parsed.get("question")
    status = parsed.get("status")
    return json.dumps({
        "source": source,
        "status": status,
        "missing": missing,
        "question": question,
        "parsed_so_far": parsed.get("parsed_so_far") if status == "needs_clarification" else parsed,
    }, default=str)

# ────────────────────────────────────────────────────────────────────────────────
#  AGENT MEMORY  (5 tools)
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def memory_store(
    scope_type: str,
    scope_id: int,
    memory_type: str,
    content: str,
    source: str,
) -> str:
    """Store an agent memory.
    scope_type: 'customer', 'product', 'vendor', 'global', 'behavioral'
    scope_id: NULL (use -1) for global/behavioral
    memory_type: 'preference', 'pattern', 'observation', 'behavioral_flag'
    source: 'observed', 'user_confirmed', 'inferred'
    """
    # Allow caller to pass -1 to mean NULL for global/behavioral scopes
    sid = None if scope_id == -1 else scope_id
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO agent_memory (scope_type, scope_id, memory_type, content, source)
               VALUES (?, ?, ?, ?, ?)""",
            (scope_type, sid, memory_type, content, source),
        )
        conn.commit()
        mid = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"Memory stored: id={mid}, {scope_type}/{sid} [{memory_type}]")
    return json.dumps({"memory_id": mid})


@mcp.tool()
def memory_recall(scope_type: str, scope_id: int) -> str:
    """All memories for a given scope (type + id)."""
    sid = None if scope_id == -1 else scope_id
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM agent_memory WHERE scope_type = ? AND scope_id IS ? ORDER BY created_at DESC",
            (scope_type, sid),
        )
        rows = rows_to_dicts(cur)
    finally:
        conn.close()
    return json.dumps(rows, default=str)


@mcp.tool()
def memory_recall_customer(customer_id: int) -> str:
    """Customer memories + global + behavioural flags."""
    conn = get_conn()
    try:
        cur = conn.execute(
            """SELECT * FROM agent_memory
               WHERE (scope_type = 'customer' AND scope_id = ?)
                  OR (scope_type = 'global')
                  OR (scope_type = 'behavioral')
               ORDER BY created_at DESC""",
            (customer_id,),
        )
        rows = rows_to_dicts(cur)
    finally:
        conn.close()
    return json.dumps(rows, default=str)


@mcp.tool()
def memory_update(memory_id: int, content: str = None, confidence: float = None) -> str:
    """Update an existing memory's content and/or confidence."""
    sets = []
    params: list = []
    if content is not None:
        sets.append("content = ?")
        params.append(content)
    if confidence is not None:
        sets.append("confidence = ?")
        params.append(confidence)
    if not sets:
        return json.dumps({"error": "Nothing to update — pass content and/or confidence"})

    sets.append("last_relevant = ?")
    params.append(now_iso())
    params.append(memory_id)

    conn = get_conn()
    try:
        conn.execute(
            f"UPDATE agent_memory SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()
    return json.dumps({"memory_id": memory_id, "updated": True})


@mcp.tool()
def memory_search(query: str) -> str:
    """LIKE search across memory content."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT * FROM agent_memory WHERE content LIKE ? ORDER BY created_at DESC LIMIT 50",
            (f"%{query}%",),
        )
        rows = rows_to_dicts(cur)
    finally:
        conn.close()
    if not rows:
        return json.dumps({"error": "No memories found", "query": query})
    return json.dumps(rows, default=str)

# ────────────────────────────────────────────────────────────────────────────────
#  SCOPED CONTEXT — "THE LENS"  (3 tools)
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def scope_customer(customer_id: int) -> str:
    """Load full customer scope: quotes, payments, balance, products, vendors, related customers, memories. Layers 0-3."""
    result = load_customer_scope(customer_id)
    return json.dumps(result, default=str)


@mcp.tool()
def scope_order(quote_id: int) -> str:
    """Load full order scope: items, charges, payments, vendors, similar past orders, intake logs. Layers 0-3."""
    result = load_order_scope(quote_id)
    return json.dumps(result, default=str)


@mcp.tool()
def scope_product(product_id: int) -> str:
    """Load full product scope: orders, vendors, customers, margin benchmarks, memories. Layers 0-3."""
    result = load_product_scope(product_id)
    return json.dumps(result, default=str)


# ────────────────────────────────────────────────────────────────────────────────
#  HEALTH CHECK  (bonus)
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def health_check() -> str:
    """Verify database connectivity and table presence."""
    conn = get_conn()
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
    finally:
        conn.close()
    expected = {
        "agent_memory", "customers", "payments", "products",
        "quote_charges", "quote_items", "quotes", "raw_intake_logs",
        "vendor_invoices", "vendor_products", "vendors",
    }
    found = set(tables) & expected
    missing = expected - found
    return json.dumps({
        "status": "ok" if not missing else "degraded",
        "tables_found": sorted(found),
        "tables_missing": sorted(missing),
        "db_path": DB_PATH,
    })

# ─── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting Print Junkie AZ MCP Server …")
    mcp.run()
