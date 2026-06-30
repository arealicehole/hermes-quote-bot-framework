"""
Print Junkie AZ — Scoped Context System ("The Lens")
Loads layered context when user focuses on a customer, order, or product.

Layer 0: FOCUS (the thing you're talking about)
Layer 1: DIRECT (its own data — quotes, payments, details)
Layer 2: CONNECTED (related entities — products, vendors, other customers)
Layer 3: CONTEXTUAL (trends, benchmarks, cross-entity patterns)
"""

import json
import sqlite3
from typing import Optional
from datetime import datetime

DB_PATH = "/home/ice/quote-bot-mcp/data/quotes.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


# ─── CUSTOMER SCOPE ────────────────────────────────────────────────────────────

def load_customer_scope(customer_id: int) -> dict:
    """
    Load all layers for a customer scope.
    Layer 1: Customer's quotes, payments, balance
    Layer 2: Products ordered, vendors, other customers with same products
    Layer 3: Margin benchmarks, pricing patterns
    """
    conn = get_conn()
    try:
        # Layer 0: Customer
        cur = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,))
        customer = cur.fetchone()
        if not customer:
            return {"error": f"Customer {customer_id} not found"}
        customer = dict(customer)

        # Layer 1: Direct — quotes with items
        cur = conn.execute("""
            SELECT q.*, 
                   COALESCE(SUM(qi.quoted_price * qi.quantity), 0) as total_revenue,
                   COALESCE(SUM(qi.quoted_cost * qi.quantity), 0) as total_cost
            FROM quotes q
            LEFT JOIN quote_items qi ON qi.quote_id = q.id
            WHERE q.customer_id = ?
            GROUP BY q.id
            ORDER BY q.created_at DESC
        """, (customer_id,))
        quotes = rows_to_dicts(cur)

        # Layer 1: Payments
        cur = conn.execute("""
            SELECT p.*, q.id as quote_number
            FROM payments p
            JOIN quotes q ON q.id = p.quote_id
            WHERE q.customer_id = ?
            ORDER BY p.payment_date DESC
        """, (customer_id,))
        payments = rows_to_dicts(cur)

        # Layer 1: Outstanding balance
        cur = conn.execute("""
            SELECT q.id as quote_id, q.status,
                   COALESCE(SUM(qi.quoted_price * qi.quantity), 0) +
                   COALESCE((SELECT SUM(amount) FROM quote_charges WHERE quote_id = q.id), 0) -
                   COALESCE((SELECT SUM(amount) FROM payments WHERE quote_id = q.id), 0) as balance
            FROM quotes q
            LEFT JOIN quote_items qi ON qi.quote_id = q.id
            WHERE q.customer_id = ? AND q.status NOT IN ('Completed', 'Declined')
            GROUP BY q.id
            HAVING balance > 0
        """, (customer_id,))
        outstanding = rows_to_dicts(cur)
        total_outstanding = sum(r["balance"] for r in outstanding)

        # Layer 2: Products this customer has ordered
        cur = conn.execute("""
            SELECT DISTINCT p.id, p.name, p.standard_cost, p.list_price,
                   COUNT(qi.id) as times_ordered,
                   AVG(qi.quoted_price) as avg_sell_price,
                   AVG(qi.quoted_cost) as avg_cost
            FROM products p
            JOIN quote_items qi ON qi.product_id = p.id
            JOIN quotes q ON q.id = qi.quote_id
            WHERE q.customer_id = ?
            GROUP BY p.id
            ORDER BY times_ordered DESC
        """, (customer_id,))
        products_ordered = rows_to_dicts(cur)

        # Layer 2: Vendors who supplied products this customer ordered
        cur = conn.execute("""
            SELECT DISTINCT v.id, v.name, v.contact_info
            FROM vendors v
            JOIN vendor_products vp ON vp.vendor_id = v.id
            JOIN products p ON p.id = vp.product_id
            JOIN quote_items qi ON qi.product_id = p.id
            JOIN quotes q ON q.id = qi.quote_id
            WHERE q.customer_id = ?
        """, (customer_id,))
        vendors = rows_to_dicts(cur)

        # Layer 2: Other customers who ordered the same products
        cur = conn.execute("""
            SELECT DISTINCT c.id, c.name, p.name as product_name
            FROM customers c
            JOIN quotes q ON q.customer_id = c.id
            JOIN quote_items qi ON qi.quote_id = q.id
            JOIN products p ON p.id = qi.product_id
            WHERE p.id IN (
                SELECT DISTINCT qi2.product_id 
                FROM quote_items qi2 
                JOIN quotes q2 ON q2.id = qi2.quote_id 
                WHERE q2.customer_id = ?
            ) AND c.id != ?
            ORDER BY c.name
        """, (customer_id, customer_id))
        related_customers = rows_to_dicts(cur)

        # Layer 3: Raw intake logs for this customer
        cur = conn.execute("""
            SELECT * FROM raw_intake_logs 
            WHERE customer_id = ? 
            ORDER BY created_at DESC LIMIT 10
        """, (customer_id,))
        intake_logs = rows_to_dicts(cur)

        # Layer 3: Agent memories for this customer
        cur = conn.execute("""
            SELECT * FROM agent_memory 
            WHERE (scope_type = 'customer' AND scope_id = ?) 
               OR scope_type = 'global' 
               OR scope_type = 'behavioral'
            ORDER BY created_at DESC
        """, (customer_id,))
        memories = rows_to_dicts(cur)

    finally:
        conn.close()

    return {
        "scope": "customer",
        "layer_0_focus": customer,
        "layer_1_direct": {
            "quotes": quotes,
            "payments": payments,
            "outstanding_balance": total_outstanding,
            "open_quotes": outstanding,
        },
        "layer_2_connected": {
            "products_ordered": products_ordered,
            "vendors": vendors,
            "related_customers": related_customers,
        },
        "layer_3_contextual": {
            "intake_logs": intake_logs,
            "memories": memories,
        },
    }


