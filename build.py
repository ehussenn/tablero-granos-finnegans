"""Build del tablero de granos (Agronasaja).

Lee credenciales desde variables de entorno (con fallback a defaults locales),
se conecta al datawarehouse finnegansbi, baja los datasets que necesita,
los normaliza (parsea numeros y fechas que estan como text) y genera un
unico index.html con todos los datos embebidos como JSON.

Pensado para correr local Y desde GitHub Actions.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# cliente API de Finnegans
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import finnegans_api as api
import bcr_pizarra

# ---------- datasets a bajar via API ----------
# (label_UI, endpoint_api, params_default, "tab_destino")
RANGO_DEFAULT = {
    "PARAMWEBREPORT_FechaDesde":      "2022-01-01",
    "PARAMWEBREPORT_FechaHasta":      "2030-12-31",
    "PARAMWEBREPORT_FechaEntregaMin": "2022-01-01",
    "PARAMWEBREPORT_FechaEntregaMax": "2030-12-31",
}
DATASETS: list[tuple[str, str, dict, str]] = [
    ("Resumen Contrato Compra Granos",          "/reports/ResumenContratoCompraGranos",      dict(RANGO_DEFAULT),                                                                "compra"),
    ("Resumen Contratos Venta Granos",          "/reports/resumenContratosVentaGranos",      dict(RANGO_DEFAULT),                                                                "venta"),
    ("Reporte Stock por Deposito",              "/reports/USR_RESSTOCKDEP",                  {"PARAMWEBREPORT_fecha":"getCurrentDate","PARAMWEBREPORT_MonedaID":"PESOS"},        "posicion"),
    ("Composicion Saldos (Email c/Vendedor)",   "/reports/USR_ComposicionSaldosResumenParaEmail_API", {"PARAMWEBREPORT_FechaCorte":"getCurrentDate"},                            "posicion"),
]

# Datasets que se normalizan completos y van al payload del HTML
PILOT_ENDPOINT  = "/reports/resumenContratosVentaGranos"
COMPRA_ENDPOINT = "/reports/ResumenContratoCompraGranos"

OUTPUT = Path(__file__).resolve().parent / "index.html"


# ---------- helpers ----------
def to_iso_date(v: Any) -> str | None:
    """Convierte distintas formas a YYYY-MM-DD.
       API Finnegans devuelve 'dd-MM-yyyy'. Tambien soporta 'yyyy-mm-dd hh:mm:ss.x'."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # dd-MM-yyyy (API)
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo}-{d}"
    # yyyy-mm-dd[...]
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


def lowercase_keys(rows: list[dict]) -> list[dict]:
    return [{(k.lower() if isinstance(k, str) else k): v for k, v in r.items()} for r in rows]


# ---------- normalizacion del piloto ----------
# Columnas que en la API vienen como string vacio para "—"
DATE_COLS_PILOT = {"fecha", "fechaminentrega", "fechamaxentrega"}


def normalize_pilot(rows: list[dict]) -> list[dict]:
    """Toma rows de la API (keys UPPERCASE, valores ya tipados, fechas dd-MM-yyyy)
       y devuelve rows con keys lowercase, fechas yyyy-mm-dd y empty strings -> None."""
    out = []
    for r in rows:
        nr: dict = {}
        for k, v in r.items():
            kl = k.lower() if isinstance(k, str) else k
            if kl in DATE_COLS_PILOT:
                nr[kl] = to_iso_date(v)
            elif isinstance(v, str) and v == "":
                nr[kl] = None
            else:
                nr[kl] = v
        # alias: en la API solo viene 'COSECHA', el HTML/JS espera tambien 'campana'
        if "campana" not in nr and "cosecha" in nr:
            nr["campana"] = nr["cosecha"]
        out.append(nr)
    return out


