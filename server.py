#!/usr/bin/env python3
"""hShop API Server"""

from flask import Flask, request, jsonify
from hshop_scraper import hShopScraper
import os
import traceback

app = Flask(__name__)

@app.route("/")
def index():
    app.logger.info("[INDEX] Página principal solicitada")
    return """
    <h1>hShop API Server</h1>
    <p>API para buscar y obtener enlaces de descarga de hShop</p>
    <ul>
        <li><b>GET /health</b> – Health check del servidor</li>
        <li><b>GET/POST /search</b> – Buscar juegos</li>
        <li><b>GET /download-link/&lt;game_id&gt;</b> – Obtener enlace de descarga</li>
    </ul>
    """

@app.route("/health")
def health():
    app.logger.info("[HEALTH] Health check solicitado")
    return jsonify({"ok": True})

@app.route("/search", methods=["GET", "POST"])
def search():
    query = request.args.get("query") or request.form.get("query") or ""
    category = request.args.get("category") or request.form.get("category") or "games"
    limit = request.args.get("limit") or request.form.get("limit") or 20
    try:
        limit = int(limit)
    except Exception:
        limit = 20
    limit = max(1, min(limit, 100))
    api_key_2captcha = os.environ.get("HSHOP_2CAPTCHA_API_KEY", "")

    app.logger.info(f"[SEARCH] Parámetros recibidos: query={query!r}, category={category!r}, limit={limit!r}")
    scraper = hShopScraper(two_captcha_api_key=api_key_2captcha)
    app.logger.info("[SEARCH] Instanciado hShopScraper")

    try:
        games = scraper.search_games(query=query, category=category)
        app.logger.info(f"[SEARCH] search_games retornó {len(games)} resultados")
        result = {
            "games": games[:limit],
            "query": query,
            "category": category,
            "total": len(games)
        }
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"[SEARCH][ERROR] Excepción: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e), "games": [], "query": query, "category": category, "total": 0}), 500

@app.route("/download-link/<game_id>")
def download_link(game_id):
    api_key_2captcha = os.environ.get("HSHOP_2CAPTCHA_API_KEY", "")
    scraper = hShopScraper(two_captcha_api_key=api_key_2captcha)
    app.logger.info(f"[DOWNLOAD-LINK] Intentando obtener enlace para game_id={game_id!r}")

    try:
        url = scraper.get_download_link(game_id)
        app.logger.info(f"[DOWNLOAD-LINK] Resultado get_download_link({game_id!r}) → {url!r}")
        if url:
            return jsonify({"download_url": url, "game_id": game_id})
        else:
            app.logger.warning(f"[DOWNLOAD-LINK] No se encontró enlace de descarga para game_id={game_id!r}")
            return jsonify({"error": "Download link not found", "game_id": game_id}), 404
    except Exception as e:
        app.logger.error(f"[DOWNLOAD-LINK][ERROR] Excepción para game_id={game_id!r}: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e), "game_id": game_id}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)