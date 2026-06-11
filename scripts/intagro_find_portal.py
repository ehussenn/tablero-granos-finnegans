"""Encuentra la URL real del portal Intagro probando variantes + scraping home."""
import sys, json
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scripts" / "scraper" / "out" / "intagro"
OUT.mkdir(parents=True, exist_ok=True)

candidates = [
    "https://www.intagro.com/",
    "https://intagro.com/",
    "https://extranet.intagro.com/",
    "https://clientes.intagro.com/",
    "https://mi.intagro.com/",
    "https://portal.intagro.com/",
    "https://www.intagro.com/login/",
    "https://www.intagro.com/clientes/",
]

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch(headless=False)
    page = ctx.new_page()
    found = {}
    for url in candidates:
        try:
            r = page.goto(url, wait_until="domcontentloaded", timeout=15000)
            status = r.status if r else None
            page.wait_for_timeout(2000)
            title = page.title()[:50] if page.url else ""
            current = page.url
            print(f"  [{status}] {url} → {current[:80]} | title: {title}")
            found[url] = {"status": status, "final_url": current, "title": title}
        except Exception as e:
            print(f"  [ERR] {url}: {str(e)[:80]}")

    # Buscar links en intagro.com home a "clientes" / "login" / "acceso"
    print("\n[+] Goto homepage para buscar link...")
    try:
        page.goto("https://www.intagro.com/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT/"home.png"), full_page=True)
        # Buscar todos los anchors con texto relevante
        links = page.locator("a").all()
        relevant = []
        for el in links[:200]:
            try:
                txt = el.inner_text(timeout=200).strip()
                href = el.get_attribute("href") or ""
                if not href: continue
                if any(k in (txt+href).lower() for k in ["clien","login","acce","ingre","extran","portal","oper"]):
                    relevant.append((txt[:30], href[:150]))
            except: pass
        seen = set()
        unique = [(t,h) for t,h in relevant if (t,h) not in seen and not seen.add((t,h))]
        print(f"\n[+] {len(unique)} links relevantes en home:")
        for t, h in unique[:25]: print(f"    [{t:30s}] -> {h}")
    except Exception as e: print(f"  [!] {e}")

    (OUT/"portal_candidates.json").write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[+] Done.")
    page.wait_for_timeout(5000)
    ctx.close()