# ---------- HTML ----------
HTML_TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Tablero Granos — Agronasaja</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{
    --bg:#f4f6fa; --card:#ffffff; --ink:#1a2233; --muted:#6c7a8c;
    --blue:#1e3a8a; --blue2:#3b82f6; --green:#16a34a; --red:#dc2626;
    --orange:#f59e0b; --line:#e5e9f2; --chip:#eef2ff;
    --row-alt:#f8fafd;
  }
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:var(--ink);background:var(--bg)}
  .wrap{max-width:1500px;margin:0 auto;padding:18px}

  /* header */
  .hero{background:linear-gradient(135deg,#1e3a8a 0%,#3b82f6 100%);color:#fff;border-radius:14px;padding:22px 28px;display:flex;justify-content:space-between;align-items:flex-start;box-shadow:0 4px 20px rgba(30,58,138,.18)}
  .hero h1{margin:0;font-size:22px;font-weight:600;letter-spacing:.2px}
  .hero .sub{margin-top:4px;opacity:.85;font-size:13px}
  .hero .meta{font-size:12px;text-align:right;opacity:.9;line-height:1.6}
  .hero .meta .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:6px;vertical-align:middle;box-shadow:0 0 0 3px rgba(34,197,94,.25)}

  /* tabs */
  .tabs{display:flex;gap:6px;margin:18px 0 14px}
  .tab{padding:10px 22px;border:1px solid var(--line);background:#fff;border-radius:10px;cursor:pointer;font-weight:500;color:var(--muted);transition:all .15s}
  .tab:hover{color:var(--ink);border-color:#c7d2e2}
  .tab.active{background:var(--blue);color:#fff;border-color:var(--blue);box-shadow:0 2px 8px rgba(30,58,138,.25)}
  .tab .count{display:inline-block;margin-left:8px;padding:1px 8px;border-radius:10px;background:rgba(255,255,255,.18);font-size:11px;font-weight:600}
  .tab:not(.active) .count{background:#eef2ff;color:var(--blue)}

  /* sub-tabs (dentro de cada panel) */
  .subtabs{display:flex;gap:0;border-bottom:2px solid var(--line);margin:6px 0 16px}
  .subtab{padding:9px 18px;border:none;background:transparent;cursor:pointer;font-weight:500;color:var(--muted);font-size:13px;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s;letter-spacing:.2px}
  .subtab:hover{color:var(--blue)}
  .subtab.active{color:var(--blue);border-bottom-color:var(--blue);font-weight:600}

  /* tab panels */
  .panel{display:none}
  .panel.active{display:block}
  .subpanel{display:none}
  .subpanel.active{display:block}

  /* kpi cards */
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:14px 0}
  .kpi{background:#fff;border-radius:12px;padding:18px 20px;border-top:3px solid var(--blue2);box-shadow:0 1px 3px rgba(0,0,0,.04)}
  .kpi.green{border-top-color:var(--green)}
  .kpi.red{border-top-color:var(--red)}
  .kpi.orange{border-top-color:var(--orange)}
  .kpi .lbl{color:var(--muted);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.6px}
  .kpi .val{font-size:28px;font-weight:700;margin-top:4px;color:var(--ink)}
  .kpi.green .val{color:var(--green)}
  .kpi.red .val{color:var(--red)}
  .kpi.orange .val{color:var(--orange)}
  .kpi .hint{color:var(--muted);font-size:12px;margin-top:6px}

  /* filtros */
  .filterbar{background:#fff;border-radius:12px;padding:14px 16px;display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;margin-bottom:14px;border:1px solid var(--line)}
  .filterbar label{font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600;letter-spacing:.5px}
  .filterbar select, .filterbar input[type=text]{display:block;margin-top:4px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;background:#fff;font-size:13px;min-width:170px;font-family:inherit}
  .filterbar .clear{padding:8px 14px;border:1px solid var(--line);background:#fff;border-radius:6px;cursor:pointer;font-size:13px;color:var(--ink)}
  .filterbar .clear:hover{border-color:var(--blue);color:var(--blue)}
  .filterbar .count{margin-left:auto;font-size:12px;color:var(--muted);align-self:center}

  /* secciones */
  .section{background:#fff;border-radius:12px;padding:18px;margin-bottom:16px;border:1px solid var(--line)}
  .section h3{margin:0 0 12px;font-size:15px;font-weight:600;display:flex;justify-content:space-between;align-items:center}
  .section h3 .badge{font-size:11px;font-weight:500;color:var(--muted)}

  /* cards por grano */
  .grain-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
  .grain-card{padding:14px;border-radius:10px;border-left:4px solid var(--blue2);background:#fafbff}
  .grain-card.soja{border-left-color:#16a34a;background:#f0fdf4}
  .grain-card.maiz{border-left-color:#f59e0b;background:#fffbeb}
  .grain-card.trigo{border-left-color:#a16207;background:#fefce8}
  .grain-card.girasol{border-left-color:#d97706;background:#fff7ed}
  .grain-card .name{font-weight:600;font-size:13px;display:flex;justify-content:space-between;align-items:center}
  .grain-card .name .cnt{font-size:11px;color:var(--muted);font-weight:500}
  .grain-card .row{display:flex;justify-content:space-between;margin-top:6px;font-size:13px}
  .grain-card .row .k{color:var(--muted)}
  .grain-card .bar{height:6px;background:#e5e9f2;border-radius:4px;overflow:hidden;margin-top:8px}
  .grain-card .bar > div{height:100%;background:linear-gradient(90deg,#16a34a,#22c55e)}

  /* tabla */
  .tbl-wrap{overflow:auto;max-height:620px;border:1px solid var(--line);border-radius:8px}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  thead th{background:var(--blue);color:#fff;text-align:left;padding:9px 10px;position:sticky;top:0;cursor:pointer;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.3px;white-space:nowrap;user-select:none}
  thead th:hover{background:#172e6b}
  thead th .arrow{opacity:.5;margin-left:4px;font-size:10px}
  thead th.sort-asc .arrow, thead th.sort-desc .arrow{opacity:1}
  tbody td{padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
  tbody tr:nth-child(even){background:var(--row-alt)}
  tbody tr:hover{background:#eff5ff}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  td.muted{color:var(--muted)}

  /* badges/chips */
  .chip{display:inline-block;padding:2px 9px;border-radius:11px;font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.3px}
  .chip.ok{background:#dcfce7;color:#15803d}
  .chip.warn{background:#fef3c7;color:#a16207}
  .chip.err{background:#fee2e2;color:#b91c1c}
  .chip.info{background:#dbeafe;color:#1e40af}
  .chip.neutral{background:#f1f5f9;color:#475569}

  /* chart container */
  .chart-wrap{position:relative;height:280px}

  /* options con 0 hits — estilo gris */
  select option.opt-zero{color:#9aa5b3}

  /* calendario de cobranzas */
  #cal-tbl{font-size:12px}
  #cal-tbl thead th{cursor:default;font-size:10.5px;padding:6px 8px}
  #cal-tbl thead th.cal-month{background:#1e3a8a;cursor:pointer}
  #cal-tbl thead th.cal-month:hover{background:#172e6b}
  #cal-tbl thead th.cal-day{background:#3b82f6;font-weight:500;padding:4px 6px;min-width:40px}
  #cal-tbl thead th.cal-day .dn{font-size:9px;opacity:.75;display:block}
  #cal-tbl thead th.cal-org{background:#1e3a8a;text-align:left;min-width:260px;position:sticky;left:0;z-index:2}
  #cal-tbl tbody td.cal-org-cell{text-align:left;font-weight:500;background:#fff;position:sticky;left:0;z-index:1;border-right:2px solid var(--line)}
  #cal-tbl tbody tr.cal-contrato td.cal-org-cell{padding-left:24px;font-weight:400;color:var(--muted);font-size:11px}
  #cal-tbl tbody tr.cal-org > td.cal-org-cell{cursor:pointer}
  #cal-tbl tbody tr.cal-org > td.cal-org-cell::before{content:'▶ ';font-size:9px;color:var(--blue)}
  #cal-tbl tbody tr.cal-org.expanded > td.cal-org-cell::before{content:'▼ '}
  #cal-tbl tbody td.cal-num{text-align:right;font-variant-numeric:tabular-nums;padding:4px 6px}
  #cal-tbl tbody td.cal-num input{width:100%;border:1px solid transparent;background:transparent;padding:3px 5px;text-align:right;font-size:11px;font-variant-numeric:tabular-nums;border-radius:3px;font-family:inherit;color:var(--ink)}
  #cal-tbl tbody td.cal-num input:hover{border-color:var(--line)}
  #cal-tbl tbody td.cal-num input:focus{border-color:var(--blue);outline:none;background:#fff;box-shadow:0 0 0 2px rgba(59,130,246,.15)}
  #cal-tbl tbody td.cal-num input[data-has-value="1"]{font-weight:600;color:var(--green);background:#f0fdf4}
  #cal-tbl tbody tr.cal-contrato td.cal-num{background:#fafbff}
  #cal-tbl tbody tr.cal-contrato td.cal-num input{font-size:10px}
  #cal-tbl tfoot td{background:#eef2ff;font-weight:700;padding:6px 8px;font-size:11.5px}
  #cal-tbl tfoot td.cal-num{text-align:right}
  #cal-tbl tfoot td.cal-org-cell{position:sticky;left:0;background:#dbeafe;text-align:left}

  /* calendario de pagos (Compra) — mismo estilo */
  #cal-cp-tbl{font-size:12px}
  #cal-cp-tbl thead th{cursor:default;font-size:10.5px;padding:6px 8px}
  #cal-cp-tbl thead th.cal-month{background:#1e3a8a;cursor:pointer}
  #cal-cp-tbl thead th.cal-month:hover{background:#172e6b}
  #cal-cp-tbl thead th.cal-day{background:#3b82f6;font-weight:500;padding:4px 6px;min-width:40px}
  #cal-cp-tbl thead th.cal-day .dn{font-size:9px;opacity:.75;display:block}
  #cal-cp-tbl thead th.cal-org{background:#1e3a8a;text-align:left;min-width:260px;position:sticky;left:0;z-index:2}
  #cal-cp-tbl tbody td.cal-org-cell{text-align:left;font-weight:500;background:#fff;position:sticky;left:0;z-index:1;border-right:2px solid var(--line)}
  #cal-cp-tbl tbody tr.cal-contrato td.cal-org-cell{padding-left:24px;font-weight:400;color:var(--muted);font-size:11px}
  #cal-cp-tbl tbody tr.cal-org > td.cal-org-cell{cursor:pointer}
  #cal-cp-tbl tbody tr.cal-org > td.cal-org-cell::before{content:'▶ ';font-size:9px;color:var(--blue)}
  #cal-cp-tbl tbody tr.cal-org.expanded > td.cal-org-cell::before{content:'▼ '}
  #cal-cp-tbl tbody td.cal-num{text-align:right;font-variant-numeric:tabular-nums;padding:4px 6px}
  #cal-cp-tbl tbody td.cal-num input{width:100%;border:1px solid transparent;background:transparent;padding:3px 5px;text-align:right;font-size:11px;font-variant-numeric:tabular-nums;border-radius:3px;font-family:inherit;color:var(--ink)}
  #cal-cp-tbl tbody td.cal-num input:hover{border-color:var(--line)}
  #cal-cp-tbl tbody td.cal-num input:focus{border-color:var(--blue);outline:none;background:#fff;box-shadow:0 0 0 2px rgba(59,130,246,.15)}
  #cal-cp-tbl tbody td.cal-num input[data-has-value="1"]{font-weight:600;color:var(--red);background:#fef2f2}
  #cal-cp-tbl tbody tr.cal-contrato td.cal-num{background:#fafbff}
  #cal-cp-tbl tbody tr.cal-contrato td.cal-num input{font-size:10px}
  #cal-cp-tbl tfoot td{background:#fee2e2;font-weight:700;padding:6px 8px;font-size:11.5px}
  #cal-cp-tbl tfoot td.cal-num{text-align:right}
  #cal-cp-tbl tfoot td.cal-org-cell{position:sticky;left:0;background:#fecaca;text-align:left}

  /* tabla con fila de totales sticky al pie */
  table tfoot td{background:#eef2ff;font-weight:700;padding:8px 10px;font-size:12.5px;border-top:2px solid var(--blue);position:sticky;bottom:0;z-index:1}
  table tfoot tr.sel td{background:#fef3c7;border-top:2px solid var(--orange);bottom:33px}
  table tfoot td.num{text-align:right;font-variant-numeric:tabular-nums;color:var(--blue)}
  table tfoot tr.sel td.num{color:#a16207}
  table tfoot td.lbl{text-align:left;color:var(--blue);text-transform:uppercase;letter-spacing:.3px;font-size:11px}
  table tfoot tr.sel td.lbl{color:#a16207}
  table tfoot td.lbl .clear-sel{margin-left:8px;font-size:10px;background:#fff;color:#a16207;border:1px solid #fde68a;padding:1px 8px;border-radius:6px;cursor:pointer;text-transform:none;letter-spacing:0;font-weight:500}
  table tfoot td.lbl .clear-sel:hover{background:#fde68a}

  /* filas seleccionadas (Excel-style) */
  tbody tr.row-sel{background:#fef3c7 !important}
  tbody tr.row-sel:hover{background:#fde68a !important}
  tbody tr{cursor:pointer;user-select:none}

  /* placeholder de tabs vacios */
  .placeholder{padding:60px 20px;text-align:center;color:var(--muted);border:2px dashed var(--line);border-radius:12px;background:#fafbff}
  .placeholder .ico{font-size:42px;margin-bottom:8px}
  .placeholder h4{margin:6px 0 4px;color:var(--ink);font-size:16px}

  /* layout 2-col */
  .row2{display:grid;grid-template-columns:2fr 1fr;gap:16px}
  @media (max-width: 1100px){ .row2{grid-template-columns:1fr} }
</style>
</head>
<body>
<div class="wrap">

  <div class="hero">
    <div>
      <h1>Tablero Granos · Agronasaja</h1>
      <div class="sub">Datawarehouse finnegansbi · Resumen comercial de compra, venta y posición</div>
    </div>
    <div class="meta">
      <div><span class="dot"></span>Última actualización: __BUILD_TIME__</div>
      <div>Fuente: API Finnegans (api.finneg.com) en vivo</div>
    </div>
  </div>

  <div class="tabs">
    <div class="tab" data-tab="compra">COMPRA <span class="count" id="cnt-compra">0</span></div>
    <div class="tab active" data-tab="venta">VENTA <span class="count" id="cnt-venta">0</span></div>
    <div class="tab" data-tab="posicion">POSICIÓN GENERAL <span class="count" id="cnt-pos">0</span></div>
  </div>

  <!-- ============ COMPRA ============ -->
  <div class="panel" data-panel="compra">

    <!-- SUB-TABS dentro de COMPRA -->
    <div class="subtabs">
      <button class="subtab active" data-sub="cp-posicion">Posición General</button>
      <button class="subtab" data-sub="cp-financiera">Financiera</button>
      <button class="subtab" data-sub="cp-canjes">Canjes</button>
    </div>

    <!-- ========== SUB: POSICION COMPRA ========== -->
    <div class="subpanel active" data-sub-panel="cp-posicion">

      <div class="kpis" id="kpi-row-cp"></div>

      <div class="filterbar" id="filterbar-cp">
        <div><label>EMPRESA</label><select id="cp-empresa"><option value="">Todas</option></select></div>
        <div><label>PROVEEDOR</label><select id="cp-org"><option value="">Todos</option></select></div>
        <div><label>GRANO/PRODUCTO</label><select id="cp-prod"><option value="">Todos</option></select></div>
        <div><label>TIPO CONTRATO</label><select id="cp-tcont"><option value="">Todos</option></select></div>
        <div><label>CAMPAÑA</label><select id="cp-camp"><option value="">Todas</option></select></div>
        <div><label>BUSCAR</label><input type="text" id="cp-q" placeholder="numero, descripción…" /></div>
        <button class="clear" id="btn-clear-cp">Limpiar</button>
        <div class="count" id="row-count-cp">0 / 0 contratos</div>
      </div>

      <div class="section">
        <h3>Resumen por Producto <span class="badge" id="grain-meta-cp"></span></h3>
        <div class="grain-grid" id="grain-grid-cp"></div>
      </div>

      <div class="row2">
        <div class="section">
          <h3>Top 10 Proveedores por Toneladas Ajustadas</h3>
          <div class="chart-wrap"><canvas id="chart-top-cp"></canvas></div>
        </div>
        <div class="section">
          <h3>Distribución por Producto (Tn Ajustadas)</h3>
          <div class="chart-wrap"><canvas id="chart-donut-cp"></canvas></div>
        </div>
      </div>

      <div class="section">
        <h3>Detalle de Contratos — Posición Física (Compra) <span class="badge">Click en encabezado para ordenar · click en filas para seleccionar</span></h3>
        <div class="tbl-wrap">
          <table id="tbl-cp">
            <thead><tr id="tbl-head-cp"></tr></thead>
            <tbody id="tbl-body-cp"></tbody>
            <tfoot id="tbl-foot-cp"></tfoot>
          </table>
        </div>
      </div>
    </div>

    <!-- ========== SUB: FINANCIERA COMPRA ========== -->
    <div class="subpanel" data-sub-panel="cp-financiera">

      <div class="kpis" id="kpi-row-cpfin"></div>

      <div class="filterbar" id="filterbar-cpfin">
        <div><label>EMPRESA</label><select id="cpf-empresa"><option value="">Todas</option></select></div>
        <div><label>PROVEEDOR</label><select id="cpf-org"><option value="">Todos</option></select></div>
        <div><label>GRANO/PRODUCTO</label><select id="cpf-prod"><option value="">Todos</option></select></div>
        <div><label>TIPO CONTRATO</label><select id="cpf-tcont"><option value="">Todos</option></select></div>
        <div><label>MONEDA</label><select id="cpf-moneda"><option value="">Todas</option></select></div>
        <div><label>CAMPAÑA</label><select id="cpf-camp"><option value="">Todas</option></select></div>
        <div><label>BUSCAR</label><input type="text" id="cpf-q" placeholder="numero, descripción…" /></div>
        <button class="clear" id="btn-clear-cpfin">Limpiar</button>
        <div class="count" id="row-count-cpfin">0 / 0 contratos</div>
      </div>

      <div class="section">
        <h3>Resumen Financiero por Producto <span class="badge" id="grain-meta-cpfin"></span></h3>
        <div class="grain-grid" id="grain-grid-cpfin"></div>
      </div>

      <div class="row2">
        <div class="section">
          <h3>Top 10 Proveedores — Importe Pendiente de Liquidar</h3>
          <div class="chart-wrap"><canvas id="chart-top-cpfin"></canvas></div>
        </div>
        <div class="section">
          <h3>Importes por Moneda</h3>
          <div class="chart-wrap"><canvas id="chart-mon-cpfin"></canvas></div>
        </div>
      </div>

      <div class="section">
        <h3>Detalle Financiero (Compra) <span class="badge">Click en encabezado para ordenar · click en filas para seleccionar</span></h3>
        <div class="tbl-wrap">
          <table id="tbl-cpfin">
            <thead><tr id="tbl-head-cpfin"></tr></thead>
            <tbody id="tbl-body-cpfin"></tbody>
            <tfoot id="tbl-foot-cpfin"></tfoot>
          </table>
        </div>
      </div>

      <!-- CALENDARIO DE PAGOS -->
      <div class="section">
        <h3>📅 Calendario de Pagos (manual) <span class="badge" id="cal-meta-cp">cargá importes esperados a pagar por contrato y fecha</span></h3>
        <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
          <label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">Año
            <select id="cal-cp-year" style="margin-left:6px;padding:6px 9px;border:1px solid var(--line);border-radius:6px"></select>
          </label>
          <label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">Moneda
            <select id="cal-cp-moneda" style="margin-left:6px;padding:6px 9px;border:1px solid var(--line);border-radius:6px"></select>
          </label>
          <button class="clear" id="cal-cp-export">⬇️ Exportar JSON</button>
          <button class="clear" id="cal-cp-import">⬆️ Importar JSON</button>
          <input type="file" id="cal-cp-import-file" accept="application/json" style="display:none" />
          <button class="clear" id="cal-cp-clear" style="color:var(--red);border-color:#fecaca">🗑️ Borrar todo</button>
          <span style="margin-left:auto;font-size:12px;color:var(--muted)" id="cal-cp-storage-info"></span>
        </div>
        <div class="tbl-wrap" style="max-height:600px">
          <table id="cal-cp-tbl">
            <thead><tr id="cal-cp-head"></tr></thead>
            <tbody id="cal-cp-body"></tbody>
            <tfoot><tr id="cal-cp-foot"></tr></tfoot>
          </table>
        </div>
      </div>

    </div>

    <!-- ========== SUB: CANJES ========== -->
    <div class="subpanel" data-sub-panel="cp-canjes">

      <!-- Precios pizarra BCR (editables, default desde scraper) -->
      <div class="section" style="background:linear-gradient(135deg,#fff7ed,#fef3c7);border:1px solid #fde68a">
        <h3>📊 Precios Pizarra BCR <span class="badge" id="cj-bcr-meta"></span></h3>
        <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end">
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">SOJA USD/Tn</label><input type="text" id="cj-px-soja" style="display:block;margin-top:4px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;width:120px;text-align:right;font-variant-numeric:tabular-nums"></div>
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">MAÍZ USD/Tn</label><input type="text" id="cj-px-maiz" style="display:block;margin-top:4px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;width:120px;text-align:right;font-variant-numeric:tabular-nums"></div>
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">TRIGO USD/Tn</label><input type="text" id="cj-px-trigo" style="display:block;margin-top:4px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;width:120px;text-align:right;font-variant-numeric:tabular-nums"></div>
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">GIRASOL USD/Tn</label><input type="text" id="cj-px-girasol" style="display:block;margin-top:4px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;width:120px;text-align:right;font-variant-numeric:tabular-nums"></div>
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">SORGO USD/Tn</label><input type="text" id="cj-px-sorgo" style="display:block;margin-top:4px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;width:120px;text-align:right;font-variant-numeric:tabular-nums"></div>
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">TC ARS/USD</label><input type="text" id="cj-tc" style="display:block;margin-top:4px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;width:120px;text-align:right;font-variant-numeric:tabular-nums"></div>
          <button class="clear" id="cj-reset-px">↺ Reset BCR</button>
        </div>
      </div>

      <!-- KPIs Canjes -->
      <div class="kpis" id="kpi-row-canjes"></div>

      <!-- Filtros -->
      <div class="filterbar">
        <div><label>CONDICIÓN PAGO</label><select id="cj-cond" multiple size="1" style="min-width:200px"></select></div>
        <div><label>VENDEDOR</label><select id="cj-vend"><option value="">Todos</option></select></div>
        <div><label>GRANO PREFERIDO</label><select id="cj-grano"><option value="auto">Auto-detectar</option><option value="soja">Soja</option><option value="maiz">Maíz</option><option value="trigo">Trigo</option><option value="girasol">Girasol</option><option value="sorgo">Sorgo</option></select></div>
        <div><label>ESTADO</label><select id="cj-estado"><option value="">Todos</option><option value="OK">Contrato OK</option><option value="PARCIAL">Con contrato parcial</option><option value="SIN">Sin contrato</option></select></div>
        <div><label>BUSCAR CLIENTE</label><input type="text" id="cj-q" placeholder="razón social…" style="min-width:240px" /></div>
        <button class="clear" id="cj-clear">Limpiar</button>
        <div class="count" id="cj-count">0 / 0 clientes</div>
      </div>

      <!-- Resumen por Vendedor -->
      <div class="section">
        <h3>Resumen por Vendedor <span class="badge" id="cj-vend-meta"></span></h3>
        <div class="tbl-wrap" style="max-height:400px">
          <table id="tbl-vend-canjes">
            <thead><tr id="tbl-vend-head"></tr></thead>
            <tbody id="tbl-vend-body"></tbody>
            <tfoot id="tbl-vend-foot"></tfoot>
          </table>
        </div>
      </div>

      <!-- Detalle por Cliente -->
      <div class="section">
        <h3>Detalle por Cliente <span class="badge">click en encabezado para ordenar · click en filas para seleccionar</span></h3>
        <div class="tbl-wrap">
          <table id="tbl-canjes">
            <thead><tr id="tbl-head-canjes"></tr></thead>
            <tbody id="tbl-body-canjes"></tbody>
            <tfoot id="tbl-foot-canjes"></tfoot>
          </table>
        </div>
      </div>

    </div>

  </div>

  <!-- ============ VENTA ============ -->
  <div class="panel active" data-panel="venta">

    <!-- SUB-TABS dentro de VENTA -->
    <div class="subtabs">
      <button class="subtab active" data-sub="posicion">Posición General</button>
      <button class="subtab" data-sub="financiera">Financiera</button>
    </div>

    <!-- ========== SUB: POSICIÓN ========== -->
    <div class="subpanel active" data-sub-panel="posicion">

      <!-- KPI ROW -->
      <div class="kpis" id="kpi-row"></div>

      <!-- FILTROS -->
      <div class="filterbar" id="filterbar">
        <div><label>EMPRESA</label><select id="f-empresa"><option value="">Todas</option></select></div>
        <div><label>CEREALERA</label><select id="f-org"><option value="">Todas</option></select></div>
        <div><label>GRANO</label><select id="f-prod"><option value="">Todos</option></select></div>
        <div><label>TIPO CONTRATO</label><select id="f-tcont"><option value="">Todos</option></select></div>
        <div><label>CAMPAÑA</label><select id="f-camp"><option value="">Todas</option></select></div>
        <div><label>BUSCAR</label><input type="text" id="f-q" placeholder="numero, descripción…" /></div>
        <button class="clear" id="btn-clear">Limpiar</button>
        <div class="count" id="row-count">0 / 0 contratos</div>
      </div>

      <!-- RESUMEN POR GRANO -->
      <div class="section">
        <h3>Resumen por Grano <span class="badge" id="grain-meta"></span></h3>
        <div class="grain-grid" id="grain-grid"></div>
      </div>

      <!-- DONUT + TOP -->
      <div class="row2">
        <div class="section">
          <h3>Top 10 Cerealeras por Toneladas Ajustadas</h3>
          <div class="chart-wrap"><canvas id="chart-top"></canvas></div>
        </div>
        <div class="section">
          <h3>Distribución por Grano (Tn Ajustadas)</h3>
          <div class="chart-wrap"><canvas id="chart-donut"></canvas></div>
        </div>
      </div>

      <!-- DETALLE -->
      <div class="section">
        <h3>Detalle de Contratos — Posición Física <span class="badge">Click en encabezado para ordenar</span></h3>
        <div class="tbl-wrap">
          <table id="tbl">
            <thead><tr id="tbl-head"></tr></thead>
            <tbody id="tbl-body"></tbody>
            <tfoot id="tbl-foot"></tfoot>
          </table>
        </div>
      </div>
    </div>

    <!-- ========== SUB: FINANCIERA ========== -->
    <div class="subpanel" data-sub-panel="financiera">

      <!-- KPI ROW FIN -->
      <div class="kpis" id="kpi-row-fin"></div>

      <!-- FILTROS FIN (mismos selectores con prefijo ff-) -->
      <div class="filterbar" id="filterbar-fin">
        <div><label>EMPRESA</label><select id="ff-empresa"><option value="">Todas</option></select></div>
        <div><label>CEREALERA</label><select id="ff-org"><option value="">Todas</option></select></div>
        <div><label>GRANO</label><select id="ff-prod"><option value="">Todos</option></select></div>
        <div><label>TIPO CONTRATO</label><select id="ff-tcont"><option value="">Todos</option></select></div>
        <div><label>MONEDA</label><select id="ff-moneda"><option value="">Todas</option></select></div>
        <div><label>CAMPAÑA</label><select id="ff-camp"><option value="">Todas</option></select></div>
        <div><label>BUSCAR</label><input type="text" id="ff-q" placeholder="numero, descripción…" /></div>
        <button class="clear" id="btn-clear-fin">Limpiar</button>
        <div class="count" id="row-count-fin">0 / 0 contratos</div>
      </div>

      <!-- RESUMEN POR GRANO FIN -->
      <div class="section">
        <h3>Resumen Financiero por Grano <span class="badge" id="grain-meta-fin"></span></h3>
        <div class="grain-grid" id="grain-grid-fin"></div>
      </div>

      <!-- CHARTS FIN -->
      <div class="row2">
        <div class="section">
          <h3>Top 10 Cerealeras — Importe Pendiente de Liquidar</h3>
          <div class="chart-wrap"><canvas id="chart-top-fin"></canvas></div>
        </div>
        <div class="section">
          <h3>Importes por Moneda</h3>
          <div class="chart-wrap"><canvas id="chart-mon-fin"></canvas></div>
        </div>
      </div>

      <!-- DETALLE FIN -->
      <div class="section">
        <h3>Detalle Financiero <span class="badge">Click en encabezado para ordenar</span></h3>
        <div class="tbl-wrap">
          <table id="tbl-fin">
            <thead><tr id="tbl-head-fin"></tr></thead>
            <tbody id="tbl-body-fin"></tbody>
            <tfoot id="tbl-foot-fin"></tfoot>
          </table>
        </div>
      </div>

      <!-- CALENDARIO COBRANZAS -->
      <div class="section">
        <h3>📅 Calendario de Cobranzas (manual) <span class="badge" id="cal-meta">cargá importes esperados por contrato y fecha</span></h3>
        <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
          <label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">Año
            <select id="cal-year" style="margin-left:6px;padding:6px 9px;border:1px solid var(--line);border-radius:6px"></select>
          </label>
          <label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">Moneda
            <select id="cal-moneda" style="margin-left:6px;padding:6px 9px;border:1px solid var(--line);border-radius:6px"></select>
          </label>
          <button class="clear" id="cal-export">⬇️ Exportar JSON</button>
          <button class="clear" id="cal-import">⬆️ Importar JSON</button>
          <input type="file" id="cal-import-file" accept="application/json" style="display:none" />
          <button class="clear" id="cal-clear" style="color:var(--red);border-color:#fecaca">🗑️ Borrar todo</button>
          <span style="margin-left:auto;font-size:12px;color:var(--muted)" id="cal-storage-info"></span>
        </div>
        <div class="tbl-wrap" style="max-height:600px">
          <table id="cal-tbl">
            <thead><tr id="cal-head"></tr></thead>
            <tbody id="cal-body"></tbody>
            <tfoot><tr id="cal-foot"></tr></tfoot>
          </table>
        </div>
      </div>

    </div>

  </div>

  <!-- ============ POSICION ============ -->
  <div class="panel" data-panel="posicion">
    <div class="placeholder">
      <div class="ico">📊</div>
      <h4>Pestaña Posición General</h4>
      <div>Próximamente: Stock por Depósito (8.485 reg.), Composición de Saldos (4.870 reg.), Clientes/Vendedores (9.365 reg.).</div>
    </div>
  </div>

</div>

<script>
/* ============== DATOS EMBEBIDOS ============== */
const PAYLOAD = __PAYLOAD__;

/* ============== HELPERS ============== */
const fmt = {
  int: (n) => n==null||isNaN(n) ? '—' : Math.round(n).toLocaleString('es-AR'),
  num: (n) => n==null||isNaN(n) ? '—' : Number(n).toLocaleString('es-AR',{maximumFractionDigits:0}),
  num2: (n) => n==null||isNaN(n) ? '—' : Number(n).toLocaleString('es-AR',{minimumFractionDigits:2,maximumFractionDigits:2}),
  pct: (n) => n==null||isNaN(n) ? '—' : (n*100).toLocaleString('es-AR',{maximumFractionDigits:1})+'%',
  money: (n, cur='') => n==null||isNaN(n) ? '—' : (cur?cur+' ':'') + Number(n).toLocaleString('es-AR',{maximumFractionDigits:0}),
  date: (s) => s||'—',
};

function uniqSorted(arr, key){
  return [...new Set(arr.map(r => r[key]).filter(v => v!=null && v!==''))].sort((a,b)=>String(a).localeCompare(String(b),'es'));
}

function grainClass(p){
  if(!p) return '';
  const s = p.toLowerCase();
  if(s.includes('soja')) return 'soja';
  if(s.includes('maiz') || s.includes('maíz')) return 'maiz';
  if(s.includes('trigo')) return 'trigo';
  if(s.includes('girasol')) return 'girasol';
  return '';
}

function estadoChip(r){
  const e = (r.estadoanulacion||'').toLowerCase();
  if(e.includes('anul')) return '<span class="chip err">Anulado</span>';
  // sin anulación: ver cumplimiento entrega (sobre cantidad ajustada = max)
  const aj = r.cantidadmax||0, ent = r.cantidadentregada||0;
  if(aj<=0) return '<span class="chip neutral">—</span>';
  const p = ent/aj;
  if(p >= 0.999) return '<span class="chip ok">Entregado</span>';
  if(p > 0)      return '<span class="chip warn">Parcial</span>';
  return '<span class="chip info">Pendiente</span>';
}

/* ============== TAB SWITCHING ============== */
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.querySelector(`.panel[data-panel="${t.dataset.tab}"]`).classList.add('active');
  });
});

/* ============== SUB-TAB SWITCHING ============== */
document.querySelectorAll('.subtab').forEach(st => {
  st.addEventListener('click', () => {
    const parent = st.closest('.panel');
    parent.querySelectorAll('.subtab').forEach(x => x.classList.remove('active'));
    parent.querySelectorAll('.subpanel').forEach(x => x.classList.remove('active'));
    st.classList.add('active');
    parent.querySelector(`.subpanel[data-sub-panel="${st.dataset.sub}"]`).classList.add('active');
  });
});

/* ============== TAB COUNTS ============== */
document.getElementById('cnt-compra').textContent  = (PAYLOAD.counts.compra||0).toLocaleString('es-AR');
document.getElementById('cnt-venta').textContent   = (PAYLOAD.counts.venta||0).toLocaleString('es-AR');
document.getElementById('cnt-pos').textContent     = (PAYLOAD.counts.posicion||0).toLocaleString('es-AR');

/* ============== PILOTO: Resumen Contratos Venta Granos ============== */
const DATA = PAYLOAD.pilot;   // array de rows ya normalizadas
let filtered = DATA.slice();
let sortKey = null, sortDir = 1;

// helper para identificar una fila de manera estable
const rowId = (r) => String(r.contratoid || r.numerointerno || r.contrato || '');

// selecciones tipo Excel (Sets de ids), una por tabla
const SEL_POS = new Set();
const SEL_FIN = new Set();
// para shift+click: ultimo indice clickeado por tabla
let lastClickIdx = {pos: null, fin: null};

// columnas a mostrar en la tabla — POSICIÓN FÍSICA (sin importes/moneda)
// sum:true  = se suma en el footer
// sum:'avg' = se promedia (precios)
// sum:false = no se totaliza
const TABLE_COLS = [
  {k:'fecha',                       lbl:'Fecha',           num:false},
  {k:'numerointerno',               lbl:'Nº',              num:false},
  {k:'organizacion',                lbl:'Cerealera',       num:false},
  {k:'producto',                    lbl:'Grano',           num:false},
  {k:'tipocontrato',                lbl:'Tipo',            num:false},
  {k:'cantidadmax',                 lbl:'Tn Ajustadas',    num:true, sum:true},
  {k:'cantidadentregada',           lbl:'Tn Entregadas',   num:true, sum:true},
  {k:'_pdteEntrega',                lbl:'Tn Pdte Entrega', num:true, sum:true},
  {k:'cantidadcertificadaneta',     lbl:'Tn Certif.',      num:true, sum:true},
  {k:'cantidadpendientecertificar', lbl:'Tn Pdte Cert.',   num:true, sum:true},
  {k:'fechaminentrega',             lbl:'Entrega Desde',   num:false},
  {k:'fechamaxentrega',             lbl:'Entrega Hasta',   num:false},
  {k:'campana',                     lbl:'Campaña',         num:false},
  {k:'corredor',                    lbl:'Corredor',        num:false},
  {k:'puertoreferencia',            lbl:'Puerto',          num:false},
  {k:'_estado',                     lbl:'Estado',          num:false, html:true},
];

/* ----- filtros encadenados ----- */
// definicion: id del select -> columna del row + placeholder
const FILTERS = [
  {id:'f-empresa', col:'empresa',         placeholder:'Todas'},
  {id:'f-org',     col:'organizacion',    placeholder:'Todas'},
  {id:'f-prod',    col:'producto',        placeholder:'Todos'},
  {id:'f-tcont',   col:'tipocontrato',    placeholder:'Todos'},
  {id:'f-camp',    col:'campana',         placeholder:'Todas'},
];

// valores unicos por columna (base, alfabetico, no cambia)
const ALL_VALS = {};
FILTERS.forEach(f => { ALL_VALS[f.col] = uniqSorted(DATA, f.col); });

function escapeHtml(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]); }

function rowPassesText(r, fQ){
  if(!fQ) return true;
  const hay = (r.numerointerno||'')+' '+(r.descripcion||'')+' '+(r.numerodocumentoadicional||'')+' '+(r.contrato||'');
  return hay.toLowerCase().includes(fQ);
}

// devuelve true si row pasa todos los filtros activos EXCEPTO el que matchea `skipCol`
function rowPassesExcept(r, skipCol){
  for(const f of FILTERS){
    if(f.col === skipCol) continue;
    const v = document.getElementById(f.id).value;
    if(v && r[f.col] !== v) return false;
  }
  const fQ = document.getElementById('f-q').value.trim().toLowerCase();
  return rowPassesText(r, fQ);
}

function rebuildSelects(){
  FILTERS.forEach(f => {
    const sel = document.getElementById(f.id);
    const current = sel.value;
    // contar cada valor posible considerando los otros filtros
    const counts = {};
    DATA.forEach(r => {
      if(rowPassesExcept(r, f.col)){
        const k = r[f.col];
        if(k != null && k !== '') counts[k] = (counts[k]||0) + 1;
      }
    });
    // todos los valores conocidos + ordenar: con datos primero, despues los de 0
    const items = ALL_VALS[f.col].map(v => ({v, n: counts[v]||0}));
    items.sort((a,b)=>{
      if((a.n>0) !== (b.n>0)) return a.n>0 ? -1 : 1;
      return String(a.v).localeCompare(String(b.v),'es');
    });
    sel.innerHTML = `<option value="">${f.placeholder}</option>` +
      items.map(it =>
        `<option value="${escapeHtml(it.v)}" class="${it.n===0?'opt-zero':''}">${escapeHtml(it.v)} (${it.n.toLocaleString('es-AR')})</option>`
      ).join('');
    // restaurar seleccion (la mantenemos aunque haya quedado en 0)
    if(current) sel.value = current;
  });
}

// listeners
FILTERS.forEach(f => {
  document.getElementById(f.id).addEventListener('change', applyFilters);
});
document.getElementById('f-q').addEventListener('input', applyFilters);
document.getElementById('btn-clear').addEventListener('click', () => {
  FILTERS.forEach(f => document.getElementById(f.id).value = '');
  document.getElementById('f-q').value = '';
  applyFilters();
});

function applyFilters(){
  // actualizar conteos en TODOS los selects (encadenados)
  rebuildSelects();

  // filtrar dataset
  const vals = {};
  FILTERS.forEach(f => { const v = document.getElementById(f.id).value; if(v) vals[f.col] = v; });
  const fQ = document.getElementById('f-q').value.trim().toLowerCase();
  filtered = DATA.filter(r => {
    for(const [col, v] of Object.entries(vals)){ if(r[col] !== v) return false; }
    return rowPassesText(r, fQ);
  });
  render();
}

// build inicial de los selects
rebuildSelects();

/* ----- KPIs + resumen grano + charts + tabla ----- */
let chartTop=null, chartDonut=null;

function render(){
  // KPIs (posición física, "ajustada" = cantidadmax)
  let cnt=filtered.length, tnAj=0, tnEnt=0;
  filtered.forEach(r => {
    tnAj  += r.cantidadmax || 0;
    tnEnt += r.cantidadentregada || 0;
  });
  const tnPdt = tnAj - tnEnt;
  const cumplimiento = tnAj>0 ? tnEnt/tnAj : null;

  document.getElementById('kpi-row').innerHTML = `
    <div class="kpi"><div class="lbl">Contratos</div><div class="val">${fmt.int(cnt)}</div><div class="hint">de ${fmt.int(DATA.length)} totales</div></div>
    <div class="kpi"><div class="lbl">Toneladas Ajustadas</div><div class="val">${fmt.num(tnAj)}</div><div class="hint">Cantidad final post-ajustes</div></div>
    <div class="kpi green"><div class="lbl">Toneladas Entregadas</div><div class="val">${fmt.num(tnEnt)}</div><div class="hint">Cumplimiento: ${fmt.pct(cumplimiento)}</div></div>
    <div class="kpi orange"><div class="lbl">Tn Pendientes de Entrega</div><div class="val">${fmt.num(tnPdt)}</div><div class="hint">= Ajustadas − Entregadas</div></div>
  `;

  // resumen por grano (sin importes)
  const byGrain = {};
  filtered.forEach(r => {
    const p = r.producto || '—';
    if(!byGrain[p]) byGrain[p] = {cnt:0,tnAj:0,tnEnt:0};
    byGrain[p].cnt++;
    byGrain[p].tnAj  += r.cantidadmax || 0;
    byGrain[p].tnEnt += r.cantidadentregada || 0;
  });
  const grainOrder = Object.entries(byGrain).sort((a,b)=>b[1].tnAj - a[1].tnAj);
  document.getElementById('grain-meta').textContent = `${grainOrder.length} granos`;
  document.getElementById('grain-grid').innerHTML = grainOrder.map(([g,v]) => {
    const pct = v.tnAj>0 ? v.tnEnt/v.tnAj : 0;
    const pdt = v.tnAj - v.tnEnt;
    return `<div class="grain-card ${grainClass(g)}">
      <div class="name"><span>${g}</span><span class="cnt">${v.cnt} contratos</span></div>
      <div class="row"><span class="k">Tn Ajustadas</span><span><b>${fmt.num(v.tnAj)}</b></span></div>
      <div class="row"><span class="k">Tn Entregadas</span><span>${fmt.num(v.tnEnt)} <span style="color:var(--muted)">(${fmt.pct(pct)})</span></span></div>
      <div class="row"><span class="k">Tn Pdte Entrega</span><span style="color:var(--orange)"><b>${fmt.num(pdt)}</b></span></div>
      <div class="bar"><div style="width:${Math.min(100,pct*100)}%"></div></div>
    </div>`;
  }).join('') || '<div class="placeholder">Sin datos para los filtros aplicados</div>';

  // chart top cerealeras por Tn Ajustadas
  const byOrg = {};
  filtered.forEach(r => { byOrg[r.organizacion||'—'] = (byOrg[r.organizacion||'—']||0)+(r.cantidadmax||0); });
  const topOrg = Object.entries(byOrg).sort((a,b)=>b[1]-a[1]).slice(0,10);
  if(chartTop) chartTop.destroy();
  chartTop = new Chart(document.getElementById('chart-top'), {
    type:'bar',
    data:{labels: topOrg.map(x=>x[0].length>30?x[0].slice(0,30)+'…':x[0]), datasets:[{label:'Tn Ajustadas', data: topOrg.map(x=>x[1]), backgroundColor:'#3b82f6', borderRadius:4}]},
    options:{indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>fmt.num(c.parsed.x)+' tn'}}}, scales:{x:{ticks:{callback:v=>v.toLocaleString('es-AR')}}}}
  });

  // donut por grano (Tn Ajustadas)
  if(chartDonut) chartDonut.destroy();
  const palette = ['#16a34a','#f59e0b','#a16207','#3b82f6','#dc2626','#6366f1','#0891b2','#94a3b8','#ec4899','#84cc16'];
  chartDonut = new Chart(document.getElementById('chart-donut'), {
    type:'doughnut',
    data:{labels: grainOrder.map(x=>x[0]), datasets:[{data: grainOrder.map(x=>x[1].tnAj), backgroundColor: palette}]},
    options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{position:'bottom',labels:{boxWidth:10,padding:8,font:{size:11}}}, tooltip:{callbacks:{label:c=>c.label+': '+fmt.num(c.parsed)+' tn'}}}}
  });

  // tabla
  const head = TABLE_COLS.map(c => {
    const ar = (sortKey===c.k) ? (sortDir>0?'▲':'▼') : '';
    const cls = (sortKey===c.k) ? (sortDir>0?'sort-asc':'sort-desc') : '';
    return `<th class="${cls}" data-k="${c.k}" data-num="${c.num?1:0}">${c.lbl}<span class="arrow">${ar||'⇅'}</span></th>`;
  }).join('');
  document.getElementById('tbl-head').innerHTML = head;
  document.querySelectorAll('thead th').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.k;
      if(sortKey === k) sortDir = -sortDir;
      else { sortKey = k; sortDir = 1; }
      render();
    });
  });

  // helper para columnas derivadas
  const getVal = (r, k) => {
    if(k==='_estado') return r.estadoanulacion||'';
    if(k==='_pdteEntrega') return (r.cantidadmax||0) - (r.cantidadentregada||0);
    return r[k];
  };

  let rows = filtered;
  if(sortKey){
    const col = TABLE_COLS.find(c=>c.k===sortKey);
    rows = filtered.slice().sort((a,b)=>{
      let va = getVal(a, sortKey);
      let vb = getVal(b, sortKey);
      if(col && col.num){ va = va||0; vb = vb||0; return (va-vb)*sortDir; }
      va = (va==null?'':String(va)); vb = (vb==null?'':String(vb));
      return va.localeCompare(vb,'es',{numeric:true})*sortDir;
    });
  }

  const MAX = 1000;   // proteccion DOM
  const visibleRows = rows.slice(0,MAX);
  const body = visibleRows.map((r,i) => {
    const id = rowId(r);
    const selCls = SEL_POS.has(id) ? ' class="row-sel"' : '';
    return `<tr data-id="${id}" data-i="${i}"${selCls}>`+TABLE_COLS.map(c=>{
      if(c.k==='_estado') return '<td>'+estadoChip(r)+'</td>';
      const v = getVal(r, c.k);
      if(c.num) return `<td class="num">${v==null?'<span class=muted>—</span>':fmt.num(v)}</td>`;
      return `<td>${v==null?'<span class=muted>—</span>':String(v)}</td>`;
    }).join('')+'</tr>';
  }).join('');
  document.getElementById('tbl-body').innerHTML = body || '<tr><td colspan="99" style="padding:30px;text-align:center;color:var(--muted)">Sin resultados</td></tr>';

  // listeners de selección de filas (Excel-style)
  document.querySelectorAll('#tbl-body tr[data-id]').forEach(tr => {
    tr.addEventListener('click', (ev) => {
      const id = tr.dataset.id, i = parseInt(tr.dataset.i,10);
      if(ev.shiftKey && lastClickIdx.pos !== null){
        const a = Math.min(i, lastClickIdx.pos), b = Math.max(i, lastClickIdx.pos);
        for(let k=a; k<=b; k++) SEL_POS.add(rowId(visibleRows[k]));
      } else {
        if(SEL_POS.has(id)) SEL_POS.delete(id);
        else SEL_POS.add(id);
        lastClickIdx.pos = i;
      }
      renderFootPos(rows);
      // refrescar clase visual sin re-renderizar toda la tabla
      document.querySelectorAll('#tbl-body tr[data-id]').forEach(t2 => {
        t2.classList.toggle('row-sel', SEL_POS.has(t2.dataset.id));
      });
    });
  });

  document.getElementById('row-count').textContent =
    `${rows.length.toLocaleString('es-AR')} / ${DATA.length.toLocaleString('es-AR')} contratos` +
    (rows.length>MAX ? ` (mostrando ${MAX})` : '');

  renderFootPos(rows);
}

function renderFootPos(rows){
  const computeFoot = (subset, label, isSel) => TABLE_COLS.map((c, idx) => {
    if(idx === 0){
      const btn = isSel ? ` <button class="clear-sel" onclick="SEL_POS.clear(); render();">Limpiar selección</button>` : '';
      return `<td class="lbl">${label} (${subset.length.toLocaleString('es-AR')})${btn}</td>`;
    }
    if(c.sum === true){
      const total = subset.reduce((acc,r) => acc + (Number(c.k==='_pdteEntrega' ? ((r.cantidadmax||0)-(r.cantidadentregada||0)) : r[c.k]) || 0), 0);
      return `<td class="num">${fmt.num(total)}</td>`;
    }
    if(c.sum === 'avg'){
      const valid = subset.map(r => Number(r[c.k])).filter(v => !isNaN(v) && v!==0);
      const avg = valid.length ? valid.reduce((a,b)=>a+b,0)/valid.length : null;
      return `<td class="num" title="promedio">${avg==null?'—':fmt.num(avg)}</td>`;
    }
    return '<td></td>';
  }).join('');

  const totalRow = `<tr>${computeFoot(rows, 'TOTAL', false)}</tr>`;
  const selRows = rows.filter(r => SEL_POS.has(rowId(r)));
  const selRow  = selRows.length ? `<tr class="sel">${computeFoot(selRows, '🟨 SELECCIONADOS', true)}</tr>` : '';
  document.getElementById('tbl-foot').innerHTML = totalRow + selRow;
}

render();


/* ============================================================
   ===============  SUB-PESTAÑA FINANCIERA  ==================
   ============================================================ */

const FIN_FILTERS = [
  {id:'ff-empresa', col:'empresa',      placeholder:'Todas'},
  {id:'ff-org',     col:'organizacion', placeholder:'Todas'},
  {id:'ff-prod',    col:'producto',     placeholder:'Todos'},
  {id:'ff-tcont',   col:'tipocontrato', placeholder:'Todos'},
  {id:'ff-moneda',  col:'moneda',       placeholder:'Todas'},
  {id:'ff-camp',    col:'campana',      placeholder:'Todas'},
];

const FIN_ALL_VALS = {};
FIN_FILTERS.forEach(f => { FIN_ALL_VALS[f.col] = uniqSorted(DATA, f.col); });

let finFiltered = DATA.slice();
let finSortKey = null, finSortDir = 1;

const FIN_TABLE_COLS = [
  {k:'fecha',                       lbl:'Fecha',          num:false},
  {k:'numerointerno',               lbl:'Nº',             num:false},
  {k:'organizacion',                lbl:'Cerealera',      num:false},
  {k:'producto',                    lbl:'Grano',          num:false},
  {k:'tipocontrato',                lbl:'Tipo',           num:false},
  {k:'moneda',                      lbl:'Mon.',           num:false},
  {k:'cantidadfijada',              lbl:'Tn Fijadas',     num:true, sum:true},
  {k:'preciopromediofijado',        lbl:'Precio Fij.',    num:true, sum:'avg'},
  {k:'importefijado',               lbl:'Imp. Fijado',    num:true, sum:true},
  {k:'cantidadliquidada',           lbl:'Tn Liquid.',     num:true, sum:true},
  {k:'precioliquidado',             lbl:'Precio Liq.',    num:true, sum:'avg'},
  {k:'importeliquidado',            lbl:'Imp. Liquid.',   num:true, sum:true},
  {k:'cantidadentregadapendienteliquidar', lbl:'Tn Pdte Liq.',   num:true, sum:true},
  {k:'importecantidadentregadapendienteliquidar', lbl:'Imp. Pdte Liq.', num:true, sum:true},
  {k:'campana',                     lbl:'Campaña',        num:false},
];

function finRowPassesText(r, fQ){
  if(!fQ) return true;
  const hay = (r.numerointerno||'')+' '+(r.descripcion||'')+' '+(r.numerodocumentoadicional||'')+' '+(r.contrato||'');
  return hay.toLowerCase().includes(fQ);
}
function finRowPassesExcept(r, skipCol){
  for(const f of FIN_FILTERS){
    if(f.col === skipCol) continue;
    const v = document.getElementById(f.id).value;
    if(v && r[f.col] !== v) return false;
  }
  return finRowPassesText(r, document.getElementById('ff-q').value.trim().toLowerCase());
}
function finRebuildSelects(){
  FIN_FILTERS.forEach(f => {
    const sel = document.getElementById(f.id);
    const current = sel.value;
    const counts = {};
    DATA.forEach(r => {
      if(finRowPassesExcept(r, f.col)){
        const k = r[f.col];
        if(k != null && k !== '') counts[k] = (counts[k]||0) + 1;
      }
    });
    const items = FIN_ALL_VALS[f.col].map(v => ({v, n: counts[v]||0}));
    items.sort((a,b)=>{ if((a.n>0)!==(b.n>0)) return a.n>0?-1:1; return String(a.v).localeCompare(String(b.v),'es'); });
    sel.innerHTML = `<option value="">${f.placeholder}</option>` +
      items.map(it=>`<option value="${escapeHtml(it.v)}" class="${it.n===0?'opt-zero':''}">${escapeHtml(it.v)} (${it.n.toLocaleString('es-AR')})</option>`).join('');
    if(current) sel.value = current;
  });
}
FIN_FILTERS.forEach(f => document.getElementById(f.id).addEventListener('change', finApply));
document.getElementById('ff-q').addEventListener('input', finApply);
document.getElementById('btn-clear-fin').addEventListener('click', () => {
  FIN_FILTERS.forEach(f => document.getElementById(f.id).value='');
  document.getElementById('ff-q').value='';
  finApply();
});

function finApply(){
  finRebuildSelects();
  const vals={};
  FIN_FILTERS.forEach(f => { const v = document.getElementById(f.id).value; if(v) vals[f.col]=v; });
  const fQ = document.getElementById('ff-q').value.trim().toLowerCase();
  finFiltered = DATA.filter(r => {
    for(const [col,v] of Object.entries(vals)){ if(r[col]!==v) return false; }
    return finRowPassesText(r, fQ);
  });
  finRender();
  calRender();   // el calendario tambien depende de los filtros
}

let finChartTop=null, finChartMon=null;

function finRender(){
  // KPIs — "Pendiente de Liquidar" se calcula SIEMPRE sobre lo entregado (no sobre lo fijado)
  let cnt=finFiltered.length, tnFij=0, tnLiq=0, tnEnt=0, tnPdtLiq=0;
  const impByMon = {};   // {moneda: {fij, liq, pdtSobreEntregado}}
  finFiltered.forEach(r => {
    tnFij    += r.cantidadfijada || 0;
    tnLiq    += r.cantidadliquidada || 0;
    tnEnt    += r.cantidadentregada || 0;
    tnPdtLiq += r.cantidadentregadapendienteliquidar || 0;
    const m = r.moneda || '—';
    if(!impByMon[m]) impByMon[m] = {fij:0, liq:0, pdt:0};
    impByMon[m].fij += r.importefijado || 0;
    impByMon[m].liq += r.importeliquidado || 0;
    impByMon[m].pdt += r.importecantidadentregadapendienteliquidar || 0;
  });
  const cumplLiq = tnEnt>0 ? tnLiq/tnEnt : null;
  const monedaTop = Object.entries(impByMon).sort((a,b)=>Math.abs(b[1].pdt) - Math.abs(a[1].pdt))[0];
  document.getElementById('kpi-row-fin').innerHTML = `
    <div class="kpi"><div class="lbl">Contratos</div><div class="val">${fmt.int(cnt)}</div><div class="hint">de ${fmt.int(DATA.length)} totales</div></div>
    <div class="kpi"><div class="lbl">Tn Fijadas / Entregadas</div><div class="val">${fmt.num(tnFij)} / ${fmt.num(tnEnt)}</div><div class="hint">Liquidadas: ${fmt.num(tnLiq)} (${fmt.pct(cumplLiq)} de entregadas)</div></div>
    <div class="kpi orange"><div class="lbl">Tn Pdte Liquidar (entregadas)</div><div class="val">${fmt.num(tnPdtLiq)}</div><div class="hint">= Entregadas − Liquidadas</div></div>
    <div class="kpi red"><div class="lbl">Imp. Pdte Liquidar · ${monedaTop?monedaTop[0]:'—'}</div><div class="val">${fmt.num(monedaTop?monedaTop[1].pdt:null)}</div><div class="hint">${Object.entries(impByMon).map(([m,v])=>m+': '+fmt.num(v.pdt)).join(' · ')}</div></div>
  `;

  // resumen por grano financiero (Pdte Liq = sobre entregadas)
  const byG = {};
  finFiltered.forEach(r => {
    const p = r.producto || '—';
    if(!byG[p]) byG[p] = {cnt:0,tnEnt:0,tnLiq:0,impPdt:{}};
    byG[p].cnt++;
    byG[p].tnEnt += r.cantidadentregada || 0;
    byG[p].tnLiq += r.cantidadliquidada || 0;
    const m = r.moneda||'—';
    byG[p].impPdt[m] = (byG[p].impPdt[m]||0) + (r.importecantidadentregadapendienteliquidar || 0);
  });
  const gOrder = Object.entries(byG).sort((a,b)=>b[1].tnEnt - a[1].tnEnt);
  document.getElementById('grain-meta-fin').textContent = `${gOrder.length} granos`;
  document.getElementById('grain-grid-fin').innerHTML = gOrder.map(([g,v]) => {
    const pct = v.tnEnt>0 ? v.tnLiq/v.tnEnt : 0;
    const monTop = Object.entries(v.impPdt).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1]))[0];
    return `<div class="grain-card ${grainClass(g)}">
      <div class="name"><span>${g}</span><span class="cnt">${v.cnt} contratos</span></div>
      <div class="row"><span class="k">Tn Entregadas</span><span><b>${fmt.num(v.tnEnt)}</b></span></div>
      <div class="row"><span class="k">Tn Liquidadas</span><span>${fmt.num(v.tnLiq)} <span style="color:var(--muted)">(${fmt.pct(pct)})</span></span></div>
      <div class="row"><span class="k">Pdte Liq. ${monTop?monTop[0]:''}</span><span style="color:var(--red)"><b>${fmt.num(monTop?monTop[1]:0)}</b></span></div>
      <div class="bar"><div style="width:${Math.min(100,pct*100)}%"></div></div>
    </div>`;
  }).join('') || '<div class="placeholder">Sin datos para los filtros aplicados</div>';

  // Chart top cerealeras por Imp Pdte Liquidar sobre entregadas (moneda dominante)
  const byOrgPdt = {};
  finFiltered.forEach(r => {
    if(monedaTop && r.moneda !== monedaTop[0]) return;
    byOrgPdt[r.organizacion||'—'] = (byOrgPdt[r.organizacion||'—']||0) + (r.importecantidadentregadapendienteliquidar||0);
  });
  const top = Object.entries(byOrgPdt).filter(x=>Math.abs(x[1])>0).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,10);
  if(finChartTop) finChartTop.destroy();
  finChartTop = new Chart(document.getElementById('chart-top-fin'), {
    type:'bar',
    data:{labels: top.map(x=>x[0].length>30?x[0].slice(0,30)+'…':x[0]), datasets:[{label:`Imp. Pdte Liq. (${monedaTop?monedaTop[0]:''})`, data: top.map(x=>x[1]), backgroundColor:'#dc2626', borderRadius:4}]},
    options:{indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:true,position:'bottom'}, tooltip:{callbacks:{label:c=>fmt.num(c.parsed.x)}}}, scales:{x:{ticks:{callback:v=>v.toLocaleString('es-AR')}}}}
  });

  // Chart por moneda (importes Fij/Liq/Pdt)
  if(finChartMon) finChartMon.destroy();
  const monedas = Object.keys(impByMon);
  finChartMon = new Chart(document.getElementById('chart-mon-fin'), {
    type:'bar',
    data:{
      labels: monedas,
      datasets:[
        {label:'Fijado',    data: monedas.map(m=>impByMon[m].fij), backgroundColor:'#3b82f6'},
        {label:'Liquidado', data: monedas.map(m=>impByMon[m].liq), backgroundColor:'#16a34a'},
        {label:'Pdte Liq.', data: monedas.map(m=>impByMon[m].pdt), backgroundColor:'#dc2626'},
      ]
    },
    options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{position:'bottom'}, tooltip:{callbacks:{label:c=>c.dataset.label+': '+fmt.num(c.parsed.y)}}}, scales:{y:{ticks:{callback:v=>v.toLocaleString('es-AR')}}}}
  });

  // Tabla detallada financiera
  const head = FIN_TABLE_COLS.map(c => {
    const ar = (finSortKey===c.k) ? (finSortDir>0?'▲':'▼') : '';
    const cls = (finSortKey===c.k) ? (finSortDir>0?'sort-asc':'sort-desc') : '';
    return `<th class="${cls}" data-k="${c.k}" data-num="${c.num?1:0}">${c.lbl}<span class="arrow">${ar||'⇅'}</span></th>`;
  }).join('');
  document.getElementById('tbl-head-fin').innerHTML = head;
  document.querySelectorAll('#tbl-head-fin th').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.k;
      if(finSortKey === k) finSortDir = -finSortDir;
      else { finSortKey = k; finSortDir = 1; }
      finRender();
    });
  });
  let rows = finFiltered;
  if(finSortKey){
    const col = FIN_TABLE_COLS.find(c=>c.k===finSortKey);
    rows = finFiltered.slice().sort((a,b)=>{
      let va = a[finSortKey], vb = b[finSortKey];
      if(col && col.num){ va=va||0; vb=vb||0; return (va-vb)*finSortDir; }
      va=(va==null?'':String(va)); vb=(vb==null?'':String(vb));
      return va.localeCompare(vb,'es',{numeric:true})*finSortDir;
    });
  }
  const MAX = 1000;
  const visibleFin = rows.slice(0,MAX);
  document.getElementById('tbl-body-fin').innerHTML = visibleFin.map((r,i) => {
    const id = rowId(r);
    const selCls = SEL_FIN.has(id) ? ' class="row-sel"' : '';
    return `<tr data-id="${id}" data-i="${i}"${selCls}>`+FIN_TABLE_COLS.map(c=>{
      const v=r[c.k];
      if(c.num) return `<td class="num">${v==null?'<span class=muted>—</span>':fmt.num(v)}</td>`;
      return `<td>${v==null?'<span class=muted>—</span>':String(v)}</td>`;
    }).join('')+'</tr>';
  }).join('') || '<tr><td colspan="99" style="padding:30px;text-align:center;color:var(--muted)">Sin resultados</td></tr>';

  // listeners selección filas (Excel-style)
  document.querySelectorAll('#tbl-body-fin tr[data-id]').forEach(tr => {
    tr.addEventListener('click', (ev) => {
      const id = tr.dataset.id, i = parseInt(tr.dataset.i,10);
      if(ev.shiftKey && lastClickIdx.fin !== null){
        const a = Math.min(i, lastClickIdx.fin), b = Math.max(i, lastClickIdx.fin);
        for(let k=a; k<=b; k++) SEL_FIN.add(rowId(visibleFin[k]));
      } else {
        if(SEL_FIN.has(id)) SEL_FIN.delete(id);
        else SEL_FIN.add(id);
        lastClickIdx.fin = i;
      }
      renderFootFin(rows);
      document.querySelectorAll('#tbl-body-fin tr[data-id]').forEach(t2 => {
        t2.classList.toggle('row-sel', SEL_FIN.has(t2.dataset.id));
      });
    });
  });

  renderFootFin(rows);

  document.getElementById('row-count-fin').textContent =
    `${rows.length.toLocaleString('es-AR')} / ${DATA.length.toLocaleString('es-AR')} contratos` +
    (rows.length>MAX ? ` (mostrando ${MAX})` : '');
}

function renderFootFin(rows){
  const computeFootFin = (subset, label, isSel) => FIN_TABLE_COLS.map((c, idx) => {
    if(idx === 0){
      const btn = isSel ? ` <button class="clear-sel" onclick="SEL_FIN.clear(); finRender();">Limpiar selección</button>` : '';
      return `<td class="lbl">${label} (${subset.length.toLocaleString('es-AR')})${btn}</td>`;
    }
    if(c.sum === true){
      const total = subset.reduce((acc,r) => acc + (Number(r[c.k]) || 0), 0);
      return `<td class="num">${fmt.num(total)}</td>`;
    }
    if(c.sum === 'avg'){
      let weightKey = null;
      if(c.k === 'preciopromediofijado') weightKey = 'cantidadfijada';
      else if(c.k === 'precioliquidado') weightKey = 'cantidadliquidada';
      if(weightKey){
        let pesos = 0, suma = 0;
        subset.forEach(r => {
          const p = Number(r[c.k]) || 0;
          const w = Number(r[weightKey]) || 0;
          if(p>0 && w>0){ pesos += w; suma += p*w; }
        });
        const avg = pesos ? suma/pesos : null;
        return `<td class="num" title="promedio ponderado por ${weightKey}">${avg==null?'—':fmt.num(avg)}</td>`;
      }
      const valid = subset.map(r => Number(r[c.k])).filter(v => !isNaN(v) && v!==0);
      const avg = valid.length ? valid.reduce((a,b)=>a+b,0)/valid.length : null;
      return `<td class="num" title="promedio">${avg==null?'—':fmt.num(avg)}</td>`;
    }
    return '<td></td>';
  }).join('');
  const totalRow = `<tr>${computeFootFin(rows, 'TOTAL', false)}</tr>`;
  const selRows = rows.filter(r => SEL_FIN.has(rowId(r)));
  const selRow  = selRows.length ? `<tr class="sel">${computeFootFin(selRows, '🟨 SELECCIONADOS', true)}</tr>` : '';
  document.getElementById('tbl-foot-fin').innerHTML = totalRow + selRow;
}

finRebuildSelects();
finRender();


/* ============================================================
   =====  CALENDARIO DE COBRANZAS (manual, localStorage) =====
   ============================================================ */

const CAL_KEY = 'tablero-granos-cobranzas-v1';
let CAL_DATA = {};   // { "MONEDA|year": { "contratoid": { "MM-DD": importe } } }
try { CAL_DATA = JSON.parse(localStorage.getItem(CAL_KEY) || '{}'); } catch(e){ CAL_DATA = {}; }

const MES_NOMBRES = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
const MES_DIAS = [31,28,31,30,31,30,31,31,30,31,30,31];   // febrero se ajusta abajo si bisiesto
let CAL_EXPANDED_MONTHS = new Set();
let CAL_EXPANDED_ORGS   = new Set();

function calStorageInfo(){
  const s = JSON.stringify(CAL_DATA);
  document.getElementById('cal-storage-info').textContent = `localStorage: ${(s.length/1024).toFixed(1)} KB`;
}
function calKey(){ return (document.getElementById('cal-moneda').value || '—') + '|' + document.getElementById('cal-year').value; }
function calGetCell(contratoid, mes, dia){
  const k = calKey();
  const cell = (CAL_DATA[k] && CAL_DATA[k][contratoid] && CAL_DATA[k][contratoid][mes+'-'+dia]) || 0;
  return cell;
}
function calSetCell(contratoid, mes, dia, val){
  const k = calKey();
  if(!CAL_DATA[k]) CAL_DATA[k] = {};
  if(!CAL_DATA[k][contratoid]) CAL_DATA[k][contratoid] = {};
  const cellKey = mes+'-'+dia;
  if(val === 0 || val === null || isNaN(val)){
    delete CAL_DATA[k][contratoid][cellKey];
    if(Object.keys(CAL_DATA[k][contratoid]).length === 0) delete CAL_DATA[k][contratoid];
  } else {
    CAL_DATA[k][contratoid][cellKey] = val;
  }
  if(Object.keys(CAL_DATA[k]).length === 0) delete CAL_DATA[k];
  localStorage.setItem(CAL_KEY, JSON.stringify(CAL_DATA));
  calStorageInfo();
}
function calIsLeap(y){ return (y%4===0 && y%100!==0) || y%400===0; }

// inicializar selects de año (de min año en data a max+2) y moneda
function calInitSelectors(){
  // años disponibles en los datos (basado en fechamaxentrega/fecha)
  const yrs = new Set();
  DATA.forEach(r => {
    [r.fecha, r.fechamaxentrega].forEach(d => {
      if(d) { const y = parseInt(d.slice(0,4),10); if(!isNaN(y)) yrs.add(y); }
    });
  });
  const cur = new Date().getFullYear();
  yrs.add(cur); yrs.add(cur+1);
  const sortedYrs = [...yrs].sort((a,b)=>b-a);
  const selY = document.getElementById('cal-year');
  selY.innerHTML = sortedYrs.map(y => `<option value="${y}" ${y===cur?'selected':''}>${y}</option>`).join('');

  const selM = document.getElementById('cal-moneda');
  const monedas = uniqSorted(DATA, 'moneda');
  selM.innerHTML = monedas.map((m,i)=>`<option value="${escapeHtml(m)}" ${i===0?'selected':''}>${escapeHtml(m)}</option>`).join('');

  selY.addEventListener('change', calRender);
  selM.addEventListener('change', calRender);

  document.getElementById('cal-export').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(CAL_DATA, null, 2)], {type:'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `cobranzas_${new Date().toISOString().slice(0,10)}.json`;
    a.click();
  });
  document.getElementById('cal-import').addEventListener('click', () => document.getElementById('cal-import-file').click());
  document.getElementById('cal-import-file').addEventListener('change', (ev) => {
    const f = ev.target.files[0];
    if(!f) return;
    const r = new FileReader();
    r.onload = (e) => {
      try {
        const obj = JSON.parse(e.target.result);
        if(confirm('¿Reemplazar todo el calendario actual por el contenido del archivo?')){
          CAL_DATA = obj;
          localStorage.setItem(CAL_KEY, JSON.stringify(CAL_DATA));
          calRender();
        }
      } catch(err){ alert('Archivo JSON inválido: '+err.message); }
    };
    r.readAsText(f);
  });
  document.getElementById('cal-clear').addEventListener('click', () => {
    if(confirm('¿Borrar TODO el calendario (todos los años, todas las monedas)?')){
      CAL_DATA = {};
      localStorage.removeItem(CAL_KEY);
      calRender();
    }
  });
}

function calRender(){
  const year = parseInt(document.getElementById('cal-year').value, 10);
  const moneda = document.getElementById('cal-moneda').value;
  const dias = MES_DIAS.slice();
  if(calIsLeap(year)) dias[1] = 29;

  // contratos a mostrar: los del filtro financiero, con esa moneda y con
  // ALGO realmente pendiente de cobrar (entregado pero no liquidado)
  const contratos = finFiltered.filter(r =>
    r.moneda === moneda &&
    Math.abs(r.importecantidadentregadapendienteliquidar || 0) > 0
  );
  // agrupar por organizacion
  const byOrg = {};
  contratos.forEach(r => {
    const o = r.organizacion || '—';
    if(!byOrg[o]) byOrg[o] = [];
    byOrg[o].push(r);
  });
  const orgs = Object.keys(byOrg).sort((a,b)=>a.localeCompare(b,'es'));

  // armar header: org + meses (con eventual expansión a días)
  let head = '<th class="cal-org">CEREALERA / CONTRATO</th>';
  const monthSlots = [];   // [{mes, expanded, span}]
  for(let m=0; m<12; m++){
    const exp = CAL_EXPANDED_MONTHS.has(m);
    monthSlots.push({mes:m, expanded:exp, span: exp ? dias[m] : 1});
    if(exp){
      head += `<th class="cal-month" data-m="${m}" colspan="${dias[m]}" style="background:#172e6b">${MES_NOMBRES[m]} ${year} ▾ (click p/cerrar)</th>`;
    } else {
      head += `<th class="cal-month" data-m="${m}">${MES_NOMBRES[m]}<br><span class="dn">click p/abrir</span></th>`;
    }
  }
  document.getElementById('cal-head').innerHTML = head;

  // segunda fila de header con los días (solo si hay meses expandidos)
  // Implementacion simple: solo una fila. Si hay meses expandidos, las celdas dia se ponen en la misma fila (no hay 2 niveles).
  // Para tener 2 niveles haria falta thead con 2 rows; lo simplifico inline.
  // Mejoramos despues si hace falta.

  // listeners headers meses
  document.querySelectorAll('#cal-head .cal-month').forEach(th => {
    th.addEventListener('click', () => {
      const m = parseInt(th.dataset.m,10);
      if(CAL_EXPANDED_MONTHS.has(m)) CAL_EXPANDED_MONTHS.delete(m);
      else CAL_EXPANDED_MONTHS.add(m);
      calRender();
    });
  });

  // armar body
  let body = '';
  let totalesMes = monthSlots.map(s => s.expanded ? Array(dias[s.mes]).fill(0) : [0]);

  orgs.forEach(o => {
    const orgExpanded = CAL_EXPANDED_ORGS.has(o);
    // calcular totales de la org (suma de cells de todos sus contratos)
    const ctos = byOrg[o];
    const orgRowVals = monthSlots.map(s => s.expanded ? Array(dias[s.mes]).fill(0) : [0]);
    ctos.forEach(c => {
      const id = String(c.contratoid || c.numerointerno || c.contrato || '?');
      monthSlots.forEach((s, mi) => {
        if(s.expanded){
          for(let d=1; d<=dias[s.mes]; d++){
            const v = calGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
            orgRowVals[mi][d-1] += v;
            totalesMes[mi][d-1] += v;
          }
        } else {
          // sum de todos los dias del mes
          let mm = 0;
          for(let d=1; d<=dias[s.mes]; d++){
            mm += calGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
          }
          orgRowVals[mi][0] += mm;
          totalesMes[mi][0] += mm;
        }
      });
    });

    // fila de la org (solo display, sin inputs editables en este nivel; los inputs van por contrato)
    let rowOrg = `<tr class="cal-org ${orgExpanded?'expanded':''}" data-org="${escapeHtml(o)}"><td class="cal-org-cell" title="click para expandir contratos">${escapeHtml(o)} <span style="font-size:10px;color:var(--muted)">(${ctos.length})</span></td>`;
    monthSlots.forEach((s, mi) => {
      orgRowVals[mi].forEach(v => {
        rowOrg += `<td class="cal-num">${v?fmt.num(v):''}</td>`;
      });
    });
    rowOrg += '</tr>';
    body += rowOrg;

    if(orgExpanded){
      ctos.forEach(c => {
        const id = String(c.contratoid || c.numerointerno || c.contrato || '?');
        const ctoLbl = `CTO ${c.numerointerno||''} · ${c.producto||''} · ${c.fecha||''}`;
        let rowC = `<tr class="cal-contrato" data-id="${escapeHtml(id)}"><td class="cal-org-cell">${escapeHtml(ctoLbl)}</td>`;
        monthSlots.forEach((s, mi) => {
          if(s.expanded){
            for(let d=1; d<=dias[s.mes]; d++){
              const mm = String(s.mes+1).padStart(2,'0');
              const dd = String(d).padStart(2,'0');
              const v = calGetCell(id, mm, dd);
              rowC += `<td class="cal-num"><input type="text" data-id="${escapeHtml(id)}" data-mm="${mm}" data-dd="${dd}" data-has-value="${v?1:0}" value="${v?fmt.num(v):''}" placeholder="—"/></td>`;
            }
          } else {
            // celda mensual: editable, representa la suma del mes (si pone valor en mes cerrado, se guarda en dia 15 por convención)
            let mm = 0;
            for(let d=1; d<=dias[s.mes]; d++) mm += calGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
            const meslabel = String(s.mes+1).padStart(2,'0');
            rowC += `<td class="cal-num"><input type="text" data-id="${escapeHtml(id)}" data-mm="${meslabel}" data-dd="15" data-monthly="1" data-has-value="${mm?1:0}" value="${mm?fmt.num(mm):''}" placeholder="—"/></td>`;
          }
        });
        rowC += '</tr>';
        body += rowC;
      });
    }
  });
  document.getElementById('cal-body').innerHTML = body;

  // listeners: org click para expandir
  document.querySelectorAll('#cal-body tr.cal-org > td.cal-org-cell').forEach(td => {
    td.addEventListener('click', () => {
      const o = td.closest('tr').dataset.org;
      if(CAL_EXPANDED_ORGS.has(o)) CAL_EXPANDED_ORGS.delete(o);
      else CAL_EXPANDED_ORGS.add(o);
      calRender();
    });
  });
  // listeners: inputs
  document.querySelectorAll('#cal-body input').forEach(inp => {
    inp.addEventListener('blur', () => {
      const raw = inp.value.replace(/\./g,'').replace(',','.').replace(/[^0-9.\-]/g,'');
      const v = parseFloat(raw);
      const id = inp.dataset.id, mm = inp.dataset.mm, dd = inp.dataset.dd;
      // Si es input monthly: limpiar todos los días del mes primero
      if(inp.dataset.monthly === '1'){
        const k = calKey();
        if(CAL_DATA[k] && CAL_DATA[k][id]){
          Object.keys(CAL_DATA[k][id]).forEach(ck => {
            if(ck.startsWith(mm+'-') && ck !== mm+'-15'){
              delete CAL_DATA[k][id][ck];
            }
          });
        }
      }
      calSetCell(id, mm, dd, isNaN(v) ? 0 : v);
      // recalcular sin re-render completo: solo updates de totales
      inp.value = (isNaN(v) || v===0) ? '' : fmt.num(v);
      inp.setAttribute('data-has-value', (!isNaN(v) && v!==0) ? '1' : '0');
      // total bottom: recompute simple
      calRecalcTotals();
    });
    inp.addEventListener('keydown', (e) => { if(e.key === 'Enter') inp.blur(); });
  });

  // footer con totales
  calRecalcTotals();

  document.getElementById('cal-meta').textContent =
    `${orgs.length} cerealeras · ${contratos.length} contratos · moneda ${moneda}`;
}

function calRecalcTotals(){
  const year = parseInt(document.getElementById('cal-year').value, 10);
  const moneda = document.getElementById('cal-moneda').value;
  const dias = MES_DIAS.slice();
  if(calIsLeap(year)) dias[1] = 29;
  const monthSlots = [];
  for(let m=0; m<12; m++){
    monthSlots.push({mes:m, expanded: CAL_EXPANDED_MONTHS.has(m), span: CAL_EXPANDED_MONTHS.has(m) ? dias[m] : 1});
  }
  const contratos = finFiltered.filter(r =>
    r.moneda === moneda &&
    Math.abs(r.importecantidadentregadapendienteliquidar || 0) > 0
  );
  const tot = monthSlots.map(s => s.expanded ? Array(dias[s.mes]).fill(0) : [0]);
  contratos.forEach(c => {
    const id = String(c.contratoid || c.numerointerno || c.contrato || '?');
    monthSlots.forEach((s, mi) => {
      if(s.expanded){
        for(let d=1; d<=dias[s.mes]; d++){
          tot[mi][d-1] += calGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
        }
      } else {
        for(let d=1; d<=dias[s.mes]; d++){
          tot[mi][0] += calGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
        }
      }
    });
  });
  let foot = '<td class="cal-org-cell">TOTAL MES</td>';
  monthSlots.forEach((s, mi) => {
    tot[mi].forEach(v => {
      foot += `<td class="cal-num">${v?fmt.num(v):'—'}</td>`;
    });
  });
  document.getElementById('cal-foot').innerHTML = foot;
  calStorageInfo();
}

calInitSelectors();
calRender();


/* ============================================================
   ===============  MODULO COMPRA  ===========================
   ============================================================ */

const DATA_CP = PAYLOAD.compra || [];

/* ---------- COMPRA: POSICIÓN GENERAL ---------- */

const CP_FILTERS = [
  {id:'cp-empresa', col:'empresa',      placeholder:'Todas'},
  {id:'cp-org',     col:'organizacion', placeholder:'Todos'},
  {id:'cp-prod',    col:'producto',     placeholder:'Todos'},
  {id:'cp-tcont',   col:'tipocontrato', placeholder:'Todos'},
  {id:'cp-camp',    col:'campana',      placeholder:'Todas'},
];
const CP_ALL_VALS = {};
CP_FILTERS.forEach(f => { CP_ALL_VALS[f.col] = uniqSorted(DATA_CP, f.col); });

let cpFiltered = DATA_CP.slice();
let cpSortKey = null, cpSortDir = 1;
const SEL_CP = new Set();
let lastClickIdxCp = null;

const CP_TABLE_COLS = [
  {k:'fecha',                       lbl:'Fecha',           num:false},
  {k:'numerointerno',               lbl:'Nº',              num:false},
  {k:'organizacion',                lbl:'Proveedor',       num:false},
  {k:'producto',                    lbl:'Producto',        num:false},
  {k:'tipocontrato',                lbl:'Tipo',            num:false},
  {k:'cantidadmax',                 lbl:'Tn Ajustadas',    num:true, sum:true},
  {k:'cantidadentregada',           lbl:'Tn Recibidas',    num:true, sum:true},
  {k:'_cpPdteRecibir',              lbl:'Tn Pdte Recibir', num:true, sum:true},
  {k:'cantidadcertificadaneta',     lbl:'Tn Certif.',      num:true, sum:true},
  {k:'cantidadpendientecertificar', lbl:'Tn Pdte Cert.',   num:true, sum:true},
  {k:'fechaminentrega',             lbl:'Entrega Desde',   num:false},
  {k:'fechamaxentrega',             lbl:'Entrega Hasta',   num:false},
  {k:'campana',                     lbl:'Campaña',         num:false},
  {k:'corredor',                    lbl:'Corredor',        num:false},
  {k:'_cpEstado',                   lbl:'Estado',          num:false, html:true},
];

function cpGetVal(r, k){
  if(k==='_cpEstado') return r.estadoanulacion||'';
  if(k==='_cpPdteRecibir') return (r.cantidadmax||0) - (r.cantidadentregada||0);
  return r[k];
}
function cpEstadoChip(r){
  const e = (r.estadoanulacion||'').toLowerCase();
  if(e.includes('anul') && !e.includes('no anul')) return '<span class="chip err">Anulado</span>';
  const aj = r.cantidadmax||0, rec = r.cantidadentregada||0;
  if(aj<=0) return '<span class="chip neutral">—</span>';
  const p = rec/aj;
  if(p >= 0.999) return '<span class="chip ok">Recibido</span>';
  if(p > 0)      return '<span class="chip warn">Parcial</span>';
  return '<span class="chip info">Pendiente</span>';
}
function cpRowPassesText(r, fQ){
  if(!fQ) return true;
  const hay = (r.numerointerno||'')+' '+(r.descripcion||'')+' '+(r.numerodocumentoadicional||'')+' '+(r.contrato||'');
  return hay.toLowerCase().includes(fQ);
}
function cpRowPassesExcept(r, skipCol){
  for(const f of CP_FILTERS){
    if(f.col === skipCol) continue;
    const v = document.getElementById(f.id).value;
    if(v && r[f.col] !== v) return false;
  }
  return cpRowPassesText(r, document.getElementById('cp-q').value.trim().toLowerCase());
}
function cpRebuildSelects(){
  CP_FILTERS.forEach(f => {
    const sel = document.getElementById(f.id);
    const current = sel.value;
    const counts = {};
    DATA_CP.forEach(r => {
      if(cpRowPassesExcept(r, f.col)){
        const k = r[f.col];
        if(k != null && k !== '') counts[k] = (counts[k]||0) + 1;
      }
    });
    const items = CP_ALL_VALS[f.col].map(v => ({v, n: counts[v]||0}));
    items.sort((a,b)=>{ if((a.n>0)!==(b.n>0)) return a.n>0?-1:1; return String(a.v).localeCompare(String(b.v),'es'); });
    sel.innerHTML = `<option value="">${f.placeholder}</option>` +
      items.map(it=>`<option value="${escapeHtml(it.v)}" class="${it.n===0?'opt-zero':''}">${escapeHtml(it.v)} (${it.n.toLocaleString('es-AR')})</option>`).join('');
    if(current) sel.value = current;
  });
}
CP_FILTERS.forEach(f => document.getElementById(f.id).addEventListener('change', cpApply));
document.getElementById('cp-q').addEventListener('input', cpApply);
document.getElementById('btn-clear-cp').addEventListener('click', () => {
  CP_FILTERS.forEach(f => document.getElementById(f.id).value='');
  document.getElementById('cp-q').value='';
  cpApply();
});

function cpApply(){
  cpRebuildSelects();
  const vals={};
  CP_FILTERS.forEach(f => { const v = document.getElementById(f.id).value; if(v) vals[f.col]=v; });
  const fQ = document.getElementById('cp-q').value.trim().toLowerCase();
  cpFiltered = DATA_CP.filter(r => {
    for(const [col,v] of Object.entries(vals)){ if(r[col]!==v) return false; }
    return cpRowPassesText(r, fQ);
  });
  cpRender();
}

let cpChartTop=null, cpChartDonut=null;

function cpRender(){
  let cnt=cpFiltered.length, tnAj=0, tnRec=0;
  cpFiltered.forEach(r => {
    tnAj  += r.cantidadmax || 0;
    tnRec += r.cantidadentregada || 0;
  });
  const tnPdt = tnAj - tnRec;
  const cumplimiento = tnAj>0 ? tnRec/tnAj : null;
  document.getElementById('kpi-row-cp').innerHTML = `
    <div class="kpi"><div class="lbl">Contratos</div><div class="val">${fmt.int(cnt)}</div><div class="hint">de ${fmt.int(DATA_CP.length)} totales</div></div>
    <div class="kpi"><div class="lbl">Toneladas Ajustadas</div><div class="val">${fmt.num(tnAj)}</div><div class="hint">Cantidad final post-ajustes</div></div>
    <div class="kpi green"><div class="lbl">Toneladas Recibidas</div><div class="val">${fmt.num(tnRec)}</div><div class="hint">Cumplimiento: ${fmt.pct(cumplimiento)}</div></div>
    <div class="kpi orange"><div class="lbl">Tn Pendientes de Recibir</div><div class="val">${fmt.num(tnPdt)}</div><div class="hint">= Ajustadas − Recibidas</div></div>
  `;

  const byG = {};
  cpFiltered.forEach(r => {
    const p = r.producto || '—';
    if(!byG[p]) byG[p] = {cnt:0,tnAj:0,tnRec:0};
    byG[p].cnt++;
    byG[p].tnAj  += r.cantidadmax || 0;
    byG[p].tnRec += r.cantidadentregada || 0;
  });
  const gOrder = Object.entries(byG).sort((a,b)=>b[1].tnAj - a[1].tnAj);
  document.getElementById('grain-meta-cp').textContent = `${gOrder.length} productos`;
  document.getElementById('grain-grid-cp').innerHTML = gOrder.map(([g,v]) => {
    const pct = v.tnAj>0 ? v.tnRec/v.tnAj : 0;
    const pdt = v.tnAj - v.tnRec;
    return `<div class="grain-card ${grainClass(g)}">
      <div class="name"><span>${g}</span><span class="cnt">${v.cnt} contratos</span></div>
      <div class="row"><span class="k">Tn Ajustadas</span><span><b>${fmt.num(v.tnAj)}</b></span></div>
      <div class="row"><span class="k">Tn Recibidas</span><span>${fmt.num(v.tnRec)} <span style="color:var(--muted)">(${fmt.pct(pct)})</span></span></div>
      <div class="row"><span class="k">Tn Pdte Recibir</span><span style="color:var(--orange)"><b>${fmt.num(pdt)}</b></span></div>
      <div class="bar"><div style="width:${Math.min(100,pct*100)}%"></div></div>
    </div>`;
  }).join('') || '<div class="placeholder">Sin datos para los filtros aplicados</div>';

  // Top 10 proveedores
  const byOrg = {};
  cpFiltered.forEach(r => { byOrg[r.organizacion||'—'] = (byOrg[r.organizacion||'—']||0)+(r.cantidadmax||0); });
  const topOrg = Object.entries(byOrg).sort((a,b)=>b[1]-a[1]).slice(0,10);
  if(cpChartTop) cpChartTop.destroy();
  cpChartTop = new Chart(document.getElementById('chart-top-cp'), {
    type:'bar',
    data:{labels: topOrg.map(x=>x[0].length>30?x[0].slice(0,30)+'…':x[0]), datasets:[{label:'Tn Ajustadas', data: topOrg.map(x=>x[1]), backgroundColor:'#0891b2', borderRadius:4}]},
    options:{indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>fmt.num(c.parsed.x)+' tn'}}}, scales:{x:{ticks:{callback:v=>v.toLocaleString('es-AR')}}}}
  });

  if(cpChartDonut) cpChartDonut.destroy();
  const palette = ['#16a34a','#f59e0b','#a16207','#3b82f6','#dc2626','#6366f1','#0891b2','#94a3b8','#ec4899','#84cc16'];
  cpChartDonut = new Chart(document.getElementById('chart-donut-cp'), {
    type:'doughnut',
    data:{labels: gOrder.map(x=>x[0]), datasets:[{data: gOrder.map(x=>x[1].tnAj), backgroundColor: palette}]},
    options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{position:'bottom',labels:{boxWidth:10,padding:8,font:{size:11}}}, tooltip:{callbacks:{label:c=>c.label+': '+fmt.num(c.parsed)+' tn'}}}}
  });

  // Tabla
  const head = CP_TABLE_COLS.map(c => {
    const ar = (cpSortKey===c.k) ? (cpSortDir>0?'▲':'▼') : '';
    const cls = (cpSortKey===c.k) ? (cpSortDir>0?'sort-asc':'sort-desc') : '';
    return `<th class="${cls}" data-k="${c.k}" data-num="${c.num?1:0}">${c.lbl}<span class="arrow">${ar||'⇅'}</span></th>`;
  }).join('');
  document.getElementById('tbl-head-cp').innerHTML = head;
  document.querySelectorAll('#tbl-head-cp th').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.k;
      if(cpSortKey === k) cpSortDir = -cpSortDir;
      else { cpSortKey = k; cpSortDir = 1; }
      cpRender();
    });
  });

  let rows = cpFiltered;
  if(cpSortKey){
    const col = CP_TABLE_COLS.find(c=>c.k===cpSortKey);
    rows = cpFiltered.slice().sort((a,b)=>{
      let va = cpGetVal(a, cpSortKey);
      let vb = cpGetVal(b, cpSortKey);
      if(col && col.num){ va=va||0; vb=vb||0; return (va-vb)*cpSortDir; }
      va=(va==null?'':String(va)); vb=(vb==null?'':String(vb));
      return va.localeCompare(vb,'es',{numeric:true})*cpSortDir;
    });
  }
  const MAX = 1000;
  const visibleRows = rows.slice(0,MAX);
  document.getElementById('tbl-body-cp').innerHTML = visibleRows.map((r,i) => {
    const id = rowId(r);
    const selCls = SEL_CP.has(id) ? ' class="row-sel"' : '';
    return `<tr data-id="${id}" data-i="${i}"${selCls}>`+CP_TABLE_COLS.map(c=>{
      if(c.k==='_cpEstado') return '<td>'+cpEstadoChip(r)+'</td>';
      const v = cpGetVal(r, c.k);
      if(c.num) return `<td class="num">${v==null?'<span class=muted>—</span>':fmt.num(v)}</td>`;
      return `<td>${v==null?'<span class=muted>—</span>':String(v)}</td>`;
    }).join('')+'</tr>';
  }).join('') || '<tr><td colspan="99" style="padding:30px;text-align:center;color:var(--muted)">Sin resultados</td></tr>';

  document.querySelectorAll('#tbl-body-cp tr[data-id]').forEach(tr => {
    tr.addEventListener('click', (ev) => {
      const id = tr.dataset.id, i = parseInt(tr.dataset.i,10);
      if(ev.shiftKey && lastClickIdxCp !== null){
        const a = Math.min(i, lastClickIdxCp), b = Math.max(i, lastClickIdxCp);
        for(let k=a; k<=b; k++) SEL_CP.add(rowId(visibleRows[k]));
      } else {
        if(SEL_CP.has(id)) SEL_CP.delete(id);
        else SEL_CP.add(id);
        lastClickIdxCp = i;
      }
      renderFootCp(rows);
      document.querySelectorAll('#tbl-body-cp tr[data-id]').forEach(t2 => {
        t2.classList.toggle('row-sel', SEL_CP.has(t2.dataset.id));
      });
    });
  });

  document.getElementById('row-count-cp').textContent =
    `${rows.length.toLocaleString('es-AR')} / ${DATA_CP.length.toLocaleString('es-AR')} contratos` +
    (rows.length>MAX ? ` (mostrando ${MAX})` : '');

  renderFootCp(rows);
}

