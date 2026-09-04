# -*- coding: utf-8 -*-
"""ARCA (ex AFIP) - bajada de LIQUIDACIONES de granos para el cruce con Finnegans.

Pedido del usuario (04/09/2026): entrar a "Liquidacion primaria de granos", elegir la
firma AGRONASAJA SRL y bajar las CUATRO consultas:

    1. LPG emitidas    (Liquidacion Primaria   - Consulta de Liquidaciones Emitidas)
    2. LPG recibidas   (Liquidacion Primaria   - Consulta Liquidaciones Recibidas)
    3. LSG emitidas    (Liquidacion Secundaria - Consulta de Liquidaciones Emitidas)
    4. LSG recibidas   (Liquidacion Secundaria - Consulta de Liquidaciones Recibidas)

La clave del cruce es el COE (12 digitos). Prefijos que se ven en la practica:
    3301 / 3302 -> primaria (compraventa / consignacion)      3310 -> secundaria

Credenciales: SOLO del .env local (ARCA_CUIT / ARCA_CLAVE), que .gitignore excluye.
Nunca se escriben en el repo.

Uso:
    py scripts/arca_lpg_scraper.py                        # ultimos 60 dias
    py scripts/arca_lpg_scraper.py --desde 2025-03-01     # historico
    py scripts/arca_lpg_scraper.py --solo lpg_recibidas
    py scripts/arca_lpg_scraper.py --forzar               # ignora el cache de ventanas
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))
import arca_cpe_scraper as A          # reusa login / perfil persistente / modales

SALIDA = RAIZ / "data" / "arca"
VENTANAS = SALIDA / "_lpg_ventanas"
SERVICIO = "Liquidación primaria de granos"
CUIT_AGNSJ = "30710712758"

# Las cuatro consultas que pidio, mas las dos "por comprador / por vendedor" que
# completan el panorama (la pantalla de Recibidas de la secundaria solo muestra las
# pendientes de aceptar; las ventas secundarias reales aparecen en "por Vendedor").
CONSULTAS = {
    "lpg_emitidas":      ("Liquidación Primaria de Granos",   "Consulta de Liquidaciones Emitidas"),
    "lpg_recibidas":     ("Liquidación Primaria de Granos",   "Consulta Liquidaciones Recibidas"),
    "lsg_emitidas":      ("Liquidación Secundaria de Granos",  "Consulta de Liquidaciones Emitidas"),
    "lsg_recibidas":     ("Liquidación Secundaria de Granos",  "Consulta de Liquidaciones Recibidas"),
    "lpg_por_comprador": ("Liquidación Primaria de Granos",   "Consulta Liquidaciones por Comprador"),
    "lsg_por_vendedor":  ("Liquidación Secundaria de Granos",  "Consulta de Liquidaciones por Vendedor"),
}

# Que significa cada consulta para Agronasaja (verificado contra los datos 09/2026):
#   lpg_emitidas / lpg_por_comprador -> COMPRAS a productores (Agronasaja emite la LPG)
#   lpg_recibidas                    -> VENTAS (el comprador o su corredor le emite la LPG)
#   lsg_emitidas / lsg_por_vendedor  -> VENTAS secundarias (Agronasaja emite como vendedor)
#   lsg_recibidas                    -> COMPRAS secundarias (le emiten a Agronasaja)
LADO = {
    "lpg_emitidas": "compra", "lpg_por_comprador": "compra", "lsg_recibidas": "compra",
    "lpg_recibidas": "venta", "lsg_emitidas": "venta", "lsg_por_vendedor": "venta",
}


def log(*a):
    print(*a, flush=True)


# -- navegacion ---------------------------------------------------------------
def entrar_servicio(ctx, pg):
    """Portal -> tarjeta 'Liquidacion primaria de granos' (abre pestana nueva) ->
    elegir la firma AGRONASAJA SRL. Devuelve la pagina del servicio.

    OJO: entrar derecho por URL a serviciosjava2 no sirve; la sesion del JSP se
    arma cuando el portal hace el POST de la tarjeta."""
    for sel in ("#buscadorInput", "input[placeholder*='Buscá']", "input[type='search']"):
        b = pg.query_selector(sel)
        if b:
            b.click(); b.fill(SERVICIO); pg.wait_for_timeout(2500)
            break
    antes = set(ctx.pages)
    h = pg.evaluate_handle(r"""() => [...document.querySelectorAll('a.full-width, a')]
          .find(e => /liquidaci[oó]n primaria de granos/i.test(e.innerText||''))""").as_element()
    if not h:
        log("    [!] no encontre la tarjeta del servicio")
        return None
    try:
        h.click()
    except Exception:
        pg.evaluate("(e) => e.click()", h)
    d = pg
    for _ in range(25):
        pg.wait_for_timeout(700)
        otras = [x for x in ctx.pages if x not in antes]
        if otras:
            d = otras[-1]
            break
    try:
        d.bring_to_front()
    except Exception:
        pass
    d.wait_for_timeout(4000)
    if "Seleccione la Empresa" in (d.inner_text("body") or ""):
        h2 = d.evaluate_handle(r"""() => [...document.querySelectorAll('button,input[type=button],a,td,div')]
              .find(e => /^\s*AGRONASAJA/i.test(e.innerText||e.value||''))""").as_element()
        if not h2:
            log("    [!] no encontre la firma AGRONASAJA SRL")
            return None
        try:
            h2.click()
        except Exception:
            d.evaluate("(e) => e.click()", h2)
        d.wait_for_timeout(5000)
    cuerpo = d.inner_text("body") or ""
    log(f"    servicio abierto ({'AGRONASAJA SRL' if CUIT_AGNSJ in cuerpo else 'revisar firma'}) "
        f"-> {d.url[:80]}")
    return d


def click_exacto(pg, txt: str, espera: int = 3800) -> bool:
    """Click en el boton/link cuyo texto ES exactamente txt (los menus del JSP)."""
    h = pg.evaluate_handle(
        r"""(t) => [...document.querySelectorAll('button,input[type=button],input[type=submit],a')]
              .find(e => (e.innerText||e.value||'').replace(/\s+/g,' ').trim().toLowerCase() === t.toLowerCase())""",
        txt).as_element()
    if not h:
        return False
    try:
        h.click()
    except Exception:
        pg.evaluate("(e) => e.click()", h)
    pg.wait_for_timeout(espera)
    return True


def ir_a_consulta(pg, menu: str, consulta: str) -> bool:
    """Desde cualquier pantalla: raiz -> menu del servicio -> consulta."""
    for _ in range(3):
        if click_exacto(pg, "Menú principal", 3000):
            break
        pg.wait_for_timeout(800)
    if not click_exacto(pg, menu):
        log(f"    [!] no pude entrar a '{menu}'")
        return False
    if not click_exacto(pg, consulta):
        log(f"    [!] no pude entrar a '{consulta}'")
        return False
    return True


# -- lectura de la grilla -----------------------------------------------------
# La grilla de resultados es la tabla cuya fila de encabezado tiene 'Coe'
# (la otra tabla con 'Coe' es la de filtros, que tiene inputs adentro).
JS_GRILLA = r"""() => {
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  let mejor = null;
  for(const t of document.querySelectorAll('table')){
    // el JSP anida tablas: la grilla real es una tabla HOJA (sin tablas adentro).
    // Si no se filtra, el contenedor gana y los encabezados salen del cuadro de filtros.
    if(t.querySelector('table')) continue;
    const trs = [...t.querySelectorAll('tr')];
    if(trs.length < 2) continue;
    // el COE se llama 'Coe' en casi todas las consultas y 'Codigo de operacion'
    // en la de "por Vendedor" (LSG)
    const ESCOE = /\bcoe\b|c[oó]digo de operaci/i;
    let hi = -1;
    for(let i = 0; i < Math.min(trs.length, 4); i++){
      const tr = trs[i];
      // el cuadro de filtros tambien dice 'Coe': se descarta porque su fila de
      // encabezado tiene menos columnas que la grilla (5 contra 8+)
      if(!ESCOE.test(txt(tr)) || tr.querySelector('input,select')) continue;
      if([...tr.querySelectorAll('th,td')].length < 6) continue;
      hi = i; break;
    }
    if(hi < 0) continue;
    const heads = [...trs[hi].querySelectorAll('th,td')].map((e,i) => txt(e) || ('col'+i));
    if(heads.length < 6) continue;
    const filas = trs.slice(hi+1)
      .map(tr => [...tr.querySelectorAll('td,th')].map(txt))
      .filter(f => f.some(c => c) && f.length >= 4);
    if(!mejor || filas.length > mejor.filas.length) mejor = {heads, filas};
  }
  return mejor;
}"""

AVISO = re.compile(r"no se encontraron|sin resultados|no hay datos|no existen|cargando", re.I)


def leer_grilla(pg) -> dict:
    d = pg.evaluate(JS_GRILLA)
    if not d:
        return {"heads": [], "filas": []}
    filas = [f for f in d["filas"]
             if not (len([c for c in f if c]) <= 2 and AVISO.search(" ".join(f)))]
    return {"heads": d["heads"], "filas": filas}


def consultar(pg, d0: str, d1: str) -> dict:
    """Llena fecha desde/hasta y aprieta 'Consultar Por Criterio'."""
    try:
        pg.fill("input[name=fechaStr]", d0)
        pg.fill("input[name=fechaHastaStr]", d1)
    except Exception as e:
        log(f"    [!] no pude cargar las fechas: {e}")
        return {"heads": [], "filas": []}
    if not click_exacto(pg, "Consultar Por Criterio", 4500):
        log("    [!] no encontre el boton Consultar Por Criterio")
        return {"heads": [], "filas": []}
    for _ in range(30):
        g = leer_grilla(pg)
        if g["filas"]:
            return g
        cuerpo = (pg.inner_text("body") or "").lower()
        if "no se encontraron" in cuerpo or "no hay datos" in cuerpo or "no existen" in cuerpo:
            return g
        pg.wait_for_timeout(600)
    return leer_grilla(pg)


def a_dicts(g: dict) -> list[dict]:
    heads = g.get("heads") or []
    out = []
    for f in g.get("filas") or []:
        r = {}
        for j, v in enumerate(f):
            k = heads[j] if j < len(heads) else f"col{j}"
            if k in r:
                k = f"{k}_{j}"
            r[k] = v
        out.append(r)
    return out


# -- ventanas de fechas + cache ----------------------------------------------
def ventanas(desde: date, hasta: date, dias: int = 30):
    """Corre de a un mes (como lo pidio para las cartas de porte)."""
    a = desde
    while a <= hasta:
        b = min(a + timedelta(days=dias - 1), hasta)
        yield a, b
        a = b + timedelta(days=1)


def f_arca(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def bajar(pg, clave: str, desde: date, hasta: date, forzar: bool, dias: int) -> list[dict]:
    menu, consulta = CONSULTAS[clave]
    VENTANAS.mkdir(parents=True, exist_ok=True)
    todo, en_pantalla = [], False
    for a, b in ventanas(desde, hasta, dias):
        cache = VENTANAS / f"{clave}_{a}_{b}.json"
        if cache.exists() and not forzar:
            filas = json.loads(cache.read_text(encoding="utf-8")).get("filas") or []
            log(f"    {clave} {a} a {b}: {len(filas)} filas (cache)")
            todo += filas
            continue
        if not en_pantalla:
            if not ir_a_consulta(pg, menu, consulta):
                break
            en_pantalla = True
        A.cerrar_modales(pg)
        g = consultar(pg, f_arca(a), f_arca(b))
        filas = a_dicts(g)
        log(f"    {clave} {a} a {b}: {len(filas)} filas")
        cache.write_text(json.dumps({"clave": clave, "desde": str(a), "hasta": str(b),
                                     "heads": g.get("heads"), "filas": filas,
                                     "bajado": datetime.now().isoformat(timespec="seconds")},
                                    ensure_ascii=False), encoding="utf-8")
        todo += filas
    return todo


# -- normalizacion ------------------------------------------------------------
def norm_fila(clave: str, r: dict) -> dict | None:
    """Deja una fila con nombres estables. Cada consulta trae encabezados distintos."""
    def g(*ks):
        for k in ks:
            v = r.get(k)
            if v not in (None, ""):
                return str(v).strip()
        return ""
    coe = re.sub(r"\D", "", g("Coe", "COE", "coe",
                               "Codigo de operación", "Código de operación",
                               "Codigo de operacion"))
    if len(coe) != 12:
        return None
    fecha = g("Fecha", "Fecha Emisión", "Fecha Emision")
    try:
        iso = datetime.strptime(fecha[:10], "%d/%m/%Y").date().isoformat()
    except Exception:
        iso = ""
    return {
        "coe": coe,
        "fecha": iso,
        "fecha_arca": fecha,
        "consulta": clave,
        "tipo": "primaria" if clave.startswith("lpg") else "secundaria",
        "flujo": "emitida" if ("emitidas" in clave or "comprador" in clave
                               or clave == "lsg_por_vendedor") else "recibida",
        "lado": LADO.get(clave, ""),
        # en emitidas la contraparte viene como 'Cuit Vendedor'; en recibidas como 'Cuit emisor'
        "cuit": re.sub(r"\D", "", g("Cuit Vendedor", "Cuit emisor", "Cuit Emisor", "CUIT")),
        "denominacion": g("Denominación", "Denominacion"),
        "sistema": g("Sistema"),
        "operacion": g("Tipo operaciÓn", "Tipo operación", "Tipo operacion"),
        "estado": g("Estado"),
        "sujeto": g("Sujeto emisor"),
    }


def consolidar(res: dict, desde: date, hasta: date) -> Path:
    filas, vistos = [], set()
    for k, rs in res.items():
        for r in rs:
            n = norm_fila(k, r)
            if not n:
                continue
            key = (n["coe"], k)
            if key in vistos:
                continue
            vistos.add(key)
            filas.append(n)
    SALIDA.mkdir(parents=True, exist_ok=True)
    f = SALIDA / "lpg_liquidaciones.json"
    f.write_text(json.dumps({
        "bajado": datetime.now().isoformat(timespec="seconds"),
        "desde": str(desde), "hasta": str(hasta),
        "cuit": CUIT_AGNSJ,
        "por_consulta": {k: sum(1 for x in filas if x["consulta"] == k) for k in CONSULTAS},
        "filas": filas,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"\n[OK] {len(filas)} liquidaciones ({len({x['coe'] for x in filas})} COE unicos) -> {f}")
    for k in CONSULTAS:
        log(f"     {k:16s} {sum(1 for x in filas if x['consulta'] == k):5d}")
    return f


def desde_cache(claves: list[str]) -> dict:
    """Rearma el consolidado leyendo todas las ventanas ya bajadas."""
    res = {k: [] for k in claves}
    for f in sorted(VENTANAS.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        k = d.get("clave")
        if k in res:
            res[k] += d.get("filas") or []
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde")
    ap.add_argument("--hasta")
    ap.add_argument("--dias", type=int, default=45, help="tamano de la ventana de consulta")
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--forzar", action="store_true", help="ignora el cache de ventanas")
    ap.add_argument("--solo", help="una sola consulta: " + " | ".join(CONSULTAS))
    ap.add_argument("--consolidar", action="store_true",
                    help="no entra a ARCA: rearma el JSON desde las ventanas cacheadas")
    a = ap.parse_args()

    hasta = date.fromisoformat(a.hasta) if a.hasta else date.today()
    desde = date.fromisoformat(a.desde) if a.desde else hasta - timedelta(days=60)
    claves = [a.solo] if a.solo else list(CONSULTAS)
    for k in claves:
        if k not in CONSULTAS:
            raise SystemExit(f"[!] consulta desconocida: {k}")

    if a.consolidar:
        consolidar(desde_cache(list(CONSULTAS)), desde, hasta)
        return

    env = A.cargar_env()
    if not env.get("ARCA_CUIT") or not env.get("ARCA_CLAVE"):
        raise SystemExit("[!] faltan ARCA_CUIT / ARCA_CLAVE en el .env local")

    log(f"[+] ARCA liquidaciones - {desde} a {hasta} - ventanas de {a.dias} dias")
    from playwright.sync_api import sync_playwright
    res = {}
    with sync_playwright() as pw:
        ctx = A.abrir(pw, a.visible)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        log("[1] login")
        A.login(pg, env["ARCA_CUIT"], env["ARCA_CLAVE"])
        if not A.espera_portal(pg, 150):
            raise SystemExit(f"[!] no llegue al portal: {pg.url}")
        log("[2] entrando al servicio de liquidaciones")
        d = entrar_servicio(ctx, pg)
        if d is None:
            raise SystemExit("[!] no pude entrar al servicio")
        for k in claves:
            log(f"\n[3] {k}")
            res[k] = bajar(d, k, desde, hasta, a.forzar, a.dias)
        try:
            ctx.close()
        except Exception:
            pass

    # el consolidado siempre sale de TODAS las ventanas cacheadas, no solo de esta corrida
    consolidar(desde_cache(list(CONSULTAS)), desde, hasta)


if __name__ == "__main__":
    main()
