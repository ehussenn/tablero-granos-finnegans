# -*- coding: utf-8 -*-
"""ARCA (ex AFIP) — bajada de Cartas de Porte Electronicas para el cruce con Finnegans.

Autorizado por el usuario (03/09/2026) para entrar con las credenciales de Agronasaja
y bajar los 4 listados que hoy baja a mano:
    1. Cp solicitada Afip          (CPE que solicito Agronasaja)
    2. Cp solicitada Afip Planta   (idem, planta)
    3. Cp Participantes            (CPE donde Agronasaja participa)
    4. Cp Participantes Acond      (idem, acondicionamiento)

Credenciales: SOLO desde .env local (ARCA_CUIT / ARCA_CLAVE), que .gitignore excluye.
Nunca se escriben en el repo ni en la pagina.

Uso:
    py scripts/arca_cpe_scraper.py --explora        # login + dump de servicios/pantallas
    py scripts/arca_cpe_scraper.py --visible        # con navegador a la vista (captcha / 2FA)
    py scripts/arca_cpe_scraper.py --desde 2025-07-01 --hasta 2026-09-03

El login de ARCA puede pedir captcha o segundo factor: en ese caso hay que correrlo
con --visible y que el usuario lo pase una vez (despues queda la sesion en el perfil).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PERFIL = RAIZ / "scripts" / "scraper" / ".arca_profile"
DUMP = RAIZ / "data" / "arca" / "_dump"
SALIDA = RAIZ / "data" / "arca"

LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
PORTAL_URL = "https://portalcf.cloud.afip.gob.ar/portal/app/"
CPE_URL = "https://cpea-app.afip.gob.ar/cpe-web/secure/index.html#/"


def normaliza_ctg(x) -> str:
    return re.sub(r"\D", "", str(x or ""))


def cargar_env() -> dict:
    env = {}
    f = RAIZ / ".env"
    if f.exists():
        for ln in f.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith("ARCA_")})
    return env


def log(*a):
    print(*a, flush=True)


def abrir(pw, visible: bool):
    PERFIL.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        str(PERFIL),
        headless=not visible,
        viewport={"width": 1580, "height": 940},
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"),
        accept_downloads=True,
        downloads_path=str(SALIDA),
    )
    return ctx


def login(pg, cuit: str, clave: str) -> bool:
    """Login con clave fiscal. Devuelve True si quedo adentro del portal."""
    pg.goto(LOGIN_URL, timeout=90_000)
    pg.wait_for_timeout(1500)
    if "portalcf" in pg.url:
        log("    ya habia sesion abierta")
        return True
    try:
        pg.fill(r"#F1\:username", cuit)
        pg.click(r"#F1\:btnSiguiente")
        pg.wait_for_timeout(1800)
        pg.fill(r"#F1\:password", clave)
        pg.click(r"#F1\:btnIngresar")
    except Exception:
        # el formulario cambia de version: intento por placeholder / type
        try:
            pg.fill("input[type=text]:visible", cuit)
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(1800)
            pg.fill("input[type=password]:visible", clave)
            pg.keyboard.press("Enter")
        except Exception as e:
            log(f"    [!] no pude completar el login automatico: {e}")
            return False
    pg.wait_for_timeout(4000)
    ok = "portalcf" in pg.url or "app" in pg.url
    log(f"    despues del login: {pg.url[:90]} ({'OK' if ok else 'revisar'})")
    return ok


def explora(pg):
    """Dump de la pantalla: servicios disponibles, links, tablas y captura."""
    DUMP.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    pg.screenshot(path=str(DUMP / f"arca_{ts}.png"), full_page=True)
    info = pg.evaluate(r"""() => ({
      url: location.href, titulo: document.title,
      links: [...document.querySelectorAll('a')].map(a => (a.innerText||'').trim())
               .filter(t => t).slice(0, 250),
      servicios: [...document.querySelectorAll('[class*=service], [class*=servicio], .card, li')]
               .map(e => (e.innerText||'').trim().split('\n')[0]).filter(t => t && t.length < 70).slice(0, 200),
      inputs: [...document.querySelectorAll('input,select,button')].map(e =>
               `${e.tagName}#${e.id||''}[${e.type||''}] ${(e.placeholder||e.innerText||e.value||'').trim().slice(0,40)}`).slice(0, 120),
      frames: [...document.querySelectorAll('iframe')].map(f => f.src).slice(0, 20)
    })""")
    (DUMP / f"arca_{ts}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"    dump -> {DUMP / f'arca_{ts}.json'} (+ captura)")
    return info


# ── LECTURA DE LA TABLA EN PANTALLA ────────────────────────────────────────────
# ARCA no tiene boton de exportar (el usuario venia copiando y pegando a mano todos
# los dias): leemos la grilla del DOM y pasamos las paginas solas.

# Columnas tal como las viene pegando en su Excel (asi el cruce no cambia de nombres)
COLS_SOLICITADAS = ["Nro Carta Porte", "CTG/CTDG", "Fecha de emisión", "Fecha de Vencimiento",
                    "Estado", "Cuit transportista", "N. Patente", "Tipo Grano", "Kilos",
                    "Cuit Destino", "N° Planta destino", "Localidad de destino",
                    "CUIT Destinatario", "CUIT Remitente Comercial",
                    "CUIT Remitente comercial venta sec", "Cuit Corredor Venta Primaria",
                    "Cuit Corredor Venta Secundaria", "Cuit Chofer",
                    "Cuit Representante Entregador", "Cuit Representante Recibidor",
                    "Cuit Intermediario Flete"]
COLS_PARTICIPANTES = ["Nro Carta Porte", "CTG/CTDG", "Cuit Solicitante", "Fecha de emisión",
                      "Fecha de Vencimiento", "Estado", "Cuit transportista", "N. Operativo",
                      "N. Patente", "Tipo Grano", "Kilos", "Cuit Destino", "N° Planta destino",
                      "Provincia de destino", "Localidad de destino", "CUIT Destinatario",
                      "CUIT Remitente Comercial", "CUIT Remitente comercial venta sec",
                      "CUIT Corredor", "Conductor segundo tramo"]

JS_TABLA = r"""() => {
  const tablas = [...document.querySelectorAll('table')]
    .map(t => ({t, n: t.querySelectorAll('tbody tr').length}))
    .filter(x => x.n > 0)
    .sort((a, b) => b.n - a.n);
  if (!tablas.length) return null;
  const t = tablas[0].t;
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  // OJO: no filtrar los vacios, la posicion tiene que coincidir con las celdas
  const heads = [...t.querySelectorAll('thead th, thead td')].map((e, i) => txt(e) || ('col' + i));
  const filas = [...t.querySelectorAll('tbody tr')].map(tr =>
    [...tr.querySelectorAll('td, th')].map(txt));
  // ARCA pinta una fila de aviso cuando no hay datos: no es un registro
  const AVISO = /no se encontraron|sin resultados|no hay datos|no existen|haga click|haga clic|cargando/i;
  return {heads, filas: filas.filter(f => f.some(c => c) && !(f.filter(c => c).length <= 2 && AVISO.test(f.join(' '))))};
}"""


def leer_grilla(pg) -> dict:
    """Devuelve {heads, filas} de la grilla mas grande de la pantalla."""
    d = pg.evaluate(JS_TABLA)
    if not d:
        # a veces la grilla vive dentro de un iframe
        for fr in pg.frames:
            try:
                d = fr.evaluate(JS_TABLA)
            except Exception:
                d = None
            if d and d.get("filas"):
                return d
    return d or {"heads": [], "filas": []}


def cerrar_modales(pg) -> str:
    """ARCA abre modales (avisos, sesion, confirmaciones) que interceptan los clicks.
    Los cierra y devuelve el texto del ultimo, para dejarlo en el log."""
    txt = ""
    for _ in range(5):
        m = None
        for sel in (".modal.show", ".modal[style*='display: block']", "div[role=dialog]"):
            c = pg.query_selector(sel)
            if c:
                try:
                    if c.is_visible():
                        m = c
                        break
                except Exception:
                    pass
        if not m:
            break
        try:
            txt = (m.inner_text() or "").replace("\n", " ").strip()[:180]
        except Exception:
            txt = ""
        if txt:
            log(f"    (modal) {txt[:120]}")
        cerrado = False
        for sel in (".modal.show .modal-footer button.btn-primary",
                    ".modal.show button:has-text('Aceptar')",
                    ".modal.show button:has-text('ACEPTAR')",
                    ".modal.show button:has-text('Cerrar')",
                    ".modal.show button:has-text('Continuar')",
                    ".modal.show button:has-text('OK')",
                    ".modal.show button.close", ".modal.show .close",
                    ".modal.show [aria-label='Close']"):
            b = pg.query_selector(sel)
            try:
                if b and b.is_visible():
                    b.click(timeout=5000)
                    cerrado = True
                    break
            except Exception:
                pass
        if not cerrado:
            try:
                pg.keyboard.press("Escape")
            except Exception:
                pass
        pg.wait_for_timeout(900)
    return txt


def esperar_grilla(pg, seg: int = 60) -> dict:
    """Espera a que la grilla termine de cargar (ARCA pinta 'Cargando ...' en la celda)."""
    for i in range(seg * 2):
        if i % 8 == 0:
            cerrar_modales(pg)
        d = leer_grilla(pg)
        filas = d.get("filas") or []
        txt = " ".join(" ".join(f) for f in filas[:3]).lower()
        if filas and "cargando" not in txt:
            return d
        cuerpo = (pg.inner_text("body") or "").lower()
        if not filas and ("no se encontraron" in cuerpo or "sin resultados" in cuerpo
                          or "no hay datos" in cuerpo or "no existen" in cuerpo):
            return d
        pg.wait_for_timeout(500)
    return leer_grilla(pg)


def maximizar_pagina(pg) -> None:
    """Si hay selector de filas por pagina, lo pone en el maximo (menos paginas que pasar)."""
    try:
        sels = pg.query_selector_all("select")
        for s in sels:
            ops = [o.inner_text().strip() for o in s.query_selector_all("option")]
            nums = [o for o in ops if o.isdigit()]
            if nums and max(int(n) for n in nums) >= 20:
                s.select_option(str(max(int(n) for n in nums)))
                pg.wait_for_timeout(1500)
                log(f"    filas por pagina -> {max(int(n) for n in nums)}")
                return
    except Exception:
        pass


HEADS_VISTOS = {}   # {tipo: encabezados reales de la grilla}


def bajar_listado(pg, nombre: str, cols: list[str] | None = None, max_pag: int = 200) -> list[dict]:
    """Lee la grilla pagina por pagina hasta que no haya mas 'siguiente'."""
    vistas, filas = set(), []
    for i in range(1, max_pag + 1):
        d = esperar_grilla(pg)
        nuevas = 0
        for f in d["filas"]:
            k = "|".join(f)
            if k in vistas:
                continue
            vistas.add(k)
            filas.append(f)
            nuevas += 1
        log(f"    {nombre}: pagina {i} · +{nuevas} filas (total {len(filas)})")
        if nuevas == 0 and i > 1:
            break
        firma = "|".join(d["filas"][0]) if d.get("filas") else ""
        # siguiente pagina: link/boton con texto o aria conocidos
        sig = None
        for sel in ("a[aria-label*='iguiente']", "button[aria-label*='iguiente']",
                    "a[title*='iguiente']", "li.next:not(.disabled) a", "a.paginate_button.next",
                    "#tabla_next:not(.disabled)"):
            sig = pg.query_selector(sel)
            if sig:
                break
        if not sig:
            sig = pg.evaluate_handle(r"""() => [...document.querySelectorAll('a,button')]
                  .find(e => /^(siguiente|next|>|»)$/i.test((e.innerText||'').trim())
                          && !e.disabled && !(e.className||'').includes('disabled'))""").as_element()
        if not sig:
            log(f"    {nombre}: no hay mas paginas")
            break
        try:
            sig.click()
        except Exception:
            pg.evaluate("(e) => e.click()", sig)
        # esperar a que la grilla REALMENTE cambie (si no, el paginado corta de mas)
        cambio = False
        for _ in range(50):
            pg.wait_for_timeout(400)
            d2 = leer_grilla(pg)
            f2 = d2.get("filas") or []
            if not f2:
                continue
            t2 = " ".join(f2[0]).lower()
            if "cargando" in t2:
                continue
            if "|".join(f2[0]) != firma:
                cambio = True
                break
        if not cambio:
            log(f"    {nombre}: la grilla no cambio al pasar de pagina — corto aca")
            break
    heads = cols or leer_grilla(pg).get("heads") or []
    if heads:
        HEADS_VISTOS[nombre.split(" ")[0]] = heads
    out = []
    for f in filas:
        if heads and len(heads) >= 2:
            out.append({heads[j] if j < len(heads) else f"col{j}": f[j] for j in range(len(f))})
        else:
            out.append({f"col{j}": v for j, v in enumerate(f)})
    return out


def guardar(nombre: str, filas: list[dict]) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)
    f = SALIDA / f"cpe_{nombre}.json"
    f.write_text(json.dumps({"listado": nombre, "bajado": datetime.now().isoformat(timespec="seconds"),
                             "heads": HEADS_VISTOS, "filas": filas},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"    -> {f.name}: {len(filas)} filas")
    return f


# ── NAVEGACION: servicio + representado ───────────────────────────────────────
SERVICIO = "Carta de porte electr"          # tarjeta del portal (sin acento final)


def espera_portal(pg, seg: int = 150) -> bool:
    """Espera a que caiga en el portal (da tiempo a captcha / 2FA hecho a mano)."""
    for _ in range(seg):
        u = pg.url or ""
        if "portalcf" in u or "/portal/app" in u:
            return True
        pg.wait_for_timeout(1000)
    return False


def entrar_servicio(ctx, pg, nombre: str = SERVICIO):
    """Abre la tarjeta del servicio. Devuelve la pagina donde quedo (el portal abre
    los servicios en una pestana nueva) o None si no la encontro."""
    # buscador del portal
    for sel in ("#buscadorInput", "input[placeholder*='Buscá']", "input[placeholder*='Busca']",
                "input[type='search']"):
        b = pg.query_selector(sel)
        if b:
            b.click(); b.fill(nombre)
            pg.wait_for_timeout(1800)
            break
    # el portal pinta los servicios de forma asincronica: hay que esperarlos
    try:
        pg.wait_for_selector("a.full-width:has-text('Carta de porte')", timeout=30_000)
    except Exception:
        try:
            pg.wait_for_function(
                r"""() => [...document.querySelectorAll('a')]
                      .some(e => (e.innerText||'').toLowerCase().includes('carta de porte'))""",
                timeout=20_000)
        except Exception:
            log("    (no aparecio la tarjeta; voy directo por URL al servicio)")
    loc = pg.locator("a.full-width").filter(has_text="Carta de porte")
    h = loc.first.element_handle() if loc.count() else None
    if not h:
        h = pg.evaluate_handle(r"""(n) => [...document.querySelectorAll('a,button,div,h3,p,span')]
              .find(e => (e.innerText||'').toLowerCase().includes(n.toLowerCase())
                      && (e.innerText||'').length < 90)""", nombre).as_element()
    if not h:
        # atajo: entrar derecho a la app del servicio con la sesion ya abierta
        p2 = ctx.new_page()
        p2.goto(CPE_URL, timeout=90_000)
        p2.wait_for_timeout(4000)
        if "cpe-web" in p2.url:
            log(f"    servicio abierto por URL directa -> {p2.url[:95]}")
            return p2
        log(f"    [!] no encontre la tarjeta '{nombre}' ni pude entrar por URL")
        p2.close()
        return None
    antes = set(ctx.pages)
    try:
        h.click()
    except Exception:
        pg.evaluate("(e) => e.click()", h)
    # el portal abre el servicio en una pestana nueva: la buscamos unos segundos
    nueva = None
    for _ in range(20):
        pg.wait_for_timeout(700)
        otras = [x for x in ctx.pages if x not in antes]
        if otras:
            nueva = otras[-1]
            break
        if pg.url and "portal/app" not in pg.url:
            nueva = pg
            break
    destino = nueva or pg
    try:
        destino.bring_to_front()
        destino.wait_for_load_state("domcontentloaded", timeout=30_000)
    except Exception:
        pass
    destino.wait_for_timeout(2500)
    log(f"    servicio abierto -> {destino.url[:95]}"
        + ("  (pestana nueva)" if nueva and nueva is not pg else ""))
    return destino


def elegir_representado(pg, texto: str = "Agronasaja", cuit: str = "30-71071275-8") -> bool:
    """Pantalla 'Elegi una persona para ingresar' del servicio CPE: elige el
    representado (por CUIT, que es univoco) y confirma el modal que aparece."""
    for intento in range(3):
        pg.wait_for_timeout(1500)
        cuerpo = pg.inner_text("body") or ""
        if "una persona para ingresar" not in cuerpo:
            return True                      # ya estamos adentro
        h = pg.evaluate_handle(r"""(o) => {
              const hit = e => (e.innerText||'').includes(o.cuit)
                            || new RegExp(o.txt, 'i').test(e.innerText||'');
              const cs = [...document.querySelectorAll('div,a,li,button,section')]
                 .filter(e => hit(e) && (e.innerText||'').length < 90);
              return cs.length ? cs[cs.length - 1] : null;
            }""", {"cuit": cuit, "txt": texto}).as_element()
        if not h:
            log(f"    [!] no encontre el representado '{texto}' ({cuit})")
            return False
        try:
            h.click()
        except Exception:
            pg.evaluate("(e) => e.click()", h)
        pg.wait_for_timeout(1800)
        # modal "Seguro que desea ingresar como ..." -> confirmar
        for sel in ("button:has-text('Aceptar')", "button:has-text('Confirmar')",
                    "button:has-text('Ingresar')", "button:has-text('Si')",
                    "button:has-text('Sí')", ".modal-footer button.btn-primary",
                    ".modal button.btn-primary"):
            b = pg.query_selector(sel)
            if b and b.is_visible():
                b.click()
                log(f"    representado confirmado ({texto}) via {sel}")
                pg.wait_for_timeout(4000)
                break
        pg.wait_for_timeout(2000)
        if "una persona para ingresar" not in (pg.inner_text("body") or ""):
            log(f"    dentro como {texto}")
            return True
    return False


# ── CONSULTAS ─────────────────────────────────────────────────────────────────
# Los ids del formulario de PARTICIPANTE son autogenerados (__BVID__nn), asi que
# los <select> se ubican por el contenido de sus opciones, que si es estable.
TIPO_DEF = "Automotor - Grano"
ROLES_PART = ["Destinatario (G/DG)", "Remitente Comercial Primario (G)",
              "Remitente Comercial Secundario (G)", "Remitente Comercial Productor (G)",
              "Remitente comercial venta secundaria 2 (G)", "Corredor Venta Primaria (G)",
              "Corredor Venta Secundaria (G)", "Mercado a término (G/DG)",
              "Transportista (G/DG)", "Transportista segundo tramo (G/DG)",
              "Chofer/Conductor primer tramo (G/DG)", "Representante Entregador (G)",
              "Representante Recibidor (G)", "Intermediario Flete (G/DG)",
              "Remitente Comercial (DG)", "Comisionista (DG)", "Corredor (DG)",
              "Conductor Segundo Tramo (G/DG)", "Pagador Flete Pagador (G/DG)"]


def sel_con_opcion(pg, texto: str):
    """Devuelve el handle del <select> que tenga una opcion con ese texto."""
    return pg.evaluate_handle(r"""(t) => [...document.querySelectorAll('select')]
        .find(s => [...s.options].some(o => (o.text||'').trim() === t))""", texto).as_element()


def sel_numerico(pg):
    """El select 'Mostrar' (filas por pagina): todas sus opciones son numeros."""
    return pg.evaluate_handle(r"""() => [...document.querySelectorAll('select')]
        .filter(s => s.options.length && [...s.options].every(o => /^\d+$/.test((o.text||'').trim())))
        .pop()""").as_element()


def poner_max_filas(pg) -> None:
    s = sel_numerico(pg)
    if not s:
        return
    mx = pg.evaluate(r"""(s) => { const ns = [...s.options].map(o => parseInt(o.text));
          const m = Math.max(...ns); s.value = String(m);
          s.dispatchEvent(new Event('change', {bubbles: true})); return m; }""", s)
    log(f"    filas por pagina -> {mx}")
    pg.wait_for_timeout(1500)


def elegir(pg, handle, label: str) -> bool:
    if not handle:
        return False
    ok = pg.evaluate(r"""(o) => { const s = o.s, t = o.t;
          const op = [...s.options].find(x => (x.text||'').trim() === t);
          if (!op) return false;
          s.value = op.value;
          s.dispatchEvent(new Event('change', {bubbles: true}));
          s.dispatchEvent(new Event('input', {bubbles: true}));
          return true; }""", {"s": handle, "t": label})
    pg.wait_for_timeout(900)
    return bool(ok)


def poner_fecha(pg, cual: str, valor: str) -> bool:
    """cual = 'Desde' | 'Hasta'. Ubica el input por la etiqueta de su bloque."""
    h = pg.evaluate_handle(r"""(t) => {
          const ins = [...document.querySelectorAll('input[type=text]')];
          return ins.find(i => {
            const c = i.closest('div')?.parentElement?.innerText || i.closest('div')?.innerText || '';
            return c.toLowerCase().includes('fecha ' + t.toLowerCase());
          }) || null; }""", cual).as_element()
    if not h:
        # fallback: los dos inputs #fechaDesde / #fechaHasta (solicitadas) o #fechaPartida x2
        ids = {"Desde": "#fechaDesde", "Hasta": "#fechaHasta"}
        h = pg.query_selector(ids[cual])
        if not h:
            fps = pg.query_selector_all("#fechaPartida")
            if len(fps) == 2:
                h = fps[0] if cual == "Desde" else fps[1]
    if not h:
        log(f"    [!] no encontre el campo Fecha {cual}")
        return False
    h.click()
    h.fill("")
    h.type(valor, delay=45)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(500)
    return True


def click_buscar(pg) -> None:
    cerrar_modales(pg)
    for sel in ("button:has-text('BUSCAR')", "button:has-text('Buscar')"):
        b = pg.query_selector(sel)
        if b:
            b.click()
            break
    pg.wait_for_timeout(3000)
    cerrar_modales(pg)
    # esperar que deje de cargar
    for _ in range(40):
        cuerpo = (pg.inner_text("body") or "").lower()
        if "cargando" not in cuerpo and "procesando" not in cuerpo:
            break
        pg.wait_for_timeout(700)


def abrir_tarjeta(pg, texto: str) -> bool:
    h = pg.evaluate_handle(r"""(t) => [...document.querySelectorAll('div,a,button,section,h3,p,span')]
          .filter(e => (e.innerText||'').trim().startsWith(t) && (e.innerText||'').length < 120)
          .pop()""", texto).as_element()
    if not h:
        log(f"    [!] no encontre la tarjeta '{texto}'")
        return False
    try:
        h.click()
    except Exception:
        pg.evaluate("(e) => e.click()", h)
    pg.wait_for_timeout(3500)
    return True


def volver_home(pg) -> None:
    b = pg.query_selector("button:has-text('VOLVER')")
    if b:
        b.click(); pg.wait_for_timeout(2500)
    else:
        pg.goto("https://cpea-app.afip.gob.ar/cpe-web/secure/index.html#/home")
        pg.wait_for_timeout(2500)


def consulta_solicitadas(pg, desde: str, hasta: str, planta: str, estado: str = "") -> list[dict]:
    """CPE Solicitadas - Granos/DG. planta = 'Productor' u 'Operador / Planta ...'."""
    cerrar_modales(pg)
    if "consultar-solicitadas" not in pg.url:
        volver_home(pg)
        if not abrir_tarjeta(pg, "CPE Solicitadas - Granos/DG"):
            return []
    elegir(pg, pg.query_selector("#tipoCPE"), TIPO_DEF)
    ph = pg.query_selector("#plantaOrigen")
    op = pg.evaluate(r"""(o) => { const s = o.s;
          const x = [...s.options].find(y => (y.text||'').startsWith(o.t));
          if (!x) return null; s.value = x.value;
          s.dispatchEvent(new Event('change', {bubbles:true})); return x.text; }""",
                     {"s": ph, "t": planta})
    log(f"    criterio: {op}")
    if estado:
        elegir(pg, pg.query_selector("#estado"), estado)
    poner_fecha(pg, "Desde", desde); poner_fecha(pg, "Hasta", hasta)
    poner_max_filas(pg)
    click_buscar(pg)
    esperar_grilla(pg)
    return bajar_listado(pg, f"solicitadas {planta[:9]} {desde}")


def consulta_participante(pg, rol: str, desde: str, hasta: str, estado: str = "Confirmada") -> list[dict]:
    """CPE Participante - Granos/DG, para un rol."""
    cerrar_modales(pg)
    if "consulta-participante" not in pg.url:
        volver_home(pg)
        if not abrir_tarjeta(pg, "CPE Participante - Granos/DG"):
            return []
    if not elegir(pg, sel_con_opcion(pg, "Destinatario (G/DG)"), rol):
        log(f"    [!] no pude elegir el rol {rol}")
        return []
    pg.wait_for_timeout(1800)
    elegir(pg, sel_con_opcion(pg, TIPO_DEF), TIPO_DEF)
    if estado:
        elegir(pg, sel_con_opcion(pg, "Confirmada"), estado)
    poner_fecha(pg, "Desde", desde); poner_fecha(pg, "Hasta", hasta)
    poner_max_filas(pg)
    click_buscar(pg)
    esperar_grilla(pg)
    return bajar_listado(pg, f"part {rol[:18]} {desde}")


VENTANA_DIAS = 30          # ARCA acepta hasta 60; 30 da menos paginas por consulta
CACHE = SALIDA / "_ventanas"   # una consulta por archivo: permite reanudar sin re-leer


def ventanas(desde: str, hasta: str, dias: int = VENTANA_DIAS):
    """Parte el rango en tramos de 'dias' (ARCA limita el rango a 60 dias)."""
    d0 = datetime.strptime(desde, "%Y-%m-%d").date()
    d1 = datetime.strptime(hasta, "%Y-%m-%d").date()
    out = []
    while d0 <= d1:
        fin = min(d0 + __import__("datetime").timedelta(days=dias - 1), d1)
        out.append((d0.strftime("%d/%m/%Y"), fin.strftime("%d/%m/%Y")))
        d0 = fin + __import__("datetime").timedelta(days=1)
    return out


def cache_path(clave: str) -> Path:
    return CACHE / (re.sub(r"[^A-Za-z0-9_.-]", "_", clave) + ".json")


def con_cache(clave: str, fn, forzar: bool = False):
    """Guarda cada consulta en su archivo: si ya esta, no vuelve a leer ARCA.
    Si una consulta falla, lo registra y sigue con la siguiente (no cachea el error)."""
    f = cache_path(clave)
    if f.exists() and not forzar:
        d = json.loads(f.read_text(encoding="utf-8"))
        log(f"    (cache) {clave}: {len(d['filas'])} filas")
        return d["filas"]
    try:
        filas = fn()
    except Exception as e:
        log(f"    [!] fallo {clave}: {type(e).__name__} {str(e)[:90]} - sigo con la siguiente")
        return []
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"clave": clave, "bajado": datetime.now().isoformat(timespec="seconds"),
                             "filas": filas}, ensure_ascii=False), encoding="utf-8")
    return filas


def consolidar_desde_cache() -> None:
    """Rearma cpe_solicitadas.json / cpe_participantes.json y el consolidado por CTG
    a partir de las ventanas ya cacheadas, aplicando el filtro de filas de aviso.
    No entra a ARCA: sirve para arreglar salidas sin gastar lecturas."""
    AVISO = ("no se encontraron", "sin resultados", "no hay datos", "no existen", "haga click", "haga clic", "cargando")
    def limpio(f):
        vals = [str(v or "").strip() for v in f.values()]
        txt = " ".join(vals).lower()
        if len([v for v in vals if v]) <= 2 and any(a in txt for a in AVISO):
            return False
        return any(vals)
    sol, par = [], []
    for f in sorted(CACHE.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        filas = [x for x in d.get("filas", []) if limpio(x)]
        clave = d.get("clave", f.stem)
        if clave.startswith("sol_"):
            crit = "Operador" if "_Operador_" in clave else "Productor"
            for x in filas:
                x.setdefault("_criterio", crit)
            sol += filas
        elif clave.startswith("part_"):
            rol = clave[5:].rsplit("_", 1)[0].replace("_", " ").strip()
            for x in filas:
                x.setdefault("_rol", rol)
            par += filas
    guardar("solicitadas", sol)
    guardar("participantes", par)
    cons = {}
    for lado, filas in (("solicitadas", sol), ("participantes", par)):
        for x in filas:
            ctg = normaliza_ctg(x.get("CTG/CTDG"))
            if not ctg:
                continue
            e = cons.setdefault(ctg, {"ctg": ctg, "lados": []})
            e.update({k: v for k, v in x.items() if v not in (None, "")})
            if lado not in e["lados"]:
                e["lados"].append(lado)
    f = SALIDA / "cpe_arca.json"
    f.write_text(json.dumps({"bajado": datetime.now().isoformat(timespec="seconds"),
                             "ctg": cons}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"[+] consolidado desde cache: {len(sol)} solicitadas + {len(par)} participantes "
        f"-> {len(cons)} CTG unicos")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--visible", action="store_true", help="navegador a la vista")
    ap.add_argument("--explora", action="store_true", help="solo login + dump")
    ap.add_argument("--desde", default="2026-02-01")
    ap.add_argument("--hasta", default=date.today().isoformat())
    ap.add_argument("--dias", type=int, default=VENTANA_DIAS, help="tamano de ventana (max 60)")
    ap.add_argument("--forzar", action="store_true", help="ignorar cache y volver a leer")
    ap.add_argument("--solo", default="", help="solicitadas | participantes")
    ap.add_argument("--roles", default="", help="lista de roles separados por ';' (por defecto todos)")
    ap.add_argument("--estado-part", dest="estado_part", default="Confirmada",
                    help="estado a filtrar en participantes (vacio = todos)")
    ap.add_argument("--consolidar", action="store_true",
                    help="rearma las salidas desde el cache, sin entrar a ARCA")
    a = ap.parse_args()

    if a.consolidar:
        consolidar_desde_cache()
        return

    env = cargar_env()
    cuit, clave = env.get("ARCA_CUIT"), env.get("ARCA_CLAVE")
    if not cuit or not clave:
        log("[!] Faltan ARCA_CUIT / ARCA_CLAVE en el .env local")
        sys.exit(2)

    from playwright.sync_api import sync_playwright
    SALIDA.mkdir(parents=True, exist_ok=True)
    vs = ventanas(a.desde, a.hasta, min(a.dias, 60))
    log(f"[+] ARCA CPE · {a.desde} -> {a.hasta} · {len(vs)} ventanas de hasta {a.dias} dias")

    with sync_playwright() as pw:
        ctx = abrir(pw, a.visible or a.explora)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        log(f"[+] entrando como {cuit[:2]}-****-{cuit[-1:]}")
        if not login(pg, cuit, clave) and not espera_portal(pg):
            explora(pg); ctx.close(); sys.exit(1)
        pg2 = entrar_servicio(ctx, pg)
        if not pg2:
            explora(pg); ctx.close(); sys.exit(1)
        pg = pg2
        elegir_representado(pg, env.get("ARCA_REPRESENTADO", "Agronasaja"))
        if a.explora:
            explora(pg); ctx.close(); return

        todo = {"solicitadas": [], "participantes": []}

        # ── 1) SOLICITADAS: Productor + Operador/Planta, todos los estados ──
        if a.solo in ("", "solicitadas"):
            for planta in ("Productor", "Operador"):
                for (d1, d2) in vs:
                    clave = f"sol_{planta}_{d1.replace('/','-')}"
                    filas = con_cache(clave, lambda: consulta_solicitadas(pg, d1, d2, planta), a.forzar)
                    for f in filas:
                        f["_criterio"] = planta
                    todo["solicitadas"] += filas
            guardar("solicitadas", todo["solicitadas"])

        # ── 2) PARTICIPANTES: todos los roles, estado Confirmada ──
        if a.solo in ("", "participantes"):
            roles = [r.strip() for r in a.roles.split(";") if r.strip()] or ROLES_PART
            vacios_f = SALIDA / "roles_sin_datos.json"
            vacios = set(json.loads(vacios_f.read_text(encoding="utf-8"))) if vacios_f.exists() and not a.forzar else set()
            con_datos = {}
            for rol in roles:
                if rol in vacios and not a.forzar:
                    log(f"    (salteo {rol}: sin datos en la corrida anterior)")
                    continue
                n_rol = 0
                for (d1, d2) in vs:
                    suf = "" if a.estado_part == "Confirmada" else "_todos"
                    clave = f"part_{rol[:22]}_{d1.replace('/','-')}{suf}"
                    filas = con_cache(clave, lambda: consulta_participante(pg, rol, d1, d2, a.estado_part), a.forzar)
                    for f in filas:
                        f["_rol"] = rol
                    todo["participantes"] += filas
                    n_rol += len(filas)
                con_datos[rol] = n_rol
                log(f"    ROL {rol}: {n_rol} filas en todo el rango")
            nuevos_vacios = sorted([r for r, n in con_datos.items() if n == 0] + list(vacios))
            vacios_f.write_text(json.dumps(nuevos_vacios, ensure_ascii=False, indent=1), encoding="utf-8")
            guardar("participantes", todo["participantes"])

        # ── consolidado por CTG ──
        cons = {}
        for lado in ("solicitadas", "participantes"):
            for f in todo[lado]:
                ctg = normaliza_ctg(f.get("CTG/CTDG"))
                if not ctg:
                    continue
                e = cons.setdefault(ctg, {"ctg": ctg, "lados": []})
                e.update({k: v for k, v in f.items() if v not in (None, "")})
                if lado not in e["lados"]:
                    e["lados"].append(lado)
        f = SALIDA / "cpe_arca.json"
        f.write_text(json.dumps({"desde": a.desde, "hasta": a.hasta,
                                 "bajado": datetime.now().isoformat(timespec="seconds"),
                                 "ctg": cons}, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"[+] consolidado: {len(cons)} CTG unicos -> {f}")
        ctx.close()


if __name__ == "__main__":
    main()