function renderFootCp(rows){
  const computeFoot = (subset, label, isSel) => CP_TABLE_COLS.map((c, idx) => {
    if(idx === 0){
      const btn = isSel ? ` <button class="clear-sel" onclick="SEL_CP.clear(); cpRender();">Limpiar selección</button>` : '';
      return `<td class="lbl">${label} (${subset.length.toLocaleString('es-AR')})${btn}</td>`;
    }
    if(c.sum === true){
      const total = subset.reduce((acc,r) => acc + (Number(cpGetVal(r,c.k)) || 0), 0);
      return `<td class="num">${fmt.num(total)}</td>`;
    }
    return '<td></td>';
  }).join('');
  const totalRow = `<tr>${computeFoot(rows, 'TOTAL', false)}</tr>`;
  const selRows = rows.filter(r => SEL_CP.has(rowId(r)));
  const selRow  = selRows.length ? `<tr class="sel">${computeFoot(selRows, '🟨 SELECCIONADOS', true)}</tr>` : '';
  document.getElementById('tbl-foot-cp').innerHTML = totalRow + selRow;
}

cpRebuildSelects();
cpRender();


/* ---------- COMPRA: FINANCIERA ---------- */

const CPF_FILTERS = [
  {id:'cpf-empresa', col:'empresa',      placeholder:'Todas'},
  {id:'cpf-org',     col:'organizacion', placeholder:'Todos'},
  {id:'cpf-prod',    col:'producto',     placeholder:'Todos'},
  {id:'cpf-tcont',   col:'tipocontrato', placeholder:'Todos'},
  {id:'cpf-moneda',  col:'moneda',       placeholder:'Todas'},
  {id:'cpf-camp',    col:'campana',      placeholder:'Todas'},
];
const CPF_ALL_VALS = {};
CPF_FILTERS.forEach(f => { CPF_ALL_VALS[f.col] = uniqSorted(DATA_CP, f.col); });
let cpfFiltered = DATA_CP.slice();
let cpfSortKey = null, cpfSortDir = 1;
const SEL_CPF = new Set();
let lastClickIdxCpf = null;

