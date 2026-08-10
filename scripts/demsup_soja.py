"""DEM-SUP Soja — fuente de datos del Extranet Agronasaja (DW Finnegans).

Réplica 1:1 de la vista "DEM-SUP Soja" del extranet (/vistas/ops-demsup-soja),
cuyo backend vive hoy en el servicio agnsja-operaciones-api. Las queries y la
lógica (merma por depósito, bolsas de 40 kg, filtros de partida/campaña) están
portadas del código original del extranet (src/lib/operaciones/dem-sup-common.ts
+ scripts/dem-sup-soja/gen_dem_sup_soja.py, historial git del repo
agronasaja-extranet, commit fdbc19d9^).

Se conecta DIRECTO al mismo DW Postgres (finnegansbi) que usa el extranet, con
las credenciales FNN_DW_* que ya usa build.py. Cuando el tablero pase a formar
parte del extranet, esta misma estructura de payload se puede servir desde una
API route del extranet (GET /api/operaciones/dem-sup-soja) sin tocar el frontend:
el shape del payload es el contrato.

Columnas (mismas letras que la vista del extranet):
  C = GRANEL EN CAMPO       (DEP SILOBOLSA%, partida campaña, kg x merma / 40)
  D = GRANEL EN SEMILLERO   (silos PERGAMINO/MORSE/GLYCINE/ARECO, kg x merma / 40)
  E = COMPRAS GRANEL REMITIDAS   (contratos compra, tn entregada x 25)
  F = COMPRAS GRANEL PENDIENTES  (contratos compra, tn pendiente x 25)
  G = COMPRAS GRANEL TOTAL       (E + F = tn contratada x 25)
  K = STOCK CLASIFICADO     (semilla terminada en DEPOSITO VENTAS%, en bolsas;
                             verificado contra la vista viva 13/14 variedades —
                             DM 46I20 tiene la partida rota en Finnegans y la
                             vista del analista la ajusta a mano)
  L = CORTE DE BOLSA        (consumos de descarte en DEPOSITO VENTAS)
  M = POTENCIAL TOTAL       (C + D + F + K)
  O = VENTA PENDIENTE       (NVs pendientes, en bolsas 40 kg)
  P = VENTA DESPACHADA      (remitos + devoluciones, en bolsas 40 kg)
  Q = VENTA TOTAL           (O + P)
  S = PROD PENDIENTE        (pedidos de campo pendientes)
  T = PROD DESPACHADO       (traslados internos a DEPOSITO PRODUCCION)

Todo en BOLSAS de 40 kg salvo los dicts *_tn (toneladas = bls * 40 / 1000).
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# API en vivo de la vista del extranet (la MISMA que consume /vistas/ops-demsup-soja).
# Incluye los ajustes del analista (ej. DM 46I20 con la partida rota en Finnegans).
# Si responde, sus números por variedad son los OFICIALES; las queries propias de
# abajo quedan como respaldo y para el desglose por producto.
VISTA_API_URL = "https://agnsja-operaciones-api.azurewebsites.net/api/operaciones/dem-sup-soja"
VISTA_API_ORIGIN = "https://sanguine86.github.io"

# ─── Campaña vigente del DEM-SUP (actualizar cuando cambie el ciclo) ─────────
CAMPANA        = "CAMPAÑA 25-26"   # partida/campaña del granel y las compras
PARTIDA_LIKE   = "26%"             # partida de la semilla clasificada (ciclo 26/27)
FECHA_MIN_NV   = "2026-03-01"      # NVs de venta desde acá (pre-órdenes ciclo siembra)
FECHA_MIN_PED  = "2025-07-01"      # pedidos de campo desde acá

# Variedades oficiales (mismas 14 que la vista del extranet; legacy se ignoran)
VARIEDADES = [
    "DM 33E22", "DM 33R22", "DM 38E26", "DM 40E25", "DM 40R26",
    "DM 46E25", "DM 46I20", "DM 46R25", "DM 47E23", "DM 49R19",
    "DM 49R26", "DM 50E22", "DM 50E25", "DM 52E21",
]

# Prefijo de producto del granel en el Stock por Depósito (para cruzar con el
# stock del tablero: estas filas de silobolsa salen del DEM-SUP, no del crudo)
PROD_PREFIX = "SEM. GRANEL SOJA DM"
# Prefijo de la semilla TERMINADA (embolsada/clasificada) — cols K, L, O, S, T
PROD_PREFIX_SEM = "SEM. SOJA DM"


# ─── .env local (mismo esquema que finnegans_api.py) ─────────────────────────
def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_dotenv()

# Sanear credenciales (espacios/saltos de línea pegados de más en los secretos de GitHub)
for _k in ("FNN_DW_HOST", "FNN_DW_USER", "FNN_DW_PASS", "FNN_DW_DB", "FNN_DW_PORT"):
    if os.environ.get(_k):
        os.environ[_k] = os.environ[_k].strip()


# ─── Helpers (idénticos al extranet) ─────────────────────────────────────────
def sf(v) -> float:
    if v is None or str(v).strip().upper() in ("NULL", "", "NONE"):
        return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


def extract_var(producto: str) -> str | None:
    p = (producto or "").upper()
    for v in VARIEDADES:
        if v in p:
            return v
    return None


def merma_silo(dep: str) -> float:
    """Factor de merma según depósito (guía DEM-SUP §5)."""
    d = (dep or "").upper()
    if "GLYCINE" in d:
        return 1.0
    if "MORSE SILO 30" in d:
        return 0.0
    if "ARECO" in d:
        return 0.4
    return 0.9   # MORSE, PERGAMINO, DEP SILOBOLSA


def to_bls(cantidad, embalaje: str) -> float:
    """Convierte cantidad+unidad a bolsas de 40 kg."""
    e = (embalaje or "").upper()
    c = sf(cantidad)
    if "800" in e or "BOLSON" in e:
        return c * 20
    if "KILO" in e:
        return c / 40
    return c   # ya viene en bolsas 40 kg


def _planta_from_dep(dep: str) -> str:
    d = (dep or "").upper()
    for planta in ("GLYCINE", "MORSE", "PERGAMINO", "ARECO"):
        if planta in d:
            return planta
    return "CAMPO"


# ─── Queries (portadas 1:1 del route del extranet) ───────────────────────────
SQL = {
    # Col C + D: granel en campo (silobolsa) y en semillero (silos planta).
    # La partida de DM 46I20 está mal cargada en Finnegans (queda vacía); se
    # acepta partida vacía cuando el depósito ya identifica la campaña.
    "cd": f"""
        SELECT producto, deposito, SUM(CAST(cantidad1 AS NUMERIC)) AS kg_neto
        FROM agronasajasrl_reporte_stock_por_deposito
        WHERE producto LIKE '{PROD_PREFIX}%'
          AND (
            partida = '{CAMPANA}'
            OR (
              (partida IS NULL OR partida = '' OR partida = 'NULL')
              AND (
                deposito LIKE 'GLYCINE PL1 SILO%'
                OR deposito LIKE 'MORSE SILO%'
                OR deposito LIKE 'PERGAMINO SILO%'
                OR deposito LIKE 'ARECO SILO%'
                OR (deposito LIKE 'DEP SILOBOLSA%' AND deposito LIKE '%25-26%')
              )
            )
          )
          AND (deposito LIKE 'DEP SILOBOLSA%'
            OR deposito LIKE 'GLYCINE PL1 SILO%'
            OR deposito LIKE 'MORSE SILO%'
            OR deposito LIKE 'PERGAMINO SILO%'
            OR deposito LIKE 'ARECO SILO%')
        GROUP BY producto, deposito
        HAVING SUM(CAST(cantidad1 AS NUMERIC)) <> 0
        ORDER BY producto, deposito""",
    # Col E/F: contratos de compra del granel (1 tn = 25 bolsas brutas de 40 kg)
    "ef": f"""
        SELECT
          TO_CHAR(fecha::date,'YYYY-MM-DD') AS fecha,
          organizacion,
          nombre AS contrato,
          producto,
          CAST(NULLIF(NULLIF(cantidadmax,''),'NULL')                AS NUMERIC) AS tn_max,
          CAST(NULLIF(NULLIF(cantidadentregada,''),'NULL')          AS NUMERIC) AS tn_entregada,
          CAST(NULLIF(NULLIF(cantidadpendienteentrega,''),'NULL')   AS NUMERIC) AS tn_pendiente
        FROM agronasajasrl_resumen_de_contrato_de_compra_de_granos
        WHERE producto LIKE '{PROD_PREFIX}%'
          AND campana = '{CAMPANA}'
          AND CAST(NULLIF(NULLIF(cantidadmax,''),'NULL') AS NUMERIC) > 0
        ORDER BY producto, fecha""",
    # Col K: stock clasificado (semilla terminada en depósitos de venta).
    # La cantidad viene en unidades del embalaje; se convierte a bolsas de
    # 40 kg según el nombre del producto (800KG=bolsón x20, 40KG=bolsa, GRANEL=kg).
    "k": """
        SELECT producto, SUM(CAST(cantidad1 AS NUMERIC)) AS qty
        FROM agronasajasrl_reporte_stock_por_deposito
        WHERE deposito LIKE 'DEPOSITO VENTAS%'
          AND producto LIKE 'SEM. SOJA DM%'
        GROUP BY producto
        HAVING SUM(CAST(cantidad1 AS NUMERIC)) <> 0""",
    # Col L: corte de bolsa (descarte de semilla ya embolsada)
    "l": f"""
        SELECT producto, unidad AS embalaje,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad
        FROM agronasajasrl_analisis_de_consumos_de_produccion
        WHERE tipodedocumento = 'Consumo de Descarte'
          AND producto LIKE 'SEM. SOJA DM%'
          AND depositoorigen LIKE 'DEPOSITO VENTAS%'
          AND partida LIKE '{PARTIDA_LIKE}'""",
    # Col O: venta pendiente (NVs)
    "o": f"""
        SELECT producto, unidadventa AS embalaje,
               CAST(NULLIF(pendientedestino::text,'NULL') AS NUMERIC) AS pendiente
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE producto LIKE 'SEM. SOJA DM%'
          AND transacconsubtiponombre IN (
              'Nota de Venta',
              'Nueva Factura de Venta Electrónica Anticipada',
              'Nueva Factura Mipyme Anticipada'
          )
          AND CAST(NULLIF(pendientedestino::text,'NULL') AS NUMERIC) > 0
          AND fechacomprobante >= '{FECHA_MIN_NV}'""",
    # Col P: despachado (remitos) + devoluciones
    "p_rem": f"""
        SELECT producto, unidadcompra AS embalaje,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE transacconsubtiponombre IN ('Remito de Venta','Remito de Venta ANTICIPADA')
          AND partida LIKE '{PARTIDA_LIKE}'
          AND producto LIKE 'SEM. SOJA DM%'""",
    "p_dev": f"""
        SELECT producto, unidadcompra AS embalaje,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE transacconsubtiponombre = 'Devolución de Venta (Stock Tercero)'
          AND partida LIKE '{PARTIDA_LIKE}'
          AND producto LIKE 'SEM. SOJA DM%'""",
    # Col S/U: pedidos de campo (producción propia)
    "u": f"""
        SELECT producto, unidadcompra AS embalaje,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad,
               CAST(NULLIF(pendientedestino::text,'NULL') AS NUMERIC) AS pendiente
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE transacconsubtiponombre = 'Pedido de Campo'
          AND producto LIKE 'SEM. SOJA DM%'
          AND fechacomprobante >= '{FECHA_MIN_PED}'""",
    # Col T: despachos a producción (traslados internos)
    "t": f"""
        SELECT producto, unidadventa AS embalaje, depositodestino,
               CAST(NULLIF(cantidad::text,'NULL') AS NUMERIC) AS cantidad
        FROM agronasajasrl_analisis_de_pendientes_ventas_semillas
        WHERE transacconsubtiponombre = 'Traslado interno - Salida'
          AND producto LIKE 'SEM. SOJA DM%'
          AND partida LIKE '{PARTIDA_LIKE}'
          AND (depositodestino LIKE 'DEPOSITO PRODUCCION%'
               OR depositodestino LIKE 'DEPÓSITO PRODUCCIÓN%')""",
}


# ─── Fetch principal ─────────────────────────────────────────────────────────
def fetch(verbose: bool = True):
    """Corre las queries DEM-SUP Soja contra el DW y devuelve el payload.

    Devuelve None si faltan credenciales FNN_DW_* o psycopg2. Lanza excepción
    si el DW falla (el caller decide el fallback).
    """
    if not all(os.environ.get(k) for k in ("FNN_DW_HOST", "FNN_DW_USER", "FNN_DW_PASS")):
        if verbose:
            print("    [!] FNN_DW_HOST/USER/PASS no seteados — DEM-SUP Soja omitido")
        return None
    try:
        import psycopg2, psycopg2.extras
    except ImportError:
        if verbose:
            print("    [!] psycopg2 no instalado — pip install psycopg2-binary")
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

        # ── C + D (granel en campo / en semillero) + detalle por depósito ──
        c_bls, d_bls = zeros(), zeros()
        det_campo, det_semillero = {v: [] for v in VARIEDADES}, {v: [] for v in VARIEDADES}
        campo_tn_prod, semillero_tn_prod = {}, {}   # tn CON MERMA por producto exacto
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

        # ── E/F (compras granel: 1 tn = 25 bolsas brutas) ──
        e_bls, f_bls = zeros(), zeros()
        for r in q("ef"):
            v = extract_var(r["producto"])
            if not v:
                continue
            e_bls[v] += sf(r["tn_entregada"]) * 25   # remitidas
            f_bls[v] += sf(r["tn_pendiente"]) * 25   # pendientes

        # ── K (stock clasificado: semilla terminada en DEPOSITO VENTAS) ──
        k_bls = zeros()
        clasif_tn_prod = {}                      # {producto exacto: tn}
        det_clasif = {v: [] for v in VARIEDADES}
        for r in q("k"):
            v = extract_var(r["producto"])
            if not v:
                continue
            p = (r["producto"] or "").upper()
            qty = sf(r["qty"])
            if "800" in p:
                bls = qty * 20                 # bolsón 800 kg = 20 bolsas
            elif "40KG" in p or "40 KG" in p:
                bls = qty                      # ya en bolsas 40 kg
            elif "GRANEL" in p:
                bls = qty / 40                 # kilos
            else:
                bls = qty * 20                 # default: bolsón
            k_bls[v] += bls
            prod = r["producto"] or ""
            tn = round(bls * 40 / 1000.0, 4)
            clasif_tn_prod[prod] = round(clasif_tn_prod.get(prod, 0.0) + tn, 4)
            det_clasif[v].append({"variedad": v, "producto": prod,
                                  "cantidad": qty, "bls": round(bls), "tn": tn})

        def _acum_prod(dic, prod, bls):
            tn = round(bls * 40 / 1000.0, 4)
            dic[prod] = round(dic.get(prod, 0.0) + tn, 4)

        # ── L (corte de bolsa) ──
        l_bls = zeros()
        corte_tn_prod = {}
        for r in q("l"):
            v = extract_var(r["producto"])
            if v:
                bls = to_bls(r["cantidad"], r["embalaje"])
                l_bls[v] += bls
                _acum_prod(corte_tn_prod, r["producto"] or "", bls)

        # ── O (venta pendiente) ──
        o_bls = zeros()
        venta_pend_tn_prod = {}
        for r in q("o"):
            v = extract_var(r["producto"])
            if v:
                bls = to_bls(r["pendiente"], r["embalaje"])
                o_bls[v] += bls
                _acum_prod(venta_pend_tn_prod, r["producto"] or "", bls)

        # ── P (despachado + devoluciones) ──
        p_bls = zeros()
        venta_desp_tn_prod = {}
        for key in ("p_rem", "p_dev"):
            for r in q(key):
                v = extract_var(r["producto"])
                if v:
                    bls = to_bls(r["cantidad"], r["embalaje"])
                    p_bls[v] += bls
                    _acum_prod(venta_desp_tn_prod, r["producto"] or "", bls)

        # ── S (pedidos de campo pendientes) ──
        s_bls = zeros()
        prod_pend_tn_prod = {}
        for r in q("u"):
            v = extract_var(r["producto"])
            if v:
                bls = to_bls(r["pendiente"], r["embalaje"])
                s_bls[v] += bls
                _acum_prod(prod_pend_tn_prod, r["producto"] or "", bls)

        # ── T (despachos a producción) ──
        t_bls = zeros()
        prod_desp_tn_prod = {}
        for r in q("t"):
            v = extract_var(r["producto"])
            if v:
                bls = to_bls(r["cantidad"], r["embalaje"])
                t_bls[v] += bls
                _acum_prod(prod_desp_tn_prod, r["producto"] or "", bls)
    finally:
        cn.close()

    # ── COPIA EXACTA DE LA VISTA DEL EXTRANET ──
    # Se consulta la API en vivo de la vista; si responde, sus valores POR VARIEDAD
    # pisan los calculados acá (incluyen los ajustes del analista). Después se
    # concilian los desgloses por producto: si a una variedad le falta diferencia
    # contra la vista, el ajuste se cuelga del producto "ancla" de esa variedad
    # (su granel), para que los totales del tablero cierren idénticos a la vista.
    fuente = "queries propias al DW"
    try:
        req = urllib.request.Request(VISTA_API_URL, headers={"User-Agent": "tablero-granos", "Origin": VISTA_API_ORIGIN})
        api = json.loads(urllib.request.urlopen(req, timeout=60).read())
        api_rows = api.get("rows") if isinstance(api.get("rows"), dict) else None
    except Exception as e:
        api_rows = None
        print(f"    [!] API de la vista DEM-SUP no disponible ({type(e).__name__}); se usan las queries propias")
    if api_rows:
        fuente = "API en vivo de la vista del extranet"
        anchor = {}   # variedad -> producto ancla (el granel de esa variedad)
        for prod in list(campo_tn_prod) + list(semillero_tn_prod):
            v2 = extract_var(prod)
            if v2 and v2 not in anchor:
                anchor[v2] = prod
        gv = lambda v2, col: sf((api_rows.get(v2) or {}).get(col))
        for v2 in VARIEDADES:
            c_bls[v2] = gv(v2, "C"); d_bls[v2] = gv(v2, "D")
            f_bls[v2] = gv(v2, "F"); e_bls[v2] = max(0.0, gv(v2, "E") - gv(v2, "F"))  # E de la vista = total contratado
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
                    prod = anchor.get(v2) or f"SEM. SOJA DM {v2} (vista extranet)"
                    dic[prod] = round(dic.get(prod, 0.0) + delta, 4)
        conciliar(campo_tn_prod, c_bls)
        conciliar(semillero_tn_prod, d_bls)
        conciliar(clasif_tn_prod, k_bls)
        conciliar(corte_tn_prod, l_bls)
        conciliar(venta_pend_tn_prod, o_bls)
        conciliar(venta_desp_tn_prod, p_bls)
        conciliar(prod_pend_tn_prod, s_bls)
        conciliar(prod_desp_tn_prod, t_bls)

    # ── Filas por variedad + fila de toneladas totales ──
    rows, tot = {}, {}
    keys = ("C", "D", "E", "F", "G", "K", "L", "M", "O", "P", "Q", "S", "T")
    for v in VARIEDADES:
        C, D = round(c_bls[v]), round(d_bls[v])
        E, F = round(e_bls[v]), round(f_bls[v])
        K, L = round(k_bls[v]), round(l_bls[v])
        O, P = round(o_bls[v]), round(p_bls[v])
        S, T = round(s_bls[v]), round(t_bls[v])
        rows[v] = {"C": C, "D": D, "E": E, "F": F, "G": E + F,
                   "K": K, "L": L, "M": C + D + F + K,
                   "O": O, "P": P, "Q": O + P, "S": S, "T": T}
        for k2 in keys:
            tot[k2] = tot.get(k2, 0) + rows[v][k2]
    tot_tn = {k2: round(n * 40 / 1000.0, 3) for k2, n in tot.items()}

    now = datetime.now(timezone.utc)
    return {
        "fuente": f"DEM-SUP Soja · Extranet Agronasaja ({fuente})",
        "campana": CAMPANA,
        "generated_at": now.isoformat(),
        "variedades": list(VARIEDADES),
        "prod_prefix": PROD_PREFIX,          # granel (cols C/D)
        "prod_prefix_sem": PROD_PREFIX_SEM,  # semilla terminada (cols K/L/O/S/T)
        "rows": rows,               # bolsas 40 kg por variedad (letras de la vista)
        "tot_bls": tot,             # fila TOTAL en bolsas
        "tot_tn": tot_tn,           # fila TONELADAS TOTALES de la vista
        "campo_tn_prod": campo_tn_prod,          # {producto: tn con merma} — silobolsa (col C)
        "semillero_tn_prod": semillero_tn_prod,  # {producto: tn con merma} — silos planta (col D)
        "clasif_tn_prod": clasif_tn_prod,        # {producto: tn} — stock clasificado (col K)
        "corte_tn_prod": corte_tn_prod,          # {producto: tn} — corte de bolsa (col L)
        "venta_pend_tn_prod": venta_pend_tn_prod,  # {producto: tn} — venta pendiente (col O)
        "venta_desp_tn_prod": venta_desp_tn_prod,  # {producto: tn} — venta despachada (col P)
        "prod_pend_tn_prod": prod_pend_tn_prod,    # {producto: tn} — prod pendiente (col S)
        "prod_desp_tn_prod": prod_desp_tn_prod,    # {producto: tn} — prod despachado (col T)
        "detalle_campo": det_campo,              # drill-down col C (por variedad)
        "detalle_semillero": det_semillero,      # drill-down col D (por variedad)
        "detalle_clasificado": det_clasif,       # drill-down col K (por variedad)
    }


if __name__ == "__main__":
    import json
    data = fetch()
    if data:
        print(f"\nDEM-SUP Soja · {data['campana']}")
        print(f"{'VAR':<10}{'C':>8}{'D':>8}{'E':>8}{'F':>8}{'G':>9}{'K':>8}{'O':>8}{'P':>8}")
        for v in data["variedades"]:
            r = data["rows"][v]
            print(f"{v:<10}{r['C']:>8}{r['D']:>8}{r['E']:>8}{r['F']:>8}{r['G']:>9}{r['K']:>8}{r['O']:>8}{r['P']:>8}")
        t = data["tot_bls"]
        print(f"{'TOT BLS':<10}{t['C']:>8}{t['D']:>8}{t['E']:>8}{t['F']:>8}{t['G']:>9}{t['K']:>8}{t['O']:>8}{t['P']:>8}")
        tt = data["tot_tn"]
        print(f"{'TOT TN':<10}{tt['C']:>8}{tt['D']:>8}{tt['E']:>8}{tt['F']:>8}{tt['G']:>9}{tt['K']:>8}{tt['O']:>8}{tt['P']:>8}")
        print(f"\nSilobolsa por producto (tn con merma):")
        for p, tn in sorted(data["campo_tn_prod"].items()):
            print(f"  {tn:>10,.2f} tn  {p}")
    else:
        print("Sin datos (credenciales o psycopg2 faltantes)")
