#!/usr/bin/env python3
"""
Seed a fresh quotes.db with demo data for a landscaping business.
Proves the framework works for any service business — not just print shops.

Usage:
    python3 seed_demo.py                    # seeds ./data/quotes.db
    python3 seed_demo.py /path/to/db.db     # seeds custom path
"""
import os
import sys
import sqlite3
from datetime import datetime, timezone

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "quotes.db")
    if not os.path.exists(db_path):
        print(f"❌ {db_path} not found. Run init_db.py first.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── Vendors ──────────────────────────────────────────────
    vendors = [
        ("Desert Nursery",     "orders@desertnursery.com"),
        ("Mulch Supply Co.",   "sales@mulchsupply.com"),
        ("Irrigation Parts Plus", "wholesale@irrigparts.com"),
    ]
    for name, contact in vendors:
        conn.execute("INSERT OR IGNORE INTO vendors (name, contact_info) VALUES (?, ?)", (name, contact))
    conn.commit()

    # ── Products ─────────────────────────────────────────────
    products = [
        ("15-Gallon Olive Tree",     "Includes planting labor",            45.0, 120.0),
        ("Premium Mulch (cu yd)",    "Delivered and spread",               22.0,  65.0),
        ("Irrigation Zone Repair",    "Per-zone, parts + labor",            15.0,  85.0),
        ("Desert Sage (5-gallon)",   "Drought-tolerant landscaping",       12.0,  38.0),
        ("Drip Line Installation",  "Per 100ft, parts + labor",           35.0, 110.0),
    ]
    for name, desc, cost, price in products:
        conn.execute("INSERT OR IGNORE INTO products (name, description, standard_cost, list_price) VALUES (?, ?, ?, ?)",
                     (name, desc, cost, price))
    conn.commit()

    # ── Customer ─────────────────────────────────────────────
    conn.execute("""INSERT OR IGNORE INTO customers (name, contact_info, email, phone, address, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                 ("Scottsdale Residence", "Mike", "mike@example.com", "480-555-0142", "4200 N Scottsdale Rd", now_iso()))
    conn.commit()

    # ── Vendor-Product mappings ───────────────────────────────
    vmap = [
        (1, 1, "OLIVE-15G", 45.0),
        (2, 2, "MULCH-PRE", 22.0),
        (3, 3, "IRR-REPAIR", 15.0),
        (1, 4, "SAGE-5G",   12.0),
        (3, 5, "DRIP-100",  35.0),
    ]
    for vid, pid, sku, cost in vmap:
        conn.execute("INSERT OR IGNORE INTO vendor_products (vendor_id, product_id, vendor_sku, vendor_cost) VALUES (?, ?, ?, ?)",
                     (vid, pid, sku, cost))
    conn.commit()

    # ── Summary ───────────────────────────────────────────────
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ["vendors", "products", "customers", "vendor_products"]}
    conn.close()

    print(f"✅ Demo data seeded in {db_path}")
    print(f"   Vendors: {counts['vendors']}  Products: {counts['products']}  Customers: {counts['customers']}  Vendor-Product mappings: {counts['vendor_products']}")
    print(f"\n   Next: python3 run_demo.py {db_path}")

if __name__ == "__main__":
    main()