const CPF_TABLE_COLS = [
  {k:'fecha',                       lbl:'Fecha',          num:false},
  {k:'numerointerno',               lbl:'Nº',             num:false},
  {k:'organizacion',                lbl:'Proveedor',      num:false},
  {k:'producto',                    lbl:'Producto',       num:false},
  {k:'tipocontrato',                lbl:'Tipo',           num:false},
  {k:'moneda',                      lbl:'Mon.',           num:false},
  {k:'cantidadfijada',              lbl:'Tn Fijadas',     num:true, sum:true},
  {k:'preciopromediofijado',        lbl:'Precio Fij.',    num:true, sum:'avg'},
  {k:'importefijado',               lbl:'Imp. Fijado',    num:true, sum:true},
  {k:'cantidadliquidada',           lbl:'Tn Liquid.',     num:true, sum:true},
  {k:'precioliquidado',             lbl:'Precio Liq.',    num:true, sum:'avg'},
  {k:'importeliquidado',            lbl:'Imp. Liquid.',   num:true, sum:true},
  {k:'cantidadpendienteliquidar',          lbl:'Tn Pdte Liq.',   num:true, sum:true},
  {k:'importependienteliquidar',           lbl:'Imp. Pdte Liq.', num:true, sum:true},
  {k:'campana',                     lbl:'Campaña',        num:false},
];