# ─── ORDER SCOPE ───────────────────────────────────────────────────────────────

def load_order_scope(quote_id: int) -> dict:
    """
    Load all layers for an order scope.
    Layer 1: Quote items, charges, payments, status history
    Layer 2: Product details, vendor info, similar past orders
    Layer 3: Margin benchmarks for these product types
    """
    conn = get_conn()
    try:
        # Layer 0: Quote
        cur = conn.execute("""
            SELECT q.*, c.name as customer_name
            FROM quotes q
            JOIN customers c ON c.id = q.customer_id
            WHERE q.id = ?
        """, (quote_id,))
        quote = cur.fetchone()
        if not quote:
            return {"error": f"Quote {quote_id} not found"}
        quote = dict(quote)
        customer_id = quote["customer_id"]

        # Layer 1: Items with product details
        cur = conn.execute("""
            SELECT qi.*, p.name as product_name, p.standard_cost, p.list_price,
                   p.description as product_description
            FROM quote_items qi
            JOIN products p ON p.id = qi.product_id
            WHERE qi.quote_id = ?
        """, (quote_id,))
        items = rows_to_dicts(cur)

        # Layer 1: Charges
        cur = conn.execute("SELECT * FROM quote_charges WHERE quote_id = ?", (quote_id,))
        charges = rows_to_dicts(cur)

        # Layer 1: Payments
        cur = conn.execute("SELECT * FROM payments WHERE quote_id = ?", (quote_id,))
        payments = rows_to_dicts(cur)

        # Calculate totals
        items_total = sum(
            (float(r.get("quoted_price", 0) or 0) * float(r.get("quantity", 1) or 1))
            for r in items
        )
        charges_total = sum(float(r.get("amount", 0) or 0) for r in charges)
        payments_total = sum(float(r.get("amount", 0) or 0) for r in payments)
        balance = items_total + charges_total - payments_total

        # Layer 2: Vendor info for products in this quote
        product_ids = [r["product_id"] for r in items if r.get("product_id")]
        vendors = []
        if product_ids:
            placeholders = ",".join("?" * len(product_ids))
            cur = conn.execute(f"""
                SELECT DISTINCT v.*, vp.vendor_cost, vp.vendor_sku, p.name as product_name
                FROM vendors v
                JOIN vendor_products vp ON vp.vendor_id = v.id
                JOIN products p ON p.id = vp.product_id
                WHERE vp.product_id IN ({placeholders})
            """, product_ids)
            vendors = rows_to_dicts(cur)

        # Layer 2: Similar past orders (same customer, same products)
        similar = []
        if product_ids:
            cur = conn.execute(f"""
                SELECT q.id as quote_id, q.status, q.created_at,
                       qi.quoted_price, qi.quantity, p.name as product_name
                FROM quotes q
                JOIN quote_items qi ON qi.quote_id = q.id
                JOIN products p ON p.id = qi.product_id
                WHERE q.customer_id = ? AND qi.product_id IN ({placeholders}) AND q.id != ?
                ORDER BY q.created_at DESC LIMIT 10
            """, [customer_id] + product_ids + [quote_id])
            similar = rows_to_dicts(cur)

        # Layer 3: Intake logs for this quote
        cur = conn.execute("""
            SELECT * FROM raw_intake_logs WHERE quote_id = ? ORDER BY created_at
        """, (quote_id,))
        intake_logs = rows_to_dicts(cur)

    finally:
        conn.close()

    return {
        "scope": "order",
        "layer_0_focus": quote,
        "layer_1_direct": {
            "items": items,
            "charges": charges,
            "payments": payments,
            "total_revenue": items_total + charges_total,
            "total_paid": payments_total,
            "balance": balance,
        },
        "layer_2_connected": {
            "vendors": vendors,
            "similar_past_orders": similar,
        },
        "layer_3_contextual": {
            "intake_logs": intake_logs,
        },
    }


