"""
SiJuri Backend — Scraper + REST API
====================================
Scrapes expedientes from sijuri.irsacorp.com.ar and exposes them
as a clean JSON REST API for the SiJuri PWA.

Usage:
  1. Copy .env.example → .env and fill in your credentials
  2. pip install flask requests beautifulsoup4 python-dotenv apscheduler
  3. python app.py

Endpoints:
  GET  /api/cases              → All cases (with optional filters)
  GET  /api/cases/:id          → Single case detail
  GET  /api/stats              → Summary stats
  POST /api/refresh            → Force a re-scrape now
  GET  /api/status             → Scraper health/status
  GET  /health                 → Health check
"""

import os
import json
import time
import hashlib
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from dotenv import load_dotenv

from scraper import SijuriScraper

# ── Config ──────────────────────────────────────────────────────
load_dotenv()

app = Flask(__name__, static_folder="static")
app.config.update(
    SIJURI_URL=os.getenv("SIJURI_URL", "http://sijuri.irsacorp.com.ar"),
    SIJURI_USER=os.getenv("SIJURI_USER", ""),
    SIJURI_PASS=os.getenv("SIJURI_PASS", ""),
    REFRESH_INTERVAL_MINUTES=int(os.getenv("REFRESH_INTERVAL_MINUTES", "15")),
    CACHE_FILE=os.getenv("CACHE_FILE", "data/cases_cache.json"),
    PORT=int(os.getenv("PORT", "3000")),
    DEBUG_MODE=os.getenv("DEBUG_MODE", "false").lower() == "true",
)