function cpfRowPassesText(r, fQ){
  if(!fQ) return true;
  const hay = (r.numerointerno||'')+' '+(r.descripcion||'')+' '+(r.numerodocumentoadicional||'')+' '+(r.contrato||'');
  return hay.toLowerCase().includes(fQ);
}
function cpfRowPassesExcept(r, skipCol){
  for(const f of CPF_FILTERS){
    if(f.col === skipCol) continue;
    const v = document.getElementById(f.id).value;
    if(v && r[f.col] !== v) return false;
  }
  return cpfRowPassesText(r, document.getElementById('cpf-q').value.trim().toLowerCase());
}
function cpfRebuildSelects(){
  CPF_FILTERS.forEach(f => {
    const sel = document.getElementById(f.id);
    const current = sel.value;
    const counts = {};
    DATA_CP.forEach(r => {
      if(cpfRowPassesExcept(r, f.col)){
        const k = r[f.col];
        if(k != null && k !== '') counts[k] = (counts[k]||0) + 1;
      }
    });
    const items = CPF_ALL_VALS[f.col].map(v => ({v, n: counts[v]||0}));
    items.sort((a,b)=>{ if((a.n>0)!==(b.n>0)) return a.n>0?-1:1; return String(a.v).localeCompare(String(b.v),'es'); });
    sel.innerHTML = `<option value="">${f.placeholder}</option>` +
      items.map(it=>`<option value="${escapeHtml(it.v)}" class="${it.n===0?'opt-zero':''}">${escapeHtml(it.v)} (${it.n.toLocaleString('es-AR')})</option>`).join('');
    if(current) sel.value = current;
  });
}
CPF_FILTERS.forEach(f => document.getElementById(f.id).addEventListener('change', cpfApply));
document.getElementById('cpf-q').addEventListener('input', cpfApply);
document.getElementById('btn-clear-cpfin').addEventListener('click', () => {
  CPF_FILTERS.forEach(f => document.getElementById(f.id).value='');
  document.getElementById('cpf-q').value='';
  cpfApply();
});

