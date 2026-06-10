"""Verifica encoding del login (usa escapes unicode, a prueba de consola)."""
from playwright.sync_api import sync_playwright

O = "ó"      # ó
DASH = "—"   # em dash
REPL = "�"   # replacement char (sintoma de encoding roto)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True); pg = b.new_page()
    pg.goto("https://tablero-agronasaja.ehussen.workers.dev/", wait_until="domcontentloaded", timeout=40000)
    pg.wait_for_timeout(1500)
    h = pg.content()
    print("sesion_ok      :", ("sesi" + O + "n") in h.lower())
    print("electronico_ok :", ("electr" + O + "nico") in h.lower())
    print("dash_ok        :", DASH in h)
    print("replacement    :", REPL in h)
    print("mojibake_A_tilde:", "Ã" in h)   # Ã
    print("lema_presente  :", "Buscando el mejor" in h)
    b.close()
