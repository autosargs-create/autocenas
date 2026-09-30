#!/usr/bin/env python3
"""
=============================================================================
🚗 AutoCenas Web Server — Vietējais tīmekļa serveris cenu meklēšanai
=============================================================================
Nodrošina interaktīvu weblapu un API piekļuvi uz http://localhost:7777
=============================================================================
"""

import sys
import os

# Nodrošinām, ka visi logi nekavējoties parādās docker logs
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(line_buffering=True)
import json
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Importējam skreiperus no mūsu autocenas.py moduļa
CURRENT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(CURRENT_DIR))

from autocenas import (
    clean_part_number,
    scrape_dipex,
    scrape_ic24,
    get_trodo_url,
    get_dipex_url,
    get_ic24_url
)

PORT = 7777
HTML_FILE = CURRENT_DIR / "templates" / "index.html"


class AutoCenasHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Kompakts un glīts logu izvads terminālī
        print(f"[AutoCenas Web] {self.address_string()} - {format % args}")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 1. Galvenā mājaslapa (HTML)
        if path == "/" or path == "/index.html":
            if not HTML_FILE.exists():
                self.send_error(404, "Index template not found")
                return

            html_bytes = HTML_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        # 2. Cenu meklēšanas API: /api/search?q=03L115562
        if path == "/api/search":
            qs = urllib.parse.parse_qs(parsed.query)
            query = qs.get("q", [""])[0].strip()
            part_number = clean_part_number(query)

            if not part_number:
                self.send_json({"error": "Detaļas numurs ir obligāts!"}, status=400)
                return

            print(f"\n[API] Meklējam detaļu: {part_number} ...")
            items = []

            # Paralēli vaicājam veikalus
            with ThreadPoolExecutor(max_workers=2) as executor:
                f_dipex = executor.submit(scrape_dipex, part_number)
                f_ic24 = executor.submit(scrape_ic24, part_number)

                try:
                    items.extend(f_dipex.result())
                except Exception as e:
                    print(f"[API] Dipex kļūda: {e}")

                try:
                    items.extend(f_ic24.result())
                except Exception as e:
                    print(f"[API] IC24 kļūda: {e}")

            # Kārtojam pēc cenas augoši
            items.sort(key=lambda x: x["price"])

            response_data = {
                "part_number": part_number,
                "count": len(items),
                "links": {
                    "trodo": get_trodo_url(part_number),
                    "dipex": get_dipex_url(part_number),
                    "ic24": get_ic24_url(part_number)
                },
                "items": items
            }

            self.send_json(response_data)
            return

        # Viss cits - 404
        self.send_error(404, "Not Found")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def run_server():
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, AutoCenasHandler)
    print(f"\n========================================================")
    print(f" 🚗 AutoCenas Web Serveris ir palaists!")
    print(f" 👉 Atver pārlūkprogrammā: http://localhost:{PORT}")
    print(f"========================================================\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServeris apturēts.")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