function cpfApply(){
  cpfRebuildSelects();
  const vals={};
  CPF_FILTERS.forEach(f => { const v = document.getElementById(f.id).value; if(v) vals[f.col]=v; });
  const fQ = document.getElementById('cpf-q').value.trim().toLowerCase();
  cpfFiltered = DATA_CP.filter(r => {
    for(const [col,v] of Object.entries(vals)){ if(r[col]!==v) return false; }
    return cpfRowPassesText(r, fQ);
  });
  cpfRender();
  calCpRender();
}

let cpfChartTop=null, cpfChartMon=null;

function cpfRender(){
  let cnt=cpfFiltered.length, tnFij=0, tnLiq=0, tnRec=0, tnPdtLiq=0;
  const impByMon = {};
  cpfFiltered.forEach(r => {
    tnFij    += r.cantidadfijada || 0;
    tnLiq    += r.cantidadliquidada || 0;
    tnRec    += r.cantidadentregada || 0;
    tnPdtLiq += r.cantidadpendienteliquidar || 0;
    const m = r.moneda || '—';
    if(!impByMon[m]) impByMon[m] = {fij:0, liq:0, pdt:0};
    impByMon[m].fij += r.importefijado || 0;
    impByMon[m].liq += r.importeliquidado || 0;
    impByMon[m].pdt += r.importependienteliquidar || 0;
  });
  const cumplLiq = tnFij>0 ? tnLiq/tnFij : null;
  const monedaTop = Object.entries(impByMon).sort((a,b)=>Math.abs(b[1].pdt) - Math.abs(a[1].pdt))[0];
  document.getElementById('kpi-row-cpfin').innerHTML = `
    <div class="kpi"><div class="lbl">Contratos</div><div class="val">${fmt.int(cnt)}</div><div class="hint">de ${fmt.int(DATA_CP.length)} totales</div></div>
    <div class="kpi"><div class="lbl">Tn Fijadas / Recibidas</div><div class="val">${fmt.num(tnFij)} / ${fmt.num(tnRec)}</div><div class="hint">Liquidadas: ${fmt.num(tnLiq)} (${fmt.pct(cumplLiq)} de fijadas)</div></div>
    <div class="kpi orange"><div class="lbl">Tn Pdte Liquidar</div><div class="val">${fmt.num(tnPdtLiq)}</div><div class="hint">campo CANTIDADPENDIENTELIQUIDAR (calculado por Finnegans)</div></div>
    <div class="kpi red"><div class="lbl">Imp. Pdte Pagar · ${monedaTop?monedaTop[0]:'—'}</div><div class="val">${fmt.num(monedaTop?monedaTop[1].pdt:null)}</div><div class="hint">${Object.entries(impByMon).map(([m,v])=>m+': '+fmt.num(v.pdt)).join(' · ')}</div></div>
  `;

  // resumen por producto financiero
  const byG = {};
  cpfFiltered.forEach(r => {
    const p = r.producto || '—';
    if(!byG[p]) byG[p] = {cnt:0,tnFij:0,tnLiq:0,impPdt:{}};
    byG[p].cnt++;
    byG[p].tnFij += r.cantidadfijada || 0;
    byG[p].tnLiq += r.cantidadliquidada || 0;
    const m = r.moneda||'—';
    byG[p].impPdt[m] = (byG[p].impPdt[m]||0) + (r.importependienteliquidar || 0);
  });
  const gOrder = Object.entries(byG).sort((a,b)=>b[1].tnFij - a[1].tnFij);
  document.getElementById('grain-meta-cpfin').textContent = `${gOrder.length} productos`;
  document.getElementById('grain-grid-cpfin').innerHTML = gOrder.map(([g,v]) => {
    const pct = v.tnFij>0 ? v.tnLiq/v.tnFij : 0;
    const monTop = Object.entries(v.impPdt).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1]))[0];
    return `<div class="grain-card ${grainClass(g)}">
      <div class="name"><span>${g}</span><span class="cnt">${v.cnt} contratos</span></div>
      <div class="row"><span class="k">Tn Fijadas</span><span><b>${fmt.num(v.tnFij)}</b></span></div>
      <div class="row"><span class="k">Tn Liquidadas</span><span>${fmt.num(v.tnLiq)} <span style="color:var(--muted)">(${fmt.pct(pct)})</span></span></div>
      <div class="row"><span class="k">Pdte Pagar ${monTop?monTop[0]:''}</span><span style="color:var(--red)"><b>${fmt.num(monTop?monTop[1]:0)}</b></span></div>
      <div class="bar"><div style="width:${Math.min(100,pct*100)}%"></div></div>
    </div>`;
  }).join('') || '<div class="placeholder">Sin datos para los filtros aplicados</div>';

  // chart top proveedores por Imp Pdte Pagar
  const byOrgPdt = {};
  cpfFiltered.forEach(r => {
    if(monedaTop && r.moneda !== monedaTop[0]) return;
    byOrgPdt[r.organizacion||'—'] = (byOrgPdt[r.organizacion||'—']||0) + (r.importependienteliquidar||0);
  });
  const top = Object.entries(byOrgPdt).filter(x=>Math.abs(x[1])>0).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,10);
  if(cpfChartTop) cpfChartTop.destroy();
  cpfChartTop = new Chart(document.getElementById('chart-top-cpfin'), {
    type:'bar',
    data:{labels: top.map(x=>x[0].length>30?x[0].slice(0,30)+'…':x[0]), datasets:[{label:`Imp. Pdte Pagar (${monedaTop?monedaTop[0]:''})`, data: top.map(x=>x[1]), backgroundColor:'#dc2626', borderRadius:4}]},
    options:{indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:true,position:'bottom'}, tooltip:{callbacks:{label:c=>fmt.num(c.parsed.x)}}}, scales:{x:{ticks:{callback:v=>v.toLocaleString('es-AR')}}}}
  });

  if(cpfChartMon) cpfChartMon.destroy();
  const monedas = Object.keys(impByMon);
  cpfChartMon = new Chart(document.getElementById('chart-mon-cpfin'), {
    type:'bar',
    data:{
      labels: monedas,
      datasets:[
        {label:'Fijado',    data: monedas.map(m=>impByMon[m].fij), backgroundColor:'#3b82f6'},
        {label:'Liquidado', data: monedas.map(m=>impByMon[m].liq), backgroundColor:'#16a34a'},
        {label:'Pdte Pagar', data: monedas.map(m=>impByMon[m].pdt), backgroundColor:'#dc2626'},
      ]
    },
    options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{position:'bottom'}, tooltip:{callbacks:{label:c=>c.dataset.label+': '+fmt.num(c.parsed.y)}}}, scales:{y:{ticks:{callback:v=>v.toLocaleString('es-AR')}}}}
  });

  // Tabla
  const head = CPF_TABLE_COLS.map(c => {
    const ar = (cpfSortKey===c.k) ? (cpfSortDir>0?'▲':'▼') : '';
    const cls = (cpfSortKey===c.k) ? (cpfSortDir>0?'sort-asc':'sort-desc') : '';
    return `<th class="${cls}" data-k="${c.k}" data-num="${c.num?1:0}">${c.lbl}<span class="arrow">${ar||'⇅'}</span></th>`;
  }).join('');
  document.getElementById('tbl-head-cpfin').innerHTML = head;
  document.querySelectorAll('#tbl-head-cpfin th').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.k;
      if(cpfSortKey === k) cpfSortDir = -cpfSortDir;
      else { cpfSortKey = k; cpfSortDir = 1; }
      cpfRender();
    });
  });
  let rows = cpfFiltered;
  if(cpfSortKey){
    const col = CPF_TABLE_COLS.find(c=>c.k===cpfSortKey);
    rows = cpfFiltered.slice().sort((a,b)=>{
      let va = a[cpfSortKey], vb = b[cpfSortKey];
      if(col && col.num){ va=va||0; vb=vb||0; return (va-vb)*cpfSortDir; }
      va=(va==null?'':String(va)); vb=(vb==null?'':String(vb));
      return va.localeCompare(vb,'es',{numeric:true})*cpfSortDir;
    });
  }
  const MAX = 1000;
  const visibleCpf = rows.slice(0,MAX);
  document.getElementById('tbl-body-cpfin').innerHTML = visibleCpf.map((r,i) => {
    const id = rowId(r);
    const selCls = SEL_CPF.has(id) ? ' class="row-sel"' : '';
    return `<tr data-id="${id}" data-i="${i}"${selCls}>`+CPF_TABLE_COLS.map(c=>{
      const v=r[c.k];
      if(c.num) return `<td class="num">${v==null?'<span class=muted>—</span>':fmt.num(v)}</td>`;
      return `<td>${v==null?'<span class=muted>—</span>':String(v)}</td>`;
    }).join('')+'</tr>';
  }).join('') || '<tr><td colspan="99" style="padding:30px;text-align:center;color:var(--muted)">Sin resultados</td></tr>';

  document.querySelectorAll('#tbl-body-cpfin tr[data-id]').forEach(tr => {
    tr.addEventListener('click', (ev) => {
      const id = tr.dataset.id, i = parseInt(tr.dataset.i,10);
      if(ev.shiftKey && lastClickIdxCpf !== null){
        const a = Math.min(i, lastClickIdxCpf), b = Math.max(i, lastClickIdxCpf);
        for(let k=a; k<=b; k++) SEL_CPF.add(rowId(visibleCpf[k]));
      } else {
        if(SEL_CPF.has(id)) SEL_CPF.delete(id);
        else SEL_CPF.add(id);
        lastClickIdxCpf = i;
      }
      renderFootCpf(rows);
      document.querySelectorAll('#tbl-body-cpfin tr[data-id]').forEach(t2 => {
        t2.classList.toggle('row-sel', SEL_CPF.has(t2.dataset.id));
      });
    });
  });

  document.getElementById('row-count-cpfin').textContent =
    `${rows.length.toLocaleString('es-AR')} / ${DATA_CP.length.toLocaleString('es-AR')} contratos` +
    (rows.length>MAX ? ` (mostrando ${MAX})` : '');

  renderFootCpf(rows);
}

function renderFootCpf(rows){
  const computeFoot = (subset, label, isSel) => CPF_TABLE_COLS.map((c, idx) => {
    if(idx === 0){
      const btn = isSel ? ` <button class="clear-sel" onclick="SEL_CPF.clear(); cpfRender();">Limpiar selección</button>` : '';
      return `<td class="lbl">${label} (${subset.length.toLocaleString('es-AR')})${btn}</td>`;
    }
    if(c.sum === true){
      const total = subset.reduce((acc,r) => acc + (Number(r[c.k]) || 0), 0);
      return `<td class="num">${fmt.num(total)}</td>`;
    }
    if(c.sum === 'avg'){
      let weightKey = null;
      if(c.k === 'preciopromediofijado') weightKey = 'cantidadfijada';
      else if(c.k === 'precioliquidado') weightKey = 'cantidadliquidada';
      if(weightKey){
        let pesos = 0, suma = 0;
        subset.forEach(r => {
          const p = Number(r[c.k]) || 0;
          const w = Number(r[weightKey]) || 0;
          if(p>0 && w>0){ pesos += w; suma += p*w; }
        });
        const avg = pesos ? suma/pesos : null;
        return `<td class="num" title="promedio ponderado por ${weightKey}">${avg==null?'—':fmt.num(avg)}</td>`;
      }
      const valid = subset.map(r => Number(r[c.k])).filter(v => !isNaN(v) && v!==0);
      const avg = valid.length ? valid.reduce((a,b)=>a+b,0)/valid.length : null;
      return `<td class="num" title="promedio">${avg==null?'—':fmt.num(avg)}</td>`;
    }
    return '<td></td>';
  }).join('');
  const totalRow = `<tr>${computeFoot(rows, 'TOTAL', false)}</tr>`;
  const selRows = rows.filter(r => SEL_CPF.has(rowId(r)));
  const selRow  = selRows.length ? `<tr class="sel">${computeFoot(selRows, '🟨 SELECCIONADOS', true)}</tr>` : '';
  document.getElementById('tbl-foot-cpfin').innerHTML = totalRow + selRow;
}

cpfRebuildSelects();
cpfRender();


/* ---------- COMPRA: CALENDARIO DE PAGOS ---------- */

const CAL_CP_KEY = 'tablero-granos-pagos-v1';
let CAL_CP_DATA = {};
try { CAL_CP_DATA = JSON.parse(localStorage.getItem(CAL_CP_KEY) || '{}'); } catch(e){ CAL_CP_DATA = {}; }
let CAL_CP_EXPANDED_MONTHS = new Set();
let CAL_CP_EXPANDED_ORGS   = new Set();

