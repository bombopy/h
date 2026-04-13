#!/usr/bin/env python3
"""hShop API Server"""

from flask import Flask, request, jsonify
from hshop_scraper import hShopScraper
import os

app = Flask(__name__)

@app.route("/")
def index():
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

    scraper = hShopScraper(two_captcha_api_key=api_key_2captcha)
    games = scraper.search_games(query=query, category=category)
    return jsonify({
        "games": games[:limit],
        "query": query,
        "category": category,
        "total": len(games)
    })

@app.route("/download-link/<game_id>")
def download_link(game_id):
    api_key_2captcha = os.environ.get("HSHOP_2CAPTCHA_API_KEY", "")
    scraper = hShopScraper(two_captcha_api_key=api_key_2captcha)
    url = scraper.get_download_link(game_id)
    if url:
        return jsonify({"download_url": url, "game_id": game_id})
    else:
        return jsonify({"error": "Download link not found", "game_id": game_id}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)