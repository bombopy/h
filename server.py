#!/usr/bin/env python3
"""
hShop API Server con Playwright integrado
Corre en Render.com - acepta requests desde cualquier cliente
"""

import os
import re
import asyncio
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urljoin, quote_plus

from flask import Flask, request, jsonify, send_file
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

app = Flask(__name__)

# Configuración
PORT = int(os.environ.get("PORT", 10000))
BASE_URL = "https://hshop.erista.me"
MAX_WAIT_TIME = 30


class PlaywrightFetcher:
    """Fetcher con Playwright para bypass de Cloudflare"""
    
    def __init__(self):
        self.cookies = None
        self.cookies_timestamp = 0
        self.cookies_ttl = 1800  # 30 minutos
    
    async def fetch(self, url: str, timeout: int = MAX_WAIT_TIME) -> Optional[str]:
        """Obtiene HTML de una URL, resolviendo Cloudflare si es necesario"""
        
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-blink-features=AutomationControlled'
                    ]
                )
                
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    viewport={'width': 1920, 'height': 1080}
                )
                
                # Anti-detección
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)
                
                page = await context.new_page()
                await page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
                
                # Esperar que pase Cloudflare
                try:
                    await page.wait_for_function(
                        """
                        () => {
                            const body = document.body.innerText.toLowerCase();
                            return !body.includes('checking your browser') && 
                                   !body.includes('just a moment');
                        }
                        """,
                        timeout=timeout * 1000
                    )
                    await asyncio.sleep(2)
                except:
                    pass
                
                html = await page.content()
                
                # Guardar cookies
                self.cookies = await context.cookies()
                
                await browser.close()
                return html
                
        except Exception as e:
            print(f"❌ Error fetching {url}: {e}")
            if browser:
                await browser.close()
            return None


class hShopScraper:
    """Scraper de hShop con Playwright"""
    
    def __init__(self):
        self.fetcher = PlaywrightFetcher()
        self.base_url = BASE_URL
    
    async def _fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        """Obtiene y parsea una página"""
        html = await self.fetcher.fetch(url)
        if html:
            return BeautifulSoup(html, 'html.parser')
        return None
    
    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r'\s+', ' ', text or '').strip()
    
    def _extract_games(self, soup: BeautifulSoup) -> List[Dict]:
        """Extrae juegos de la página"""
        games = []
        
        cards = soup.select('a.list-entry.block-link[href^="/t/"]')
        if not cards:
            cards = soup.select('a[href^="/t/"]')
        
        for link in cards:
            href = link.get('href', '')
            if not href or not re.match(r'/t/\d+$', href):
                continue
            
            game_id = href.split('/')[-1]
            
            # Extraer título
            h3_elem = link.select_one('h3')
            title = self._clean_text(h3_elem.get_text() if h3_elem else '')
            if not title:
                title = self._clean_text(link.get('title', ''))
            if not title:
                title = f"Game {game_id}"
            
            # Extraer metadata
            size = "Unknown"
            title_id = "Unknown"
            
            for span in link.select('span'):
                text = self._clean_text(span.get_text())
                if 'MB' in text or 'GB' in text or 'KB' in text:
                    size = text
                elif len(text) == 16 and text.isalnum():
                    title_id = text
            
            games.append({
                'id': game_id,
                'title': title,
                'url': urljoin(self.base_url, href),
                'size': size,
                'title_id': title_id
            })
        
        return games
    
    async def search_games(self, query: str = "", category: str = "games", limit: int = 20) -> List[Dict]:
        """Busca juegos"""
        if query:
            url = f"{self.base_url}/search?q={quote_plus(query)}&category={category}"
        else:
            url = f"{self.base_url}/{category}"
        
        soup = await self._fetch_page(url)
        if not soup:
            return []
        
        games = self._extract_games(soup)
        return games[:limit]
    
    async def get_download_link(self, game_id: str) -> Optional[str]:
        """Obtiene el enlace de descarga de un juego"""
        url = f"{self.base_url}/t/{game_id}"
        
        soup = await self._fetch_page(url)
        if not soup:
            return None
        
        # Buscar enlace de descarga
        download_link = soup.select_one('a[href*="/content/"][href*="token="]')
        if download_link:
            href = download_link.get('href', '')
            if href:
                return urljoin(self.base_url, href)
        
        return None