function calCpStorageInfo(){
  const s = JSON.stringify(CAL_CP_DATA);
  document.getElementById('cal-cp-storage-info').textContent = `localStorage: ${(s.length/1024).toFixed(1)} KB`;
}
function calCpKey(){ return (document.getElementById('cal-cp-moneda').value || '—') + '|' + document.getElementById('cal-cp-year').value; }
function calCpGetCell(contratoid, mes, dia){
  const k = calCpKey();
  return (CAL_CP_DATA[k] && CAL_CP_DATA[k][contratoid] && CAL_CP_DATA[k][contratoid][mes+'-'+dia]) || 0;
}
function calCpSetCell(contratoid, mes, dia, val){
  const k = calCpKey();
  if(!CAL_CP_DATA[k]) CAL_CP_DATA[k] = {};
  if(!CAL_CP_DATA[k][contratoid]) CAL_CP_DATA[k][contratoid] = {};
  const cellKey = mes+'-'+dia;
  if(val === 0 || val === null || isNaN(val)){
    delete CAL_CP_DATA[k][contratoid][cellKey];
    if(Object.keys(CAL_CP_DATA[k][contratoid]).length === 0) delete CAL_CP_DATA[k][contratoid];
  } else {
    CAL_CP_DATA[k][contratoid][cellKey] = val;
  }
  if(Object.keys(CAL_CP_DATA[k]).length === 0) delete CAL_CP_DATA[k];
  localStorage.setItem(CAL_CP_KEY, JSON.stringify(CAL_CP_DATA));
  calCpStorageInfo();
}

function calCpInitSelectors(){
  const yrs = new Set();
  DATA_CP.forEach(r => {
    [r.fecha, r.fechamaxentrega].forEach(d => {
      if(d){ const y = parseInt(d.slice(0,4),10); if(!isNaN(y)) yrs.add(y); }
    });
  });
  const cur = new Date().getFullYear();
  yrs.add(cur); yrs.add(cur+1);
  const sortedYrs = [...yrs].sort((a,b)=>b-a);
  const selY = document.getElementById('cal-cp-year');
  selY.innerHTML = sortedYrs.map(y => `<option value="${y}" ${y===cur?'selected':''}>${y}</option>`).join('');

  const selM = document.getElementById('cal-cp-moneda');
  const monedas = uniqSorted(DATA_CP, 'moneda');
  selM.innerHTML = monedas.map((m,i)=>`<option value="${escapeHtml(m)}" ${i===0?'selected':''}>${escapeHtml(m)}</option>`).join('');

  selY.addEventListener('change', calCpRender);
  selM.addEventListener('change', calCpRender);

  document.getElementById('cal-cp-export').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(CAL_CP_DATA, null, 2)], {type:'application/json'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `pagos_${new Date().toISOString().slice(0,10)}.json`;
    a.click();
  });
  document.getElementById('cal-cp-import').addEventListener('click', () => document.getElementById('cal-cp-import-file').click());
  document.getElementById('cal-cp-import-file').addEventListener('change', (ev) => {
    const f = ev.target.files[0];
    if(!f) return;
    const r = new FileReader();
    r.onload = (e) => {
      try {
        const obj = JSON.parse(e.target.result);
        if(confirm('¿Reemplazar todo el calendario de pagos por el contenido del archivo?')){
          CAL_CP_DATA = obj;
          localStorage.setItem(CAL_CP_KEY, JSON.stringify(CAL_CP_DATA));
          calCpRender();
        }
      } catch(err){ alert('Archivo JSON inválido: '+err.message); }
    };
    r.readAsText(f);
  });
  document.getElementById('cal-cp-clear').addEventListener('click', () => {
    if(confirm('¿Borrar TODO el calendario de pagos?')){
      CAL_CP_DATA = {};
      localStorage.removeItem(CAL_CP_KEY);
      calCpRender();
    }
  });
}

function calCpRender(){
  const year = parseInt(document.getElementById('cal-cp-year').value, 10);
  const moneda = document.getElementById('cal-cp-moneda').value;
  const dias = MES_DIAS.slice();
  if(calIsLeap(year)) dias[1] = 29;

  const contratos = cpfFiltered.filter(r =>
    r.moneda === moneda &&
    Math.abs(r.importependienteliquidar || 0) > 0
  );
  const byOrg = {};
  contratos.forEach(r => {
    const o = r.organizacion || '—';
    if(!byOrg[o]) byOrg[o] = [];
    byOrg[o].push(r);
  });
  const orgs = Object.keys(byOrg).sort((a,b)=>a.localeCompare(b,'es'));

  let head = '<th class="cal-org">PROVEEDOR / CONTRATO</th>';
  const monthSlots = [];
  for(let m=0; m<12; m++){
    const exp = CAL_CP_EXPANDED_MONTHS.has(m);
    monthSlots.push({mes:m, expanded:exp, span: exp ? dias[m] : 1});
    if(exp){
      head += `<th class="cal-month" data-m="${m}" colspan="${dias[m]}" style="background:#172e6b">${MES_NOMBRES[m]} ${year} ▾ (click p/cerrar)</th>`;
    } else {
      head += `<th class="cal-month" data-m="${m}">${MES_NOMBRES[m]}<br><span class="dn">click p/abrir</span></th>`;
    }
  }
  document.getElementById('cal-cp-head').innerHTML = head;
  document.querySelectorAll('#cal-cp-head .cal-month').forEach(th => {
    th.addEventListener('click', () => {
      const m = parseInt(th.dataset.m,10);
      if(CAL_CP_EXPANDED_MONTHS.has(m)) CAL_CP_EXPANDED_MONTHS.delete(m);
      else CAL_CP_EXPANDED_MONTHS.add(m);
      calCpRender();
    });
  });

  let body = '';
  let totalesMes = monthSlots.map(s => s.expanded ? Array(dias[s.mes]).fill(0) : [0]);

  orgs.forEach(o => {
    const orgExpanded = CAL_CP_EXPANDED_ORGS.has(o);
    const ctos = byOrg[o];
    const orgRowVals = monthSlots.map(s => s.expanded ? Array(dias[s.mes]).fill(0) : [0]);
    ctos.forEach(c => {
      const id = String(c.contratoid || c.numerointerno || c.contrato || '?');
      monthSlots.forEach((s, mi) => {
        if(s.expanded){
          for(let d=1; d<=dias[s.mes]; d++){
            const v = calCpGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
            orgRowVals[mi][d-1] += v;
            totalesMes[mi][d-1] += v;
          }
        } else {
          let mm = 0;
          for(let d=1; d<=dias[s.mes]; d++){
            mm += calCpGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
          }
          orgRowVals[mi][0] += mm;
          totalesMes[mi][0] += mm;
        }
      });
    });

    let rowOrg = `<tr class="cal-org ${orgExpanded?'expanded':''}" data-org="${escapeHtml(o)}"><td class="cal-org-cell" title="click para expandir contratos">${escapeHtml(o)} <span style="font-size:10px;color:var(--muted)">(${ctos.length})</span></td>`;
    monthSlots.forEach((s, mi) => {
      orgRowVals[mi].forEach(v => {
        rowOrg += `<td class="cal-num">${v?fmt.num(v):''}</td>`;
      });
    });
    rowOrg += '</tr>';
    body += rowOrg;

    if(orgExpanded){
      ctos.forEach(c => {
        const id = String(c.contratoid || c.numerointerno || c.contrato || '?');
        const ctoLbl = `CTO ${c.numerointerno||''} · ${c.producto||''} · ${c.fecha||''}`;
        let rowC = `<tr class="cal-contrato" data-id="${escapeHtml(id)}"><td class="cal-org-cell">${escapeHtml(ctoLbl)}</td>`;
        monthSlots.forEach((s, mi) => {
          if(s.expanded){
            for(let d=1; d<=dias[s.mes]; d++){
              const mm = String(s.mes+1).padStart(2,'0');
              const dd = String(d).padStart(2,'0');
              const v = calCpGetCell(id, mm, dd);
              rowC += `<td class="cal-num"><input type="text" data-id="${escapeHtml(id)}" data-mm="${mm}" data-dd="${dd}" data-has-value="${v?1:0}" value="${v?fmt.num(v):''}" placeholder="—"/></td>`;
            }
          } else {
            let mm = 0;
            for(let d=1; d<=dias[s.mes]; d++) mm += calCpGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
            const meslabel = String(s.mes+1).padStart(2,'0');
            rowC += `<td class="cal-num"><input type="text" data-id="${escapeHtml(id)}" data-mm="${meslabel}" data-dd="15" data-monthly="1" data-has-value="${mm?1:0}" value="${mm?fmt.num(mm):''}" placeholder="—"/></td>`;
          }
        });
        rowC += '</tr>';
        body += rowC;
      });
    }
  });
  document.getElementById('cal-cp-body').innerHTML = body;

  document.querySelectorAll('#cal-cp-body tr.cal-org > td.cal-org-cell').forEach(td => {
    td.addEventListener('click', () => {
      const o = td.closest('tr').dataset.org;
      if(CAL_CP_EXPANDED_ORGS.has(o)) CAL_CP_EXPANDED_ORGS.delete(o);
      else CAL_CP_EXPANDED_ORGS.add(o);
      calCpRender();
    });
  });
  document.querySelectorAll('#cal-cp-body input').forEach(inp => {
    inp.addEventListener('blur', () => {
      const raw = inp.value.replace(/\./g,'').replace(',','.').replace(/[^0-9.\-]/g,'');
      const v = parseFloat(raw);
      const id = inp.dataset.id, mm = inp.dataset.mm, dd = inp.dataset.dd;
      if(inp.dataset.monthly === '1'){
        const k = calCpKey();
        if(CAL_CP_DATA[k] && CAL_CP_DATA[k][id]){
          Object.keys(CAL_CP_DATA[k][id]).forEach(ck => {
            if(ck.startsWith(mm+'-') && ck !== mm+'-15') delete CAL_CP_DATA[k][id][ck];
          });
        }
      }
      calCpSetCell(id, mm, dd, isNaN(v) ? 0 : v);
      inp.value = (isNaN(v) || v===0) ? '' : fmt.num(v);
      inp.setAttribute('data-has-value', (!isNaN(v) && v!==0) ? '1' : '0');
      calCpRecalcTotals();
    });
    inp.addEventListener('keydown', (e) => { if(e.key === 'Enter') inp.blur(); });
  });

  calCpRecalcTotals();

  document.getElementById('cal-meta-cp').textContent =
    `${orgs.length} proveedores · ${contratos.length} contratos · moneda ${moneda}`;
}

function calCpRecalcTotals(){
  const year = parseInt(document.getElementById('cal-cp-year').value, 10);
  const moneda = document.getElementById('cal-cp-moneda').value;
  const dias = MES_DIAS.slice();
  if(calIsLeap(year)) dias[1] = 29;
  const monthSlots = [];
  for(let m=0; m<12; m++){
    monthSlots.push({mes:m, expanded: CAL_CP_EXPANDED_MONTHS.has(m), span: CAL_CP_EXPANDED_MONTHS.has(m) ? dias[m] : 1});
  }
  const contratos = cpfFiltered.filter(r =>
    r.moneda === moneda &&
    Math.abs(r.importependienteliquidar || 0) > 0
  );
  const tot = monthSlots.map(s => s.expanded ? Array(dias[s.mes]).fill(0) : [0]);
  contratos.forEach(c => {
    const id = String(c.contratoid || c.numerointerno || c.contrato || '?');
    monthSlots.forEach((s, mi) => {
      if(s.expanded){
        for(let d=1; d<=dias[s.mes]; d++){
          tot[mi][d-1] += calCpGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
        }
      } else {
        for(let d=1; d<=dias[s.mes]; d++){
          tot[mi][0] += calCpGetCell(id, String(s.mes+1).padStart(2,'0'), String(d).padStart(2,'0'));
        }
      }
    });
  });
  let foot = '<td class="cal-org-cell">TOTAL MES</td>';
  monthSlots.forEach((s, mi) => {
    tot[mi].forEach(v => {
      foot += `<td class="cal-num">${v?fmt.num(v):'—'}</td>`;
    });
  });
  document.getElementById('cal-cp-foot').innerHTML = foot;
  calCpStorageInfo();
}

calCpInitSelectors();
calCpRender();


/* ============================================================
   ===============  CANJES (sub-pestaña COMPRA) ==============
   ============================================================ */

const SALDOS  = PAYLOAD.saldos || [];        // composicion de saldos
const BCR     = PAYLOAD.bcr || {granos:{}, tc_usd_ars:null};

// Mapeo: producto Finnegans -> clave BCR
function granoBCR(producto){
  if(!producto) return null;
  const p = producto.toLowerCase();
  if(p.includes('soja'))    return 'soja';
  if(p.includes('maíz') || p.includes('maiz')) return 'maiz';
  if(p.includes('trigo'))   return 'trigo';
  if(p.includes('girasol')) return 'girasol';
  if(p.includes('sorgo'))   return 'sorgo';
  return null;
}

// Precios editables (localStorage)
const CJ_PX_KEY = 'tablero-granos-canjes-precios-v1';
let CJ_PX = {soja:null, maiz:null, trigo:null, girasol:null, sorgo:null, tc:null};
try {
  const saved = JSON.parse(localStorage.getItem(CJ_PX_KEY) || 'null');
  if(saved) CJ_PX = saved;
} catch(e) {}

function cjResetPrecios(fromBCR=true){
  if(fromBCR){
    const g = BCR.granos || {};
    CJ_PX = {
      soja:    g.soja    ? g.soja.usd    : null,
      maiz:    g.maiz    ? g.maiz.usd    : null,
      trigo:   g.trigo   ? g.trigo.usd   : null,
      girasol: g.girasol ? g.girasol.usd : null,
      sorgo:   g.sorgo   ? g.sorgo.usd   : null,
      tc:      BCR.tc_usd_ars || null,
    };
    localStorage.setItem(CJ_PX_KEY, JSON.stringify(CJ_PX));
  }
  cjUpdateInputs();
  cjRender();
}
function cjUpdateInputs(){
  ['soja','maiz','trigo','girasol','sorgo'].forEach(g=>{
    const el = document.getElementById('cj-px-'+g);
    if(el) el.value = CJ_PX[g] != null ? fmt.num2(CJ_PX[g]) : '';
  });
  document.getElementById('cj-tc').value = CJ_PX.tc != null ? fmt.num2(CJ_PX.tc) : '';
}
function cjReadInputs(){
  ['soja','maiz','trigo','girasol','sorgo'].forEach(g=>{
    const v = parseFloat((document.getElementById('cj-px-'+g).value||'').replace(/\./g,'').replace(',','.').replace(/[^0-9.\-]/g,''));
    CJ_PX[g] = isNaN(v) ? null : v;
  });
  const t = parseFloat((document.getElementById('cj-tc').value||'').replace(/\./g,'').replace(',','.').replace(/[^0-9.\-]/g,''));
  CJ_PX.tc = isNaN(t) ? null : t;
  localStorage.setItem(CJ_PX_KEY, JSON.stringify(CJ_PX));
}

// Inicial: si no hay precios guardados, usar los del BCR
if(CJ_PX.soja==null && CJ_PX.tc==null) cjResetPrecios(true);
else cjUpdateInputs();
document.getElementById('cj-bcr-meta').textContent =
  BCR.fecha_informe ? `Fuente: ${BCR.source} · informe ${BCR.fecha_informe}` : 'BCR no disponible';

['soja','maiz','trigo','girasol','sorgo'].forEach(g=>{
  document.getElementById('cj-px-'+g).addEventListener('blur', ()=>{ cjReadInputs(); cjRender(); });
});
document.getElementById('cj-tc').addEventListener('blur', ()=>{ cjReadInputs(); cjRender(); });
document.getElementById('cj-reset-px').addEventListener('click', ()=> cjResetPrecios(true));

// Tabla de Canjes — detalle por cliente
const CJ_TABLE_COLS = [
  {k:'cliente',         lbl:'Cliente',          num:false},
  {k:'vendedor',        lbl:'Vendedor',         num:false},
  {k:'grano',           lbl:'Grano',            num:false},
  {k:'meses',           lbl:'Meses Canje',      num:false},
  {k:'saldoUsd',        lbl:'USD Canje',        num:true, sum:true},
  {k:'tnCanje',         lbl:'Tn Canje',         num:true, sum:true},
  {k:'tnContratadas',   lbl:'Tn Contratadas',   num:true, sum:true},
  {k:'usdCubierto',     lbl:'USD Cubierto',     num:true, sum:true},
  {k:'usdFaltante',     lbl:'USD Faltante',     num:true, sum:true},
  {k:'tnFaltante',      lbl:'Tn Faltante',      num:true, sum:true},
  {k:'precio',          lbl:'Precio USD',       num:true, sum:'avg'},
  {k:'_estado',         lbl:'Estado',           num:false, html:true},
];

let cjFiltered = [];
let cjSortKey = null, cjSortDir = 1;
const SEL_CJ = new Set();
let lastClickIdxCj = null;

// Poblar dropdowns CONDICION y VENDEDOR + listeners ------------------------
function cjInitFilters(){
  // condiciones de pago: solo las que contienen "canje"
  const condSel = document.getElementById('cj-cond');
  const allConds = [...new Set(SALDOS.map(s => s.condicionpago).filter(v => v && v.toLowerCase().includes('canje')))].sort();
  condSel.size = Math.min(allConds.length + 1, 8);
  condSel.innerHTML = allConds.map(c => {
    // default seleccionadas: las que contengan "2026"
    const selected = c.includes('2026') ? 'selected' : '';
    return `<option value="${escapeHtml(c)}" ${selected}>${escapeHtml(c)}</option>`;
  }).join('');

  // vendedores: todos los que aparezcan en saldos con condicion canje
  const vendSel = document.getElementById('cj-vend');
  const allVends = [...new Set(SALDOS
    .filter(s => s.condicionpago && s.condicionpago.toLowerCase().includes('canje'))
    .map(s => s.vendedor).filter(v => v))].sort();
  vendSel.innerHTML = '<option value="">Todos</option>' +
    allVends.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');

  condSel.addEventListener('change', cjApply);
  vendSel.addEventListener('change', cjApply);
}

function cjGetSelectedConds(){
  const sel = document.getElementById('cj-cond');
  return [...sel.selectedOptions].map(o => o.value);
}

