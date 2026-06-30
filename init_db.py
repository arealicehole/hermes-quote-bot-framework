#!/usr/bin/env python3
"""
Bootstrap a fresh quotes.db for a new business deployment.
Creates all tables with the correct schema. No data — just structure.

Usage:
    python3 init_db.py                    # creates ./data/quotes.db
    python3 init_db.py /path/to/db.db     # creates at custom path
"""
import os
import sys
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id INTEGER,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL DEFAULT 0.8,
    source TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_relevant DATETIME,
    times_referenced INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    contact_info TEXT,
    created_at TEXT,
    email TEXT,
    phone TEXT,
    address TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    standard_cost REAL,
    list_price REAL
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    contact_info TEXT
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER,
    status TEXT DEFAULT 'Draft',
    created_at TEXT,
    notes TEXT,
    total_sell REAL DEFAULT 0,
    total_cost REAL DEFAULT 0,
    total_discount REAL DEFAULT 0,
    gross_profit REAL DEFAULT 0,
    margin_pct REAL DEFAULT 0,
    FOREIGN KEY (customer_id) REFERENCES customers (id)
);

CREATE TABLE IF NOT EXISTS quote_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER,
    product_id INTEGER,
    quantity INTEGER,
    quoted_cost REAL,
    quoted_price REAL,
    variants TEXT,
    variants_json TEXT,
    FOREIGN KEY (quote_id) REFERENCES quotes (id),
    FOREIGN KEY (product_id) REFERENCES products (id)
);

CREATE TABLE IF NOT EXISTS quote_charges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER,
    description TEXT,
    amount REAL,
    type TEXT,
    notes TEXT,
    FOREIGN KEY (quote_id) REFERENCES quotes (id)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER,
    amount REAL,
    payment_date TEXT,
    method TEXT,
    notes TEXT,
    FOREIGN KEY (quote_id) REFERENCES quotes (id)
);

CREATE TABLE IF NOT EXISTS vendor_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER,
    invoice_number TEXT,
    amount REAL,
    issue_date TEXT,
    due_date TEXT,
    status TEXT DEFAULT 'Unpaid',
    notes TEXT,
    related_quote_id INTEGER,
    FOREIGN KEY (vendor_id) REFERENCES vendors (id),
    FOREIGN KEY (related_quote_id) REFERENCES quotes (id)
);

CREATE TABLE IF NOT EXISTS vendor_products (
    vendor_id INTEGER,
    product_id INTEGER,
    vendor_sku TEXT,
    vendor_cost REAL,
    FOREIGN KEY (vendor_id) REFERENCES vendors (id),
    FOREIGN KEY (product_id) REFERENCES products (id),
    PRIMARY KEY (vendor_id, product_id)
);

CREATE TABLE IF NOT EXISTS raw_intake_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER,
    customer_id INTEGER,
    raw_text TEXT,
    status TEXT DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    source_type TEXT,
    parsed_data TEXT,
    parsed_confidence REAL,
    FOREIGN KEY (quote_id) REFERENCES quotes (id),
    FOREIGN KEY (customer_id) REFERENCES customers (id)
);

CREATE TABLE IF NOT EXISTS quote_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    customer_name TEXT,
    service TEXT,
    amount REAL NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'declined')),
    decline_reason TEXT,
    old_status TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (quote_id) REFERENCES quotes (id)
);

CREATE TABLE IF NOT EXISTS exploratory_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'Researching',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS exploratory_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    vendor_id INTEGER,
    vendor_cost REAL,
    moq INTEGER,
    notes TEXT,
    created_at TEXT,
    FOREIGN KEY (product_id) REFERENCES exploratory_products (id),
    FOREIGN KEY (vendor_id) REFERENCES vendors (id)
);
"""

def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("data", "quotes.db")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    if os.path.exists(db_path):
        print(f"⚠️  {db_path} already exists. Aborting to prevent data loss.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()

    print(f"✅ Fresh database created at {db_path}")
    print(f"   Tables: customers, products, vendors, quotes, quote_items,")
    print(f"           quote_charges, payments, vendor_invoices, vendor_products,")
    print(f"           raw_intake_logs, quote_outcomes, agent_memory,")
    print(f"           exploratory_products, exploratory_quotes")
    print(f"\n   Next: python3 seed_demo.py {db_path}")

if __name__ == "__main__":
    main()