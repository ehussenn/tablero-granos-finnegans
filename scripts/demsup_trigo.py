"""DEM-SUP Trigo — fuente de datos del Extranet Agronasaja (misma arquitectura que soja).

Réplica de la vista "DEM-SUP Trigo" del extranet (/vistas/ops-demsup-trigo):
la fuente OFICIAL por variedad es la API en vivo de esa vista (incluye los ajustes
del analista); las queries propias al DW dan el desglose por producto y quedan de
respaldo. Config portada del historial del extranet (route dem-sup-trigo).

Columnas: C granel en campo · D granel en semillero · K stock clasificado ·
L corte de bolsa · O venta pendiente · P venta despachada · S prod pendiente ·
T prod despachado. Todo en bolsas de 40 kg salvo los dicts *_tn (toneladas).
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone

from demsup_soja import (  # helpers idénticos (misma guía trigo/soja)
    sf, merma_silo, to_bls, _planta_from_dep,
)

VISTA_API_URL = "https://agnsja-operaciones-api.azurewebsites.net/api/operaciones/dem-sup-trigo"
VISTA_API_ORIGIN = "https://sanguine86.github.io"

CAMPANA       = "CAMPAÑA 25-26"
PARTIDA_LIKE  = "26%"
FECHA_MIN_NV  = "2026-02-01"   # NVs/remitos de trigo desde acá (route del extranet)
FECHA_MIN_PED = "2025-07-01"

VARIEDADES = ["DM CASUARINA", "DM CATALPA", "DM TIPA",
              "DM PEHUEN", "DM ARAUCARIA", "DM AROMO", "DM ALERCE"]

PROD_PREFIX     = "SEM. GRANEL TRIGO DM"   # granel (cols C/D)
PROD_PREFIX_SEM = "SEM. TRIGO DM"          # semilla terminada (cols K/L/O/S/T)


def extract_var(producto: str) -> str | None:
    p = (producto or "").upper()
    for v in ("ALERCE", "ARAUCARIA", "AROMO", "CASUARINA", "CATALPA", "PEHUEN", "TIPA"):
        if f"DM {v}" in p:
            return f"DM {v}"
    return None


SQL = {
    "cd": f"""
        SELECT producto, deposito, SUM(CAST(cantidad1 AS NUMERIC)) AS kg_neto
        FROM agronasajasrl_reporte_stock_por_deposito
        WHERE partida = '{CAMPANA}'
          AND producto LIKE '{PROD_PREFIX}%'
          AND (deposito LIKE 'DEP SILOBOLSA%'
            OR deposito LIKE 'GLYCINE PL1 SILO%'
            OR deposito LIKE 'MORSE SILO%'
            OR deposito LIKE 'PERGAMINO SILO%'
            OR deposito LIKE 'ARECO SILO%')
        GROUP BY producto, deposito
        HAVING SUM(CAST(cantidad1 AS NUMERIC)) <> 0
        ORDER BY producto, deposito""",
    "k": f"""
        SELECT producto, SUM(CAST(cantidad1 AS NUMERIC)) AS qty
        FROM agronasajasrl_reporte_stock_por_deposito
        WHERE deposito LIKE 'DEPOSITO VENTAS%'
          AND producto LIKE '{PROD_PREFIX_SEM}%'
        GROUP BY producto
        HAVING SUM(CAST(cantidad1 AS NUMERIC)) <> 0""",
    "l": f"""
        SELECT producto, unidad AS embalaje,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad
        FROM agronasajasrl_analisis_de_consumos_de_produccion
        WHERE tipodedocumento = 'Consumo de Descarte'
          AND producto LIKE '{PROD_PREFIX_SEM}%'
          AND depositoorigen LIKE 'DEPOSITO VENTAS%'
          AND partida LIKE '{PARTIDA_LIKE}'""",
    "o": f"""
        SELECT producto, unidadventa AS embalaje,
               CAST(NULLIF(pendientedestino::text,'NULL') AS NUMERIC) AS pendiente
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE producto LIKE '{PROD_PREFIX_SEM}%'
          AND transacconsubtiponombre IN (
              'Nota de Venta',
              'Nueva Factura de Venta Electrónica Anticipada',
              'Nueva Factura Mipyme Anticipada'
          )
          AND CAST(NULLIF(pendientedestino::text,'NULL') AS NUMERIC) > 0
          AND fechacomprobante >= '{FECHA_MIN_NV}'""",
    "p_rem": f"""
        SELECT producto, unidadcompra AS embalaje,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE transacconsubtiponombre IN ('Remito de Venta','Remito de Venta ANTICIPADA')
          AND partida LIKE '{PARTIDA_LIKE}'
          AND fechacomprobante >= '{FECHA_MIN_NV}'
          AND producto LIKE '{PROD_PREFIX_SEM}%'""",
    "p_dev": f"""
        SELECT producto, unidadcompra AS embalaje,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE transacconsubtiponombre = 'Devolución de Venta (Stock Tercero)'
          AND fechacomprobante >= '{FECHA_MIN_NV}'
          AND producto LIKE '{PROD_PREFIX_SEM}%'""",
    "u": f"""
        SELECT producto, unidadcompra AS embalaje,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad,
               CAST(NULLIF(pendientedestino::text,'NULL') AS NUMERIC) AS pendiente
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE transacconsubtiponombre = 'Pedido de Campo'
          AND producto LIKE '{PROD_PREFIX_SEM}%'
          AND fechacomprobante >= '{FECHA_MIN_PED}'""",
    "t": f"""
        SELECT producto, unidadventa AS embalaje, depositodestino,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE transacconsubtiponombre = 'Traslado interno - Salida'
          AND producto LIKE '{PROD_PREFIX_SEM}%'
          AND partida LIKE '{PARTIDA_LIKE}'
          AND (depositodestino LIKE 'DEPOSITO PRODUCCION%'
               OR depositodestino LIKE 'DEPÓSITO PRODUCCIÓN%')""",
}


def fetch(verbose: bool = True):
    """Igual que demsup_soja.fetch() pero para trigo. Devuelve None sin credenciales."""
    if not all(os.environ.get(k) for k in ("FNN_DW_HOST", "FNN_DW_USER", "FNN_DW_PASS")):
        if verbose:
            print("    [!] FNN_DW_HOST/USER/PASS no seteados — DEM-SUP Trigo omitido")
        return None
    try:
        import psycopg2, psycopg2.extras
    except ImportError:
        return None

    cn = psycopg2.connect(
        host=os.environ["FNN_DW_HOST"],
        dbname=os.environ.get("FNN_DW_DB", "finnegansbi"),
        user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
        port=int(os.environ.get("FNN_DW_PORT", "5432")),
        sslmode="require", connect_timeout=20,
    )
    try:
        cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        def q(key):
            cur.execute(SQL[key])
            return [dict(r) for r in cur.fetchall()]

        zeros = lambda: {v: 0.0 for v in VARIEDADES}

        c_bls, d_bls = zeros(), zeros()
        det_campo, det_semillero = {v: [] for v in VARIEDADES}, {v: [] for v in VARIEDADES}
        campo_tn_prod, semillero_tn_prod = {}, {}
        for r in q("cd"):
            v = extract_var(r["producto"])
            if not v:
                continue
            kg, dep = sf(r["kg_neto"]), r["deposito"] or ""
            m = merma_silo(dep)
            bls = kg * m / 40
            tn = round(kg * m / 1000.0, 4)
            item = {"variedad": v, "deposito": dep, "kilos": round(kg),
                    "merma": m, "bls": round(bls), "tn": tn,
                    "planta": _planta_from_dep(dep)}
            prod = r["producto"] or ""
            if "DEP SILOBOLSA" in dep.upper():
                c_bls[v] += bls
                campo_tn_prod[prod] = round(campo_tn_prod.get(prod, 0.0) + kg * m / 1000.0, 4)
                if kg > 0:
                    det_campo[v].append(item)
            else:
                d_bls[v] += bls
                semillero_tn_prod[prod] = round(semillero_tn_prod.get(prod, 0.0) + kg * m / 1000.0, 4)
                if kg > 0:
                    det_semillero[v].append(item)

        k_bls = zeros()
        clasif_tn_prod = {}
        det_clasif = {v: [] for v in VARIEDADES}
        for r in q("k"):
            v = extract_var(r["producto"])
            if not v:
                continue
            p = (r["producto"] or "").upper()
            qty = sf(r["qty"])
            if "800" in p:
                bls = qty * 20
            elif "40KG" in p or "40 KG" in p:
                bls = qty
            elif "GRANEL" in p:
                bls = qty / 40
            else:
                bls = qty * 20
            k_bls[v] += bls
            prod = r["producto"] or ""
            tn = round(bls * 40 / 1000.0, 4)
            clasif_tn_prod[prod] = round(clasif_tn_prod.get(prod, 0.0) + tn, 4)
            det_clasif[v].append({"variedad": v, "producto": prod,
                                  "cantidad": qty, "bls": round(bls), "tn": tn})

        def _acum_prod(dic, prod, bls):
            tn = round(bls * 40 / 1000.0, 4)
            dic[prod] = round(dic.get(prod, 0.0) + tn, 4)

        l_bls = zeros(); corte_tn_prod = {}
        for r in q("l"):
            v = extract_var(r["producto"])
            if v:
                bls = to_bls(r["cantidad"], r["embalaje"])
                l_bls[v] += bls
                _acum_prod(corte_tn_prod, r["producto"] or "", bls)

        o_bls = zeros(); venta_pend_tn_prod = {}
        for r in q("o"):
            v = extract_var(r["producto"])
            if v:
                bls = to_bls(r["pendiente"], r["embalaje"])
                o_bls[v] += bls
                _acum_prod(venta_pend_tn_prod, r["producto"] or "", bls)

        p_bls = zeros(); venta_desp_tn_prod = {}
        for key in ("p_rem", "p_dev"):
            for r in q(key):
                v = extract_var(r["producto"])
                if v:
                    bls = to_bls(r["cantidad"], r["embalaje"])
                    p_bls[v] += bls
                    _acum_prod(venta_desp_tn_prod, r["producto"] or "", bls)

        s_bls = zeros(); prod_pend_tn_prod = {}
        for r in q("u"):
            v = extract_var(r["producto"])
            if v:
                bls = to_bls(r["pendiente"], r["embalaje"])
                s_bls[v] += bls
                _acum_prod(prod_pend_tn_prod, r["producto"] or "", bls)

        t_bls = zeros(); prod_desp_tn_prod = {}
        for r in q("t"):
            v = extract_var(r["producto"])
            if v:
                bls = to_bls(r["cantidad"], r["embalaje"])
                t_bls[v] += bls
                _acum_prod(prod_desp_tn_prod, r["producto"] or "", bls)
    finally:
        cn.close()

    # ── COPIA EXACTA DE LA VISTA: la API en vivo manda; conciliación por variedad ──
    fuente = "queries propias al DW"
    try:
        req = urllib.request.Request(VISTA_API_URL, headers={"User-Agent": "tablero-granos", "Origin": VISTA_API_ORIGIN})
        api = json.loads(urllib.request.urlopen(req, timeout=60).read())
        api_rows = api.get("rows") if isinstance(api.get("rows"), dict) else None
    except Exception as e:
        api_rows = None
        print(f"    [!] API de la vista DEM-SUP Trigo no disponible ({type(e).__name__}); se usan las queries propias")
    if api_rows:
        fuente = "API en vivo de la vista del extranet"
        anchor = {}
        for prod in list(campo_tn_prod) + list(semillero_tn_prod) + list(clasif_tn_prod):
            v2 = extract_var(prod)
            if v2 and v2 not in anchor:
                anchor[v2] = prod
        gv = lambda v2, col: sf((api_rows.get(v2) or {}).get(col))
        for v2 in VARIEDADES:
            c_bls[v2] = gv(v2, "C"); d_bls[v2] = gv(v2, "D")
            k_bls[v2] = gv(v2, "K"); l_bls[v2] = gv(v2, "L")
            o_bls[v2] = gv(v2, "O"); p_bls[v2] = gv(v2, "P")
            s_bls[v2] = gv(v2, "S"); t_bls[v2] = gv(v2, "T")
        def conciliar(dic, bls_por_var):
            por_var = {}
            for prod, tn in dic.items():
                v2 = extract_var(prod)
                if v2:
                    por_var[v2] = por_var.get(v2, 0.0) + tn
            for v2 in VARIEDADES:
                delta = round(bls_por_var[v2] * 40 / 1000.0 - por_var.get(v2, 0.0), 4)
                if abs(delta) > 0.05:
                    prod = anchor.get(v2) or f"SEM. TRIGO {v2} (vista extranet)"
                    dic[prod] = round(dic.get(prod, 0.0) + delta, 4)
        conciliar(campo_tn_prod, c_bls)
        conciliar(semillero_tn_prod, d_bls)
        conciliar(clasif_tn_prod, k_bls)
        conciliar(corte_tn_prod, l_bls)
        conciliar(venta_pend_tn_prod, o_bls)
        conciliar(venta_desp_tn_prod, p_bls)
        conciliar(prod_pend_tn_prod, s_bls)
        conciliar(prod_desp_tn_prod, t_bls)

    rows, tot = {}, {}
    keys = ("C", "D", "K", "L", "M", "O", "P", "Q", "S", "T")
    for v in VARIEDADES:
        C, D = round(c_bls[v]), round(d_bls[v])
        K, L = round(k_bls[v]), round(l_bls[v])
        O, P = round(o_bls[v]), round(p_bls[v])
        S, T = round(s_bls[v]), round(t_bls[v])
        rows[v] = {"C": C, "D": D, "K": K, "L": L, "M": C + D + K,
                   "O": O, "P": P, "Q": O + P, "S": S, "T": T}
        for k2 in keys:
            tot[k2] = tot.get(k2, 0) + rows[v][k2]
    tot_tn = {k2: round(n * 40 / 1000.0, 3) for k2, n in tot.items()}

    now = datetime.now(timezone.utc)
    return {
        "fuente": f"DEM-SUP Trigo · Extranet Agronasaja ({fuente})",
        "campana": CAMPANA,
        "generated_at": now.isoformat(),
        "variedades": list(VARIEDADES),
        "prod_prefix": PROD_PREFIX,
        "prod_prefix_sem": PROD_PREFIX_SEM,
        "rows": rows,
        "tot_bls": tot,
        "tot_tn": tot_tn,
        "campo_tn_prod": campo_tn_prod,
        "semillero_tn_prod": semillero_tn_prod,
        "clasif_tn_prod": clasif_tn_prod,
        "corte_tn_prod": corte_tn_prod,
        "venta_pend_tn_prod": venta_pend_tn_prod,
        "venta_desp_tn_prod": venta_desp_tn_prod,
        "prod_pend_tn_prod": prod_pend_tn_prod,
        "prod_desp_tn_prod": prod_desp_tn_prod,
        "detalle_campo": det_campo,
        "detalle_semillero": det_semillero,
        "detalle_clasificado": det_clasif,
    }


if __name__ == "__main__":
    d = fetch()
    if d:
        print("fuente:", d["fuente"])
        print(f"{'VAR':<14}{'C':>8}{'D':>8}{'K':>8}{'L':>8}{'O':>8}{'P':>8}{'S':>6}{'T':>6}")
        for v in d["variedades"]:
            r = d["rows"][v]
            print(f"{v:<14}{r['C']:>8}{r['D']:>8}{r['K']:>8}{r['L']:>8}{r['O']:>8}{r['P']:>8}{r['S']:>6}{r['T']:>6}")
        tt = d["tot_tn"]
        print(f"{'TOT TN':<14}{tt['C']:>8}{tt['D']:>8}{tt['K']:>8}{tt['L']:>8}{tt['O']:>8}{tt['P']:>8}")
