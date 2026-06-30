#!/usr/bin/env python3
"""
Print Junkie AZ Quote Bot — HTTP MCP Server
Exposes the same 27+ tools via HTTP on port 3207.
Agents (Herb, Kaizen, Gwen) connect here instead of spawning their own subprocess.

Usage:
    python3 server_http.py

Environment:
    QUOTES_DB_PATH  - path to quotes.db (default: /home/ice/quote-bot-mcp/data/quotes.db)
    MCP_PORT        - HTTP port (default: 3207)
    MCP_HOST        - bind address (default: 127.0.0.1)
"""
import os, sys, logging, sqlite3, json
from datetime import datetime, timezone

# Import the SAME mcp instance from server.py (shares all tools)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("printjunkie-mcp-http")

PORT = int(os.environ.get("MCP_PORT", "3207"))
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
DB_PATH = os.environ.get("QUOTES_DB_PATH", "/home/ice/quote-bot-mcp/data/quotes.db")

if __name__ == "__main__":
    logger.info(f"Starting HTTP MCP on {HOST}:{PORT} ...")
    logger.info(f"DB: {DB_PATH}")

    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from starlette.responses import JSONResponse

    # Health check endpoint
    async def health(request):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            total_quotes = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
            total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM quotes WHERE status='Completed'").fetchone()[0]
            conn.close()
            return JSONResponse({
                "status": "ok",
                "server": "Print Junkie AZ MCP",
                "port": PORT,
                "db": DB_PATH,
                "quotes": total_quotes,
                "customers": total_customers,
                "completed": completed,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=500)

    app = server.mcp.streamable_http_app()

    wrapped_app = Starlette(routes=[
        Route("/health", health),
        Mount("/mcp", app=app),
    ])

    uvicorn.run(wrapped_app, host=HOST, port=PORT, log_level="info")
