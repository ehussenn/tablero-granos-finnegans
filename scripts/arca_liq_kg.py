# -*- coding: utf-8 -*-
"""KILOS E IMPORTES de cada liquidacion de ARCA (pedido del usuario 04/09/2026:
"necesito saber los kg que quedan pendientes por pasar de primaria y secundaria
de manera automatica").

La grilla de ARCA no muestra kilos: hay que abrir el comprobante. El boton
"Ver Liquidacion" de la pagina esta roto en los navegadores modernos (usa
getElementById sin document.), asi que se hace lo mismo a mano:

    1. en la consulta que corresponda, se filtra por COE (campo codOperacionStr)
    2. de la fila sale el liqCodigo (esta en el onclick del boton)
    3. se submitea el form frmGo a generarReporte.do?liqCodigo=NNN
    4. ARCA devuelve el PDF de la liquidacion y de ahi se leen kg e importes

Los PDF y lo parseado quedan cacheados en data/arca/_lpg_pdf/, asi que las
corridas siguientes solo bajan los COE nuevos.

Salida: data/arca/lpg_detalle.json  ->  {coe: {kg, tn, importe, neto, grano, ...}}
Despues scripts/arca_liq_cruce.py lo pega al cruce y el tablero muestra las tn.

Uso:
    py scripts/arca_liq_kg.py                     # los que faltan pasar
    py scripts/arca_liq_kg.py --max 10            # probar con pocos
    py scripts/arca_liq_kg.py --coes 330132387119
    py scripts/arca_liq_kg.py --todos             # TODAS las de ARCA (tarda)
    py scripts/arca_liq_kg.py --solo-parsear      # re-parsea los PDF ya bajados
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))
import arca_cpe_scraper as A
import arca_lpg_scraper as L

DATA = RAIZ / "data"
PDFS = DATA / "arca" / "_lpg_pdf"
SALIDA = DATA / "arca" / "lpg_detalle.json"


def log(*a):
    print(*a, flush=True)


# ── parseo del PDF de la liquidacion ─────────────────────────────────────────
def _num(s: str) -> float:
    """'10,242,192.14' o '9268952.16' -> float. En estos PDF el separador
    decimal es el punto y el de miles la coma."""
    s = re.sub(r"[^\d.,-]", "", str(s or ""))
    if not s:
        return 0.0
    s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return 0.0


# En la SECUNDARIA el grano y el puerto van pegados en la misma celda
# ("15 - TRIGO PAN OTROS"): se saca el puerto del final para quedarse con el grano.
PUERTOS = ("OTROS", "ROSARIO", "SAN LORENZO/SAN MARTIN", "SAN LORENZO", "SAN MARTIN",
           "BAHIA BLANCA", "NECOCHEA",
           "QUEQUEN", "DIAMANTE", "VILLA CONSTITUCION", "RAMALLO", "SAN NICOLAS",
           "SAN PEDRO", "ZARATE", "CAMPANA", "BUENOS AIRES", "LIMA", "ARROYO SECO",
           "PUNTA ALVEAR", "TIMBUES", "ACEVEDO", "BARRANQUERAS", "IBICUY",
           "CONCEPCION DEL URUGUAY", "SANTA FE", "CORDOBA")


def _sin_puerto(g: str) -> str:
    for pto in sorted(PUERTOS, key=len, reverse=True):
        if g.endswith(" " + pto):
            return g[: -len(pto) - 1].strip()
    return g


def parsea_pdf(ruta: Path) -> dict | None:
    try:
        import pdfplumber
    except Exception as e:
        log(f"    [!] falta pdfplumber ({e}); pip install pdfplumber")
        return None
    try:
        with pdfplumber.open(str(ruta)) as pdf:
            txt = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as e:
        log(f"    [!] no pude leer {ruta.name}: {e}")
        return None
    if not txt.strip():
        return None

    d: dict = {"texto_ok": True}
    m = re.search(r"C\.?O\.?E\.?\s*:?\s*(\d{12})", txt)
    if m:
        d["coe"] = m.group(1)

    # grano: "$ 296322 G2 19 - MAIZ $ 0 OTROS"  /  "19 - MAIZ"
    m = re.search(r"\b(\d{1,3})\s*-\s*([A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ /.]{2,40})", txt)
    if m and "Compraventa" not in m.group(2) and "Consignaci" not in m.group(2):
        d["grano_cod"] = m.group(1)
        d["grano"] = _sin_puerto(m.group(2).strip())
    m = re.search(r"COE ORIGINAL\s*:?\s*(\d{12})", txt)
    if m:
        d["coe_original"] = m.group(1)
    m = re.search(r"Tipo de operaci[oó]n\s*:\s*([^\n]+)", txt)
    if m:
        d["operacion"] = m.group(1).strip()

    # bloque OPERACION. Las dos familias lo escriben distinto:
    #   PRIMARIA   31280 Kg $296.32 $9268952.16 10.5 $973239.98 $10242192.14
    #   SECUNDARIA 31.68 Tn $500000.00 $15840000.00 10.5 $1663200.00 $17503200.00 ...
    # OJO: en las de "Ajuste Unificado" la cantidad es 0 a proposito (ajustan precio
    # sobre grano ya entregado) y NO hay que reemplazarla por los kilos de la
    # mercaderia, que ya se contaron en la liquidacion original.
    kg = 0.0
    ops, montos_civa = [], []
    for linea in txt.splitlines():
        m = re.match(r"^\s*([\d.,]+)\s*(Kg|Tn)\s+(\$.*)$", linea)
        if not m:
            continue
        cant = _num(m.group(1)) * (1000.0 if m.group(2) == "Tn" else 1.0)
        montos = [_num(x) for x in re.findall(r"\$\s*([\d.,]+)", m.group(3))]
        kg += cant
        ops.append((cant, m.group(2), montos))
        # columnas: precio | subtotal | importe IVA | operacion c/IVA | ...
        if len(montos) >= 4:
            montos_civa.append(montos[3])
    if ops:
        pr, un, mo = ops[0]
        if mo:
            d["precio_kg"] = round(mo[0] / (1000.0 if un == "Tn" else 1.0), 6)
        d["unidad"] = un
        d["subtotal"] = round(sum(o[2][1] for o in ops if len(o[2]) >= 2), 2)
    if montos_civa:
        d["op_civa"] = round(sum(montos_civa), 2)

    # kilos de MERCADERIA ENTREGADA (las cartas de porte que respaldan la liquidacion).
    # El peso es el ultimo numero de la linea del comprobante: la cantidad de columnas
    # cambia (a veces falta el factor o el contenido proteico).
    kg_cp = 0.0
    cps = []
    for linea in txt.splitlines():
        mm = re.match(r"^\s*(\d{10,14})\s+G\d\b(.*)$", linea)
        if not mm:
            continue
        resto = re.split(r"Localidad", mm.group(2))[0]
        nums = re.findall(r"[\d.,]+", resto)
        if not nums:
            continue
        kg_cp += _num(nums[-1])
        cps.append(mm.group(1))
    if kg_cp:
        d["kg_mercaderia"] = round(kg_cp, 2)
        d["comprobantes"] = cps[:20]
    if not ops and kg_cp:                       # no habia bloque OPERACION
        kg = kg_cp
    d["ajuste"] = bool(re.search(r"AJUSTES? POR IMPORTE|Ajuste Unificado|Ajuste Contrato", txt)) \
                  or (bool(ops) and kg == 0)
    d["kg"] = round(kg, 2)
    d["tn"] = round(kg / 1000.0, 3)

    for etiqueta, clave in (("Total Operaci[oó]n", "importe"),
                            ("Importe Neto a Pagar", "neto"),
                            ("IMPORTE NETO LIQUIDACI[OÓ]N", "neto"),
                            ("TOTAL A PAGAR", "neto"),
                            ("Subtotal General", "importe"),
                            ("Subtotal D[eé]bito-Cr[eé]dito", "subtotal_ajuste"),
                            ("Total Retenciones Afip", "ret_afip"),
                            ("Total Deducciones", "deducciones"),
                            ("Pago seg[uú]n condiciones", "pago")):
        if clave in d:
            continue
        m = re.search(etiqueta + r"\s*:?\s*\$\s*([\d.,]+)", txt)
        if m:
            d[clave] = _num(m.group(1))
    # la secundaria no trae "Total Operacion": el importe es la operacion c/IVA
    if not d.get("importe") and d.get("op_civa"):
        d["importe"] = d["op_civa"]

    m = re.search(r"Precio/TN[^\n]*\n\s*\$?\s*([\d.,]+)", txt)
    if m:
        d["precio_tn"] = _num(m.group(1))

    # razones sociales: el PDF las pone en dos columnas en la misma linea
    m = re.search(r"Raz[oó]n Social:\s*(.+?)\s+Raz[oó]n Social:\s*(.+)", txt)
    if m:
        d["comprador"], d["vendedor"] = m.group(1).strip(), m.group(2).strip()
    ms = re.search(r"C\.U\.I\.T\.:\s*(\d{11})\s+C\.U\.I\.T\.:\s*(\d{11})", txt)
    if ms:
        d["cuit_comprador"], d["cuit_vendedor"] = ms.group(1), ms.group(2)

    # dolares, cuando la operacion se pacto en USD
    m = re.search(r"pactada en d[oó]lares[^\n]*?USD\s*([\d.,]+)", txt)
    if m:
        d["usd"] = _num(m.group(1).replace(".", "X").replace(",", ".").replace("X", ""))
    m = re.search(r"TC\s*([\d.,]+)", txt)
    if m:
        d["tc"] = _num(m.group(1))
    return d


# ── bajada del PDF ───────────────────────────────────────────────────────────
def liq_codigo(pg) -> dict | None:
    """Codigo interno de la liquidacion y la accion que devuelve su PDF.

    Las dos familias de pantallas lo publican distinto:
      PRIMARIA  -> onclick con  pd.jsp?liqCodigo=NNN&action=generarReporte.do?...
      SECUNDARIA-> un <a class="imprimir" data-liqcodigo="NNN" data-tipodeajuste="0">
                   que jQuery manda a generarReportesLSG.do?tipo=T&liqCodigo=NNN
    """
    return pg.evaluate(r"""() => {
      const t = [...document.querySelectorAll('a,img,input,td')]
        .map(e => (e.getAttribute('onclick')||'') + (e.getAttribute('href')||''))
        .find(x => /liqCodigo=(\d+)/.test(x));
      const m = t && t.match(/liqCodigo=(\d+)/);
      if(m) return {cod: m[1], accion: 'generarReporte.do?liqCodigo=' + m[1]};
      const im = document.querySelector('.imprimir[data-liqcodigo], [data-liqcodigo]');
      if(im){
        const c = im.getAttribute('data-liqcodigo');
        const tp = im.getAttribute('data-tipodeajuste') || '0';
        return {cod: c, accion: 'generarReportesLSG.do?tipo=' + tp + '&liqCodigo=' + c};
      }
      return null;
    }""")


def hay_filtro(pg) -> bool:
    try:
        return pg.query_selector("input[name=codOperacionStr]") is not None
    except Exception:
        return False


def refresca_pantalla(pg, menu: str, boton: str) -> bool:
    """Vuelve a pedir la pantalla de la consulta.

    Hace falta despues de CADA reporte: el JSP usa un token HTTP_SAVED_ID de un
    solo uso, el POST de generarReporte.do lo consume y la consulta siguiente
    vuelve sin resultados (se veia como un si / un no alternado)."""
    corto = "Menú principal LSG" if "Secundaria" in menu else "Menú principal LPG"
    if L.click_exacto(pg, corto, 2600) and L.click_exacto(pg, boton, 3000) and hay_filtro(pg):
        return True
    return bool(L.ir_a_consulta(pg, menu, boton)) and hay_filtro(pg)


def baja_pdf(pg, coe: str, menu: str, boton: str, refrescar: bool = False) -> tuple[Path | None, str]:
    """Filtra por COE en la consulta que este en pantalla y baja el PDF."""
    if refrescar or not hay_filtro(pg):
        if not refresca_pantalla(pg, menu, boton):
            return None, "no pude volver a la pantalla de consulta"
    try:
        pg.fill("input[name=fechaStr]", "", timeout=8000)
        pg.fill("input[name=fechaHastaStr]", "", timeout=8000)
        pg.fill("input[name=codOperacionStr]", coe, timeout=8000)
    except Exception as e:
        return None, f"no pude cargar el filtro: {str(e)[:60]}"
    if not L.click_exacto(pg, "Consultar Por Criterio", 3800):
        return None, "no encontre el boton Consultar"
    for _ in range(12):
        ref = liq_codigo(pg)
        if ref:
            break
        pg.wait_for_timeout(500)
    else:
        return None, "la consulta no devolvio el comprobante"
    destino = PDFS / f"{coe}.pdf"
    try:
        with pg.expect_download(timeout=60_000) as dl:
            pg.evaluate("""(accion) => {
              const f = document.frmGo || document.forms['frmGo'];
              f.action = accion;
              f.method = 'post';
              f.submit();
            }""", ref["accion"])
        dl.value.save_as(str(destino))
    except Exception as e:
        return None, f"no bajo el PDF: {str(e)[:90]}"
    if destino.stat().st_size < 800:
        return None, "el PDF vino vacio"
    return destino, ref["cod"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coes", help="lista de COE separados por coma")
    ap.add_argument("--todos", action="store_true", help="todas las liquidaciones de ARCA")
    ap.add_argument("--max", type=int, default=0, help="cortar despues de N (para probar)")
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--forzar", action="store_true", help="vuelve a bajar los PDF ya cacheados")
    ap.add_argument("--solo-parsear", action="store_true", dest="solo_parsear",
                    help="no entra a ARCA: re-parsea los PDF que ya estan bajados")
    a = ap.parse_args()

    PDFS.mkdir(parents=True, exist_ok=True)
    cruce = DATA / "arca_liq_cruce.json"
    if not cruce.exists():
        raise SystemExit(f"[!] falta {cruce} — corre primero scripts/arca_liq_cruce.py")
    C = json.loads(cruce.read_text(encoding="utf-8"))

    # de donde saco cada COE (la consulta dice en que pantalla esta)
    if a.coes:
        pedidos = [(c.strip(), "lpg_recibidas") for c in a.coes.split(",") if c.strip()]
        origen = {c: cs for c, cs in pedidos}
        for f in C["filas"]:
            if f["coe"] in origen:
                origen[f["coe"]] = f["consulta"]
        objetivo = [(c, origen[c]) for c, _ in pedidos]
    elif a.todos:
        vistos = {}
        for f in C["filas"]:
            vistos.setdefault(f["coe"], f["consulta"])
        objetivo = sorted(vistos.items())
    else:
        objetivo = [(f["coe"], f["consulta"]) for f in C["faltan"]]

    # lo que ya tengo parseado
    det = json.loads(SALIDA.read_text(encoding="utf-8")).get("detalle", {}) if SALIDA.exists() else {}

    if a.solo_parsear:
        log("[+] re-parseando los PDF cacheados")
        n = 0
        for f in sorted(PDFS.glob("*.pdf")):
            d = parsea_pdf(f)
            if d:
                det[f.stem] = d
                n += 1
        log(f"    {n} PDF parseados")
    else:
        pendientes = [(c, s) for c, s in objetivo
                      if a.forzar or (c not in det and not (PDFS / f"{c}.pdf").exists())]
        # los que tienen PDF pero no estan parseados
        for c, s in objetivo:
            if c not in det and (PDFS / f"{c}.pdf").exists() and not a.forzar:
                d = parsea_pdf(PDFS / f"{c}.pdf")
                if d:
                    det[c] = d
        if a.max:
            pendientes = pendientes[:a.max]
        log(f"[+] {len(objetivo)} liquidaciones a mirar · {len(pendientes)} PDF por bajar "
            f"· {len(det)} ya parseadas")

        if pendientes:
            env = A.cargar_env()
            if not env.get("ARCA_CUIT") or not env.get("ARCA_CLAVE"):
                raise SystemExit("[!] faltan ARCA_CUIT / ARCA_CLAVE en el .env local")
            from playwright.sync_api import sync_playwright
            # agrupo por consulta para no navegar el menu en cada COE
            porc: dict[str, list[str]] = {}
            for c, s in pendientes:
                porc.setdefault(s, []).append(c)
            ok = err = 0
            with sync_playwright() as pw:
                ctx = A.abrir(pw, a.visible)
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                log("[1] login")
                A.login(pg, env["ARCA_CUIT"], env["ARCA_CLAVE"])
                if not A.espera_portal(pg, 150):
                    raise SystemExit(f"[!] no llegue al portal: {pg.url}")
                d = L.entrar_servicio(ctx, pg)
                if d is None:
                    raise SystemExit("[!] no pude entrar al servicio")
                for consulta, coes in porc.items():
                    menu, boton = L.CONSULTAS.get(consulta, L.CONSULTAS["lpg_recibidas"])
                    log(f"\n[2] {consulta} — {len(coes)} liquidaciones")
                    if not L.ir_a_consulta(d, menu, boton):
                        log("    [!] no pude entrar a la consulta, salteo")
                        continue
                    bajado = False
                    for i, coe in enumerate(coes, 1):
                        ruta, info = baja_pdf(d, coe, menu, boton, refrescar=bajado)
                        bajado = ruta is not None
                        if ruta is None:
                            log(f"    {i}/{len(coes)} {coe}: {info}")
                            err += 1
                            continue
                        p = parsea_pdf(ruta)
                        if not p:
                            log(f"    {i}/{len(coes)} {coe}: PDF bajado pero no lo pude leer")
                            err += 1
                            continue
                        p["liq_codigo"] = info
                        det[coe] = p
                        ok += 1
                        log(f"    {i}/{len(coes)} {coe}: {p.get('tn', 0):,.3f} tn · "
                            f"$ {p.get('importe', 0):,.2f} · {p.get('grano', '?')}"
                            + ("  (ajuste, 0 tn nuevas)" if p.get("ajuste") else ""))
                try:
                    ctx.close()
                except Exception:
                    pass
            log(f"\n    bajadas OK {ok} · con problema {err}")

    tn = sum(v.get("tn", 0) for v in det.values())
    SALIDA.write_text(json.dumps({
        "generado": datetime.now().isoformat(timespec="seconds"),
        "n": len(det), "tn": round(tn, 3),
        "detalle": det,
    }, ensure_ascii=False), encoding="utf-8")
    log(f"\n[OK] {len(det)} liquidaciones con kilos · {tn:,.1f} tn -> {SALIDA.name}")

    # cuanto de eso es de lo que falta pasar
    falt = [f["coe"] for f in C["faltan"]]
    con = [c for c in falt if c in det]
    tnf = sum(det[c].get("tn", 0) for c in con)
    impf = sum(det[c].get("importe", 0) for c in con)
    log(f"     de los {len(falt)} que faltan pasar tengo kilos de {len(con)}: "
        f"{tnf:,.1f} tn · $ {impf:,.0f}")


if __name__ == "__main__":
    main()
