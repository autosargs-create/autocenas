#!/usr/bin/env python3
"""
=============================================================================
🚗 AutoCenas — Auto rezerves daļu cenu salīdzinātājs (Dipex, IC24, Trodo)
=============================================================================
Ievadi oriģinālo detaļas numuru (OEM / artikulu) un saņem salīdzinošu tabulu
ar pieejamajām cenām, ražotājiem un saitēm no Latvijas lielākajiem veikaliem.
=============================================================================
"""

import sys
import os
import re
import json
import base64
import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path

# ANSI krāsas terminālim
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def clean_part_number(pn: str) -> str:
    """Iztīra liekās atstarpes un simbolus meklēšanai."""
    return re.sub(r'[\s\.\-]+', '', pn).upper()


def get_trodo_url(part_number: str) -> str:
    return f"https://www.trodo.lv/catalogsearch/result/?q={part_number}&searchby=number"


def get_dipex_url(part_number: str) -> str:
    return f"https://dipex.lv/lv/catalog/rezerves-dalas/?search_q={part_number}"


def get_ic24_url(part_number: str) -> str:
    b64_q = base64.b64encode(part_number.encode()).decode()
    return f"https://www.ic24.lv/detalas/?search={b64_q}"


def scrape_dipex(part_number: str):
    """Izvelk cenas un detaļas no Dipex.lv."""
    url = get_dipex_url(part_number)
    html = ""
    # 1. Mēģinām ar ātru curl pieprasījumu
    cmd_curl = [
        'curl', '-s', '-L',
        '-A', 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
        url
    ]
    try:
        res = subprocess.check_output(cmd_curl, stderr=subprocess.DEVNULL, text=True, timeout=12)
        if 'GTMTool' in res:
            html = res
    except Exception:
        pass

    # 2. Ja Cloudflare nobloķēja curl, palaižam caur Chromium, kas apiet aizsardzību
    if not html:
        cmd_chrome = [
            'chromium', '--headless=new', '--disable-gpu',
            '--no-sandbox', '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            '--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            '--dump-dom', url
        ]
        try:
            html = subprocess.check_output(cmd_chrome, stderr=subprocess.DEVNULL, text=True, timeout=30)
        except Exception:
            return []

    # 1. Parsējam GTM datus pēc produkta ID
    impressions = re.findall(r'GTMTool\.addImpressions\(\s*({[\s\S]*?})\s*\);', html)
    id_map = {}
    for imp in impressions:
        try:
            clean = re.sub(r'(\w+):', r'"\1":', imp)
            clean = re.sub(r',\s*}', '}', clean)
            data = json.loads(clean)
            id_map[str(data.get('id'))] = data
        except Exception:
            pass

    # 2. Parsējam karšu saites un piesaistām cenas
    card_matches = re.findall(r'<div class="product-item[^\"]*"[\s\S]*?href="([^\"]+)"[^>]*data-product-id="(\d+)"', html)
    results = []
    seen = set()

    for href, pid in card_matches:
        item = id_map.get(pid)
        if item:
            brand = item.get('brand', '').strip().upper()
            name = item.get('name', '').strip()
            price = float(item.get('price', 0))
            link = f"https://dipex.lv{href}"
            key = (brand, price)
            if key not in seen:
                seen.add(key)
                results.append({
                    'store': 'Dipex.lv',
                    'brand': brand,
                    'name': name,
                    'price': price,
                    'stock': 'Noliktavā',
                    'url': link
                })

    # Ja karšu meklēšana neatrada, bet impressions bija
    if not results and id_map:
        for pid, item in id_map.items():
            results.append({
                'store': 'Dipex.lv',
                'brand': item.get('brand', '').strip().upper(),
                'name': item.get('name', '').strip(),
                'price': float(item.get('price', 0)),
                'stock': 'Noliktavā',
                'url': url
            })

    return results