# Instancia global del scraper
scraper = hShopScraper()


@app.route('/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({"status": "ok", "service": "hshop-api"})


@app.route('/search', methods=['GET', 'POST'])
async def search_games():
    """
    Buscar juegos
    
    GET params o POST body:
    - query: texto de búsqueda (opcional)
    - category: games/dlc/updates/virtual-console (default: games)
    - limit: número máximo de resultados (default: 20)
    
    Response:
    {
        "success": true,
        "games": [
            {
                "id": "12345",
                "title": "Pokemon X",
                "url": "https://hshop.erista.me/t/12345",
                "size": "1.7 GB",
                "title_id": "0004000000055D00"
            }
        ]
    }
    """
    try:
        if request.method == 'POST':
            data = request.get_json() or {}
        else:
            data = request.args.to_dict()
        
        query = data.get('query', '')
        category = data.get('category', 'games')
        limit = int(data.get('limit', 20))
        
        # Ejecutar búsqueda
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        games = loop.run_until_complete(
            scraper.search_games(query, category, limit)
        )
        loop.close()
        
        return jsonify({
            "success": True,
            "count": len(games),
            "games": games
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/download-link/<game_id>', methods=['GET'])
async def get_download_link(game_id: str):
    """
    Obtener enlace de descarga
    
    GET /download-link/12345
    
    Response:
    {
        "success": true,
        "game_id": "12345",
        "download_url": "https://hshop.erista.me/content/12345?token=..."
    }
    """
    try:
        if not game_id.isdigit():
            return jsonify({
                "success": False,
                "error": "Invalid game_id"
            }), 400
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        download_url = loop.run_until_complete(
            scraper.get_download_link(game_id)
        )
        loop.close()
        
        if download_url:
            return jsonify({
                "success": True,
                "game_id": game_id,
                "download_url": download_url
            })
        else:
            return jsonify({
                "success": False,
                "error": "Download link not found"
            }), 404
            
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/', methods=['GET'])
def index():
    """Documentación de la API"""
    return """
    <h1>hShop API Server</h1>
    <p>API para buscar y obtener enlaces de descarga de hShop</p>
    
    <h2>Endpoints:</h2>
    
    <h3>GET /health</h3>
    <p>Health check del servidor</p>
    
    <h3>GET/POST /search</h3>
    <p>Buscar juegos</p>
    <pre>
Params:
  - query: búsqueda (opcional)
  - category: games/dlc/updates/virtual-console (default: games)
  - limit: max resultados (default: 20)

Ejemplo:
  GET /search?query=pokemon&category=games&limit=10
    </pre>
    
    <h3>GET /download-link/:game_id</h3>
    <p>Obtener enlace de descarga</p>
    <pre>
Ejemplo:
  GET /download-link/12345
    </pre>
    
    <h2>Ejemplo de uso desde Python:</h2>
    <pre>
import requests

API_URL = "https://tu-servidor.onrender.com"

# Buscar juegos
response = requests.get(f"{API_URL}/search", params={
    "query": "pokemon",
    "category": "games",
    "limit": 10
})
games = response.json()["games"]

# Obtener enlace de descarga
game_id = games[0]["id"]
response = requests.get(f"{API_URL}/download-link/{game_id}")
download_url = response.json()["download_url"]

# Descargar el archivo
response = requests.get(download_url, stream=True)
with open("game.cia", "wb") as f:
    for chunk in response.iter_content(chunk_size=8192):
        f.write(chunk)
    </pre>
    """


if __name__ == '__main__':
    print(f"🚀 hShop API Server iniciando en puerto {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
