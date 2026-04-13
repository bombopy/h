#!/usr/bin/env python3
"""
Cliente hShop para Termux
Se conecta al servidor API en Render.com
"""

import requests
from tqdm import tqdm
from pathlib import Path
from typing import List, Dict, Optional

# ⚠️ CONFIGURA TU URL DE RENDER AQUI
API_URL = "https://TU-SERVIDOR.onrender.com"

DOWNLOAD_DIR = Path.home() / "downloads" / "hshop_games"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


class hShopClient:
    """Cliente para la API de hShop"""
    
    def __init__(self, api_url: str = API_URL):
        self.api_url = api_url.rstrip('/')
        self.session = requests.Session()
    
    def health_check(self) -> bool:
        """Verifica que el servidor esté activo"""
        try:
            resp = self.session.get(f"{self.api_url}/health", timeout=60)
            return resp.status_code == 200
        except:
            return False
    
    def search_games(self, query: str = "", category: str = "games", limit: int = 20) -> List[Dict]:
        """Busca juegos"""
        try:
            resp = self.session.get(
                f"{self.api_url}/search",
                params={"query": query, "category": category, "limit": limit},
                timeout=90
            )
            
            if resp.status_code == 200:
                data = resp.json()
                return data.get("games", [])
            else:
                print(f"❌ Error: {resp.status_code}")
                return []
        except Exception as e:
            print(f"❌ Error buscando: {e}")
            return []
    
    def get_download_link(self, game_id: str) -> Optional[str]:
        """Obtiene enlace de descarga"""
        try:
            resp = self.session.get(
                f"{self.api_url}/download-link/{game_id}",
                timeout=90
            )
            
            if resp.status_code == 200:
                data = resp.json()
                return data.get("download_url")
            else:
                print(f"❌ Error obteniendo enlace: {resp.status_code}")
                return None
        except Exception as e:
            print(f"❌ Error: {e}")
            return None
    
    def download_file(self, url: str, filename: str) -> bool:
        """Descarga un archivo con progress bar"""
        filepath = DOWNLOAD_DIR / filename
        
        if filepath.exists():
            print(f"⏭️  Ya existe: {filename}")
            return True
        
        try:
            print(f"\n⬇️  Descargando: {filename}")
            resp = self.session.get(url, stream=True, timeout=120)
            resp.raise_for_status()
            
            total_size = int(resp.headers.get('content-length', 0))
            
            with open(filepath, 'wb') as f, tqdm(
                desc=filename,
                total=total_size,
                unit='iB',
                unit_scale=True,
                unit_divisor=1024,
            ) as pbar:
                for chunk in resp.iter_content(chunk_size=8192):
                    size = f.write(chunk)
                    pbar.update(size)
            
            print(f"✅ Descargado: {filepath}")
            return True
            
        except Exception as e:
            print(f"❌ Error descargando: {e}")
            if filepath.exists():
                filepath.unlink()
            return False
    
    def download_game(self, game_id: str, title: str = None) -> bool:
        """Descarga un juego por ID"""
        link = self.get_download_link(game_id)
        
        if not link:
            return False
        
        filename = f"{title or game_id}_{game_id}.cia"
        filename = "".join(c if c.isalnum() or c in " -_." else "_" for c in filename)
        
        return self.download_file(link, filename)


def show_games(games: List[Dict]):
    """Muestra lista de juegos"""
    print("\n" + "="*70)
    print("📋 RESULTADOS:")
    print("="*70)
    
    for i, game in enumerate(games, 1):
        print(f"\n{i}. {game['title']}")
        print(f"   ID: {game['id']} | Size: {game['size']}")
        print(f"   TitleID: {game['title_id']}")
    
    print("\n" + "="*70)


def interactive_mode():
    """Modo interactivo"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║           hSHOP CLIENT v2.0 (API Mode)                       ║
║           Descarga automática de juegos 3DS                  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    # Verificar configuración
    if "TU-SERVIDOR" in API_URL:
        print("❌ ERROR: Configurá API_URL con tu servidor de Render")
        return
    
    client = hShopClient()
    
    # Health check
    print("🔍 Verificando servidor...")
    if not client.health_check():
        print("❌ Servidor no disponible")
        print("   Tip: Puede estar despertando, esperá 30 segundos y probá de nuevo")
        return
    
    print(f"✅ Conectado a: {API_URL}")
    print(f"📁 Descargas en: {DOWNLOAD_DIR}")
    
    while True:
        print("\n┌─ MENÚ PRINCIPAL")
        print("│")
        print("│ 1. Buscar juegos")
        print("│ 2. Ver populares")
        print("│ 3. Descargar por ID")
        print("│ 4. Salir")
        print("└─")
        
        choice = input("\nOpción: ").strip()
        
        if choice == "1":
            query = input("\n🔍 Búsqueda: ").strip()
            category = input("📁 Categoría [games]: ").strip() or "games"
            
            games = client.search_games(query, category, limit=30)
            
            if games:
                show_games(games)
                
                dl = input("\nDescargar (número o 'n'): ").strip()
                if dl.isdigit():
                    idx = int(dl) - 1
                    if 0 <= idx < len(games):
                        game = games[idx]
                        client.download_game(game['id'], game['title'])
        
        elif choice == "2":
            games = client.search_games("", "games", limit=30)
            
            if games:
                show_games(games)
                
                dl = input("\nDescargar (número o 'n'): ").strip()
                if dl.isdigit():
                    idx = int(dl) - 1
                    if 0 <= idx < len(games):
                        game = games[idx]
                        client.download_game(game['id'], game['title'])
        
        elif choice == "3":
            game_id = input("\n🎮 ID del juego: ").strip()
            if game_id.isdigit():
                client.download_game(game_id)
        
        elif choice == "4":
            print("\n👋 ¡Hasta luego!")
            break


if __name__ == "__main__":
    try:
        interactive_mode()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrumpido")
