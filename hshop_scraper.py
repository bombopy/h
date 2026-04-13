#!/usr/bin/env python3
"""
hShop Scraper & Downloader
Busca y descarga juegos de 3DS desde hShop.erista.me

Requiere: pip install requests tqdm beautifulsoup4
"""

import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import parse_qs
from urllib.parse import urljoin
from urllib.parse import quote_plus
from typing import List, Dict, Optional, Tuple, Callable

try:
    import requests
    import certifi
    import urllib3
    import charset_normalizer
    import idna
    from tqdm import tqdm
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ Error: Faltan dependencias")
    print("Instala con: pip install requests tqdm beautifulsoup4")
    sys.exit(1)

try:
    from scrapling.fetchers import StealthyFetcher
except ImportError:
    StealthyFetcher = None


class _SoupSelection:
    def __init__(self, items: List):
        self._items = items

    def __iter__(self):
        return iter(self._items)

    def __bool__(self):
        return bool(self._items)

    def get(self):
        return self._items[0] if self._items else None

    def getall(self):
        return self._items


class _SoupNode:
    def __init__(self, node):
        self._node = node

    @property
    def attrib(self) -> Dict[str, str]:
        attrs = getattr(self._node, "attrs", {}) or {}
        return {k: (" ".join(v) if isinstance(v, list) else str(v)) for k, v in attrs.items()}

    @staticmethod
    def _extract_text_from_nodes(nodes: List) -> List[str]:
        texts: List[str] = []
        for n in nodes:
            texts.extend([t for t in n.stripped_strings if t])
        return texts

    def css(self, selector: str) -> _SoupSelection:
        text_mode = selector.endswith("::text")
        base_selector = selector[:-6]

        if text_mode:
            if base_selector:
                selected = self._node.select(base_selector)
                return _SoupSelection(self._extract_text_from_nodes(selected))
            return _SoupSelection([t for t in self._node.stripped_strings if t])

        selected_nodes = self._node.select(selector)
        return _SoupSelection([_SoupNode(n) for n in selected_nodes])


def _fetch_html_page(url: str) -> _SoupNode:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    response = _http_get_with_retries(url, headers=headers, timeout=45)
    return _SoupNode(BeautifulSoup(response.text, "html.parser"))


def _http_get_with_retries(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 45,
    retries: int = 3,
    backoff_seconds: float = 1.2,
):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            # Reintenta errores temporales del servidor.
            if response.status_code >= 500:
                response.raise_for_status()
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait_time = backoff_seconds * attempt
            print(f"⚠️  Reintentando ({attempt}/{retries}) por error HTTP: {exc}")
            time.sleep(wait_time)

    if last_error:
        raise last_error
    raise RuntimeError("Fallo HTTP inesperado sin detalles")


