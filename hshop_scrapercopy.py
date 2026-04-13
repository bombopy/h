#!/usr/bin/env python3
"""
hShop Scraper & Downloader
Busca y descarga juegos de 3DS desde hShop.erista.me

Requiere: pip install scrapling requests tqdm
"""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import urljoin
from urllib.parse import quote_plus
from typing import List, Dict, Optional

try:
    from scrapling.fetchers import StealthyFetcher
    import requests
    from tqdm import tqdm
except ImportError:
    print("❌ Error: Faltan dependencias")
    print("Instala con: pip install scrapling requests tqdm")
    sys.exit(1)


class hShopScraper:
    """Scraper para hShop con descarga automática"""
    
    BASE_URL = "https://hshop.erista.me"
    
    def __init__(self, download_dir: str = "./downloads"):
        """
        Inicializa el scraper
        
        Args:
            download_dir: Directorio donde guardar las descargas
        """
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)
        print(f"📁 Directorio de descargas: {self.download_dir.absolute()}")

    @staticmethod
    def _safe_filename(name: str) -> str:
        clean = re.sub(r'[^\w\-. ]', '_', name).strip()
        return clean or "download.bin"

    @staticmethod
    def _filename_from_url(url: str) -> str:
        path = urlparse(url).path
        if not path or path.endswith("/"):
            return "download.bin"
        return hShopScraper._safe_filename(path.split("/")[-1])

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    def _extract_games_from_page(self, page) -> List[Dict]:
        games = []
        game_links = page.css('a[href^="/t/"]')

        for link in game_links:
            href = link.attrib.get("href", "")
            if not href or not re.match(r"/t/\d+$", href):
                continue

            game_id = href.split("/")[-1]

            text_nodes = link.css("::text").getall() if link.css("::text") else []
            title = ""
            for node in text_nodes:
                candidate = self._clean_text(node)
                if candidate and candidate.lower() not in {"also known as:", "id", "size", "title id"}:
                    title = candidate
                    break

            if not title and text_nodes:
                title = self._clean_text(" ".join(text_nodes))

            if not title:
                title = self._clean_text(link.attrib.get("title", ""))
            if not title:
                title = self._clean_text(link.attrib.get("aria-label", ""))
            if not title:
                title = f"Game {game_id}"

            parent = link.parent
            if parent:
                meta_text = parent.css("::text").getall()
                meta_str = self._clean_text(" ".join(meta_text))

                size_match = re.search(r"(\d+\.?\d*)\s*(GiB|MiB|KiB)", meta_str, re.IGNORECASE)
                size = size_match.group(0) if size_match else "N/A"

                tid_match = re.search(r"([0-9A-F]{16})", meta_str)
                title_id = tid_match.group(1) if tid_match else "N/A"
            else:
                size = "N/A"
                title_id = "N/A"

            games.append(
                {
                    "id": game_id,
                    "title": title,
                    "size": size,
                    "title_id": title_id,
                    "url": urljoin(self.BASE_URL, href),
                }
            )

        seen = set()
        unique_games = []
        for game in games:
            if game["id"] in seen:
                continue
            seen.add(game["id"])
            unique_games.append(game)
        return unique_games

    @staticmethod
    def _extract_direct_download_from_page(page, base_url: str) -> Optional[str]:
        for link in page.css("a[href]"):
            href = (link.attrib.get("href") or "").strip()
            if not href:
                continue

            lower_href = href.lower()
            text = " ".join(link.css("::text").getall()).lower()

            if any(key in lower_href for key in [".cia", "cdn", "/download/"]):
                return href if href.startswith("http") else urljoin(base_url, href)

            if "download" in text and ("cia" in text or "archive" in text):
                return href if href.startswith("http") else urljoin(base_url, href)

        for elem in page.css("[data-download-url]"):
            data_url = (elem.attrib.get("data-download-url") or "").strip()
            if data_url and ".cia" in data_url.lower():
                return data_url if data_url.startswith("http") else urljoin(base_url, data_url)

        return None
    
    def search_games(self, query: str = "", category: str = "games") -> List[Dict]:
        """
        Busca juegos en hShop
        
        Args:
            query: Término de búsqueda (vacío = top games)
            category: Categoría (games, updates, dlc, virtual-console, etc.)
        
        Returns:
            Lista de diccionarios con información de juegos
        """
        print(f"\n🔍 Buscando en hShop: '{query}' en categoría '{category}'...")
        
        category_map = {
            "games": "games",
            "updates": "updates",
            "dlc": "dlc",
            "virtual-console": "virtual-console",
            "dsiware": "dsiware",
            "videos": "videos",
            "extras": "extras",
            "themes": "themes",
        }

        if query:
            encoded_query = quote_plus(query)
            search_type = category_map.get(category.lower().strip(), "all")
            url = (
                f"{self.BASE_URL}/search/results?"
                f"q={encoded_query}&qt=Text&type={search_type}&sort=likes&order=desc"
            )
        else:
            url = f"{self.BASE_URL}/c/{category}"
        
        try:
            page = StealthyFetcher.fetch(url, headless=True, network_idle=True, solve_cloudflare=True)
            games = self._extract_games_from_page(page)

            print(f"✅ Encontrados {len(games)} juegos")
            return games
            
        except Exception as e:
            print(f"❌ Error al buscar: {e}")
            return []
    
    def get_download_link(self, game_id: str) -> Optional[str]:
        """
        Obtiene el enlace de descarga directo de un juego
        
        Args:
            game_id: ID del juego en hShop
        
        Returns:
            URL de descarga o None
        """
        try:
            url = f"{self.BASE_URL}/t/{game_id}"
            print(f"\n📥 Obteniendo enlace de descarga para ID {game_id}...")

            page_state = {"token": "", "widget_url": ""}

            def open_download_widget(page_obj):
                # Espera a que turnstile complete y luego abre el widget con token.
                page_obj.wait_for_timeout(2500)
                token = ""
                token_field = page_obj.locator('input[name="cf-turnstile-response"]')
                if token_field.count() > 0:
                    token = token_field.input_value() or ""

                page_state["token"] = token
                if not token:
                    return

                widget_url = f"{url.rstrip('/')}/download-widget?captcha_token={token}"
                page_state["widget_url"] = widget_url
                page_obj.goto(widget_url)
                page_obj.wait_for_load_state("networkidle")

            page = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                solve_cloudflare=True,
                page_action=open_download_widget,
            )

            direct_url = self._extract_direct_download_from_page(page, self.BASE_URL)
            if direct_url:
                return direct_url

            token = page_state.get("token", "")
            if token:
                widget_url = page_state.get("widget_url") or f"{url}/download-widget?captcha_token={token}"
                try:
                    widget_response = requests.get(widget_url, timeout=30)
                    widget_response.raise_for_status()
                    cia_match = re.search(r'https?://[^\s"\']+\.cia(?:\?[^\s"\']*)?', widget_response.text, re.IGNORECASE)
                    if cia_match:
                        return cia_match.group(0)
                except Exception:
                    pass

            print("⚠️  No se encontró enlace de descarga directo")
            print("    El captcha/token puede requerir resolución manual")
            return None
            
        except Exception as e:
            print(f"❌ Error al obtener enlace: {e}")
            return None

    def print_download_link(self, game_id: str, title: str = None) -> bool:
        """
        Obtiene y muestra el enlace de descarga directo de un juego.

        Args:
            game_id: ID del juego
            title: Título del juego (opcional)

        Returns:
            True si el enlace fue encontrado
        """
        download_url = self.get_download_link(game_id)

        if not download_url:
            return False

        print("\n🔗 Enlace de descarga directo:")
        if title:
            print(f"🎮 {title}")
        print(download_url)
        return True

    def download_file(self, url: str, filename: Optional[str] = None) -> bool:
        """Descarga un archivo desde una URL directa con barra de progreso."""
        if not filename:
            filename = self._filename_from_url(url)

        filename = self._safe_filename(filename)
        filepath = self.download_dir / filename

        if filepath.exists():
            print(f"⏭️  Ya existe: {filepath.name}")
            return True

        try:
            print(f"\n⬇️  Descargando: {filepath.name}")
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            with open(filepath, "wb") as file_obj, tqdm(
                desc=filepath.name,
                total=total_size if total_size > 0 else None,
                unit="iB",
                unit_scale=True,
                unit_divisor=1024,
            ) as progress:
                for chunk in response.iter_content(chunk_size=1024 * 128):
                    if not chunk:
                        continue
                    written = file_obj.write(chunk)
                    progress.update(written)

            print(f"✅ Descarga completada: {filepath}")
            return True
        except Exception as error:
            print(f"❌ Error al descargar: {error}")
            if filepath.exists():
                filepath.unlink()
            return False

    def download_game_by_id(self, game_id: str, title: Optional[str] = None) -> bool:
        """Obtiene enlace del item y descarga directamente el archivo."""
        download_url = self.get_download_link(game_id)
        if not download_url:
            return False

        default_name = None
        if title:
            base = self._safe_filename(title)
            default_name = f"{base}_{game_id}.cia"

        return self.download_file(download_url, default_name)