function cjBuildRows(){
  const condSel = cjGetSelectedConds();
  const vendSel = document.getElementById('cj-vend').value;

  // Filtrar saldos por condicion canje seleccionada
  const saldosFiltrados = SALDOS.filter(s => {
    const c = s.condicionpago || '';
    if(condSel.length > 0 && !condSel.includes(c)) return false;
    if(vendSel && s.vendedor !== vendSel) return false;
    return true;
  });

  // Agrupar saldos por cliente (sumamos los IMPORTES de los movimientos con condicion canje)
  const byClient = {};
  saldosFiltrados.forEach(s => {
    const id = s.organizacionid || s.organizacion || '—';
    if(!byClient[id]){
      byClient[id] = {
        id,
        cliente: (s.organizacion||'').trim(),
        vendedor: s.vendedor || '',
        cuit: s.cuit || '',
        saldoArs:0, saldoUsd:0,
        meses: new Set(),
        condiciones: new Set(),
      };
    }
    byClient[id].saldoArs += s.importemonppal || 0;
    byClient[id].saldoUsd += s.importemonsecundaria || 0;
    if(s.condicionpago) byClient[id].condiciones.add(s.condicionpago);
    // extraer mes de la condicion para mostrar "Meses Canje"
    const m = (s.condicionpago||'').match(/canje\s+(\w+)\s+(\d{4})/i);
    if(m) byClient[id].meses.add(m[1] + ' ' + m[2]);
  });

  // Agrupar contratos de compra POR CLIENTE (proveedor en compra = cliente en canjes)
  const ctosByClient = {};
  (DATA_CP || []).forEach(c => {
    const k = (c.organizacion||'').trim().toUpperCase();
    if(!ctosByClient[k]) ctosByClient[k] = [];
    ctosByClient[k].push(c);
  });

  // Construir filas
  const granoSel = document.getElementById('cj-grano').value;
  const rows = [];
  Object.values(byClient).forEach(b => {
    const nameKey = (b.cliente||'').trim().toUpperCase();
    const ctos = ctosByClient[nameKey] || [];

    // determinar grano: si el usuario eligió uno fijo, usar ese.
    // si "auto": tomar el grano del primer contrato del cliente, sino soja por default
    let grano = granoSel === 'auto' ? null : granoSel;
    if(!grano){
      for(const c of ctos){
        const g = granoBCR(c.producto);
        if(g){ grano = g; break; }
      }
      if(!grano) grano = 'soja';
    }

    const precio = CJ_PX[grano] || 0;
    const tc = CJ_PX.tc || 0;
    const saldoUSDeff = (b.saldoUsd && Math.abs(b.saldoUsd) > 0.01)
      ? b.saldoUsd
      : (tc>0 ? b.saldoArs / tc : 0);
    const tnCanje = precio > 0 ? saldoUSDeff / precio : 0;

    let tnContratadas = 0;
    ctos.forEach(c => {
      if(granoBCR(c.producto) === grano){
        tnContratadas += c.cantidadmax || 0;
      }
    });

    const usdCubierto = Math.min(tnContratadas, Math.max(tnCanje, 0)) * precio;
    const usdFaltante = Math.max(saldoUSDeff - usdCubierto, 0);
    const tnFaltante  = precio > 0 ? usdFaltante / precio : 0;

    let estado;
    if(tnContratadas <= 0)            estado = 'SIN';
    else if(tnContratadas >= tnCanje) estado = 'OK';
    else                              estado = 'PARCIAL';

    rows.push({
      id: b.id, cliente: b.cliente, vendedor: b.vendedor, grano,
      meses: [...b.meses].sort().join(', ') || '—',
      saldoArs: b.saldoArs, saldoUsd: saldoUSDeff,
      tnCanje, tnContratadas, usdCubierto, usdFaltante, tnFaltante,
      precio,
      _estado: estado,
    });
  });
  return rows;
}

function cjEstadoChip(r){
  if(r._estado === 'OK')      return '<span class="chip ok">Contrato OK</span>';
  if(r._estado === 'PARCIAL') return '<span class="chip warn">Con contrato parcial</span>';
  return '<span class="chip err">Sin contrato</span>';
}

function cjApply(){
  const grano = document.getElementById('cj-grano').value;
  const estado = document.getElementById('cj-estado').value;
  const q = (document.getElementById('cj-q').value||'').toLowerCase().trim();
  const all = cjBuildRows();
  cjFiltered = all.filter(r => {
    if(estado && r._estado !== estado) return false;
    if(q && !(r.cliente||'').toLowerCase().includes(q)) return false;
    return true;
  });
  cjRender();
}

['cj-grano','cj-estado'].forEach(id => document.getElementById(id).addEventListener('change', cjApply));
document.getElementById('cj-q').addEventListener('input', cjApply);
document.getElementById('cj-clear').addEventListener('click', () => {
  document.getElementById('cj-grano').value = 'auto';
  document.getElementById('cj-estado').value = '';
  document.getElementById('cj-vend').value = '';
  document.getElementById('cj-q').value = '';
  // condiciones: restaurar las que tienen "2026"
  [...document.getElementById('cj-cond').options].forEach(o => o.selected = o.value.includes('2026'));
  cjApply();
});

function cjRender(){
  if(cjFiltered.length === 0 && SALDOS.length > 0) cjFiltered = cjBuildRows();
  const rows = cjFiltered;

  // KPIs
  let clientesEnCanje = 0, totalUsdCanje = 0, totalUsdCubierto = 0, totalUsdFaltante = 0;
  let totalTnFaltante = 0, okN = 0, parcialN = 0, sinN = 0;
  const granosCount = {};
  rows.forEach(r => {
    clientesEnCanje++;
    totalUsdCanje += r.saldoUsd || 0;
    totalUsdCubierto += r.usdCubierto || 0;
    totalUsdFaltante += r.usdFaltante || 0;
    totalTnFaltante += r.tnFaltante || 0;
    if(r._estado === 'OK') okN++;
    else if(r._estado === 'PARCIAL') parcialN++;
    else sinN++;
    granosCount[r.grano] = (granosCount[r.grano]||0) + 1;
  });
  const cobertura = totalUsdCanje > 0 ? totalUsdCubierto/totalUsdCanje : 0;
  const granosStr = Object.entries(granosCount).map(([g,n])=>`${n} ${g}`).join(' · ');

  document.getElementById('kpi-row-canjes').innerHTML = `
    <div class="kpi"><div class="lbl">Clientes en Canje</div><div class="val">${fmt.int(clientesEnCanje)}</div><div class="hint">${rows.length} filas · ${granosStr}</div></div>
    <div class="kpi"><div class="lbl">USD Canje Total</div><div class="val">$${fmt.num(totalUsdCanje)}</div><div class="hint">Aforado a precio dispo USD/Tn</div></div>
    <div class="kpi green"><div class="lbl">USD Cubierto</div><div class="val">$${fmt.num(totalUsdCubierto)}</div><div class="hint">${fmt.pct(cobertura)} cobertura · ${okN} OK · ${parcialN} parcial</div></div>
    <div class="kpi red"><div class="lbl">USD Faltante a Contratar</div><div class="val">$${fmt.num(totalUsdFaltante)}</div><div class="hint">${sinN} sin contrato · ${fmt.num(totalTnFaltante)} Tn a contratar</div></div>
  `;

  // Resumen por Vendedor
  cjRenderVendedores(rows);

  // Cabecera tabla
  const head = CJ_TABLE_COLS.map(c => {
    const ar = (cjSortKey===c.k) ? (cjSortDir>0?'▲':'▼') : '';
    const cls = (cjSortKey===c.k) ? (cjSortDir>0?'sort-asc':'sort-desc') : '';
    return `<th class="${cls}" data-k="${c.k}" data-num="${c.num?1:0}">${c.lbl}<span class="arrow">${ar||'⇅'}</span></th>`;
  }).join('');
  document.getElementById('tbl-head-canjes').innerHTML = head;
  document.querySelectorAll('#tbl-head-canjes th').forEach(th => {
    th.addEventListener('click', () => {
      const k = th.dataset.k;
      if(cjSortKey === k) cjSortDir = -cjSortDir;
      else { cjSortKey = k; cjSortDir = 1; }
      cjRender();
    });
  });

  let sorted = rows;
  if(cjSortKey){
    const col = CJ_TABLE_COLS.find(c=>c.k===cjSortKey);
    sorted = rows.slice().sort((a,b)=>{
      let va = a[cjSortKey], vb = b[cjSortKey];
      if(col && col.num){ va=va||0; vb=vb||0; return (va-vb)*cjSortDir; }
      va=(va==null?'':String(va)); vb=(vb==null?'':String(vb));
      return va.localeCompare(vb,'es',{numeric:true})*cjSortDir;
    });
  } else {
    // default sort: por USD canje desc
    sorted = rows.slice().sort((a,b) => (b.saldoUsd||0) - (a.saldoUsd||0));
  }

  const MAX = 1000;
  const visible = sorted.slice(0,MAX);
  document.getElementById('tbl-body-canjes').innerHTML = visible.map((r,i) => {
    const id = String(r.id);
    const selCls = SEL_CJ.has(id) ? ' class="row-sel"' : '';
    return `<tr data-id="${id}" data-i="${i}"${selCls}>`+CJ_TABLE_COLS.map(c=>{
      if(c.k==='_estado') return '<td>'+cjEstadoChip(r)+'</td>';
      const v = r[c.k];
      if(c.k === 'grano') return `<td><span class="chip ${grainClass(v) || 'neutral'}" style="background:#f1f5f9;color:#475569;text-transform:capitalize">${v}</span></td>`;
      if(c.num) return `<td class="num">${v==null?'<span class=muted>—</span>':fmt.num(v)}</td>`;
      return `<td>${v==null?'<span class=muted>—</span>':String(v)}</td>`;
    }).join('')+'</tr>';
  }).join('') || '<tr><td colspan="99" style="padding:30px;text-align:center;color:var(--muted)">Sin resultados</td></tr>';

  document.querySelectorAll('#tbl-body-canjes tr[data-id]').forEach(tr => {
    tr.addEventListener('click', (ev) => {
      const id = tr.dataset.id, i = parseInt(tr.dataset.i,10);
      if(ev.shiftKey && lastClickIdxCj !== null){
        const a = Math.min(i, lastClickIdxCj), b = Math.max(i, lastClickIdxCj);
        for(let k=a; k<=b; k++) SEL_CJ.add(String(visible[k].id));
      } else {
        if(SEL_CJ.has(id)) SEL_CJ.delete(id);
        else SEL_CJ.add(id);
        lastClickIdxCj = i;
      }
      renderFootCj(sorted);
      document.querySelectorAll('#tbl-body-canjes tr[data-id]').forEach(t2 => {
        t2.classList.toggle('row-sel', SEL_CJ.has(t2.dataset.id));
      });
    });
  });

  renderFootCj(sorted);
  document.getElementById('cj-count').textContent =
    `${sorted.length.toLocaleString('es-AR')} / ${SALDOS.length > 0 ? new Set(SALDOS.map(s=>s.organizacionid||s.nombre)).size.toLocaleString('es-AR') : 0} clientes`;
}

function renderFootCj(rows){
  const computeFoot = (subset, label, isSel) => CJ_TABLE_COLS.map((c, idx) => {
    if(idx === 0){
      const btn = isSel ? ` <button class="clear-sel" onclick="SEL_CJ.clear(); cjRender();">Limpiar selección</button>` : '';
      return `<td class="lbl">${label} (${subset.length.toLocaleString('es-AR')})${btn}</td>`;
    }
    if(c.sum === true){
      const total = subset.reduce((acc,r) => acc + (Number(r[c.k]) || 0), 0);
      return `<td class="num">${fmt.num(total)}</td>`;
    }
    if(c.sum === 'avg'){
      const valid = subset.map(r => Number(r[c.k])).filter(v => !isNaN(v) && v!==0);
      const avg = valid.length ? valid.reduce((a,b)=>a+b,0)/valid.length : null;
      return `<td class="num" title="promedio">${avg==null?'—':fmt.num(avg)}</td>`;
    }
    return '<td></td>';
  }).join('');
  const totalRow = `<tr>${computeFoot(rows, 'TOTAL', false)}</tr>`;
  const selRows = rows.filter(r => SEL_CJ.has(String(r.id)));
  const selRow  = selRows.length ? `<tr class="sel">${computeFoot(selRows, '🟨 SELECCIONADOS', true)}</tr>` : '';
  document.getElementById('tbl-foot-canjes').innerHTML = totalRow + selRow;
}

function cjRenderVendedores(rows){
  // agrupar por vendedor
  const byVend = {};
  rows.forEach(r => {
    const v = r.vendedor || '— sin vendedor —';
    if(!byVend[v]) byVend[v] = {vendedor:v, clientes:new Set(), usdCanje:0, usdCubierto:0, usdFaltante:0, ok:0, parcial:0, sin:0};
    byVend[v].clientes.add(r.id);
    byVend[v].usdCanje    += r.saldoUsd || 0;
    byVend[v].usdCubierto += r.usdCubierto || 0;
    byVend[v].usdFaltante += r.usdFaltante || 0;
    if(r._estado === 'OK') byVend[v].ok++;
    else if(r._estado === 'PARCIAL') byVend[v].parcial++;
    else byVend[v].sin++;
  });
  const arr = Object.values(byVend).map(v => ({
    ...v, clientes: v.clientes.size,
    cobertura: v.usdCanje > 0 ? v.usdCubierto/v.usdCanje : 0,
  })).sort((a,b) => b.usdCanje - a.usdCanje);

  document.getElementById('cj-vend-meta').textContent = `${arr.length} vendedores`;

  const head = `
    <th>Vendedor</th>
    <th class="num">Clientes</th>
    <th class="num">USD Canje</th>
    <th class="num">USD Cubierto</th>
    <th class="num">USD Faltante</th>
    <th>Cobertura</th>
    <th class="num">OK</th>
    <th class="num">Parcial</th>
    <th class="num">Sin Contrato</th>
  `;
  document.getElementById('tbl-vend-head').innerHTML = head;

  document.getElementById('tbl-vend-body').innerHTML = arr.map(v => {
    const pctW = Math.min(100, v.cobertura*100);
    const barColor = v.cobertura >= 0.9 ? '#16a34a' : (v.cobertura >= 0.3 ? '#16a34a' : '#f59e0b');
    return `<tr>
      <td>${escapeHtml(v.vendedor)}</td>
      <td class="num">${fmt.int(v.clientes)}</td>
      <td class="num">$${fmt.num(v.usdCanje)}</td>
      <td class="num">$${fmt.num(v.usdCubierto)}</td>
      <td class="num">$${fmt.num(v.usdFaltante)}</td>
      <td>
        <div style="display:flex;align-items:center;gap:8px">
          <div style="flex:1;height:6px;background:#e5e9f2;border-radius:4px;overflow:hidden;min-width:80px">
            <div style="height:100%;width:${pctW}%;background:${barColor}"></div>
          </div>
          <span style="font-size:11.5px;font-weight:600;min-width:48px;text-align:right">${fmt.pct(v.cobertura)}</span>
        </div>
      </td>
      <td class="num" style="color:var(--green)">${v.ok || '—'}</td>
      <td class="num" style="color:var(--orange)">${v.parcial || '—'}</td>
      <td class="num" style="color:var(--red)">${v.sin || '—'}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="99" style="text-align:center;padding:20px;color:var(--muted)">Sin vendedores con datos</td></tr>';

  // footer totales
  const tot = arr.reduce((acc,v) => ({
    clientes: acc.clientes + v.clientes,
    canje: acc.canje + v.usdCanje,
    cub: acc.cub + v.usdCubierto,
    falt: acc.falt + v.usdFaltante,
    ok: acc.ok + v.ok, p: acc.p + v.parcial, s: acc.s + v.sin,
  }), {clientes:0,canje:0,cub:0,falt:0,ok:0,p:0,s:0});
  const cob = tot.canje>0 ? tot.cub/tot.canje : 0;
  document.getElementById('tbl-vend-foot').innerHTML = `
    <tr>
      <td class="lbl">TOTAL</td>
      <td class="num">${fmt.int(tot.clientes)}</td>
      <td class="num">$${fmt.num(tot.canje)}</td>
      <td class="num">$${fmt.num(tot.cub)}</td>
      <td class="num">$${fmt.num(tot.falt)}</td>
      <td class="num">${fmt.pct(cob)}</td>
      <td class="num">${tot.ok}</td>
      <td class="num">${tot.p}</td>
      <td class="num">${tot.s}</td>
    </tr>`;
}

cjInitFilters();
cjApply();


</script>
</body>
</html>
"""


# ---------- runner ----------
def main() -> int:
    print(f"[+] Autenticando contra API de Finnegans ...", flush=True)
    api.get_token()
    print(f"[+] Token OK", flush=True)

    counts = {"compra": 0, "venta": 0, "posicion": 0}
    pilot_rows: list[dict] = []
    compra_rows: list[dict] = []

    for label, endpoint, params, tab in DATASETS:
        print(f"  [{tab:<8}] {label:<42}  -> GET {endpoint}", flush=True)
        try:
            data = api.call(endpoint, params)
        except Exception as e:
            print(f"    [!] ERROR: {e}")
            continue
        if not isinstance(data, list):
            data = []
        raw_n = len(data)
        # filtrar anulados: descartar cuando ESTADOANULACION incluya "anul" (case-insensitive)
        # pero solo si la fila TIENE esa columna
        def is_anulado(r):
            v = (r.get("ESTADOANULACION") or r.get("estadoanulacion") or "")
            return "anul" in str(v).lower() and "no anul" not in str(v).lower()
        data = [r for r in data if not is_anulado(r)]
        anulados_n = raw_n - len(data)
        n = len(data)
        counts[tab] += n
        print(f"    -> {n} filas (descarté {anulados_n} anulados de {raw_n})")
        if endpoint == PILOT_ENDPOINT:
            pilot_rows = data
            print(f"    [+] piloto VENTA: {len(pilot_rows)} filas")
        elif endpoint == COMPRA_ENDPOINT:
            compra_rows = data
            print(f"    [+] COMPRA: {len(compra_rows)} filas")

    pilot_norm  = normalize_pilot(pilot_rows)
    compra_norm = normalize_pilot(compra_rows)   # misma normalizacion, mismas columnas

    # Composicion de Saldos detallada (con CONDICIONPAGO y VENDEDOR) para modulo Canjes
    print(f"\n[+] Bajando Composicion Saldo Cliente (detallada con condicion y vendedor)...", flush=True)
    saldos_raw = api.call("/reports/composicionSaldoCliente",
                          {"PARAMWEBREPORT_fecha": "getCurrentDate"})
    if not isinstance(saldos_raw, list):
        saldos_raw = []
    saldos_norm = [{k.lower(): v for k, v in r.items()} for r in saldos_raw]
    canjes_n = sum(1 for r in saldos_norm if "canje" in (r.get("condicionpago") or "").lower())
    print(f"    -> {len(saldos_norm)} filas de saldos, {canjes_n} con condicion 'Canje'")

    # Precios pizarra BCR (publico, sin auth)
    print(f"\n[+] Bajando precios pizarra BCR...", flush=True)
    try:
        bcr = bcr_pizarra.fetch_pizarra()
        print(f"    -> TC USD/ARS: {bcr.get('tc_usd_ars')}")
        for g, d in bcr.get("granos", {}).items():
            print(f"       {g}: ARS {d.get('ars')} / USD {d.get('usd')} (estim={d.get('estimativo')})")
    except Exception as e:
        print(f"    [!] error BCR: {e}")
        bcr = {"fetched_at": None, "tc_usd_ars": None, "granos": {}}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "pilot":  pilot_norm,
        "compra": compra_norm,
        "saldos": saldos_norm,
        "bcr":    bcr,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    build_time = datetime.now().strftime("%Y-%m-%d %H:%M (%Z)").strip().rstrip("()").strip()

    html = HTML_TEMPLATE.replace("__PAYLOAD__", payload_json).replace("__BUILD_TIME__", build_time)
    OUTPUT.write_text(html, encoding="utf-8")
    size_kb = OUTPUT.stat().st_size / 1024
    print(f"\n[+] Escrito {OUTPUT}  ({size_kb:,.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