class hShopScraper:
    """Scraper para hShop con descarga automática"""
    
    BASE_URL = "https://hshop.erista.me"
    
    def __init__(
        self,
        download_dir: str = "./downloads",
        prefer_stealth: bool = True,
        two_captcha_api_key: str = "",
    ):
        """
        Inicializa el scraper
        
        Args:
            download_dir: Directorio donde guardar las descargas
        """
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)
        # --- LOGS DETALLADOS DE SCRAPLING ---
        self.use_stealth = False
        self.two_captcha_api_key = (two_captcha_api_key or "").strip()
        print(f"📁 Directorio de descargas: {self.download_dir.absolute()}")
        try:
            if prefer_stealth:
                if StealthyFetcher is not None:
                    # Probar si Chromium está disponible
                    import subprocess
                    try:
                        result = subprocess.run(["which", "chromium-browser"], capture_output=True, text=True)
                        if result.returncode == 0:
                            print(f"✅ Chromium encontrado en: {result.stdout.strip()}")
                        else:
                            print("⚠️  Chromium no encontrado en PATH (chromium-browser)")
                        # Probar playwright
                        import playwright
                        print(f"✅ Playwright importado: {playwright.__version__}")
                        self.use_stealth = True
                        print("✅ Modo scrapling activo")
                    except Exception as e:
                        print(f"❌ Error al probar Chromium/Playwright: {e}")
                        self.use_stealth = False
                else:
                    print("⚠️  StealthyFetcher no disponible (scrapling no instalado o error de importación)")
                    self.use_stealth = False
            else:
                print("⚠️  prefer_stealth=False, forzando modo fallback")
                self.use_stealth = False
        except Exception as e:
            print(f"❌ Error al inicializar modo scrapling: {e}")
            self.use_stealth = False
        if not self.use_stealth:
            print("⚠️  Modo sin scrapling (fallback)")
        else:
            print("✅ Modo scrapling activo")

    def set_2captcha_api_key(self, api_key: str):
        self.two_captcha_api_key = (api_key or "").strip()

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
    def _extract_content_id_from_url(url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            match = re.search(r"/content/(\d+)", parsed.path or "", re.IGNORECASE)
            if match:
                return match.group(1)
        except Exception:
            pass
        return None

    @staticmethod
    def is_direct_download_url(url: str, expected_game_id: Optional[str] = None) -> bool:
        raw = (url or "").strip()
        if not raw:
            return False

        try:
            parsed = urlparse(raw)
            path = (parsed.path or "").lower()
            query = parse_qs(parsed.query)
        except Exception:
            return False

        if path.endswith(".cia"):
            return True

        content_id = hShopScraper._extract_content_id_from_url(raw)
        has_token = bool(query.get("token"))
        if content_id and has_token:
            if expected_game_id and str(expected_game_id).strip() and content_id != str(expected_game_id).strip():
                return False
            return True

        return False

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    def _extract_games_from_page(self, page) -> List[Dict]:
        games = []
        cards = page.css('a.list-entry.block-link[href^="/t/"]')
        if not cards:
            cards = page.css('a[href^="/t/"]')

        for link in cards:
            href = link.attrib.get("href", "")
            if not href or not re.match(r"/t/\d+$", href):
                continue

            game_id = href.split("/")[-1]

            title = self._clean_text(link.css("h3::text").get() or "")
            if not title:
                text_nodes = link.css("::text").getall() if link.css("::text") else []
                for node in text_nodes:
                    candidate = self._clean_text(node)
                    if candidate and candidate.lower() not in {"also known as:", "id", "size", "title id"}:
                        title = candidate
                        break

            if not title:
                title = self._clean_text(link.attrib.get("title", ""))
            if not title:
                title = self._clean_text(link.attrib.get("aria-label", ""))
            if not title:
                title = f"Game {game_id}"

            size = "N/A"
            title_id = "N/A"
            content_category = "N/A"
            region = "N/A"

            for meta in link.css("div.meta-content"):
                spans = [self._clean_text(t) for t in meta.css("span::text").getall() if self._clean_text(t)]
                if len(spans) < 2:
                    continue

                label = spans[-1].lower()
                value = spans[0]
                if label == "size":
                    size = value
                elif label == "title id":
                    title_id = value

            info_rows = link.css("div.base-info h4")
            for row in info_rows:
                row_text = self._clean_text(" ".join(row.css("::text").getall()))
                if "content in" not in row_text.lower():
                    continue
                span_texts = [self._clean_text(v) for v in row.css("span::text").getall() if self._clean_text(v)]
                if span_texts:
                    content_category = span_texts[0]
                    region = span_texts[-1]
                else:
                    region_match = re.search(r"➞\s*([A-Za-z0-9\- ]+)", row_text)
                    if region_match:
                        region = self._clean_text(region_match.group(1))
                break

            games.append(
                {
                    "id": game_id,
                    "title": title,
                    "size": size,
                    "title_id": title_id,
                    "category": content_category,
                    "region": region,
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

    def _find_next_page_url(self, page) -> Optional[str]:
        for link in page.css("a[href]"):
            text = self._clean_text(" ".join(link.css("::text").getall())).lower()
            href = (link.attrib.get("href") or "").strip()
            if not href:
                continue
            if "show next" in text or "next" == text:
                return href if href.startswith("http") else urljoin(self.BASE_URL, href)
        return None

    def search_games_page(self, query: str = "", category: str = "games", page_url: Optional[str] = None) -> Tuple[List[Dict], Optional[str]]:
        """Busca una sola página de resultados y devuelve el siguiente enlace de paginación."""
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

        encoded_query = quote_plus(query) if query else ""
        search_type = category_map.get(category.lower().strip(), "all")

        if page_url:
            url = page_url
        elif query:
            url = (
                f"{self.BASE_URL}/search/results?"
                f"q={encoded_query}&qt=Text&type={search_type}&sort=likes&order=desc"
            )
        else:
            url = f"{self.BASE_URL}/c/{category}"

        print(f"\n🔍 Cargando resultados: {url}")
        try:
            if self.use_stealth:
                page = StealthyFetcher.fetch(url, headless=True, network_idle=True, solve_cloudflare=True)
            else:
                page = _fetch_html_page(url)
        except Exception as first_error:
            if query and not page_url:
                fallback_url = (
                    f"{self.BASE_URL}/search/results?"
                    f"q={encoded_query}&qt=Text&type={search_type}"
                )
                print(f"⚠️  Búsqueda principal falló ({first_error}). Probando URL alternativa...")
                try:
                    if self.use_stealth:
                        page = StealthyFetcher.fetch(fallback_url, headless=True, network_idle=True, solve_cloudflare=True)
                    else:
                        page = _fetch_html_page(fallback_url)
                except Exception as second_error:
                    category_url = f"{self.BASE_URL}/c/{category}"
                    print(f"⚠️  URL alternativa falló ({second_error}). Probando fallback por categoría...")
                    if self.use_stealth:
                        page = StealthyFetcher.fetch(category_url, headless=True, network_idle=True, solve_cloudflare=True)
                    else:
                        page = _fetch_html_page(category_url)

                    category_games = self._extract_games_from_page(page)
                    q = (query or "").strip().lower()
                    filtered_games = [
                        g for g in category_games
                        if q in (g.get("title") or "").lower() or q in (g.get("id") or "").lower()
                    ]
                    print(f"ℹ️  Fallback por categoría activo: {len(filtered_games)} resultados locales")
                    return filtered_games, None
            else:
                raise

        games = self._extract_games_from_page(page)
        next_url = self._find_next_page_url(page)
        return games, next_url

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

    @staticmethod
    def _extract_token_from_text(value: str) -> Optional[str]:
        text = (value or "").strip()
        if not text:
            return None

        # Si pegan una URL, intenta leer parámetros de query primero.
        try:
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            for key in ("captcha_token", "token", "cf-turnstile-response"):
                vals = query.get(key)
                if vals and vals[0]:
                    return vals[0].strip()
        except Exception:
            pass

        token_match = re.search(r"(?:captcha_)?token=([^&#\s\"']+)", text, re.IGNORECASE)
        if token_match:
            return token_match.group(1)

        # Permite pegar solo el token plano
        if re.fullmatch(r"[A-Za-z0-9._\-+=]{20,}", text):
            return text
        return None

    def resolve_download_link_from_token(self, game_id: str, captcha_token: str) -> Optional[str]:
        """Convierte un captcha_token en enlace directo de descarga."""
        token = (captcha_token or "").strip()
        if not token:
            return None

        token_encoded = quote_plus(token)
        item_url = f"{self.BASE_URL}/t/{game_id}"
        widget_url = f"{item_url}/download-widget?captcha_token={token_encoded}"
        try:
            response = requests.get(
                widget_url,
                timeout=30,
                headers={
                    "Referer": item_url,
                    "User-Agent": "Mozilla/5.0",
                },
            )
            response.raise_for_status()
            content = response.text

            direct_match = re.search(
                r'https?://download\d+\.erista\.me/content/\d+\?token=[^\s"\'&]+',
                content,
                re.IGNORECASE,
            )
            if direct_match:
                return direct_match.group(0)

            generic_content_match = re.search(
                r'https?://[^\s"\']+/content/\d+\?token=[^\s"\'&]+',
                content,
                re.IGNORECASE,
            )
            if generic_content_match:
                return generic_content_match.group(0)

            cia_match = re.search(r'https?://[^\s"\']+\.cia(?:\?[^\s"\']*)?', content, re.IGNORECASE)
            if cia_match:
                return cia_match.group(0)

            return None
        except Exception as e:
            print(f"❌ Error resolviendo token de captcha: {e}")
            return None

    def resolve_download_link_from_value(self, game_id: str, value: str) -> Optional[str]:
        """
        Acepta:
        - enlace final con token (download*.erista.me/content/...?...)
        - enlace download-widget?captcha_token=...
        - token plano
        y devuelve enlace directo.
        """
        raw = (value or "").strip()
        if not raw:
            return None

        try:
            parsed = urlparse(raw)
            query = parse_qs(parsed.query)
            if "/content/" in (parsed.path or "") and query.get("token"):
                return raw if self.is_direct_download_url(raw, game_id) else None
        except Exception:
            pass

        if "download-widget" in raw.lower() and "captcha_token=" in raw.lower():
            token = self._extract_token_from_text(raw)
            if token:
                return self.resolve_download_link_from_token(game_id, token)

        token = self._extract_token_from_text(raw)
        if token:
            return self.resolve_download_link_from_token(game_id, token)

        return None

    @staticmethod
    def _extract_turnstile_sitekey_from_html(html: str) -> Optional[str]:
        content = html or ""
        patterns = [
            r'data-sitekey=["\']([^"\']+)["\']',
            r'sitekey["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            r'turnstile\s*\.\s*render\([^,]+,\s*\{[^}]*sitekey\s*:\s*["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                key = (match.group(1) or "").strip()
                if key:
                    return key
        return None

    def get_turnstile_sitekey(self, game_id: str) -> Optional[str]:
        item_url = f"{self.BASE_URL}/t/{game_id}"
        try:
            response = requests.get(
                item_url,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0",
                },
            )
            response.raise_for_status()
            return self._extract_turnstile_sitekey_from_html(response.text)
        except Exception as exc:
            print(f"⚠️  No se pudo obtener sitekey Turnstile: {exc}")
            return None

    def _solve_turnstile_via_2captcha(
        self,
        site_key: str,
        page_url: str,
        timeout_seconds: int = 180,
        poll_interval_seconds: int = 5,
    ) -> Optional[str]:
        api_key = (self.two_captcha_api_key or "").strip()
        if not api_key:
            return None

        try:
            create_response = requests.post(
                "https://2captcha.com/in.php",
                data={
                    "key": api_key,
                    "method": "turnstile",
                    "sitekey": site_key,
                    "pageurl": page_url,
                    "json": 1,
                },
                timeout=30,
            )
            create_response.raise_for_status()
            create_payload = create_response.json()
        except Exception as exc:
            print(f"❌ Error creando tarea 2Captcha: {exc}")
            return None

        if int(create_payload.get("status", 0)) != 1:
            print(f"❌ 2Captcha rechazó la tarea: {create_payload.get('request')}")
            return None

        task_id = str(create_payload.get("request", "")).strip()
        if not task_id:
            print("❌ 2Captcha no devolvió task id")
            return None

        started = time.time()
        while (time.time() - started) <= max(timeout_seconds, 15):
            try:
                result_response = requests.get(
                    "https://2captcha.com/res.php",
                    params={
                        "key": api_key,
                        "action": "get",
                        "id": task_id,
                        "json": 1,
                    },
                    timeout=30,
                )
                result_response.raise_for_status()
                result_payload = result_response.json()
            except Exception as exc:
                print(f"⚠️  Error consultando 2Captcha: {exc}")
                time.sleep(max(2, poll_interval_seconds))
                continue

            if int(result_payload.get("status", 0)) == 1:
                token = (result_payload.get("request") or "").strip()
                return token or None

            state = str(result_payload.get("request", "")).strip()
            if state == "CAPCHA_NOT_READY":
                time.sleep(max(2, poll_interval_seconds))
                continue

            print(f"❌ 2Captcha devolvió error: {state}")
            return None

        print("❌ Timeout esperando respuesta de 2Captcha")
        return None

    def resolve_download_link_with_2captcha(self, game_id: str) -> Optional[str]:
        """Resuelve el captcha Turnstile con 2Captcha y devuelve enlace directo."""
        api_key = (self.two_captcha_api_key or "").strip()
        if not api_key:
            print("⚠️  Falta API key de 2Captcha")
            return None

        item_url = f"{self.BASE_URL}/t/{game_id}"
        site_key = self.get_turnstile_sitekey(game_id)
        if not site_key:
            print("⚠️  No se detectó sitekey Turnstile en la página")
            return None

        token = self._solve_turnstile_via_2captcha(site_key, item_url)
        if not token:
            return None

        return self.resolve_download_link_from_token(game_id, token)
    
    def search_games(self, query: str = "", category: str = "games", load_all: bool = False, max_pages: int = 10) -> List[Dict]:
        """
        Busca juegos en hShop
        
        Args:
            query: Término de búsqueda (vacío = top games)
            category: Categoría (games, updates, dlc, virtual-console, etc.)
        
        Returns:
            Lista de diccionarios con información de juegos
        """
        print(f"\n🔍 Buscando en hShop: '{query}' en categoría '{category}'...")
        
        try:
            all_games = []
            current_url = None
            page_count = 0

            while True:
                page_games, next_url = self.search_games_page(query, category, current_url)
                all_games.extend(page_games)
                page_count += 1

                if not load_all:
                    break
                if not next_url or page_count >= max_pages:
                    break
                current_url = next_url

            seen = set()
            games = []
            for game in all_games:
                if game["id"] in seen:
                    continue
                seen.add(game["id"])
                games.append(game)

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

            if not self.use_stealth:
                page = _fetch_html_page(url)
                direct_url = self._extract_direct_download_from_page(page, self.BASE_URL)
                if direct_url:
                    return direct_url
                if self.two_captcha_api_key:
                    print("ℹ️  Intentando resolver captcha via 2Captcha...")
                    solved = self.resolve_download_link_with_2captcha(game_id)
                    if solved:
                        return solved
                print("⚠️  No se encontró enlace de descarga directo (modo fallback sin scrapling)")
                print("    Sugerencia: resuelve captcha en navegador y usa token/URL para resolver enlace")
                # Guardar HTML para diagnóstico
                try:
                    import datetime, os
                    html = str(page._node) if page else ''
                    now = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                    fname = f"/tmp/render_debug_{game_id}_{now}.html"
                    with open(fname, 'w', encoding='utf-8') as f:
                        f.write(html)
                    print(f"[DEBUG][get_download_link] Guardado HTML de error en: {fname}")
                except Exception as e:
                    print(f"[DEBUG][get_download_link] No se pudo guardar HTML: {e}")
                return None

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

            if self.two_captcha_api_key:
                print("ℹ️  Intentando resolver captcha via 2Captcha...")
                solved = self.resolve_download_link_with_2captcha(game_id)
                if solved:
                    return solved

            print("⚠️  No se encontró enlace de descarga directo")
            print("    El captcha/token puede requerir resolución manual")
            # Guardar HTML para diagnóstico
            try:
                import datetime, os
                html = str(page._node) if page else ''
                now = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                fname = f"/tmp/render_debug_{game_id}_{now}.html"
                with open(fname, 'w', encoding='utf-8') as f:
                    f.write(html)
                print(f"[DEBUG][get_download_link] Guardado HTML de error en: {fname}")
            except Exception as e:
                print(f"[DEBUG][get_download_link] No se pudo guardar HTML: {e}")
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

    def download_file(
        self,
        url: str,
        filename: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
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
            downloaded = 0

            if progress_callback:
                progress_callback(downloaded, total_size)
                with open(filepath, "wb") as file_obj:
                    for chunk in response.iter_content(chunk_size=1024 * 128):
                        if not chunk:
                            continue
                        written = file_obj.write(chunk)
                        downloaded += written
                        progress_callback(downloaded, total_size)
            else:
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
                        downloaded += written
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
        print(f"   Categoria: {game.get('category', 'N/A')}")
        print(f"   Región: {game.get('region', 'N/A')}")
    
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

            all_games = []
            next_url = None

            page_games, next_url = scraper.search_games_page(query, category)
            all_games.extend(page_games)

            while all_games:
                show_games(all_games, max_show=len(all_games))
                print("\nAcciones: [número]=generar enlace y descargar, m=cargar más, q=volver")
                action = input("Elige acción: ").strip().lower()

                if action == "q":
                    break

                if action == "m":
                    if not next_url:
                        print("⚠️  No hay más resultados para cargar")
                        continue
                    more_games, next_url = scraper.search_games_page(query, category, next_url)
                    if not more_games:
                        print("⚠️  No se encontraron más resultados")
                        continue

                    known = {g["id"] for g in all_games}
                    new_items = [g for g in more_games if g["id"] not in known]
                    all_games.extend(new_items)
                    print(f"✅ Se cargaron {len(new_items)} resultados nuevos")
                    continue

                if action.isdigit():
                    idx = int(action) - 1
                    if 0 <= idx < len(all_games):
                        game = all_games[idx]
                        link = scraper.get_download_link(game["id"])
                        if link:
                            print("\n🔗 Enlace generado:")
                            print(f"🎮 {game['title']}")
                            print(link)
                            should_download = input("\n¿Descargar ahora? (s/n): ").strip().lower()
                            if should_download in ("s", "si", "y", "yes"):
                                filename = f"{scraper._safe_filename(game['title'])}_{game['id']}.cia"
                                scraper.download_file(link, filename)
                    else:
                        print("❌ Índice inválido")
                else:
                    print("❌ Opción inválida")
        
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