def show_games(games: List[Dict], max_show: int = 20):
    """Muestra lista de juegos encontrados"""
    print("\n" + "="*80)
    print("📋 JUEGOS ENCONTRADOS:")
    print("="*80)
    
    for i, game in enumerate(games[:max_show], 1):
        print(f"\n{i}. {game['title']}")
        print(f"   ID: {game['id']} | Size: {game['size']} | TitleID: {game['title_id']}")
        print(f"   URL: {game['url']}")
    
    if len(games) > max_show:
        print(f"\n... y {len(games) - max_show} más")
    
    print("\n" + "="*80)


def interactive_mode():
    """Modo interactivo para buscar y descargar"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║           hSHOP SCRAPER & DOWNLOADER v1.0                    ║
║           Descarga automática de juegos 3DS                  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    scraper = hShopScraper()
    
    while True:
        print("\n┌─ MENÚ PRINCIPAL")
        print("│")
        print("│ 1. Buscar juegos")
        print("│ 2. Ver juegos populares")
        print("│ 3. Descargar por ID")
        print("│ 4. Descargar desde URL directa")
        print("│ 5. Salir")
        print("└─")
        
        choice = input("\nSelecciona una opción: ").strip()
        
        if choice == "1":
            query = input("\n🔍 Ingresa búsqueda: ").strip()
            category = input("📁 Categoría (games/dlc/updates/virtual-console) [games]: ").strip() or "games"
            
            games = scraper.search_games(query, category)
            
            if games:
                show_games(games)
                
                download_choice = input("\nBotón descargar (número o 'n'): ").strip()
                if download_choice.isdigit():
                    idx = int(download_choice) - 1
                    if 0 <= idx < len(games):
                        game = games[idx]
                        link = scraper.get_download_link(game['id'])
                        if link:
                            print("\n🔗 Enlace generado:")
                            print(f"🎮 {game['title']}")
                            print(link)
                            should_download = input("\n¿Descargar ahora? (s/n): ").strip().lower()
                            if should_download in ("s", "si", "y", "yes"):
                                filename = f"{scraper._safe_filename(game['title'])}_{game['id']}.cia"
                                scraper.download_file(link, filename)
        
        elif choice == "2":
            games = scraper.search_games(query="", category="games")
            
            if games:
                show_games(games, max_show=30)
                
                download_choice = input("\nBotón descargar (número o 'n'): ").strip()
                if download_choice.isdigit():
                    idx = int(download_choice) - 1
                    if 0 <= idx < len(games):
                        game = games[idx]
                        link = scraper.get_download_link(game['id'])
                        if link:
                            print("\n🔗 Enlace generado:")
                            print(f"🎮 {game['title']}")
                            print(link)
                            should_download = input("\n¿Descargar ahora? (s/n): ").strip().lower()
                            if should_download in ("s", "si", "y", "yes"):
                                filename = f"{scraper._safe_filename(game['title'])}_{game['id']}.cia"
                                scraper.download_file(link, filename)
        
        elif choice == "3":
            game_id = input("\n🎮 Ingresa el ID del juego: ").strip()
            if game_id.isdigit():
                scraper.download_game_by_id(game_id)
        
        elif choice == "4":
            raw_url = input("\n🔗 Pega URL directa del archivo: ").strip()
            if raw_url:
                filename = input("📝 Nombre de archivo (opcional): ").strip() or None
                scraper.download_file(raw_url, filename)

        elif choice == "5":
            print("\n👋 ¡Hasta luego!")
            break
        
        else:
            print("❌ Opción inválida")


if __name__ == "__main__":
    try:
        interactive_mode()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrumpido por el usuario")
        sys.exit(0)