def scrape_ic24(part_number: str):
    """Izvelk cenas un pieejamību no IC24.lv."""
    url = get_ic24_url(part_number)
    cmd = [
        'chromium', '--headless=new', '--disable-gpu',
        '--no-sandbox', '--disable-dev-shm-usage',
        '--disable-blink-features=AutomationControlled',
        '--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        '--virtual-time-budget=6000',
        '--dump-dom', url
    ]
    try:
        html = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=35)
    except Exception:
        return []

    results = []
    blocks = re.findall(r'<div[^>]*class="[^\"]*horizontal-baner-b2c-23[^\"]*"[\s\S]*?<\/div>\s*<\/div>\s*<\/span>\s*<\/span>', html)

    for b in blocks:
        m_brand = re.search(r'class="manufacture">([^<]+)</span>', b)
        m_desc = re.search(r'class="description">([^<]+)</span>', b)
        m_price = re.search(r'class="[^\"]*price_gross_2[^\"]*">([^<]+)</div>', b)
        m_link = re.search(r'href="(/produkti/[^\"]+)"', b)
        m_stock = re.search(r'class="progress-bar-description[^\"]*"[^>]*>([\s\S]*?)<\/div>', b)

        if m_price:
            try:
                price = float(m_price.group(1).replace(',', '.').strip())
                brand = unescape(m_brand.group(1).strip().upper()) if m_brand else 'NAV NORĀDĪTS'
                name = unescape(m_desc.group(1).strip()) if m_desc else ''
                link = f"https://www.ic24.lv{m_link.group(1)}" if m_link else url
                stock_raw = re.sub(r'<[^>]+>', '', m_stock.group(1)).strip() if m_stock else 'Pieejams'
                stock_clean = unescape(stock_raw).replace('&gt;', '>').strip()

                results.append({
                    'store': 'IC24.lv',
                    'brand': brand,
                    'name': name,
                    'price': price,
                    'stock': stock_clean,
                    'url': link
                })
            except Exception:
                pass

    return results


def print_comparison_table(part_number: str, items: list, brand_filter: str = None):
    """Izvada pārskatāmu un noformētu cenu tabulu terminālī."""
    if brand_filter:
        bf = brand_filter.upper()
        items = [x for x in items if bf in x['brand'] or bf in x['name'].upper()]

    # Kārtojam pēc cenas (augoši)
    items.sort(key=lambda x: x['price'])

    trodo_link = get_trodo_url(part_number)
    dipex_link = get_dipex_url(part_number)
    ic24_link = get_ic24_url(part_number)

    print(f"\n{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════════════{RESET}")
    print(f" 🔍 {BOLD}Meklētā detaļa:{RESET} {YELLOW}{BOLD}{part_number}{RESET}")
    print(f"{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════════════{RESET}")
    print(f" 🌐 {BOLD}Tiešās saites uz veikaliem:{RESET}")
    print(f"    • {BOLD}Trodo.lv:{RESET}  {trodo_link}")
    print(f"    • {BOLD}Dipex.lv:{RESET}  {dipex_link}")
    print(f"    • {BOLD}IC24.lv:{RESET}   {ic24_link}")
    print(f"{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════════════{RESET}\n")

    if not items:
        print(f"{YELLOW}⚠️  Neviena konkrēta cena netika atrasta. Lūdzu, pārbaudi saites augstāk!{RESET}\n")
        return

    # Formāta platumi
    w_store = 10
    w_brand = 16
    w_price = 10
    w_stock = 14
    w_name = 44

    header = (
        f"┌{'─' * w_store}┬{'─' * w_brand}┬{'─' * w_name}┬{'─' * w_price}┬{'─' * w_stock}┐\n"
        f"│ {BOLD}{'Veikals':<{w_store-2}}{RESET} │ {BOLD}{'Ražotājs':<{w_brand-2}}{RESET} │ {BOLD}{'Nosaukums / Apraksts':<{w_name-2}}{RESET} │ {BOLD}{'Cena':>{w_price-2}}{RESET} │ {BOLD}{'Pieejamība':<{w_stock-2}}{RESET} │\n"
        f"├{'─' * w_store}┼{'─' * w_brand}┼{'─' * w_name}┼{'─' * w_price}┼{'─' * w_stock}┤"
    )
    print(header)

    lowest_price = items[0]['price']

    for it in items:
        is_cheapest = (it['price'] == lowest_price)
        price_str = f"{it['price']:.2f} €"

        if is_cheapest:
            color = GREEN + BOLD
            tag = " ★"
        else:
            color = RESET
            tag = ""

        disp_store = it['store'][:w_store-2]
        disp_brand = it['brand'][:w_brand-2]
        disp_name = it['name'][:w_name-2]
        disp_stock = it['stock'][:w_stock-2]

        print(
            f"│ {disp_store:<{w_store-2}} │ "
            f"{disp_brand:<{w_brand-2}} │ "
            f"{disp_name:<{w_name-2}} │ "
            f"{color}{price_str + tag:>{w_price-2}}{RESET} │ "
            f"{disp_stock:<{w_stock-2}} │"
        )

    footer = f"└{'─' * w_store}┴{'─' * w_brand}┴{'─' * w_name}┴{'─' * w_price}┴{'─' * w_stock}┘"
    print(footer)
    print(f"\n💡 {GREEN}{BOLD}★ Zaļā krāsā:{RESET} Lētākais atrastais variants ({lowest_price:.2f} €). Kopā atrastas {len(items)} pozīcijas.\n")