logging.basicConfig(
    level=logging.DEBUG if app.config["DEBUG_MODE"] else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("sijuri")

# ── In-memory store ─────────────────────────────────────────────
store = {
    "cases": [],
    "last_refresh": None,
    "last_error": None,
    "refresh_count": 0,
    "is_refreshing": False,
}
store_lock = threading.Lock()


# ── Cache persistence ───────────────────────────────────────────
def save_cache():
    """Persist current cases to disk so restarts don't lose data."""
    cache_path = Path(app.config["CACHE_FILE"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "cases": store["cases"],
                "last_refresh": store["last_refresh"],
                "refresh_count": store["refresh_count"],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    log.info(f"Cache saved: {len(store['cases'])} cases → {cache_path}")


def load_cache():
    """Load cases from disk cache if available."""
    cache_path = Path(app.config["CACHE_FILE"])
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            store["cases"] = data.get("cases", [])
            store["last_refresh"] = data.get("last_refresh")
            store["refresh_count"] = data.get("refresh_count", 0)
            log.info(f"Cache loaded: {len(store['cases'])} cases")
        except Exception as e:
            log.warning(f"Failed to load cache: {e}")


# ── Scraping logic ──────────────────────────────────────────────
def do_refresh():
    """Run the scraper and update the store."""
    if store["is_refreshing"]:
        log.warning("Refresh already in progress, skipping")
        return

    with store_lock:
        store["is_refreshing"] = True

    try:
        url = app.config["SIJURI_URL"]
        user = app.config["SIJURI_USER"]
        passwd = app.config["SIJURI_PASS"]

        if not user or not passwd:
            raise ValueError(
                "SIJURI_USER and SIJURI_PASS must be set in .env"
            )

        log.info(f"Starting scrape of {url} ...")
        scraper = SijuriScraper(base_url=url)
        scraper.login(user, passwd)
        cases = scraper.fetch_all_cases()
        log.info(f"Scrape complete: {len(cases)} cases found")

        # Detect changes
        old_hash = hashlib.md5(
            json.dumps(store["cases"], sort_keys=True).encode()
        ).hexdigest()
        new_hash = hashlib.md5(
            json.dumps(cases, sort_keys=True).encode()
        ).hexdigest()

        with store_lock:
            store["cases"] = cases
            store["last_refresh"] = datetime.utcnow().isoformat() + "Z"
            store["last_error"] = None
            store["refresh_count"] += 1

        if old_hash != new_hash:
            log.info("Data changed — saving cache")
            save_cache()
        else:
            log.info("No changes detected")

    except Exception as e:
        log.error(f"Scrape failed: {e}")
        with store_lock:
            store["last_error"] = str(e)
    finally:
        with store_lock:
            store["is_refreshing"] = False


# ── Background scheduler ────────────────────────────────────────
def start_scheduler():
    """Run do_refresh() on a fixed interval in a daemon thread."""
    interval = app.config["REFRESH_INTERVAL_MINUTES"] * 60

    def loop():
        while True:
            do_refresh()
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    log.info(
        f"Scheduler started: refreshing every "
        f"{app.config['REFRESH_INTERVAL_MINUTES']} min"
    )


# ── CORS middleware ─────────────────────────────────────────────
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


# ══════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()})


@app.route("/api/status")
def api_status():
    return jsonify(
        {
            "total_cases": len(store["cases"]),
            "last_refresh": store["last_refresh"],
            "last_error": store["last_error"],
            "refresh_count": store["refresh_count"],
            "is_refreshing": store["is_refreshing"],
            "refresh_interval_minutes": app.config["REFRESH_INTERVAL_MINUTES"],
        }
    )


@app.route("/api/cases")
def api_cases():
    """
    GET /api/cases?status=activo&estudio=Pellegrini&q=García&limit=20&offset=0
    """
    cases = store["cases"]

    # Filter by status
    status = request.args.get("status")
    if status and status != "todos":
        cases = [c for c in cases if c.get("estado", "").lower() == status.lower()]

    # Filter by estudio
    estudio = request.args.get("estudio")
    if estudio:
        cases = [c for c in cases if estudio.lower() in c.get("estudio", "").lower()]

    # Free-text search
    q = request.args.get("q", "").strip().lower()
    if q:
        cases = [
            c
            for c in cases
            if q in c.get("nombre", "").lower()
            or q in c.get("expediente", "").lower()
            or q in c.get("estudio", "").lower()
        ]

    # Sort by last movement date (most recent first)
    cases = sorted(cases, key=lambda c: c.get("ultimoMov", ""), reverse=True)

    # Pagination
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        limit, offset = 50, 0

    total = len(cases)
    cases = cases[offset : offset + limit]

    return jsonify(
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "last_refresh": store["last_refresh"],
            "cases": cases,
        }
    )


@app.route("/api/cases/<case_id>")
def api_case_detail(case_id):
    """GET /api/cases/:id → single case with full movement history."""
    case = next((c for c in store["cases"] if str(c.get("id")) == str(case_id)), None)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    return jsonify({"case": case, "last_refresh": store["last_refresh"]})


@app.route("/api/stats")
def api_stats():
    """GET /api/stats → summary statistics."""
    cases = store["cases"]
    estados = {}
    estudios = {}
    fueros = {}

    for c in cases:
        est = c.get("estado", "desconocido")
        estados[est] = estados.get(est, 0) + 1

        estudio = c.get("estudio", "Sin estudio")
        estudios[estudio] = estudios.get(estudio, 0) + 1

        fuero = c.get("fuero", "Sin fuero")
        fueros[fuero] = fueros.get(fuero, 0) + 1

    return jsonify(
        {
            "total": len(cases),
            "by_status": estados,
            "by_estudio": estudios,
            "by_fuero": fueros,
            "last_refresh": store["last_refresh"],
        }
    )


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """POST /api/refresh → trigger an immediate re-scrape."""
    if store["is_refreshing"]:
        return jsonify({"message": "Refresh already in progress"}), 409
    threading.Thread(target=do_refresh, daemon=True).start()
    return jsonify({"message": "Refresh started"})


# ── Serve the PWA frontend ──────────────────────────────────────
@app.route("/")
def serve_index():
    return send_from_directory("static", "index.html")


@app.route("/<path:path>")
def serve_static(path):
    return send_from_directory("static", path)


# ══════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log.info("=" * 50)
    log.info("  SiJuri Backend starting...")
    log.info("=" * 50)

    load_cache()
    start_scheduler()

    app.run(
        host="0.0.0.0",
        port=app.config["PORT"],
        debug=app.config["DEBUG_MODE"],
    )
