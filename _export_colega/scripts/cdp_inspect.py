"""Genérico: se conecta por CDP a una sesión abierta y vuelca el estado de la
página activa (inputs, selects, botones, links) + sniffea JSON con señales de calidad.
Uso: py scripts/cdp_inspect.py <port> [forms|links]"""
import sys, json, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
port = sys.argv[1] if len(sys.argv) > 1 else "9334"
mode = sys.argv[2] if len(sys.argv) > 2 else "forms"
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(f"http://localhost:{port}")
    ctx = b.contexts[0]
    page = ctx.pages[-1] if ctx.pages else ctx.new_page()
    print("pestañas:", [pg.url for pg in ctx.pages])
    print("URL activa:", page.url)
    if mode == "forms":
        info = page.evaluate("""()=>({
            inputs:Array.from(document.querySelectorAll('input,textarea')).map(e=>({id:e.id,name:e.name,type:e.type,ph:e.placeholder,fcn:e.getAttribute('formcontrolname')})),
            selects:Array.from(document.querySelectorAll('select,mat-select,[role=combobox]')).map(e=>({id:e.id,name:e.name,fcn:e.getAttribute('formcontrolname'),ph:e.getAttribute('placeholder')})),
            buttons:Array.from(document.querySelectorAll('button,input[type=submit],input[type=button],a')).map(e=>({t:(e.innerText||e.value||'').trim().slice(0,40),id:e.id,href:(e.getAttribute('href')||'').slice(0,50)})).filter(x=>x.t)
        })""")
        print("\n=== inputs ==="); [print("  ", x) for x in info["inputs"][:25]]
        print("\n=== selects ==="); [print("  ", x) for x in info["selects"][:20]]
        print("\n=== botones/links ==="); [print("  ", x) for x in info["buttons"][:40]]
    b.close()