def generate_html_report(part_number: str, items: list, output_file: Path):
    """Ģenerē vizuālu HTML cenu salīdzinājuma lapu."""
    items.sort(key=lambda x: x['price'])
    lowest_price = items[0]['price'] if items else 0

    rows_html = ""
    for it in items:
        is_cheapest = (it['price'] == lowest_price)
        highlight_class = "cheapest" if is_cheapest else ""
        badge = '<span class="badge-cheapest">LĒTĀKAIS</span>' if is_cheapest else ''

        rows_html += f"""
        <tr class="{highlight_class}">
            <td><strong>{it['store']}</strong></td>
            <td><span class="brand-tag">{it['brand']}</span></td>
            <td>{it['name']}</td>
            <td class="price-cell">{it['price']:.2f} € {badge}</td>
            <td>{it['stock']}</td>
            <td><a href="{it['url']}" target="_blank" class="buy-btn">Atvērt veikalā ↗</a></td>
        </tr>
        """

    trodo_link = get_trodo_url(part_number)
    dipex_link = get_dipex_url(part_number)
    ic24_link = get_ic24_url(part_number)

    html_content = f"""<!DOCTYPE html>
<html lang="lv">
<head>
    <meta charset="UTF-8">
    <title>Cenu salīdzinājums: {part_number}</title>
    <style>
        :root {{
            --bg: #0f172a;
            --surface: #1e293b;
            --primary: #38bdf8;
            --text: #f8fafc;
            --text-dim: #94a3b8;
            --green: #22c55e;
            --border: #334155;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 2rem;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        header {{
            background: var(--surface);
            padding: 1.5rem 2rem;
            border-radius: 12px;
            border: 1px solid var(--border);
            margin-bottom: 2rem;
        }}
        h1 {{
            margin: 0 0 0.5rem 0;
            font-size: 1.8rem;
            color: var(--primary);
        }}
        .links-bar {{
            display: flex;
            gap: 1rem;
            margin-top: 1rem;
            flex-wrap: wrap;
        }}
        .store-pill {{
            background: #0f172a;
            color: var(--text);
            text-decoration: none;
            padding: 0.6rem 1.2rem;
            border-radius: 8px;
            border: 1px solid var(--border);
            font-size: 0.95rem;
            transition: all 0.2s;
        }}
        .store-pill:hover {{
            border-color: var(--primary);
            color: var(--primary);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--surface);
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid var(--border);
        }}
        th, td {{
            padding: 1rem 1.2rem;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            background: #111827;
            color: var(--text-dim);
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        tr:hover td {{
            background: #27354a;
        }}
        .brand-tag {{
            background: #334155;
            padding: 0.2rem 0.6rem;
            border-radius: 4px;
            font-size: 0.85rem;
            font-weight: 600;
        }}
        .price-cell {{
            font-size: 1.2rem;
            font-weight: 700;
            color: #fff;
        }}
        .cheapest td {{
            background: rgba(34, 197, 94, 0.08);
        }}
        .cheapest .price-cell {{
            color: var(--green);
        }}
        .badge-cheapest {{
            background: var(--green);
            color: #000;
            font-size: 0.65rem;
            font-weight: 800;
            padding: 2px 6px;
            border-radius: 4px;
            margin-left: 6px;
            vertical-align: middle;
        }}
        .buy-btn {{
            display: inline-block;
            background: var(--primary);
            color: #0f172a;
            font-weight: 600;
            text-decoration: none;
            padding: 0.4rem 0.8rem;
            border-radius: 6px;
            font-size: 0.85rem;
        }}
        .buy-btn:hover {{
            background: #7dd3fc;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🚗 Auto Cenu Salīdzinājums: {part_number}</h1>
            <p style="color: var(--text-dim); margin: 0;">Atrastas {len(items)} pozīcijas no Latvijas auto detaļu veikaliem.</p>
            <div class="links-bar">
                <a href="{trodo_link}" target="_blank" class="store-pill">Trodo.lv meklēšana ↗</a>
                <a href="{dipex_link}" target="_blank" class="store-pill">Dipex.lv meklēšana ↗</a>
                <a href="{ic24_link}" target="_blank" class="store-pill">IC24.lv meklēšana ↗</a>
            </div>
        </header>

        <table>
            <thead>
                <tr>
                    <th>Veikals</th>
                    <th>Ražotājs</th>
                    <th>Nosaukums / Artikuls</th>
                    <th>Cena</th>
                    <th>Pieejamība</th>
                    <th>Saite</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
    output_file.write_text(html_content, encoding='utf-8')
    print(f"📊 {GREEN}HTML atskaite saglabāta:{RESET} {output_file}")


def main():
    parser = argparse.ArgumentParser(description="Auto rezerves daļu cenu meklētājs (Dipex, IC24, Trodo)")
    parser.add_argument("code", nargs="?", default=None, help="Oriģinālais detaļas numurs (OEM vai artikuls)")
    parser.add_argument("--brand", default=None, help="Filtrēt pēc konkrēta ražotāja (piem., bosch, mann, vag)")
    parser.add_argument("--html", action="store_true", help="Ģenerēt vizuālu HTML atskaiti")
    parser.add_argument("--open", action="store_true", help="Automātiski atvērt HTML pārlūkprogrammā")

    args = parser.parse_args()

    part_number = args.code
    if not part_number:
        print(f"\n{BOLD}{CYAN}🚗 AutoCenas — Rezerves daļu cenu salīdzinātājs{RESET}")
        try:
            part_number = input(f"{BOLD}Ievadi detaļas numuru (piemēram, 03L115562 vai W712/94): {RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAtcelts.")
            sys.exit(0)

    part_number = clean_part_number(part_number)
    if not part_number:
        print(f"{RED}Kļūda: Detaļas numurs nevar būt tukšs!{RESET}")
        sys.exit(1)

    print(f"\n{CYAN}⏳ Meklējam detaļu {BOLD}{part_number}{RESET}{CYAN} veikalos Dipex.lv un IC24.lv...{RESET}")

    all_items = []

    # Paralēli vaicājam abus veikalus
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_dipex = executor.submit(scrape_dipex, part_number)
        future_ic24 = executor.submit(scrape_ic24, part_number)

        try:
            dipex_items = future_dipex.result()
            print(f"  ✓ {BOLD}Dipex.lv:{RESET} atrastas {len(dipex_items)} pozīcijas")
            all_items.extend(dipex_items)
        except Exception as e:
            print(f"  ✗ {RED}Dipex.lv kļūda:{RESET} {e}")

        try:
            ic24_items = future_ic24.result()
            print(f"  ✓ {BOLD}IC24.lv:{RESET} atrastas {len(ic24_items)} pozīcijas")
            all_items.extend(ic24_items)
        except Exception as e:
            print(f"  ✗ {RED}IC24.lv kļūda:{RESET} {e}")

    # Izvadam tabulu terminālī
    print_comparison_table(part_number, all_items, brand_filter=args.brand)

    # Ja pieprasīts HTML
    if args.html or args.open:
        report_path = Path.home() / "projects" / "autoparts-compare" / f"cenas_{part_number}.html"
        generate_html_report(part_number, all_items, report_path)

        if args.open:
            subprocess.run(['xdg-open', str(report_path)], stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