# ─── PRODUCT SCOPE ─────────────────────────────────────────────────────────────

def load_product_scope(product_id: int) -> dict:
    """
    Load all layers for a product scope.
    Layer 1: Product details, cost/price history
    Layer 2: All customers who ordered it, vendors who supply it
    Layer 3: Margin benchmarks, pricing trends, popularity
    """
    conn = get_conn()
    try:
        # Layer 0: Product
        cur = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cur.fetchone()
        if not product:
            return {"error": f"Product {product_id} not found"}
        product = dict(product)

        # Layer 1: All quotes containing this product
        cur = conn.execute("""
            SELECT q.id, q.status, q.created_at, c.name as customer_name,
                   qi.quantity, qi.quoted_cost, qi.quoted_price,
                   (qi.quoted_price - qi.quoted_cost) as margin_per_unit
            FROM quote_items qi
            JOIN quotes q ON q.id = qi.quote_id
            JOIN customers c ON c.id = q.customer_id
            WHERE qi.product_id = ?
            ORDER BY q.created_at DESC
        """, (product_id,))
        orders = rows_to_dicts(cur)

        # Layer 2: Vendors who supply this product
        cur = conn.execute("""
            SELECT v.*, vp.vendor_cost, vp.vendor_sku
            FROM vendors v
            JOIN vendor_products vp ON vp.vendor_id = v.id
            WHERE vp.product_id = ?
        """, (product_id,))
        vendors = rows_to_dicts(cur)

        # Layer 2: Customers who ordered this
        cur = conn.execute("""
            SELECT DISTINCT c.id, c.name, COUNT(qi.id) as times_ordered,
                   AVG(qi.quoted_price) as avg_price_paid
            FROM customers c
            JOIN quotes q ON q.customer_id = c.id
            JOIN quote_items qi ON qi.quote_id = q.id
            WHERE qi.product_id = ?
            GROUP BY c.id
            ORDER BY times_ordered DESC
        """, (product_id,))
        customers = rows_to_dicts(cur)

        # Layer 3: Margin benchmarks
        if orders:
            margins = [o["margin_per_unit"] for o in orders if o["margin_per_unit"]]
            prices = [o["quoted_price"] for o in orders if o["quoted_price"]]
            avg_margin = sum(margins) / len(margins) if margins else 0
            avg_price = sum(prices) / len(prices) if prices else 0
            margin_pct = (avg_margin / avg_price * 100) if avg_price else 0
        else:
            avg_margin = 0
            avg_price = 0
            margin_pct = 0

        # Layer 3: Memories for this product
        cur = conn.execute("""
            SELECT * FROM agent_memory 
            WHERE scope_type = 'product' AND scope_id = ?
            ORDER BY created_at DESC
        """, (product_id,))
        memories = rows_to_dicts(cur)

    finally:
        conn.close()

    return {
        "scope": "product",
        "layer_0_focus": product,
        "layer_1_direct": {"orders": orders},
        "layer_2_connected": {"vendors": vendors, "customers": customers},
        "layer_3_contextual": {
            "avg_margin_per_unit": round(avg_margin, 2),
            "avg_sell_price": round(avg_price, 2),
            "avg_margin_pct": round(margin_pct, 1),
            "total_orders": len(orders),
            "memories": memories,
        },
    }
