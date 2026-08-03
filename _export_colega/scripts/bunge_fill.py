"""Autocompleta usuario+contraseña en el login de Bunge (operacionesbasa) sobre
la sesión CDP abierta (:9333). El CAPTCHA + Ingresar van a mano.
Credenciales por entorno: BUNGE_USER / BUNGE_PASS (o .env del proyecto)."""
import sys, os
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
envf = Path(__file__).resolve().parent.parent / ".env"
if envf.exists():
    for ln in envf.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
USER = os.environ.get("BUNGE_USER"); PWD = os.environ.get("BUNGE_PASS")
if not (USER and PWD):
    print("[!] Falta BUNGE_USER / BUNGE_PASS (env o .env)"); sys.exit(1)
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp("http://localhost:9333")
    ctx = b.contexts[0]
    page = next((pg for pg in ctx.pages if "Login.aspx" in pg.url), ctx.pages[-1])
    ok = page.evaluate("""(c)=>{
        const u=document.getElementById('txbUsuario'); const p=document.getElementById('txbContrasena');
        if(!u||!p) return 'no fields';
        u.value=c.u; p.value=c.p;
        u.dispatchEvent(new Event('input',{bubbles:true})); p.dispatchEvent(new Event('input',{bubbles:true}));
        const cap=document.getElementById('uccapcha_txtCapchaUSuario'); if(cap) cap.focus();
        return 'ok';
    }""", {"u": USER, "p": PWD})
    print("[+] fill:", ok, "- falta CAPTCHA + Ingresar")
    b.close()
