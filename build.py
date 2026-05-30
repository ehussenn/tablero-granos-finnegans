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
    # Stock por Deposito se baja ahora directamente más abajo (sin MonedaID, que rompía); ver "stock_silobolsa"
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
<title>Granos — Agronasaja</title>
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

  /* ===== Layout con menú lateral (sidebar) + topbar ===== */
  .tabs{display:none !important}      /* reemplazadas por el sidebar */
  .subtabs{display:none !important}   /* idem (siguen funcionando por JS) */
  .app-shell{display:flex;min-height:100vh}
  .sidebar{width:248px;flex:0 0 248px;background:#14264a;color:#aebbd2;position:fixed;top:0;left:0;bottom:0;overflow-y:auto;border-right:1px solid #24375f;z-index:30}
  .sidebar .brand{display:flex;align-items:center;gap:10px;padding:18px 18px 4px}
  .sidebar .brand-logo{width:38px;height:38px;border-radius:9px;background:linear-gradient(135deg,#1e3a8a,#3b82f6);display:flex;align-items:center;justify-content:center;font-size:20px;flex:0 0 38px}
  .sidebar .brand-name{font-weight:800;letter-spacing:1px;color:#fff;font-size:15px;line-height:1.1}
  .sidebar .brand-sub{font-size:10px;letter-spacing:2px;color:#8ea3c4;text-transform:uppercase;margin-top:2px}
  .sidebar .campana{margin:14px 18px 4px;font-size:10px;letter-spacing:1.5px;color:#60a5fa;text-transform:uppercase;font-weight:700;border-top:1px solid #24375f;padding-top:12px}
  .nav{padding:4px 10px 28px}
  .nav-group{font-size:10px;letter-spacing:1.5px;color:#6b80a6;text-transform:uppercase;font-weight:700;margin:16px 10px 6px}
  .nav-item{display:block;padding:9px 12px;border-radius:8px;color:#c2cee3;font-size:13.5px;cursor:pointer;text-decoration:none;border-left:3px solid transparent;transition:all .15s;margin:1px 0}
  .nav-item:hover{background:#1c2f54;color:#fff}
  .nav-item.active{background:#1c2f54;color:#bfdbfe;border-left-color:#3b82f6;font-weight:600}
  .main{flex:1;margin-left:248px;min-width:0;display:flex;flex-direction:column}
  .topbar{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:11px 24px;display:flex;justify-content:space-between;align-items:center;z-index:25;gap:16px}
  .topbar-title{font-size:18px;font-weight:700;color:var(--ink)}
  .topbar-right{display:flex;align-items:center;gap:14px}
  .topbar-meta{font-size:11px;color:var(--muted);text-align:right;line-height:1.35}
  .topbar-meta .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:5px;box-shadow:0 0 0 3px rgba(34,197,94,.25)}
  .admin-pill{background:#dbeafe;color:#1e3a8a;border:1px solid #bfdbfe;padding:7px 16px;border-radius:20px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
  .admin-pill:hover{background:#bfdbfe}
  .logout-btn{display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid var(--line);color:var(--ink);padding:7px 16px;border-radius:20px;font-size:13px;font-weight:600;text-decoration:none;cursor:pointer;white-space:nowrap}
  .logout-btn:hover{border-color:#dc2626;color:#dc2626}
  .content{padding:20px 24px;max-width:1560px;width:100%}
  .menu-toggle{display:none;background:none;border:none;font-size:22px;cursor:pointer;color:var(--ink);line-height:1}
  @media (max-width:880px){
    .sidebar{transform:translateX(-100%);transition:transform .2s;box-shadow:0 0 30px rgba(0,0,0,.4)}
    .sidebar.open{transform:none}
    .main{margin-left:0}
    .menu-toggle{display:block}
  }

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
  .kpi.yellow{border-top-color:#eab308}
  .kpi.pink{border-top-color:#ec4899}
  .kpi .lbl{color:var(--muted);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.6px}
  .kpi .val{font-size:28px;font-weight:700;margin-top:4px;color:var(--ink)}
  .kpi.green .val{color:var(--green)}
  .kpi.red .val{color:var(--red)}
  .kpi.orange .val{color:var(--orange)}
  .kpi.yellow .val{color:#a16207}
  .kpi.pink .val{color:#be185d}
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

  /* Cruce Cliente x Comprador (matrix) */
  #cx-matrix{font-size:11.5px}
  #cx-matrix thead th{background:#1e3a8a;color:#fff;padding:8px 6px;font-size:10.5px;text-transform:uppercase;letter-spacing:.2px;text-align:center;border-right:1px solid rgba(255,255,255,.08);position:sticky;top:0;z-index:1}
  #cx-matrix thead th.cx-cliente-h{text-align:left;background:#0f172a;min-width:240px;position:sticky;left:0;z-index:3}
  #cx-matrix thead th.cx-pct-cli{background:#7c2d12;min-width:60px}
  #cx-matrix thead th.cx-precio-cli{background:#0d9488;min-width:75px}
  #cx-matrix thead th.cx-comprador{background:#1e3a8a;min-width:95px;cursor:default}
  #cx-matrix thead th.cx-comprador .pct{display:block;font-size:9.5px;background:#f59e0b;color:#451a03;padding:1px 4px;border-radius:8px;margin-top:3px;font-weight:600}
  #cx-matrix thead th.cx-comprador .pct.zero{background:#fee2e2;color:#7f1d1d}
  #cx-matrix tbody td{padding:6px 5px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}
  #cx-matrix tbody td.cx-cli-name{text-align:left;font-weight:500;background:#fff;position:sticky;left:0;z-index:2;border-right:2px solid var(--line)}
  #cx-matrix tbody tr:nth-child(even) td:not(.cx-cli-name){background:#fafbff}
  #cx-matrix tbody tr:hover td{background:#eff5ff}
  #cx-matrix tbody td.cx-pct-cell{background:#fef3c7;color:#92400e;font-weight:600}
  #cx-matrix tbody td.cx-precio-cell{background:#ccfbf1;color:#134e4a;font-weight:600}
  #cx-matrix tbody td.cx-empty{color:#cbd5e1}
  #cx-matrix tfoot td{background:#eef2ff;font-weight:700;padding:8px 6px;font-size:11.5px;border-top:2px solid var(--blue);position:sticky;bottom:0;z-index:1;text-align:right}
  #cx-matrix tfoot td.cx-foot-lbl{text-align:left;background:#dbeafe;color:var(--blue);position:sticky;left:0;z-index:2}

  /* Cards de cultivos (variante con balance) */
  .cult-card{padding:14px;border-radius:10px;border-left:4px solid #94a3b8;background:#fafbff}
  .cult-card.soja{border-left-color:#16a34a;background:#f0fdf4}
  .cult-card.maiz{border-left-color:#f59e0b;background:#fffbeb}
  .cult-card.trigo{border-left-color:#a16207;background:#fefce8}
  .cult-card.girasol{border-left-color:#d97706;background:#fff7ed}
  .cult-card .name{font-weight:600;font-size:13px;display:flex;justify-content:space-between;align-items:center}
  .cult-card .name .cnt{font-size:11px;color:var(--muted);font-weight:500}
  .cult-card .r{display:flex;justify-content:space-between;margin-top:4px;font-size:12.5px}
  .cult-card .r .k{color:var(--muted)}
  .cult-card .bal{margin-top:8px;padding-top:6px;border-top:1px solid var(--line);display:flex;justify-content:space-between;font-weight:700;font-size:13px}
  .cult-card .pos{color:var(--green)}
  .cult-card .neg{color:var(--red)}

  /* Vista toggle */
  .vista-toggle{transition:all .15s}
  .vista-toggle.active{background:#16a34a;border-color:#16a34a;color:#fff;box-shadow:0 2px 6px rgba(22,163,74,.3)}

  /* Posicion Granaria */
  #pn-tabla thead th{background:#1e3a8a;color:#fff;padding:6px 8px;font-size:10px;text-transform:uppercase;letter-spacing:.3px;border-right:1px solid rgba(255,255,255,.1);text-align:center}
  #pn-tabla thead th.pn-prod{background:#0f172a;text-align:left;position:sticky;left:0;z-index:3;min-width:180px}
  #pn-tabla thead th.grp{background:#7c2d12;border-bottom:2px solid #fed7aa}
  #pn-tabla thead th.grp-prod{background:#15803d;border-bottom:2px solid #86efac}
  #pn-tabla thead th.grp-compra{background:#1e3a8a;border-bottom:2px solid #93c5fd}
  #pn-tabla thead th.grp-venta{background:#9a3412;border-bottom:2px solid #fdba74}
  #pn-tabla thead th.grp-resultado{background:#581c87;border-bottom:2px solid #d8b4fe}
  #pn-tabla tbody td{padding:5px 6px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}
  #pn-tabla tbody td.pn-prod-cell{text-align:left;font-weight:500;background:#fff;position:sticky;left:0;z-index:2;border-right:2px solid var(--line)}
  #pn-tabla tbody tr.pn-grupo td{background:#fffbeb;font-weight:700;color:#92400e;border-top:2px solid #fcd34d;font-size:12px}
  #pn-tabla tbody tr.pn-grupo td.pn-prod-cell{background:#fef3c7}
  #pn-tabla tbody tr.pn-total td{background:#dbeafe;font-weight:700;color:#1e3a8a;border-top:2px solid var(--blue);font-size:12px}
  #pn-tabla tbody tr.pn-total td.pn-prod-cell{background:#bfdbfe}
  #pn-tabla tbody td input{width:100%;border:1px solid transparent;background:transparent;padding:2px 4px;text-align:right;font-size:11px;font-family:inherit;font-variant-numeric:tabular-nums;border-radius:3px;color:inherit}
  #pn-tabla tbody td input:hover{border-color:var(--line);background:#fff}
  #pn-tabla tbody td input:focus{border-color:var(--blue);background:#fff;outline:none;box-shadow:0 0 0 2px rgba(59,130,246,.15)}
  #pn-tabla tbody td.editable{background:#fffbeb}
  #pn-tabla tbody td.editable input{color:#92400e;font-weight:500}
  #pn-tabla tbody td.calc{background:#f0fdf4;color:#15803d;font-weight:500}
  #pn-tabla tbody td.pos-pos{background:#dcfce7;color:#15803d;font-weight:700}
  #pn-tabla tbody td.pos-neg{background:#fee2e2;color:#991b1b;font-weight:700}
  #pn-tabla tfoot td{background:#1e3a8a;color:#fff;font-weight:700;padding:6px 8px;font-size:12px;border-top:2px solid #0f172a;position:sticky;bottom:0}
  #pn-tabla tfoot td.pn-prod-cell{background:#0f172a;position:sticky;left:0;z-index:1}

  /* Cards de cultivo posicion */
  .pn-card{padding:14px;border-radius:10px;border-top:4px solid #94a3b8;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.04)}
  .pn-card.soja{border-top-color:#16a34a}
  .pn-card.maiz{border-top-color:#f59e0b}
  .pn-card.trigo{border-top-color:#a16207}
  .pn-card.girasol{border-top-color:#d97706}
  .pn-card.sorgo{border-top-color:#7c2d12}
  .pn-card .name{font-weight:700;font-size:13px;display:flex;justify-content:space-between;align-items:center;text-transform:uppercase;letter-spacing:.3px}
  .pn-card .pos-val{font-size:26px;font-weight:800;margin:4px 0}
  .pn-card .pos-val.pos{color:var(--green)}
  .pn-card .pos-val.neg{color:var(--red)}
  .pn-card .of-de{display:flex;justify-content:space-between;font-size:11.5px;color:var(--muted);margin-top:6px}
  .pn-card .bar-cobertura{margin-top:8px;height:6px;background:#e5e9f2;border-radius:4px;overflow:hidden}
  .pn-card .bar-cobertura > div{height:100%;background:linear-gradient(90deg,#3b82f6,#1e3a8a)}
  .pn-card .pct{text-align:right;font-size:11px;font-weight:600;color:var(--muted);margin-top:2px}

  /* Pagos Granos (POSICION GENERAL) */
  .pg-alert{padding:12px 16px;border-radius:8px;margin-bottom:10px;border-left:4px solid;display:flex;justify-content:space-between;align-items:center}
  .pg-alert .lbl{font-weight:700;font-size:13px}
  .pg-alert .det{font-size:12px;color:var(--muted);margin-top:2px}
  .pg-alert .tot{font-size:18px;font-weight:700;text-align:right}
  .pg-alert.vencido{background:#fef2f2;border-color:var(--red);color:#991b1b}
  .pg-alert.hoy{background:#fff7ed;border-color:var(--orange);color:#9a3412}
  .pg-alert.proximo7{background:#fefce8;border-color:#ca8a04;color:#854d0e}
  .pg-alert.proximo30{background:#eff6ff;border-color:var(--blue2);color:#1e40af}
  .pg-alert.sinfecha{background:#f8fafc;border-color:#94a3b8;color:#475569}

  /* Filtros sticky: quedan pegados arriba al hacer scroll dentro de la página. */
  #pg-filterbar{position:sticky;top:54px;z-index:20;box-shadow:0 4px 10px -6px rgba(15,23,42,.18)}

  #pg-tbl{font-size:12.5px}
  #pg-tbl thead th{background:var(--blue);color:#fff;padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.3px;text-align:left;position:sticky;top:0;z-index:2}
  #pg-tbl thead th.num{text-align:right}
  #pg-tbl tbody tr td{padding:4px 8px;border-bottom:1px solid var(--line)}
  #pg-tbl tbody tr.pagado td{background:#f0fdf4;color:#15803d;text-decoration:line-through}
  #pg-tbl tbody tr.vencido td{background:#fef2f2}
  #pg-tbl tbody tr.hoy td{background:#fff7ed}
  #pg-tbl tbody tr.proximo7 td{background:#fefce8}
  #pg-tbl tbody tr:hover td{background:#eff5ff}
  #pg-tbl tbody td input, #pg-tbl tbody td.editable{width:100%;border:1px solid transparent;background:transparent;padding:3px 5px;font-size:11.5px;font-family:inherit;color:inherit;border-radius:3px;outline:none}
  #pg-tbl tbody td input:hover, #pg-tbl tbody td.editable:hover{border-color:var(--line);background:#fff}
  #pg-tbl tbody td input:focus, #pg-tbl tbody td.editable:focus{border-color:var(--blue);background:#fff;box-shadow:0 0 0 2px rgba(59,130,246,.15)}
  #pg-tbl tbody td.num input{text-align:right;font-variant-numeric:tabular-nums}
  #pg-tbl tbody td.iva input{text-align:right;color:#b45309;font-weight:600}
  #pg-tbl tbody td.action{text-align:center;white-space:nowrap}
  #pg-tbl tbody td .row-btn{border:1px solid var(--line);background:#fff;cursor:pointer;padding:2px 7px;border-radius:4px;font-size:10.5px;color:var(--ink);margin:0 1px}
  #pg-tbl tbody td .row-btn.pay{background:#16a34a;color:#fff;border-color:#16a34a}
  #pg-tbl tbody td .row-btn.del{background:#fff;color:var(--red);border-color:#fecaca}
  #pg-tbl tbody td .row-btn:hover{filter:brightness(0.95)}
  #pg-tbl tfoot td{background:#eef2ff;font-weight:700;padding:8px 10px;font-size:13px;border-top:2px solid var(--blue);position:sticky;bottom:0}
  #pg-tbl tfoot td.num{text-align:right;color:var(--blue);font-variant-numeric:tabular-nums}

  /* Modo lectura (sin PAT configurado):
     Sin PAT los cambios igual se editan y guardan en localStorage; solo se pierde el auto-backup
     al repo. Por eso solo escondemos el boton viejo de config (reemplazado por "Administración"). */
  body.pg-reader #pg-autobackup-cfg{ display:none !important }
  #pg-reader-banner{display:none}
  body.pg-reader #pg-reader-banner{display:flex;align-items:center;gap:12px;padding:10px 14px;background:#fef3c7;border-left:4px solid #f59e0b;border-radius:8px;color:#854d0e;font-size:13px;margin-bottom:12px}
  body.pg-reader #pg-reader-banner .lbl{font-weight:700}

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

  /* ====== Mi Bandeja ====== */
  #mb-filterbar{position:sticky;top:54px;z-index:20;box-shadow:0 4px 10px -6px rgba(15,23,42,.18)}
  .mb-cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}
  .mb-card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px;position:relative;display:flex;flex-direction:column;gap:10px;transition:box-shadow .15s}
  .mb-card:hover{box-shadow:0 4px 14px -6px rgba(15,23,42,.12)}
  .mb-card.urg-alta{border-left:4px solid #dc2626;background:#fff7f7}
  .mb-card.urg-media{border-left:4px solid #f59e0b;background:#fffaf0}
  .mb-card.urg-baja{border-left:4px solid #94a3b8}
  .mb-card.estado-respondido{opacity:.55;background:#f0fdf4;border-left-color:#16a34a}
  .mb-card.estado-archivado{opacity:.4;background:#f8fafc}
  .mb-card-head{display:flex;align-items:start;justify-content:space-between;gap:8px}
  .mb-card-head h4{font-size:14px;line-height:1.35;margin:0;color:var(--ink);font-weight:600}
  .mb-card.estado-respondido h4{text-decoration:line-through}
  .mb-chip{display:inline-block;padding:2px 7px;border-radius:6px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.3px;white-space:nowrap}
  .mb-chip.urg-alta{background:#fee2e2;color:#991b1b}
  .mb-chip.urg-media{background:#fef3c7;color:#92400e}
  .mb-chip.urg-baja{background:#f1f5f9;color:#475569}
  .mb-chip.cat{background:#dbeafe;color:#1e40af}
  .mb-meta{font-size:11.5px;color:var(--muted);display:flex;flex-wrap:wrap;gap:8px;align-items:center}
  .mb-meta .sender{font-weight:600;color:#1e3a8a}
  .mb-section-lbl{font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
  .mb-card textarea{width:100%;border:1px solid var(--line);border-radius:6px;padding:7px 9px;font-family:inherit;font-size:12.5px;resize:vertical;min-height:46px;color:var(--ink);background:#fff;line-height:1.45}
  .mb-card textarea:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 2px rgba(59,130,246,.15)}
  .mb-card.readonly textarea{background:#f8fafc;border-color:transparent;pointer-events:none}
  .mb-actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:4px;padding-top:10px;border-top:1px dashed var(--line)}
  .mb-actions button{padding:5px 10px;border:1px solid var(--line);background:#fff;border-radius:6px;cursor:pointer;font-size:11.5px;color:var(--ink)}
  .mb-actions button:hover{border-color:var(--blue);color:var(--blue)}
  .mb-actions button.primary{background:#16a34a;color:#fff;border-color:#16a34a;font-weight:600}
  .mb-actions button.primary:hover{filter:brightness(.95);color:#fff}
  .mb-actions button.danger{color:#dc2626;border-color:#fecaca}
  .mb-actions button.danger:hover{background:#fef2f2;color:#dc2626}
  .mb-actions .outlook-link{margin-left:auto;font-size:11px;color:var(--blue);text-decoration:none;padding:5px 8px;border-radius:6px;border:1px solid var(--line)}
  .mb-actions .outlook-link:hover{background:#eff5ff}
  .mb-empty{padding:60px 20px;text-align:center;color:var(--muted);border:2px dashed var(--line);border-radius:12px;background:#fafbff;grid-column:1/-1}

  /* card fijada: indicador visual (cinta arriba a la derecha) */
  .mb-card.is-fijado{border-top:3px solid #8b5cf6}
  .mb-card.is-fijado::after{content:"📌";position:absolute;top:6px;right:10px;font-size:14px;opacity:.85}
  .mb-actions button.pinned{background:#ede9fe;color:#5b21b6;border-color:#c4b5fd;font-weight:600}
  .mb-actions button.pinned:hover{filter:brightness(.95);color:#5b21b6}

  /* gate visual: si el usuario no es propietario, ocultar botones de edicion */
  body.mb-readonly .mb-edit-only{display:none !important}
  body.mb-readonly #mb-readonly-banner{display:block}
</style>
</head>
<body>
<div class="app-shell">

  <aside class="sidebar" id="sidebar">
    <div class="brand">
      <div class="brand-logo">🌱</div>
      <div>
        <div class="brand-name">AGRONASAJA</div>
        <div class="brand-sub">Portal de Granos</div>
      </div>
    </div>
    <div class="campana">Resumen comercial</div>
    <nav class="nav">
      <div class="nav-group">Compra</div>
      <a class="nav-item" data-go-tab="compra" data-go-sub="cp-posicion" data-title="Compra · Posición General">Posición General</a>
      <a class="nav-item" data-go-tab="compra" data-go-sub="cp-financiera" data-title="Compra · Financiera">Financiera</a>
      <a class="nav-item" data-go-tab="compra" data-go-sub="cp-canjes" data-title="Compra · Canjes">Canjes</a>
      <a class="nav-item" data-go-tab="compra" data-go-sub="cp-cruce" data-title="Compra · Cruce Cliente × Comprador">Cruce Cliente × Comprador</a>
      <a class="nav-item" data-go-tab="compra" data-go-sub="pg-pagos" data-title="Compra · Proyectado Pagos Granos">Proyectado Pagos</a>
      <div class="nav-group">Venta</div>
      <a class="nav-item active" data-go-tab="venta" data-go-sub="posicion" data-title="Venta · Posición General">Posición General</a>
      <a class="nav-item" data-go-tab="venta" data-go-sub="financiera" data-title="Venta · Financiera">Financiera</a>
      <div class="nav-group">Posición General</div>
      <a class="nav-item" data-go-tab="posicion" data-go-sub="pn-granaria" data-title="Posición Granaria">Posición Granaria</a>
      <a class="nav-item" data-go-tab="posicion" data-go-sub="pn-financiera" data-title="Posición Financiera">Posición Financiera</a>
      <div class="nav-group nav-internal" style="display:none">Personal</div>
      <a class="nav-item nav-internal" style="display:none" data-go-tab="personal" data-go-sub="mb-bandeja" data-title="Mi Bandeja · Pendientes de Mail">📬 Mi Bandeja</a>
    </nav>
  </aside>

  <div class="main">
    <header class="topbar">
      <div style="display:flex;align-items:center;gap:12px">
        <button class="menu-toggle" id="menu-toggle" aria-label="Menú">☰</button>
        <div class="topbar-title" id="topbar-title">Venta · Posición General</div>
      </div>
      <div class="topbar-right">
        <div class="topbar-meta"><span class="dot"></span>Actualizado: __BUILD_TIME__</div>
        <button class="admin-pill" id="btn-admin">Administración</button>
        <a class="logout-btn" href="/logout">⤴ Salir</a>
      </div>
    </header>
    <div class="content">

  <div class="tabs">
    <div class="tab" data-tab="compra">COMPRA <span class="count" id="cnt-compra">0</span></div>
    <div class="tab active" data-tab="venta">VENTA <span class="count" id="cnt-venta">0</span></div>
    <div class="tab" data-tab="posicion">POSICIÓN GENERAL <span class="count" id="cnt-pos">0</span></div>
    <div class="tab nav-internal" data-tab="personal" style="display:none">PERSONAL <span class="count" id="cnt-personal">0</span></div>
  </div>

  <!-- ============ COMPRA ============ -->
  <div class="panel" data-panel="compra">

    <!-- SUB-TABS dentro de COMPRA -->
    <div class="subtabs">
      <button class="subtab active" data-sub="cp-posicion">Posición General</button>
      <button class="subtab" data-sub="cp-financiera">Financiera</button>
      <button class="subtab" data-sub="cp-canjes">Canjes</button>
      <button class="subtab" data-sub="cp-cruce">Cruce Cliente × Comprador</button>
      <button class="subtab" data-sub="pg-pagos">📅 Proyectado Pagos Granos</button>
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

    <!-- ========== SUB: CRUCE CLIENTE x COMPRADOR ========== -->
    <div class="subpanel" data-sub-panel="cp-cruce">

      <div class="section" style="background:linear-gradient(135deg,#eff6ff,#dbeafe);border:1px solid #93c5fd">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
          <div>
            <h3 style="margin:0">Cruce Clientes × Compradores</h3>
            <div style="font-size:12px;color:var(--muted);margin-top:4px">Tablero reactivo · Cliente: 3.25% comisión compra (excepciones editables) · Comprador: % variable por tabla editable · Precios USD/tn (TC histórico).</div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;align-items:center">
              <span id="cx-rango-chip" style="background:#fef3c7;color:#92400e;padding:3px 10px;border-radius:6px;font-size:11.5px;font-weight:600">01/01/2026 — —</span>
              <span style="background:#dcfce7;color:#15803d;padding:3px 10px;border-radius:6px;font-size:11.5px;font-weight:600">✓ Validado vs Liquidaciones</span>
              <span style="background:#dcfce7;color:#15803d;padding:3px 10px;border-radius:6px;font-size:11.5px;font-weight:600">Todo USD/tn</span>
              <button id="cx-reload" style="background:var(--green);color:#fff;border:1px solid var(--green);padding:5px 12px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer">🔄 Actualizar datos</button>
              <label style="display:flex;align-items:center;gap:4px;font-size:11.5px;color:var(--muted);margin-left:6px;cursor:pointer">
                <input type="checkbox" id="cx-hide-zeros" checked /> Ocultar compradores sin % cargado
              </label>
            </div>
            <div style="font-size:11px;color:var(--muted);margin-top:6px" id="cx-meta">— ops · 0 CTGs procesados</div>
          </div>
        </div>
      </div>

      <!-- Toggle Vista -->
      <div style="display:flex;gap:0;margin-bottom:14px">
        <button class="vista-toggle active" data-vista="kgcom" style="padding:10px 22px;border:1px solid #16a34a;background:#16a34a;color:#fff;border-radius:8px 0 0 8px;cursor:pointer;font-weight:600">Cruce Kg + Comisiones</button>
        <button class="vista-toggle" data-vista="precio" style="padding:10px 22px;border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:0 8px 8px 0;cursor:pointer;font-weight:500">Precio Compra vs Venta</button>
      </div>

      <!-- Filtros -->
      <div class="filterbar">
        <div><label>GRANO</label><select id="cx-grano"><option value="">Todos</option></select></div>
        <div><label>MES</label><select id="cx-mes"><option value="">Todos</option></select></div>
        <div><label>CLIENTE</label><select id="cx-cliente"><option value="">Todos</option></select></div>
        <div><label>COMPRADOR</label><select id="cx-comprador"><option value="">Todos</option></select></div>
        <div><label>ENTREGADOR</label><select id="cx-entregador"><option value="">Todos</option></select></div>
        <div><label>VENDEDOR</label><select id="cx-vendedor"><option value="">Todos</option></select></div>
        <div><label>BUSCAR CLIENTE</label><input type="text" id="cx-q" placeholder="texto…" /></div>
        <button class="clear" id="cx-clear">Limpiar filtros</button>
      </div>

      <!-- Tabla editable de % por Comprador -->
      <div class="section" style="background:#fff7ed;border:1px solid #fed7aa">
        <h3 style="display:flex;justify-content:space-between;align-items:center">
          <span>% Comisión por Comprador <span class="badge">editable · persiste en localStorage</span></span>
          <span>
            <button class="clear" id="cx-pct-defaults">↺ Cargar defaults</button>
            <button class="clear" id="cx-pct-clear" style="color:var(--red);border-color:#fecaca">🗑️ Limpiar todo</button>
          </span>
        </h3>
        <div id="cx-pct-grid" style="overflow:auto;max-height:480px"></div>
      </div>

      <!-- Comisión Cliente: default + excepciones -->
      <div class="section" style="background:#f0fdf4;border:1px solid #86efac">
        <h3>% Comisión Cliente <span class="badge">3.25% default · excepciones editables</span></h3>
        <div style="display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap">
          <div>
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">% DEFAULT</label>
            <input type="text" id="cx-cli-default" style="display:block;margin-top:4px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;width:100px;text-align:right;font-variant-numeric:tabular-nums" value="3.25" />
          </div>
          <div style="flex:1;min-width:300px">
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600">EXCEPCIONES (formato: CLIENTE = %, una por línea)</label>
            <textarea id="cx-cli-excs" style="display:block;margin-top:4px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;width:100%;height:60px;font-family:inherit;font-size:12px" placeholder="BENAYAS S.A. = 2.75&#10;BENAYAS MIGUEL ANGEL = 2.75"></textarea>
          </div>
        </div>
      </div>

      <!-- Cards Resumen por cultivo -->
      <div class="section">
        <h3>Resumen por cultivo (con filtros aplicados)</h3>
        <div class="grain-grid" id="cx-cultivos"></div>
      </div>

      <!-- Totales generales -->
      <div class="section">
        <h3>Totales generales</h3>
        <div class="kpis" id="cx-totales"></div>
      </div>

      <!-- Pendientes -->
      <div class="section" id="cx-pendientes-section" style="background:#fef3c7;border:1px dashed var(--orange);display:none">
        <h3 style="color:#a16207">⚠️ Pendientes <span class="badge">operaciones sin % comprador cargado</span></h3>
        <div id="cx-pendientes-content" style="font-size:13px;color:#a16207"></div>
      </div>

      <!-- Detalle Matrix Cliente x Comprador -->
      <div class="section">
        <h3>📋 Detalle <span class="badge" id="cx-matrix-meta">cruce de operaciones</span></h3>
        <div class="tbl-wrap" style="max-height:700px">
          <table id="cx-matrix">
            <thead id="cx-matrix-head"></thead>
            <tbody id="cx-matrix-body"></tbody>
            <tfoot id="cx-matrix-foot"></tfoot>
          </table>
        </div>
      </div>

      <!-- Ganancia Mensual -->
      <div class="section">
        <h3>📅 Ganancia Mensual <span class="badge">balance acumulado mes a mes</span></h3>
        <div class="tbl-wrap">
          <table id="cx-mensual">
            <thead><tr>
              <th>MES</th>
              <th class="num">Ops</th>
              <th class="num">Kg</th>
              <th class="num">Comisión Compra</th>
              <th class="num">Comisión Venta</th>
              <th class="num">Margen P.V−P.C</th>
              <th class="num">BALANCE USD</th>
            </tr></thead>
            <tbody id="cx-mensual-body"></tbody>
            <tfoot id="cx-mensual-foot"></tfoot>
          </table>
        </div>
      </div>

    </div>

    <!-- ========== PROYECTADO DE PAGOS GRANOS ========== -->
    <div class="subpanel" data-sub-panel="pg-pagos">

      <!-- Banner modo lectura -->
      <div id="pg-reader-banner">
        <span style="font-size:20px">⚠️</span>
        <div>
          <div class="lbl">Auto-backup desactivado</div>
          <div style="opacity:.85">Podés editar / agregar / borrar filas — los cambios se guardan en este navegador. Para que se backupeen al repo automáticamente, configurá el PAT en el botón <b>Administración</b> (arriba a la derecha).</div>
        </div>
      </div>

      <!-- Banners de alertas -->
      <div id="pg-alertas"></div>

      <!-- KPIs principales -->
      <div class="kpis" id="pg-kpis"></div>

      <!-- Toggle Pagado/Pendiente -->
      <div style="display:flex;gap:0;margin-bottom:14px">
        <button class="pago-toggle active" data-pago="" style="padding:10px 24px;border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:8px 0 0 8px;cursor:pointer;font-weight:600;font-size:13px">Todos <span style="font-size:11px;font-weight:500;opacity:.7" id="pg-toggle-all">(—)</span></button>
        <button class="pago-toggle" data-pago="pendiente" style="padding:10px 24px;border:1px solid var(--line);border-left:0;background:#fff;color:var(--ink);cursor:pointer;font-weight:500;font-size:13px">⏳ Pendientes <span style="font-size:11px;opacity:.7" id="pg-toggle-pend">(—)</span></button>
        <button class="pago-toggle" data-pago="pagado" style="padding:10px 24px;border:1px solid var(--line);border-left:0;background:#fff;color:var(--ink);border-radius:0 8px 8px 0;cursor:pointer;font-weight:500;font-size:13px">✓ Pagados <span style="font-size:11px;opacity:.7" id="pg-toggle-pago">(—)</span></button>
      </div>

      <!-- Filtros (sticky al hacer scroll) -->
      <div class="filterbar" id="pg-filterbar">
        <div><label>CLIENTE</label><select id="pg-cliente"><option value="">Todos</option></select></div>
        <div><label>ESTADO FECHA</label><select id="pg-estado">
          <option value="">Todos</option>
          <option value="vencido">Vencidos</option>
          <option value="hoy">Vencen HOY</option>
          <option value="proximo7">Próximos 7 días</option>
          <option value="proximo30">Próximos 30 días</option>
          <option value="futuro">Futuro lejano</option>
          <option value="sinfecha">Sin fecha</option>
        </select></div>
        <div><label>MES PAGO</label><select id="pg-mes"><option value="">Todos</option></select></div>
        <div><label>BUSCAR</label><input type="text" id="pg-q" placeholder="cliente…" /></div>
        <button class="clear" id="pg-clear">Limpiar</button>
        <button class="clear" id="pg-add-row" style="background:#16a34a;color:#fff;border-color:#16a34a">+ Agregar fila</button>
        <div class="count" id="pg-count">0 pagos</div>
      </div>

      <!-- Acciones -->
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;font-size:12px;align-items:center">
        <button class="clear" id="pg-backup" style="background:#16a34a;color:#fff;border-color:#16a34a;font-weight:600">💾 Backup ahora</button>
        <button class="clear" id="pg-autobackup-cfg">⚙️ Auto-backup</button>
        <span id="pg-autobackup-status" style="color:var(--muted);font-size:11.5px"></span>
        <span id="pg-backup-info" style="color:var(--muted)"></span>
        <button class="clear" id="pg-import">⬆️ Importar JSON</button>
        <input type="file" id="pg-import-file" accept="application/json" style="display:none" />
        <span style="margin-left:auto;color:var(--muted)" id="pg-storage-info"></span>
      </div>

      <!-- Modal config auto-backup -->
      <div id="pg-autobackup-modal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(15,23,42,0.6);z-index:1000;align-items:center;justify-content:center;padding:20px">
        <div style="background:#fff;border-radius:12px;padding:24px;max-width:560px;width:100%;max-height:90vh;overflow:auto">
          <h3 style="margin:0 0 12px;font-size:18px">⚙️ Configurar Auto-backup a GitHub</h3>
          <p style="font-size:13px;color:var(--muted);line-height:1.5">Cada vez que edites un pago, el tablero va a guardar automáticamente el JSON al repo (en <code>data/proyectado_pagos.json</code>) usando tu Personal Access Token (PAT).</p>
          <details style="margin:12px 0;font-size:13px"><summary style="cursor:pointer;font-weight:600;color:var(--blue)">¿Cómo crear el PAT? (click para ver pasos)</summary>
            <ol style="margin:10px 0 0 20px;line-height:1.7;color:var(--ink)">
              <li>Abrí <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" style="color:var(--blue)">github.com/settings/personal-access-tokens/new</a></li>
              <li><b>Token name</b>: <code>tablero-pagos</code></li>
              <li><b>Expiration</b>: 1 año (o más)</li>
              <li><b>Repository access</b>: <code>Only select repositories</code> → seleccioná <code>tablero-granos-finnegans</code></li>
              <li><b>Repository permissions</b> → buscá <code>Contents</code> → ponelo en <code>Read and write</code></li>
              <li>Click <b>Generate token</b> y copialo (empieza con <code>github_pat_</code>)</li>
            </ol>
          </details>
          <label style="display:block;margin-top:14px;font-size:11px;font-weight:600;text-transform:uppercase;color:var(--muted)">Pegá tu PAT acá:</label>
          <input type="password" id="pg-pat-input" placeholder="github_pat_..." style="width:100%;margin-top:4px;padding:9px 12px;border:1px solid var(--line);border-radius:6px;font-family:monospace;font-size:12px" />
          <div id="pg-pat-status" style="margin-top:8px;font-size:12px;min-height:18px"></div>
          <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:18px">
            <button class="clear" id="pg-pat-cancel">Cancelar</button>
            <button class="clear" id="pg-pat-disable" style="color:var(--red);border-color:#fecaca">Desactivar</button>
            <button class="clear" id="pg-pat-save" style="background:var(--green);color:#fff;border-color:var(--green);font-weight:600">Guardar y activar</button>
          </div>
        </div>
      </div>

      <!-- Banner backup overdue -->
      <div id="pg-backup-banner" style="display:none;padding:10px 14px;border-radius:8px;background:#fef3c7;border-left:4px solid var(--orange);color:#92400e;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center">
        <span id="pg-backup-banner-msg">⚠️ Hace 0 días que no hacés backup</span>
        <button class="clear" id="pg-backup-banner-btn" style="background:var(--orange);color:#fff;border-color:var(--orange);font-weight:600">💾 Descargar ahora</button>
      </div>

      <!-- Barra acciones masivas -->
      <div id="pg-massbar" style="display:none;background:#dbeafe;border:1px solid var(--blue2);border-radius:8px;padding:10px 14px;margin-bottom:10px;align-items:center;gap:10px;flex-wrap:wrap">
        <span style="font-weight:600;color:var(--blue)" id="pg-mass-count">0 seleccionados</span>
        <span style="color:var(--muted)">·</span>
        <button class="clear" id="pg-mass-pay" style="background:var(--green);color:#fff;border-color:var(--green);font-weight:600">✓ Marcar como Pagados</button>
        <button class="clear" id="pg-mass-unpay" style="border-color:var(--orange);color:var(--orange)">↺ Desmarcar Pagados</button>
        <button class="clear" id="pg-mass-del" style="border-color:#fecaca;color:var(--red)">🗑️ Borrar seleccionados</button>
        <span style="margin-left:auto"></span>
        <button class="clear" id="pg-mass-clear">Limpiar selección</button>
      </div>

      <!-- Tabla editable -->
      <div class="section">
        <h3>Proyectado de Pagos <span class="badge">click en celdas para editar · checkbox para seleccionar · cambios se guardan automáticamente</span></h3>
        <div class="tbl-wrap" style="max-height:700px">
          <table id="pg-tbl">
            <thead><tr id="pg-tbl-head"></tr></thead>
            <tbody id="pg-tbl-body"></tbody>
            <tfoot id="pg-tbl-foot"></tfoot>
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

  <!-- ============ POSICION GRANARIA ============ -->
  <div class="panel" data-panel="posicion">

    <!-- SUB-TABS Posicion General -->
    <div class="subtabs">
      <button class="subtab active" data-sub="pn-granaria">Posición Granaria</button>
      <button class="subtab" data-sub="pn-financiera">Posición Financiera</button>
    </div>

    <!-- ========== SUB: GRANARIA ========== -->
    <div class="subpanel active" data-sub-panel="pn-granaria">

    <!-- Header -->
    <div class="section" style="background:linear-gradient(135deg,#1e3a8a 0%,#3b82f6 100%);color:#fff;border:none">
      <h3 style="color:#fff;margin:0">📊 Posición Granaria · Agronasaja</h3>
      <div style="font-size:12px;opacity:.85;margin-top:4px">Análisis comercial · Compra · Venta · Cobertura — datos en vivo + carga manual de Planta y Producción</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
        <span id="pn-campana-chip" style="background:rgba(255,255,255,.18);padding:3px 10px;border-radius:6px;font-size:11.5px;font-weight:600">Campaña actual</span>
        <span style="background:rgba(34,197,94,.3);padding:3px 10px;border-radius:6px;font-size:11.5px;font-weight:600">Valores en Toneladas</span>
      </div>
    </div>

    <!-- Filtros -->
    <div class="filterbar">
      <div><label>CAMPAÑA</label><select id="pn-campana"><option value="">Todas</option></select></div>
      <div><label>EMPRESA</label><select id="pn-empresa"><option value="">Todas</option></select></div>
      <button class="clear" id="pn-clear">Limpiar</button>
      <span style="margin-left:auto;color:var(--muted);font-size:12px" id="pn-info"></span>
    </div>

    <!-- KPI cards por cultivo (auto) -->
    <div class="section">
      <h3>Posición por Cultivo <span class="badge" id="pn-cards-meta"></span></h3>
      <div id="pn-cards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px"></div>
    </div>

    <!-- Detalle tabla -->
    <div class="section">
      <h3>Análisis de Posición Granaria · Detalle por Producto <span class="badge">click en celdas de Planta/Producción para editar</span></h3>
      <div class="tbl-wrap" style="max-height:700px">
        <table id="pn-tabla" style="font-size:11.5px">
          <thead id="pn-thead"></thead>
          <tbody id="pn-tbody"></tbody>
          <tfoot id="pn-tfoot"></tfoot>
        </table>
      </div>
      <div style="margin-top:10px;font-size:11.5px;color:var(--muted)">
        💡 <strong>Cómo funciona</strong>: las columnas de <strong>Compra</strong> y <strong>Venta</strong> vienen automáticas de los contratos en Finnegans. Las columnas de <strong>Planta</strong> (Silo, Bolsas, Silo Bolsa) y <strong>Producción</strong> (Pend Cos, Cosechado, Campo Est) las cargás vos haciendo click en cada celda y se guardan en localStorage. El resto se calcula solo.
      </div>
    </div>

    </div><!-- /subpanel pn-granaria -->

    <!-- ========== SUB: FINANCIERA ========== -->
    <div class="subpanel" data-sub-panel="pn-financiera">

      <!-- Header -->
      <div class="section" style="background:linear-gradient(135deg,#1e3a8a 0%,#3b82f6 100%);color:#fff;border:none">
        <h3 style="color:#fff;margin:0">💰 Posición Financiera · Agronasaja</h3>
        <div style="font-size:12px;opacity:.85;margin-top:4px">Valorización de pendientes de liquidar + Stock físico — para cierre patrimonial</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
          <span id="fn-tc-chip" style="background:rgba(255,255,255,.18);padding:3px 10px;border-radius:6px;font-size:11.5px;font-weight:600">TC USD/ARS: —</span>
          <span id="fn-px-chip" style="background:rgba(255,255,255,.18);padding:3px 10px;border-radius:6px;font-size:11.5px;font-weight:600">Precios BCR (USD/tn): —</span>
        </div>
      </div>

      <!-- Filtros -->
      <div class="filterbar">
        <div><label>CAMPAÑA</label><select id="fn-camp"><option value="">Todas</option></select></div>
        <div><label>EMPRESA</label><select id="fn-emp"><option value="">Todas</option></select></div>
        <button class="clear" id="fn-clear">Limpiar</button>
        <span style="margin-left:auto;color:var(--muted);font-size:12px" id="fn-info"></span>
      </div>

      <!-- KPI totales -->
      <div class="kpis" id="fn-kpis"></div>

      <!-- 1. CONTRATO VENTA -->
      <div class="section">
        <h3><span style="background:#84cc16;color:#fff;padding:4px 14px;border-radius:6px;display:inline-block;font-size:14px">Contrato Venta</span> <span class="badge" id="fn-vta-meta"></span></h3>
        <div class="tbl-wrap">
          <table id="fn-tbl-vta" style="width:100%">
            <thead><tr>
              <th>Cultivo</th><th>Campaña</th>
              <th class="num">Ajustada</th><th class="num">Entregada</th><th class="num">Liquidada</th><th class="num">Pend de liq</th>
              <th style="background:#f8fafd"></th>
              <th class="num">Totales</th><th class="num">Total USD</th><th class="num">Total $</th>
            </tr></thead>
            <tbody></tbody>
            <tfoot></tfoot>
          </table>
        </div>
      </div>

      <!-- 2. CONTRATO COMPRA -->
      <div class="section">
        <h3><span style="background:#84cc16;color:#fff;padding:4px 14px;border-radius:6px;display:inline-block;font-size:14px">Contrato Compra</span> <span class="badge" id="fn-cpr-meta"></span></h3>
        <div class="tbl-wrap">
          <table id="fn-tbl-cpr" style="width:100%">
            <thead><tr>
              <th>Cultivo</th><th>Campaña</th>
              <th class="num">Ajustada</th><th class="num">Entregada</th><th class="num">Liquidada</th><th class="num">Pend de liq</th>
              <th style="background:#f8fafd"></th>
              <th class="num">Totales</th><th class="num">Total USD</th><th class="num">Total $</th>
            </tr></thead>
            <tbody></tbody>
            <tfoot></tfoot>
          </table>
        </div>
      </div>

      <!-- 3. STOCK -->
      <div class="section">
        <h3><span style="background:#84cc16;color:#fff;padding:4px 14px;border-radius:6px;display:inline-block;font-size:14px">Stock</span> <span class="badge">Toma los valores de Posición Granaria (Silo / Bolsas / Silo Bolsa); editá ahí para actualizar</span></h3>
        <div class="tbl-wrap">
          <table id="fn-tbl-stk" style="width:100%">
            <thead><tr>
              <th>Tipo</th><th>Campaña</th>
              <th class="num">Silo Bolsa</th><th class="num">Silo</th><th class="num">Bolsas</th><th class="num">Totales</th>
              <th style="background:#f8fafd"></th>
              <th class="num">Totales</th><th class="num">Total USD</th><th class="num">Total $</th>
            </tr></thead>
            <tbody></tbody>
            <tfoot></tfoot>
          </table>
        </div>
      </div>

      <!-- 4. PENDIENTES -->
      <div class="section">
        <h3><span style="background:#84cc16;color:#fff;padding:4px 14px;border-radius:6px;display:inline-block;font-size:14px">Pendientes</span> <span class="badge">Mercadería pendiente de liquidar que no está aplicada al sistema · Editá las celdas o agregá filas</span></h3>
        <div class="tbl-wrap">
          <table id="fn-tbl-pdt" style="width:100%">
            <thead><tr>
              <th>Tipo</th><th>Campaña</th>
              <th class="num">Silo Bolsa</th><th class="num">Silo</th><th class="num">En Tránsito</th><th class="num">Totales</th>
              <th style="background:#f8fafd"></th>
              <th class="num">Totales</th><th class="num">Total USD</th><th class="num">Total $</th>
              <th></th>
            </tr></thead>
            <tbody></tbody>
            <tfoot></tfoot>
          </table>
        </div>
        <div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="clear" id="fn-pdt-add">+ Agregar fila</button>
          <span style="font-size:11.5px;color:var(--muted)">Las celdas se guardan en localStorage. Click en cada celda numérica para editar.</span>
        </div>
      </div>

      <div style="margin-top:14px;padding:14px;background:#fff;border-radius:10px;border:1px solid var(--line);font-size:12.5px;color:var(--muted);line-height:1.55">
        💡 <strong>Cómo se calcula</strong>: <strong>Totales</strong> = pendiente de liquidar (tn). <strong>Total USD</strong> = Totales × precio BCR USD/tn del cultivo. <strong>Total $</strong> = Total USD × TC. Cultivos sin precio en BCR muestran "—" (podés editar el precio manualmente más adelante si querés).
      </div>

    </div><!-- /subpanel pn-financiera -->

  </div>

  <!-- ============ PERSONAL · Mi Bandeja ============ -->
  <div class="panel nav-internal" data-panel="personal" style="display:none">
    <div class="subtabs">
      <button class="subtab active" data-sub="mb-integrantes">🏢 Integrantes <span class="count" id="mb-cnt-int">0</span></button>
      <button class="subtab" data-sub="mb-generales">🌐 Generales <span class="count" id="mb-cnt-gen">0</span></button>
      <button class="subtab" data-sub="mb-fijados">📌 Fijados · Contratos a firmar <span class="count" id="mb-cnt-fij">0</span></button>
    </div>

    <div id="mb-readonly-banner" style="display:none;padding:12px 14px;border-radius:10px;background:#fef3c7;border-left:4px solid #f59e0b;color:#92400e;margin-bottom:14px;font-size:13px">
      ⚠️ <b>Modo lectura</b> · Estás viendo la bandeja personal de Ezequiel. Solo el propietario puede editar las notas.
    </div>

    <!-- KPIs compartidos -->
    <div class="kpis" id="mb-kpis"></div>

    <!-- Filtros sticky compartidos entre las 3 sub-pestañas -->
    <div class="filterbar" id="mb-filterbar">
      <div><label>URGENCIA</label><select id="mb-urgencia">
        <option value="">Todas</option>
        <option value="alta">⚠ Alta</option>
        <option value="media">● Media</option>
        <option value="baja">○ Baja</option>
      </select></div>
      <div><label>CATEGORÍA</label><select id="mb-categoria">
        <option value="">Todas</option>
        <option value="compra">🌾 Compra</option>
        <option value="venta">📦 Venta</option>
        <option value="logistica">🚚 Logística</option>
        <option value="liquidacion">💰 Liquidación</option>
        <option value="banco">🏦 Banco</option>
        <option value="otro">📂 Otro</option>
      </select></div>
      <div><label>ESTADO</label><select id="mb-estado">
        <option value="pendiente">Pendientes</option>
        <option value="">Todos</option>
        <option value="respondido">Respondidos</option>
        <option value="archivado">Archivados</option>
      </select></div>
      <div><label>BUSCAR</label><input type="text" id="mb-q" placeholder="remitente, asunto, nota…" /></div>
      <button class="clear" id="mb-clear">Limpiar</button>
      <button class="clear mb-edit-only" id="mb-add" style="background:#16a34a;color:#fff;border-color:#16a34a">+ Nueva nota</button>
      <div class="count" id="mb-count">0 / 0</div>
    </div>

    <!-- Acciones -->
    <div class="mb-edit-only" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;font-size:12px;align-items:center">
      <button class="clear" id="mb-backup" style="background:#16a34a;color:#fff;border-color:#16a34a;font-weight:600">💾 Backup ahora</button>
      <span id="mb-backup-info" style="color:var(--muted)"></span>
      <span style="margin-left:auto;color:var(--muted);font-size:11.5px" id="mb-storage-info"></span>
    </div>

    <!-- 3 SUB-PESTAÑAS: integrantes / generales / fijados -->
    <div class="subpanel active" data-sub-panel="mb-integrantes">
      <div id="mb-cards-integrantes" class="mb-cards-grid"></div>
    </div>
    <div class="subpanel" data-sub-panel="mb-generales">
      <div id="mb-cards-generales" class="mb-cards-grid"></div>
    </div>
    <div class="subpanel" data-sub-panel="mb-fijados">
      <div id="mb-cards-fijados" class="mb-cards-grid"></div>
    </div>

    <div style="margin-top:18px;padding:14px;background:#fff;border-radius:10px;border:1px solid var(--line);font-size:12.5px;color:var(--muted);line-height:1.55">
      💡 <strong>Cómo se usa</strong>: <b>Integrantes</b> son mails del equipo Agronasaja. <b>Generales</b> son mails de externos (corredores, clientes, bancos). <b>Fijados</b> reúne los que marcaste con 📌 — usalo para los <b>contratos a firmar</b>. Click 📌 en cualquier card para fijarla. Las notas se guardan en tu navegador y se sincronizan al repo si tenés PAT configurado.
    </div>

  </div><!-- /panel personal -->

    </div><!-- /.content -->
  </div><!-- /.main -->
</div><!-- /.app-shell -->

<script>
/* ============== FORZAR ACCESO POR EL PORTAL (Cloudflare Worker) ============== */
/* Si entran directo por GitHub Pages, los rebotamos al Worker para que pasen por el login */
(function(){
  try {
    var h = location.hostname || "";
    if (h.endsWith(".github.io")) {
      location.replace("https://tablero-agronasaja.ehussen.workers.dev/" + (location.search || ""));
    }
  } catch(e) {}
})();

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

/* ============== MENÚ LATERAL (sidebar) ============== */
/* Cada item dispara los .tab/.subtab existentes (que ya están ocultos por CSS),
   reutilizando toda la lógica de switching sin tocarla. */
const NAV_ITEMS = document.querySelectorAll('.nav-item');
NAV_ITEMS.forEach(item => {
  item.addEventListener('click', () => {
    const tab = item.dataset.goTab, sub = item.dataset.goSub;
    const tabEl = document.querySelector(`.tab[data-tab="${tab}"]`);
    if(tabEl) tabEl.click();
    if(sub){
      const stEl = document.querySelector(`.panel[data-panel="${tab}"] .subtab[data-sub="${sub}"]`);
      if(stEl) stEl.click();
    }
    NAV_ITEMS.forEach(x => x.classList.remove('active'));
    item.classList.add('active');
    const t = document.getElementById('topbar-title');
    if(t) t.textContent = item.dataset.title || item.textContent.trim();
    // cerrar el sidebar en mobile
    document.getElementById('sidebar').classList.remove('open');
  });
});

/* ============== ADMINISTRACIÓN (abre config de editor/PAT) ============== */
document.getElementById('btn-admin').addEventListener('click', () => {
  const m = document.getElementById('pg-autobackup-modal');
  if(!m) return;
  // El modal vive dentro del subpanel Proyectado (oculto si estás en otra sección);
  // lo movemos al body para que se vea desde cualquier pantalla.
  if(m.parentElement !== document.body) document.body.appendChild(m);
  m.style.display = 'flex';
  const pat = localStorage.getItem('tablero-granos-github-pat-v1') || '';
  const inp = document.getElementById('pg-pat-input'); if(inp) inp.value = pat;
  const stt = document.getElementById('pg-pat-status'); if(stt) stt.innerHTML = pat ? '✅ Auto-backup activo' : '';
});

/* ============== Toggle del menú en mobile ============== */
const _mt = document.getElementById('menu-toggle');
if(_mt) _mt.addEventListener('click', () => document.getElementById('sidebar').classList.toggle('open'));

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

function cjResetPrecios(fromBCR=true, doRender=true){
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
  // El render real ocurre en cjApply() al final del init (línea ~3193).
  // Evitar renderizar acá porque cjFiltered/CJ_TABLE_COLS aún no están
  // inicializados si esta función corre temprano (visitante sin localStorage).
  if(doRender) cjRender();
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
// (doRender=false: el render se hace luego en cjApply(); evita TDZ de cjFiltered)
if(CJ_PX.soja==null && CJ_PX.tc==null) cjResetPrecios(true, false);
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


/* ============================================================
   =====  CRUCE CLIENTE x COMPRADOR (sub-pestaña COMPRA) =====
   ============================================================ */

const CRUCES_RAW = PAYLOAD.cruces || [];

// Defaults de comisiones por comprador (de la planilla del usuario).
// Cada comprador tiene componentes: base, prod (produccion), vol (volatil),
// par (paritaria), otros, sell (sellado). El % TOTAL = suma de todos.
const CX_PCT_DEFAULTS = {
  "LDC ARGENTINA S.A.":                              {base: 0.5,                                            sell: 0},
  "ALLARIA AGRONEGOCIOS S.A.":                       {base: 1,             vol: 1},
  "CARGILL SOCIEDAD ANONIMA COMERCIAL E INDUSTRIAL": {base: 1,             vol: 1,  par: 0.3,               sell: 1.25},
  "COFCO INTERNATIONAL ARGENTINA S.A":                {base: 1, prod: 2,                                     sell: 0.7},
  "GEAR S A A I C F E I":                            {base: 1},
  "TOMAS HNOS Y CIA SA":                             {base: 0.5,           vol: 1},
  "GRANEROS Y ELEVADORES ARGENTINOS DE COLON SCL":   {base: 2},
  "AGRICULTORES FEDERADOS ARGENTINOS SOC COOP LTDA": {base: 2},
  "LA BRAGADENSE SA":                                {base: 1,             vol: 0},
  "ASOC DE COOPERATIVAS ARGENTINAS COOP LTDA":       {base: 1,                      par: 0.3,               sell: 1.25},
  "COOP DEFENSA DE AGRICULTORES LTDA":               {base: 1},
  "COOP AGROP DE LA VIOLETA LTDA":                   {base: 2.5},
  "EDUARDO BERAZA S. A.":                            {base: 1},
  "AGROTECNOLOGIA Y SERVICIOS SA":                   {base: 1},
  "COOP AGRICOLA LTDA LA UNION DE ALFONSO":          {base: 1.5},
  "LARTIRIGOYEN Y CIA SA":                           {base: 2},
  "ARGENTRADING S.A.":                               {base: 0.5,                                            sell: 0},
  "BUNGE ARGENTINA S.A.":                            {base: 0},
  "PUERTO ARROYO SECO SA":                           {base: 1,                      par: 0.4},
  "FYO ACOPIO S.A.":                                 {base: 0.5,                                            sell: 0},
  "RECTA AGRO SA":                                   {base: 0.75,                              otros: 0.5},
  "COMMODITIES S.A.":                                {base: 0.5},
  "J.H.B SAU":                                       {base: 0.5, prod: 1},
  "ACEITERA GENERAL DEHEZA S A":                     {base: 1},
};

const CX_PCT_KEY  = "tablero-granos-cx-pct-comprador-v2";  // v2 = componentes en lugar de un % unico
const CX_PCT_KEY_V1 = "tablero-granos-cx-pct-comprador-v1";
const CX_CLI_KEY  = "tablero-granos-cx-cli-comision-v1";
const CX_PCT_COMPS = ["base","prod","vol","par","otros","sell"];
const CX_PCT_LABELS = {base:"Base", prod:"Producción", vol:"Volátil", par:"Paritaria", otros:"Otros", sell:"Sellado"};
let CX_PCT = {};
let CX_CLI_DEFAULT = 3.25;
let CX_CLI_EXCS = {"BENAYAS S.A.": 2.75, "BENAYAS MIGUEL ANGEL": 2.75};
let cxVista = "kgcom";

// Cargar comisiones por comprador. Soporta migracion de v1 (un % unico) a v2 (componentes).
try {
  const savedV2 = JSON.parse(localStorage.getItem(CX_PCT_KEY) || "null");
  if(savedV2 && typeof savedV2 === "object"){
    CX_PCT = savedV2;
  } else {
    // Migracion v1 -> v2
    const savedV1 = JSON.parse(localStorage.getItem(CX_PCT_KEY_V1) || "null");
    if(savedV1 && typeof savedV1 === "object"){
      // En v1 cada valor era un numero. Lo trato como "base" en v2.
      for(const [name, pct] of Object.entries(savedV1)){
        CX_PCT[name] = (typeof pct === "object") ? pct : {base: pct};
      }
    }
  }
} catch(e){}
try {
  const saved = JSON.parse(localStorage.getItem(CX_CLI_KEY) || "null");
  if(saved){
    CX_CLI_DEFAULT = saved.def != null ? saved.def : 3.25;
    CX_CLI_EXCS    = saved.excs || CX_CLI_EXCS;
  }
} catch(e){}

// Si no hay nada guardado, usar defaults de la planilla
if(Object.keys(CX_PCT).length === 0) CX_PCT = JSON.parse(JSON.stringify(CX_PCT_DEFAULTS));

// Asegurar que cada entrada sea un objeto (no un numero crudo de v1)
for(const k of Object.keys(CX_PCT)){
  if(typeof CX_PCT[k] !== "object") CX_PCT[k] = {base: Number(CX_PCT[k])||0};
}

function cxSavePct(){ localStorage.setItem(CX_PCT_KEY, JSON.stringify(CX_PCT)); }

// Suma de componentes de un comprador. Retorna null si no esta cargado.
function cxSumComps(obj){
  if(!obj) return 0;
  return CX_PCT_COMPS.reduce((s,c) => s + (Number(obj[c])||0), 0);
}
function cxSaveCli(){ localStorage.setItem(CX_CLI_KEY, JSON.stringify({def: CX_CLI_DEFAULT, excs: CX_CLI_EXCS})); }

function cxClienteUpper(s){ return (s||"").trim().toUpperCase(); }
function cxGetPctCliente(cliente){
  // buscar match case-insensitive en excepciones
  const k = cxClienteUpper(cliente);
  for(const [name, pct] of Object.entries(CX_CLI_EXCS)){
    if(cxClienteUpper(name) === k) return pct;
  }
  return CX_CLI_DEFAULT;
}
function cxGetPctObj(comprador){
  if(comprador == null) return null;
  if(CX_PCT[comprador] != null) return CX_PCT[comprador];
  const k = cxClienteUpper(comprador);
  for(const [name, obj] of Object.entries(CX_PCT)){
    if(cxClienteUpper(name) === k) return obj;
  }
  return null;
}
function cxGetPctComprador(comprador){
  const obj = cxGetPctObj(comprador);
  if(!obj) return null;
  const sum = cxSumComps(obj);
  // si todos los componentes son 0/null, lo devolvemos como null (pendiente)
  // EXCEPTO si explicitamente alguno fue definido como 0 (caso BUNGE)
  const hasAny = CX_PCT_COMPS.some(c => obj[c] != null);
  if(!hasAny) return null;
  return sum;
}

// Buscar precio del contrato (compra o venta)
// El traslado trae NOMBRECONTRATO = "CONT-CPRA-GRA - 878"
// El dataset de Resumen tiene:
//   contrato: "CONT-CPRA-GRA - 878 (Grano Trigo) - PESOS"  (con descripción)
//   nombre:   "CONT-CPRA-GRA - 878"                        (puro)
// Por eso indexamos por 'nombre' que es lo que matchea
const CX_CONTRATOS_COMPRA_IDX = {};
(DATA_CP || []).forEach(c => {
  if(c.nombre) CX_CONTRATOS_COMPRA_IDX[c.nombre] = c;
});
const CX_CONTRATOS_VENTA_IDX = {};
(DATA || []).forEach(c => {   // DATA es venta (pilot)
  if(c.nombre) CX_CONTRATOS_VENTA_IDX[c.nombre] = c;
});

// Prioridad: precio LIQUIDADO (final/real) > precio PROMEDIO FIJADO
function cxPrecioCompra(nombreContrato){
  const c = CX_CONTRATOS_COMPRA_IDX[nombreContrato];
  if(!c) return null;
  return Number(c.precioliquidado) || Number(c.preciopromediofijado) || null;
}
function cxPrecioVenta(nombreContrato){
  const c = CX_CONTRATOS_VENTA_IDX[nombreContrato];
  if(!c) return null;
  return Number(c.precioliquidado) || Number(c.preciopromediofijado) || null;
}

function cxMesISO(fechaStr){
  // fecha dd-MM-yyyy o yyyy-MM-dd
  if(!fechaStr) return null;
  const m1 = String(fechaStr).match(/^(\d{2})-(\d{2})-(\d{4})/);
  if(m1) return `${m1[3]}-${m1[2]}`;
  const m2 = String(fechaStr).match(/^(\d{4})-(\d{2})/);
  if(m2) return `${m2[1]}-${m2[2]}`;
  return null;
}

// Enriquecer cruces con precios + comisiones
function cxBuildOps(){
  return CRUCES_RAW.map(c => {
    const kg = c.kg || 0;
    const tn = kg/1000;
    const precioC = cxPrecioCompra(c.contrato_compra);
    const precioV = cxPrecioVenta(c.contrato_venta);
    const pctC = cxGetPctCliente(c.cliente);
    const pctV = cxGetPctComprador(c.comprador);
    const comComp = (precioC != null) ? tn * precioC * (pctC/100) : 0;
    const comVent = (precioV != null && pctV != null) ? tn * precioV * (pctV/100) : 0;
    const margenPVPC = (precioV != null && precioC != null) ? precioV - precioC : null;
    const balance = comComp - comVent;
    // Vendedor: del contrato de venta (es el vendedor de Agronasaja que cerró la operación)
    const ventaCto = CX_CONTRATOS_VENTA_IDX[c.contrato_venta];
    const vendedor = (ventaCto && (ventaCto.vendedor || ventaCto.vendedornombre)) || null;
    return {
      ...c,
      mes: cxMesISO(c.fecha),
      tn,
      precioC, precioV,
      pctC, pctV,
      comComp, comVent,
      margenPVPC,
      vendedor,
      balance,
      pendiente: pctV == null,
    };
  });
}

const MES_LABELS = {
  "01": "Ene", "02": "Feb", "03": "Mar", "04": "Abr", "05": "May", "06": "Jun",
  "07": "Jul", "08": "Ago", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dic",
};
function mesNice(yyyymm){
  if(!yyyymm) return "—";
  const [y,m] = yyyymm.split("-");
  return `${MES_LABELS[m] || m} ${y}`;
}

function cxInitFilters(){
  const ops = cxBuildOps();
  const fillSel = (id, vals, defLbl) => {
    const sel = document.getElementById(id);
    sel.innerHTML = `<option value="">${defLbl}</option>` +
      vals.filter(v => v).sort().map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  };
  fillSel("cx-grano",      [...new Set(ops.map(o=>o.grano))],         "Todos");
  fillSel("cx-cliente",    [...new Set(ops.map(o=>o.cliente))],       "Todos");
  fillSel("cx-comprador",  [...new Set(ops.map(o=>o.comprador))],     "Todos");
  fillSel("cx-entregador", [...new Set(ops.map(o=>o.entregador))],    "Todos");
  fillSel("cx-vendedor",   [...new Set(ops.map(o=>o.vendedor))],      "Todos");
  // meses
  const meses = [...new Set(ops.map(o=>o.mes).filter(Boolean))].sort();
  document.getElementById("cx-mes").innerHTML = '<option value="">Todos</option>' +
    meses.map(m => `<option value="${m}">${mesNice(m)}</option>`).join("");

  ["cx-grano","cx-mes","cx-cliente","cx-comprador","cx-entregador","cx-vendedor"].forEach(id =>
    document.getElementById(id).addEventListener("change", cxRender)
  );
  document.getElementById("cx-q").addEventListener("input", cxRender);
  document.getElementById("cx-clear").addEventListener("click", () => {
    ["cx-grano","cx-mes","cx-cliente","cx-comprador","cx-entregador","cx-vendedor","cx-q"].forEach(id => {
      document.getElementById(id).value = "";
    });
    cxRender();
  });

  // Toggle vistas
  document.querySelectorAll(".vista-toggle").forEach(b => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".vista-toggle").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      cxVista = b.dataset.vista;
      cxRender();
    });
  });
}

function cxRenderPctGrid(){
  const compradoresEnOps = [...new Set(CRUCES_RAW.map(c => c.comprador).filter(Boolean))];
  const todos = new Set([
    ...Object.keys(CX_PCT_DEFAULTS),
    ...Object.keys(CX_PCT),
    ...compradoresEnOps,
  ]);
  const list = [...todos].sort();
  const grid = document.getElementById("cx-pct-grid");
  // Reemplazo grid por una tabla con columnas Base/Prod/Vol/Par/Otros/Sellado/TOTAL
  grid.style.display = "block";
  let html = `<table style="width:100%;border-collapse:collapse;font-size:11.5px">
    <thead><tr style="background:#fff7ed;border-bottom:2px solid #fed7aa">
      <th style="text-align:left;padding:6px 8px;font-size:10.5px;text-transform:uppercase;letter-spacing:.3px;color:#9a3412">Comprador</th>
      ${CX_PCT_COMPS.map(c => `<th style="text-align:right;padding:6px 4px;font-size:10.5px;text-transform:uppercase;letter-spacing:.3px;color:#9a3412;min-width:60px">${CX_PCT_LABELS[c]}</th>`).join("")}
      <th style="text-align:right;padding:6px 8px;font-size:10.5px;text-transform:uppercase;letter-spacing:.3px;color:var(--blue);min-width:70px">% TOTAL</th>
    </tr></thead>
    <tbody>`;
  for(const name of list){
    const obj = CX_PCT[name] || {};
    const inOps = compradoresEnOps.includes(name);
    const total = cxSumComps(obj);
    const hasAny = CX_PCT_COMPS.some(c => obj[c] != null);
    const totalBg = !hasAny ? "#fee2e2" : (total === 0 ? "#f3f4f6" : "#dcfce7");
    const totalCol = !hasAny ? "var(--red)" : (total === 0 ? "var(--muted)" : "var(--green)");
    html += `<tr style="border-bottom:1px solid var(--line)${inOps?'':';opacity:0.7'}">
      <td style="padding:4px 8px;font-size:11.5px;font-weight:${inOps?'600':'400'}" title="${escapeHtml(name)}">${escapeHtml(name.length>34?name.slice(0,34)+'…':name)}</td>
      ${CX_PCT_COMPS.map(c => {
        const v = obj[c];
        const show = (v == null || v === "") ? "" : String(v).replace(".",",");
        return `<td style="padding:2px"><input type="text" data-comprador="${escapeHtml(name)}" data-comp="${c}" class="cx-pct-input" value="${show}" placeholder="—" style="width:100%;padding:3px 5px;border:1px solid var(--line);border-radius:3px;text-align:right;font-size:11px;font-variant-numeric:tabular-nums;background:${v==null||v===''?'#fff':'#fffbeb'}"/></td>`;
      }).join("")}
      <td style="padding:4px 8px;text-align:right;font-weight:700;font-size:12px;color:${totalCol};background:${totalBg};border-radius:4px">${hasAny ? total.toLocaleString("es-AR",{maximumFractionDigits:2}) + "%" : "—"}</td>
    </tr>`;
  }
  html += `</tbody></table>`;
  grid.innerHTML = html;

  document.querySelectorAll(".cx-pct-input").forEach(inp => {
    inp.addEventListener("blur", () => {
      const name = inp.dataset.comprador;
      const comp = inp.dataset.comp;
      const raw = (inp.value||"").trim().replace(",",".");
      if(!CX_PCT[name]) CX_PCT[name] = {};
      if(raw === ""){
        delete CX_PCT[name][comp];
        if(Object.keys(CX_PCT[name]).length === 0) delete CX_PCT[name];
      } else {
        const v = parseFloat(raw);
        if(isNaN(v)) return;
        CX_PCT[name][comp] = v;
      }
      cxSavePct();
      cxRender();
    });
    inp.addEventListener("keydown", e => { if(e.key === "Enter") inp.blur(); });
  });
}

// Filtros + cruces + matrix render
function cxApplyFilters(ops){
  const g = document.getElementById("cx-grano").value;
  const mes = document.getElementById("cx-mes").value;
  const cli = document.getElementById("cx-cliente").value;
  const cmp = document.getElementById("cx-comprador").value;
  const ent = document.getElementById("cx-entregador").value;
  const vnd = document.getElementById("cx-vendedor").value;
  const q = (document.getElementById("cx-q").value||"").toLowerCase().trim();
  return ops.filter(o => {
    if(g && o.grano !== g) return false;
    if(mes && o.mes !== mes) return false;
    if(cli && o.cliente !== cli) return false;
    if(cmp && o.comprador !== cmp) return false;
    if(ent && o.entregador !== ent) return false;
    if(vnd && o.vendedor !== vnd) return false;
    if(q && !(o.cliente||"").toLowerCase().includes(q)) return false;
    return true;
  });
}

function cxRender(){
  // refrescar inputs de cliente
  document.getElementById("cx-cli-default").value = CX_CLI_DEFAULT;
  document.getElementById("cx-cli-excs").value = Object.entries(CX_CLI_EXCS).map(([n,p]) => `${n} = ${p}`).join("\n");

  cxRenderPctGrid();

  const allOps = cxBuildOps();
  const ops = cxApplyFilters(allOps);
  document.getElementById("cx-meta").textContent = `${ops.length} ops · ${allOps.length} CTGs procesados`;

  // Rango de fechas dinámico: desde 01/01/2026 hasta la fecha máxima de las ops
  const fechas = allOps.map(o => o.fecha).filter(Boolean);
  if(fechas.length){
    // las fechas vienen en dd-MM-yyyy, las convierto a Date
    const toDate = s => {
      const m = String(s).match(/^(\d{2})-(\d{2})-(\d{4})/);
      return m ? new Date(`${m[3]}-${m[2]}-${m[1]}`) : null;
    };
    const validDates = fechas.map(toDate).filter(Boolean);
    if(validDates.length){
      const max = new Date(Math.max(...validDates.map(d => d.getTime())));
      const dd = String(max.getDate()).padStart(2,'0');
      const mm = String(max.getMonth()+1).padStart(2,'0');
      const yy = max.getFullYear();
      document.getElementById('cx-rango-chip').textContent = `01/01/2026 — ${dd}/${mm}/${yy}`;
    }
  }

  // Pendientes (% comprador no cargado)
  const pendientes = ops.filter(o => o.pendiente);
  if(pendientes.length){
    const compFaltan = [...new Set(pendientes.map(o => o.comprador).filter(Boolean))];
    document.getElementById("cx-pendientes-section").style.display = "block";
    document.getElementById("cx-pendientes-content").innerHTML =
      `<strong>${pendientes.length} ops</strong>${compFaltan.length ? ` · Falta % de: ${compFaltan.map(c=>escapeHtml(c)).join(", ")}` : ""}`;
  } else {
    document.getElementById("cx-pendientes-section").style.display = "none";
  }

  // Resumen por cultivo
  const byCult = {};
  ops.forEach(o => {
    const g = o.grano || "—";
    if(!byCult[g]) byCult[g] = {cnt:0, kg:0, comComp:0, comVent:0, margenTot:0};
    byCult[g].cnt++;
    byCult[g].kg += o.kg || 0;
    byCult[g].comComp += o.comComp || 0;
    byCult[g].comVent += o.comVent || 0;
    if(o.margenPVPC != null) byCult[g].margenTot += (o.margenPVPC * (o.tn||0));
  });
  const cultivos = Object.entries(byCult).sort((a,b) => b[1].kg - a[1].kg);
  document.getElementById("cx-cultivos").innerHTML = cultivos.map(([g,v]) => {
    const bal = v.comComp - v.comVent;
    return `<div class="cult-card ${grainClass(g)}">
      <div class="name"><span>${g}</span><span class="cnt">${v.cnt} ops</span></div>
      <div class="r"><span class="k">Kg</span><span>${fmt.num(v.kg)}</span></div>
      <div class="r"><span class="k">Comisión Compra</span><span style="color:#a16207;font-weight:600">$${fmt.num2(v.comComp)}</span></div>
      <div class="r"><span class="k">Comisión Venta</span><span style="color:#be185d;font-weight:600">$${fmt.num2(v.comVent)}</span></div>
      <div class="r"><span class="k">Margen P.V−P.C</span><span class="${v.margenTot>=0?'pos':'neg'}">$${fmt.num2(v.margenTot)}</span></div>
      <div class="bal"><span>BALANCE</span><span style="color:var(--green);font-weight:700">$${fmt.num2(bal)}</span></div>
    </div>`;
  }).join("") || '<div class="placeholder">Sin operaciones para los filtros aplicados</div>';

  // Totales generales
  const tot = {comComp:0, comVent:0, balance:0, margenTot:0, ops:ops.length, kg:0,
               clientes:new Set(), compradores:new Set(), entregadores:new Set()};
  ops.forEach(o => {
    tot.comComp += o.comComp || 0;
    tot.comVent += o.comVent || 0;
    tot.kg += o.kg || 0;
    if(o.margenPVPC != null) tot.margenTot += (o.margenPVPC * (o.tn||0));
    if(o.cliente) tot.clientes.add(o.cliente);
    if(o.comprador) tot.compradores.add(o.comprador);
    if(o.entregador) tot.entregadores.add(o.entregador);
  });
  tot.balance = tot.comComp - tot.comVent;
  document.getElementById("cx-totales").innerHTML = `
    <div class="kpi yellow"><div class="lbl">Comisión Compra</div><div class="val">$${fmt.num2(tot.comComp)}</div></div>
    <div class="kpi pink"><div class="lbl">Comisión Venta</div><div class="val">$${fmt.num2(tot.comVent)}</div></div>
    <div class="kpi green"><div class="lbl">Balance USD</div><div class="val">$${fmt.num2(tot.balance)}</div></div>
    <div class="kpi"><div class="lbl">Margen P.V−P.C</div><div class="val">$${fmt.num2(tot.margenTot)}</div></div>
    <div class="kpi"><div class="lbl">Operaciones</div><div class="val">${fmt.int(tot.ops)}</div></div>
    <div class="kpi"><div class="lbl">Kg</div><div class="val">${fmt.num(tot.kg)}</div></div>
    <div class="kpi"><div class="lbl">Clientes</div><div class="val">${tot.clientes.size}</div></div>
    <div class="kpi"><div class="lbl">Compradores</div><div class="val">${tot.compradores.size}</div></div>
    <div class="kpi"><div class="lbl">Entregadores</div><div class="val">${tot.entregadores.size}</div></div>
  `;

  // Matrix
  cxRenderMatrix(ops);

  // Ganancia mensual
  cxRenderMensual(ops);
}

function cxRenderMensual(ops){
  // Agrupar ops por mes (yyyy-mm)
  const byMes = {};
  ops.forEach(o => {
    const m = o.mes;
    if(!m) return;
    if(!byMes[m]) byMes[m] = {ops:0, kg:0, comComp:0, comVent:0, margenTot:0};
    byMes[m].ops++;
    byMes[m].kg += o.kg || 0;
    byMes[m].comComp += o.comComp || 0;
    byMes[m].comVent += o.comVent || 0;
    if(o.margenPVPC != null) byMes[m].margenTot += (o.margenPVPC * (o.tn||0));
  });
  const meses = Object.keys(byMes).sort();   // yyyy-mm orden asc
  const body = meses.map(m => {
    const v = byMes[m];
    const balance = v.comComp - v.comVent;
    return `<tr>
      <td>${mesNice(m)}</td>
      <td class="num">${fmt.int(v.ops)}</td>
      <td class="num">${fmt.num(v.kg)}</td>
      <td class="num" style="color:#a16207">$${fmt.num2(v.comComp)}</td>
      <td class="num" style="color:#be185d">$${fmt.num2(v.comVent)}</td>
      <td class="num" style="color:${v.margenTot>=0?'var(--green)':'var(--red)'}">$${fmt.num2(v.margenTot)}</td>
      <td class="num" style="font-weight:700;color:var(--green)">$${fmt.num2(balance)}</td>
    </tr>`;
  }).join("") || '<tr><td colspan="7" style="text-align:center;padding:20px;color:var(--muted)">Sin operaciones</td></tr>';
  document.getElementById("cx-mensual-body").innerHTML = body;

  // Footer totales
  const tot = meses.reduce((acc, m) => {
    const v = byMes[m];
    acc.ops += v.ops; acc.kg += v.kg; acc.comComp += v.comComp;
    acc.comVent += v.comVent; acc.margenTot += v.margenTot;
    return acc;
  }, {ops:0, kg:0, comComp:0, comVent:0, margenTot:0});
  const balance = tot.comComp - tot.comVent;
  document.getElementById("cx-mensual-foot").innerHTML = `<tr>
    <td><strong>TOTAL (${meses.length} ${meses.length===1?'mes':'meses'})</strong></td>
    <td class="num"><strong>${fmt.int(tot.ops)}</strong></td>
    <td class="num"><strong>${fmt.num(tot.kg)}</strong></td>
    <td class="num" style="color:#a16207"><strong>$${fmt.num2(tot.comComp)}</strong></td>
    <td class="num" style="color:#be185d"><strong>$${fmt.num2(tot.comVent)}</strong></td>
    <td class="num" style="color:${tot.margenTot>=0?'var(--green)':'var(--red)'}"><strong>$${fmt.num2(tot.margenTot)}</strong></td>
    <td class="num" style="font-weight:700;color:var(--green)"><strong>$${fmt.num2(balance)}</strong></td>
  </tr>`;
}

function cxRenderMatrix(ops){
  // Agrupar: rows = cliente, cols = comprador
  const clientes = [...new Set(ops.map(o => o.cliente).filter(Boolean))].sort();
  let compradores = [...new Set(ops.map(o => o.comprador).filter(Boolean))].sort();

  // Si el checkbox "Ocultar compradores sin %" está activo, filtramos los que no tengan % cargado o tengan 0%
  const hideZeros = document.getElementById("cx-hide-zeros");
  if(hideZeros && hideZeros.checked){
    compradores = compradores.filter(cp => {
      const pct = cxGetPctComprador(cp);
      return pct != null && pct > 0;
    });
  }

  document.getElementById("cx-matrix-meta").textContent =
    `${clientes.length} clientes × ${compradores.length} compradores · vista: ${cxVista === "kgcom" ? "Kg + Comisiones" : "Precio Compra vs Venta"}`;

  // matriz de datos: cliente -> comprador -> {kg, precioVTot/cnt, precioCCli (1ero), ops:[...]}
  const matrix = {};
  const precioCliente = {};   // cliente -> precio compra (promedio ponderado por kg)
  const precioCpyKg = {};     // cliente -> {sumPxKg, sumKg}
  clientes.forEach(c => { matrix[c] = {}; precioCpyKg[c] = {sumPxKg:0, sumKg:0}; });

  ops.forEach(o => {
    if(!o.cliente || !o.comprador) return;
    if(!matrix[o.cliente][o.comprador]) matrix[o.cliente][o.comprador] = {kg:0, ops:0, sumPVxKg:0, sumKgPV:0};
    const cell = matrix[o.cliente][o.comprador];
    cell.kg += o.kg||0;
    cell.ops++;
    if(o.precioV != null && o.kg){ cell.sumPVxKg += o.precioV*o.kg; cell.sumKgPV += o.kg; }
    if(o.precioC != null && o.kg){
      precioCpyKg[o.cliente].sumPxKg += o.precioC*o.kg;
      precioCpyKg[o.cliente].sumKg += o.kg;
    }
  });
  clientes.forEach(c => {
    const t = precioCpyKg[c];
    precioCliente[c] = t.sumKg > 0 ? t.sumPxKg / t.sumKg : null;
  });

  // Header
  let head = '<tr><th class="cx-cliente-h">Cliente</th><th class="cx-pct-cli">% Cliente</th><th class="cx-precio-cli">Precio<br/>Compra<br/>USD/tn</th>';
  compradores.forEach(cp => {
    const pct = cxGetPctComprador(cp);
    const pctTxt = pct != null ? `${fmt.num2(pct)}%` : "—";
    const pctCls = pct === 0 || pct == null ? "zero" : "";
    const short = cp.length > 18 ? cp.slice(0,18) + "…" : cp;
    head += `<th class="cx-comprador" title="${escapeHtml(cp)}">${escapeHtml(short)}<span class="pct ${pctCls}">${pctTxt}</span></th>`;
  });
  head += '</tr>';
  document.getElementById("cx-matrix-head").innerHTML = head;

  // Body
  let body = "";
  clientes.forEach(cli => {
    const pctCli = cxGetPctCliente(cli);
    const precCli = precioCliente[cli];
    body += `<tr><td class="cx-cli-name" title="${escapeHtml(cli)}">${escapeHtml(cli)}</td>`;
    body += `<td class="cx-pct-cell">${fmt.num2(pctCli)}%</td>`;
    body += `<td class="cx-precio-cell">${precCli != null ? fmt.num2(precCli) : '—'}</td>`;
    compradores.forEach(cp => {
      const cell = matrix[cli][cp];
      if(!cell || cell.kg === 0){
        body += '<td class="cx-empty">—</td>';
      } else if(cxVista === "kgcom") {
        body += `<td>${fmt.num(cell.kg)}</td>`;
      } else {
        const precioCpr = cell.sumKgPV > 0 ? cell.sumPVxKg / cell.sumKgPV : null;
        const diff = (precioCpr != null && precCli != null) ? (precioCpr - precCli) : null;
        const diffTxt = diff != null ? `<span style="font-size:9px;color:${diff>=0?'var(--green)':'var(--red)'}">${diff>=0?'+':''}${fmt.num2(diff)}</span>` : '';
        body += `<td>${precioCpr != null ? fmt.num2(precioCpr) : '—'}<br/><span style="font-size:9px;color:var(--muted)">${fmt.num(cell.kg)} kg</span>${diffTxt ? '<br/>' + diffTxt : ''}</td>`;
      }
    });
    body += '</tr>';
  });
  document.getElementById("cx-matrix-body").innerHTML = body || '<tr><td colspan="99" style="padding:30px;text-align:center;color:var(--muted)">Sin datos</td></tr>';

  // Footer (totales por comprador)
  let foot = '<tr><td class="cx-foot-lbl">TOTAL</td><td></td><td></td>';
  compradores.forEach(cp => {
    let total = 0;
    clientes.forEach(cli => {
      const cell = matrix[cli][cp];
      if(cell) total += cell.kg || 0;
    });
    foot += `<td>${total > 0 ? fmt.num(total) : '—'}</td>`;
  });
  foot += '</tr>';
  document.getElementById("cx-matrix-foot").innerHTML = foot;
}

// Inputs de comision cliente
document.getElementById("cx-cli-default").addEventListener("blur", () => {
  const v = parseFloat(document.getElementById("cx-cli-default").value.replace(",","."));
  if(!isNaN(v)){ CX_CLI_DEFAULT = v; cxSaveCli(); cxRender(); }
});
document.getElementById("cx-cli-excs").addEventListener("blur", () => {
  const txt = document.getElementById("cx-cli-excs").value;
  const newExcs = {};
  txt.split("\n").forEach(line => {
    const m = line.match(/^(.+?)\s*=\s*([-\d.,]+)/);
    if(m){
      const name = m[1].trim();
      const v = parseFloat(m[2].replace(",","."));
      if(!isNaN(v)) newExcs[name] = v;
    }
  });
  CX_CLI_EXCS = newExcs;
  cxSaveCli();
  cxRender();
});

// Defaults / Clear de % comprador
document.getElementById("cx-pct-defaults").addEventListener("click", () => {
  CX_PCT = {...CX_PCT_DEFAULTS};
  cxSavePct();
  cxRender();
});
document.getElementById("cx-pct-clear").addEventListener("click", () => {
  if(confirm("¿Limpiar TODOS los % de comprador?")){
    CX_PCT = {};
    cxSavePct();
    cxRender();
  }
});

cxInitFilters();
cxRender();

// Listeners adicionales: ocultar 0% + botón Actualizar datos
document.getElementById("cx-hide-zeros").addEventListener("change", cxRender);
document.getElementById("cx-reload").addEventListener("click", () => location.reload());


/* ============================================================
   =====  PROYECTADO PAGOS GRANOS (POSICION GENERAL)  ========
   ============================================================ */

const PG_KEY = "tablero-granos-pagos-proyectados-v1";
const PG_INICIALES = PAYLOAD.pagos_iniciales || [];
const PG_RAW_URL = "https://raw.githubusercontent.com/ehussenn/tablero-granos-finnegans/main/data/proyectado_pagos.json";

// Editor = tiene PAT configurado. Lector = no.
function pgIsEditor(){ return !!localStorage.getItem("tablero-granos-github-pat-v1"); }

let PG_DATA = [];
let PG_LOADED = false;

async function pgLoadInitial(){
  if(pgIsEditor()){
    try {
      const saved = JSON.parse(localStorage.getItem(PG_KEY) || "null");
      if(Array.isArray(saved)){ PG_DATA = saved; PG_LOADED = true; return; }
    } catch(e){}
    PG_DATA = JSON.parse(JSON.stringify(PG_INICIALES));
  } else {
    // Lector: trae el JSON mas fresco del repo (no usa localStorage)
    try {
      const resp = await fetch(PG_RAW_URL + "?t=" + Date.now(), {cache:"no-store"});
      if(resp.ok){
        PG_DATA = await resp.json();
        PG_LOADED = true;
        return;
      }
    } catch(e){
      console.warn("Reader: fallo fetch repo, uso PAYLOAD fallback", e);
    }
    PG_DATA = JSON.parse(JSON.stringify(PG_INICIALES));
  }
  PG_LOADED = true;
}

function pgSave(){ localStorage.setItem(PG_KEY, JSON.stringify(PG_DATA)); pgStorageInfo(); }
function pgStorageInfo(){
  const sz = (JSON.stringify(PG_DATA).length/1024).toFixed(1);
  document.getElementById("pg-storage-info").textContent = `${PG_DATA.length} pagos · ${sz} KB en localStorage`;
}

// Hoy en yyyy-mm-dd
function pgHoyISO(){
  const d = new Date(); d.setHours(0,0,0,0);
  return d.toISOString().slice(0,10);
}
function pgDaysDiff(isoA, isoB){
  if(!isoA || !isoB) return null;
  const a = new Date(isoA), b = new Date(isoB);
  return Math.round((b - a) / (1000*60*60*24));
}
function pgEstado(r){
  if(r.pagado) return "pagado";
  if(!r.fecha_pago) return "sinfecha";
  const diff = pgDaysDiff(pgHoyISO(), r.fecha_pago);
  if(diff < 0) return "vencido";
  if(diff === 0) return "hoy";
  if(diff <= 7) return "proximo7";
  if(diff <= 30) return "proximo30";
  return "futuro";
}

// Columnas tabla
const PG_COLS = [
  {k:"cliente",       lbl:"Cliente",        type:"text"},
  {k:"fecha_fijacion",lbl:"Fecha Fijación", type:"date"},
  {k:"tn_fijadas",    lbl:"Tn",             type:"num"},
  {k:"precio_fijado", lbl:"Precio",         type:"num"},
  {k:"total_sin_iva", lbl:"Sin IVA",        type:"num"},
  {k:"iva_pct",       lbl:"% IVA",          type:"iva"},
  {k:"total_con_iva", lbl:"Con IVA",        type:"num"},
  {k:"fecha_pago",    lbl:"Fecha Pago",     type:"date"},
];

function pgRecalc(r){
  // Si hay tn y precio → total sin IVA = tn × precio
  if(r.tn_fijadas != null && r.precio_fijado != null){
    r.total_sin_iva = r.tn_fijadas * r.precio_fijado;
  }
  // Si hay sin IVA + IVA pct → con IVA
  if(r.total_sin_iva != null && r.iva_pct != null){
    r.total_con_iva = r.total_sin_iva * (1 + r.iva_pct/100);
  }
}

function pgFmtNum(v){
  if(v == null || isNaN(v)) return "";
  return Number(v).toLocaleString("es-AR", {minimumFractionDigits:2, maximumFractionDigits:2});
}
function pgFmtDate(v){ return v || ""; }
function pgParseNum(s){
  if(s == null || s === "") return null;
  const v = parseFloat(String(s).replace(/\./g,"").replace(",","."));
  return isNaN(v) ? null : v;
}

function pgInitFilters(){
  // Clientes únicos
  const clientes = [...new Set(PG_DATA.map(r => r.cliente).filter(Boolean))].sort();
  document.getElementById("pg-cliente").innerHTML = '<option value="">Todos</option>' +
    clientes.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
  // Meses de pago únicos
  const meses = [...new Set(PG_DATA.map(r => r.fecha_pago ? r.fecha_pago.slice(0,7) : null).filter(Boolean))].sort();
  document.getElementById("pg-mes").innerHTML = '<option value="">Todos</option>' +
    meses.map(m => `<option value="${m}">${mesNice(m)}</option>`).join("");
}

let PG_TOGGLE_PAGO = "";  // "" | "pendiente" | "pagado"
const PG_SEL = new Set();   // ids de filas seleccionadas (masivas)

function pgFiltered(){
  const c = document.getElementById("pg-cliente").value;
  const e = document.getElementById("pg-estado").value;
  const m = document.getElementById("pg-mes").value;
  const q = (document.getElementById("pg-q").value||"").toLowerCase().trim();
  const out = PG_DATA.filter(r => {
    if(PG_TOGGLE_PAGO === "pagado" && !r.pagado) return false;
    if(PG_TOGGLE_PAGO === "pendiente" && r.pagado) return false;
    if(c && r.cliente !== c) return false;
    if(m && (!r.fecha_pago || !r.fecha_pago.startsWith(m))) return false;
    if(q && !(r.cliente||"").toLowerCase().includes(q)) return false;
    if(e && pgEstado(r) !== e) return false;
    return true;
  });
  // Orden cronológico por fecha_pago ascendente. Sin fecha → al final.
  out.sort((a, b) => {
    const fa = a.fecha_pago || "";
    const fb = b.fecha_pago || "";
    if(!fa && !fb) return 0;
    if(!fa) return 1;
    if(!fb) return -1;
    return fa.localeCompare(fb);
  });
  return out;
}

function pgUpdateToggleCounters(){
  const all = PG_DATA.length;
  const pag = PG_DATA.filter(r => r.pagado).length;
  const pend = all - pag;
  const allEl = document.getElementById("pg-toggle-all");
  const pagEl = document.getElementById("pg-toggle-pago");
  const pendEl = document.getElementById("pg-toggle-pend");
  if(allEl) allEl.textContent = `(${all})`;
  if(pagEl) pagEl.textContent = `(${pag})`;
  if(pendEl) pendEl.textContent = `(${pend})`;

  // Estilo visual del toggle activo
  document.querySelectorAll(".pago-toggle").forEach(btn => {
    const isActive = btn.dataset.pago === PG_TOGGLE_PAGO;
    if(isActive){
      btn.classList.add("active");
      btn.style.background = btn.dataset.pago === "pagado" ? "#16a34a" :
                              btn.dataset.pago === "pendiente" ? "#f59e0b" : "var(--blue)";
      btn.style.color = "#fff";
      btn.style.borderColor = btn.style.background;
    } else {
      btn.classList.remove("active");
      btn.style.background = "#fff";
      btn.style.color = "var(--ink)";
      btn.style.borderColor = "var(--line)";
    }
  });
}

function pgRenderAlertas(){
  const hoy = pgHoyISO();
  const sets = {vencido:[], hoy:[], proximo7:[], proximo30:[], sinfecha:[]};
  PG_DATA.forEach(r => {
    if(r.pagado) return;
    const est = pgEstado(r);
    if(sets[est]) sets[est].push(r);
  });

  const sumar = arr => arr.reduce((s,r) => s + (Number(r.total_con_iva)||0), 0);
  const html = [];
  if(sets.vencido.length){
    html.push(`<div class="pg-alert vencido">
      <div><div class="lbl">🔴 ${sets.vencido.length} pagos VENCIDOS</div>
      <div class="det">${sets.vencido.slice(0,3).map(r => `${r.cliente} (${r.fecha_pago})`).join(" · ")}${sets.vencido.length>3?` · +${sets.vencido.length-3} más`:''}</div></div>
      <div class="tot">$${pgFmtNum(sumar(sets.vencido))}</div></div>`);
  }
  if(sets.hoy.length){
    html.push(`<div class="pg-alert hoy">
      <div><div class="lbl">⚠️ ${sets.hoy.length} pagos vencen HOY</div>
      <div class="det">${sets.hoy.slice(0,3).map(r => r.cliente).join(" · ")}${sets.hoy.length>3?` · +${sets.hoy.length-3} más`:''}</div></div>
      <div class="tot">$${pgFmtNum(sumar(sets.hoy))}</div></div>`);
  }
  if(sets.proximo7.length){
    html.push(`<div class="pg-alert proximo7">
      <div><div class="lbl">🟡 ${sets.proximo7.length} pagos en los próximos 7 días</div>
      <div class="det">${sets.proximo7.slice(0,4).map(r => `${r.cliente} (${r.fecha_pago})`).join(" · ")}${sets.proximo7.length>4?` · +${sets.proximo7.length-4} más`:''}</div></div>
      <div class="tot">$${pgFmtNum(sumar(sets.proximo7))}</div></div>`);
  }
  if(sets.proximo30.length){
    html.push(`<div class="pg-alert proximo30">
      <div><div class="lbl">🔵 ${sets.proximo30.length} pagos próximos 30 días</div>
      <div class="det">Total estimado a cubrir</div></div>
      <div class="tot">$${pgFmtNum(sumar(sets.proximo30))}</div></div>`);
  }
  if(sets.sinfecha.length){
    html.push(`<div class="pg-alert sinfecha">
      <div><div class="lbl">📝 ${sets.sinfecha.length} pagos sin fecha asignada</div>
      <div class="det">Cargá la fecha de pago para que aparezcan en las alertas</div></div>
      <div class="tot">$${pgFmtNum(sumar(sets.sinfecha))}</div></div>`);
  }
  document.getElementById("pg-alertas").innerHTML = html.join("");
}

function pgRenderKpis(filtered){
  const tot = filtered.reduce((acc,r) => {
    acc.con += Number(r.total_con_iva)||0;
    acc.sin += Number(r.total_sin_iva)||0;
    acc.tn  += Number(r.tn_fijadas)||0;
    if(r.pagado) acc.pagado += Number(r.total_con_iva)||0;
    else acc.pend += Number(r.total_con_iva)||0;
    return acc;
  }, {con:0,sin:0,tn:0,pagado:0,pend:0});

  document.getElementById("pg-kpis").innerHTML = `
    <div class="kpi"><div class="lbl">Pagos</div><div class="val">${fmt.int(filtered.length)}</div><div class="hint">de ${PG_DATA.length} totales</div></div>
    <div class="kpi"><div class="lbl">Total Tn fijadas</div><div class="val">${fmt.num(tot.tn)}</div></div>
    <div class="kpi red"><div class="lbl">Pendiente (con IVA)</div><div class="val">$${fmt.num(tot.pend)}</div></div>
    <div class="kpi green"><div class="lbl">Ya pagado</div><div class="val">$${fmt.num(tot.pagado)}</div></div>
  `;
}

function pgRender(){
  // Mostrar/ocultar UI de edición según modo
  const editor = pgIsEditor();
  document.body.classList.toggle("pg-reader", !editor);

  pgInitFilters();
  pgRenderAlertas();
  pgUpdateToggleCounters();

  const filtered = pgFiltered();
  pgRenderKpis(filtered);
  document.getElementById("pg-count").textContent =
    `${filtered.length} / ${PG_DATA.length} pagos`;

  // Header tabla
  // chequear si todos los visibles están seleccionados
  const allVisibleSelected = filtered.length > 0 && filtered.every(r => PG_SEL.has(r.id));
  const someVisibleSelected = filtered.some(r => PG_SEL.has(r.id));
  const head = `
    <th style="width:28px;text-align:center"><input type="checkbox" id="pg-chk-all" ${allVisibleSelected?'checked':''} ${!allVisibleSelected && someVisibleSelected?'data-indet="1"':''} title="Seleccionar/Deseleccionar todos los visibles"/></th>
    <th>#</th>
    ${PG_COLS.map(c => `<th class="${c.type==='num'||c.type==='iva'?'num':''}">${c.lbl}</th>`).join("")}
    <th>Estado</th>
    <th>Acciones</th>
  `;
  document.getElementById("pg-tbl-head").innerHTML = head;
  // Indeterminate visual para el checkbox
  const chkAll = document.getElementById("pg-chk-all");
  if(chkAll){
    chkAll.indeterminate = !allVisibleSelected && someVisibleSelected;
    chkAll.addEventListener("change", () => {
      if(chkAll.checked){
        filtered.forEach(r => PG_SEL.add(r.id));
      } else {
        filtered.forEach(r => PG_SEL.delete(r.id));
      }
      pgRender();
    });
  }

  // Body
  const body = filtered.map((r, idx) => {
    const est = pgEstado(r);
    const estLabels = {
      vencido:   '<span class="chip err">VENCIDO</span>',
      hoy:       '<span class="chip warn">HOY</span>',
      proximo7:  '<span class="chip warn">7 días</span>',
      proximo30: '<span class="chip info">30 días</span>',
      futuro:    '<span class="chip neutral">futuro</span>',
      sinfecha:  '<span class="chip neutral">—</span>',
      pagado:    '<span class="chip ok">✓ Pagado</span>',
    };
    const isSel = PG_SEL.has(r.id);
    let row = `<tr class="${est}${isSel?' row-sel':''}" data-id="${r.id}">
      <td style="text-align:center"><input type="checkbox" class="pg-chk-row" data-id="${r.id}" ${isSel?'checked':''}/></td>
      <td style="color:var(--muted);font-size:11px">${idx+1}</td>`;
    PG_COLS.forEach(c => {
      const v = r[c.k];
      if(c.type === "text"){
        row += `<td><input type="text" data-id="${r.id}" data-k="${c.k}" value="${v ? escapeHtml(v) : ''}"/></td>`;
      } else if(c.type === "date"){
        row += `<td><input type="date" data-id="${r.id}" data-k="${c.k}" value="${v || ''}"/></td>`;
      } else if(c.type === "num"){
        row += `<td class="num"><input type="text" data-id="${r.id}" data-k="${c.k}" value="${v != null ? pgFmtNum(v) : ''}"/></td>`;
      } else if(c.type === "iva"){
        row += `<td class="num iva"><input type="text" data-id="${r.id}" data-k="${c.k}" value="${v != null ? pgFmtNum(v) : ''}"/></td>`;
      }
    });
    row += `<td>${estLabels[est] || ''}</td>`;
    row += `<td class="action">
      <button class="row-btn pay" data-id="${r.id}" data-act="pay" title="${r.pagado?'Desmarcar como pagado':'Marcar como pagado'}">${r.pagado ? '↺' : '✓'}</button>
      ${r.pagado
        ? `<button class="row-btn" data-id="${r.id}" data-act="locked" title="Desmarcá primero como NO pagado para poder borrar" style="background:#f1f5f9;color:#94a3b8;cursor:not-allowed">🔒</button>`
        : `<button class="row-btn del" data-id="${r.id}" data-act="del" title="Borrar fila">🗑️</button>`}
    </td>`;
    row += `</tr>`;
    return row;
  }).join("");
  document.getElementById("pg-tbl-body").innerHTML = body || '<tr><td colspan="99" style="padding:30px;text-align:center;color:var(--muted)">Sin pagos para los filtros aplicados</td></tr>';

  // Footer totales
  const totSin = filtered.reduce((s,r)=> s+(Number(r.total_sin_iva)||0), 0);
  const totCon = filtered.reduce((s,r)=> s+(Number(r.total_con_iva)||0), 0);
  const totTn  = filtered.reduce((s,r)=> s+(Number(r.tn_fijadas)||0), 0);
  document.getElementById("pg-tbl-foot").innerHTML = `<tr>
    <td colspan="4">TOTAL (${filtered.length} filas)</td>
    <td class="num">${pgFmtNum(totTn)}</td>
    <td></td>
    <td class="num">${pgFmtNum(totSin)}</td>
    <td></td>
    <td class="num">${pgFmtNum(totCon)}</td>
    <td></td>
    <td></td>
    <td></td>
  </tr>`;

  // Listeners
  document.querySelectorAll("#pg-tbl-body input").forEach(inp => {
    inp.addEventListener("blur", () => {
      const id = inp.dataset.id, k = inp.dataset.k;
      const r = PG_DATA.find(x => x.id === id);
      if(!r) return;
      const t = inp.type;
      let v = inp.value;
      if(["tn_fijadas","precio_fijado","total_sin_iva","iva_pct","total_con_iva"].includes(k)){
        v = pgParseNum(v);
      } else if(k.startsWith("fecha")){
        v = v || null;
      } else {
        v = v.trim() || null;
      }
      r[k] = v;
      // recalcular dependientes si se editó tn/precio/iva
      if(k === "tn_fijadas" || k === "precio_fijado" || k === "iva_pct"){
        pgRecalc(r);
      } else if(k === "total_sin_iva" && r.iva_pct != null){
        // Si edita "sin IVA" recalcular con IVA
        r.total_con_iva = r.total_sin_iva * (1 + r.iva_pct/100);
      } else if(k === "total_con_iva" && r.total_sin_iva){
        // Si edita "con IVA" recalcular el % IVA
        r.iva_pct = Math.round((r.total_con_iva / r.total_sin_iva - 1) * 1000) / 10;
      }
      pgSave();
      pgRender();
    });
  });

  document.querySelectorAll("#pg-tbl-body .row-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id, act = btn.dataset.act;
      const idx = PG_DATA.findIndex(x => x.id === id);
      if(idx < 0) return;
      if(act === "pay"){
        PG_DATA[idx].pagado = !PG_DATA[idx].pagado;
      } else if(act === "del"){
        if(PG_DATA[idx].pagado){
          alert("Esta fila está marcada como PAGADA y no puede borrarse.\nDesmarcala primero apretando el botón ↺.");
          return;
        }
        if(!confirm(`¿Borrar pago de ${PG_DATA[idx].cliente || '(sin cliente)'}?`)) return;
        PG_DATA.splice(idx, 1);
      } else if(act === "locked"){
        alert("Esta fila está marcada como PAGADA.\nPara borrarla:\n1) Apretá el botón ↺ a la izquierda para desmarcar como pagado\n2) Después aparece el botón 🗑️ para borrar");
        return;
      }
      pgSave();
      pgRender();
    });
  });

  // Listeners de checkboxes individuales
  document.querySelectorAll(".pg-chk-row").forEach(chk => {
    chk.addEventListener("change", () => {
      const id = chk.dataset.id;
      if(chk.checked) PG_SEL.add(id);
      else PG_SEL.delete(id);
      pgRefreshMassbar(filtered);
      // toggle visual de la fila sin re-renderizar todo
      const tr = chk.closest("tr");
      if(tr) tr.classList.toggle("row-sel", chk.checked);
      // actualizar el "select all"
      const chkAll2 = document.getElementById("pg-chk-all");
      if(chkAll2){
        const allSel = filtered.every(r => PG_SEL.has(r.id));
        const someSel = filtered.some(r => PG_SEL.has(r.id));
        chkAll2.checked = allSel;
        chkAll2.indeterminate = !allSel && someSel;
      }
    });
  });

  pgRefreshMassbar(filtered);
  pgStorageInfo();
  pgRefreshBackupInfo();
}

function pgRefreshMassbar(filtered){
  const bar = document.getElementById("pg-massbar");
  const n = PG_SEL.size;
  if(n === 0){ bar.style.display = "none"; return; }
  bar.style.display = "flex";
  // contar cuantos seleccionados son pagados/pendientes
  const sel = PG_DATA.filter(r => PG_SEL.has(r.id));
  const pag = sel.filter(r => r.pagado).length;
  const pen = sel.length - pag;
  const tot = sel.reduce((s,r)=> s+(Number(r.total_con_iva)||0), 0);
  document.getElementById("pg-mass-count").textContent =
    `${n} seleccionados (${pen} pendientes, ${pag} pagados) · $${pgFmtNum(tot)}`;
}

// Filtros listeners
["pg-cliente","pg-estado","pg-mes","pg-q"].forEach(id =>
  document.getElementById(id).addEventListener(id === "pg-q" ? "input" : "change", pgRender)
);
document.getElementById("pg-clear").addEventListener("click", () => {
  ["pg-cliente","pg-estado","pg-mes","pg-q"].forEach(id => document.getElementById(id).value = "");
  PG_TOGGLE_PAGO = "";
  pgRender();
});

// Toggle Todos / Pendientes / Pagados
document.querySelectorAll(".pago-toggle").forEach(btn => {
  btn.addEventListener("click", () => {
    PG_TOGGLE_PAGO = btn.dataset.pago;
    pgRender();
  });
});

// Acciones masivas
document.getElementById("pg-mass-pay").addEventListener("click", () => {
  const ids = [...PG_SEL];
  if(ids.length === 0) return;
  const pen = PG_DATA.filter(r => ids.includes(r.id) && !r.pagado).length;
  if(pen === 0){ alert("Todos los seleccionados ya están marcados como pagados."); return; }
  if(!confirm(`¿Marcar ${pen} pagos como PAGADOS?`)) return;
  PG_DATA.forEach(r => { if(PG_SEL.has(r.id)) r.pagado = true; });
  pgSave();
  pgRender();
});
document.getElementById("pg-mass-unpay").addEventListener("click", () => {
  const ids = [...PG_SEL];
  if(ids.length === 0) return;
  const pag = PG_DATA.filter(r => ids.includes(r.id) && r.pagado).length;
  if(pag === 0){ alert("Ninguno de los seleccionados está marcado como pagado."); return; }
  if(!confirm(`¿Desmarcar ${pag} pagos (volverlos a pendientes)?`)) return;
  PG_DATA.forEach(r => { if(PG_SEL.has(r.id)) r.pagado = false; });
  pgSave();
  pgRender();
});
document.getElementById("pg-mass-del").addEventListener("click", () => {
  const ids = [...PG_SEL];
  if(ids.length === 0) return;
  const sel = PG_DATA.filter(r => ids.includes(r.id));
  const pagadosCount = sel.filter(r => r.pagado).length;
  if(pagadosCount > 0){
    alert(`No se puede borrar: ${pagadosCount} de los seleccionados están marcados como PAGADOS.\nDesmarcalos primero como pendientes y volvé a intentar.`);
    return;
  }
  if(!confirm(`¿Borrar ${sel.length} pagos seleccionados? No se puede deshacer.`)) return;
  PG_DATA = PG_DATA.filter(r => !PG_SEL.has(r.id));
  PG_SEL.clear();
  pgSave();
  pgRender();
});
document.getElementById("pg-mass-clear").addEventListener("click", () => {
  PG_SEL.clear();
  pgRender();
});

// Agregar fila (al final, scrollea hasta ahi para que se vea)
document.getElementById("pg-add-row").addEventListener("click", () => {
  const newId = "n" + Date.now();
  PG_DATA.push({
    id: newId,
    cliente: "",
    fecha_fijacion: pgHoyISO(),
    tn_fijadas: null,
    precio_fijado: null,
    total_sin_iva: null,
    iva_pct: 10.5,   // default granos
    total_con_iva: null,
    fecha_pago: null,
    pagado: false,
  });
  pgSave();
  pgRender();
  // Scroll a la nueva fila y foco en el campo Cliente
  requestAnimationFrame(() => {
    const newRow = document.querySelector(`#pg-tbl-body tr[data-id="${newId}"]`);
    if(newRow){
      newRow.scrollIntoView({behavior:"smooth", block:"center"});
      const firstInput = newRow.querySelector('input[data-k="cliente"]');
      if(firstInput) firstInput.focus();
    }
  });
});

// ===== Sistema de Backup =====
const PG_BACKUP_KEY = "tablero-granos-pagos-lastbackup-v1";

function pgLastBackupDate(){
  return localStorage.getItem(PG_BACKUP_KEY); // ISO yyyy-mm-dd
}
function pgDaysSinceBackup(){
  const last = pgLastBackupDate();
  if(!last) return null;
  return pgDaysDiff(last, pgHoyISO());
}
function pgRefreshBackupInfo(){
  const last = pgLastBackupDate();
  const info = document.getElementById("pg-backup-info");
  const banner = document.getElementById("pg-backup-banner");
  const bannerMsg = document.getElementById("pg-backup-banner-msg");
  if(!last){
    info.textContent = "⚠️ Nunca hiciste backup";
    info.style.color = "var(--red)";
    banner.style.display = "flex";
    bannerMsg.innerHTML = "⚠️ <strong>Nunca hiciste backup</strong>. Recomendado: bajá el JSON ahora para tener una copia segura.";
  } else {
    const days = pgDaysSinceBackup();
    info.textContent = `último backup: ${last} (hace ${days} día${days===1?'':'s'})`;
    info.style.color = days > 7 ? "var(--red)" : (days > 1 ? "var(--orange)" : "var(--muted)");
    if(days >= 1){
      banner.style.display = "flex";
      bannerMsg.innerHTML = `⚠️ Hace <strong>${days} día${days===1?'':'s'}</strong> que no hacés backup. Tu último backup fue el ${last}.`;
    } else {
      banner.style.display = "none";
    }
  }
}

function pgDoBackup(){
  const blob = new Blob([JSON.stringify(PG_DATA, null, 2)], {type:"application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `proyectado_pagos_${pgHoyISO()}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Marcar backup hecho hoy
  localStorage.setItem(PG_BACKUP_KEY, pgHoyISO());
  pgRefreshBackupInfo();
}

// Botones de backup
document.getElementById("pg-backup").addEventListener("click", pgDoBackup);
document.getElementById("pg-backup-banner-btn").addEventListener("click", pgDoBackup);
document.getElementById("pg-import").addEventListener("click", () => document.getElementById("pg-import-file").click());
document.getElementById("pg-import-file").addEventListener("change", ev => {
  const f = ev.target.files[0];
  if(!f) return;
  const r = new FileReader();
  r.onload = e => {
    try {
      const obj = JSON.parse(e.target.result);
      if(!Array.isArray(obj)) throw new Error("debe ser un array de pagos");
      if(confirm(`¿Reemplazar tus ${PG_DATA.length} pagos por los ${obj.length} del archivo?`)){
        PG_DATA = obj;
        pgSave();
        pgRender();
      }
    } catch(err){ alert("JSON inválido: "+err.message); }
  };
  r.readAsText(f);
});
// (Botones reset y clear-data removidos para evitar borrado accidental
//  Los datos viven en localStorage y se actualizan editando in-place.)

// Carga inicial async + render
(async () => {
  await pgLoadInitial();
  pgRender();
})();


/* ===== AUTO-BACKUP a GitHub (commit automatico al repo) ===== */

const PG_PAT_KEY    = "tablero-granos-github-pat-v1";
const PG_REPO_OWNER = "ehussenn";
const PG_REPO_NAME  = "tablero-granos-finnegans";
const PG_REPO_PATH  = "data/proyectado_pagos.json";

let pgAutoTimer = null;
let pgAutoSha = null;     // sha actual del archivo en GitHub
let pgAutoLastSaved = null;
let pgAutoStatusInterval = null;

function pgGetPAT(){ return localStorage.getItem(PG_PAT_KEY) || ""; }
function pgSetPAT(v){
  if(v) localStorage.setItem(PG_PAT_KEY, v);
  else localStorage.removeItem(PG_PAT_KEY);
}

function pgUpdateAutoStatus(){
  const el = document.getElementById("pg-autobackup-status");
  const pat = pgGetPAT();
  if(!pat){
    el.innerHTML = `<span style="color:var(--muted)">auto-backup desactivado</span>`;
    return;
  }
  if(pgAutoLastSaved){
    const sec = Math.floor((Date.now() - pgAutoLastSaved) / 1000);
    let txt = "";
    if(sec < 60) txt = `hace ${sec}s`;
    else if(sec < 3600) txt = `hace ${Math.floor(sec/60)} min`;
    else txt = `hace ${Math.floor(sec/3600)}h ${Math.floor((sec%3600)/60)}min`;
    el.innerHTML = `🔄 auto-backup ON · último: ${txt}`;
    el.style.color = "var(--green)";
  } else {
    el.innerHTML = `🔄 auto-backup ON · esperando primer cambio`;
    el.style.color = "var(--blue)";
  }
}

function pgStartAutoStatusTicker(){
  if(pgAutoStatusInterval) clearInterval(pgAutoStatusInterval);
  pgAutoStatusInterval = setInterval(pgUpdateAutoStatus, 5000);
  pgUpdateAutoStatus();
}

// Codificar a base64 UTF-8 (porque btoa no maneja UTF-8 directamente)
function pgToBase64(str){
  const utf8 = new TextEncoder().encode(str);
  let bin = "";
  utf8.forEach(b => bin += String.fromCharCode(b));
  return btoa(bin);
}

// Obtener el SHA actual del archivo en el repo (necesario para PUT update)
async function pgFetchCurrentSha(){
  const pat = pgGetPAT();
  if(!pat) return null;
  const url = `https://api.github.com/repos/${PG_REPO_OWNER}/${PG_REPO_NAME}/contents/${PG_REPO_PATH}`;
  try {
    const resp = await fetch(url, {
      headers: {
        "Authorization": `Bearer ${pat}`,
        "Accept": "application/vnd.github+json",
      },
    });
    if(resp.status === 404) return "";   // archivo no existe (caso raro)
    if(!resp.ok) throw new Error("HTTP " + resp.status);
    const j = await resp.json();
    return j.sha;
  } catch(e){
    console.warn("pgFetchCurrentSha error:", e);
    return null;
  }
}

// Commit del JSON al repo
async function pgDoAutoCommit(){
  const pat = pgGetPAT();
  if(!pat) return false;

  const el = document.getElementById("pg-autobackup-status");
  el.innerHTML = "⏳ guardando en GitHub...";
  el.style.color = "var(--orange)";

  // si no tengo sha, lo busco
  if(pgAutoSha === null){
    pgAutoSha = await pgFetchCurrentSha();
  }

  const content = JSON.stringify(PG_DATA, null, 2);
  const body = {
    message: `Auto-backup pagos ${new Date().toISOString().slice(0,16).replace('T',' ')}`,
    content: pgToBase64(content),
    branch: "main",
  };
  if(pgAutoSha) body.sha = pgAutoSha;

  const url = `https://api.github.com/repos/${PG_REPO_OWNER}/${PG_REPO_NAME}/contents/${PG_REPO_PATH}`;
  try {
    const resp = await fetch(url, {
      method: "PUT",
      headers: {
        "Authorization": `Bearer ${pat}`,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if(!resp.ok){
      const txt = await resp.text();
      // SHA conflict (409): re-fetch y reintentar 1 vez
      if(resp.status === 409){
        pgAutoSha = await pgFetchCurrentSha();
        if(pgAutoSha){
          body.sha = pgAutoSha;
          const r2 = await fetch(url, {
            method: "PUT",
            headers: { "Authorization": `Bearer ${pat}`, "Accept": "application/vnd.github+json", "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          if(r2.ok){
            const j2 = await r2.json();
            pgAutoSha = j2.content.sha;
            pgAutoLastSaved = Date.now();
            pgUpdateAutoStatus();
            return true;
          }
        }
      }
      throw new Error("HTTP " + resp.status + ": " + txt.slice(0,200));
    }
    const j = await resp.json();
    pgAutoSha = j.content.sha;
    pgAutoLastSaved = Date.now();
    pgUpdateAutoStatus();
    return true;
  } catch(e){
    console.error("pgDoAutoCommit error:", e);
    el.innerHTML = `⚠️ error guardando: ${e.message.slice(0,60)}`;
    el.style.color = "var(--red)";
    return false;
  }
}

// Debounce: cuando se llama, espera 5s sin más cambios, despues commitea
function pgScheduleAutoCommit(){
  if(!pgGetPAT()) return;
  if(pgAutoTimer) clearTimeout(pgAutoTimer);
  document.getElementById("pg-autobackup-status").innerHTML = "✏️ cambios pendientes (guardando en 5s...)";
  document.getElementById("pg-autobackup-status").style.color = "var(--orange)";
  pgAutoTimer = setTimeout(() => {
    pgAutoTimer = null;
    pgDoAutoCommit();
  }, 5000);
}

// Hook: cada vez que pgSave se llama, schedular auto-commit
const _origPgSave = pgSave;
pgSave = function(){
  _origPgSave();
  pgScheduleAutoCommit();
};

// Modal handlers
document.getElementById("pg-autobackup-cfg").addEventListener("click", () => {
  document.getElementById("pg-autobackup-modal").style.display = "flex";
  document.getElementById("pg-pat-input").value = pgGetPAT();
  document.getElementById("pg-pat-status").innerHTML = pgGetPAT() ? '✅ Auto-backup activo' : '';
});
document.getElementById("pg-pat-cancel").addEventListener("click", () => {
  document.getElementById("pg-autobackup-modal").style.display = "none";
});
document.getElementById("pg-pat-disable").addEventListener("click", () => {
  pgSetPAT("");
  if(pgAutoTimer){ clearTimeout(pgAutoTimer); pgAutoTimer = null; }
  document.getElementById("pg-pat-input").value = "";
  document.getElementById("pg-pat-status").innerHTML = '❌ Auto-backup desactivado';
  pgUpdateAutoStatus();
});
document.getElementById("pg-pat-save").addEventListener("click", async () => {
  const v = document.getElementById("pg-pat-input").value.trim();
  if(!v.startsWith("github_pat_") && !v.startsWith("ghp_")){
    document.getElementById("pg-pat-status").innerHTML = '<span style="color:var(--red)">❌ Token no parece válido (debe empezar con github_pat_ o ghp_)</span>';
    return;
  }
  pgSetPAT(v);
  document.getElementById("pg-pat-status").innerHTML = '⏳ probando token...';
  pgAutoSha = await pgFetchCurrentSha();
  if(pgAutoSha === null){
    document.getElementById("pg-pat-status").innerHTML = '<span style="color:var(--red)">❌ Falló el test: token inválido o sin permisos sobre el repo</span>';
    pgSetPAT("");
    return;
  }
  document.getElementById("pg-pat-status").innerHTML = '<span style="color:var(--green)">✅ Token OK. Disparando primer backup...</span>';
  const ok = await pgDoAutoCommit();
  if(ok){
    document.getElementById("pg-pat-status").innerHTML = '<span style="color:var(--green)">✅ Auto-backup ACTIVADO. Cada cambio se guardará en GitHub automáticamente.</span>';
    setTimeout(() => document.getElementById("pg-autobackup-modal").style.display = "none", 1500);
  }
});

pgStartAutoStatusTicker();


/* ============================================================
   ============  POSICION GRANARIA  ============================
   ============================================================ */

const PN_KEY = "tablero-granos-posicion-granaria-v1";
// PN_MANUAL guarda los inputs editables del usuario:
//  { producto: { silo, bolsas, silobolsa, pendcos, cosechado, campoest }, ... }
let PN_MANUAL = {};
try { PN_MANUAL = JSON.parse(localStorage.getItem(PN_KEY) || "{}") || {}; } catch(e){ PN_MANUAL = {}; }
function pnSave(){ localStorage.setItem(PN_KEY, JSON.stringify(PN_MANUAL)); }

// Mapeo de producto Finnegans a "familia" agrupadora
function pnFamilia(prod){
  if(!prod) return "Otros";
  const p = prod.toLowerCase();
  if(p.includes("soja")) return "SOJA";
  if(p.includes("maíz") || p.includes("maiz")) return "MAÍZ";
  if(p.includes("trigo")) return "TRIGO";
  if(p.includes("girasol")) return "GIRASOL";
  if(p.includes("sorgo")) return "SORGO";
  if(p.includes("cebada")) return "CEBADA";
  if(p.includes("avena")) return "AVENA";
  if(p.includes("arveja")) return "ARVEJA";
  if(p.includes("centeno")) return "CENTENO";
  if(p.includes("camelina")) return "CAMELINA";
  if(p.includes("rabanito")) return "RABANITO";
  return "OTROS";
}
function pnSubtipo(prod){
  if(!prod) return "Otro";
  const p = prod.toLowerCase();
  if(p.includes("sem") || p.includes("semilla")) return "Semilla";
  if(p.includes("descarte")) return "Descarte";
  if(p.includes("consumo")) return "Consumo";
  return "Grano";
}

// Listas de productos únicos
function pnProductosUnicos(){
  const all = new Set();
  (DATA_CP || []).forEach(c => { if(c.producto) all.add(c.producto); });
  (DATA    || []).forEach(c => { if(c.producto) all.add(c.producto); });
  return [...all].sort();
}

function pnInitFiltros(){
  // Campañas y empresas únicas combinadas
  const camps = new Set(), emps = new Set();
  (DATA_CP || []).forEach(c => { if(c.campana) camps.add(c.campana); if(c.empresa) emps.add(c.empresa); });
  (DATA    || []).forEach(c => { if(c.campana) camps.add(c.campana); if(c.empresa) emps.add(c.empresa); });
  const fillSel = (id, vals, def) => {
    const sel = document.getElementById(id);
    sel.innerHTML = `<option value="">${def}</option>` +
      [...vals].sort().map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
  };
  fillSel("pn-campana", camps, "Todas");
  fillSel("pn-empresa", emps,  "Todas");
  // Default: campaña más reciente (la más alta alfabéticamente)
  const campArr = [...camps].sort();
  if(campArr.length){
    const last = campArr[campArr.length - 1];
    document.getElementById("pn-campana").value = last;
    document.getElementById("pn-campana-chip").textContent = last;
  }

  ["pn-campana","pn-empresa"].forEach(id => document.getElementById(id).addEventListener("change", () => {
    const c = document.getElementById("pn-campana").value;
    document.getElementById("pn-campana-chip").textContent = c || "Todas las campañas";
    pnRender();
  }));
  document.getElementById("pn-clear").addEventListener("click", () => {
    document.getElementById("pn-campana").value = "";
    document.getElementById("pn-empresa").value = "";
    document.getElementById("pn-campana-chip").textContent = "Todas las campañas";
    pnRender();
  });
}

// Estructura columnas: grupos y subcolumnas
const PN_COLS = [
  {grp:"PLANTA",     cls:"grp",        cols:[
    {k:"plantaTot",    lbl:"Total",      calc:true},
    {k:"silo",         lbl:"Silo"},
    {k:"bolsas",       lbl:"Bolsas"},
    {k:"silobolsa",    lbl:"Silo Bolsa"},
  ]},
  {grp:"PRODUCCIÓN", cls:"grp-prod",   cols:[
    {k:"prodTot",      lbl:"Total",      calc:true},
    {k:"pendCos",      lbl:"Pend Cos",   edit:true, manK:"pendcos"},
    {k:"cosechado",    lbl:"Cosechado"},
    {k:"campoEst",     lbl:"Campo Est",  edit:true, manK:"campoest"},
  ]},
  {grp:"P+C",        cls:"grp-compra", cols:[
    {k:"pcTot",        lbl:"Total P+C",  calc:true},
  ]},
  {grp:"COMPRA",     cls:"grp-compra", cols:[
    {k:"compraTot",    lbl:"Tot Compra", calc:true},
    {k:"compraPend",   lbl:"Pend Ing",   calc:true},
    {k:"compraEntr",   lbl:"Entregado",  calc:true},
  ]},
  {grp:"OFERTA",     cls:"grp-prod",   cols:[
    {k:"ofertaTot",    lbl:"Oferta Tot", calc:true},
  ]},
  {grp:"VENTA",      cls:"grp-venta",  cols:[
    {k:"vtaSem",          lbl:"Vta Sem",     edit:true, manK:"vtaSem"},
    {k:"pendVincular",    lbl:"Pend Vincular", edit:true, manK:"pendVincular"},
    {k:"ventaCtosAjust",  lbl:"Tot Venta",   calc:true},
    {k:"ventaCtos",       lbl:"Ctos P.E",    calc:true},
    {k:"ventaEntr",       lbl:"Ctos Entr",   calc:true},
  ]},
  {grp:"DEMANDA",    cls:"grp-venta",  cols:[
    {k:"demandaTot",   lbl:"Demanda Tot",calc:true},
  ]},
  {grp:"RESULTADO",  cls:"grp-resultado", cols:[
    {k:"posPend",      lbl:"Pos Pend",   calc:true},
    {k:"posicion",     lbl:"Posición",   calc:true, hl:true},
  ]},
];

// Defaults embebidos: valores manuales que vienen del cierre — el usuario los puede sobreescribir
// editando la celda (PN_MANUAL en localStorage tiene prioridad sobre estos defaults).
const PN_DEFAULTS = {
  "Maiz Segunda": { pendcos: 26802 },
};
function pnGetMan(prod, k){
  const o = PN_MANUAL[prod] || {};
  if(o[k] !== undefined && o[k] !== null && o[k] !== "") return Number(o[k]) || 0;
  const d = PN_DEFAULTS[prod] || {};
  return Number(d[k]) || 0;
}
function pnSetMan(prod, k, v){
  if(!PN_MANUAL[prod]) PN_MANUAL[prod] = {};
  if(v === null || v === "" || isNaN(v)){
    delete PN_MANUAL[prod][k];
    if(Object.keys(PN_MANUAL[prod]).length === 0) delete PN_MANUAL[prod];
  } else {
    PN_MANUAL[prod][k] = Number(v);
  }
  pnSave();
}

function pnCalcRow(producto, opsCompra, opsVenta){
  // PLANTA: TODO auto desde Stock por Deposito (USR_RESSTOCKDEP), categorizado por nombre de deposito
  // SILO (silos físicos sin "bolsa", "descarte", "ventas"), SILOBOLSA, BOLSAS (DEPOSITO VENTAS ...)
  // Si no hay valor en la API, cae a lo cargado manualmente (PN_MANUAL) para no perder data vieja.
  const siloAuto      = (PAYLOAD.stock_silo      && PAYLOAD.stock_silo[producto])      || 0;
  const bolsasAuto    = (PAYLOAD.stock_bolsas    && PAYLOAD.stock_bolsas[producto])    || 0;
  const silobolsaAuto = (PAYLOAD.stock_silobolsa && PAYLOAD.stock_silobolsa[producto]) || 0;
  const silo       = siloAuto      || pnGetMan(producto, "silo");
  const bolsas     = bolsasAuto    || pnGetMan(producto, "bolsas");
  const silobolsa  = silobolsaAuto || pnGetMan(producto, "silobolsa");
  const plantaTot  = silo + bolsas + silobolsa;

  // PRODUCCIÓN
  // Cosechado AUTO desde traslados (Traslado CPE Agronasaja + Rec Sem PROPIA, origen Dep Cosecha)
  // Pend Cos y Campo Est siguen siendo manuales
  const pendCos    = pnGetMan(producto, "pendcos");
  const cosechadoAuto = (PAYLOAD.cosechado && PAYLOAD.cosechado[producto]) || 0;
  const cosechado  = cosechadoAuto || pnGetMan(producto, "cosechado");
  const campoEst   = pnGetMan(producto, "campoest");
  const prodTot    = pendCos + cosechado + campoEst;

  // COMPRA (auto desde contratos de compra)
  let compraTot = 0, compraEntr = 0;
  opsCompra.forEach(c => {
    compraTot  += Number(c.cantidadmax) || 0;
    compraEntr += Number(c.cantidadentregada) || 0;
  });
  const compraPend = compraTot - compraEntr;

  // P+C
  const pcTot = prodTot + compraTot;

  // OFERTA = PLANTA + PRODUCCION + COMPRA (lo total disponible)
  const ofertaTot = plantaTot + prodTot + compraTot;

  // VENTA
  // Vta Sem: MANUAL — el potencial de semilla lo carga el gerente comercial
  // Pend Vincular: MANUAL — mercadería ya entregada al exportador que aún no se asignó a un contrato
  // Tot Venta: cantidad ajustada de contratos NO-semilla
  // Ctos P.E: pendiente de entrega NO-semilla = ajustada - entregada
  // Ctos Entr: ya entregado NO-semilla
  const vtaSem       = pnGetMan(producto, "vtaSem");
  const pendVincular = pnGetMan(producto, "pendVincular");
  let ventaCtosAjust = 0, ventaEntr = 0;
  opsVenta.forEach(c => {
    if((c.producto || "").toLowerCase().includes("sem")) return;  // semilla va aparte (manual)
    ventaCtosAjust += Number(c.cantidadmax) || 0;
    ventaEntr      += Number(c.cantidadentregada) || 0;
  });
  const ventaCtos = ventaCtosAjust - ventaEntr;   // pendiente entrega contratos

  // DEMANDA = total comprometido = semilla + pend vincular + contratos no-semilla
  const demandaTot = vtaSem + pendVincular + ventaCtosAjust;

  // RESULTADO
  const posPend = compraPend - ventaCtos;   // pendiente neto compra vs venta
  const posicion = ofertaTot - demandaTot;

  return {
    silo, bolsas, silobolsa, plantaTot,
    pendCos, cosechado, campoEst, prodTot,
    pcTot,
    compraTot, compraPend, compraEntr,
    ofertaTot,
    vtaSem, pendVincular, ventaCtosAjust, ventaCtos, ventaEntr,
    demandaTot,
    posPend, posicion,
  };
}

function pnFiltrarOps(){
  const c = document.getElementById("pn-campana").value;
  const e = document.getElementById("pn-empresa").value;
  const filtra = r => {
    if(c && r.campana !== c) return false;
    if(e && r.empresa !== e) return false;
    return true;
  };
  return {
    compras: (DATA_CP || []).filter(filtra),
    ventas:  (DATA    || []).filter(filtra),
  };
}

// Familias cuyas semillas estan expandidas (en memoria, se cierra al recargar)
const PN_SEM_EXPANDED = new Set();
function pnRender(){
  const {compras, ventas} = pnFiltrarOps();
  document.getElementById("pn-info").textContent =
    `${compras.length} contratos compra · ${ventas.length} contratos venta`;

  // Productos únicos en los filtros aplicados
  const prods = new Set();
  compras.forEach(c => { if(c.producto) prods.add(c.producto); });
  ventas.forEach(c => { if(c.producto) prods.add(c.producto); });
  // También incluir productos con datos manuales cargados
  Object.keys(PN_MANUAL).forEach(p => prods.add(p));
  const prodList = [...prods].sort();

  // Agrupar por familia
  const byFamilia = {};
  prodList.forEach(p => {
    const f = pnFamilia(p);
    if(!byFamilia[f]) byFamilia[f] = [];
    byFamilia[f].push(p);
  });
  const familias = Object.keys(byFamilia).sort((a,b) => {
    const orden = ["SOJA","MAÍZ","TRIGO","GIRASOL","SORGO","CEBADA","AVENA","ARVEJA","CENTENO","CAMELINA","RABANITO","OTROS"];
    return orden.indexOf(a) - orden.indexOf(b);
  });

  // Calcular por producto
  const dataPorProd = {};
  prodList.forEach(p => {
    const cp = compras.filter(c => c.producto === p);
    const vp = ventas.filter(c  => c.producto === p);
    dataPorProd[p] = pnCalcRow(p, cp, vp);
  });

  // Render Header tabla con grupos
  const thead = document.getElementById("pn-thead");
  let h1 = "<tr><th class='pn-prod' rowspan='2'>Producto</th>";
  PN_COLS.forEach(g => h1 += `<th colspan="${g.cols.length}" class="${g.cls}">${g.grp}</th>`);
  h1 += "</tr><tr>";
  PN_COLS.forEach(g => g.cols.forEach(c => h1 += `<th class="${g.cls}">${c.lbl}</th>`));
  h1 += "</tr>";
  thead.innerHTML = h1;

  // Render Body - agrupado por familia
  let body = "";
  const totalsFam = {};
  const grandTotal = {};
  PN_COLS.forEach(g => g.cols.forEach(c => grandTotal[c.k] = 0));

  // helper para renderizar una fila de producto (con opciones de estilo)
  function pnRenderProdRow(prod, opts){
    const r = dataPorProd[prod] || {};
    const cls = (opts && opts.cls) || '';
    const indented = !!(opts && opts.indent);
    const tdStyle = indented ? ' style="padding-left:36px;color:var(--muted);font-size:12.5px"' : '';
    let row = `<tr class="${cls}"><td class="pn-prod-cell"${tdStyle} title="${escapeHtml(prod)}">${escapeHtml(prod.length>30?prod.slice(0,30)+'…':prod)}</td>`;
    PN_COLS.forEach(g => g.cols.forEach(c => {
      const v = r[c.k] || 0;
      if(c.edit){
        row += `<td class="editable"><input type="text" data-prod="${escapeHtml(prod)}" data-k="${c.manK}" value="${v ? fmt.num(v) : ''}" placeholder="—"/></td>`;
      } else if(c.hl){
        const cls2 = v >= 0 ? "pos-pos" : "pos-neg";
        row += `<td class="${cls2}">${fmt.num(v)}</td>`;
      } else {
        row += `<td class="calc">${v ? fmt.num(v) : '—'}</td>`;
      }
    }));
    return row + "</tr>";
  }

  familias.forEach(fam => {
    const productos = byFamilia[fam];
    // separar semillas (todas las variedades) del resto
    const semillas = productos.filter(p => pnSubtipo(p) === 'Semilla');
    const otros    = productos.filter(p => pnSubtipo(p) !== 'Semilla');

    // totales familia (sobre TODOS los productos, esten o no expandidas las semillas)
    const totFam = {};
    const totSem = {};
    PN_COLS.forEach(g => g.cols.forEach(c => { totFam[c.k] = 0; totSem[c.k] = 0; }));
    productos.forEach(prod => {
      const r = dataPorProd[prod];
      PN_COLS.forEach(g => g.cols.forEach(c => {
        const v = r[c.k] || 0;
        totFam[c.k] += v;
        grandTotal[c.k] += v;
      }));
    });
    semillas.forEach(prod => {
      const r = dataPorProd[prod];
      PN_COLS.forEach(g => g.cols.forEach(c => { totSem[c.k] += (r[c.k] || 0); }));
    });

    // 1) Productos NO-semilla (Grano X, Consumo, Descarte...) — filas normales
    otros.forEach(prod => { body += pnRenderProdRow(prod); });

    // 2) Fila agrupadora SEMILLA <FAM>  — clickeable; expande/colapsa variedades
    if(semillas.length > 0){
      const expanded = PN_SEM_EXPANDED.has(fam);
      const arrow = expanded ? '▾' : '▸';
      let rowSem = `<tr class="pn-semilla-header" data-sem-fam="${escapeHtml(fam)}" style="cursor:pointer;background:#fff7ed">
        <td class="pn-prod-cell" style="font-weight:600">${arrow} SEMILLA ${fam} <span style="font-size:10.5px;color:var(--muted);font-weight:500">(${semillas.length} variedades)</span></td>`;
      PN_COLS.forEach(g => g.cols.forEach(c => {
        const v = totSem[c.k];
        const cls2 = c.hl ? (v >= 0 ? "pos-pos" : "pos-neg") : "";
        rowSem += `<td class="${cls2}" style="font-weight:600">${v ? fmt.num(v) : '—'}</td>`;
      }));
      rowSem += "</tr>";
      body += rowSem;

      // 3) Variedades (solo si expandido)
      if(expanded){
        semillas.forEach(prod => { body += pnRenderProdRow(prod, {cls:'pn-semilla-child', indent:true}); });
      }
    }

    // 4) Fila TOTAL familia (ya tiene la suma de todo, sin importar expansion)
    let rowFam = `<tr class="pn-grupo"><td class="pn-prod-cell">▸ TOTAL ${fam}</td>`;
    PN_COLS.forEach(g => g.cols.forEach(c => {
      const v = totFam[c.k];
      const cls = c.hl ? (v >= 0 ? "pos-pos" : "pos-neg") : "";
      rowFam += `<td class="${cls}">${v ? fmt.num(v) : '—'}</td>`;
    }));
    rowFam += "</tr>";
    body += rowFam;
    totalsFam[fam] = totFam;
  });

  document.getElementById("pn-tbody").innerHTML = body || '<tr><td colspan="99" style="padding:30px;text-align:center;color:var(--muted)">Sin productos para los filtros aplicados</td></tr>';

  // Footer total general
  let foot = `<tr class="pn-total"><td class="pn-prod-cell">TOTAL GENERAL</td>`;
  PN_COLS.forEach(g => g.cols.forEach(c => {
    foot += `<td>${grandTotal[c.k] ? fmt.num(grandTotal[c.k]) : '—'}</td>`;
  }));
  foot += "</tr>";
  document.getElementById("pn-tfoot").innerHTML = foot;

  // Listeners inputs editables
  document.querySelectorAll("#pn-tbody input").forEach(inp => {
    inp.addEventListener("blur", () => {
      const prod = inp.dataset.prod;
      const k = inp.dataset.k;
      const raw = (inp.value || "").trim().replace(/\./g,"").replace(",",".");
      const v = parseFloat(raw);
      pnSetMan(prod, k, isNaN(v) ? null : v);
      pnRender();
    });
    inp.addEventListener("keydown", e => { if(e.key === "Enter") inp.blur(); });
  });

  // Listener: click en fila SEMILLA <FAM> → expande/colapsa variedades
  document.querySelectorAll("#pn-tbody .pn-semilla-header").forEach(tr => {
    tr.addEventListener("click", (e) => {
      // ignorar clicks en inputs (por las dudas)
      if(e.target.tagName === 'INPUT') return;
      const fam = tr.dataset.semFam;
      if(PN_SEM_EXPANDED.has(fam)) PN_SEM_EXPANDED.delete(fam);
      else PN_SEM_EXPANDED.add(fam);
      pnRender();
    });
  });

  // Cards arriba (por familia)
  pnRenderCards(totalsFam, familias);
}

function pnRenderCards(totalsFam, familias){
  const cls = {SOJA:"soja", "MAÍZ":"maiz", TRIGO:"trigo", GIRASOL:"girasol", SORGO:"sorgo"};
  const html = familias.map(fam => {
    const t = totalsFam[fam];
    const posicion = t.posicion;
    const cobertura = t.ofertaTot > 0 ? t.demandaTot / t.ofertaTot : 0;
    return `<div class="pn-card ${cls[fam] || ''}">
      <div class="name"><span>${fam}</span><span style="font-size:11px;color:var(--muted)">Posición</span></div>
      <div class="pos-val ${posicion>=0?'pos':'neg'}">${posicion>=0?'+':''}${fmt.num(posicion)} <span style="font-size:14px;color:var(--muted)">Tn</span></div>
      <div class="of-de"><span>Of ${fmt.num(t.ofertaTot)}</span><span>De ${fmt.num(t.demandaTot)}</span></div>
      <div class="bar-cobertura"><div style="width:${Math.min(100, cobertura*100)}%"></div></div>
      <div class="pct">${fmt.pct(cobertura)} cobertura</div>
    </div>`;
  }).join("");
  document.getElementById("pn-cards").innerHTML = html;
  document.getElementById("pn-cards-meta").textContent = `${familias.length} cultivos`;
}

pnInitFiltros();
pnRender();


/* ============================================================
   ============  POSICION FINANCIERA  ==========================
   ============================================================ */
const FN_PDT_KEY = "tablero-granos-fn-pendientes-v1";
let FN_PDT = [];
try { FN_PDT = JSON.parse(localStorage.getItem(FN_PDT_KEY) || "[]") || []; } catch(e){ FN_PDT = []; }
function fnPdtSave(){ localStorage.setItem(FN_PDT_KEY, JSON.stringify(FN_PDT)); }

function fnGrainKey(prod){
  if(!prod) return null;
  const p = String(prod).toLowerCase();
  if(p.includes('soja')) return 'soja';
  if(p.includes('maíz') || p.includes('maiz')) return 'maiz';
  if(p.includes('trigo') || p.includes('triticale')) return 'trigo';
  if(p.includes('girasol')) return 'girasol';
  if(p.includes('sorgo')) return 'sorgo';
  return null;
}
function fnPrice(prod){
  const k = fnGrainKey(prod);
  if(!k) return null;
  const g = (PAYLOAD.bcr && PAYLOAD.bcr.granos || {})[k];
  return g && g.usd ? g.usd : null;
}
const FN_TC = (PAYLOAD.bcr && PAYLOAD.bcr.tc_usd_ars) || 0;
function fnFmtUsd(v){ return v==null ? '—' : (fmt.num2(v) + ' USD'); }
function fnFmtArs(v){ return v==null ? '—' : ('$ ' + fmt.num2(v)); }

// Campañas que se incluyen en Posicion Financiera (el cierre actual)
const FN_CAMP_OK = new Set(["CAMPAÑA 24-25", "CAMPAÑA 25-26"]);

function fnFiltrarYAgrupar(rows, campSel, empSel){
  const m = {};
  (rows||[]).forEach(r => {
    const p = r.producto || '—';
    const c = r.campana || '—';
    const e = r.empresa || r.organizacion || '—';
    if(!FN_CAMP_OK.has(c)) return;          // omitir campañas viejas (21-22, 22-23, 23-24, etc.)
    if(campSel && c !== campSel) return;
    if(empSel && e !== empSel) return;
    const k = p + '||' + c;
    if(!m[k]) m[k] = {producto:p, campana:c, ajustada:0, entregada:0, liquidada:0, pdteLiq:0};
    m[k].ajustada  += Number(r.cantidadmax || r.cantidadajustada || 0);
    m[k].entregada += Number(r.cantidadentregada || 0);
    m[k].liquidada += Number(r.cantidadliquidada || 0);
  });
  // Pend de liq = Entregada - Liquidada (pendiente de liquidar de lo entregado)
  Object.values(m).forEach(r => { r.pdteLiq = r.entregada - r.liquidada; });
  return Object.values(m)
    .filter(r => r.pdteLiq >= 0)  // omitir Pend de liq negativos
    .filter(r => r.ajustada || r.entregada || r.liquidada || r.pdteLiq)
    .sort((a,b) => a.campana.localeCompare(b.campana) || a.producto.localeCompare(b.producto));
}

function fnRenderContratoTbl(tblId, metaId, rows){
  const tbl = document.getElementById(tblId);
  const tbody = tbl.querySelector('tbody'); const tfoot = tbl.querySelector('tfoot');
  let totAj=0, totEnt=0, totLiq=0, totPdt=0, totUsd=0, totArs=0;
  let html = '';
  rows.forEach(r => {
    const px = fnPrice(r.producto);
    const usd = px ? r.pdteLiq * px : null;
    const ars = (usd!=null) ? usd * FN_TC : null;
    totAj += r.ajustada; totEnt += r.entregada; totLiq += r.liquidada; totPdt += r.pdteLiq;
    if(usd!=null) totUsd += usd;
    if(ars!=null) totArs += ars;
    html += `<tr>
      <td>${r.producto}</td><td>${r.campana}</td>
      <td class="num">${fmt.num(r.ajustada)}</td><td class="num">${fmt.num(r.entregada)}</td>
      <td class="num">${fmt.num(r.liquidada)}</td><td class="num">${fmt.num(r.pdteLiq)}</td>
      <td style="background:#f8fafd"></td>
      <td class="num">${fmt.num(r.pdteLiq)}</td>
      <td class="num">${fnFmtUsd(usd)}</td>
      <td class="num">${fnFmtArs(ars)}</td>
    </tr>`;
  });
  tbody.innerHTML = html || `<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:18px">Sin datos con los filtros actuales</td></tr>`;
  tfoot.innerHTML = `<tr style="background:#eef2ff;font-weight:700">
    <td colspan="2">TOTAL</td>
    <td class="num">${fmt.num(totAj)}</td><td class="num">${fmt.num(totEnt)}</td>
    <td class="num">${fmt.num(totLiq)}</td><td class="num">${fmt.num(totPdt)}</td>
    <td style="background:#eef2ff"></td>
    <td class="num">${fmt.num(totPdt)}</td>
    <td class="num">${fnFmtUsd(totUsd)}</td>
    <td class="num">${fnFmtArs(totArs)}</td>
  </tr>`;
  const meta = document.getElementById(metaId); if(meta) meta.textContent = `${rows.length} filas`;
  return {tn:totPdt, usd:totUsd, ars:totArs};
}

function fnRenderStock(campSel){
  const tbl = document.getElementById('fn-tbl-stk');
  const tbody = tbl.querySelector('tbody'); const tfoot = tbl.querySelector('tfoot');
  const agrupado = {};
  Object.entries(PN_MANUAL || {}).forEach(([prod, vals]) => {
    if(!vals) return;
    const familia = (typeof pnFamilia==='function') ? pnFamilia(prod) : prod;
    const sub = (typeof pnSubtipo==='function') ? pnSubtipo(prod) : '';
    const tipo = sub && sub !== 'Grano' ? `${familia} ${sub}` : familia;
    const camp = campSel || '—';
    const k = tipo + '||' + camp;
    if(!agrupado[k]) agrupado[k] = {tipo, campana:camp, silobolsa:0, silo:0, bolsas:0, prodSample:prod};
    agrupado[k].silobolsa += Number(vals.silobolsa||0);
    agrupado[k].silo += Number(vals.silo||0);
    agrupado[k].bolsas += Number(vals.bolsas||0);
  });
  const rows = Object.values(agrupado).filter(r => r.silobolsa||r.silo||r.bolsas).sort((a,b)=>a.tipo.localeCompare(b.tipo));
  let totSb=0,totSi=0,totBo=0,totTot=0,totUsd=0,totArs=0;
  let html='';
  rows.forEach(r => {
    const tot = r.silobolsa + r.silo + r.bolsas;
    const px = fnPrice(r.prodSample);
    const usd = px ? tot * px : null;
    const ars = (usd!=null) ? usd * FN_TC : null;
    totSb+=r.silobolsa; totSi+=r.silo; totBo+=r.bolsas; totTot+=tot;
    if(usd!=null) totUsd+=usd; if(ars!=null) totArs+=ars;
    html += `<tr>
      <td>${r.tipo}</td><td>${r.campana}</td>
      <td class="num">${fmt.num(r.silobolsa)}</td><td class="num">${fmt.num(r.silo)}</td><td class="num">${fmt.num(r.bolsas)}</td>
      <td class="num">${fmt.num(tot)}</td>
      <td style="background:#f8fafd"></td>
      <td class="num">${fmt.num(tot)}</td>
      <td class="num">${fnFmtUsd(usd)}</td>
      <td class="num">${fnFmtArs(ars)}</td>
    </tr>`;
  });
  tbody.innerHTML = html || `<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:18px">Sin stock cargado en Posición Granaria todavía (cargá Silo/Bolsas/Silo Bolsa ahí y aparecen acá)</td></tr>`;
  tfoot.innerHTML = `<tr style="background:#eef2ff;font-weight:700">
    <td colspan="2">TOTAL</td>
    <td class="num">${fmt.num(totSb)}</td><td class="num">${fmt.num(totSi)}</td><td class="num">${fmt.num(totBo)}</td>
    <td class="num">${fmt.num(totTot)}</td>
    <td style="background:#eef2ff"></td>
    <td class="num">${fmt.num(totTot)}</td>
    <td class="num">${fnFmtUsd(totUsd)}</td>
    <td class="num">${fnFmtArs(totArs)}</td>
  </tr>`;
  return {tn:totTot, usd:totUsd, ars:totArs};
}

function fnRenderPendientes(){
  const tbl = document.getElementById('fn-tbl-pdt');
  const tbody = tbl.querySelector('tbody'); const tfoot = tbl.querySelector('tfoot');
  let totSb=0,totSi=0,totEt=0,totTot=0,totUsd=0,totArs=0;
  let html='';
  FN_PDT.forEach((r,i) => {
    const tot = (Number(r.silobolsa||0) + Number(r.silo||0) + Number(r.transito||0));
    const px = fnPrice(r.tipo);
    const usd = px ? tot * px : null;
    const ars = (usd!=null) ? usd * FN_TC : null;
    totSb+=Number(r.silobolsa||0); totSi+=Number(r.silo||0); totEt+=Number(r.transito||0); totTot+=tot;
    if(usd!=null) totUsd+=usd; if(ars!=null) totArs+=ars;
    const inpSty = 'padding:4px;border:1px solid var(--line);border-radius:4px;font-size:12.5px';
    html += `<tr data-idx="${i}">
      <td><input type="text" data-k="tipo" value="${r.tipo||''}" style="width:160px;${inpSty}"/></td>
      <td><input type="text" data-k="campana" value="${r.campana||''}" style="width:80px;${inpSty}"/></td>
      <td class="num"><input type="number" step="any" data-k="silobolsa" value="${r.silobolsa||''}" style="width:90px;text-align:right;${inpSty}"/></td>
      <td class="num"><input type="number" step="any" data-k="silo" value="${r.silo||''}" style="width:90px;text-align:right;${inpSty}"/></td>
      <td class="num"><input type="number" step="any" data-k="transito" value="${r.transito||''}" style="width:90px;text-align:right;${inpSty}"/></td>
      <td class="num">${fmt.num(tot)}</td>
      <td style="background:#f8fafd"></td>
      <td class="num">${fmt.num(tot)}</td>
      <td class="num">${fnFmtUsd(usd)}</td>
      <td class="num">${fnFmtArs(ars)}</td>
      <td><button class="fn-pdt-del" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:16px" title="Borrar fila">×</button></td>
    </tr>`;
  });
  if(FN_PDT.length===0) html = `<tr><td colspan="11" style="text-align:center;color:var(--muted);padding:14px">No hay pendientes cargados. Click "+ Agregar fila" para empezar.</td></tr>`;
  tbody.innerHTML = html;
  tfoot.innerHTML = `<tr style="background:#eef2ff;font-weight:700">
    <td colspan="2">TOTAL</td>
    <td class="num">${fmt.num(totSb)}</td><td class="num">${fmt.num(totSi)}</td><td class="num">${fmt.num(totEt)}</td>
    <td class="num">${fmt.num(totTot)}</td>
    <td style="background:#eef2ff"></td>
    <td class="num">${fmt.num(totTot)}</td>
    <td class="num">${fnFmtUsd(totUsd)}</td>
    <td class="num">${fnFmtArs(totArs)}</td>
    <td></td>
  </tr>`;
  tbody.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('change', () => {
      const tr = inp.closest('tr'); const idx = Number(tr.dataset.idx); const k = inp.dataset.k;
      FN_PDT[idx][k] = (inp.type==='number') ? (inp.value===''? '' : Number(inp.value)) : inp.value;
      fnPdtSave(); fnRender();
    });
  });
  tbody.querySelectorAll('.fn-pdt-del').forEach(btn => {
    btn.addEventListener('click', () => {
      const tr = btn.closest('tr'); const idx = Number(tr.dataset.idx);
      FN_PDT.splice(idx,1); fnPdtSave(); fnRender();
    });
  });
  return {tn:totTot, usd:totUsd, ars:totArs};
}

function fnInitFiltros(){
  // Solo las campañas vigentes para el cierre (24-25 y 25-26)
  const camps = [...new Set([
    ...(PAYLOAD.pilot||[]).map(r=>r.campana),
    ...(PAYLOAD.compra||[]).map(r=>r.campana)
  ].filter(c => c && FN_CAMP_OK.has(c)))].sort();
  const sel = document.getElementById('fn-camp');
  camps.forEach(c => { const o=document.createElement('option'); o.value=c; o.textContent=c; sel.appendChild(o); });
  const emps = [...new Set([
    ...(PAYLOAD.pilot||[]).map(r=>r.empresa||r.organizacion),
    ...(PAYLOAD.compra||[]).map(r=>r.empresa||r.organizacion)
  ].filter(Boolean))].sort();
  const sel2 = document.getElementById('fn-emp');
  emps.forEach(e => { const o=document.createElement('option'); o.value=e; o.textContent=e; sel2.appendChild(o); });
  ['fn-camp','fn-emp'].forEach(id => document.getElementById(id).addEventListener('change', fnRender));
  document.getElementById('fn-clear').addEventListener('click', () => {
    document.getElementById('fn-camp').value=''; document.getElementById('fn-emp').value=''; fnRender();
  });
  document.getElementById('fn-pdt-add').addEventListener('click', () => {
    FN_PDT.push({tipo:'',campana:'',silobolsa:'',silo:'',transito:''}); fnPdtSave(); fnRender();
  });
  document.getElementById('fn-tc-chip').textContent = `TC USD/ARS: ${fmt.num2(FN_TC)}`;
  const px = (PAYLOAD.bcr && PAYLOAD.bcr.granos) || {};
  const txt = Object.entries(px).map(([k,v]) => `${k}: ${v && v.usd ? fmt.num2(v.usd) : '—'}`).join(' · ');
  document.getElementById('fn-px-chip').textContent = `Precios BCR (USD/tn): ${txt}`;
}

function fnRender(){
  const camp = document.getElementById('fn-camp').value;
  const emp = document.getElementById('fn-emp').value;
  const vtaRows = fnFiltrarYAgrupar(PAYLOAD.pilot, camp, emp);
  const cprRows = fnFiltrarYAgrupar(PAYLOAD.compra, camp, emp);
  const tVta = fnRenderContratoTbl('fn-tbl-vta','fn-vta-meta',vtaRows);
  const tCpr = fnRenderContratoTbl('fn-tbl-cpr','fn-cpr-meta',cprRows);
  const tStk = fnRenderStock(camp);
  const tPdt = fnRenderPendientes();
  const kpi = document.getElementById('fn-kpis');
  const cxc = tVta.usd, cxp = tCpr.usd, stock = tStk.usd + tPdt.usd;
  const neto = stock + cxc - cxp;
  kpi.innerHTML = `
    <div class="kpi green"><div class="lbl">STOCK + PENDIENTES (USD)</div><div class="val">${fnFmtUsd(stock)}</div><div class="hint">${fmt.num(tStk.tn+tPdt.tn)} tn valoradas</div></div>
    <div class="kpi"><div class="lbl">CUENTAS POR COBRAR (USD)</div><div class="val">${fnFmtUsd(cxc)}</div><div class="hint">Venta pte. liquidar · ${fmt.num(tVta.tn)} tn</div></div>
    <div class="kpi red"><div class="lbl">CUENTAS POR PAGAR (USD)</div><div class="val">${fnFmtUsd(cxp)}</div><div class="hint">Compra pte. liquidar · ${fmt.num(tCpr.tn)} tn</div></div>
    <div class="kpi orange"><div class="lbl">POSICIÓN NETA (USD)</div><div class="val">${fnFmtUsd(neto)}</div><div class="hint">Stock+Pdtes + CxC − CxP</div></div>
  `;
  document.getElementById('fn-info').textContent = `Calculado al ${new Date().toLocaleString('es-AR')}`;
}

fnInitFiltros();
fnRender();


/* ============================================================
   ============  MI BANDEJA · Notas de Mail (Personal)  ========
   ============================================================
   Cada usuario interno (@agronasaja.com.ar) tiene su propia bandeja
   personal con notas sobre mails pendientes. El owner del archivo
   actual es ehussen — los demas internos lo ven en modo lectura. */

const MB_OWNER = "ehussen@agronasaja.com.ar";
const MB_STORAGE_KEY = "tablero-granos-mb-ehussen-v2";
const MB_REPO_PATH = "data/bandeja_ehussen.json";
let MB_ACTIVE_SUB = "integrantes";  // "integrantes" | "generales" | "fijados"

// Defaults pre-poblados con accionables detectados en la bandeja real
// bandeja: "integrantes" (mails @agronasaja.com.ar) | "generales" (externos)
// fijado: true → aparece también en la sub-pestaña Fijados (contratos a firmar)
const MB_DEFAULTS = [
  // ===== INTEGRANTES (equipo Agronasaja) — todos contratos a firmar, fijados =====
  { id:"mb-sangui-soja1", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATO SOJA 1° LOS SANGUIS",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-17",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Sofia pidió hacer contrato de compra a Los Sanguis por 1122.02 tn de Soja 1° (140 tn ya salieron del campo, 980 tn pendientes según rinde estimado).",
    accion:"Cargar contrato en Finnegans contra el remito ya cargado. Confirmar a Sofia + Matías + Tomás.", outlook:"" },
  { id:"mb-sangui-soja2", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATO SOJA 2° LOS SANGUIS",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-17",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Contrato de compra a Los Sanguis por 273 tn de Soja 2°, todo pendiente de cosechar.",
    accion:"Cargar contrato en Finnegans (campaña 25-26). Confirmar a Sofia.", outlook:"" },
  { id:"mb-sangui-maiz1", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATO MAIZ 1° LOS SANGUIS",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-17",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Contrato de compra a Los Sanguis por 840 tn de Maíz 1° (sembrado en octubre).",
    accion:"Cargar contrato en Finnegans. Confirmar a Sofia + Matías + JP + Tomás.", outlook:"" },
  { id:"mb-sangui-maiztardio", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATO MAIZ TARDÍO / 2DA LOS SANGUIS",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-17",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Contrato de compra a Los Sanguis por 1949.08 tn de Maíz Tardío/2da.",
    accion:"Cargar contrato en Finnegans. Confirmar a Sofia + Matías + JP + Tomás.", outlook:"" },
  { id:"mb-sangui-girasol", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATO GIRASOL LOS SANGUIS",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-17",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Contrato de compra por 113.359 tn de Girasol a Los Sanguis. Remito ya cargado en depósito La Isabel 25-26, falta asociarlo.",
    accion:"Cargar contrato + asociar remito existente en Finnegans.", outlook:"" },
  { id:"mb-delsen-soja1", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATO SOJA 1° DELSEN",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-16",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Contrato de compra a Delsen por 1342.21 tn de Soja 1°.",
    accion:"Cargar contrato en Finnegans. Confirmar a Sofia + Matías + Tomás.", outlook:"" },
  { id:"mb-delsen-soja2", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATO SOJA 2° DELSEN",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-16",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Contrato de compra a Delsen por 330.55 tn de Soja 2°.",
    accion:"Cargar contrato en Finnegans. Confirmar a Sofia.", outlook:"" },
  { id:"mb-delsen-maiz1", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATOS MAIZ PRIMERA DELSEN",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-15",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"DOS contratos a Delsen: (1) 1296 tn Maíz Primera. (2) 399.2 tn por el negocio húmedo de maíz que Delsen ya facturó (Fc N° 630). Excel adjunto con detalle.",
    accion:"Cargar AMBOS contratos en Finnegans. Vincular Fc 630 al contrato (2).", outlook:"" },
  { id:"mb-delsen-maiztardio", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATO MAIZ TARDÍO DELSEN",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-16",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Contrato de compra a Delsen por 1582.51 tn de Maíz Tardío.",
    accion:"Cargar contrato en Finnegans.", outlook:"" },
  { id:"mb-delsen-girasol", bandeja:"integrantes", fijado:true,
    asunto:"CONTRATO Y FACTURA GIRASOL DELSEN",
    remitente:"Sofia Alvarez · salvarez@agronasaja.com.ar", fecha:"2026-04-16",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Contrato de compra a Delsen por 131.56 tn de Girasol. Adjunta factura 50% Delsen (ya entregado).",
    accion:"Cargar contrato + factura en Finnegans. Vincular entrega.", outlook:"" },
  { id:"mb-jpg-convenios-fina", bandeja:"integrantes", fijado:true,
    asunto:"Confección contratos Compensación Convenios FINA 25-26",
    remitente:"JP Gonzalez · jpgonzalez@agronasaja.com.ar", fecha:"2026-04-23",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Planilla con TODOS los contratos venta/compra a vincular para dejar compensados los convenios FINA. Mayoría requiere liquidación falsa posterior.",
    accion:"Revisar planilla adjunta. Cargar contratos uno por uno. Coordinar con Carla / Sofia / Tomás. Avisar a JP cuando esté.", outlook:"" },
  { id:"mb-ffior-sauthier", bandeja:"integrantes", fijado:true,
    asunto:"FC Servicio Multiplicación Semilla Trigo - Marcelo Sauthier",
    remitente:"Federico Fiorito · ffiorito@agronasaja.com.ar", fecha:"2026-04-23",
    urgencia:"media", categoria:"compra", estado:"pendiente",
    nota:"Sauthier pasó factura por servicio multiplicación semilla trigo. Fede ya me la mandó. Sauthier quiere CHEQUE (no fijación).",
    accion:"Coordinar con Mariana Zanchetta para emitir cheque. Avisar a Fede y a Sauthier cuando esté.", outlook:"" },
  { id:"mb-baglietto-canje", bandeja:"integrantes", fijado:true,
    asunto:"Canje trigo Agro Baglietto 2026 — 250 tn × 230 USD",
    remitente:"Juan Baglietto · jbaglietto@agronasaja.com.ar", fecha:"2026-05-21",
    urgencia:"alta", categoria:"compra", estado:"pendiente",
    nota:"Juan confirma: hay que cerrar canje de trigo 2026 con Agro Baglietto por 250 tn a 230 USD. Pasarle todo al hermano (jcampuzano / fpavese) para que armen el contrato.",
    accion:"Cargar contrato. Pasar info a Campuzano/Pavese. Cerrar canje con la contraparte.", outlook:"" },
  { id:"mb-ffior-sanchez", bandeja:"integrantes", fijado:true,
    asunto:"Forward trigo Miguel Sanchez — 30 tn × 230 USD diciembre",
    remitente:"Federico Fiorito · ffiorito@agronasaja.com.ar", fecha:"2026-05-12",
    urgencia:"media", categoria:"compra", estado:"pendiente",
    nota:"Fede cerró canje forward con Miguel Sanchez: 30 tn trigo diciembre 230 USD puerto Rosario Sur. Parte del canje cancela compra de semilla trigo (~3567 USD).",
    accion:"Cargar contrato forward. Vincular cancelación de FC semilla trigo. Coordinar con Mariana.", outlook:"" },
  { id:"mb-mendez-soja", bandeja:"integrantes", fijado:true,
    asunto:"Negocio MM Mendez — 60 tn soja + 2 cupos × 317 USD",
    remitente:"Ramón Podesta · rpodesta@agronasaja.com.ar", fecha:"2026-05-07",
    urgencia:"media", categoria:"compra", estado:"pendiente",
    nota:"Manuel M Mendez cumplió 180 tn soja pendiente. Va a entregar 60 tn más para seguir pagando deuda. Ramón confirmó precio 317 USD para 2 nuevos cupos.",
    accion:"Cargar contrato/entrega. Confirmar con Santiago/Mendez. Seguir cobro de deuda.", outlook:"" },
  { id:"mb-boxados-canje-trigo", bandeja:"integrantes", fijado:true,
    asunto:"Canje trigo BOXADOS Juan Manuel — 120 tn",
    remitente:"Matías Loza · mloza@agronasaja.com.ar", fecha:"2026-05-29",
    urgencia:"media", categoria:"compra", estado:"pendiente",
    nota:"Matías avisó: el lunes el cliente Boxados Juan Manuel va a entregar 120 tn de trigo para cancelar cuenta corriente. F. Lauretta coordina cupos para martes.",
    accion:"Cargar contrato del canje. Coordinar cupos con F. Lauretta (Crivello pidió cupo martes).", outlook:"" },

  // ===== GENERALES (externos) — contratos enviados por corredores/clientes =====
  { id:"mb-cofco-addenda-1501756", bandeja:"generales", fijado:true,
    asunto:"ENVIO ADDENDA contrato 1501756 (Soja)",
    remitente:"Juan I. Kobryn · COFCO International", fecha:"2026-05-29",
    urgencia:"alta", categoria:"venta", estado:"pendiente",
    nota:"COFCO envió addenda del contrato comprador/vendedor N° 1501756 (Soja). Fede Ricotti (Glycine) acusó recibo.",
    accion:"FIRMAR addenda. Devolver a Juan Kobryn + copia Glycine.", outlook:"" },
  { id:"mb-ldc-mezcla-740", bandeja:"generales", fijado:true,
    asunto:"Notif. Contrato LDC 001CV906000015 — MEZCLA 7-40",
    remitente:"LDC · envio.documentos@ldc.com", fecha:"2026-05-29",
    urgencia:"media", categoria:"compra", estado:"pendiente",
    nota:"LDC notifica contrato de retiro N° 001CV906000015. Producto: MEZCLA 7-40. Alta: 11/04/2025. Sin rechazo dentro de plazo se considera aceptado.",
    accion:"Revisar términos. Si OK, no responder (silencio = aceptación). Si hay objeción, contestar dentro del plazo.", outlook:"" },
  { id:"mb-ldc-mezcla-209", bandeja:"generales", fijado:true,
    asunto:"Notif. Contrato LDC 001CV209004441 — MEZCLA",
    remitente:"LDC · envio.documentos@ldc.com", fecha:"2026-05-29",
    urgencia:"media", categoria:"compra", estado:"pendiente",
    nota:"LDC notifica contrato de retiro N° 001CV209004441. Producto: MEZCLA. Alta: 14/04/2025. Sin rechazo dentro de plazo se considera aceptado.",
    accion:"Revisar términos. Si OK, no responder. Si hay objeción, contestar dentro del plazo.", outlook:"" },
  { id:"mb-intagro-711878", bandeja:"generales", fijado:true,
    asunto:"Confirmación Negocio Intagro N° 711878 — Maíz 120 tn",
    remitente:"Intagro · interfase@intagro.com", fecha:"2026-05-29",
    urgencia:"media", categoria:"venta", estado:"pendiente",
    nota:"Intagro confirma negocio 711878/0/1: 120 tn maíz. Vendedor Agronasaja, comprador ARGENTRADING S.A. (Consignatario). Destino Zona Rosario Total.",
    accion:"Confirmar a Intagro / Santiago Anguine + cargar contrato venta en Finnegans.", outlook:"" },
  { id:"mb-fyo-ampliacion-277916", bandeja:"generales", fijado:false,
    asunto:"FYO — Ampliación Contrato 277916 Soja",
    remitente:"FYO · aplicaciones@fyo.com", fecha:"2026-05-29",
    urgencia:"baja", categoria:"venta", estado:"pendiente",
    nota:"FYO confirma ampliación contrato 277916: Soja, 13.841 kg a 310 USD / 460.000 ARS.",
    accion:"Verificar que la ampliación quedó cargada en Finnegans. No requiere respuesta.", outlook:"" },
  { id:"mb-agrinter-cupos-soja", bandeja:"generales", fijado:false,
    asunto:"CP y Cupos Soja CTO 27032 Agronasaja/UNUS",
    remitente:"Logística Agrintercereales", fecha:"2026-05-29",
    urgencia:"media", categoria:"logistica", estado:"pendiente",
    nota:"Agrintercereales envió 2 cupos soja: 30/5 (SOJ300526ALVZHYC) y 1/6 (SOJ010626ALV5ZZ9) contra contrato 27032 con UNUS.",
    accion:"Pasar cupos a logística interna / Carla / Matute. Confirmar carga.", outlook:"" },
];

let MB_DATA = [];
let MB_PAT_OK = false;
let MB_OWNER_MODE = false;
let MB_BACKUP_SHA = null;
let MB_LAST_SAVED = null;

function mbGetUser(){
  try{
    const m = (document.cookie||"").match(/(?:^|; )agronasaja_user=([^;]*)/);
    if(!m) return "";
    return decodeURIComponent(m[1]).toLowerCase();
  } catch(e){ return ""; }
}
function mbIsInternal(user){ return /@agronasaja\.com\.ar$/i.test(user||""); }

function mbSave(){
  try{ localStorage.setItem(MB_STORAGE_KEY, JSON.stringify(MB_DATA)); }catch(e){}
  if(MB_OWNER_MODE) mbAutoBackup();
}

async function mbLoadInitial(){
  // 1) intento del repo (datos canónicos)
  let fromRepo = null;
  try{
    const r = await fetch(`./${MB_REPO_PATH}?t=${Date.now()}`, {cache:"no-store"});
    if(r.ok) fromRepo = await r.json();
  } catch(e){}

  if(Array.isArray(fromRepo) && fromRepo.length){
    MB_DATA = fromRepo;
  } else {
    // 2) localStorage
    try{
      const ls = JSON.parse(localStorage.getItem(MB_STORAGE_KEY) || "null");
      if(Array.isArray(ls) && ls.length) MB_DATA = ls;
      else MB_DATA = JSON.parse(JSON.stringify(MB_DEFAULTS));
    } catch(e){ MB_DATA = JSON.parse(JSON.stringify(MB_DEFAULTS)); }
  }
}

function mbFiltered(forSub){
  // forSub: "integrantes" | "generales" | "fijados"
  const sub = forSub || MB_ACTIVE_SUB;
  const u = document.getElementById("mb-urgencia").value;
  const c = document.getElementById("mb-categoria").value;
  const e = document.getElementById("mb-estado").value;
  const q = (document.getElementById("mb-q").value||"").toLowerCase().trim();
  return MB_DATA.filter(r => {
    // gate por sub-pestaña
    if(sub === "fijados"){
      if(!r.fijado) return false;
    } else {
      if((r.bandeja||"integrantes") !== sub) return false;
    }
    if(u && r.urgencia !== u) return false;
    if(c && r.categoria !== c) return false;
    if(e && r.estado !== e) return false;
    if(q){
      const blob = `${r.asunto||""} ${r.remitente||""} ${r.nota||""} ${r.accion||""}`.toLowerCase();
      if(!blob.includes(q)) return false;
    }
    return true;
  }).sort((a,b) => {
    const stOrder = {pendiente:0, respondido:1, archivado:2};
    if(stOrder[a.estado] !== stOrder[b.estado]) return stOrder[a.estado] - stOrder[b.estado];
    const urgOrder = {alta:0, media:1, baja:2};
    if(urgOrder[a.urgencia] !== urgOrder[b.urgencia]) return (urgOrder[a.urgencia]||9) - (urgOrder[b.urgencia]||9);
    return (b.fecha||"").localeCompare(a.fecha||"");
  });
}

function mbRenderKpis(){
  // Contadores por sub-pestaña (incluyen TODOS los estados para el badge del subtab)
  const ints = MB_DATA.filter(r => (r.bandeja||"integrantes") === "integrantes");
  const gens = MB_DATA.filter(r => (r.bandeja||"integrantes") === "generales");
  const fijs = MB_DATA.filter(r => r.fijado);
  const elInt = document.getElementById("mb-cnt-int"); if(elInt) elInt.textContent = ints.filter(r=>r.estado==="pendiente").length;
  const elGen = document.getElementById("mb-cnt-gen"); if(elGen) elGen.textContent = gens.filter(r=>r.estado==="pendiente").length;
  const elFij = document.getElementById("mb-cnt-fij"); if(elFij) elFij.textContent = fijs.filter(r=>r.estado==="pendiente").length;

  // KPIs del scope actual
  const sub = MB_ACTIVE_SUB;
  const scope = sub === "fijados" ? fijs : (sub === "generales" ? gens : ints);
  const pend = scope.filter(r => r.estado === "pendiente").length;
  const alta = scope.filter(r => r.estado === "pendiente" && r.urgencia === "alta").length;
  const respondidos = scope.filter(r => r.estado === "respondido").length;
  const scopeLbl = sub === "fijados" ? "📌 Fijados" : sub === "generales" ? "🌐 Generales" : "🏢 Integrantes";
  document.getElementById("mb-kpis").innerHTML = `
    <div class="kpi red"><div class="lbl">${scopeLbl} · URGENTES</div><div class="val">${alta}</div></div>
    <div class="kpi orange"><div class="lbl">${scopeLbl} · PENDIENTES</div><div class="val">${pend}</div></div>
    <div class="kpi green"><div class="lbl">RESPONDIDOS</div><div class="val">${respondidos}</div></div>
    <div class="kpi"><div class="lbl">TOTAL ${scopeLbl.toUpperCase()}</div><div class="val">${scope.length}</div></div>
  `;
  const cnt = document.getElementById("cnt-personal");
  if(cnt) cnt.textContent = ints.filter(r=>r.estado==="pendiente").length + gens.filter(r=>r.estado==="pendiente").length;
}

function mbEscape(s){
  return String(s||"").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
}

function mbRenderCardsInto(containerId, sub){
  const cont = document.getElementById(containerId);
  if(!cont) return;
  const filt = mbFiltered(sub);
  if(!filt.length){
    const subLbl = sub === "fijados" ? "📌 Fijados" : sub === "generales" ? "🌐 Generales" : "🏢 Integrantes";
    cont.innerHTML = `<div class="mb-empty">📭 Sin mails en ${subLbl} para los filtros aplicados.${MB_OWNER_MODE && sub !== "fijados" ? ' Apretá <b>+ Nueva nota</b> para agregar uno.' : ''}${sub === "fijados" ? ' Marcá 📌 en cualquier card de Integrantes o Generales para fijarla acá.' : ''}</div>`;
    return;
  }
  const ro = MB_OWNER_MODE ? "" : "readonly";
  cont.innerHTML = filt.map(r => {
    const urgLbl = {alta:"⚠ ALTA", media:"● MEDIA", baja:"○ BAJA"}[r.urgencia] || r.urgencia;
    const catLbl = {compra:"🌾 Compra", venta:"📦 Venta", logistica:"🚚 Logística", liquidacion:"💰 Liquidación", banco:"🏦 Banco", otro:"📂 Otro"}[r.categoria] || r.categoria || "—";
    const estadoCls = `estado-${r.estado||"pendiente"}`;
    const bandejaLbl = (r.bandeja||"integrantes") === "generales" ? "🌐 Generales" : "🏢 Integrantes";
    const pinLbl = r.fijado ? "📌 Fijado" : "📍 Fijar";
    const pinCls = r.fijado ? "pinned" : "";
    return `
    <div class="mb-card urg-${r.urgencia||"baja"} ${estadoCls} ${ro} ${r.fijado?"is-fijado":""}" data-id="${r.id}">
      <div class="mb-card-head">
        <h4>${mbEscape(r.asunto)}</h4>
        <span class="mb-chip urg-${r.urgencia||"baja"}">${urgLbl}</span>
      </div>
      <div class="mb-meta">
        <span class="sender">${mbEscape(r.remitente)}</span>
        <span>·</span><span>${mbEscape(r.fecha)}</span>
        <span>·</span><span class="mb-chip cat">${catLbl}</span>
        ${sub === "fijados" ? `<span>·</span><span class="mb-chip" style="background:#ede9fe;color:#5b21b6">${bandejaLbl}</span>` : ""}
      </div>
      <div>
        <div class="mb-section-lbl">Nota interna</div>
        <textarea data-id="${r.id}" data-k="nota" rows="2" placeholder="Contexto / qué pasó / a quién involucra…">${mbEscape(r.nota)}</textarea>
      </div>
      <div>
        <div class="mb-section-lbl">Acción a tomar</div>
        <textarea data-id="${r.id}" data-k="accion" rows="2" placeholder="Qué hay que hacer concretamente y a quién avisarle…">${mbEscape(r.accion)}</textarea>
      </div>
      <div class="mb-actions">
        ${MB_OWNER_MODE ? `<button class="${pinCls}" data-id="${r.id}" data-act="fijar" title="${r.fijado?'Quitar de Fijados':'Agregar a Fijados / Contratos a firmar'}">${pinLbl}</button>` : ""}
        ${MB_OWNER_MODE ? (r.estado === "pendiente"
          ? `<button class="primary" data-id="${r.id}" data-act="resolver">✓ Respondido</button>`
          : `<button data-id="${r.id}" data-act="reabrir">↺ Reabrir</button>`) : ""}
        ${MB_OWNER_MODE && r.estado !== "archivado" ? `<button data-id="${r.id}" data-act="archivar">📁 Archivar</button>` : ""}
        ${MB_OWNER_MODE ? `<button class="danger" data-id="${r.id}" data-act="borrar">🗑️</button>` : ""}
        ${r.outlook ? `<a class="outlook-link" href="${mbEscape(r.outlook)}" target="_blank">↗ Outlook</a>` : ""}
      </div>
    </div>`;
  }).join("");

  if(MB_OWNER_MODE){
    cont.querySelectorAll("textarea").forEach(t => {
      t.addEventListener("blur", () => {
        const r = MB_DATA.find(x => x.id === t.dataset.id);
        if(!r) return;
        const v = t.value;
        if(r[t.dataset.k] === v) return;
        r[t.dataset.k] = v;
        mbSave();
      });
    });
    cont.querySelectorAll("button[data-act]").forEach(b => {
      b.addEventListener("click", () => {
        const r = MB_DATA.find(x => x.id === b.dataset.id);
        if(!r) return;
        const act = b.dataset.act;
        if(act === "fijar") r.fijado = !r.fijado;
        else if(act === "resolver") r.estado = "respondido";
        else if(act === "reabrir") r.estado = "pendiente";
        else if(act === "archivar") r.estado = "archivado";
        else if(act === "borrar"){
          if(!confirm(`¿Borrar la nota "${r.asunto}"?`)) return;
          MB_DATA = MB_DATA.filter(x => x.id !== r.id);
        }
        mbSave();
        mbRender();
      });
    });
  }
}

function mbRender(){
  // Detectar sub-pestaña activa
  const activeSub = document.querySelector('.panel[data-panel="personal"] .subpanel.active');
  if(activeSub){
    const sp = activeSub.getAttribute("data-sub-panel") || "";
    MB_ACTIVE_SUB = sp.replace("mb-","") || "integrantes";
  }
  mbRenderKpis();
  // Renderizar las 3 listas (la activa es la única visible)
  mbRenderCardsInto("mb-cards-integrantes", "integrantes");
  mbRenderCardsInto("mb-cards-generales", "generales");
  mbRenderCardsInto("mb-cards-fijados", "fijados");
  // Conteo header
  const tot = mbFiltered().length;
  document.getElementById("mb-count").textContent = `${tot} / ${MB_DATA.length}`;
}

function mbStorageInfo(){
  try{
    const bytes = (localStorage.getItem(MB_STORAGE_KEY)||"").length;
    document.getElementById("mb-storage-info").textContent = `${MB_DATA.length} notas · ${(bytes/1024).toFixed(1)} KB`;
  } catch(e){}
}

// Wire-up filtros
["mb-urgencia","mb-categoria","mb-estado","mb-q"].forEach(id => {
  const el = document.getElementById(id);
  if(el) el.addEventListener(id === "mb-q" ? "input" : "change", mbRender);
});
// Re-render cuando se cambia de sub-pestaña dentro de Personal
document.querySelectorAll('.panel[data-panel="personal"] .subtab').forEach(st => {
  st.addEventListener("click", () => { setTimeout(mbRender, 30); });
});
const mbClearBtn = document.getElementById("mb-clear");
if(mbClearBtn) mbClearBtn.addEventListener("click", () => {
  ["mb-urgencia","mb-categoria","mb-q"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("mb-estado").value = "pendiente";
  mbRender();
});
const mbAddBtn = document.getElementById("mb-add");
if(mbAddBtn) mbAddBtn.addEventListener("click", () => {
  const id = "mb-" + Date.now();
  MB_DATA.unshift({
    id, asunto:"(nuevo) ", remitente:"", fecha: new Date().toISOString().slice(0,10),
    urgencia:"media", categoria:"interno", estado:"pendiente",
    nota:"", accion:"", outlook:""
  });
  mbSave();
  mbRender();
  setTimeout(() => {
    const c = document.querySelector(`.mb-card[data-id="${id}"]`);
    if(c){ c.scrollIntoView({behavior:"smooth", block:"center"}); c.querySelector("textarea")?.focus(); }
  }, 50);
});
const mbBackupBtn = document.getElementById("mb-backup");
if(mbBackupBtn) mbBackupBtn.addEventListener("click", async () => {
  mbBackupBtn.textContent = "Guardando…";
  const ok = await mbAutoBackup(true);
  mbBackupBtn.textContent = ok ? "✓ Guardado" : "💾 Backup ahora";
  setTimeout(() => mbBackupBtn.textContent = "💾 Backup ahora", 2200);
});

/* === Auto-backup al repo (mismo PAT que Proyectado Pagos) === */
async function mbGetBackupSha(){
  const pat = (typeof pgGetPAT === "function") ? pgGetPAT() : "";
  if(!pat) return null;
  try{
    const r = await fetch(`https://api.github.com/repos/${PG_REPO_OWNER}/${PG_REPO_NAME}/contents/${MB_REPO_PATH}`, {
      headers:{ "Authorization":"Bearer "+pat, "Accept":"application/vnd.github+json" }
    });
    if(r.ok){ const j = await r.json(); return j.sha || null; }
  } catch(e){}
  return null;
}
let mbAutoTimer = null;
async function mbAutoBackup(forceNow){
  const pat = (typeof pgGetPAT === "function") ? pgGetPAT() : "";
  if(!pat) return false;
  if(!forceNow){
    // debounce 2s
    if(mbAutoTimer) clearTimeout(mbAutoTimer);
    mbAutoTimer = setTimeout(() => mbAutoBackup(true), 2000);
    return true;
  }
  if(MB_BACKUP_SHA === null) MB_BACKUP_SHA = await mbGetBackupSha();
  const body = {
    message: `Mi Bandeja ehussen · ${new Date().toISOString()}`,
    content: btoa(unescape(encodeURIComponent(JSON.stringify(MB_DATA, null, 2)))),
  };
  if(MB_BACKUP_SHA) body.sha = MB_BACKUP_SHA;
  try{
    const r = await fetch(`https://api.github.com/repos/${PG_REPO_OWNER}/${PG_REPO_NAME}/contents/${MB_REPO_PATH}`, {
      method:"PUT",
      headers:{ "Authorization":"Bearer "+pat, "Accept":"application/vnd.github+json", "Content-Type":"application/json" },
      body: JSON.stringify(body)
    });
    if(r.ok){
      const j = await r.json();
      MB_BACKUP_SHA = j.content?.sha || null;
      MB_LAST_SAVED = Date.now();
      const info = document.getElementById("mb-backup-info");
      if(info) info.textContent = `✓ guardado ${new Date().toLocaleTimeString('es-AR',{hour:'2-digit',minute:'2-digit'})}`;
      mbStorageInfo();
      return true;
    } else if(r.status === 409 || r.status === 422){
      MB_BACKUP_SHA = await mbGetBackupSha();
      return await mbAutoBackup(true);
    }
  } catch(e){}
  return false;
}

/* === Gate: mostrar nav-internal solo si usuario es @agronasaja.com.ar === */
(function mbInit(){
  const user = mbGetUser();
  const internal = mbIsInternal(user);
  if(!internal) return;  // usuarios no internos no ven la pestaña
  document.querySelectorAll(".nav-internal").forEach(el => el.style.display = "");
  // owner mode: solo el owner edita
  MB_OWNER_MODE = (user === MB_OWNER);
  if(!MB_OWNER_MODE) document.body.classList.add("mb-readonly");
  // cargar datos y renderizar
  (async () => {
    await mbLoadInitial();
    mbRender();
    mbStorageInfo();
  })();
})();

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

    # Filtrar contratos ANULADOS (la API trae todos; el cierre solo cuenta los No Anulado)
    def _no_anul(r):
        v = (r.get("estadoanulacion") or "").strip().lower()
        return v != "anulado"
    _ant_pilot = len(pilot_norm); _ant_compra = len(compra_norm)
    pilot_norm  = [r for r in pilot_norm  if _no_anul(r)]
    compra_norm = [r for r in compra_norm if _no_anul(r)]
    print(f"[+] Filtro Anulado: venta {_ant_pilot}->{len(pilot_norm)}  compra {_ant_compra}->{len(compra_norm)}")

    # Composicion de Saldos detallada (con CONDICIONPAGO y VENDEDOR) para modulo Canjes
    print(f"\n[+] Bajando Composicion Saldo Cliente (detallada con condicion y vendedor)...", flush=True)
    saldos_raw = api.call("/reports/composicionSaldoCliente",
                          {"PARAMWEBREPORT_fecha": "getCurrentDate"})
    if not isinstance(saldos_raw, list):
        saldos_raw = []
    saldos_norm = [{k.lower(): v for k, v in r.items()} for r in saldos_raw]
    canjes_n = sum(1 for r in saldos_norm if "canje" in (r.get("condicionpago") or "").lower())
    print(f"    -> {len(saldos_norm)} filas de saldos, {canjes_n} con condicion 'Canje'")

    # Traslados de granos para modulo "Cruce Cliente x Comprador" (cruzando por CTG)
    print(f"\n[+] Bajando Traslados de Granos (2026) para cruce Cliente x Comprador...", flush=True)
    traslados_raw = api.call("/reports/trasladoGranos", {
        "PARAMFechaDesde": "2026-01-01",
        "PARAMFechaHasta": "2030-12-31",
    })
    if not isinstance(traslados_raw, list):
        traslados_raw = []
    # Filtrar solo los traslados que conforman el cruce (compra CV + venta CV)
    SUBTIPOS_CRUCE = {"Recepción de Granos COMPRA CV", "Traslado de Granos VENTA CV"}
    traslados_cv = [r for r in traslados_raw if r.get("TRANSACCIONSUBTIPONOMBRE") in SUBTIPOS_CRUCE]
    print(f"    -> {len(traslados_raw)} traslados totales, {len(traslados_cv)} de COMPRA CV / VENTA CV")

    # Agrupar por CTG (NUMERODOCUMENTOADICIONAL) y armar el cruce
    cruces = {}
    for r in traslados_cv:
        ctg = r.get("NUMERODOCUMENTOADICIONAL")
        if not ctg:
            continue
        if ctg not in cruces:
            cruces[ctg] = {"ctg": ctg, "kg": 0.0}
        c = cruces[ctg]
        c["kg"] = float(r.get("PESONETO") or 0)   # debería ser el mismo en ambos lados
        c["grano"] = r.get("GRANO")
        c["fecha"] = r.get("FECHA")
        c["cosecha"] = r.get("COSECHA")
        c["destinatario"] = r.get("DESTINATARIO")
        c["entregador"] = r.get("REPRESENTANTE") or r.get("TRANSPORTISTA")
        c["titular"] = r.get("TITULAR")
        c["estado_ctg"] = r.get("ESTADO CTG")
        if r.get("OPERACIONTIPO") == "Compra":
            c["cliente"] = r.get("ORGANIZACIONNOMBRE")
            c["contrato_compra"] = r.get("NOMBRECONTRATO")
            c["doc_contrato_compra"] = r.get("NUMERODOCUMENTOCONTRATO")
        elif r.get("OPERACIONTIPO") == "Venta":
            c["comprador"] = r.get("ORGANIZACIONNOMBRE")
            c["contrato_venta"] = r.get("NOMBRECONTRATO")
            c["doc_contrato_venta"] = r.get("NUMERODOCUMENTOCONTRATO")

    cruces_list = list(cruces.values())
    completos = sum(1 for c in cruces_list if c.get("cliente") and c.get("comprador"))
    print(f"    -> {len(cruces_list)} CTGs unicos, {completos} con cliente+comprador completos")

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

    # Datos del Excel "Proyectado de Pagos Granos" (carga inicial, despues editable en HTML)
    pagos_path = Path(__file__).resolve().parent / "data" / "proyectado_pagos.json"
    if pagos_path.exists():
        try:
            pagos_iniciales = json.loads(pagos_path.read_text(encoding="utf-8"))
            print(f"\n[+] Cargados {len(pagos_iniciales)} pagos iniciales desde {pagos_path.name}")
        except Exception as e:
            print(f"[!] Error leyendo {pagos_path}: {e}")
            pagos_iniciales = []
    else:
        print(f"[.] No existe {pagos_path}, modulo de pagos arranca vacio")
        pagos_iniciales = []

    # Cosechado: total trasladado desde el campo a depositos
    # Filtro: subtipos 'Traslado CPE Agronasaja' (TRAS-VTA-GRANO-AS = granos) +
    #         'Recepción de Semilla PROPIA' (REC-SEM-PPIO = semilla propia)
    # Suma PESONETO (kg) por GRANO -> convertido a tn
    print(f"\n[+] Calculando COSECHADO desde traslados (Traslado CPE Agronasaja + Rec Sem PROPIA)...", flush=True)
    cosechado = {}
    try:
        SUBT_COS = {"Traslado CPE Agronasaja", "Recepción de Semilla PROPIA"}
        acum_cos = {}
        for row in traslados_raw:
            if row.get("TRANSACCIONSUBTIPONOMBRE") not in SUBT_COS: continue
            g = row.get("GRANO") or ""
            if not g: continue
            try: kg = float(row.get("PESONETO") or 0)
            except: kg = 0.0
            acum_cos[g] = acum_cos.get(g, 0.0) + kg
        cosechado = {p: round(kg/1000.0, 4) for p, kg in acum_cos.items() if kg}
        print(f"    -> {len(cosechado)} productos con cosechado")
        for p, t in sorted(cosechado.items(), key=lambda x: -abs(x[1]))[:6]:
            print(f"       {t:>12,.2f} tn  {p}")
    except Exception as e:
        print(f"    [!] Error cosechado: {e}")

    # Stock por Deposito -> categorizar por tipo de deposito y agregar por producto (kg -> tn)
    print(f"\n[+] Bajando Stock por Deposito y categorizando (SILO/SILOBOLSA/BOLSAS/DESCARTE)...", flush=True)
    stock_silo, stock_silobolsa, stock_bolsas, stock_descarte = {}, {}, {}, {}
    try:
        stock_raw = api.call("/reports/USR_RESSTOCKDEP", {"PARAMWEBREPORT_fecha":"getCurrentDate"})
        if not isinstance(stock_raw, list): stock_raw = []

        def categorizar(dep):
            if not dep: return None
            d = dep.upper()
            # Excluir depositos de ALQUILER (contra-cuentas que no son stock real, ajusta el Excel)
            if "ALQUILER" in d: return None
            if "SILO DESCARTE" in d: return "DESCARTE"
            if "SILOBOLSA" in d or "SILO BOLSA" in d: return "SILOBOLSA"
            if "DEPOSITO VENTAS" in d or "DEPÓSITO VENTAS" in d: return "BOLSAS"
            if "SILO" in d: return "SILO"
            return None

        acums = {"SILO": {}, "SILOBOLSA": {}, "BOLSAS": {}, "DESCARTE": {}}
        for row in stock_raw:
            cat = categorizar(row.get("DEPOSITO"))
            if not cat: continue
            prod = row.get("PRODUCTO") or ""
            if not prod: continue
            try: kg = float(row.get("CANTIDAD1") or 0)
            except: kg = 0.0
            acums[cat][prod] = acums[cat].get(prod, 0.0) + kg

        # convertir a tn y filtrar ceros
        stock_silo      = {p: round(kg/1000.0, 4) for p, kg in acums["SILO"].items() if kg}
        stock_silobolsa = {p: round(kg/1000.0, 4) for p, kg in acums["SILOBOLSA"].items() if kg}
        stock_bolsas    = {p: round(kg/1000.0, 4) for p, kg in acums["BOLSAS"].items() if kg}
        stock_descarte  = {p: round(kg/1000.0, 4) for p, kg in acums["DESCARTE"].items() if kg}
        print(f"    -> SILO:      {len(stock_silo)} productos")
        print(f"    -> SILOBOLSA: {len(stock_silobolsa)} productos")
        print(f"    -> BOLSAS:    {len(stock_bolsas)} productos")
        print(f"    -> DESCARTE:  {len(stock_descarte)} productos")
    except Exception as e:
        print(f"    [!] Error stock por deposito: {e}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "pilot":  pilot_norm,
        "compra": compra_norm,
        "saldos": saldos_norm,
        "bcr":    bcr,
        "cruces": cruces_list,
        "pagos_iniciales": pagos_iniciales,
        "stock_silo":      stock_silo,
        "stock_silobolsa": stock_silobolsa,
        "stock_bolsas":    stock_bolsas,
        "stock_descarte":  stock_descarte,
        "cosechado":       cosechado,
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
