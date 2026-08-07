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
import balanza_finales

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


def fetch_produccion() -> tuple[dict, dict]:
    """Baja el Portal de Producción de Agronasaja (app pública en GitHub Pages, sin login)
    y arma la producción por campaña/cultivo para la Posición Granaria:
      - CAMPAÑA 25-26 (cosecha): cosechado (tnAgnsj) + pendiente ((haAgnsj-haCosechada)*rinde)
      - CAMPAÑA 26-27 (siembra): gruesa (ha*rinde gruesa) + fina (ha*rinde estándar por la rotación cu26)
    Devuelve (produccion_camp, pend_detalle) donde pend_detalle es el desglose del
    pendiente de cosecha por CAMPO (tn Agronasaja) para el drill-down.
    Fuente: https://sanguine86.github.io/agronasaja-produccion/index.html (data embebida).
    """
    import urllib.request
    from collections import defaultdict
    url = "https://sanguine86.github.io/agronasaja-produccion/index.html"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        h = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"    [!] no pude bajar el portal de producción: {e}")
        return {}, {}
    dec = json.JSONDecoder()

    def find_array(pat, size):
        for m in re.finditer(pat, h):
            try:
                arr, _ = dec.raw_decode(h, m.start())
                if isinstance(arr, list) and len(arr) == size:
                    return arr
            except Exception:
                pass
        return None

    def prod(cu):
        c = (cu or "").upper()
        if "SOJA" in c: return "Grano Soja"
        if "PISINGALLO" in c: return "Grano Maíz Pisingallo"
        if "MAIZ" in c or "MAÍZ" in c: return "Grano Maíz"
        if "TRIGO" in c: return "Grano Trigo Pan"
        if "CEBADA" in c: return "Grano Cebada"
        if "CAMELINA" in c: return "Grano Camelina"
        if "COLZA" in c: return "Grano Colza"
        if "PASTURA" in c: return None
        if "GIRASOL" in c: return "Grano Girasol"
        if "SORGO" in c: return "Grano Sorgo"
        if "MANI" in c or "MANÍ" in c: return "Grano Maní"
        return "Grano " + (cu or "").title()

    # extrae un objeto {...} literal asignado a NAME en el HTML de la app
    def obj_after(name):
        m = re.search(re.escape(name) + r'\s*=\s*\{', h)
        if not m: return {}
        i = h.index('{', m.start()); d = 0
        for j in range(i, len(h)):
            if h[j] == '{': d += 1
            elif h[j] == '}':
                d -= 1
                if d == 0:
                    try: return json.loads(h[i:j+1].replace("'", '"'))
                    except Exception: return {}
        return {}

    out = {}
    det = {}   # detalle por campo del pendiente de cosecha: {campaña: {producto: [{campo, tn}]}}
    # --- COSECHA 25/26 ---
    # Fuente: página "Información General" del Portal de Producción (la misma que embebe
    # el extranet en /vistas/produccion-info):
    #   COSECHADO = tabla "Agronasaja vs Socios por Cultivo" → columna KG AGNSJ
    #               (retiros con beneficiario AGRONASAJA, todos los convenios)
    #   PEND COS  = "Estimado por Cosechar (Agronasaja)" → tnAgnsjPend
    #               (pendiente físico por lote × rinde, ajustado por convenio:
    #                a AGNSJ le toca (retirado_total + pendiente)×participación − ya_retirado)
    # Cultivos unificados por producto (toda la soja junta, todo el maíz junto).
    cos = None    # lotes del Seguimiento de Cosecha (para pendiente y rindes)
    for m in re.finditer(r'\[\{"encargado"', h):
        try:
            arr, _ = dec.raw_decode(h, m.start())
            if isinstance(arr, list) and arr and "haCosechada" in arr[0]:
                cos = arr; break
        except Exception:
            pass
    retiros = None   # retiros por lote con beneficiario (dataset R de Información General)
    for m in re.finditer(r'\bR\s*=\s*\[\{', h):
        try:
            arr, _ = dec.raw_decode(h, h.index("[", m.start()))
            if isinstance(arr, list) and arr and "kgCampo" in arr[0] and "beneficiario" in arr[0]:
                retiros = arr; break
        except Exception:
            pass
    if cos:
        RINDE_EST = obj_after("RINDE_EST")           # {campo|lote|cultivo: tn/ha}
        RINDE_REGIONAL = obj_after("RINDE_REGIONAL")  # {cultivo: kg/ha}
        rrc = {}                                      # rinde real prom por cultivo (kg/ha)
        rinde_lote = {}                               # campo|lote|cultivo -> kg/ha real
        for l in cos:
            c = (l.get("cultivo") or "").upper().strip()
            if not c: continue
            d = rrc.setdefault(c, {"haC": 0.0, "kgT": 0.0})
            d["haC"] += l.get("haCosechada") or 0; d["kgT"] += l.get("kgTotales") or 0
            hc = l.get("haCosechada") or 0
            if hc > 0 and (l.get("kgTotales") or 0) > 0:
                k1 = f'{(l.get("campo") or "").upper().strip()}|{(l.get("lote") or "").upper().strip()}|{c}'
                rinde_lote[k1] = (l.get("kgTotales") or 0) / hc
        for c, d in rrc.items():
            d["rinde"] = d["kgT"] / d["haC"] if d["haC"] > 0 else 0.0

        c25 = defaultdict(lambda: {"cosechado": 0.0, "pendcos": 0.0})
        # detalle por LOTE para los drill-downs de Cosechado y Pend Cos:
        #   producto -> {"cosechado": [{campo, lote, cultivo, tn, rinde}], "pendcos": [...]}
        # rinde en kg/ha (real del lote en cosechado; estimado usado en el pendiente)
        c25det = defaultdict(lambda: {"cosechado": [], "pendcos": []})

        # ── COSECHADO: KG AGNSJ de los retiros, agrupado por producto y campo/lote ──
        cos_lote = defaultdict(float)
        for r in (retiros or []):
            if (r.get("beneficiario") or "").upper().strip() != "AGRONASAJA": continue
            p = prod(r.get("cultivo"))
            if not p: continue
            tn = (r.get("kgCampo") or 0) / 1000.0
            c25[p]["cosechado"] += tn
            cos_lote[(p, (r.get("campo") or "").upper().strip() or "—",
                      (r.get("lote") or "").upper().strip(),
                      (r.get("cultivo") or "").upper().strip())] += tn
        for (p, campo, lote, c), tn in cos_lote.items():
            if tn <= 0.05: continue
            c25det[p]["cosechado"].append({"campo": campo, "lote": lote, "cultivo": c,
                                           "tn": round(tn, 1),
                                           "rinde": round(rinde_lote.get(f"{campo}|{lote}|{c}", 0))})

        # ── PEND COS: port 1:1 de _calcPendiente() de Información General ──
        ret_all, ret_agn = defaultdict(float), defaultdict(float)   # kg por convenio||cultivo
        for r in (retiros or []):
            c2 = (r.get("cultivo") or "").upper().strip()
            if not c2: continue
            ck = f'{(r.get("convenio") or "").upper().strip()}||{c2}'
            kg = (r.get("kgFinal") or 0) or (r.get("kgDescarga") or 0) or (r.get("kgCampo") or 0)
            ret_all[ck] += kg
            if (r.get("beneficiario") or "").upper().strip() == "AGRONASAJA":
                ret_agn[ck] += kg
        conv_agg = {}
        for l in cos:
            p = prod(l.get("cultivo"))
            if not p: continue
            c = (l.get("cultivo") or "").upper().strip()
            haL = l.get("haLote") or 0; hc = l.get("haCosechada") or 0
            haP = l.get("haPerdidas") or 0; pct = l.get("participacion") or 0
            ck = f'{(l.get("convenio") or "").upper().strip()}||{c}'
            d = conv_agg.setdefault(ck, {"producto": p, "pendFis": 0.0, "pctw": 0.0, "haL": 0.0, "lotes": []})
            d["haL"] += haL; d["pctw"] += pct * haL
            pend = max(0.0, haL - hc - haP)
            if pend <= 0: continue
            campo = (l.get("campo") or "").upper().strip(); lote = (l.get("lote") or "").upper().strip()
            key1 = f"{campo}|{lote}|{c}"
            if key1 in RINDE_EST:      rk = RINDE_EST[key1] * 1000.0
            elif rrc[c]["rinde"] > 0:  rk = rrc[c]["rinde"]
            else:                      rk = RINDE_REGIONAL.get(c, 5000)
            tn_fis = pend * rk / 1000.0
            d["pendFis"] += tn_fis
            d["lotes"].append({"campo": campo or "—", "lote": lote, "cultivo": c, "tn_fis": tn_fis, "rinde": rk})
        for ck, d in conv_agg.items():
            if d["pendFis"] <= 0.01: continue
            pct = d["pctw"] / d["haL"] if d["haL"] > 0 else 0
            cos_tn = ret_all.get(ck, 0) / 1000.0
            ret_a = ret_agn.get(ck, 0) / 1000.0
            target = (cos_tn + d["pendFis"]) * pct        # lo que le corresponde a AGNSJ del convenio
            pend_agn = max(0.0, min(target - ret_a, d["pendFis"]))
            if pend_agn <= 0.01: continue
            p = d["producto"]
            c25[p]["pendcos"] += pend_agn
            # repartir el pendiente AGNSJ del convenio entre sus lotes, proporcional al físico
            for lt in d["lotes"]:
                tn = pend_agn * lt["tn_fis"] / d["pendFis"]
                if tn > 0.05:
                    c25det[p]["pendcos"].append({"campo": lt["campo"], "lote": lt["lote"], "cultivo": lt["cultivo"],
                                                 "tn": round(tn, 1), "rinde": round(lt["rinde"])})

        out["CAMPAÑA 25-26"] = {p: {"cosechado": round(v["cosechado"], 1), "pendcos": round(v["pendcos"], 1)}
                                for p, v in c25.items() if v["cosechado"] > 0.5 or v["pendcos"] > 0.5}
        det["CAMPAÑA 25-26"] = {p: {k: sorted(v[k], key=lambda d: -d["tn"]) for k in ("cosechado", "pendcos")}
                                for p, v in c25det.items()}
        tot_pend = sum(v["pendcos"] for v in out["CAMPAÑA 25-26"].values())
        tot_cos = sum(v["cosechado"] for v in out["CAMPAÑA 25-26"].values())
        print(f"    -> cosecha 25/26 (KG AGNSJ, {len(retiros or [])} retiros): {len(out['CAMPAÑA 25-26'])} cultivos · cosechado {tot_cos:.0f} tn · pend AGNSJ {tot_pend:.0f} tn")

    # --- SIEMBRA 26/27 (array ~306 con keys cortas: e,co,pa,ca,...cu,cu26,rg,tng) ---
    sie = None
    for m in re.finditer(r'\[\{"e"', h):
        try:
            arr, _ = dec.raw_decode(h, m.start())
            if isinstance(arr, list) and len(arr) > 50 and arr and "cu26" in arr[0]:
                sie = arr; break
        except Exception:
            pass
    if sie:
        # Estimado AGNSJ = ha × rinde × participación (todos los convenios), coherente con
        # el criterio "parte de Agronasaja como beneficiario" de Información General.
        RINDE_FINA = {"Grano Trigo Pan": 4.5, "Grano Cebada": 4.5, "Grano Camelina": 1.3, "Grano Colza": 2.2}
        c26 = defaultdict(lambda: {"pendcos": 0.0})
        c26det = defaultdict(lambda: {"cosechado": [], "pendcos": []})   # mismo shape que 25-26
        for l in sie:
            ha = l.get("ha") or 0; rg = l.get("rg") or 0
            pa = l.get("pa") if l.get("pa") is not None else 1
            campo = (l.get("ca") or "").upper().strip() or "—"
            lote = (l.get("lt") or "").upper().strip()
            pg = prod(l.get("cu"))                       # gruesa: ha * rinde gruesa * participación
            if pg and rg and pa:
                c26[pg]["pendcos"] += ha * rg * pa
                c26det[pg]["pendcos"].append({"campo": campo, "lote": lote, "cultivo": (l.get("cu") or "").upper().strip(),
                                              "tn": round(ha * rg * pa, 1), "rinde": round(rg * 1000)})
            cu26 = str(l.get("cu26") or "")              # fina: primer cultivo de la rotación
            fina = prod(cu26.split("/")[0]) if "/" in cu26 else None
            if fina and fina in RINDE_FINA and pa:
                c26[fina]["pendcos"] += ha * RINDE_FINA[fina] * pa
                c26det[fina]["pendcos"].append({"campo": campo, "lote": lote, "cultivo": cu26.split("/")[0].upper().strip(),
                                                "tn": round(ha * RINDE_FINA[fina] * pa, 1), "rinde": round(RINDE_FINA[fina] * 1000)})
        out["CAMPAÑA 26-27"] = {p: {"pendcos": round(v["pendcos"], 1)} for p, v in c26.items() if v["pendcos"] > 0.5}
        det["CAMPAÑA 26-27"] = {p: {k: sorted(v[k], key=lambda d: -d["tn"]) for k in ("cosechado", "pendcos")}
                                for p, v in c26det.items() if p in out["CAMPAÑA 26-27"]}
        print(f"    -> siembra 26/27 (AGNSJ x participación): {len(sie)} lotes · {len(out['CAMPAÑA 26-27'])} cultivos")
    return out, det


# ---------- HTML ----------
HTML_TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Granos — Agronasaja</title>
<script>
/* Estado compartido SOLO funciona detrás del Worker (portal). Si alguien entra por el
   link directo de GitHub Pages, lo mandamos al portal para que todos trabajen sobre la
   MISMA data (KV). Cuando el Worker sirve esta página, el hostname es workers.dev, así
   que este redirect NO se dispara ahí (no hay loop). */
(function(){
  try{
    if(location.hostname.endsWith(".github.io")){
      location.replace("https://tablero-agronasaja.ehussen.workers.dev/");
    }
  }catch(e){}
})();
</script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{
    --bg:#f4f6fa; --card:#ffffff; --ink:#1a2233; --muted:#6c7a8c;
    --blue:#15803d; --blue2:#22c55e; --green:#16a34a; --red:#dc2626;
    --orange:#f59e0b; --line:#e5e9f2; --chip:#ecfdf5;
    --row-alt:#f8fafd;
  }
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:var(--ink);background:var(--bg)}
  .wrap{max-width:1500px;margin:0 auto;padding:18px}

  /* header */
  .hero{background:linear-gradient(135deg,#15803d 0%,#22c55e 100%);color:#fff;border-radius:14px;padding:22px 28px;display:flex;justify-content:space-between;align-items:flex-start;box-shadow:0 4px 20px rgba(21,128,61,.18)}
  .hero h1{margin:0;font-size:22px;font-weight:600;letter-spacing:.2px}
  .hero .sub{margin-top:4px;opacity:.85;font-size:13px}
  .hero .meta{font-size:12px;text-align:right;opacity:.9;line-height:1.6}
  .hero .meta .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:6px;vertical-align:middle;box-shadow:0 0 0 3px rgba(34,197,94,.25)}

  /* tabs */
  .tabs{display:flex;gap:6px;margin:18px 0 14px}
  .tab{padding:10px 22px;border:1px solid var(--line);background:#fff;border-radius:10px;cursor:pointer;font-weight:500;color:var(--muted);transition:all .15s}
  .tab:hover{color:var(--ink);border-color:#c7d2e2}
  .tab.active{background:var(--blue);color:#fff;border-color:var(--blue);box-shadow:0 2px 8px rgba(21,128,61,.25)}
  .tab .count{display:inline-block;margin-left:8px;padding:1px 8px;border-radius:10px;background:rgba(255,255,255,.18);font-size:11px;font-weight:600}
  .tab:not(.active) .count{background:#ecfdf5;color:var(--blue)}

  /* sub-tabs (dentro de cada panel) */
  .subtabs{display:flex;gap:0;border-bottom:2px solid var(--line);margin:6px 0 16px}
  .subtab{padding:9px 18px;border:none;background:transparent;cursor:pointer;font-weight:500;color:var(--muted);font-size:13px;border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s;letter-spacing:.2px}
  .subtab:hover{color:var(--blue)}
  .subtab.active{color:var(--blue);border-bottom-color:var(--blue);font-weight:600}

  /* ===== Layout con menú lateral (sidebar) + topbar ===== */
  .tabs{display:none !important}      /* reemplazadas por el sidebar */
  .subtabs{display:none !important}   /* idem (siguen funcionando por JS) */
  .app-shell{display:flex;min-height:100vh}
  .sidebar{width:248px;flex:0 0 248px;background:#123524;color:#b3c6ba;position:fixed;top:0;left:0;bottom:0;overflow-y:auto;border-right:1px solid #1e4634;z-index:30}
  .sidebar .brand{display:flex;align-items:center;gap:10px;padding:18px 18px 4px}
  .sidebar .brand-logo{width:38px;height:38px;border-radius:9px;background:linear-gradient(135deg,#15803d,#22c55e);display:flex;align-items:center;justify-content:center;font-size:20px;flex:0 0 38px}
  .sidebar .brand-name{font-weight:800;letter-spacing:1px;color:#fff;font-size:15px;line-height:1.1}
  .sidebar .brand-sub{font-size:10px;letter-spacing:2px;color:#93b8a4;text-transform:uppercase;margin-top:2px}
  .sidebar .campana{margin:14px 18px 4px;font-size:10px;letter-spacing:1.5px;color:#4ade80;text-transform:uppercase;font-weight:700;border-top:1px solid #1e4634;padding-top:12px}
  .sidebar .campana-home{display:block;text-decoration:none;cursor:pointer;padding:10px 18px 10px 18px;margin:0;border-top:1px solid #1e4634;border-radius:0;transition:background .15s,color .15s;font-size:11px}
  .sidebar .campana-home:hover{background:#17402e;color:#fff}
  .sidebar .campana-home.active{background:linear-gradient(90deg,#17402e,#16a34a44);color:#fff;border-left:3px solid #16a34a}

  /* ===== PORTADA / HOME ===== */
  .home-hero{position:relative;border-radius:14px;overflow:hidden;background:linear-gradient(135deg,#14532d 0%,#16a34a 35%,#84cc16 75%,#fde68a 100%);color:#fff;padding:0;margin-bottom:22px;box-shadow:0 10px 30px rgba(20,83,45,.25)}
  .home-hero-bg{position:absolute;inset:0;opacity:.18;background-image:
    radial-gradient(circle at 20% 80%, rgba(255,255,255,.4) 0%, transparent 30%),
    radial-gradient(circle at 80% 20%, rgba(255,255,255,.3) 0%, transparent 25%),
    url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='400' height='200' viewBox='0 0 400 200'><g fill='%23fff' opacity='.5'><ellipse cx='40' cy='180' rx='12' ry='4'/><ellipse cx='80' cy='185' rx='10' ry='3'/><ellipse cx='160' cy='178' rx='14' ry='4'/><ellipse cx='220' cy='182' rx='11' ry='3'/><ellipse cx='280' cy='186' rx='13' ry='4'/><ellipse cx='340' cy='180' rx='12' ry='3'/><path d='M40 180 L40 130 M37 155 L43 145 M37 145 L43 135 M37 165 L43 155'/><path d='M80 185 L80 140 M77 165 L83 155 M77 155 L83 145' stroke='%23fff' stroke-width='1' fill='none'/><path d='M160 178 L160 125 M157 152 L163 142 M157 142 L163 132 M157 162 L163 152' stroke='%23fff' stroke-width='1' fill='none'/><path d='M220 182 L220 138 M217 160 L223 150 M217 150 L223 140' stroke='%23fff' stroke-width='1' fill='none'/><path d='M280 186 L280 135 M277 162 L283 152 M277 152 L283 142 M277 172 L283 162' stroke='%23fff' stroke-width='1' fill='none'/><path d='M340 180 L340 130 M337 155 L343 145 M337 145 L343 135' stroke='%23fff' stroke-width='1' fill='none'/></g></svg>");
    background-size:cover,cover,400px}
  .home-hero-content{position:relative;padding:36px 38px 30px;z-index:2}
  .home-greet-eyebrow{font-size:11.5px;letter-spacing:2.5px;font-weight:700;color:#bbf7d0;text-transform:uppercase;margin-bottom:6px;text-shadow:0 1px 2px rgba(0,0,0,.2)}
  .home-title{font-size:32px;font-weight:800;margin:0 0 6px;letter-spacing:-.5px;text-shadow:0 2px 8px rgba(0,0,0,.25)}
  .home-sub{font-size:14px;opacity:.95;max-width:720px;line-height:1.5;text-shadow:0 1px 3px rgba(0,0,0,.2)}
  .home-kpis{margin-top:26px;display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
  .home-kpi{background:rgba(255,255,255,.95);color:#14532d;border-radius:10px;padding:14px 16px;backdrop-filter:blur(6px);box-shadow:0 4px 12px rgba(0,0,0,.08)}
  .home-kpi-label{font-size:10.5px;font-weight:700;color:#16a34a;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:4px}
  .home-kpi-value{font-size:22px;font-weight:800;color:#14532d;font-variant-numeric:tabular-nums}
  .home-kpi-sub{font-size:11.5px;color:#65a30d;margin-top:2px;font-weight:600}

  .home-shortcuts{padding:0 4px}
  .home-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
  .home-card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px;cursor:pointer;text-decoration:none;color:var(--ink);transition:transform .15s,box-shadow .15s,border-color .15s;display:flex;flex-direction:column;gap:8px}
  .home-card:hover{transform:translateY(-2px);box-shadow:0 8px 20px rgba(0,0,0,.08);border-color:#16a34a}
  .home-card-icon{width:42px;height:42px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:600}
  .home-card-title{font-size:15px;font-weight:700;color:var(--ink)}
  .home-card-desc{font-size:11.5px;color:var(--muted);line-height:1.4}
  .nav{padding:4px 10px 28px}
  .nav-group{font-size:10px;letter-spacing:1.5px;color:#7d9c8a;text-transform:uppercase;font-weight:700;margin:16px 10px 6px;background:none;border:none;width:calc(100% - 20px);text-align:left;cursor:pointer;padding:4px 6px;border-radius:6px;display:flex;align-items:center;gap:4px;font-family:inherit;transition:color .15s}
  .nav-group:hover{color:#bbf7d0;background:#17402e}
  .nav-arrow{display:inline-block;width:10px;font-size:9px;transition:transform .15s;color:#22c55e}
  .nav-section.collapsed .nav-arrow{transform:rotate(-90deg)}
  .nav-section.collapsed .nav-items{display:none}
  .nav-items{display:block}
  .nav-item{display:block;padding:9px 12px;border-radius:8px;color:#c2cee3;font-size:13.5px;cursor:pointer;text-decoration:none;border-left:3px solid transparent;transition:all .15s;margin:1px 0}
  .nav-item:hover{background:#17402e;color:#fff}
  .nav-item.active{background:#17402e;color:#bbf7d0;border-left-color:#22c55e;font-weight:600}
  .main{flex:1;margin-left:248px;min-width:0;display:flex;flex-direction:column}
  .topbar{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:11px 24px;display:flex;justify-content:space-between;align-items:center;z-index:25;gap:16px}
  .topbar-title{font-size:18px;font-weight:700;color:var(--ink)}
  .topbar-right{display:flex;align-items:center;gap:14px}
  .topbar-meta{font-size:11px;color:var(--muted);text-align:right;line-height:1.35}
  .topbar-meta .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:5px;box-shadow:0 0 0 3px rgba(34,197,94,.25)}
  .admin-pill{background:#dcfce7;color:#15803d;border:1px solid #bbf7d0;padding:7px 16px;border-radius:20px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
  .admin-pill:hover{background:#bbf7d0}
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
  /* perf: aislar el layout/estilo de cada subpanel (contención) y no renderizar lo
     que queda fuera de pantalla -> cambiar de panel deja de forzar recálculo global. */
  .subpanel.active{display:block;content-visibility:auto;contain-intrinsic-size:0 1200px}

  /* kpi cards */
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:14px 0}
  .kpi{background:#fff;border-radius:14px;padding:18px 20px;border-top:3px solid var(--blue2);box-shadow:0 2px 10px rgba(16,64,40,.06)}
  .kpi.green{border-top-color:var(--green)}
  .kpi.red{border-top-color:var(--red)}
  .kpi.orange{border-top-color:var(--orange)}
  .kpi.yellow{border-top-color:#eab308}
  .kpi.pink{border-top-color:#ec4899}
  .kpi .lbl{color:var(--muted);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.6px}
  .kpi .val{font-size:31px;font-weight:800;margin-top:4px;color:var(--ink);letter-spacing:-.3px}
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
  .section{background:#fff;border-radius:14px;padding:20px;margin-bottom:16px;border:1px solid var(--line);box-shadow:0 2px 10px rgba(16,64,40,.04)}
  .section h3{margin:0 0 12px;font-size:15px;font-weight:600;display:flex;justify-content:space-between;align-items:center}
  .section h3 .badge{font-size:11px;font-weight:500;color:var(--muted)}
  /* Tablas con columnas redimensionables (drag desde borde derecho del th) */
  table.resizable-cols{table-layout:fixed}
  table.resizable-cols th{position:relative}
  table.resizable-cols th .col-resize{position:absolute;top:0;right:0;bottom:0;width:6px;cursor:col-resize;user-select:none;z-index:2}
  table.resizable-cols th .col-resize:hover,table.resizable-cols th .col-resize.dragging{background:rgba(59,130,246,.4)}
  table.resizable-cols td,table.resizable-cols th{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  table.resizable-cols td.allow-wrap{white-space:normal}
  /* encabezados legibles: que el titulo se acomode en varias lineas en vez de cortarse con "..." */
  table.resizable-cols thead th{white-space:normal;text-overflow:clip;line-height:1.12;vertical-align:bottom}

  /* Section collapsible (<details>) */
  details.section-collapsible{padding:18px}
  details.section-collapsible > summary{margin:0 0 0;font-size:15px;font-weight:600;display:flex;align-items:center;gap:6px;cursor:pointer;list-style:none;user-select:none}
  details.section-collapsible > summary::-webkit-details-marker{display:none}
  details.section-collapsible[open] > summary{margin-bottom:12px}
  details.section-collapsible > summary .badge{font-size:11px;font-weight:500;color:var(--muted);margin-left:auto}
  details.section-collapsible > summary:hover{color:#15803d}
  details.section-collapsible .collapse-arrow{display:inline-block;width:14px;font-size:11px;color:#15803d;transition:transform .15s}
  details.section-collapsible:not([open]) .collapse-arrow{transform:rotate(-90deg)}

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
  /* perf: no renderizar las filas fuera de pantalla -> scroll fluido en tablas largas.
     Se excluyen la matriz de cruce y los calendarios (celdas sticky a la izquierda). */
  .tbl-wrap table:not(#cx-matrix):not(#cal-tbl):not(#cal-cp-tbl):not(#pl-tbl) tbody tr{content-visibility:auto;contain-intrinsic-size:0 32px}
  /* Calendario de Cobranzas (pendiente de liquidar) */
  .pl-lbl{font-size:11px;color:var(--muted);text-transform:uppercase;font-weight:600;display:block;margin-bottom:4px}
  .pl-inp{padding:7px 9px;border:1px solid var(--line);border-radius:6px}
  #pl-tbl{border-collapse:separate;border-spacing:0;font-size:12px}
  #pl-tbl th,#pl-tbl td{white-space:nowrap;border-bottom:1px solid var(--line);border-right:1px solid #eef1f6;padding:6px 8px}
  #pl-tbl thead th{position:sticky;top:0;background:#15803d;color:#fff;z-index:2;text-align:right}
  #pl-tbl thead th.pl-month{background:#172e6b;text-align:center;top:0}
  #pl-tbl thead tr:nth-child(2) th{top:30px;background:#24407e}
  #pl-tbl .pl-fz{position:sticky;background:#fff;z-index:1;text-align:left}
  #pl-tbl tbody tr:nth-child(even) .pl-fz{background:var(--row-alt)}
  #pl-tbl thead th.pl-fz{z-index:5;background:#15803d;color:#fff}
  #pl-tbl tfoot td{position:sticky;bottom:0;background:#dcfce7;font-weight:700;z-index:1}
  #pl-tbl tfoot td.pl-fz{z-index:3;background:#cbe0f7}
  #pl-tbl td.pl-day{padding:1px}
  #pl-tbl input.pl-cell{width:84px;border:1px solid transparent;background:transparent;text-align:right;padding:5px 6px;border-radius:4px;font-variant-numeric:tabular-nums;font-size:12px}
  #pl-tbl input.pl-cell:hover{border-color:var(--line)}
  #pl-tbl input.pl-cell:focus{border-color:var(--blue);outline:none;background:#fff}
  #pl-tbl input.pl-num{width:62px}
  #pl-tbl thead th.pl-month{background:#172e6b;text-align:center}
  #pl-tbl td.pl-cobrar,#pl-tbl td.pl-estim{background:#f0f9ff;color:#0c4a6e;font-weight:600}
  #pl-tbl tbody tr:nth-child(even) td.pl-cobrar,#pl-tbl tbody tr:nth-child(even) td.pl-estim{background:#e0f2fe}
  tbody tr:hover{background:#eff5ff}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  td.muted{color:var(--muted)}

  /* badges/chips */
  .chip{display:inline-block;padding:2px 9px;border-radius:11px;font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.3px}
  .chip.ok{background:#dcfce7;color:#15803d}
  .chip.warn{background:#fef3c7;color:#a16207}
  .chip.err{background:#fee2e2;color:#b91c1c}
  .chip.info{background:#dcfce7;color:#1e40af}
  .chip.neutral{background:#f1f5f9;color:#475569}

  /* chart container */
  .chart-wrap{position:relative;height:280px}

  /* options con 0 hits — estilo gris */
  select option.opt-zero{color:#9aa5b3}

  /* calendario de cobranzas */
  #cal-tbl{font-size:12px}
  #cal-tbl thead th{cursor:default;font-size:10.5px;padding:6px 8px}
  #cal-tbl thead th.cal-month{background:#15803d;cursor:pointer}
  #cal-tbl thead th.cal-month:hover{background:#172e6b}
  #cal-tbl thead th.cal-day{background:#22c55e;font-weight:500;padding:4px 6px;min-width:40px}
  #cal-tbl thead th.cal-day .dn{font-size:9px;opacity:.75;display:block}
  #cal-tbl thead th.cal-org{background:#15803d;text-align:left;min-width:260px;position:sticky;left:0;z-index:2}
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
  #cal-tbl tfoot td{background:#ecfdf5;font-weight:700;padding:6px 8px;font-size:11.5px}
  #cal-tbl tfoot td.cal-num{text-align:right}
  #cal-tbl tfoot td.cal-org-cell{position:sticky;left:0;background:#dcfce7;text-align:left}

  /* calendario de pagos (Compra) — mismo estilo */
  #cal-cp-tbl{font-size:12px}
  #cal-cp-tbl thead th{cursor:default;font-size:10.5px;padding:6px 8px}
  #cal-cp-tbl thead th.cal-month{background:#15803d;cursor:pointer}
  #cal-cp-tbl thead th.cal-month:hover{background:#172e6b}
  #cal-cp-tbl thead th.cal-day{background:#22c55e;font-weight:500;padding:4px 6px;min-width:40px}
  #cal-cp-tbl thead th.cal-day .dn{font-size:9px;opacity:.75;display:block}
  #cal-cp-tbl thead th.cal-org{background:#15803d;text-align:left;min-width:260px;position:sticky;left:0;z-index:2}
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
  #cx-matrix thead th{background:#15803d;color:#fff;padding:8px 6px;font-size:10.5px;text-transform:uppercase;letter-spacing:.2px;text-align:center;border-right:1px solid rgba(255,255,255,.08);position:sticky;top:0;z-index:1}
  #cx-matrix thead th.cx-cliente-h{text-align:left;background:#0f172a;min-width:240px;position:sticky;left:0;z-index:3}
  #cx-matrix thead th.cx-pct-cli{background:#7c2d12;min-width:60px}
  #cx-matrix thead th.cx-precio-cli{background:#0d9488;min-width:75px}
  #cx-matrix thead th.cx-comprador{background:#15803d;min-width:95px;cursor:default}
  #cx-matrix thead th.cx-comprador .pct{display:block;font-size:9.5px;background:#f59e0b;color:#451a03;padding:1px 4px;border-radius:8px;margin-top:3px;font-weight:600}
  #cx-matrix thead th.cx-comprador .pct.zero{background:#fee2e2;color:#7f1d1d}
  #cx-matrix tbody td{padding:6px 5px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums}
  #cx-matrix tbody td.cx-cli-name{text-align:left;font-weight:500;background:#fff;position:sticky;left:0;z-index:2;border-right:2px solid var(--line)}
  #cx-matrix tbody tr:nth-child(even) td:not(.cx-cli-name){background:#fafbff}
  #cx-matrix tbody tr:hover td{background:#eff5ff}
  #cx-matrix tbody td.cx-pct-cell{background:#fef3c7;color:#92400e;font-weight:600}
  #cx-matrix tbody td.cx-precio-cell{background:#ccfbf1;color:#134e4a;font-weight:600}
  #cx-matrix tbody td.cx-empty{color:#cbd5e1}
  #cx-matrix tfoot td{background:#ecfdf5;font-weight:700;padding:8px 6px;font-size:11.5px;border-top:2px solid var(--blue);position:sticky;bottom:0;z-index:1;text-align:right}
  #cx-matrix tfoot td.cx-foot-lbl{text-align:left;background:#dcfce7;color:var(--blue);position:sticky;left:0;z-index:2}

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
  #pn-tabla thead th{background:#15803d;color:#fff;padding:4px 4px;font-size:9px;text-transform:uppercase;letter-spacing:.2px;border-right:1px solid rgba(255,255,255,.1);text-align:center}
  #pn-tabla thead th.pn-prod{background:#0f172a;text-align:left;position:sticky;left:0;z-index:3;min-width:150px}
  #pn-tabla thead th.grp{background:#7c2d12;border-bottom:2px solid #fed7aa}
  #pn-tabla thead th.grp-prod{background:#15803d;border-bottom:2px solid #86efac}
  #pn-tabla thead th.grp-compra{background:#15803d;border-bottom:2px solid #93c5fd}
  #pn-tabla thead th.grp-venta{background:#9a3412;border-bottom:2px solid #fdba74}
  #pn-tabla thead th.grp-resultado{background:#581c87;border-bottom:2px solid #d8b4fe}
  #pn-tabla tbody td{padding:3px 5px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums;font-size:11px}
  #pn-tabla tbody td.pn-prod-cell{text-align:left;font-weight:500;background:#fff;position:sticky;left:0;z-index:2;border-right:2px solid var(--line)}
  #pn-tabla tbody tr.pn-grupo td{background:#fffbeb;font-weight:700;color:#92400e;border-top:2px solid #fcd34d;font-size:12px}
  #pn-tabla tbody tr.pn-grupo td.pn-prod-cell{background:#fef3c7}
  #pn-tabla tbody tr.pn-total td{background:#dcfce7;font-weight:700;color:#15803d;border-top:2px solid var(--blue);font-size:12px}
  #pn-tabla tbody tr.pn-total td.pn-prod-cell{background:#bbf7d0}
  #pn-tabla tbody td input{width:100%;border:1px solid transparent;background:transparent;padding:2px 4px;text-align:right;font-size:11px;font-family:inherit;font-variant-numeric:tabular-nums;border-radius:3px;color:inherit}
  #pn-tabla tbody td input:hover{border-color:var(--line);background:#fff}
  #pn-tabla tbody td input:focus{border-color:var(--blue);background:#fff;outline:none;box-shadow:0 0 0 2px rgba(59,130,246,.15)}
  #pn-tabla tbody td.editable{background:#fffbeb}
  #pn-tabla tbody td.editable input{color:#92400e;font-weight:500}
  #pn-tabla tbody td.calc{background:#f0fdf4;color:#15803d;font-weight:500}
  #pn-tabla tbody td.pos-pos{background:#dcfce7;color:#15803d;font-weight:700}
  #pn-tabla tbody td.pos-neg{background:#fee2e2;color:#991b1b;font-weight:700}
  #pn-tabla tfoot td{background:#15803d;color:#fff;font-weight:700;padding:6px 8px;font-size:12px;border-top:2px solid #0f172a;position:sticky;bottom:0}
  #pn-tabla tfoot td.pn-prod-cell{background:#0f172a;position:sticky;left:0;z-index:1}

  /* ===== Drill-down de la Posicion Granaria ===== */
  #pn-tabla tbody td.pn-drill-cell-link{cursor:pointer;position:relative;text-decoration:underline dotted rgba(21,128,61,.4);text-underline-offset:2px}
  #pn-tabla tbody td.pn-drill-cell-link:hover{background:#bbf7d0 !important;box-shadow:inset 0 0 0 1px #16a34a}
  #pn-tabla tbody td.pn-drill-active{background:#15803d !important;color:#fff !important;box-shadow:inset 0 0 0 2px #052e16}
  #pn-tabla tbody tr.pn-drill-row td{padding:0;background:#f8fafc;border-bottom:2px solid #cbd5e1}
  #pn-tabla tbody tr.pn-drill-row td.pn-prod-cell{position:static}
  .pn-drill-inner{padding:10px 14px 12px 40px}
  .pn-drill-head{font-size:11.5px;font-weight:700;color:#0f172a;margin-bottom:6px;text-transform:none;letter-spacing:0;text-align:left}
  .pn-drill-head span{font-weight:500;color:var(--muted)}
  .pn-drill-empty{font-size:11.5px;color:var(--muted);padding:4px 0}
  table.pn-drill-tbl{border-collapse:collapse;width:auto;min-width:520px;background:#fff;border:1px solid #e2e8f0;border-radius:6px;overflow:hidden}
  table.pn-drill-tbl th{background:#0f172a;color:#fff;font-size:9.5px;text-transform:uppercase;letter-spacing:.3px;padding:5px 9px;text-align:left;font-weight:600}
  table.pn-drill-tbl th.num,table.pn-drill-tbl td.num{text-align:right;font-variant-numeric:tabular-nums}
  table.pn-drill-tbl td{padding:4px 9px;font-size:11px;border-bottom:1px solid #eef2f7;text-align:left;color:#1e293b}
  table.pn-drill-tbl tbody tr:nth-child(even) td{background:#f8fafc}
  table.pn-drill-tbl td.pn-drill-nro{font-weight:700;color:#0f172a;white-space:nowrap}
  table.pn-drill-tbl td.pn-drill-fe{white-space:nowrap;color:var(--muted);font-size:10.5px}
  table.pn-drill-tbl tr.pn-drill-tot td{background:#ecfdf5 !important;font-weight:800;color:#15803d;border-top:2px solid #86efac}
  .pn-fij-si{color:#15803d;font-weight:700}
  .pn-fij-par{color:#b45309;font-weight:700}
  .pn-fij-no{color:#b91c1c;font-weight:700}

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
  .pn-card .bar-cobertura > div{height:100%;background:linear-gradient(90deg,#22c55e,#15803d)}
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
  #pg-tbl tfoot td{background:#ecfdf5;font-weight:700;padding:8px 10px;font-size:13px;border-top:2px solid var(--blue);position:sticky;bottom:0}
  #pg-tbl tfoot td.num{text-align:right;color:var(--blue);font-variant-numeric:tabular-nums}

  /* Modo lectura (sin PAT configurado):
     Sin PAT los cambios igual se editan y guardan en localStorage; solo se pierde el auto-backup
     al repo. Por eso solo escondemos el boton viejo de config (reemplazado por "Administración"). */
  body.pg-reader #pg-autobackup-cfg{ display:none !important }
  #pg-reader-banner{display:none}
  body.pg-reader #pg-reader-banner{display:flex;align-items:center;gap:12px;padding:10px 14px;background:#fef3c7;border-left:4px solid #f59e0b;border-radius:8px;color:#854d0e;font-size:13px;margin-bottom:12px}
  body.pg-reader #pg-reader-banner .lbl{font-weight:700}

  /* tabla con fila de totales sticky al pie */
  table tfoot td{background:#ecfdf5;font-weight:700;padding:8px 10px;font-size:12.5px;border-top:2px solid var(--blue);position:sticky;bottom:0;z-index:1}
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

  /* ====== Calculadoras (Canje / Proforma) ====== */
  .calc-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .calc-grid label{display:block;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);margin-bottom:4px}
  .calc-grid input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:7px;font-size:13.5px;font-family:inherit;color:var(--ink);text-align:right;font-variant-numeric:tabular-nums}
  .calc-grid input:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 2px rgba(59,130,246,.15)}
  .calc-result-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .calc-card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}
  .calc-card .lbl{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
  .calc-card .val{font-size:22px;font-weight:700;color:var(--ink);margin-top:4px;font-variant-numeric:tabular-nums}
  .calc-card .hint{font-size:11px;color:var(--muted);margin-top:2px}
  .calc-card.highlight{background:linear-gradient(135deg,#15803d 0%,#22c55e 100%);border:none}
  .calc-card.highlight .lbl{color:rgba(255,255,255,.85)}
  .calc-card.highlight .val{color:#fff;font-size:28px}
  .calc-card.highlight .hint{color:rgba(255,255,255,.75)}
  .calc-card.subtle{background:#f8fafc;border-color:#e2e8f0}
  .calc-card.subtle .val{font-size:18px;color:#475569}

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
  .mb-chip.cat{background:#dcfce7;color:#1e40af}
  .mb-meta{font-size:11.5px;color:var(--muted);display:flex;flex-wrap:wrap;gap:8px;align-items:center}
  .mb-meta .sender{font-weight:600;color:#15803d}
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
    <a class="campana campana-home" data-go-tab="home" data-title="Resumen Comercial">🏠 Resumen comercial</a>
    <nav class="nav">
      <div class="nav-section" data-section="compra">
        <button class="nav-group" type="button" aria-expanded="true"><span class="nav-arrow">▾</span> Compra</button>
        <div class="nav-items">
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-posicion" data-title="Compra · Posición General">Posición General</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-financiera" data-title="Compra · Financiera">Financiera</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-canjes" data-title="Compra · Canjes">Canjes</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-canje-liq" data-title="Compra · Análisis de Canje de Compras">🔄 Análisis Canje Compras</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-finales-pend" data-title="Compra · Finales Pendientes">🧾 Finales Pendientes</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-finales" data-title="Compra · Finales de Compra">🧮 Finales de Compra</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-cruce" data-title="Compra · Cruce Cliente × Comprador">Cruce Cliente × Comprador</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="pg-pagos" data-title="Compra · Proyectado Pagos Granos">Proyectado Pagos</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-calc-canje" data-title="Compra · Calculador de Canje">🔄 Calculador Canje</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-calc-proforma" data-title="Compra · Calculador de Proforma">📄 Calculador Proforma</a>
          <a class="nav-item" data-go-tab="compra" data-go-sub="cp-traza" data-title="Compra · Trazabilidad">📦 Trazabilidad</a>
        </div>
      </div>
      <div class="nav-section" data-section="venta">
        <button class="nav-group" type="button" aria-expanded="true"><span class="nav-arrow">▾</span> Venta</button>
        <div class="nav-items">
          <a class="nav-item" data-go-tab="venta" data-go-sub="posicion" data-title="Venta · Posición General">Posición General</a>
          <a class="nav-item" data-go-tab="venta" data-go-sub="financiera" data-title="Venta · Financiera">Financiera</a>
          <a class="nav-item" data-go-tab="venta" data-go-sub="vt-precios" data-title="Venta · Precios por Contrato">💰 Precios por Contrato</a>
        </div>
      </div>
      <div class="nav-section" data-section="posicion">
        <button class="nav-group" type="button" aria-expanded="true"><span class="nav-arrow">▾</span> Posición General</button>
        <div class="nav-items">
          <a class="nav-item" data-go-tab="posicion" data-go-sub="pn-granaria" data-title="Posición Granaria">Posición Granaria</a>
          <a class="nav-item" data-go-tab="posicion" data-go-sub="pn-financiera" data-title="Posición Financiera">Posición Financiera</a>
          <a class="nav-item" data-go-tab="posicion" data-go-sub="pn-taqueo" data-title="Taqueo CTG">🔎 Taqueo CTG</a>
        </div>
      </div>
      <div class="nav-section" data-section="contratos">
        <button class="nav-group" type="button" aria-expanded="true"><span class="nav-arrow">▾</span> Contratos</button>
        <div class="nav-items">
          <a class="nav-item" data-go-tab="contratos" data-go-sub="ct-compra" data-title="Códigos de Contratos · Compra">Códigos Compra</a>
          <a class="nav-item" data-go-tab="contratos" data-go-sub="ct-venta" data-title="Códigos de Contratos · Venta">Códigos Venta</a>
        </div>
      </div>
      <div class="nav-section nav-internal" data-section="personal" style="display:none">
        <button class="nav-group" type="button" aria-expanded="true"><span class="nav-arrow">▾</span> Personal</button>
        <div class="nav-items">
          <a class="nav-item" data-go-tab="personal" data-go-sub="mb-bandeja" data-title="Mi Bandeja · Pendientes de Mail">📬 Mi Bandeja</a>
        </div>
      </div>
    </nav>
  </aside>

  <div class="main">
    <header class="topbar">
      <div style="display:flex;align-items:center;gap:12px">
        <button class="menu-toggle" id="menu-toggle" aria-label="Menú">☰</button>
        <div class="topbar-title" id="topbar-title">Resumen Comercial</div>
      </div>
      <div class="topbar-right">
        <div class="topbar-meta"><span class="dot"></span>Actualizado: __BUILD_TIME__</div>
        <button class="admin-pill" id="btn-admin" style="display:none">Administración</button>
        <a class="logout-btn" href="/logout">⤴ Salir</a>
      </div>
    </header>
    <div class="content">

  <div class="tabs">
    <div class="tab active" data-tab="home">INICIO</div>
    <div class="tab" data-tab="compra">COMPRA <span class="count" id="cnt-compra">0</span></div>
    <div class="tab" data-tab="venta">VENTA <span class="count" id="cnt-venta">0</span></div>
    <div class="tab" data-tab="posicion">POSICIÓN GENERAL <span class="count" id="cnt-pos">0</span></div>
    <div class="tab" data-tab="contratos">CONTRATOS <span class="count" id="cnt-contratos">0</span></div>
    <div class="tab nav-internal" data-tab="personal" style="display:none">PERSONAL <span class="count" id="cnt-personal">0</span></div>
  </div>

  <!-- ============ HOME / PORTADA ============ -->
  <div class="panel active" data-panel="home">
    <div class="home-hero">
      <div class="home-hero-bg"></div>
      <div class="home-hero-content">
        <div class="home-greeting">
          <div class="home-greet-eyebrow">PORTAL DE GRANOS · AGRONASAJA SRL</div>
          <h1 class="home-title">Bienvenido al tablero comercial 🌾</h1>
          <div class="home-sub">Resumen consolidado de la operación de granos · Compra · Venta · Stock · Cerealeras</div>
        </div>
        <div class="home-kpis" id="home-kpis"></div>
      </div>
    </div>

    <div class="home-shortcuts">
      <h3 style="margin:0 0 12px;color:var(--ink);font-size:15px">Atajos</h3>
      <div class="home-cards">
        <a class="home-card" data-go-tab="compra" data-go-sub="cp-posicion">
          <div class="home-card-icon" style="background:#dbeafe;color:#1e3a8a">📥</div>
          <div class="home-card-title">Compra</div>
          <div class="home-card-desc">Posición · Canjes · Calculadores · Trazabilidad</div>
        </a>
        <a class="home-card" data-go-tab="venta" data-go-sub="posicion">
          <div class="home-card-icon" style="background:#fde68a;color:#92400e">📤</div>
          <div class="home-card-title">Venta</div>
          <div class="home-card-desc">Contratos · Precios · Financiera</div>
        </a>
        <a class="home-card" data-go-tab="posicion" data-go-sub="pn-granaria">
          <div class="home-card-icon" style="background:#dcfce7;color:#166534">📊</div>
          <div class="home-card-title">Posición Granaria</div>
          <div class="home-card-desc">Cosecha × Producto · Stock vs Comprometido</div>
        </a>
        <a class="home-card" data-go-tab="compra" data-go-sub="cp-traza">
          <div class="home-card-icon" style="background:#fed7aa;color:#7c2d12">📦</div>
          <div class="home-card-title">Trazabilidad</div>
          <div class="home-card-desc">CTG → Cerealera · Cargill · LDC · ACA · Allaria · FYO · Bunge · COFCO · Intagro</div>
        </a>
        <a class="home-card" data-go-tab="compra" data-go-sub="pg-pagos">
          <div class="home-card-icon" style="background:#fce7f3;color:#9f1239">💰</div>
          <div class="home-card-title">Proyectado Pagos</div>
          <div class="home-card-desc">Calendario · KV sincronizado</div>
        </a>
        <a class="home-card" data-go-tab="contratos" data-go-sub="ct-compra">
          <div class="home-card-icon" style="background:#e9d5ff;color:#581c87">📋</div>
          <div class="home-card-title">Códigos de Contratos</div>
          <div class="home-card-desc">Compra · Venta</div>
        </a>
      </div>
    </div>
  </div>

  <!-- ============ COMPRA ============ -->
  <div class="panel" data-panel="compra">

    <!-- SUB-TABS dentro de COMPRA -->
    <div class="subtabs">
      <button class="subtab active" data-sub="cp-posicion">Posición General</button>
      <button class="subtab" data-sub="cp-financiera">Financiera</button>
      <button class="subtab" data-sub="cp-canjes">Canjes</button>
      <button class="subtab" data-sub="cp-canje-liq">🔄 Análisis Canje Compras</button>
      <button class="subtab" data-sub="cp-finales-pend">🧾 Finales Pendientes</button>
      <button class="subtab" data-sub="cp-finales">🧮 Finales de Compra</button>
      <button class="subtab" data-sub="cp-cruce">Cruce Cliente × Comprador</button>
      <button class="subtab" data-sub="pg-pagos">📅 Proyectado Pagos Granos</button>
      <button class="subtab" data-sub="cp-calc-canje">🔄 Calc. Canje</button>
      <button class="subtab" data-sub="cp-calc-proforma">📄 Calc. Proforma</button>
      <button class="subtab" data-sub="cp-traza">📦 Trazabilidad</button>
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

      <details class="section section-collapsible" data-collapse="resumen-cp" open>
        <summary><span class="collapse-arrow">▾</span> Resumen por Producto <span class="badge" id="grain-meta-cp"></span></summary>
        <div class="grain-grid" id="grain-grid-cp"></div>
      </details>

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

    <!-- ========== SUB: FINALES DE COMPRA ========== -->
    <div class="subpanel" data-sub-panel="cp-finales">
      <div class="section" style="background:linear-gradient(135deg,#eff6ff,#dbeafe);border:1px solid #93c5fd">
        <h3>🧮 Finales de Compra — Verificador de Factor <span class="badge" id="fl-meta"></span></h3>
        <p style="margin:6px 0 0;font-size:12.5px;color:var(--muted)">
          Datos traídos de la <b>balanza</b>. Calculo el <b>factor oficial de cámara</b> (soja: daños 5% → −1/pt; verdes 5% → −0,2/pt; quebrados 20%; mat. extraña 1%) y lo comparo con el de la cerealera.
          <b>⚠️ Revisar</b> = difieren (puede ser error de la cerealera o un ajuste comercial). <b>Calc.</b> = lo calculé yo (la cerealera no lo cargó).
        </p>
      </div>

      <div class="filterbar">
        <div style="flex:1"><label>AGREGAR CONTRATO / CTG / CARTA PORTE</label>
          <input type="text" id="fl-add" placeholder="escribí el nº de contrato o CTG y Enter…" style="min-width:300px"></div>
        <button class="clear" id="fl-add-btn" style="background:#1e3a8a;color:#fff;border-color:#1e3a8a">+ Agregar</button>
        <button class="clear" id="fl-clear" style="color:var(--red);border-color:#fecaca">🗑️ Vaciar lista</button>
        <button class="clear" id="fl-xls" style="background:#16a34a;color:#fff;border-color:#16a34a">⬇️ Excel</button>
        <div class="count" id="fl-count">0 cargados</div>
      </div>
      <div id="fl-msg" style="margin:0 0 10px;font-size:12.5px;min-height:18px"></div>

      <div class="section">
        <div class="tbl-wrap" style="max-height:680px">
          <table id="fl-tbl">
            <thead><tr id="fl-head"></tr></thead>
            <tbody id="fl-body"></tbody>
            <tfoot id="fl-foot"></tfoot>
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
        <div><label>CAMPAÑA</label><select id="cj-camp"><option value="">Todas</option></select></div>
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

    <!-- ========== SUB: ANÁLISIS DE CANJE DE COMPRAS (pendiente liquidar por comercial) ========== -->
    <div class="subpanel" data-sub-panel="cp-canje-liq">
      <div class="section" style="background:linear-gradient(135deg,#0f766e,#115e59);color:#fff;border:none">
        <h3 style="color:#fff;margin:0">🔄 Análisis de Canje de Compras · Pendiente de liquidar por comercial</h3>
        <div style="font-size:12px;opacity:.9;margin-top:4px;color:#fff">
          Contratos de <b>compra de granos</b> con mercadería <b>entregada que falta liquidar</b> (entregado − liquidado), y si esa cantidad <b>tiene precio (fijación)</b> o no. Filtrá por comercial para ir cerrando con cada uno.
        </div>
      </div>

      <div class="kpis" id="clq-kpis" style="margin-top:14px"></div>

      <div class="filterbar" style="margin-top:12px">
        <div><label>COMERCIAL</label><select id="clq-com"><option value="">Todos</option></select></div>
        <div><label>GRANO</label><select id="clq-grano"><option value="">Todos</option></select></div>
        <div><label>CAMPAÑA</label><select id="clq-camp"><option value="">Todas</option></select></div>
        <div><label>¿A PRECIO?</label><select id="clq-precio"><option value="">Todos</option><option value="si">Con precio</option><option value="parcial">Parcial</option><option value="no">Sin precio</option></select></div>
        <div><label>BUSCAR PROVEEDOR / CONTRATO</label><input type="text" id="clq-q" placeholder="razón social o nº…" style="min-width:200px"></div>
        <button class="clear" id="clq-reset">Limpiar</button>
      </div>

      <div class="section">
        <h3>Resumen por comercial <span class="badge">tn entregado sin liquidar · cuánto tiene precio</span></h3>
        <div style="overflow-x:auto"><table class="tbl" id="clq-resumen"><thead></thead><tbody></tbody></table></div>
      </div>

      <div class="section">
        <h3>Detalle por contrato <span class="badge" id="clq-det-meta">—</span></h3>
        <div style="overflow-x:auto"><table class="tbl" id="clq-detalle"><thead></thead><tbody></tbody></table></div>
      </div>

      <div style="margin-top:14px;padding:12px;background:#fff;border-radius:10px;border:1px solid var(--line);font-size:12.5px;color:var(--muted);line-height:1.55">
        💡 <b>Pendiente liquidar</b> = tn entregadas por el proveedor que todavía no liquidaste (entregado − liquidado, campo de Finnegans). <b>¿A precio?</b> sale de la fijación del contrato de compra: <span class="chip ok">A precio</span>/<span class="chip ok">Fijado</span> = tiene precio cerrado · <span class="chip warn">Fijado X%</span> = parcial · <span class="chip info">A fijar (sin precio)</span> = falta ponerle precio. El <b>comercial</b> se toma de la cuenta del proveedor (composición de saldos).
      </div>
    </div>

    <!-- ========== SUB: FINALES PENDIENTES (cola de trabajo con semáforo) ========== -->
    <div class="subpanel" data-sub-panel="cp-finales-pend">
      <div class="section" style="background:linear-gradient(135deg,#0f766e,#115e59);color:#fff;border:none">
        <h3 style="color:#fff;margin:0">🧾 Finales Pendientes · cola de trabajo</h3>
        <div style="font-size:12px;opacity:.9;margin-top:4px;color:#fff">
          Contratos de <b>compra de granos ya liquidados</b> (entregado pendiente de liquidar = 0) → a esos hay que <b>hacerles la final</b>. 🔴 <b>Por hacer</b> · 🟡 <b>Enviada a admin</b> · 🟢 <b>Hecha</b>. Los que no están liquidados o tienen liquidación parcial no aparecen (todavía no se puede hacer la final). Default: campaña actual.
        </div>
      </div>

      <div class="kpis" id="fp-kpis" style="margin-top:14px"></div>

      <div class="filterbar" style="margin-top:12px">
        <div><label>ESTADO</label><select id="fp-estado"><option value="">Todos</option><option value="pendiente">🔴 Pendiente</option><option value="enviada">🟡 Enviada a admin</option><option value="hecha">🟢 Hecha</option></select></div>
        <div><label>GRANO</label><select id="fp-grano"><option value="">Todos</option></select></div>
        <div><label>CAMPAÑA</label><select id="fp-camp"><option value="">Todas</option></select></div>
        <div><label>BUSCAR PROVEEDOR / CONTRATO</label><input type="text" id="fp-q" placeholder="razón social o nº…" style="min-width:200px"></div>
        <button class="clear" id="fp-reset">Limpiar</button>
      </div>

      <div class="section">
        <h3>Cola de finales <span class="badge" id="fp-meta">—</span></h3>
        <div style="overflow-x:auto"><table class="tbl" id="fp-tabla"><thead></thead><tbody></tbody></table></div>
      </div>

      <div style="margin-top:14px;padding:12px;background:#fff;border-radius:10px;border:1px solid var(--line);font-size:12.5px;color:var(--muted);line-height:1.55">
        💡 <b>Plan para ir metiendo finales</b>: la cola son los contratos <b>ya liquidados</b> a los que falta hacerles la final (🔴), ordenados por fecha (más viejo primero). Cuando la mandás a las administrativas, apretá <b>“Enviar a admin”</b>: pasa a 🟡 y <b>abre un correo pre-armado</b> con los datos del contrato (la primera vez te pide el email de administración y lo guarda). Cuando ya quedó cargada, apretá <b>“Hecha”</b> y pasa a 🟢. Filtrás por campaña (arranca en la actual). Fuente: contratos de compra de Finnegans (se refresca en cada deploy); el estado 🟡/🟢 se guarda <b>en la nube, compartido entre todos</b> (se sincroniza solo cada 15s).
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

    <!-- ========== SUB: CALCULADOR DE CANJE ========== -->
    <div class="subpanel" data-sub-panel="cp-calc-canje">
      <div class="section">
        <h3>🔄 Calculador de Canje <span class="badge">cliente paga su deuda con grano</span></h3>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start">

          <div>
            <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px">📝 Datos del canje</div>
            <div class="calc-grid">
              <div><label>Deuda cliente (USD con IVA)</label><input type="number" id="cnj-deuda" step="0.01" value="5976.09"/></div>
              <div><label>Precio commodity (USD/Tn)</label><input type="number" id="cnj-precio" step="0.01" value="308.58"/></div>
              <div><label>% Liquidación (típ. 100%)</label><input type="number" id="cnj-liq" step="0.01" value="100"/></div>
              <div><label>Tipo de cambio (ARS/USD)</label><input type="number" id="cnj-tc" step="0.01" value="1356.5"/></div>
              <div><label>% IVA</label><input type="number" id="cnj-iva" step="0.001" value="10.5"/></div>
              <div><label>% Comisión</label><input type="number" id="cnj-com" step="0.01" value="2"/></div>
              <div><label>% Sellado + registro</label><input type="number" id="cnj-sel" step="0.01" value="1.25"/></div>
              <div><label>% Perc. IVA (depende SISA)</label><input type="number" id="cnj-perc" step="0.01" value="0"/></div>
              <div><label>% Ret. IIBB</label><input type="number" id="cnj-ib" step="0.01" value="0"/></div>
            </div>
            <button class="clear" id="cnj-reset" style="margin-top:14px">↺ Resetear a valores ejemplo</button>
          </div>

          <div>
            <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px">📊 Resultados</div>
            <div class="calc-result-grid">
              <div class="calc-card"><div class="lbl">Precio Neto (USD/Tn)</div><div class="val" id="cnj-out-precio-neto">—</div><div class="hint">= Precio − Comisión − Sellado − Perc.IVA − Ret.IIBB</div></div>
              <div class="calc-card"><div class="lbl">Precio Liquidable (USD/Tn)</div><div class="val" id="cnj-out-precio-liq">—</div><div class="hint">= Precio Neto × % Liquidación</div></div>
              <div class="calc-card highlight"><div class="lbl">TONELADAS a entregar</div><div class="val" id="cnj-out-tn">—</div><div class="hint" id="cnj-out-kg">— kg</div></div>
              <div class="calc-card"><div class="lbl">Total USD (deuda)</div><div class="val" id="cnj-out-total-usd">—</div></div>
              <div class="calc-card"><div class="lbl">Total ARS</div><div class="val" id="cnj-out-total-ars">—</div></div>
              <div class="calc-card subtle"><div class="lbl">Comisión $</div><div class="val" id="cnj-out-com">—</div></div>
              <div class="calc-card subtle"><div class="lbl">Sellado + Reg. $</div><div class="val" id="cnj-out-sel">—</div></div>
            </div>
          </div>
        </div>

        <div style="margin-top:18px;padding:12px;background:#fffbeb;border-radius:10px;border-left:4px solid #f59e0b;font-size:12.5px;color:#92400e;line-height:1.6">
          <b>📌 Procedimiento (memo):</b><br/>
          1. Poner deuda cliente final <b>con IVA</b><br/>
          2. Poner precio commodity actual y el <b>% de liquidación</b> (típ. 100%; si el grano liquida a menos, el cliente entrega más toneladas)<br/>
          3. Chequear % comisiones y gastos (sellado 1,02 / cámara 1,25 según corresponda)<br/>
          4. Avisar al cliente que se hace una ND del 1% IVA por la percepción<br/>
          5. Si hay gastos de calidad o acondicionamiento, sumarlos aparte
        </div>
      </div>
    </div>

    <!-- ========== SUB: CALCULADOR DE PROFORMA ========== -->
    <div class="subpanel" data-sub-panel="cp-calc-proforma">
      <div class="section">
        <h3>📄 Calculador de Proforma <span class="badge">para liquidar contrato vendido a cerealera</span></h3>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start">

          <div>
            <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px">📝 Datos de la proforma</div>
            <div class="calc-grid">
              <div><label>Toneladas</label><input type="number" id="prf-tn" step="0.01" value="282.42"/></div>
              <div><label>Precio (USD/Tn)</label><input type="number" id="prf-precio" step="0.01" value="190"/></div>
              <div><label>Tipo de cambio (ARS/USD)</label><input type="number" id="prf-tc" step="0.01" value="1347.5"/></div>
              <div><label>% Liquidación (típ. 100%)</label><input type="number" id="prf-liq" step="0.01" value="100"/></div>
              <div><label>% Comisión</label><input type="number" id="prf-com" step="0.01" value="1.5"/></div>
              <div><label>Tarifa cámara ($/cam)</label><input type="number" id="prf-cam-tarifa" step="0.01" value="22553"/></div>
              <div><label>N° camiones</label><input type="number" id="prf-cam-n" step="1" value="8"/></div>
              <div><label>% Gastos entrega/lab</label><input type="number" id="prf-gtos" step="0.01" value="1.25"/></div>
              <div><label>% IVA</label><input type="number" id="prf-iva" step="0.01" value="12.1"/></div>
            </div>

            <div style="margin-top:14px;padding:10px;background:#eef2ff;border-radius:8px;font-size:11.5px;color:var(--blue);font-weight:600">
              <label style="font-size:10px;color:var(--blue);text-transform:uppercase">📋 Régimen SISA</label>
              <div style="display:flex;gap:8px;margin-top:6px">
                <label style="font-weight:500"><input type="radio" name="prf-sisa" value="none" checked/> Sin retenciones (canje)</label>
                <label style="font-weight:500"><input type="radio" name="prf-sisa" value="sisa1"/> SISA 1</label>
                <label style="font-weight:500"><input type="radio" name="prf-sisa" value="sisa2"/> SISA 2</label>
              </div>
            </div>

            <button class="clear" id="prf-reset" style="margin-top:14px">↺ Resetear a valores ejemplo</button>
          </div>

          <div>
            <div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px">📊 Resultados</div>
            <div class="calc-result-grid">
              <div class="calc-card subtle"><div class="lbl">Precio en $/Tn</div><div class="val" id="prf-out-precio-ars">—</div></div>
              <div class="calc-card"><div class="lbl">Total Pesos (bruto)</div><div class="val" id="prf-out-total">—</div></div>
              <div class="calc-card subtle"><div class="lbl">Comisión $</div><div class="val" id="prf-out-com">—</div></div>
              <div class="calc-card subtle"><div class="lbl">Cámara $</div><div class="val" id="prf-out-cam">—</div></div>
              <div class="calc-card subtle"><div class="lbl">Gastos entrega/lab</div><div class="val" id="prf-out-gtos">—</div></div>
              <div class="calc-card highlight"><div class="lbl">A FACTURAR (Sin IVA)</div><div class="val" id="prf-out-fact">—</div></div>
              <div class="calc-card"><div class="lbl">A FACTURAR (Con IVA)</div><div class="val" id="prf-out-fact-iva">—</div></div>
              <div class="calc-card subtle"><div class="lbl">Equivalente USD</div><div class="val" id="prf-out-usd">—</div></div>
            </div>

            <div id="prf-sisa-box" style="margin-top:14px;padding:12px;background:#fef3c7;border-radius:10px;border-left:4px solid #d97706;display:none">
              <div style="font-weight:700;font-size:12.5px;color:#92400e;margin-bottom:6px" id="prf-sisa-titulo">Régimen SISA</div>
              <table style="width:100%;font-size:12px;color:#78350f">
                <tr><td>Bruto:</td><td class="num" id="prf-sisa-bruto">—</td></tr>
                <tr><td>Comisión:</td><td class="num" id="prf-sisa-com">—</td></tr>
                <tr><td>Gastos lab:</td><td class="num" id="prf-sisa-gtos">—</td></tr>
                <tr><td>Subtotal:</td><td class="num" id="prf-sisa-sub">—</td></tr>
                <tr><td>+ IVA:</td><td class="num" id="prf-sisa-iva">—</td></tr>
                <tr id="prf-sisa-iva-ret"><td>− Ret. IVA:</td><td class="num"></td></tr>
                <tr id="prf-sisa-gan"><td>− Ret. Ganancias 2%:</td><td class="num"></td></tr>
                <tr style="font-weight:700;border-top:1px solid #d97706"><td>TOTAL A PAGAR:</td><td class="num" id="prf-sisa-total">—</td></tr>
              </table>
            </div>
          </div>
        </div>

        <div style="margin-top:18px;padding:12px;background:#fffbeb;border-radius:10px;border-left:4px solid #f59e0b;font-size:12.5px;color:#92400e;line-height:1.6">
          <b>📌 Notas:</b> las liquidaciones por <b>canje no llevan retenciones</b> (elegí "Sin retenciones"). Las liquidaciones a pagar usan SISA 1 (Ret. IVA 5%) o SISA 2 (Ret. IVA 7% + Ret. Ganancias 2%) según el cliente.
        </div>
      </div>
    </div>

    <!-- ========== SUB: TRAZABILIDAD ========== -->
    <div class="subpanel" data-sub-panel="cp-traza">

      <div class="section" style="background:linear-gradient(135deg,#0c4a6e 0%,#0891b2 100%);color:#fff;border:none">
        <h3 style="color:#fff;margin:0">📦 Trazabilidad de Compra · CP → Traslado → Liquidación</h3>
        <div style="font-size:12px;opacity:.9;margin-top:4px">Cada CTG con su Carta de Porte, entregador (productor), peso, contrato de compra, contrato de venta y cerealera destino. Click en una fila para ver el detalle (COE, liquidación, comisión).</div>
      </div>

      <div class="kpis" id="tz-kpis"></div>

      <div class="filterbar" id="tz-filters">
        <div><label>ENTREGADOR</label><select id="tz-ent"><option value="">Todos</option></select></div>
        <div><label>CEREALERA</label><select id="tz-cer"><option value="">Todas</option></select></div>
        <div><label>PRODUCTO</label><select id="tz-prod"><option value="">Todos</option></select></div>
        <div><label>CONTRATO COMPRA</label><select id="tz-ccomp"><option value="">Todos</option></select></div>
        <div><label>FECHA DESDE</label><input type="date" id="tz-fdesde"/></div>
        <div><label>FECHA HASTA</label><input type="date" id="tz-fhasta"/></div>
        <div><label>BUSCAR</label><input type="text" id="tz-q" placeholder="CTG, CP, contrato…" /></div>
        <button class="clear" id="tz-clear">Limpiar</button>
        <div class="count" id="tz-count">0 / 0</div>
      </div>

      <div class="section">
        <h3>Detalle por CTG <span class="badge">click en header para ordenar · click en fila para ver detalle</span></h3>
        <div class="tbl-wrap" style="max-height:680px">
          <table id="tz-tbl">
            <thead><tr id="tz-head"></tr></thead>
            <tbody id="tz-body"></tbody>
            <tfoot id="tz-foot"></tfoot>
          </table>
        </div>
      </div>

      <div style="margin-top:14px;padding:12px;background:#fff;border-radius:10px;border:1px solid var(--line);font-size:12.5px;color:var(--muted);line-height:1.55">
        💡 <b>Cómo se lee</b>: cada fila es UN CTG (Carta de Porte). <b>Entregador</b> = quien la emitió (productor que entregó el grano). <b>Cerealera</b> = destinatario final (Cargill, LDC, etc.). <b>Contrato Compra</b> = COMPxxx (a quién le compramos), <b>Contrato Venta</b> = VENxxx (a quién se lo vendimos). Click en una fila → consulta a Finnegans en vivo y trae el detalle (COE, liquidación, comisión, factor).
      </div>

    </div>

  </div>

  <!-- ============ VENTA ============ -->
  <div class="panel" data-panel="venta">

    <!-- SUB-TABS dentro de VENTA -->
    <div class="subtabs">
      <button class="subtab active" data-sub="posicion">Posición General</button>
      <button class="subtab" data-sub="financiera">Financiera</button>
      <button class="subtab" data-sub="vt-precios">💰 Precios por Contrato</button>
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
      <details class="section section-collapsible" data-collapse="resumen-vt" open>
        <summary><span class="collapse-arrow">▾</span> Resumen por Grano <span class="badge" id="grain-meta"></span></summary>
        <div class="grain-grid" id="grain-grid"></div>
      </details>

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

      <!-- CALENDARIO DE COBRANZAS (tipo Excel FinancieroVenta) -->
      <div class="section" id="pl-section" style="background:linear-gradient(135deg,#eff6ff,#dbeafe);border:1px solid #93c5fd">
        <h3>📅 Calendario de Cobranzas — Pendiente de Liquidar <span class="badge" id="pl-meta"></span></h3>
        <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end;margin-bottom:12px">
          <div><label class="pl-lbl">Campaña</label><select id="pl-camp" class="pl-inp"></select></div>
          <div><label class="pl-lbl">Desde</label><input type="date" id="pl-start" value="2026-06-16" class="pl-inp"></div>
          <div><label class="pl-lbl">Días</label><input type="number" id="pl-days" value="45" min="7" max="180" class="pl-inp" style="width:80px"></div>
          <div><label class="pl-lbl">TC ARS/USD</label><input type="text" id="pl-tc" class="pl-inp" style="width:90px;text-align:right"></div>
          <button class="clear" id="pl-clear-manual" style="color:var(--red);border-color:#fecaca">🗑️ Borrar cargas manuales</button>
          <span class="badge" style="margin-left:auto">Pend. Liquidar viene del sistema · Precio, Liq. pend. de pasar e importes por día se cargan a mano (se guardan en tu navegador)</span>
        </div>
        <div class="tbl-wrap" id="pl-wrap" style="max-height:680px;overflow:auto">
          <table id="pl-tbl">
            <thead id="pl-head"></thead>
            <tbody id="pl-body"></tbody>
            <tfoot id="pl-foot"></tfoot>
          </table>
        </div>
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

      <!-- (Detalle Financiero retirado a pedido — ver Calendario de Cobranzas abajo) -->

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

    <!-- ========== SUB: PRECIOS POR CONTRATO ========== -->
    <div class="subpanel" data-sub-panel="vt-precios">

      <div class="kpis" id="vp-kpis"></div>

      <div class="filterbar">
        <div><label>EMPRESA</label><select id="vp-emp"><option value="">Todas</option></select></div>
        <div><label>CEREALERA</label><select id="vp-org"><option value="">Todas</option></select></div>
        <div><label>CORREDOR</label><select id="vp-corr"><option value="">Todos</option></select></div>
        <div><label>GRANO</label><select id="vp-prod"><option value="">Todos</option></select></div>
        <div><label>MONEDA</label><select id="vp-mon"><option value="">Todas</option></select></div>
        <div><label>CAMPAÑA</label><select id="vp-camp"><option value="">Todas</option></select></div>
        <div><label>BUSCAR</label><input type="text" id="vp-q" placeholder="número contrato…" /></div>
        <button class="clear" id="vp-clear">Limpiar</button>
        <div class="count" id="vp-count">0 / 0</div>
      </div>

      <!-- Resumen por CULTIVO (cards con precio promedio ponderado por grano) -->
      <div class="section">
        <h3>Resumen por Cultivo <span class="badge" id="vp-meta-grano">Precio promedio ponderado por toneladas fijadas — un card por cultivo</span></h3>
        <div class="grain-grid" id="vp-cards-grano"></div>
      </div>

      <!-- Resumen por Cerealera (cards con precio promedio ponderado) -->
      <div class="section">
        <h3>Resumen por Cerealera <span class="badge" id="vp-meta">Precio promedio ponderado por toneladas fijadas</span></h3>
        <div class="grain-grid" id="vp-cards"></div>
      </div>

      <!-- Detalle: cada contrato y su precio cerrado -->
      <div class="section">
        <h3>Detalle · Cada Contrato y su Precio Cerrado <span class="badge">click en encabezado para ordenar</span></h3>
        <div class="tbl-wrap" style="max-height:680px">
          <table id="vp-tbl">
            <thead><tr id="vp-tbl-head"></tr></thead>
            <tbody id="vp-tbl-body"></tbody>
            <tfoot id="vp-tbl-foot"></tfoot>
          </table>
        </div>
      </div>

      <div style="margin-top:14px;padding:12px;background:#fff;border-radius:10px;border:1px solid var(--line);font-size:12.5px;color:var(--muted);line-height:1.55">
        💡 <strong>Cómo se lee</strong>: cada fila es un contrato de venta con su <b>precio fijado</b> (cerrado con la cerealera). Los cards arriba muestran el <b>precio promedio ponderado</b> por toneladas para cada cerealera — comparás qué te paga cada uno. Click en cualquier columna del header para ordenar.
      </div>

    </div>

  </div>

  <!-- ============ POSICION GRANARIA ============ -->
  <div class="panel" data-panel="posicion">

    <!-- SUB-TABS Posicion General -->
    <div class="subtabs">
      <button class="subtab active" data-sub="pn-granaria">Posición Granaria</button>
      <button class="subtab" data-sub="pn-financiera">Posición Financiera</button>
      <button class="subtab" data-sub="pn-taqueo">🔎 Taqueo CTG</button>
    </div>

    <!-- ========== SUB: GRANARIA ========== -->
    <div class="subpanel active" data-sub-panel="pn-granaria">

    <!-- Header -->
    <div class="section" style="background:linear-gradient(135deg,#15803d 0%,#22c55e 100%);color:#fff;border:none">
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
        💡 <strong>Cómo funciona</strong>: casi todo es automático — <strong>Compra</strong> y <strong>Venta</strong> salen de los contratos de Finnegans, <strong>Planta</strong> del Stock por Depósito y <strong>Producción</strong> (Pend Cos, Cosechado) del Portal de Producción. Para la <strong>SEMILLA SOJA</strong>, Planta usa el idioma y los números del <strong>DEM-SUP Soja del Extranet Agronasaja</strong>: <strong>Granel en Campo</strong> (col C, silobolsas × merma), <strong>Granel en Semillero</strong> (col D, silos planta × merma), <strong>Stock Clasificado</strong> (col K, semilla terminada en depósitos de venta) y <strong>Corte de Bolsa</strong> (col L, pérdida — no suma al total). Del lado de venta, <strong>Prod Pendiente / Prod Despachado / Total Prod</strong> (cols S y T: pedidos de campo y despachos a producción) y <strong>Demanda Tot Pendiente</strong> (venta pendiente col O + prod pendiente) también salen del DEM-SUP. <strong>Hacé click en un número</strong> (subrayado punteado) para ver el detalle.
        <br/>🧮 <strong>Pos Pend es editable tipo Excel</strong> (por producto y en la fila TOTAL de cada cultivo): escribí un número fijo, o una fórmula que empiece con <code>=</code> usando los nombres de columnas y +, −, ×, ÷ — ej. <code>=pendcos + pendingreso - ctospe</code>. Nombres disponibles: <code>cosechado, pendcos, totalprod, planta, granelcampo, semillero, clasificado, corte, totalpc, compra, pendingreso, entregado, oferta, vtasem, pendvincular, totventa, ctospe, ctosentr, prodpend, proddesp, totalprodsem, demanda, demandapend</code>. La fórmula queda guardada (violeta) y se recalcula sola con cada dato nuevo; borrá la celda para volver al cálculo automático. La Posición no cambia (sigue siendo Oferta − Demanda).
      </div>
    </div>

    </div><!-- /subpanel pn-granaria -->

    <!-- ========== SUB: FINANCIERA ========== -->
    <div class="subpanel" data-sub-panel="pn-financiera">

      <!-- Header -->
      <div class="section" style="background:linear-gradient(135deg,#15803d 0%,#22c55e 100%);color:#fff;border:none">
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

    <!-- ===== SUBPANEL: Taqueo CTG ===== -->
    <div class="subpanel" data-sub-panel="pn-taqueo">
      <div class="section" style="background:linear-gradient(135deg,#0f766e,#115e59);color:#fff;border:none">
        <h3 style="color:#fff;margin:0">🔎 Taqueo CTG · Seguimiento fino Finnegans</h3>
        <div style="font-size:12px;opacity:.9;margin-top:4px;color:#fff" id="tq-ventana">—</div>
      </div>

      <div class="kpis" id="tq-kpis" style="margin-top:14px"></div>

      <div class="section">
        <h3>Seguimiento por grano × flujo <span class="badge">sale de campo (propio) · consignación (compra↔venta)</span></h3>
        <div style="overflow-x:auto"><table class="tbl" id="tq-seg"><thead></thead><tbody></tbody></table></div>
      </div>

      <div class="section">
        <h3>💧 Pendiente de liquidar (de lo entregado) · por cerealera <span class="badge">click en una cerealera para ver contratos y CTGs</span></h3>
        <div id="tq-pend"></div>
      </div>

      <div class="section">
        <h3>🧾 Entregado SIN liquidar · verificador de CTG <span class="badge" id="tq-liq-badge">Finnegans: entregas ↔ liquidaciones</span></h3>
        <div style="font-size:12px;color:var(--muted);margin:-4px 0 10px">
          Elegí <b>campaña</b>, <b>organización</b> y <b>cultivo</b> para ver las cartas de porte entregadas que todavía <b>no entraron en ninguna liquidación</b> del contrato — así verificás por qué no están liquidadas, si falta una liquidación o si la CP está mal cargada.
        </div>
        <div class="filterbar" id="tq-liq-filters" style="margin-bottom:12px">
          <div><label>CAMPAÑA</label><select id="tql-camp"><option value="">Todas</option></select></div>
          <div><label>ORGANIZACIÓN</label><select id="tql-org"><option value="">Todas</option></select></div>
          <div><label>CULTIVO</label><select id="tql-prod"><option value="">Todos</option></select></div>
          <div><label>BUSCAR CTG / CARTA DE PORTE</label><input type="text" id="tql-q" placeholder="nº CTG o cp…" style="min-width:180px"></div>
          <button class="clear" id="tql-reset">Limpiar</button>
        </div>
        <div class="kpis" id="tq-liq-kpis" style="margin-bottom:12px"></div>
        <div id="tq-liq"></div>
      </div>

      <div class="section">
        <h3>🔗 Taqueo por rango de fechas <span class="badge">elegí el período y cruza Finnegans ↔ extranet (como tu romaneo)</span></h3>
        <div class="filterbar" style="margin-bottom:10px">
          <div><label>DESDE</label><input type="date" id="tq-desde" value="2026-01-01"></div>
          <div><label>HASTA</label><input type="date" id="tq-hasta"></div>
          <button class="clear" id="tq-cruzar">🔄 Cruzar</button>
          <span style="margin-left:auto;color:var(--muted);font-size:12px" id="tq-cruce-info"></span>
        </div>
        <div id="tq-alertas"></div>
      </div>

      <div style="margin-top:14px;padding:12px;background:#fff;border-radius:10px;border:1px solid var(--line);font-size:12.5px;color:var(--muted);line-height:1.55">
        💡 <b>Fuente</b>: Finnegans (traslados + contratos de venta) cruzado con los extranets. <b>Cargill</b> usa la descarga completa (movements) → cruce en ambas direcciones. Las demás usan calidad/análisis (parcial) → por ahora solo "falta en Finnegans". <b>Elegí el rango de fechas</b> arriba y apretá Cruzar: se filtran los dos lados a ese período, se sacan duplicados y se muestran los que faltan ingresar en cada sistema. Se actualiza en cada deploy.
      </div>

    </div><!-- /subpanel pn-taqueo -->

  </div>

  <!-- ============ CONTRATOS · Códigos de Contratos ============ -->
  <div class="panel" data-panel="contratos">
    <div class="subtabs">
      <button class="subtab active" data-sub="ct-compra">🌾 Compra <span class="count" id="cnt-ct-compra">0</span></button>
      <button class="subtab" data-sub="ct-venta">📦 Venta <span class="count" id="cnt-ct-venta">0</span></button>
    </div>

    <div class="kpis">
      <div class="kpi"><div class="lbl">Total Contratos</div><div class="val" id="ct-total">0</div></div>
      <div class="kpi green"><div class="lbl">Compra</div><div class="val" id="ct-tot-compra">0</div></div>
      <div class="kpi"><div class="lbl">Venta</div><div class="val" id="ct-tot-venta">0</div></div>
      <div class="kpi orange"><div class="lbl">Último guardado</div><div class="val" id="ct-last-save" style="font-size:14px">—</div></div>
    </div>

    <div class="filterbar" id="ct-filterbar">
      <div><label>BUSCAR</label><input type="text" id="ct-q" placeholder="número o beneficiario…" style="min-width:280px" /></div>
      <button class="clear" id="ct-clear">Limpiar</button>
      <button class="clear" id="ct-add" style="background:#16a34a;color:#fff;border-color:#16a34a">+ Agregar contrato</button>
      <button class="clear" id="ct-save" style="background:#1e3a8a;color:#fff;border-color:#1e3a8a;font-weight:600">💾 Guardar ahora</button>
      <div class="count" id="ct-count">0 / 0</div>
    </div>

    <div class="subpanel active" data-sub-panel="ct-compra">
      <div class="section">
        <h3>Códigos de Contratos · Compra <span class="badge">click en celdas para editar · se guarda y sincroniza en vivo (vos + tu compañera)</span></h3>
        <div class="tbl-wrap" style="max-height:680px">
          <table id="ct-tbl-compra">
            <thead><tr>
              <th style="width:32px">#</th>
              <th style="width:180px">Nº Contrato</th>
              <th>Beneficiario</th>
              <th style="width:80px">Acción</th>
            </tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="subpanel" data-sub-panel="ct-venta">
      <div class="section">
        <h3>Códigos de Contratos · Venta <span class="badge">click en celdas para editar · se guarda y sincroniza en vivo (vos + tu compañera)</span></h3>
        <div class="tbl-wrap" style="max-height:680px">
          <table id="ct-tbl-venta">
            <thead><tr>
              <th style="width:32px">#</th>
              <th style="width:180px">Nº Contrato</th>
              <th>Beneficiario</th>
              <th style="width:80px">Acción</th>
            </tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <div style="margin-top:14px;padding:12px;background:#fff;border-radius:10px;border:1px solid var(--line);font-size:12.5px;color:var(--muted);line-height:1.55">
      💡 <strong>Cómo se usa</strong>: click en cualquier celda para editarla. <b>+ Agregar contrato</b> abajo para sumar una fila. Los cambios se guardan automáticamente en el servidor — otros usuarios los ven sin esperar nada.
    </div>
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
      <button class="clear" id="mb-refresh" style="background:#1e3a8a;color:#fff;border-color:#1e3a8a;font-weight:600" title="Pulla cards nuevas del repo (los mails que Claude haya agregado desde tu última carga). Tus notas locales no se tocan.">🔄 Actualizar bandeja</button>
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

/* ============== API DE PERSISTENCIA COMPARTIDA ==============
   Llama al Worker /api/data/<key>. Cuando el Worker tiene Cloudflare KV bindeada,
   guarda en KV (compartido entre todos los usuarios). Si la KV no esta lista,
   apiSave/apiLoad devuelven null y el caller cae a localStorage como antes.

   Solo funciona cuando estamos detras del Worker (no en GitHub Pages directo). */
const API_AVAILABLE = !location.hostname.endsWith(".github.io");  // si estamos en el Worker
async function apiLoad(key){
  if(!API_AVAILABLE) return null;
  try{
    const r = await fetch(`/api/data/${key}?t=${Date.now()}`, {cache:"no-store", credentials:"include"});
    if(!r.ok) return null;
    const txt = await r.text();
    try { return JSON.parse(txt); } catch(e) { return null; }
  } catch(e){ return null; }
}
async function apiSave(key, value){
  if(!API_AVAILABLE) return false;
  try{
    const r = await fetch(`/api/data/${key}`, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      credentials:"include",
      body: JSON.stringify(value),
    });
    return r.ok;
  } catch(e){ return false; }
}
// Debounce para auto-save: agrupa cambios rapidos en una sola escritura
const _apiSaveTimers = {};
function apiSaveDebounced(key, getValue, statusCb){
  if(_apiSaveTimers[key]) clearTimeout(_apiSaveTimers[key]);
  if(statusCb) statusCb("pending");
  _apiSaveTimers[key] = setTimeout(async () => {
    _apiSaveTimers[key] = null;
    if(statusCb) statusCb("saving");
    const ok = await apiSave(key, getValue());
    if(statusCb) statusCb(ok ? "saved" : "error");
  }, 1500);
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
    document.querySelector('.campana-home').classList.remove('active');
    const t = document.getElementById('topbar-title');
    if(t) t.textContent = item.dataset.title || item.textContent.trim();
    // cerrar el sidebar en mobile
    document.getElementById('sidebar').classList.remove('open');
  });
});

/* ============== HOME (Resumen Comercial) ============== */
function goHome(){
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  document.querySelector('.tab[data-tab="home"]').classList.add('active');
  document.querySelector('.panel[data-panel="home"]').classList.add('active');
  NAV_ITEMS.forEach(x => x.classList.remove('active'));
  document.querySelector('.campana-home').classList.add('active');
  const t = document.getElementById('topbar-title');
  if(t) t.textContent = 'Resumen Comercial';
  document.getElementById('sidebar').classList.remove('open');
  homeRenderKpis();
}
document.querySelector('.campana-home').addEventListener('click', (ev) => { ev.preventDefault(); goHome(); });
// Tarjetas de atajos en la portada
document.querySelectorAll('.home-card').forEach(c => {
  c.addEventListener('click', (ev) => {
    ev.preventDefault();
    const tab = c.dataset.goTab, sub = c.dataset.goSub;
    const tabEl = document.querySelector(`.tab[data-tab="${tab}"]`);
    if(tabEl) tabEl.click();
    if(sub){
      const stEl = document.querySelector(`.panel[data-panel="${tab}"] .subtab[data-sub="${sub}"]`);
      if(stEl) stEl.click();
    }
    // Activar el nav-item correspondiente
    const navEl = document.querySelector(`.nav-item[data-go-tab="${tab}"][data-go-sub="${sub}"]`);
    if(navEl) {
      NAV_ITEMS.forEach(x => x.classList.remove('active'));
      navEl.classList.add('active');
      document.querySelector('.campana-home').classList.remove('active');
      const t2 = document.getElementById('topbar-title');
      if(t2) t2.textContent = navEl.dataset.title || navEl.textContent.trim();
    }
  });
});

// Render KPIs de la portada — usa typeof DATA para no romper si DATA/DATA_CP
// todavía no se definieron (se ejecuta antes en el script y necesita resiliencia).
function homeRenderKpis(){
  const el = document.getElementById('home-kpis');
  if(!el) return;
  const dataCp = (typeof DATA_CP !== 'undefined') ? DATA_CP : [];
  const data   = (typeof DATA    !== 'undefined') ? DATA    : [];
  const compraN = (PAYLOAD.counts && PAYLOAD.counts.compra) || dataCp.length || 0;
  const ventaN  = (PAYLOAD.counts && PAYLOAD.counts.venta)  || data.length || 0;
  const tnVta = data.reduce((s,r)=>s+(Number(r.cantidadmax)||0),0);
  const tnCpa = dataCp.reduce((s,r)=>s+(Number(r.cantidadmax)||0),0);
  const cerealeras = new Set();
  data.forEach(r => { if(r.organizacion) cerealeras.add(r.organizacion); });
  const kpis = [
    {lbl:'Contratos Compra', val:compraN.toLocaleString('es-AR'), sub: (tnCpa/1000).toFixed(1) + ' k tn'},
    {lbl:'Contratos Venta',  val:ventaN.toLocaleString('es-AR'),  sub: (tnVta/1000).toFixed(1) + ' k tn'},
    {lbl:'Cerealeras Activas', val:cerealeras.size.toLocaleString('es-AR'), sub:'Cargill · LDC · ACA · Allaria · FYO · COFCO · Bunge · Intagro'.slice(0,40)+'…'},
    {lbl:'Actualizado', val: '__BUILD_TIME__'.split(' ')[0] || '—', sub: '__BUILD_TIME__'.split(' ').slice(1).join(' ') || ''},
  ];
  el.innerHTML = kpis.map(k => `
    <div class="home-kpi">
      <div class="home-kpi-label">${k.lbl}</div>
      <div class="home-kpi-value">${k.val}</div>
      <div class="home-kpi-sub">${k.sub}</div>
    </div>`).join('');
}
// Render diferido — esperamos a que DATA/DATA_CP estén definidos
setTimeout(homeRenderKpis, 0);

/* ============== ADMINISTRACIÓN (abre config de editor/PAT) ============== */
// Solo visible para el "usuario madre" (ehussen). Otros usuarios internos no lo ven.
(function showAdminOnlyForOwner(){
  try{
    const m = (document.cookie||"").match(/(?:^|; )agronasaja_user=([^;]*)/);
    const user = m ? decodeURIComponent(m[1]).toLowerCase() : "";
    const ADMIN_USERS = new Set(["ehussen@agronasaja.com.ar"]);
    if(ADMIN_USERS.has(user)){
      const btn = document.getElementById('btn-admin');
      if(btn) btn.style.display = "";
    }
  } catch(e){}
})();
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

/* ============== Tablas con columnas redimensionables ============== */
// Aplica handles de resize a los <th> de una tabla, persistiendo widths en localStorage.
// Llamada repetidamente es safe (no agrega handles duplicados ni rompe estilos).
function makeColumnsResizable(tableEl, persistKey){
  if(!tableEl) return;
  tableEl.classList.add('resizable-cols');
  const ths = tableEl.querySelectorAll('thead th');
  if(!ths.length) return;

  // Restaurar widths guardados
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('tbl-cols-' + persistKey) || '{}') || {}; } catch(e){}
  ths.forEach((th, idx) => {
    if(saved[idx]){ th.style.width = saved[idx] + 'px'; }
  });

  // Agregar handles (idempotente)
  ths.forEach((th, idx) => {
    if(th.querySelector('.col-resize')) return;
    const handle = document.createElement('span');
    handle.className = 'col-resize';
    handle.title = 'Arrastrá para cambiar el ancho';
    th.appendChild(handle);
    // Evitar que el click en handle dispare el sort del th
    handle.addEventListener('click', e => e.stopPropagation());
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault(); e.stopPropagation();
      handle.classList.add('dragging');
      const startX = e.pageX;
      const startW = th.offsetWidth;
      // Throttle por frame: el reflow de la tabla (table-layout:fixed) es caro,
      // así que aplicamos el nuevo ancho como mucho 1 vez por frame en vez de
      // en cada mousemove (que dispara decenas de veces por segundo y cuelga).
      let rafId = null, lastX = startX;
      const onMove = (ev) => {
        lastX = ev.pageX;
        if(rafId) return;
        rafId = requestAnimationFrame(() => {
          rafId = null;
          th.style.width = Math.max(50, startW + (lastX - startX)) + 'px';
        });
      };
      const onUp = () => {
        if(rafId){ cancelAnimationFrame(rafId); rafId = null; }
        th.style.width = Math.max(50, startW + (lastX - startX)) + 'px';
        handle.classList.remove('dragging');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        // persistir todos los widths actuales
        const out = {};
        ths.forEach((t, i) => { if(t.style.width) out[i] = parseInt(t.style.width); });
        try { localStorage.setItem('tbl-cols-' + persistKey, JSON.stringify(out)); } catch(e){}
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  });
}

// Aplicar al cargar — y re-aplicar después de que cada tabla se haya renderizado
function applyResizableTables(){
  makeColumnsResizable(document.getElementById('tbl-cp'),     'compra-pos-v3');  // Compra · Posición Física (v3: anchos por defecto más amplios)
  makeColumnsResizable(document.getElementById('tbl'),         'venta-pos');     // Venta · Posición Física
  makeColumnsResizable(document.getElementById('tbl-cpfin'),  'compra-fin');     // Compra · Financiera
  makeColumnsResizable(document.getElementById('tbl-fin'),     'venta-fin');     // Venta · Financiera
  makeColumnsResizable(document.getElementById('tbl-canjes'), 'compra-canjes'); // Compra · Canjes
}
// Esperar a que el DOM y los renders iniciales hayan poblado los thead.
// requestIdleCallback corre cuando el browser está idle (no bloquea load inicial).
(window.requestIdleCallback || ((fn) => setTimeout(fn, 500)))(applyResizableTables);

/* ============== Secciones colapsables <details data-collapse="..."> ============== */
(function(){
  const KEY = 'tablero-granos-section-collapsed-v1';
  let closed;
  try { closed = new Set(JSON.parse(localStorage.getItem(KEY) || '[]')); }
  catch(e){ closed = new Set(); }
  document.querySelectorAll('details.section-collapsible[data-collapse]').forEach(d => {
    const name = d.dataset.collapse;
    if(closed.has(name)) d.removeAttribute('open');
    d.addEventListener('toggle', () => {
      if(d.open) closed.delete(name);
      else closed.add(name);
      try { localStorage.setItem(KEY, JSON.stringify([...closed])); } catch(e){}
    });
  });
})();

/* ============== Sidebar: secciones colapsables ============== */
(function(){
  const KEY = 'tablero-granos-nav-collapsed-v1';
  let collapsed;
  try { collapsed = new Set(JSON.parse(localStorage.getItem(KEY) || '[]')); }
  catch(e){ collapsed = new Set(); }
  const sections = document.querySelectorAll('.nav-section');

  function applyState(){
    sections.forEach(sec => {
      const name = sec.dataset.section;
      // si la sección tiene el item activo, forzamos expandido (para que se vea)
      const hasActive = sec.querySelector('.nav-item.active');
      if(hasActive){ sec.classList.remove('collapsed'); collapsed.delete(name); }
      else if(collapsed.has(name)) sec.classList.add('collapsed');
      else sec.classList.remove('collapsed');
      const btn = sec.querySelector('.nav-group');
      if(btn) btn.setAttribute('aria-expanded', sec.classList.contains('collapsed') ? 'false' : 'true');
    });
  }

  sections.forEach(sec => {
    const btn = sec.querySelector('.nav-group');
    if(!btn) return;
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      const name = sec.dataset.section;
      sec.classList.toggle('collapsed');
      if(sec.classList.contains('collapsed')) collapsed.add(name);
      else collapsed.delete(name);
      try { localStorage.setItem(KEY, JSON.stringify([...collapsed])); } catch(e){}
      btn.setAttribute('aria-expanded', sec.classList.contains('collapsed') ? 'false' : 'true');
    });
  });

  applyState();
  // Cuando se cambia de sub-pestaña, re-aplicamos para abrir la sección activa
  document.addEventListener('click', (e) => {
    if(e.target.closest('.nav-item')) setTimeout(applyState, 50);
  });
})();

/* ============== TAB COUNTS ============== */
document.getElementById('cnt-compra').textContent  = (PAYLOAD.counts.compra||0).toLocaleString('es-AR');
document.getElementById('cnt-venta').textContent   = (PAYLOAD.counts.venta||0).toLocaleString('es-AR');
document.getElementById('cnt-pos').textContent     = (PAYLOAD.counts.posicion||0).toLocaleString('es-AR');

/* ============== PILOTO: Resumen Contratos Venta Granos ============== */
// maizSplit: divide "Grano Maíz" en "Grano Maíz 1ra" o "Grano Maíz 2da"
// según fecha de entrega vs 01/07/<año_cosecha>.
// - 1ra: entrega ANTES del 01/07 del año-cosecha (campaña)
// - 2da: entrega DESDE el 01/07 inclusive
// Respeta campaña: 25/26 → corte 01/07/2026, 26/27 → 01/07/2027, etc.
// Aplica a "Grano Maíz" puro (no pisingallo, semillas, etc).
function maizSplit(c){
  if(!c || !c.producto) return c;
  const p = c.producto;
  // Early-exit ultra rápido: si no contiene "aíz" o "aiz", no es Maíz puro
  if(p.length > 14 || (p.indexOf('aíz') < 0 && p.indexOf('aiz') < 0)) return c;
  if(!/^grano\s+ma[ií]z\s*$/i.test(p.trim())) return c;
  let anioCos = null;
  const camp = String(c.campana || "").trim();
  const m = camp.match(/(\d{2,4})\s*[\/\-]?\s*(\d{2,4})?/);
  if(m){
    const a2 = m[2] || m[1];
    const num = parseInt(a2, 10);
    if(!isNaN(num)) anioCos = num < 100 ? 2000 + num : num;
  }
  if(!anioCos){
    const f = c.fecha || c.fechaminentrega || c.fechamaxentrega;
    if(f){
      const ym = String(f).match(/(\d{4})/);
      if(ym) anioCos = parseInt(ym[1], 10) + 1;
    }
  }
  if(!anioCos) return c;
  const corte = new Date(anioCos, 6, 1);  // 01/07/anioCos
  const entregaStr = c.fechaminentrega || c.fechamaxentrega;
  if(!entregaStr) return c;
  let entrega = null;
  const isoM = String(entregaStr).match(/(\d{4})-(\d{2})-(\d{2})/);
  if(isoM){
    entrega = new Date(parseInt(isoM[1]), parseInt(isoM[2])-1, parseInt(isoM[3]));
  } else {
    const dmy = String(entregaStr).match(/(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})/);
    if(dmy) entrega = new Date(parseInt(dmy[3]), parseInt(dmy[2])-1, parseInt(dmy[1]));
  }
  if(!entrega || isNaN(entrega.getTime())) return c;
  const variante = entrega < corte ? "1ra" : "2da";
  return Object.assign({}, c, { producto: `Grano Maíz ${variante}` });
}

const DATA = (PAYLOAD.pilot || []);   // sin split de maíz: "Grano Maíz" queda combinado (1ra+2da)
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
  {k:'cantidadliquidada',           lbl:'Tn Liquidadas',   num:true, sum:true},
  {k:'_pdteLiquidar',               lbl:'Tn Pdte Liquidar',num:true, sum:true},
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

// build inicial de los selects — diferimos: panel default es 'home'.
(window.requestIdleCallback || ((fn) => setTimeout(fn, 100)))(rebuildSelects);

/* ----- KPIs + resumen grano + charts + tabla ----- */
let chartTop=null, chartDonut=null;

function render(){
  // KPIs (posición física, "ajustada" = cantidadmax)
  let cnt=filtered.length, tnAj=0, tnEnt=0, tnLiq=0;
  filtered.forEach(r => {
    tnAj  += r.cantidadmax || 0;
    tnEnt += r.cantidadentregada || 0;
    tnLiq += r.cantidadliquidada || 0;
  });
  const tnPdt = tnAj - tnEnt;
  const tnPdtLiq = tnEnt - tnLiq;
  const cumplimiento = tnAj>0 ? tnEnt/tnAj : null;

  document.getElementById('kpi-row').innerHTML = `
    <div class="kpi"><div class="lbl">Contratos</div><div class="val">${fmt.int(cnt)}</div><div class="hint">de ${fmt.int(DATA.length)} totales</div></div>
    <div class="kpi"><div class="lbl">Toneladas Ajustadas</div><div class="val">${fmt.num(tnAj)}</div><div class="hint">Cantidad final post-ajustes</div></div>
    <div class="kpi green"><div class="lbl">Toneladas Entregadas</div><div class="val">${fmt.num(tnEnt)}</div><div class="hint">Cumplimiento: ${fmt.pct(cumplimiento)}</div></div>
    <div class="kpi orange"><div class="lbl">Tn Pendientes de Entrega</div><div class="val">${fmt.num(tnPdt)}</div><div class="hint">= Ajustadas − Entregadas</div></div>
    <div class="kpi red"><div class="lbl">Tn Pendientes de Liquidar</div><div class="val">${fmt.num(tnPdtLiq)}</div><div class="hint">= Entregadas − Liquidadas (de lo entregado)</div></div>
  `;

  // resumen por grano (sin importes)
  const byGrain = {};
  filtered.forEach(r => {
    const p = r.producto || '—';
    if(!byGrain[p]) byGrain[p] = {cnt:0,tnAj:0,tnEnt:0,tnLiq:0};
    byGrain[p].cnt++;
    byGrain[p].tnAj  += r.cantidadmax || 0;
    byGrain[p].tnEnt += r.cantidadentregada || 0;
    byGrain[p].tnLiq += r.cantidadliquidada || 0;
  });
  const grainOrder = Object.entries(byGrain).sort((a,b)=>b[1].tnAj - a[1].tnAj);
  document.getElementById('grain-meta').textContent = `${grainOrder.length} granos`;
  document.getElementById('grain-grid').innerHTML = grainOrder.map(([g,v]) => {
    const pct = v.tnAj>0 ? v.tnEnt/v.tnAj : 0;
    const pdt = v.tnAj - v.tnEnt;
    const pdtLiq = v.tnEnt - v.tnLiq;
    const pctLiq = v.tnEnt>0 ? v.tnLiq/v.tnEnt : 0;
    return `<div class="grain-card ${grainClass(g)}">
      <div class="name"><span>${g}</span><span class="cnt">${v.cnt} contratos</span></div>
      <div class="row"><span class="k">Tn Ajustadas</span><span><b>${fmt.num(v.tnAj)}</b></span></div>
      <div class="row"><span class="k">Tn Entregadas</span><span>${fmt.num(v.tnEnt)} <span style="color:var(--muted)">(${fmt.pct(pct)})</span></span></div>
      <div class="row"><span class="k">Tn Pdte Entrega</span><span style="color:var(--orange)"><b>${fmt.num(pdt)}</b></span></div>
      <div class="row"><span class="k">Tn Pdte Liquidar</span><span style="color:var(--red)"><b>${fmt.num(pdtLiq)}</b> <span style="color:var(--muted);font-weight:400">(${fmt.pct(pctLiq)} liq.)</span></span></div>
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
    if(k==='_pdteLiquidar') return (r.cantidadentregada||0) - (r.cantidadliquidada||0);
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

  const MAX = 400;   // proteccion DOM
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
      const total = subset.reduce((acc,r) => {
        let v;
        if(c.k==='_pdteEntrega') v = (r.cantidadmax||0)-(r.cantidadentregada||0);
        else if(c.k==='_pdteLiquidar') v = (r.cantidadentregada||0)-(r.cantidadliquidada||0);
        else v = Number(r[c.k]);
        return acc + (Number(v) || 0);
      }, 0);
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
   ===========  VENTA · Precios por Contrato  =================
   ============================================================
   Sub-pestaña con cada contrato y su precio cerrado, agrupable por
   cerealera (Cargill, LDC, COFCO, FYO, etc) para comparar precios. */

const VP_COLS = [
  {k:'numerointerno',          lbl:'Nº',           num:false},
  {k:'fecha',                  lbl:'Fecha',        num:false},
  {k:'organizacion',           lbl:'Cerealera',    num:false},
  {k:'producto',               lbl:'Grano',        num:false},
  {k:'tipocontrato',           lbl:'Tipo',         num:false},
  {k:'cantidadfijada',         lbl:'Tn Fijadas',   num:true,  sum:true},
  {k:'preciopromediofijado',   lbl:'Precio Fij.',  num:true,  sum:'avg'},
  {k:'moneda',                 lbl:'Mon.',         num:false},
  {k:'importefijado',          lbl:'Importe',      num:true,  sum:true},
  {k:'campana',                lbl:'Campaña',      num:false},
];

let VP_SORT_K = "fecha", VP_SORT_D = -1;

function vpInitFiltros(){
  // Solo contratos con precio fijado o cantidad fijada > 0
  const base = DATA.filter(r => (Number(r.cantidadfijada)>0) || (Number(r.preciopromediofijado)>0));
  const map = {emp:"empresa", org:"organizacion", corr:"corredor", prod:"producto", mon:"moneda", camp:"campana"};
  Object.keys(map).forEach(k => {
    const vals = [...new Set(base.map(r => r[map[k]]).filter(v => v!=null && v!==""))]
      .sort((a,b)=>String(a).localeCompare(String(b),"es"));
    const sel = document.getElementById("vp-"+k);
    if(sel){
      sel.innerHTML = '<option value="">Todas</option>' +
        vals.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");
    }
  });
}

function vpFiltered(){
  const emp  = document.getElementById("vp-emp").value;
  const o    = document.getElementById("vp-org").value;
  const corr = document.getElementById("vp-corr").value;
  const p    = document.getElementById("vp-prod").value;
  const m    = document.getElementById("vp-mon").value;
  const c    = document.getElementById("vp-camp").value;
  const q    = (document.getElementById("vp-q").value||"").toLowerCase().trim();
  return DATA.filter(r => {
    if(!((Number(r.cantidadfijada)>0) || (Number(r.preciopromediofijado)>0))) return false;
    if(emp  && r.empresa !== emp) return false;
    if(o    && r.organizacion !== o) return false;
    if(corr && r.corredor !== corr) return false;
    if(p    && r.producto !== p) return false;
    if(m    && r.moneda !== m) return false;
    if(c    && r.campana !== c) return false;
    if(q && !`${r.numerointerno||""} ${r.descripcion||""}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function vpEscape(s){ return String(s||"").replace(/[&<>"']/g, ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch])); }

// IMPORTANTE: el campo "moneda" en Finnegans esta mal etiquetado en muchos contratos.
// Detectar la moneda REAL por la magnitud del precio (USD: 50-2000 / ARS: > 5000).
function vpRealMoneda(precio, monedaNominal){
  const p = Number(precio) || 0;
  if(p <= 0) return monedaNominal || "—";
  if(p < 5000) return "DOLARES";    // precios USD/Tn: 50-2000 para granos
  return "PESOS";                    // precios ARS/Tn: > 5000 (típico 50k-500k)
}

function vpRender(){
  const rows = vpFiltered();

  // KPIs — agrupar por moneda DETECTADA (no la del campo moneda)
  let totTn=0, totImp=0, totW=0;
  const byMon = {};
  rows.forEach(r => {
    const tn = Number(r.cantidadfijada)||0;
    const pr = Number(r.preciopromediofijado)||0;
    const im = Number(r.importefijado)||0;
    totTn += tn; totImp += im; totW += tn*pr;
    const m = vpRealMoneda(pr, r.moneda);
    if(!byMon[m]) byMon[m] = {tn:0, w:0, imp:0};
    byMon[m].tn += tn; byMon[m].w += tn*pr; byMon[m].imp += im;
  });
  const monedasStr = Object.entries(byMon).filter(x=>x[1].tn>0).map(([m,v]) => {
    const a = v.tn>0 ? v.w/v.tn : 0;
    return `${fmt.num2(a)} ${m}`;
  }).join(" / ") || "—";
  document.getElementById("vp-kpis").innerHTML = `
    <div class="kpi"><div class="lbl">Contratos</div><div class="val">${fmt.int(rows.length)}</div><div class="hint">con precio fijado</div></div>
    <div class="kpi"><div class="lbl">Tn Fijadas</div><div class="val">${fmt.num(totTn)}</div></div>
    <div class="kpi green"><div class="lbl">Precio Prom. Ponderado</div><div class="val" style="font-size:22px">${monedasStr}</div><div class="hint">ponderado por Tn · moneda detectada por magnitud (Finnegans tiene labels mezclados)</div></div>
    <div class="kpi"><div class="lbl">Importe Fijado</div><div class="val">${fmt.num(totImp)}</div></div>
  `;
  document.getElementById("vp-count").textContent = `${rows.length} / ${DATA.length}`;

  // Cards por CULTIVO (grano) — precio promedio ponderado por grano POR MONEDA DETECTADA
  const byGrano = {};
  rows.forEach(r => {
    const g = r.producto || "—";
    if(!byGrano[g]) byGrano[g] = {cnt:0, tn:0, imp:0, byMon:{}, cerealeras:new Set()};
    const tn = Number(r.cantidadfijada)||0;
    const pr = Number(r.preciopromediofijado)||0;
    const mo = vpRealMoneda(pr, r.moneda);
    byGrano[g].cnt++;
    byGrano[g].tn += tn;
    byGrano[g].imp += Number(r.importefijado)||0;
    if(!byGrano[g].byMon[mo]) byGrano[g].byMon[mo] = {tn:0, w:0, imp:0};
    byGrano[g].byMon[mo].tn += tn;
    byGrano[g].byMon[mo].w  += tn*pr;
    byGrano[g].byMon[mo].imp += Number(r.importefijado)||0;
    if(r.organizacion) byGrano[g].cerealeras.add(r.organizacion);
  });
  const granoOrder = Object.entries(byGrano).sort((a,b) => b[1].tn - a[1].tn);
  document.getElementById("vp-meta-grano").textContent = `${granoOrder.length} cultivos · ${rows.length} contratos filtrados`;
  document.getElementById("vp-cards-grano").innerHTML = granoOrder.map(([g, v]) => {
    // listar precio promedio por cada moneda presente
    const monRows = Object.entries(v.byMon).filter(([m,vm])=>vm.tn>0).sort((a,b)=>b[1].tn - a[1].tn).map(([m, vm]) => {
      const avg = vm.tn>0 ? vm.w/vm.tn : 0;
      return `<div class="row"><span class="k">Prom. ${escapeHtml(m)}</span><span style="color:var(--blue)"><b>${fmt.num2(avg)}</b></span></div>`;
    }).join("");
    return `<div class="grain-card ${grainClass(g)}">
      <div class="name"><span>${vpEscape(g)}</span><span class="cnt">${v.cnt} ctos</span></div>
      <div class="row"><span class="k">Tn Fijadas</span><span><b>${fmt.num(v.tn)}</b></span></div>
      ${monRows}
      <div class="row"><span class="k" style="font-size:10.5px">Cerealeras</span><span style="font-size:10.5px;color:var(--muted)">${v.cerealeras.size} distintas</span></div>
    </div>`;
  }).join("") || '<div class="placeholder">Sin contratos con precio fijado para los filtros aplicados</div>';

  // Cards por Cerealera — agrupar precio promedio por MONEDA DETECTADA
  const byOrg = {};
  rows.forEach(r => {
    const o = r.organizacion || "—";
    if(!byOrg[o]) byOrg[o] = {cnt:0, tn:0, imp:0, byMon:{}, productos:new Set()};
    const tn = Number(r.cantidadfijada)||0;
    const pr = Number(r.preciopromediofijado)||0;
    const mo = vpRealMoneda(pr, r.moneda);
    byOrg[o].cnt++;
    byOrg[o].tn += tn;
    byOrg[o].imp += Number(r.importefijado)||0;
    if(!byOrg[o].byMon[mo]) byOrg[o].byMon[mo] = {tn:0, w:0};
    byOrg[o].byMon[mo].tn += tn;
    byOrg[o].byMon[mo].w  += tn*pr;
    if(r.producto) byOrg[o].productos.add(r.producto);
  });
  const orgOrder = Object.entries(byOrg).sort((a,b)=>b[1].tn - a[1].tn);
  document.getElementById("vp-meta").textContent = `${orgOrder.length} cerealeras · precio promedio ponderado por Tn · moneda detectada por magnitud`;
  document.getElementById("vp-cards").innerHTML = orgOrder.map(([org, v]) => {
    const monRows = Object.entries(v.byMon).filter(([m,vm])=>vm.tn>0).sort((a,b)=>b[1].tn - a[1].tn).map(([m, vm]) => {
      const avg = vm.tn>0 ? vm.w/vm.tn : 0;
      return `<div class="row"><span class="k">Prom. ${escapeHtml(m)}</span><span style="color:var(--blue)"><b>${fmt.num2(avg)}</b></span></div>`;
    }).join("");
    const prods = [...v.productos].slice(0,3).join(", ") + (v.productos.size>3 ? "…" : "");
    return `<div class="grain-card">
      <div class="name"><span title="${vpEscape(org)}">${vpEscape(org.length>34?org.slice(0,34)+"…":org)}</span><span class="cnt">${v.cnt} ctos</span></div>
      <div class="row"><span class="k">Tn Fijadas</span><span><b>${fmt.num(v.tn)}</b></span></div>
      ${monRows}
      <div class="row"><span class="k">Importe</span><span>${fmt.num(v.imp)}</span></div>
      <div class="row"><span class="k" style="font-size:10.5px">Granos</span><span style="font-size:10.5px;color:var(--muted)">${vpEscape(prods)}</span></div>
    </div>`;
  }).join("") || '<div class="placeholder">Sin contratos con precio fijado para los filtros aplicados</div>';

  // Tabla detalle
  const head = VP_COLS.map(c => {
    const arr = (VP_SORT_K === c.k) ? (VP_SORT_D>0?'▲':'▼') : '';
    return `<th class="${c.num?'num':''}" data-sort-vp="${c.k}" style="cursor:pointer">${c.lbl} ${arr}</th>`;
  }).join("");
  document.getElementById("vp-tbl-head").innerHTML = head;

  let sorted = rows.slice();
  if(VP_SORT_K){
    const col = VP_COLS.find(c => c.k === VP_SORT_K);
    sorted.sort((a,b) => {
      let va = a[VP_SORT_K], vb = b[VP_SORT_K];
      if(col && col.num){ va = Number(va)||0; vb = Number(vb)||0; return (va-vb)*VP_SORT_D; }
      va = String(va==null?'':va); vb = String(vb==null?'':vb);
      return va.localeCompare(vb,"es",{numeric:true})*VP_SORT_D;
    });
  }
  const body = sorted.slice(0, 400).map(r => {
    return `<tr>${VP_COLS.map(c => {
      let v = r[c.k];
      if(c.k === "moneda"){
        // Mostrar moneda DETECTADA por magnitud del precio (Finnegans tiene labels mezclados)
        const detected = vpRealMoneda(r.preciopromediofijado, r.moneda);
        const mismatch = detected !== r.moneda;
        return `<td title="${mismatch ? 'Original Finnegans: ' + (r.moneda||'') + ' (detectada por magnitud)' : ''}" style="${mismatch?'color:#b45309;font-weight:600':''}">${vpEscape(detected)}${mismatch?' ⚠':''}</td>`;
      }
      if(c.num){
        if(c.k === "preciopromediofijado") v = (v==null||v===0)?'—':fmt.num2(v);
        else v = (v==null)?'—':fmt.num(v);
        return `<td class="num">${v}</td>`;
      }
      return `<td>${vpEscape(v||'')}</td>`;
    }).join("")}</tr>`;
  }).join("");
  document.getElementById("vp-tbl-body").innerHTML = body || '<tr><td colspan="10" style="text-align:center;padding:18px;color:var(--muted)">Sin contratos con precio fijado</td></tr>';

  // Footer total
  const totFoot = VP_COLS.map(c => {
    if(c.k === "numerointerno") return `<td><b>TOTAL · ${rows.length} contratos</b></td>`;
    if(c.sum === true){
      const t = rows.reduce((s,r) => s + (Number(r[c.k])||0), 0);
      return `<td class="num"><b>${fmt.num(t)}</b></td>`;
    }
    if(c.sum === "avg"){
      // precio promedio ponderado por cantidadfijada
      let w=0, t=0;
      rows.forEach(r => { const tn=Number(r.cantidadfijada)||0; const pr=Number(r[c.k])||0; if(pr>0){w+=tn*pr; t+=tn;} });
      const a = t>0?w/t:0;
      return `<td class="num" title="promedio ponderado por Tn"><b>${fmt.num2(a)}</b></td>`;
    }
    return '<td></td>';
  }).join("");
  document.getElementById("vp-tbl-foot").innerHTML = `<tr>${totFoot}</tr>`;

  // Sort listeners
  document.querySelectorAll('#vp-tbl-head [data-sort-vp]').forEach(th => {
    th.addEventListener("click", () => {
      const k = th.getAttribute("data-sort-vp");
      if(VP_SORT_K === k) VP_SORT_D = -VP_SORT_D;
      else { VP_SORT_K = k; VP_SORT_D = -1; }
      vpRender();
    });
  });
}

// Wire-up filtros
(function vpInit(){
  vpInitFiltros();
  const FIDS = ["vp-emp","vp-org","vp-corr","vp-prod","vp-mon","vp-camp","vp-q"];
  FIDS.forEach(id => {
    const el = document.getElementById(id);
    if(el) el.addEventListener(id === "vp-q" ? "input" : "change", vpRender);
  });
  const clr = document.getElementById("vp-clear");
  if(clr) clr.addEventListener("click", () => {
    FIDS.forEach(id => document.getElementById(id).value = "");
    vpRender();
  });
  vpRender();
})();


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

  // Tabla detallada financiera (se omite si se sacó la sección del HTML)
  if(document.getElementById('tbl-head-fin')){
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
  const MAX = 400;
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
   ===  CALENDARIO DE COBRANZAS — PENDIENTE DE LIQUIDAR     ===
   ===  Filas: Producto x Cerealera (Venta, campaña sel.)   ===
   ===  Pend. Liq = cantidadentregadapendienteliquidar       ===
   ===  Precio / Liq pend de pasar / importes por día: manual ==
   ===  (persistido en localStorage, scopeado por campaña)   ===
   ============================================================ */
const PL_KEY = 'tablero-granos-cobranzas-v1';
let PL_DATA = { precio:{}, liqpend:{}, precioEst:{}, dias:{}, tc:null };
try { const s = JSON.parse(localStorage.getItem(PL_KEY)||'null'); if(s) PL_DATA = Object.assign({precio:{},liqpend:{},precioEst:{},dias:{},tc:null}, s); } catch(e){}
function plSave(){ try{ localStorage.setItem(PL_KEY, JSON.stringify(PL_DATA)); }catch(e){} }
function plNum(x){ const v = parseFloat(String(x==null?'':x).replace(/\./g,'').replace(',','.').replace(/[^0-9.\-]/g,'')); return isNaN(v)?0:v; }
function plCleanProd(p){ return (String(p||'—').replace(/^grano\s+/i,'').trim()) || '—'; }

let PL_ROWS = [], PL_DAYS = [], PL_MONTHS = [];
const PL_GRAIN_PX = {soja:325, maiz:180, trigo:205, girasol:null, sorgo:null, otros:null}; // precio estimado default USD/Tn
const PL_FIX = [
  {key:'prod',  lbl:'Producto',          w:96},
  {key:'org',   lbl:'Organización',      w:210},
  {key:'pend',  lbl:'Pend. Liq. (Tn)',   w:84},
  {key:'precio',lbl:'Precio',            w:64},
  {key:'liq',   lbl:'Liq. Pend. pasar',  w:88},
  {key:'real',  lbl:'Real Pend. Liq.',   w:92},
];
(function(){ let l=0; PL_FIX.forEach(c=>{ c.left=l; l+=c.w; }); })();
// columnas finales (igual al Excel), después de los días
const PL_END = [
  {key:'pxest', lbl:'Precio estimado',      w:96},
  {key:'cobrar',lbl:'Pend. a cobrar (ARS)', w:140},
  {key:'estim', lbl:'Estimado a cobrar',    w:130},
];
function plTC(){ return plNum(PL_DATA.tc) || 0; }
function plPxEst(row){
  const v = PL_DATA.precioEst[row.k];
  if(v!=null && v!=='') return plNum(v);
  const d = PL_GRAIN_PX[row.gk];
  return d!=null ? d : 0;
}

function plInitCamp(){
  const sel = document.getElementById('pl-camp');
  const camps = uniqSorted(DATA, 'campana').filter(Boolean)
    .sort((a,b)=>String(b).localeCompare(String(a),'es',{numeric:true}));
  sel.innerHTML = camps.map(c=>`<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  sel.value = camps.find(c => /25\s*-\s*26/.test(c)) || camps[0] || '';
}
function plBuildDays(){
  const start = document.getElementById('pl-start').value || '2026-06-16';
  const n = Math.max(1, Math.min(365, parseInt(document.getElementById('pl-days').value,10)||45));
  const WD = ['Dom','Lun','Mar','Mié','Jue','Vie','Sáb'];
  const MO = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre'];
  const p = start.split('-').map(Number);
  const dt = new Date(p[0], (p[1]||1)-1, p[2]||1);
  const out = [];
  for(let i=0;i<n;i++){
    const iso = dt.getFullYear()+'-'+String(dt.getMonth()+1).padStart(2,'0')+'-'+String(dt.getDate()).padStart(2,'0');
    out.push({iso, wd:WD[dt.getDay()], d:dt.getDate(), monthLbl:MO[dt.getMonth()]+' '+dt.getFullYear()});
    dt.setDate(dt.getDate()+1);
  }
  return out;
}
function plBuildRows(){
  const camp = document.getElementById('pl-camp').value;
  const order = {soja:0,maiz:1,trigo:2,girasol:3,sorgo:4,otros:9};
  const map = {};
  DATA.forEach(r => {
    if(camp && r.campana !== camp) return;
    const tn = Number(r.cantidadentregadapendienteliquidar)||0;
    if(tn <= 0.0001) return;
    const prod = plCleanProd(r.producto), org = (r.organizacion||'—').trim();
    const k = [camp, prod, org].join('§');   // key scopeada por campaña
    if(!map[k]) map[k] = {k, prod, org, pend:0, gk:(granoBCR(r.producto)||'otros')};
    map[k].pend += tn;
  });
  return Object.values(map).sort((a,b)=>
    (order[a.gk]-order[b.gk]) || a.prod.localeCompare(b.prod,'es') || (b.pend-a.pend));
}
function plBuildMonths(days){
  const months = []; let i=0;
  while(i<days.length){
    let j=i; while(j<days.length && days[j].monthLbl===days[i].monthLbl) j++;
    months.push({lbl:days[i].monthLbl, days:days.slice(i,j)}); i=j;
  }
  return months;
}
function plRender(){
  PL_ROWS = plBuildRows();
  PL_DAYS = plBuildDays();
  PL_MONTHS = plBuildMonths(PL_DAYS);
  const tc = plTC();

  // THEAD 2 niveles: fila1 = fijas(rowspan2) + meses(colspan) + finales(rowspan2); fila2 = días
  const fixTh = PL_FIX.map(c=>`<th class="pl-fz" rowspan="2" style="left:${c.left}px;min-width:${c.w}px;width:${c.w}px">${escapeHtml(c.lbl)}</th>`).join('');
  const monthTh = PL_MONTHS.map(m=>`<th class="pl-month" colspan="${m.days.length}">${escapeHtml(m.lbl)}</th>`).join('');
  const endTh = PL_END.map(c=>`<th rowspan="2" style="min-width:${c.w}px">${escapeHtml(c.lbl)}</th>`).join('');
  const dayTh = PL_DAYS.map(d=>`<th class="pl-dayh" style="min-width:64px" title="${d.iso}">${d.wd} ${d.d}</th>`).join('');
  document.getElementById('pl-head').innerHTML = `<tr>${fixTh}${monthTh}${endTh}</tr><tr>${dayTh}</tr>`;

  // TBODY
  const body = PL_ROWS.map((row,ri)=>{
    const md = PL_DATA.dias[row.k] || {};
    const precio = PL_DATA.precio[row.k];
    const liq = PL_DATA.liqpend[row.k];
    const real = row.pend - plNum(liq);
    const pxEst = plPxEst(row);
    const pxEstShown = (PL_DATA.precioEst[row.k]!=null && PL_DATA.precioEst[row.k]!=='') ? PL_DATA.precioEst[row.k]
                      : (PL_GRAIN_PX[row.gk]!=null ? PL_GRAIN_PX[row.gk] : '');
    const cobrar = (real>0 && pxEst>0 && tc>0) ? real*pxEst*tc : 0;
    const estim = (plNum(precio)>0) ? row.pend*plNum(precio) : 0;
    const dayTds = PL_DAYS.map(d=>{
      const val = md[d.iso];
      return `<td class="pl-day"><input class="pl-cell" data-k="${escapeHtml(row.k)}" data-d="${d.iso}" value="${val!=null?escapeHtml(val):''}"></td>`;
    }).join('');
    return `<tr>
      <td class="pl-fz" style="left:${PL_FIX[0].left}px">${escapeHtml(row.prod)}</td>
      <td class="pl-fz" style="left:${PL_FIX[1].left}px" title="${escapeHtml(row.org)}">${escapeHtml(row.org)}</td>
      <td class="pl-fz num" style="left:${PL_FIX[2].left}px">${fmt.num(row.pend)}</td>
      <td class="pl-fz" style="left:${PL_FIX[3].left}px"><input class="pl-cell pl-num" data-k="${escapeHtml(row.k)}" data-f="precio" value="${precio!=null?escapeHtml(precio):''}"></td>
      <td class="pl-fz" style="left:${PL_FIX[4].left}px"><input class="pl-cell pl-num" data-k="${escapeHtml(row.k)}" data-f="liq" value="${liq!=null?escapeHtml(liq):''}"></td>
      <td class="pl-fz num" id="pl-real-${ri}" style="left:${PL_FIX[5].left}px">${fmt.num(real)}</td>
      ${dayTds}
      <td class="pl-day"><input class="pl-cell pl-num" data-k="${escapeHtml(row.k)}" data-f="pxest" value="${pxEstShown!==''?escapeHtml(pxEstShown):''}"></td>
      <td class="num pl-cobrar" id="pl-cob-${ri}">${cobrar?fmt.num(cobrar):''}</td>
      <td class="num pl-estim" id="pl-est-${ri}">${estim?fmt.num(estim):''}</td>
    </tr>`;
  }).join('');
  document.getElementById('pl-body').innerHTML = body ||
    `<tr><td colspan="${PL_FIX.length+PL_DAYS.length+PL_END.length}" style="padding:24px;text-align:center;color:var(--muted)">Sin pendiente de liquidar para esta campaña</td></tr>`;
  plRenderFoot();
  const totPend = PL_ROWS.reduce((s,r)=>s+r.pend,0);
  document.getElementById('pl-meta').textContent =
    `${document.getElementById('pl-camp').value||'todas'} · ${PL_ROWS.length} filas · ${fmt.num(totPend)} tn pend. liq.`;
}
function plRenderFoot(){
  const tc = plTC();
  const dayCols = PL_DAYS.map(d=>{ let s=0; PL_ROWS.forEach(row=> s+=plNum((PL_DATA.dias[row.k]||{})[d.iso])); return s; });
  let totPend=0, totReal=0, totCob=0, totEst=0;
  PL_ROWS.forEach(row=>{
    totPend += row.pend;
    const real = row.pend - plNum(PL_DATA.liqpend[row.k]);
    totReal += real;
    const pxEst = plPxEst(row);
    if(real>0 && pxEst>0 && tc>0) totCob += real*pxEst*tc;
    const pr = plNum(PL_DATA.precio[row.k]);
    if(pr>0) totEst += row.pend*pr;
  });
  let acum=0;
  const dayTot  = dayCols.map(s=>`<td class="num">${s?fmt.num(s):''}</td>`).join('');
  const dayAcum = dayCols.map(s=>{ acum+=s; return `<td class="num">${acum?fmt.num(acum):''}</td>`; }).join('');
  const endTot   = `<td></td><td class="num">${totCob?fmt.num(totCob):''}</td><td class="num">${totEst?fmt.num(totEst):''}</td>`;
  const endBlank = `<td></td><td></td><td></td>`;
  document.getElementById('pl-foot').innerHTML = `
    <tr>
      <td class="pl-fz" style="left:${PL_FIX[0].left}px" colspan="2">A cobrar (día)</td>
      <td class="pl-fz num" style="left:${PL_FIX[2].left}px">${fmt.num(totPend)}</td>
      <td class="pl-fz" style="left:${PL_FIX[3].left}px"></td>
      <td class="pl-fz" style="left:${PL_FIX[4].left}px"></td>
      <td class="pl-fz num" style="left:${PL_FIX[5].left}px">${fmt.num(totReal)}</td>
      ${dayTot}${endTot}
    </tr>
    <tr>
      <td class="pl-fz" style="left:${PL_FIX[0].left}px" colspan="6">Acumulado</td>
      ${dayAcum}${endBlank}
    </tr>`;
}
// recalcula Real / Pend a cobrar / Estimado de una fila sin re-render total
function plUpdateRow(k){
  const row = PL_ROWS.find(r=>r.k===k); if(!row) return;
  const ri = PL_ROWS.indexOf(row);
  const real = row.pend - plNum(PL_DATA.liqpend[row.k]);
  const tc = plTC(), pxEst = plPxEst(row);
  const cobrar = (real>0 && pxEst>0 && tc>0) ? real*pxEst*tc : 0;
  const pr = plNum(PL_DATA.precio[row.k]);
  const estim = pr>0 ? row.pend*pr : 0;
  const rc=document.getElementById('pl-real-'+ri); if(rc) rc.textContent = fmt.num(real);
  const cc=document.getElementById('pl-cob-'+ri);  if(cc) cc.textContent = cobrar?fmt.num(cobrar):'';
  const ec=document.getElementById('pl-est-'+ri);  if(ec) ec.textContent = estim?fmt.num(estim):'';
}
// edición con delegación (un solo listener para todas las celdas)
document.getElementById('pl-body').addEventListener('change', (ev)=>{
  const inp = ev.target;
  if(!inp || !inp.classList || !inp.classList.contains('pl-cell')) return;
  const k = inp.dataset.k, val = inp.value.trim(), f = inp.dataset.f;
  if(f === 'precio'){
    if(val==='') delete PL_DATA.precio[k]; else PL_DATA.precio[k]=val;
    plUpdateRow(k); plRenderFoot();
  } else if(f === 'liq'){
    if(val==='') delete PL_DATA.liqpend[k]; else PL_DATA.liqpend[k]=val;
    plUpdateRow(k); plRenderFoot();
  } else if(f === 'pxest'){
    if(val==='') delete PL_DATA.precioEst[k]; else PL_DATA.precioEst[k]=val;
    plUpdateRow(k); plRenderFoot();
  } else {
    const d = inp.dataset.d;
    if(!PL_DATA.dias[k]) PL_DATA.dias[k]={};
    if(val==='') delete PL_DATA.dias[k][d]; else PL_DATA.dias[k][d]=val;
    plRenderFoot();
  }
  plSave();
});
plInitCamp();
if(PL_DATA.tc==null) PL_DATA.tc = (PAYLOAD.bcr && PAYLOAD.bcr.tc_usd_ars) || 1428.5;
document.getElementById('pl-tc').value = fmt.num2(PL_DATA.tc);
document.getElementById('pl-camp').addEventListener('change', plRender);
document.getElementById('pl-start').addEventListener('change', plRender);
document.getElementById('pl-days').addEventListener('change', plRender);
document.getElementById('pl-tc').addEventListener('blur', ()=>{
  const t = parseFloat((document.getElementById('pl-tc').value||'').replace(/\./g,'').replace(',','.').replace(/[^0-9.\-]/g,''));
  PL_DATA.tc = isNaN(t)?null:t; plSave(); plRender();
});
document.getElementById('pl-clear-manual').addEventListener('click', ()=>{
  if(!confirm('¿Borrar todas las cargas manuales (precio, liq. pend. de pasar, precio estimado e importes por día)?')) return;
  const tc = PL_DATA.tc;
  PL_DATA = { precio:{}, liqpend:{}, precioEst:{}, dias:{}, tc };
  plSave(); plRender();
});
plRender();


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

const DATA_CP = (PAYLOAD.compra || []);   // sin split de maíz

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
  {k:'fecha',                       lbl:'Fecha',           num:false, w:'100px'},
  {k:'numerointerno',               lbl:'Nº',              num:false, w:'70px'},
  {k:'organizacion',                lbl:'Proveedor',       num:false, w:'240px'},
  {k:'producto',                    lbl:'Producto',        num:false, w:'150px'},
  {k:'tipocontrato',                lbl:'Tipo',            num:false, w:'155px'},
  {k:'_cpFijado',                   lbl:'¿A Precio?',      num:false, html:true, w:'130px'},
  {k:'cantidadmax',                 lbl:'Tn Ajustadas',    num:true, sum:true, w:'95px'},
  {k:'cantidadentregada',           lbl:'Tn Recibidas',    num:true, sum:true, w:'95px'},
  {k:'_cpPdteRecibir',              lbl:'Tn Pdte Recibir', num:true, sum:true, w:'95px'},
  {k:'cantidadliquidada',           lbl:'Tn Liquidadas',   num:true, sum:true, w:'95px'},
  {k:'_cpPdteLiquidar',             lbl:'Tn Pdte Liquidar',num:true, sum:true, w:'95px'},
  {k:'cantidadcertificadaneta',     lbl:'Tn Certif.',      num:true, sum:true, w:'90px'},
  {k:'cantidadpendientecertificar', lbl:'Tn Pdte Cert.',   num:true, sum:true, w:'90px'},
  {k:'fechaminentrega',             lbl:'Entrega Desde',   num:false, w:'110px'},
  {k:'fechamaxentrega',             lbl:'Entrega Hasta',   num:false, w:'110px'},
  {k:'campana',                     lbl:'Campaña',         num:false, w:'150px'},
  {k:'corredor',                    lbl:'Corredor',        num:false, w:'150px'},
  {k:'_cpEstado',                   lbl:'Estado',          num:false, html:true, w:'110px'},
];

function cpGetVal(r, k){
  if(k==='_cpEstado') return r.estadoanulacion||'';
  if(k==='_cpPdteRecibir') return (r.cantidadmax||0) - (r.cantidadentregada||0);
  if(k==='_cpPdteLiquidar') return (r.cantidadentregada||0) - (r.cantidadliquidada||0);
  if(k==='_cpFijado'){ const aj=r.cantidadmax||0, fj=r.cantidadfijada||0;
    const t=(r.tipocontrato||'').toLowerCase();
    if(t.includes('a precio')) return 2;               // ordena: a precio arriba
    return aj>0 ? Math.min(1, fj/aj) : 0; }            // % fijado como valor de orden
  return r[k];
}
// ¿El contrato de compra tiene precio / fijación?
function cpFijadoChip(r){
  const t=(r.tipocontrato||'').toLowerCase();
  const aj=r.cantidadmax||0, fj=r.cantidadfijada||0, px=r.preciopromediofijado||0;
  const pxTxt = px>0 ? ` <span style="color:var(--muted);font-weight:400">$${fmt.num(px)}</span>` : '';
  if(t.includes('a precio'))       return '<span class="chip ok">A precio</span>'+pxTxt;
  if(t.includes('contra entrega')) return '<span class="chip neutral">C/Entrega</span>';
  if(aj>0 && fj>=aj*0.999)         return '<span class="chip ok">Fijado</span>'+pxTxt;
  if(fj>0)                          return `<span class="chip warn">Fijado ${fmt.pct(fj/aj)}</span>`+pxTxt;
  return '<span class="chip info">A fijar (sin precio)</span>';
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
  let cnt=cpFiltered.length, tnAj=0, tnRec=0, tnLiq=0;
  cpFiltered.forEach(r => {
    tnAj  += r.cantidadmax || 0;
    tnRec += r.cantidadentregada || 0;
    tnLiq += r.cantidadliquidada || 0;
  });
  const tnPdt = tnAj - tnRec;
  const tnPdtLiq = tnRec - tnLiq;   // entregado pero aun no liquidado
  const cumplimiento = tnAj>0 ? tnRec/tnAj : null;
  document.getElementById('kpi-row-cp').innerHTML = `
    <div class="kpi"><div class="lbl">Contratos</div><div class="val">${fmt.int(cnt)}</div><div class="hint">de ${fmt.int(DATA_CP.length)} totales</div></div>
    <div class="kpi"><div class="lbl">Toneladas Ajustadas</div><div class="val">${fmt.num(tnAj)}</div><div class="hint">Cantidad final post-ajustes</div></div>
    <div class="kpi green"><div class="lbl">Toneladas Recibidas</div><div class="val">${fmt.num(tnRec)}</div><div class="hint">Cumplimiento: ${fmt.pct(cumplimiento)}</div></div>
    <div class="kpi orange"><div class="lbl">Tn Pendientes de Recibir</div><div class="val">${fmt.num(tnPdt)}</div><div class="hint">= Ajustadas − Recibidas</div></div>
    <div class="kpi red"><div class="lbl">Tn Pendientes de Liquidar</div><div class="val">${fmt.num(tnPdtLiq)}</div><div class="hint">= Recibidas − Liquidadas (de lo entregado)</div></div>
  `;

  const byG = {};
  cpFiltered.forEach(r => {
    const p = r.producto || '—';
    if(!byG[p]) byG[p] = {cnt:0,tnAj:0,tnRec:0,tnLiq:0};
    byG[p].cnt++;
    byG[p].tnAj  += r.cantidadmax || 0;
    byG[p].tnRec += r.cantidadentregada || 0;
    byG[p].tnLiq += r.cantidadliquidada || 0;
  });
  const gOrder = Object.entries(byG).sort((a,b)=>b[1].tnAj - a[1].tnAj);
  document.getElementById('grain-meta-cp').textContent = `${gOrder.length} productos`;
  document.getElementById('grain-grid-cp').innerHTML = gOrder.map(([g,v]) => {
    const pct = v.tnAj>0 ? v.tnRec/v.tnAj : 0;
    const pdt = v.tnAj - v.tnRec;
    const pdtLiq = v.tnRec - v.tnLiq;
    const pctLiq = v.tnRec>0 ? v.tnLiq/v.tnRec : 0;
    return `<div class="grain-card ${grainClass(g)}">
      <div class="name"><span>${g}</span><span class="cnt">${v.cnt} contratos</span></div>
      <div class="row"><span class="k">Tn Ajustadas</span><span><b>${fmt.num(v.tnAj)}</b></span></div>
      <div class="row"><span class="k">Tn Recibidas</span><span>${fmt.num(v.tnRec)} <span style="color:var(--muted)">(${fmt.pct(pct)})</span></span></div>
      <div class="row"><span class="k">Tn Pdte Recibir</span><span style="color:var(--orange)"><b>${fmt.num(pdt)}</b></span></div>
      <div class="row"><span class="k">Tn Pdte Liquidar</span><span style="color:var(--red)"><b>${fmt.num(pdtLiq)}</b> <span style="color:var(--muted);font-weight:400">(${fmt.pct(pctLiq)} liq.)</span></span></div>
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
    const st = c.w ? ` style="width:${c.w};min-width:${c.w}"` : '';
    return `<th class="${cls}" data-k="${c.k}" data-num="${c.num?1:0}"${st}>${c.lbl}<span class="arrow">${ar||'⇅'}</span></th>`;
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
  const MAX = 400;
  const visibleRows = rows.slice(0,MAX);
  document.getElementById('tbl-body-cp').innerHTML = visibleRows.map((r,i) => {
    const id = rowId(r);
    const selCls = SEL_CP.has(id) ? ' class="row-sel"' : '';
    return `<tr data-id="${id}" data-i="${i}"${selCls}>`+CP_TABLE_COLS.map(c=>{
      if(c.k==='_cpEstado') return '<td>'+cpEstadoChip(r)+'</td>';
      if(c.k==='_cpFijado') return '<td>'+cpFijadoChip(r)+'</td>';
      if(c.k==='organizacion'){ const o=r.organizacion||''; return `<td style="min-width:${c.w}" title="${o.replace(/"/g,'&quot;')}">${o||'<span class=muted>—</span>'}</td>`; }
      const v = cpGetVal(r, c.k);
      if(c.num) return `<td class="num">${v==null?'<span class=muted>—</span>':fmt.num(v)}</td>`;
      const sv = v==null?'':String(v);
      return `<td title="${sv.replace(/"/g,'&quot;')}">${sv||'<span class=muted>—</span>'}</td>`;
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
  const MAX = 400;
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

// Diferimos: el panel default es 'home', no urge renderizar Compra · Posición.
// Si el usuario navega a Compra rápido, el render aún se ejecuta inmediato porque
// requestIdleCallback corre dentro de 50-300ms.
(window.requestIdleCallback || ((fn) => setTimeout(fn, 50)))(() => {
  cpfRebuildSelects();
  cpfRender();
});


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
  {k:'pctFijado',       lbl:'¿A Precio?',       num:true, html:true},
  {k:'usdCubierto',     lbl:'USD Cubierto',     num:true, sum:true},
  {k:'usdFaltante',     lbl:'USD Faltante',     num:true, sum:true},
  {k:'tnFaltante',      lbl:'Tn Faltante',      num:true, sum:true},
  {k:'precio',          lbl:'Precio USD',       num:true, sum:'avg'},
  {k:'_ctosDetalle',    lbl:'Contratos del Grano', num:false, html:true},
  {k:'_estado',         lbl:'Estado',           num:false, html:true},
];

// Render del detalle de contratos del grano (para columna "Contratos del Grano")
function cjCtosDetalleHtml(r){
  const ctos = r.ctosDelGrano || [];
  if(!ctos.length) return '<span style="color:#dc2626;font-weight:600">— sin contratos —</span>';
  // Mostrar resumen breve: "3 ctos · 561 tn · vence May/Jul 2026"
  const tn = ctos.reduce((s,c)=>s+(Number(c.cantidadmax)||0),0);
  const entregada = ctos.reduce((s,c)=>s+(Number(c.cantidadentregada)||0),0);
  const pendEnt = tn - entregada;
  // Sacar meses únicos de fechamaxentrega
  const meses = new Set();
  const MES_ABREV = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
  ctos.forEach(c => {
    const f = c.fechamaxentrega || c.fechaminentrega || c.fecha;
    const m = String(f||'').match(/(\d{4})-(\d{2})/);
    if(m) meses.add(`${MES_ABREV[parseInt(m[2])-1]}/${m[1].slice(2)}`);
  });
  const mesesStr = [...meses].slice(0,4).join(', ');
  // Tooltip con detalle por contrato
  const tooltip = ctos.slice(0,12).map(c => {
    const f = c.fechamaxentrega || c.fechaminentrega;
    const fStr = String(f||'').match(/(\d{4})-(\d{2})-(\d{2})/);
    const fDate = fStr ? `${fStr[3]}/${fStr[2]}/${fStr[1].slice(2)}` : '';
    return `${c.numerodocumento || c.contrato || '?'}: ${fmt.num(c.cantidadmax||0)} tn (entreg ${fStr ? fDate : '?'})`;
  }).join(' | ');
  return `<span title="${escapeHtml(tooltip)}" style="font-size:10.5px">
    <b>${ctos.length}</b> cto${ctos.length>1?'s':''} · <b>${fmt.num(tn)}</b> tn
    ${pendEnt>0 ? `<span style="color:#b45309"> · pend ${fmt.num(pendEnt)}</span>` : ''}
    ${mesesStr ? `<br><span style="color:var(--muted)">${escapeHtml(mesesStr)}</span>` : ''}
  </span>`;
}

let cjFiltered = [];
let cjSortKey = null, cjSortDir = 1;
const SEL_CJ = new Set();
let lastClickIdxCj = null;

// Poblar dropdowns CONDICION y VENDEDOR + listeners ------------------------
function cjInitFilters(){
  // condiciones de pago: solo las que contienen "canje"
  const condSel = document.getElementById('cj-cond');
  // Orden cronologico: parsea "Canje <Mes> <Año>" y ordena por (Año, Mes).
  // Las que no tienen fecha parseable (ej. "Canje Insumos") van al final.
  const MES_NUM = {
    enero:1, febrero:2, marzo:3, abril:4, mayo:5, junio:6,
    julio:7, agosto:8, septiembre:9, setiembre:9, octubre:10, noviembre:11, diciembre:12
  };
  function condKey(c){
    const m = (c||"").toLowerCase().match(/canje\s+([a-záéíóúñ]+)\s+(\d{4})/i);
    if(!m) return [9999, 99, c];   // sin fecha al final, alfabetico entre si
    const mes = MES_NUM[m[1]] || 99;
    const anio = parseInt(m[2], 10);
    return [anio, mes, c];
  }
  const allConds = [...new Set(SALDOS.map(s => s.condicionpago).filter(v => v && v.toLowerCase().includes('canje')))]
    .sort((a,b) => {
      const ka = condKey(a), kb = condKey(b);
      if(ka[0] !== kb[0]) return ka[0] - kb[0];
      if(ka[1] !== kb[1]) return ka[1] - kb[1];
      return String(ka[2]).localeCompare(String(kb[2]), 'es');
    });
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

  // campañas: todas las que aparezcan en los contratos de compra (más reciente primero)
  const campSel = document.getElementById('cj-camp');
  const allCamps = [...new Set((DATA_CP || []).map(c => c.campana).filter(v => v))]
    .sort((a, b) => String(b).localeCompare(String(a), 'es', {numeric:true}));
  campSel.innerHTML = '<option value="">Todas</option>' +
    allCamps.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');

  condSel.addEventListener('change', cjApply);
  vendSel.addEventListener('change', cjApply);
  campSel.addEventListener('change', cjApply);
}

function cjGetSelectedConds(){
  const sel = document.getElementById('cj-cond');
  return [...sel.selectedOptions].map(o => o.value);
}

// Normaliza un nombre de cliente para hacer matching robusto
// (quita SA, SRL, puntos, acentos, espacios extra, todo en MAYÚS)
function cjNormName(s){
  if(!s) return '';
  return String(s).toUpperCase()
    .normalize('NFD').replace(/[̀-ͯ]/g,'')   // quita acentos
    .replace(/\bS\.?A\.?C?\.?I?\.?F?\.?I?\.?\b/g,'')   // SA, SAC, SACIFI, etc
    .replace(/\bS\.?R\.?L\.?\b/g,'')                    // SRL
    .replace(/\bS\.?A\.?S\.?\b/g,'')                    // SAS
    .replace(/[.,()]/g,'')                              // puntuación
    .replace(/\s+/g,' ')
    .trim();
}

// Si la condición de pago es de canje, sacar el mes-año (1-12, año)
function cjMesAnio(cond){
  const MES_NUM = {
    enero:1, febrero:2, marzo:3, abril:4, mayo:5, junio:6,
    julio:7, agosto:8, septiembre:9, setiembre:9, octubre:10, noviembre:11, diciembre:12,
  };
  const m = (cond||'').toLowerCase().match(/canje\s+([a-záéíóúñ]+)\s+(\d{4})/i);
  if(!m) return null;
  const mes = MES_NUM[m[1].normalize('NFD').replace(/[̀-ͯ]/g,'')] || null;
  if(!mes) return null;
  return {mes, anio: parseInt(m[2], 10)};
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
        mesesParsed: [],  // [{mes, anio}]
        condiciones: new Set(),
      };
    }
    byClient[id].saldoArs += s.importemonppal || 0;
    byClient[id].saldoUsd += s.importemonsecundaria || 0;
    if(s.condicionpago) byClient[id].condiciones.add(s.condicionpago);
    const m = (s.condicionpago||'').match(/canje\s+(\w+)\s+(\d{4})/i);
    if(m) byClient[id].meses.add(m[1] + ' ' + m[2]);
    const ma = cjMesAnio(s.condicionpago);
    if(ma) byClient[id].mesesParsed.push(ma);
  });

  // Agrupar contratos de compra POR CLIENTE — matching normalizado (S.A. ≡ SA, etc)
  // SALDOS no trae campaña, así que el filtro por campaña se aplica acá, sobre los
  // contratos que se consideran como "cobertura" del canje.
  const campSel = document.getElementById('cj-camp').value;
  const ctosByClient = {};
  (DATA_CP || []).forEach(c => {
    if(campSel && c.campana !== campSel) return;
    const k = cjNormName(c.organizacion);
    if(!k) return;
    if(!ctosByClient[k]) ctosByClient[k] = [];
    ctosByClient[k].push(c);
  });

  // Construir filas
  const granoSel = document.getElementById('cj-grano').value;
  const rows = [];
  Object.values(byClient).forEach(b => {
    const nameKey = cjNormName(b.cliente);
    const ctos = ctosByClient[nameKey] || [];

    // determinar grano:
    //  - si el usuario eligió uno fijo, usar ese (forzado)
    //  - si "auto": (1) primer contrato del cliente; (2) si no tiene contratos, inferir
    //    por el mes de canje: julio-diciembre suele ser MAÍZ (campaña de invierno
    //    tardía), enero-junio suele ser SOJA (campaña gruesa). Sin info, soja.
    let grano = granoSel === 'auto' ? null : granoSel;
    if(!grano){
      for(const c of ctos){
        const g = granoBCR(c.producto);
        if(g){ grano = g; break; }
      }
      if(!grano){
        // inferir por mes
        const meses = b.mesesParsed.map(x => x.mes);
        const algunoTardio = meses.some(m => m >= 6);  // junio-diciembre
        grano = algunoTardio ? 'maiz' : 'soja';
      }
    }

    const precio = CJ_PX[grano] || 0;
    const tc = CJ_PX.tc || 0;
    const saldoUSDeff = (b.saldoUsd && Math.abs(b.saldoUsd) > 0.01)
      ? b.saldoUsd
      : (tc>0 ? b.saldoArs / tc : 0);
    const tnCanje = precio > 0 ? saldoUSDeff / precio : 0;

    // Contratos del cliente del grano elegido — separamos por entregado/pendiente
    // y medimos cuánto está "a precio" (fijado) vs "a fijar".
    let tnContratadas = 0, tnFijada = 0;
    const ctosDelGrano = [];
    ctos.forEach(c => {
      if(granoBCR(c.producto) === grano){
        const max = Number(c.cantidadmax) || 0;
        tnContratadas += max;
        // cantidadfijada = tn con precio cerrado (puede venir negativa o > max → clamp)
        const fij = Math.min(Math.max(Number(c.cantidadfijada) || 0, 0), max);
        tnFijada += fij;
        ctosDelGrano.push(c);
      }
    });
    // % de las tn contratadas que ya tienen precio cerrado (0..1)
    const pctFijado = tnContratadas > 0 ? tnFijada / tnContratadas : 0;

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
      tnFijada, pctFijado,
      ctosDelGrano,   // array de contratos del grano elegido (para mostrar detalle)
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

// ¿Los contratos que cubren el canje tienen precio cerrado o están a fijar?
function cjPrecioChip(r){
  if(!r.ctosDelGrano || !r.ctosDelGrano.length)
    return '<span class="muted">— sin contratos —</span>';
  const p = r.pctFijado || 0;
  if(p >= 0.995) return '<span class="chip ok">A precio</span>';
  if(p <= 0.005) return '<span class="chip err">A fijar</span>';
  return `<span class="chip warn" title="${fmt.num(r.tnFijada||0)} de ${fmt.num(r.tnContratadas||0)} tn a precio">Parcial ${fmt.pct(p)}</span>`;
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
  document.getElementById('cj-camp').value = '';
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

  const MAX = 400;
  const visible = sorted.slice(0,MAX);
  document.getElementById('tbl-body-canjes').innerHTML = visible.map((r,i) => {
    const id = String(r.id);
    const selCls = SEL_CJ.has(id) ? ' class="row-sel"' : '';
    return `<tr data-id="${id}" data-i="${i}"${selCls}>`+CJ_TABLE_COLS.map(c=>{
      if(c.k==='_estado') return '<td>'+cjEstadoChip(r)+'</td>';
      if(c.k==='pctFijado') return '<td class="num">'+cjPrecioChip(r)+'</td>';
      if(c.k==='_ctosDetalle') return '<td>'+cjCtosDetalleHtml(r)+'</td>';
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

  // detalle de clientes por vendedor (para el botón Copiar / enviar)
  CJ_VEND_DETALLE = {};
  rows.forEach(r => { const v = r.vendedor || '— sin vendedor —'; (CJ_VEND_DETALLE[v] = CJ_VEND_DETALLE[v] || []).push(r); });

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
    <th>Enviar</th>
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
      <td><button class="clear" style="padding:3px 9px;font-size:11px" onclick="cjCopyVend(this)" data-v="${escapeHtml(v.vendedor)}">📋 Copiar</button></td>
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
      <td></td>
    </tr>`;
}

// Detalle de clientes por vendedor (poblado en cjRenderVendedores) para el botón Copiar
let CJ_VEND_DETALLE = {};
function cjCopyVend(btn){
  const v = btn.dataset.v;
  const rows = (CJ_VEND_DETALLE[v] || []).slice().sort((a,b) => (b.saldoUsd||0) - (a.saldoUsd||0));
  if(!rows.length){ return; }
  const conds = [...new Set(rows.flatMap(r => [...(r.condiciones||[])]))].join(', ');
  let tot = 0, totFalt = 0;
  let txt = `Canjes ${conds} — ${v}\n\n`;
  rows.forEach(r => {
    tot += r.saldoUsd || 0; totFalt += r.usdFaltante || 0;
    const vto = r.meses && r.meses.size ? ` · vto ${[...r.meses].join(', ')}` : '';
    const falta = (r.usdFaltante||0) > 1 ? `  ⚠ FALTA CONTRATO USD ${fmt.num(r.usdFaltante)}` : '';
    txt += `• ${r.cliente}: USD ${fmt.num(r.saldoUsd)}${vto}${falta}\n`;
  });
  txt += `\nTOTAL canje: USD ${fmt.num(tot)}`;
  if(totFalt > 1) txt += `\nFalta cubrir con contrato de compra: USD ${fmt.num(totFalt)}`;
  navigator.clipboard.writeText(txt).then(() => {
    const o = btn.textContent; btn.textContent = '✓ Copiado'; setTimeout(() => btn.textContent = o, 1500);
  }).catch(() => alert(txt));
}

cjInitFilters();
// Diferimos: solo se ve si el usuario navega a Compra · Canjes.
(window.requestIdleCallback || ((fn) => setTimeout(fn, 200)))(cjApply);


/* ============================================================
   =====  FINALES DE COMPRA — verificador de factor  =========
   ============================================================ */
// Datos de balanza embebidos (fuente de búsqueda). La lista que ve el usuario
// arranca VACÍA y se va llenando a medida que carga códigos de contrato.
const FINALES = PAYLOAD.finales || [];
const FL_GEN = (PAYLOAD.generated_at || '').slice(0,10);   // fecha del último refresco
const FL_KEY = 'tablero-finales-lista-v1';
const FL_BAK_KEY = 'tablero-finales-lista-backup-v1';   // respaldo automático
let FL_LIST = [];
try { const s=JSON.parse(localStorage.getItem(FL_KEY)||'[]'); if(Array.isArray(s)) FL_LIST=s; } catch(e){}
// Si la lista principal está vacía pero hay respaldo con datos, ofrecer recuperar
try {
  const bak=JSON.parse(localStorage.getItem(FL_BAK_KEY)||'[]');
  if(Array.isArray(bak) && bak.length && !FL_LIST.length){ FL_LIST = bak; }
} catch(e){}
let FL_OPEN = new Set();   // contratos con el detalle desplegado
let FL_UNDO = null;        // snapshot para "Deshacer" el último borrado
function flSave(){
  try{
    localStorage.setItem(FL_KEY, JSON.stringify(FL_LIST));
    if(FL_LIST.length) localStorage.setItem(FL_BAK_KEY, JSON.stringify(FL_LIST));  // respaldo solo si hay datos
  }catch(e){}
}
function flSnapshot(){ FL_UNDO = JSON.parse(JSON.stringify(FL_LIST)); }
function flGroupKey(r){ return String(r.contrato || ('CTG '+r.ctg)); }

const FL_TC = (PAYLOAD.bcr && PAYLOAD.bcr.tc_usd_ars) || 0;
const FL_COLS = [
  {k:'contrato',     lbl:'Contrato'},
  {k:'ctg',          lbl:'CTG'},
  {k:'cliente',      lbl:'Cliente'},
  {k:'grano',        lbl:'Grano'},
  {k:'humedad',      lbl:'Hum. %', num:true},
  {k:'danos',        lbl:'Daños %', num:true},
  {k:'verdes',       lbl:'Verdes %', num:true},
  {k:'quebrados',    lbl:'Queb. %', num:true},
  {k:'precio',       lbl:'Precio', num:true},
  {k:'factorCereal', lbl:'F. Cereal.', num:true},
  {k:'factorOficial',lbl:'F. Oficial', num:true},
  {k:'_difpct',      lbl:'Dif. %', num:true, html:true},
  {k:'_camara',      lbl:'Cámara'},
  {k:'_flete',       lbl:'Flete'},
  {k:'_estado',      lbl:'Estado', html:true},
  {k:'_pneto',       lbl:'P. neto', num:true},
  {k:'_totalusd',    lbl:'Total USD (±)', num:true, html:true},
];
function flTn(r){ const v=Number(r.kgAplicar)||0; return v>2000 ? v/1000 : v; }   // >2000 => viene en kg
function flIsUsd(r){ return /usd|dol/i.test(String(r.moneda||'')) || (Number(r.precio)||0) < 2000; }
// factor que se aplicó en la liquidación: el de la cerealera; si no lo cargó, el oficial
function flFactorAplic(r){ return r.factorCereal!=null ? r.factorCereal : (r.factorOficial!=null ? r.factorOficial : null); }
// Dif % = 100 − factor (el descuento que aplica el factor). + = descuento, − = bonificación.
function flDifPct(r){ const f=flFactorAplic(r); return f==null ? null : Math.round((100-f)*100)/100; }
function flTotalUSD(r){
  const d=flDifPct(r); if(d==null) return null;
  const precio=Number(r.precio)||0, tn=flTn(r);
  let tot=precio*(d/100)*tn;        // + = a descontar, − = a bonificar
  if(!flIsUsd(r) && FL_TC) tot=tot/FL_TC;
  return tot;
}
function flCamara(r){ const v=r.condCamara; return (v==null||v==='')?'—':String(v); }
function flFlete(r){
  if(r.condFlete) return String(r.condFlete);
  if((r.fleteCorto&&String(r.fleteCorto).trim())||(r.fleteLargo&&String(r.fleteLargo).trim())) return 'Sí';
  return '—';
}
function flDifChip(r){
  const d=flDifPct(r); if(d==null) return '<span class="muted">—</span>';
  if(Math.abs(d)<0.005) return '<span style="color:var(--muted)">0</span>';
  const desc=d>0;  // 100−factor>0 => descuento
  return `<span title="${desc?'descuento':'bonificación'}" style="color:${desc?'#b45309':'var(--green)'};font-weight:600">${fmt.num2(d)}%</span>`;
}
function flTotChip(r){
  const t=flTotalUSD(r); if(t==null) return '<span class="muted">—</span>';
  if(Math.abs(t)<0.005) return '<span style="color:var(--muted)">0</span>';
  const desc=t>0;  // + = a descontar
  return `<span title="${desc?'a descontar':'a bonificar'}" style="color:${desc?'#b45309':'var(--green)'};font-weight:700">${fmt.num2(Math.abs(t))} ${desc?'desc.':'bonif.'}</span>`;
}
function flEstadoChip(r){
  const e=r.estado;
  if(e==='revisar')     return '<span class="chip err">⚠ Revisar</span>';
  if(e==='ok')          return '<span class="chip ok">✅ OK</span>';
  if(e==='calc_only')   return '<span class="chip" style="background:#dbeafe;color:#1e40af">🧮 Calculado</span>';
  if(e==='solo_cereal') return '<span class="chip warn">Solo cereal</span>';
  return '<span class="muted">— s/factor —</span>';
}
function flPNeto(r){
  const f=(r.factorOficial!=null)?r.factorOficial:r.factorCereal;
  const p=Number(r.precio)||0;
  return (f!=null && p) ? p*f/100 : null;
}
function flMsg(html,color){ const e=document.getElementById('fl-msg'); e.innerHTML=html; e.style.color=color||'var(--muted)'; }

// Busca en los datos de balanza por contrato / CTG / carta porte
function flBuscar(code){
  const q=String(code||'').toLowerCase().trim();
  if(!q) return [];
  return FINALES.filter(r=>{
    const ct=String(r.contrato||'').toLowerCase();
    const cg=String(r.ctg||'').toLowerCase();
    return ct.includes(q) || cg.includes(q) || q===cg || q===ct;
  });
}
function flAdd(){
  const inp=document.getElementById('fl-add');
  const code=inp.value.trim();
  if(!code){ return; }
  const hits=flBuscar(code);
  if(!hits.length){
    flMsg(`❌ No encontré <b>"${escapeHtml(code)}"</b> en balanza (último refresco ${FL_GEN}). Puede que aún no esté finalizado — avisame y actualizo.`,'var(--red)');
    return;
  }
  let added=0;
  hits.forEach(h=>{
    if(!FL_LIST.some(x=>String(x.ctg)===String(h.ctg) && x.contrato===h.contrato)){
      FL_LIST.push(h); added++;
    }
  });
  flSave(); flRender();
  flMsg(added ? `✅ Agregado: <b>${escapeHtml(hits[0].contrato||code)}</b> (${added} ${added>1?'liquidaciones':'liquidación'})` : 'Ese contrato ya estaba en la lista.', added?'var(--green)':'var(--orange)');
  inp.value=''; inp.focus();
}
const FL_EST_RANK={revisar:5,solo_cereal:4,calc_only:3,ok:2,sin_factor:1};
function flWorst(rows){ return rows.slice().sort((a,b)=>(FL_EST_RANK[b.estado]||0)-(FL_EST_RANK[a.estado]||0))[0].estado; }
function flRender(){
  document.getElementById('fl-head').innerHTML=
    FL_COLS.map(c=>`<th class="${c.num?'num':''}">${c.lbl}</th>`).join('')+'<th></th>';
  // agrupar por contrato, en orden de carga
  const order=[], groups={};
  FL_LIST.forEach(r=>{ const k=flGroupKey(r); if(!groups[k]){groups[k]={k,rows:[]};order.push(k);} groups[k].rows.push(r); });
  let html='';
  order.forEach(k=>{
    const g=groups[k], open=FL_OPEN.has(k), r0=g.rows[0], worst=flWorst(g.rows);
    // total USD del contrato (suma de las diferencias a descontar/bonificar)
    let gtot=0, gany=false;
    g.rows.forEach(r=>{ const t=flTotalUSD(r); if(t!=null){ gtot+=t; gany=true; } });
    const gtotChip = (!gany||Math.abs(gtot)<0.005) ? '' : `<span title="${gtot>0?'a descontar':'a bonificar'}" style="color:${gtot>0?'#b45309':'var(--green)'};font-weight:700">${fmt.num2(Math.abs(gtot))} ${gtot>0?'desc.':'bonif.'}</span>`;
    html+=`<tr class="fl-grp" data-c="${escapeHtml(k)}" style="cursor:pointer;background:#eef2ff;font-weight:600">
      <td>${open?'▼':'▶'} ${escapeHtml(String(r0.contrato||k))}</td>
      <td class="num">${g.rows.length} liq</td>
      <td>${escapeHtml(String(r0.cliente||''))}</td>
      <td>${escapeHtml(String(r0.grano||''))}</td>
      <td colspan="10" style="color:var(--muted);font-weight:400">${open?'':'(click para ver el detalle)'}</td>
      <td>${flEstadoChip({estado:worst})}</td>
      <td></td>
      <td class="num">${gtotChip}</td>
      <td style="white-space:nowrap"><a href="#" class="fl-dl" data-c="${escapeHtml(k)}" title="Descargar este contrato en Excel" style="text-decoration:none;margin-right:8px">📥</a><a href="#" class="fl-rmg" data-c="${escapeHtml(k)}" title="Quitar contrato" style="color:var(--red);text-decoration:none">✕</a></td>
    </tr>`;
    if(open){
      g.rows.forEach(r=>{
        const gi=FL_LIST.indexOf(r);
        html+='<tr style="background:#fff">'+FL_COLS.map(c=>{
          if(c.k==='_estado')  return '<td>'+flEstadoChip(r)+'</td>';
          if(c.k==='_difpct')  return '<td class="num">'+flDifChip(r)+'</td>';
          if(c.k==='_totalusd')return '<td class="num">'+flTotChip(r)+'</td>';
          if(c.k==='_camara')  return '<td>'+escapeHtml(flCamara(r))+'</td>';
          if(c.k==='_flete')   return '<td>'+escapeHtml(flFlete(r))+'</td>';
          if(c.k==='_pneto'){ const v=flPNeto(r); return `<td class="num">${v==null?'<span class=muted>—</span>':fmt.num2(v)}</td>`; }
          if(c.k==='contrato') return `<td style="padding-left:20px;color:var(--muted)">↳</td>`;
          let v=r[c.k];
          if(c.num) return `<td class="num">${v==null||v===''?'<span class=muted>—</span>':fmt.num2(Number(v))}</td>`;
          return `<td>${v==null?'':escapeHtml(String(v))}</td>`;
        }).join('')+`<td><a href="#" class="fl-rm" data-i="${gi}" title="Quitar liquidación" style="color:var(--red);text-decoration:none">✕</a></td>`+'</tr>';
      });
    }
  });
  document.getElementById('fl-body').innerHTML = html ||
    `<tr><td colspan="${FL_COLS.length+1}" style="padding:30px;text-align:center;color:var(--muted)">Lista vacía — agregá un contrato arriba ☝️</td></tr>`;
  const rev=FL_LIST.filter(r=>r.estado==='revisar').length;
  document.getElementById('fl-count').textContent=`${order.length} contratos · ${FL_LIST.length} liq`;
  document.getElementById('fl-meta').textContent=`${order.length} contratos`+(rev?` · ${rev} ⚠ a revisar`:'')+` · balanza al ${FL_GEN}`;
}
function flUndo(){ if(FL_UNDO){ FL_LIST=FL_UNDO; FL_UNDO=null; flSave(); flRender(); flMsg('↩ Restaurado.','var(--green)'); } }
function flMsgUndo(txt){ document.getElementById('fl-msg').innerHTML=`${txt} <a href="#" id="fl-undo" style="margin-left:8px;font-weight:600">↩ Deshacer</a>`; document.getElementById('fl-msg').style.color='var(--orange)'; }
function flExcelRows(rows, fname){
  if(!rows.length){ flMsg('No hay nada para exportar.','var(--orange)'); return; }
  const hdr=FL_COLS.map(c=>c.lbl);
  const line=a=>a.map(v=>{ v=(v==null?'':String(v)).replace(/"/g,'""'); return /[";\n]/.test(v)?`"${v}"`:v; }).join(';');
  const out=[line(hdr)];
  rows.forEach(r=>{
    out.push(line(FL_COLS.map(c=>{
      if(c.k==='_estado') return ({revisar:'A REVISAR',ok:'OK',calc_only:'CALCULADO',solo_cereal:'SOLO CEREAL',sin_factor:'SIN FACTOR'})[r.estado]||r.estado;
      if(c.k==='_pneto'){ const v=flPNeto(r); return v==null?'':v.toFixed(2).replace('.',','); }
      if(c.k==='_difpct'){ const v=flDifPct(r); return v==null?'':String(v).replace('.',','); }
      if(c.k==='_totalusd'){ const v=flTotalUSD(r); return v==null?'':v.toFixed(2).replace('.',','); }
      if(c.k==='_camara') return flCamara(r)==='—'?'':flCamara(r);
      if(c.k==='_flete') return flFlete(r)==='—'?'':flFlete(r);
      let v=r[c.k]; if(v==null) return '';
      if(c.num) return String(v).replace('.',',');
      return v;
    })));
  });
  const blob=new Blob(['﻿'+out.join('\n')],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download=fname; a.click();
}
function flExcel(){ if(!FL_LIST.length){ flMsg('No hay nada cargado para exportar.','var(--orange)'); return; } flExcelRows(FL_LIST, 'finales_compra_'+(FL_GEN||'')+'.csv'); }
function flExcelGrupo(k){
  const rows=FL_LIST.filter(r=>flGroupKey(r)===k);
  const nom=String((rows[0]&&rows[0].contrato)||k).replace(/[^a-zA-Z0-9_-]+/g,'_');
  flExcelRows(rows, 'final_'+nom+'.csv');
  flMsg('⬇️ Descargado el contrato '+(rows[0]&&rows[0].contrato||k),'var(--green)');
}
document.getElementById('fl-add-btn').addEventListener('click',flAdd);
document.getElementById('fl-add').addEventListener('keydown',e=>{ if(e.key==='Enter'){ e.preventDefault(); flAdd(); } });
document.getElementById('fl-clear').addEventListener('click',()=>{
  if(FL_LIST.length && !confirm('¿Vaciar TODA la lista? (vas a poder deshacer)')) return;
  flSnapshot(); FL_LIST=[]; try{localStorage.removeItem(FL_BAK_KEY);}catch(e){} flSave(); flRender();
  flMsgUndo('🗑️ Lista vaciada.');
});
document.getElementById('fl-xls').addEventListener('click',flExcel);
document.getElementById('fl-body').addEventListener('click',e=>{
  // descargar contrato individual en Excel
  const dl=e.target.closest('.fl-dl');
  if(dl){ e.preventDefault(); e.stopPropagation(); flExcelGrupo(dl.dataset.c); return; }
  // toggle abrir/cerrar contrato
  const grp=e.target.closest('.fl-grp');
  if(grp && !e.target.closest('.fl-rmg') && !e.target.closest('.fl-dl')){ const k=grp.dataset.c; if(FL_OPEN.has(k))FL_OPEN.delete(k);else FL_OPEN.add(k); flRender(); return; }
  // quitar contrato entero
  const rmg=e.target.closest('.fl-rmg');
  if(rmg){ e.preventDefault(); const k=rmg.dataset.c; flSnapshot(); FL_LIST=FL_LIST.filter(r=>flGroupKey(r)!==k); flSave(); flRender(); flMsgUndo('🗑️ Contrato quitado.'); return; }
  // quitar una liquidación
  const rm=e.target.closest('.fl-rm');
  if(rm){ e.preventDefault(); const i=parseInt(rm.dataset.i,10); flSnapshot(); FL_LIST.splice(i,1); flSave(); flRender(); flMsgUndo('🗑️ Liquidación quitada.'); return; }
});
document.getElementById('fl-msg').addEventListener('click',e=>{ if(e.target.id==='fl-undo'){ e.preventDefault(); flUndo(); } });
(window.requestIdleCallback || ((fn)=>setTimeout(fn,300)))(flRender);


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

// Editor: ahora todos los usuarios internos son editores (la persistencia es server-side via KV).
// pgIsEditor() queda como true por compatibilidad con codigo viejo que pregunta por PAT.
function pgIsEditor(){ return API_AVAILABLE || !!localStorage.getItem("tablero-granos-github-pat-v1"); }

let PG_DATA = [];
let PG_LOADED = false;

async function pgLoadInitial(){
  // 1) Intentar primero la API (Cloudflare KV via Worker) — fuente canonica compartida
  const fromApi = await apiLoad("pagos");
  if(Array.isArray(fromApi) && fromApi.length){
    PG_DATA = fromApi;
    PG_LOADED = true;
    try{ localStorage.setItem(PG_KEY, JSON.stringify(PG_DATA)); }catch(e){}
    return;
  }
  // 2) Fallback: localStorage (editor) — si la KV esta vacia pero el navegador tiene datos
  if(pgIsEditor()){
    try {
      const saved = JSON.parse(localStorage.getItem(PG_KEY) || "null");
      if(Array.isArray(saved) && saved.length){
        PG_DATA = saved;
        PG_LOADED = true;
        // Migracion: KV vacia + localStorage con datos → pushear a la API para inicializarla
        if(API_AVAILABLE && Array.isArray(fromApi)){
          apiSave("pagos", PG_DATA).then(ok => {
            if(ok) console.log("[migracion] localStorage → KV (pagos):", PG_DATA.length, "filas");
          });
        }
        return;
      }
    } catch(e){}
    PG_DATA = JSON.parse(JSON.stringify(PG_INICIALES));
  } else {
    // 3) Fallback lector: trae el JSON del repo
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

function pgSave(){
  localStorage.setItem(PG_KEY, JSON.stringify(PG_DATA));
  pgStorageInfo();
  // Auto-save a Cloudflare KV via Worker (debounce 1.5s) — sin necesidad de PAT
  if(API_AVAILABLE){
    apiSaveDebounced("pagos", () => PG_DATA, (state) => {
      const el = document.getElementById("pg-autobackup-status");
      if(!el) return;
      if(state === "pending"){ el.innerHTML = "✏️ guardando..."; el.style.color = "var(--orange)"; }
      else if(state === "saving"){ el.innerHTML = "⏳ guardando en servidor..."; el.style.color = "var(--orange)"; }
      else if(state === "saved"){ el.innerHTML = "✓ guardado en servidor"; el.style.color = "var(--green)"; }
      else if(state === "error"){ el.innerHTML = "⚠️ error guardando (queda en local)"; el.style.color = "var(--red)"; }
    });
  }
}
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

// ===== POS PEND editable tipo Excel =====
// El usuario puede escribir en la celda Pos Pend un número fijo o una FÓRMULA que
// empiece con "=" usando los nombres de las columnas de esa fila, p. ej.:
//   =pendcos + pendingreso - ctospe
// Se guarda por producto (o por familia, en la fila TOTAL) en localStorage.
// Celda vacía => vuelve al cálculo automático (pend ingreso − ctos p.e.).
const PN_PPF_KEY = "tablero-granos-pospend-formulas-v1";
let PN_PPF = {};
try { PN_PPF = JSON.parse(localStorage.getItem(PN_PPF_KEY) || "{}") || {}; } catch(e){ PN_PPF = {}; }
function ppfSave(){ localStorage.setItem(PN_PPF_KEY, JSON.stringify(PN_PPF)); }
// Alias (sin acentos, minúsculas) -> key interna de la fila
const PN_PPF_ALIAS = {
  cosechado:'cosechado', pendcos:'pendCos', totalprod:'prodTot',
  planta:'plantaTot', granelcampo:'silobolsa', silobolsa:'silobolsa',
  semillero:'silo', silo:'silo', clasificado:'bolsas', bolsas:'bolsas',
  corte:'corteBolsa', cortebolsa:'corteBolsa', totalpc:'pcTot', pc:'pcTot',
  compra:'compraTot', totcompra:'compraTot', pendingreso:'compraPend',
  entregado:'compraEntr', oferta:'ofertaTot', vtasem:'vtaSem',
  pendvincular:'pendVincular', totventa:'ventaCtosAjust', venta:'ventaCtosAjust',
  ctospe:'ventaCtos', pendentrega:'ventaCtos', ctosentr:'ventaEntr',
  prodpend:'prodPendSem', proddesp:'prodDespSem', totalprodsem:'prodTotSem',
  demanda:'demandaTot', demandapend:'demandaTotPend', pospend:'posPend', posicion:'posicion',
};
function ppfEval(formula, ctx){
  let f = String(formula || '').trim();
  if(f.startsWith('=')) f = f.slice(1);
  // Formato argentino en los números QUE ESCRIBIÓ EL USUARIO: 1.234,5 -> 1234.5.
  // Va ANTES de sustituir columnas: los valores sustituidos ya vienen con punto
  // decimal y este paso les arrancaría el punto (ej. 19334.445 -> 19334445).
  f = f.replace(/(\d)\.(?=\d{3}(\D|$))/g, '$1').replace(/,/g, '.');
  const norm = s => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'');
  // reemplazar cada nombre de columna por su valor en esta fila
  let expr = f.replace(/[a-zA-Záéíóúñ_][a-zA-Z0-9áéíóúñ_]*/g, (id) => {
    const k = PN_PPF_ALIAS[norm(id)];
    if(!k) throw new Error('columna desconocida: ' + id);
    return '(' + (Number(ctx[k]) || 0) + ')';
  });
  if(!/^[0-9+\-*\/(). ]*$/.test(expr)) throw new Error('caracteres inválidos');
  return Function('"use strict";return(' + expr + ')')();
}

// Mapeo de producto Finnegans a "familia" agrupadora
function pnFamilia(prod){
  if(!prod) return "OTROS";
  const p = prod.toLowerCase();
  if(p.includes("soja")) return "SOJA";
  if(p.includes("maíz") || p.includes("maiz")) return "MAÍZ";
  if(p.includes("trigo")) return "TRIGO";
  // el resto (cebada, camelina, colza, girasol, sorgo, arveja, avena, centeno, etc.) -> OTROS
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
// PLANTA usa el idioma del DEM-SUP Soja del Extranet Agronasaja:
//   Granel en Campo (ex Silo Bolsa) · Granel en Semillero (ex Silo) ·
//   Stock Clasificado (ex Bolsas) · Corte de Bolsa (nueva, informativa).
// Las keys internas (silo/bolsas/silobolsa) se conservan para no romper
// los valores manuales guardados ni los drill-downs.
const PN_COLS = [
  // PRODUCCIÓN va primero (pedido del gerente): Cosechado = "Kg por Beneficiario" (KG AGNSJ)
  // y Pend Cos = "Estimado por Cosechar (Agronasaja)" de la vista Información General del
  // Portal de Producción (extranet /vistas/produccion-info), cultivos unificados.
  {grp:"PRODUCCIÓN", cls:"grp-prod",   cols:[
    {k:"cosechado",    lbl:"Cosechado"},
    {k:"pendCos",      lbl:"Pend Cos",   calc:true},
    {k:"prodTot",      lbl:"Total",      calc:true},
  ]},
  {grp:"PLANTA",     cls:"grp",        cols:[
    {k:"plantaTot",    lbl:"Total",      calc:true},
    {k:"silobolsa",    lbl:"Granel en Campo"},
    {k:"silo",         lbl:"Granel en Semillero"},
    {k:"bolsas",       lbl:"Stock Clasificado"},
    {k:"corteBolsa",   lbl:"Corte de Bolsa", calc:true},
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
  // PROD. SEMILLA y Demanda Total Pendiente: mismas columnas que el DEM-SUP Soja del
  // extranet (S pedidos de campo pendientes, T despachos a producción, O venta pendiente).
  // Solo semilla soja por ahora; el resto de productos muestra "—".
  {grp:"PROD. SEMILLA", cls:"grp-prod", cols:[
    {k:"prodPendSem",  lbl:"Prod Pendiente",  calc:true},
    {k:"prodDespSem",  lbl:"Prod Despachado", calc:true},
    {k:"prodTotSem",   lbl:"Total Prod",      calc:true},
  ]},
  {grp:"DEMANDA",    cls:"grp-venta",  cols:[
    {k:"demandaTot",   lbl:"Demanda Tot",calc:true},
    {k:"demandaTotPend", lbl:"Demanda Tot Pendiente", calc:true},
  ]},
  {grp:"RESULTADO",  cls:"grp-resultado", cols:[
    {k:"posPend",      lbl:"Pos Pend",   calc:true},
    {k:"posicion",     lbl:"Posición",   calc:true, hl:true},
  ]},
];

// Defaults embebidos: valores manuales que vienen del cierre — el usuario los puede sobreescribir
// editando la celda (PN_MANUAL en localStorage tiene prioridad sobre estos defaults).
// PLANTA / stock físico embebido (silobolsa de la bajada de Finnegans). Es físico = pertenece
// a la campaña de COSECHA VIGENTE (25/26). La PRODUCCIÓN va aparte, por campaña (PN_PROD_BY_CAMP).
// Vacío: el stock (silo/bolsas/silobolsa) sale 100% AUTOMÁTICO del reporte
// "Resumen de Stock por Depósito" (USR_RESSTOCKDEP), categorizado por depósito.
// Antes había valores hardcodeados de un print viejo que pisaban el auto y quedaban
// desactualizados (ej. arveja 262 cuando el reporte da 0). Ya no.
const PN_DEFAULTS = {};
// Producción propia POR CAMPAÑA (cosechado + pendiente a cosechar, en tn). Cada campaña con
// sus cultivos. La producción se muestra según la campaña SELECCIONADA en el filtro.
// Producción propia POR CAMPAÑA — AUTOMÁTICO del Portal de Producción de Agronasaja
// (cosechado + pendiente de cosecha 25/26, y siembra estimada 26/27). Si la app no
// respondió en el build, cae al fallback hardcodeado de abajo.
const PN_PROD_FALLBACK = {
  "CAMPAÑA 25-26": {
    "Grano Soja":            { cosechado: 22199.2, pendcos: 15.7 },
    "Grano Maíz":            { cosechado: 18071.3, pendcos: 269.2 },
    "Grano Maíz Pisingallo": { cosechado: 353.4 },
    "Grano Girasol":         { cosechado: 642.7 },
    "Grano Maní":            { cosechado: 616.3 },
    "Grano Sorgo":           { cosechado: 230.5, pendcos: 18.6 },
  },
  "CAMPAÑA 26-27": {
    "Grano Trigo Pan":  { pendcos: 9104 },
    "Grano Cebada":     { pendcos: 1787 },
    "Grano Camelina":   { pendcos: 263 },
    "Grano Colza":      { pendcos: 187 },
  },
};
const PN_PROD_BY_CAMP = (PAYLOAD.produccion_camp && Object.keys(PAYLOAD.produccion_camp).length)
  ? PAYLOAD.produccion_camp : PN_PROD_FALLBACK;
const PN_PROD_KEYS = new Set(["cosechado", "pendcos", "campoest"]);
let PN_SEL_CAMP = "";   // campaña seleccionada (para elegir la producción correcta)
function pnGetMan(prod, k){
  const o = PN_MANUAL[prod] || {};
  if(o[k] !== undefined && o[k] !== null && o[k] !== "") return Number(o[k]) || 0;
  // producción (cosechado/pendcos/campoest) sale de la campaña seleccionada
  if(PN_PROD_KEYS.has(k)){
    const camp = PN_PROD_BY_CAMP[PN_SEL_CAMP] || {};
    return Number((camp[prod] || {})[k]) || 0;
  }
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

function pnCalcRow(producto, opsCompra, opsVenta, incluyePlanta){
  // incluyePlanta=false => campaña NO vigente: el STOCK FÍSICO (silo/bolsas/silobolsa y el
  // cosechado auto de traslados) no le pertenece, va en 0. La PRODUCCIÓN (cosechado/pendiente
  // cargados) sí se muestra según la campaña seleccionada (viene de PN_PROD_BY_CAMP).
  const origen = (incluyePlanta === false) ? false : true;
  // PLANTA: TODO auto desde Stock por Deposito (USR_RESSTOCKDEP), categorizado por nombre de deposito
  // SILO (silos físicos sin "bolsa", "descarte", "ventas"), SILOBOLSA, BOLSAS (DEPOSITO VENTAS ...)
  // Si no hay valor en la API, cae a lo cargado manualmente (PN_MANUAL) para no perder data vieja.
  let siloAuto      = origen ? ((PAYLOAD.stock_silo      && PAYLOAD.stock_silo[producto])      || 0) : 0;
  let bolsasAuto    = origen ? ((PAYLOAD.stock_bolsas    && PAYLOAD.stock_bolsas[producto])    || 0) : 0;
  let silobolsaAuto = origen ? ((PAYLOAD.stock_silobolsa && PAYLOAD.stock_silobolsa[producto]) || 0) : 0;
  // SEMILLA SOJA: sale del DEM-SUP Soja del Extranet Agronasaja (vista /vistas/ops-demsup-soja):
  //  - GRANEL (SEM. GRANEL SOJA DM%): Granel en Campo = col C (silobolsa), Granel en
  //    Semillero = col D (silos planta) — kg netos de la partida de la campaña × merma.
  //  - TERMINADA (SEM. SOJA DM%): Stock Clasificado = col K (unidades → bolsas 40kg → tn).
  // El resto de productos sigue con el stock crudo por depósito.
  if(origen && pnEsDemsupProd(producto)){
    silobolsaAuto = (PN_DEMSUP.campo_tn_prod     || {})[producto] || 0;
    siloAuto      = (PN_DEMSUP.semillero_tn_prod || {})[producto] || 0;
  }
  if(origen && pnEsDemsupSem(producto)){
    bolsasAuto    = (PN_DEMSUP.clasif_tn_prod    || {})[producto] || 0;
  }
  // El valor cargado (manual en localStorage o default embebido de la bajada) tiene prioridad
  // sobre el auto de USR_RESSTOCKDEP; si no hay cargado, usa el auto.
  const siloMan      = origen ? pnGetMan(producto, "silo")      : 0;
  const bolsasMan    = origen ? pnGetMan(producto, "bolsas")    : 0;
  const silobolsaMan = origen ? pnGetMan(producto, "silobolsa") : 0;
  const silo       = siloMan      > 0 ? siloMan      : siloAuto;
  const bolsas     = bolsasMan    > 0 ? bolsasMan    : bolsasAuto;
  const silobolsa  = silobolsaMan > 0 ? silobolsaMan : silobolsaAuto;
  const plantaTot  = silo + bolsas + silobolsa;
  // Corte de Bolsa (DEM-SUP col L: descarte de semilla ya embolsada) — informativa,
  // es una pérdida del proceso, NO suma al total de Planta.
  const corteBolsa = (origen && PN_DEMSUP) ? ((PN_DEMSUP.corte_tn_prod || {})[producto] || 0) : 0;

  // PRODUCCIÓN
  // Cosechado AUTO desde traslados (Traslado CPE Agronasaja + Rec Sem PROPIA, origen Dep Cosecha)
  // Campo Est: total estimado a cosechar (manual o default embebido)
  // Pend Cos: AUTO = Campo Est - Cosechado (decrece a medida que se cosecha).
  //           Si el usuario carga un valor manual de pendcos, ese override prevalece.
  // La SEMILLA SOJA no suma cosechado propio: su producción viaja UNIFICADA dentro de
  // "Grano Soja" en el Seguimiento de Cosecha (convenio AGRONASAJA) hasta que producción
  // pueda separar consumo vs semilla. Si no, el traslado "Rec Sem PROPIA" la contaba doble.
  const esSemillaSoja = pnFamilia(producto) === 'SOJA' && pnSubtipo(producto) === 'Semilla';
  const cosechadoAuto = (origen && !esSemillaSoja) ? ((PAYLOAD.cosechado && PAYLOAD.cosechado[producto]) || 0) : 0;
  const cosechadoMan  = pnGetMan(producto, "cosechado");  // producción de la campaña seleccionada
  // El valor cargado (real, de la planilla de producción) tiene prioridad sobre el auto de traslados.
  const cosechado  = cosechadoMan > 0 ? cosechadoMan : cosechadoAuto;
  const campoEst   = pnGetMan(producto, "campoest");
  const pendCosManual = pnGetMan(producto, "pendcos");
  // Si el usuario cargó pend cos manual, usar ese. Sino, calcular Campo Est - Cosechado
  const pendCos    = pendCosManual > 0 ? pendCosManual : Math.max(0, campoEst - cosechado);
  // prodTot = lo que SE VA A COSECHAR EN TOTAL = pendiente + ya cosechado.
  // Si hay campoEst, ese ES el total (no sumar pendCos para no duplicar).
  const prodTot    = campoEst > 0 ? campoEst : (pendCos + cosechado);

  // COMPRA (auto desde contratos de compra, reporte REST)
  // Cantidad ajustada = entregado + pendiente de entrega (como el "Resumen de Contratos" de Finnegans).
  let compraTot = 0, compraEntr = 0, compraPend = 0;
  opsCompra.forEach(c => {
    const ent = Number(c.cantidadentregada) || 0;
    const pen = Number(c.cantidadpendienteentrega) || 0;
    compraEntr += ent;
    compraPend += pen;
    compraTot  += ent + pen;   // ajustada
  });

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
  // Cantidad ajustada = entregado + pendiente de entrega (reporte de Finnegans).
  let ventaCtosAjust = 0, ventaEntr = 0, ventaCtos = 0;
  opsVenta.forEach(c => {
    if((c.producto || "").toLowerCase().includes("sem")) return;  // semilla va aparte (manual)
    const ent = Number(c.cantidadentregada) || 0;
    const pen = Number(c.cantidadpendienteentrega) || 0;
    ventaEntr      += ent;
    ventaCtos      += pen;         // pendiente de entrega (directo del contrato)
    ventaCtosAjust += ent + pen;   // ajustada = total venta
  });

  // DEMANDA = total comprometido = semilla + pend vincular + contratos no-semilla
  const demandaTot = vtaSem + pendVincular + ventaCtosAjust;

  // PROD. SEMILLA + DEMANDA TOTAL PENDIENTE — del DEM-SUP Soja del extranet (solo semilla soja):
  //   Prod Pendiente = col S (pedidos de campo pendientes, siembra propia)
  //   Prod Despachado = col T (traslados internos a depósitos de producción)
  //   Demanda Total Pendiente = venta pendiente (col O, NVs) + prod pendiente (col S)
  const prodPendSem  = PN_DEMSUP ? ((PN_DEMSUP.prod_pend_tn_prod  || {})[producto] || 0) : 0;
  const prodDespSem  = PN_DEMSUP ? ((PN_DEMSUP.prod_desp_tn_prod  || {})[producto] || 0) : 0;
  const prodTotSem   = prodPendSem + prodDespSem;
  const ventaPendSem = PN_DEMSUP ? ((PN_DEMSUP.venta_pend_tn_prod || {})[producto] || 0) : 0;
  const demandaTotPend = ventaPendSem + prodPendSem;

  // RESULTADO
  const posPend = compraPend - ventaCtos;   // pendiente neto compra vs venta
  const posicion = ofertaTot - demandaTot;

  return {
    silo, bolsas, silobolsa, plantaTot, corteBolsa,
    pendCos, cosechado, campoEst, prodTot,
    pcTot,
    compraTot, compraPend, compraEntr,
    ofertaTot,
    vtaSem, pendVincular, ventaCtosAjust, ventaCtos, ventaEntr,
    prodPendSem, prodDespSem, prodTotSem,
    demandaTot, demandaTotPend,
    posPend, posicion,
  };
}

function pnFiltrarOps(){
  // DATA_CP y DATA ya vienen con maizSplit aplicado al inicio (Grano Maíz 1ra/2da)
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
// Familias (TOTAL SOJA, etc.) expandidas: por defecto TODO colapsado -> solo se ven los totales.
const PN_FAM_EXPANDED = new Set();

// ===== DRILL-DOWN de la Posicion Granaria =====
// Al clickear una celda numerica (pendiente ingreso, entregado, silo bolsa, etc.) se despliega
// el DETALLE: que contratos / que depositos componen ese numero.
const PN_STOCK_DET = (PAYLOAD.stock_detalle || {});   // {producto:[{dep,cat,tn}]}
// DEM-SUP Soja — fuente de datos del Extranet Agronasaja (/vistas/ops-demsup-soja, DW en vivo).
// El GRANEL EN CAMPO (col C: silobolsa con merma por depósito) alimenta la columna Silo Bolsa
// de la semilla granel soja; el payload trae además el resto de columnas de esa vista
// (semillero, compras, clasificado, ventas) listas para usarse cuando haga falta.
const PN_DEMSUP = PAYLOAD.demsup_soja || null;
// granel (SEM. GRANEL SOJA DM%) → cols C/D del DEM-SUP; terminada (SEM. SOJA DM%) → cols K/L/O/S/T
const pnEsDemsupProd = p => !!(PN_DEMSUP && p && p.toUpperCase().startsWith(PN_DEMSUP.prod_prefix));
const pnEsDemsupSem  = p => !!(PN_DEMSUP && p && PN_DEMSUP.prod_prefix_sem && p.toUpperCase().startsWith(PN_DEMSUP.prod_prefix_sem));
const PN_PROD_PEND_DET = (PAYLOAD.produccion_pend_det || {});  // {campaña:{producto:[{campo,tn}]}}
let PN_LAST_COMPRAS = [], PN_LAST_VENTAS = [];         // ops filtradas del ultimo render
const PN_DRILL = {
  cosechado:      {kind:'prodpend', field:'cosechado', title:'Cosechado por campo / lote — Kg AGNSJ (retiros beneficiario Agronasaja)'},
  pendCos:        {kind:'prodpend', field:'pendcos',   title:'Estimado por cosechar (parte Agronasaja) por campo / lote'},
  silo:           {kind:'stock', cat:'SILO',      title:'Granel en semillero (silos planta)'},
  bolsas:         {kind:'stock', cat:'BOLSAS',    title:'Stock clasificado (depósitos de venta)'},
  silobolsa:      {kind:'stock', cat:'SILOBOLSA', title:'Granel en campo (silo bolsas)'},
  compraTot:      {kind:'compra', field:'tot',  title:'Compra · cantidad ajustada (entregado + pendiente)'},
  compraPend:     {kind:'compra', field:'pend', title:'Compra · pendiente de ingreso — contratos que faltan entregar'},
  compraEntr:     {kind:'compra', field:'entr', title:'Compra · ya entregado'},
  ventaCtosAjust: {kind:'venta',  field:'tot',  title:'Venta · cantidad ajustada (entregado + pendiente)'},
  ventaCtos:      {kind:'venta',  field:'pend', title:'Venta · pendiente de entrega — contratos que faltan entregar'},
  ventaEntr:      {kind:'venta',  field:'entr', title:'Venta · ya entregado'},
};
function pnFij(c){
  const ent = Number(c.cantidadentregada)||0, pen = Number(c.cantidadpendienteentrega)||0;
  const aj = ent + pen, fij = Number(c.cantidadfijada)||0;
  if(aj <= 0) return {t:'—', cls:''};
  if(fij >= aj - 0.05) return {t:'✓ A precio', cls:'pn-fij-si'};
  if(fij > 0.05)       return {t:'◑ '+Math.round(fij/aj*100)+'% fijado', cls:'pn-fij-par'};
  return {t:'○ A fijar', cls:'pn-fij-no'};
}
// Bloque DEM-SUP Soja para los drill-downs de Planta: granel en campo (col C),
// granel en semillero (col D) o stock clasificado (col K), por variedad —
// mismos números que las columnas homónimas de la vista del Extranet Agronasaja.
function pnDemsupDrillHTML(cat, conMargen){
  if(!PN_DEMSUP) return '';
  const cfg = {
    SILOBOLSA: {det:'detalle_campo',       col:'C', titulo:'Semilla soja a granel en campo — DEM-SUP Soja',     unidad:'silobolsa(s)'},
    SILO:      {det:'detalle_semillero',   col:'D', titulo:'Semilla soja a granel en semillero — DEM-SUP Soja', unidad:'silo(s) de planta'},
    BOLSAS:    {det:'detalle_clasificado', col:'K', titulo:'Semilla soja clasificada (stock) — DEM-SUP Soja',   unidad:'producto(s)'},
  }[cat];
  if(!cfg) return '';
  let rows = [];
  (PN_DEMSUP.variedades||[]).forEach(v => ((PN_DEMSUP[cfg.det]||{})[v]||[]).forEach(d => rows.push(d)));
  if(!rows.length) return '';
  rows.sort((a,b)=>b.tn-a.tn);
  const totBls = (PN_DEMSUP.tot_bls||{})[cfg.col] || 0, totTn = (PN_DEMSUP.tot_tn||{})[cfg.col] || 0;
  let t = `<div class="pn-drill-head"${conMargen?' style="margin-top:12px"':''}>🌱 ${cfg.titulo} <span>· ${rows.length} ${cfg.unidad} · ${fmt.num(totBls)} bolsas 40kg · ${fmt.num(totTn)} tn</span></div>`;
  if(cat === 'BOLSAS'){
    t += `<table class="pn-drill-tbl"><thead><tr><th>Variedad</th><th>Producto (embalaje)</th><th class="num">Unidades</th><th class="num">Bolsas 40kg</th><th class="num">Toneladas</th></tr></thead><tbody>`;
    rows.forEach(r => {
      t += `<tr><td style="font-weight:600">${escapeHtml(r.variedad)}</td><td>${escapeHtml(r.producto)}</td><td class="num">${fmt.num(r.cantidad)}</td><td class="num">${fmt.num(r.bls)}</td><td class="num">${fmt.num(r.tn)}</td></tr>`;
    });
    t += `<tr class="pn-drill-tot"><td colspan="3">Total stock clasificado (col K del DEM-SUP)</td><td class="num">${fmt.num(totBls)}</td><td class="num">${fmt.num(totTn)}</td></tr>`;
  } else {
    t += `<table class="pn-drill-tbl"><thead><tr><th>Variedad</th><th>Depósito</th><th>Planta</th><th class="num">Kg netos</th><th class="num">Merma</th><th class="num">Bolsas 40kg</th><th class="num">Toneladas</th></tr></thead><tbody>`;
    rows.forEach(r => {
      t += `<tr><td style="font-weight:600">${escapeHtml(r.variedad)}</td><td>${escapeHtml(r.deposito)}</td><td>${escapeHtml(r.planta||'')}</td><td class="num">${fmt.num(r.kilos)}</td><td class="num">× ${r.merma}</td><td class="num">${fmt.num(r.bls)}</td><td class="num">${fmt.num(r.tn)}</td></tr>`;
    });
    t += `<tr class="pn-drill-tot"><td colspan="5">Total (col ${cfg.col} del DEM-SUP)</td><td class="num">${fmt.num(totBls)}</td><td class="num">${fmt.num(totTn)}</td></tr>`;
  }
  t += `</tbody></table>`;
  t += `<div style="font-size:10.5px;color:var(--muted);margin-top:4px">Fuente: <strong>DEM-SUP Soja · Extranet Agronasaja</strong> (DW Finnegans en vivo, ${escapeHtml(PN_DEMSUP.campana||'')}). Misma lógica que la vista <em>/vistas/ops-demsup-soja</em>.</div>`;
  return t;
}

function pnDrillHTML(type, prods){
  const cfg = PN_DRILL[type]; if(!cfg) return '';
  const set = new Set(prods);
  const esC = (prods.length>1);   // mostrar columna Producto si es un TOTAL de familia
  if(cfg.kind === 'stock'){
    // La SEMILLA SOJA sale del DEM-SUP Soja del extranet: se muestra en su propio bloque
    // (por variedad) y se excluye del listado crudo para no duplicar. Granel (C/D) en
    // silobolsa/silos; terminada (K) en depósitos de venta.
    const exclDemsup = p => (
      ((cfg.cat === 'SILOBOLSA' || cfg.cat === 'SILO') && pnEsDemsupProd(p)) ||
      (cfg.cat === 'BOLSAS' && pnEsDemsupSem(p))
    );
    const demsupAca = prods.some(exclDemsup);
    let rows = [];
    prods.forEach(p => {
      if(demsupAca && exclDemsup(p)) return;
      (PN_STOCK_DET[p]||[]).forEach(d => { if(d.cat===cfg.cat) rows.push({p, dep:d.dep, tn:d.tn}); });
    });
    rows.sort((a,b)=>b.tn-a.tn);
    const demsupHTML = demsupAca ? pnDemsupDrillHTML(cfg.cat, rows.length > 0) : '';
    if(!rows.length && !demsupHTML) return `<div class="pn-drill-inner"><div class="pn-drill-head">${cfg.title}</div><div class="pn-drill-empty">Sin stock en depósitos de este tipo.</div></div>`;
    let t = `<div class="pn-drill-inner">`;
    if(rows.length){
      const tot = rows.reduce((s,r)=>s+r.tn,0);
      t += `<div class="pn-drill-head">${cfg.title} <span>· ${rows.length} depósito(s) · ${fmt.num(tot)} tn</span></div>`;
      t += `<table class="pn-drill-tbl"><thead><tr><th>Depósito</th>${esC?'<th>Producto</th>':''}<th class="num">Toneladas</th></tr></thead><tbody>`;
      rows.forEach(r => { t += `<tr><td>${escapeHtml(r.dep)}</td>${esC?`<td>${escapeHtml(r.p)}</td>`:''}<td class="num">${fmt.num(r.tn)}</td></tr>`; });
      t += `<tr class="pn-drill-tot"><td${esC?' colspan="2"':''}>Total</td><td class="num">${fmt.num(tot)}</td></tr>`;
      t += `</tbody></table>`;
    }
    t += demsupHTML;
    t += `</div>`;
    return t;
  }
  if(cfg.kind === 'prodpend'){
    // Cosechado / Pendiente de cosecha por CAMPO y LOTE (tn AGNSJ, convenio AGRONASAJA),
    // de la campaña seleccionada. Incluye el rinde del lote: real en cosechado,
    // estimado (RINDE_EST / promedio real / regional) en el pendiente.
    const camp = (PN_PROD_PEND_DET[PN_SEL_CAMP] || {});
    let rows = [];
    prods.forEach(p => (((camp[p] || {})[cfg.field]) || []).forEach(d => rows.push({p, ...d})));
    rows.sort((a,b)=>b.tn-a.tn);
    if(!rows.length) return `<div class="pn-drill-inner"><div class="pn-drill-head">${cfg.title}</div><div class="pn-drill-empty">Sin desglose por campo para esta campaña.</div></div>`;
    const tot = rows.reduce((s,r)=>s+r.tn,0);
    // rinde promedio ponderado por tn (solo filas con rinde cargado)
    const conR = rows.filter(r=>r.rinde>0);
    const rProm = conR.length ? conR.reduce((s,r)=>s+r.rinde*r.tn,0) / conR.reduce((s,r)=>s+r.tn,0) : 0;
    let t = `<div class="pn-drill-inner"><div class="pn-drill-head">${cfg.title} <span>· ${rows.length} lote(s) · ${fmt.num(tot)} tn${rProm?` · rinde prom ${fmt.num(rProm)} kg/ha`:''}</span></div>`;
    t += `<table class="pn-drill-tbl"><thead><tr><th>Campo</th><th>Lote</th><th>Cultivo</th><th class="num">Rinde (kg/ha)</th><th class="num">Tn Agronasaja</th></tr></thead><tbody>`;
    rows.forEach(r => {
      t += `<tr><td>${escapeHtml(r.campo||'—')}</td><td>${escapeHtml(r.lote||'—')}</td><td>${escapeHtml(r.cultivo||r.p||'')}</td><td class="num">${r.rinde>0?fmt.num(r.rinde):'—'}</td><td class="num">${fmt.num(r.tn)}</td></tr>`;
    });
    t += `<tr class="pn-drill-tot"><td colspan="3">Total (${rows.length} lotes)</td><td class="num">${rProm?fmt.num(rProm):'—'}</td><td class="num">${fmt.num(tot)}</td></tr>`;
    t += `</tbody></table>`;
    t += `<div style="font-size:10.5px;color:var(--muted);margin-top:4px">Fuente: <strong>Portal de Producción — Información General</strong> (vista del Extranet Agronasaja): cosechado = "Kg por Beneficiario" (retiros AGNSJ, todos los convenios); pendiente = "Estimado por Cosechar (Agronasaja)". En el pendiente, el rinde es el estimado usado para proyectar las tn.</div>`;
    t += `</div>`;
    return t;
  }
  // contratos compra / venta
  const src = (cfg.kind==='compra') ? PN_LAST_COMPRAS : PN_LAST_VENTAS;
  const cp = (cfg.kind==='compra');
  const val = c => {
    const ent = Number(c.cantidadentregada)||0, pen = Number(c.cantidadpendienteentrega)||0;
    return cfg.field==='pend' ? pen : cfg.field==='entr' ? ent : ent+pen;
  };
  // venta: la semilla va aparte (manual), igual que en pnCalcRow → excluir de los contratos
  let rows = src.filter(c => set.has(c.producto) && !(cfg.kind==='venta' && (c.producto||'').toLowerCase().includes('sem')));
  rows = rows.filter(c => val(c) > 0.001);
  rows.sort((a,b)=>val(b)-val(a));
  if(!rows.length) return `<div class="pn-drill-inner"><div class="pn-drill-head">${cfg.title}</div><div class="pn-drill-empty">Sin contratos.</div></div>`;
  const tot = rows.reduce((s,c)=>s+val(c),0);
  const lblTn = cfg.field==='pend' ? (cp?'Pend. ingreso':'Pend. entrega') : cfg.field==='entr' ? 'Entregado' : 'Ajustada';
  let t = `<div class="pn-drill-inner"><div class="pn-drill-head">${cfg.title} <span>· ${rows.length} contrato(s) · ${fmt.num(tot)} tn</span></div>`;
  t += `<table class="pn-drill-tbl"><thead><tr><th>Nº</th><th>${cp?'Entregador / Vendedor':'Cliente'}</th>${esC?'<th>Producto</th>':''}<th>Campaña</th><th>Entrega</th><th class="num">${lblTn} (tn)</th><th>¿A precio?</th></tr></thead><tbody>`;
  rows.forEach(c => {
    const f = pnFij(c);
    const nro = (c.numerointerno!=null?('#'+c.numerointerno):'') + (c.numerodocumentoadicional?` · ${escapeHtml(String(c.numerodocumentoadicional))}`:'');
    const ent = (c.fechaminentrega||'') && (c.fechamaxentrega||'') ? `${pnFecha(c.fechaminentrega)}–${pnFecha(c.fechamaxentrega)}` : (pnFecha(c.fechaminentrega)||pnFecha(c.fechamaxentrega)||'—');
    const camp = (c.campana||'').replace('CAMPAÑA ','').replace('CAMPANA ','') || '—';
    t += `<tr><td class="pn-drill-nro">${nro||'—'}</td><td>${escapeHtml(c.organizacion||'—')}</td>${esC?`<td>${escapeHtml(c.producto||'')}</td>`:''}<td>${camp}</td><td class="pn-drill-fe">${ent}</td><td class="num">${fmt.num(val(c))}</td><td class="${f.cls}">${f.t}</td></tr>`;
  });
  const ncolTot = 5 + (esC?1:0);
  t += `<tr class="pn-drill-tot"><td colspan="${ncolTot}">Total (${rows.length})</td><td class="num">${fmt.num(tot)}</td><td></td></tr>`;
  t += `</tbody></table></div>`;
  return t;
}
function pnFecha(s){ if(!s) return ''; const m=String(s).match(/^(\d{4})-(\d{2})-(\d{2})/); return m?`${m[3]}/${m[2]}/${m[1].slice(2)}`:String(s); }
// Total de columnas de la tabla (1 producto + todas las de PN_COLS) para el colspan del detalle
const PN_TOTAL_COLS = 1 + PN_COLS.reduce((n,g)=>n+g.cols.length,0);

function pnRender(){
  const {compras, ventas} = pnFiltrarOps();
  PN_LAST_COMPRAS = compras; PN_LAST_VENTAS = ventas;   // para el drill-down
  // PLANTA y PRODUCCIÓN (stock físico + cosecha) pertenecen a la CAMPAÑA DE COSECHA VIGENTE,
  // no a campañas forward (ej. 26/27, que solo tiene contratos compra/venta sin grano físico).
  // Cosecha vigente = la campaña MÁS RECIENTE con grano realmente entregado (>=20% del máximo
  // entregado de cualquier campaña). Así 25/26 (miles de tn) cuenta y 26/27 (forward) no.
  // Auto-mantiene: cuando 26/27 empiece a recibir grano de verdad, pasa a ser la vigente.
  const selCamp = document.getElementById("pn-campana").value;
  const entrPorCamp = {};
  (DATA_CP || []).forEach(c => {
    if(!c.campana) return;
    entrPorCamp[c.campana] = (entrPorCamp[c.campana] || 0) + (Number(c.cantidadentregada) || 0);
  });
  const maxEntr = Math.max(0, ...Object.values(entrPorCamp));
  const umbral = maxEntr * 0.2;
  let campCosecha = null;
  Object.keys(entrPorCamp).forEach(k => {
    if(entrPorCamp[k] >= umbral && entrPorCamp[k] > 0 && (campCosecha === null || k > campCosecha)) campCosecha = k;
  });
  // incluyePlanta: el STOCK FÍSICO (silo/bolsas/silobolsa) solo en la cosecha vigente (o "Todas").
  // La PRODUCCIÓN se muestra por campaña (PN_PROD_BY_CAMP) sin importar cuál sea la vigente.
  PN_SEL_CAMP = selCamp;   // usado por pnGetMan para elegir la producción de la campaña
  const incluyePlanta = !selCamp || selCamp === campCosecha;
  const tieneProd = !!(PN_PROD_BY_CAMP[selCamp]);
  document.getElementById("pn-info").textContent =
    `${compras.length} contratos compra · ${ventas.length} contratos venta` +
    (incluyePlanta ? "" : " · sin stock físico (campaña no vigente)") +
    (tieneProd ? " · con producción estimada" : "");

  // Productos únicos en los filtros aplicados
  const prods = new Set();
  compras.forEach(c => { if(c.producto) prods.add(c.producto); });
  ventas.forEach(c => { if(c.producto) prods.add(c.producto); });
  // Productos con PRODUCCIÓN cargada para la campaña seleccionada (siempre se muestran)
  Object.keys(PN_PROD_BY_CAMP[selCamp] || {}).forEach(p => prods.add(p));
  // Productos con stock físico / manual — solo si la campaña es la vigente
  if(incluyePlanta){
    Object.keys(PN_MANUAL).forEach(p => prods.add(p));
    Object.keys(PN_DEFAULTS).forEach(p => prods.add(p));
    // Semilla soja con datos en el DEM-SUP del extranet: entra aunque no tenga contratos
    // en la campaña (si no, variedades como DM 46I20 quedaban afuera de los totales).
    if(PN_DEMSUP){
      ["campo_tn_prod","semillero_tn_prod","clasif_tn_prod","corte_tn_prod",
       "venta_pend_tn_prod","prod_pend_tn_prod","prod_desp_tn_prod"]
        .forEach(k => Object.keys(PN_DEMSUP[k] || {}).forEach(p => prods.add(p)));
    }
  }
  const prodList = [...prods].sort();

  // Agrupar por familia
  const byFamilia = {};
  prodList.forEach(p => {
    const f = pnFamilia(p);
    if(!byFamilia[f]) byFamilia[f] = [];
    byFamilia[f].push(p);
  });
  const familias = Object.keys(byFamilia).sort((a,b) => {
    const orden = ["SOJA","MAÍZ","TRIGO","OTROS"];
    return orden.indexOf(a) - orden.indexOf(b);
  });

  // Calcular por producto
  const dataPorProd = {};
  prodList.forEach(p => {
    const cp = compras.filter(c => c.producto === p);
    const vp = ventas.filter(c  => c.producto === p);
    dataPorProd[p] = pnCalcRow(p, cp, vp, incluyePlanta);
  });

  // POS PEND estilo Excel: si el usuario guardó un número/fórmula para el producto,
  // pisa el cálculo automático ANTES de armar los totales (familia y general lo heredan).
  prodList.forEach(p => {
    if(PN_PPF[p] === undefined) return;
    try { dataPorProd[p].posPend = ppfEval(PN_PPF[p], dataPorProd[p]); dataPorProd[p]._ppfErr = null; }
    catch(e){ dataPorProd[p]._ppfErr = String(e.message || e); }
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
      if(c.k === 'posPend'){
        // Editable tipo Excel: número o fórmula "=" con nombres de columnas
        const f = PN_PPF[prod];
        const tit = r._ppfErr ? ('Error: ' + r._ppfErr) : (f !== undefined ? ('ƒ ' + f + ' = ' + fmt.num(v)) : 'Editable: número o fórmula (ej. =pendcos + pendingreso - ctospe)');
        row += `<td class="editable" title="${escapeHtml(tit)}"><input type="text" data-ppf="${escapeHtml(prod)}" value="${f !== undefined ? escapeHtml(f) : (v ? fmt.num(v) : '')}" placeholder="—" style="${r._ppfErr ? 'color:#dc2626;font-weight:700' : (f !== undefined ? 'color:#7c3aed;font-weight:700' : '')}"/></td>`;
      } else if(c.edit){
        row += `<td class="editable"><input type="text" data-prod="${escapeHtml(prod)}" data-k="${c.manK}" value="${v ? fmt.num(v) : ''}" placeholder="—"/></td>`;
      } else if(c.hl){
        const cls2 = v >= 0 ? "pos-pos" : "pos-neg";
        row += `<td class="${cls2}">${fmt.num(v)}</td>`;
      } else if(PN_DRILL[c.k] && v){
        row += `<td class="calc pn-drill-cell-link" data-drill="${c.k}" data-prod="${escapeHtml(prod)}">${fmt.num(v)}</td>`;
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

    // Pos Pend de la familia: fórmula propia (clave "FAM:<familia>") o suma de productos.
    // Si hay fórmula, se ajusta también el TOTAL GENERAL por la diferencia.
    let famPpfErr = null;
    const famPpf = PN_PPF['FAM:' + fam];
    if(famPpf !== undefined){
      const orig = totFam.posPend || 0;
      try {
        totFam.posPend = ppfEval(famPpf, totFam);
        grandTotal.posPend += (totFam.posPend - orig);
      } catch(e){ famPpfErr = String(e.message || e); }
    }

    // 1) TOTAL familia = ENCABEZADO clickeable (amarillo). Por defecto COLAPSADO -> solo totales.
    const famExpanded = PN_FAM_EXPANDED.has(fam);
    let rowFam = `<tr class="pn-grupo pn-fam-header" data-fam="${escapeHtml(fam)}" style="cursor:pointer"><td class="pn-prod-cell">${famExpanded ? '▾' : '▸'} TOTAL ${fam}</td>`;
    PN_COLS.forEach(g => g.cols.forEach(c => {
      const v = totFam[c.k];
      const cls = c.hl ? (v >= 0 ? "pos-pos" : "pos-neg") : "";
      if(c.k === 'posPend'){
        const tit = famPpfErr ? ('Error: ' + famPpfErr) : (famPpf !== undefined ? ('ƒ ' + famPpf + ' = ' + fmt.num(v)) : 'Editable: número o fórmula (ej. =pendcos + pendingreso - ctospe)');
        rowFam += `<td class="editable" title="${escapeHtml(tit)}"><input type="text" data-ppf-fam="${escapeHtml(fam)}" value="${famPpf !== undefined ? escapeHtml(famPpf) : (v ? fmt.num(v) : '')}" placeholder="—" style="font-weight:700;${famPpfErr ? 'color:#dc2626' : (famPpf !== undefined ? 'color:#7c3aed' : '')}"/></td>`;
      } else if(PN_DRILL[c.k] && v){
        rowFam += `<td class="${cls} pn-drill-cell-link" data-drill="${c.k}" data-fam="${escapeHtml(fam)}">${fmt.num(v)}</td>`;
      } else {
        rowFam += `<td class="${cls}">${v ? fmt.num(v) : '—'}</td>`;
      }
    }));
    rowFam += "</tr>";
    body += rowFam;
    totalsFam[fam] = totFam;

    // DESCARTE + POTENCIAL (solo SOJA y TRIGO): el procesado de semilla deja ~10% de descarte (grano).
    //  - Descarte  = 10% de la semilla en stock (a granel en silo + silobolsa)
    //  - Potencial = descarte + 10% de la semilla en contratos pendientes de ingreso
    if(fam === "SOJA" || fam === "TRIGO"){
      const semStock = (totSem.silo || 0) + (totSem.silobolsa || 0);
      const semPendIng = totSem.compraPend || 0;
      const descarte  = semStock * 0.10;
      const potencial = descarte + semPendIng * 0.10;
      const ncols = PN_COLS.reduce((n,g) => n + g.cols.length, 0);
      if(descarte > 0.05 || potencial > 0.05){
        body += `<tr class="pn-descarte" style="background:#f0fdf4">`+
          `<td class="pn-prod-cell" style="padding-left:36px;font-weight:600;color:#15803d">↳ Descarte semilla (10%)</td>`+
          `<td class="pos-pos" style="font-weight:700;color:#15803d">${fmt.num(descarte)}</td>`+
          `<td colspan="${ncols-1}" style="font-size:11px;color:var(--muted)">10% de ${fmt.num(semStock)} tn de semilla (granel + silo bolsa)</td></tr>`;
        body += `<tr class="pn-potencial" style="background:#ecfeff">`+
          `<td class="pn-prod-cell" style="padding-left:36px;font-weight:700;color:#0e7490">↳ Potencial descarte</td>`+
          `<td style="font-weight:800;color:#0e7490">${fmt.num(potencial)}</td>`+
          `<td colspan="${ncols-1}" style="font-size:11px;color:var(--muted)">descarte + 10% de ${fmt.num(semPendIng)} tn pendiente de ingreso</td></tr>`;
      }
    }

    // 2) Detalle (productos + semillas) — SOLO si la familia está expandida
    if(famExpanded){
      otros.forEach(prod => { body += pnRenderProdRow(prod, {indent:true}); });
      if(semillas.length > 0){
        const semExpanded = PN_SEM_EXPANDED.has(fam);
        let rowSem = `<tr class="pn-semilla-header" data-sem-fam="${escapeHtml(fam)}" style="cursor:pointer;background:#fff7ed">
          <td class="pn-prod-cell" style="font-weight:600;padding-left:36px">${semExpanded ? '▾' : '▸'} SEMILLA ${fam} <span style="font-size:10.5px;color:var(--muted);font-weight:500">(${semillas.length} variedades)</span></td>`;
        PN_COLS.forEach(g => g.cols.forEach(c => {
          const v = totSem[c.k];
          const cls2 = c.hl ? (v >= 0 ? "pos-pos" : "pos-neg") : "";
          rowSem += `<td class="${cls2}" style="font-weight:600">${v ? fmt.num(v) : '—'}</td>`;
        }));
        rowSem += "</tr>";
        body += rowSem;
        if(semExpanded){
          semillas.forEach(prod => { body += pnRenderProdRow(prod, {cls:'pn-semilla-child', indent:true}); });
        }
      }
    }
  });

  document.getElementById("pn-tbody").innerHTML = body || '<tr><td colspan="99" style="padding:30px;text-align:center;color:var(--muted)">Sin productos para los filtros aplicados</td></tr>';

  // Footer total general
  let foot = `<tr class="pn-total"><td class="pn-prod-cell">TOTAL GENERAL</td>`;
  PN_COLS.forEach(g => g.cols.forEach(c => {
    foot += `<td>${grandTotal[c.k] ? fmt.num(grandTotal[c.k]) : '—'}</td>`;
  }));
  foot += "</tr>";
  document.getElementById("pn-tfoot").innerHTML = foot;

  // Listeners inputs editables (solo los de valores manuales, con data-k)
  document.querySelectorAll("#pn-tbody input[data-k]").forEach(inp => {
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

  // Listeners POS PEND tipo Excel (por producto y por familia): guarda el texto tal cual
  // (número o fórmula "="); vacío = volver al cálculo automático.
  document.querySelectorAll("#pn-tbody input[data-ppf], #pn-tbody input[data-ppf-fam]").forEach(inp => {
    inp.addEventListener("click", e => e.stopPropagation());   // no togglear la familia
    inp.addEventListener("blur", () => {
      if(inp.value === inp.defaultValue) return;   // sin cambios -> no pisar nada
      const key = (inp.dataset.ppf !== undefined) ? inp.dataset.ppf : ('FAM:' + inp.dataset.ppfFam);
      const val = (inp.value || '').trim();
      if(!val) delete PN_PPF[key];
      else PN_PPF[key] = val;
      ppfSave();
      pnRender();
    });
    inp.addEventListener("keydown", e => { if(e.key === "Enter") inp.blur(); });
  });

  // Listener: click en fila TOTAL <FAM> (amarilla) → expande/colapsa los productos de ese grano
  document.querySelectorAll("#pn-tbody .pn-fam-header").forEach(tr => {
    tr.addEventListener("click", (e) => {
      if(e.target.tagName === 'INPUT') return;
      const fam = tr.dataset.fam;
      if(PN_FAM_EXPANDED.has(fam)) PN_FAM_EXPANDED.delete(fam);
      else PN_FAM_EXPANDED.add(fam);
      pnRender();
    });
  });

  // Listener: click en fila SEMILLA <FAM> → expande/colapsa variedades
  document.querySelectorAll("#pn-tbody .pn-semilla-header").forEach(tr => {
    tr.addEventListener("click", (e) => {
      if(e.target.tagName === 'INPUT') return;
      e.stopPropagation();   // no togglear la familia
      const fam = tr.dataset.semFam;
      if(PN_SEM_EXPANDED.has(fam)) PN_SEM_EXPANDED.delete(fam);
      else PN_SEM_EXPANDED.add(fam);
      pnRender();
    });
  });

  // Listener: DRILL-DOWN — click en celda numérica (pend ingreso, entregado, silo bolsa, etc.)
  // despliega el detalle (contratos que faltan entregar / depósitos donde está el grano).
  document.querySelectorAll("#pn-tbody .pn-drill-cell-link").forEach(td => {
    td.addEventListener("click", (e) => {
      e.stopPropagation();   // no togglear la familia
      const tr = td.closest("tr");
      const type = td.dataset.drill;
      const prods = td.dataset.fam ? (byFamilia[td.dataset.fam] || []) : [td.dataset.prod];
      const key = (td.dataset.fam || td.dataset.prod) + "|" + type;
      const nx = tr.nextElementSibling;
      // toggle: si ya está abierto ese mismo detalle, cerrarlo
      if(nx && nx.classList.contains("pn-drill-row") && nx.dataset.key === key){
        nx.remove(); td.classList.remove("pn-drill-active"); return;
      }
      // cerrar cualquier detalle abierto inmediatamente debajo (de otra celda de la misma fila)
      if(nx && nx.classList.contains("pn-drill-row")){
        tr.querySelectorAll(".pn-drill-active").forEach(x=>x.classList.remove("pn-drill-active"));
        nx.remove();
      }
      const nr = document.createElement("tr");
      nr.className = "pn-drill-row"; nr.dataset.key = key;
      nr.innerHTML = `<td colspan="${PN_TOTAL_COLS}" class="pn-drill-cell">${pnDrillHTML(type, prods)}</td>`;
      tr.after(nr);
      td.classList.add("pn-drill-active");
      nr.querySelector(".pn-drill-inner").scrollIntoView({behavior:"smooth", block:"nearest"});
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
   ============  CALCULADORES Canje + Proforma  ================
   ============================================================
   Forms con autocálculo on input. Persisten el último set de valores
   en localStorage por usuario. */

function cnjFmt(n, dec=2){
  if(n==null||isNaN(n)) return "—";
  return Number(n).toLocaleString("es-AR", {minimumFractionDigits:dec, maximumFractionDigits:dec});
}
function cnjFmt0(n){
  if(n==null||isNaN(n)) return "—";
  return Number(n).toLocaleString("es-AR", {maximumFractionDigits:0});
}

/* ----- CALCULADOR DE CANJE ----- */
const CNJ_KEY = "tablero-granos-cnj-v1";
const CNJ_DEFAULTS = {
  deuda:5976.09, precio:308.58, tc:1356.5, iva:10.5,
  com:2, sel:1.25, perc:0, ib:0, liq:100,
};
function cnjGet(){
  const r = {};
  ["deuda","precio","tc","iva","com","sel","perc","ib","liq"].forEach(k => {
    r[k] = parseFloat(document.getElementById("cnj-"+k).value) || 0;
  });
  return r;
}
function cnjSet(v){
  Object.keys(v).forEach(k => {
    const el = document.getElementById("cnj-"+k); if(el) el.value = v[k];
  });
}
function cnjRender(){
  const v = cnjGet();
  // Precio Neto = Precio − Precio×(Comisión + Sellado + PercIVA + IIBB)
  const desc = (v.com + v.sel + v.perc + v.ib) / 100;
  const precioNeto = v.precio * (1 - desc);
  const com$  = v.precio * v.com / 100;
  const sel$  = v.precio * v.sel / 100;
  // % Liquidación: el grano liquida al X% de su valor (típ. 100%). Si es menor,
  // el cliente tiene que entregar MÁS toneladas para cubrir la misma deuda.
  const liqF = (v.liq > 0 ? v.liq/100 : 1);
  const precioLiq = precioNeto * liqF;
  // Toneladas a entregar: Deuda con IVA / Precio Liquidable / (1 + IVA)
  // (Si la deuda viene con IVA y el precio commodity es neto)
  const factorIva = 1 + (v.iva/100);
  const deudaSinIva = v.deuda / factorIva;
  const tn = precioLiq > 0 ? deudaSinIva / precioLiq : 0;
  const kg = tn * 1000;
  const totalARS = v.deuda * v.tc;

  document.getElementById("cnj-out-precio-neto").textContent = cnjFmt(precioNeto, 2) + " USD";
  document.getElementById("cnj-out-precio-liq").textContent = cnjFmt(precioLiq, 2) + " USD";
  document.getElementById("cnj-out-tn").textContent = cnjFmt(tn, 3);
  document.getElementById("cnj-out-kg").textContent = cnjFmt0(kg) + " kg";
  document.getElementById("cnj-out-total-usd").textContent = "USD " + cnjFmt(v.deuda, 2);
  document.getElementById("cnj-out-total-ars").textContent = "$ " + cnjFmt0(totalARS);
  document.getElementById("cnj-out-com").textContent = cnjFmt(com$, 2) + " USD";
  document.getElementById("cnj-out-sel").textContent = cnjFmt(sel$, 2) + " USD";

  try{ localStorage.setItem(CNJ_KEY, JSON.stringify(v)); }catch(e){}
}
(function cnjInit(){
  ["deuda","precio","tc","iva","com","sel","perc","ib","liq"].forEach(k => {
    const el = document.getElementById("cnj-"+k);
    if(el) el.addEventListener("input", cnjRender);
  });
  const rst = document.getElementById("cnj-reset");
  if(rst) rst.addEventListener("click", () => { cnjSet(CNJ_DEFAULTS); cnjRender(); });
  // restore from localStorage
  try{
    const ls = JSON.parse(localStorage.getItem(CNJ_KEY) || "null");
    if(ls && typeof ls === "object") cnjSet(ls);
  } catch(e){}
  cnjRender();
})();

/* ----- CALCULADOR DE PROFORMA ----- */
const PRF_KEY = "tablero-granos-prf-v1";
const PRF_DEFAULTS = {
  tn:282.42, precio:190, tc:1347.5, liq:100,
  com:1.5, "cam-tarifa":22553, "cam-n":8,
  gtos:1.25, iva:12.1,
};
function prfGet(){
  const r = {};
  Object.keys(PRF_DEFAULTS).forEach(k => {
    r[k] = parseFloat(document.getElementById("prf-"+k).value) || 0;
  });
  r.sisa = (document.querySelector('input[name="prf-sisa"]:checked')||{}).value || "none";
  return r;
}
function prfSet(v){
  Object.keys(v).forEach(k => {
    if(k === "sisa") return;
    const el = document.getElementById("prf-"+k); if(el) el.value = v[k];
  });
}
function prfRender(){
  const v = prfGet();
  const precioARS = v.precio * v.tc;
  const total    = v.tn * v.precio * v.tc * (v.liq/100);
  const com      = total * v.com / 100;
  const cam      = v["cam-tarifa"] * v["cam-n"];
  const gtos     = total * v.gtos / 100;
  const factSin  = total - com - cam - gtos;
  const factCon  = factSin * (1 + v.iva/100);
  const eqUsd    = v.tc > 0 ? factCon / v.tc : 0;

  document.getElementById("prf-out-precio-ars").textContent = "$ " + cnjFmt0(precioARS);
  document.getElementById("prf-out-total").textContent = "$ " + cnjFmt0(total);
  document.getElementById("prf-out-com").textContent = "$ " + cnjFmt0(com);
  document.getElementById("prf-out-cam").textContent = "$ " + cnjFmt0(cam);
  document.getElementById("prf-out-gtos").textContent = "$ " + cnjFmt0(gtos);
  document.getElementById("prf-out-fact").textContent = "$ " + cnjFmt0(factSin);
  document.getElementById("prf-out-fact-iva").textContent = "$ " + cnjFmt0(factCon);
  document.getElementById("prf-out-usd").textContent = "USD " + cnjFmt(eqUsd, 2);

  // Régimen SISA
  const box = document.getElementById("prf-sisa-box");
  const ivaRetRow = document.getElementById("prf-sisa-iva-ret");
  const ganRow = document.getElementById("prf-sisa-gan");
  if(v.sisa === "none"){
    box.style.display = "none";
  } else {
    box.style.display = "block";
    const bruto = v.tn * v.precio * v.tc;
    const sCom = bruto * v.com / 100;
    const sGtos = bruto * v.gtos / 100;
    const sub = bruto - sCom - sGtos;
    const conIva = sub * (1 + v.iva/100);
    let total;
    if(v.sisa === "sisa1"){
      const retIva = bruto * 0.05;
      document.getElementById("prf-sisa-titulo").textContent = "Régimen SISA 1 (Ret. IVA 5%)";
      ivaRetRow.style.display = "table-row";
      ivaRetRow.cells[1].textContent = "$ " + cnjFmt0(-retIva);
      ganRow.style.display = "none";
      total = conIva - retIva;
    } else {
      const retIva = bruto * 0.07;
      const retGan = bruto * 0.02;
      document.getElementById("prf-sisa-titulo").textContent = "Régimen SISA 2 (Ret. IVA 7% + Ret. Ganancias 2%)";
      ivaRetRow.style.display = "table-row";
      ivaRetRow.cells[0].textContent = "− Ret. IVA 7%:";
      ivaRetRow.cells[1].textContent = "$ " + cnjFmt0(-retIva);
      ganRow.style.display = "table-row";
      ganRow.cells[1].textContent = "$ " + cnjFmt0(-retGan);
      total = conIva - retIva - retGan;
    }
    document.getElementById("prf-sisa-bruto").textContent = "$ " + cnjFmt0(bruto);
    document.getElementById("prf-sisa-com").textContent = "$ " + cnjFmt0(-sCom);
    document.getElementById("prf-sisa-gtos").textContent = "$ " + cnjFmt0(-sGtos);
    document.getElementById("prf-sisa-sub").textContent = "$ " + cnjFmt0(sub);
    document.getElementById("prf-sisa-iva").textContent = "$ " + cnjFmt0(conIva - sub);
    document.getElementById("prf-sisa-total").textContent = "$ " + cnjFmt0(total);
  }

  const persist = Object.assign({}, v); delete persist.sisa;
  try{ localStorage.setItem(PRF_KEY, JSON.stringify(persist)); }catch(e){}
}
(function prfInit(){
  Object.keys(PRF_DEFAULTS).forEach(k => {
    const el = document.getElementById("prf-"+k);
    if(el) el.addEventListener("input", prfRender);
  });
  document.querySelectorAll('input[name="prf-sisa"]').forEach(r => r.addEventListener("change", prfRender));
  const rst = document.getElementById("prf-reset");
  if(rst) rst.addEventListener("click", () => { prfSet(PRF_DEFAULTS); prfRender(); });
  try{
    const ls = JSON.parse(localStorage.getItem(PRF_KEY) || "null");
    if(ls && typeof ls === "object") prfSet(ls);
  } catch(e){}
  prfRender();
})();


/* ============================================================
   ============  TRAZABILIDAD DE COMPRA  =======================
   ============================================================
   Cada CTG con su Carta de Porte, entregador, contratos compra/venta,
   peso y destinatario. Click en fila → lazy fetch a Finnegans via Worker
   para detalle (COE, liquidacion, comision, factor). */

const TZ_RAW = (PAYLOAD && Array.isArray(PAYLOAD.traza)) ? PAYLOAD.traza : [];
const TZ_COLS = [
  {k:"ctg",              lbl:"CTG",              num:false, w:"130px"},
  {k:"cp",               lbl:"CP",               num:false, w:"130px"},
  {k:"fecha",            lbl:"Fecha",            num:false, w:"95px"},
  {k:"entregador",       lbl:"Entregador",       num:false},
  {k:"producto",         lbl:"Grano",            num:false, w:"140px"},
  {k:"peso_neto",        lbl:"Peso Neto (kg)",   num:true},
  {k:"contrato_compra",  lbl:"Cto Compra",       num:false, w:"110px"},
  {k:"contrato_venta",   lbl:"Cto Venta",        num:false, w:"110px"},
  {k:"cerealera",        lbl:"Cerealera",        num:false},
];
let TZ_SORT_K = "fecha", TZ_SORT_D = -1;
let TZ_EXPANDED = new Set();   // CTGs con detalle expandido

function tzEscape(s){ return String(s||"").replace(/[&<>"']/g, ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch])); }

function tzInitFilters(){
  const uniq = (key) => [...new Set(TZ_RAW.map(r => r[key]).filter(v => v != null && v !== ""))].sort((a,b)=>String(a).localeCompare(String(b),"es"));
  const fillSelect = (id, vals, allLbl="Todos") => {
    const sel = document.getElementById(id);
    if(!sel) return;
    sel.innerHTML = `<option value="">${allLbl}</option>` +
      vals.map(v => `<option value="${tzEscape(v)}">${tzEscape(v)}</option>`).join("");
  };
  fillSelect("tz-ent",  uniq("entregador"));
  fillSelect("tz-cer",  uniq("cerealera"), "Todas");
  fillSelect("tz-prod", uniq("producto"));
  fillSelect("tz-ccomp",uniq("contrato_compra"));
}

function tzParseFecha(s){
  // Finnegans devuelve "dd-mm-yyyy". Convertir a yyyy-mm-dd para comparar.
  if(!s) return "";
  const m = String(s).match(/^(\d{1,2})-(\d{1,2})-(\d{4})$/);
  if(m) return `${m[3]}-${m[2].padStart(2,"0")}-${m[1].padStart(2,"0")}`;
  return s;
}

function tzFiltered(){
  const ent  = document.getElementById("tz-ent").value;
  const cer  = document.getElementById("tz-cer").value;
  const prod = document.getElementById("tz-prod").value;
  const cc   = document.getElementById("tz-ccomp").value;
  const fd   = document.getElementById("tz-fdesde").value;
  const fh   = document.getElementById("tz-fhasta").value;
  const q    = (document.getElementById("tz-q").value||"").toLowerCase().trim();
  return TZ_RAW.filter(r => {
    if(ent  && r.entregador !== ent) return false;
    if(cer  && r.cerealera  !== cer) return false;
    if(prod && r.producto   !== prod) return false;
    if(cc   && r.contrato_compra !== cc) return false;
    const fIso = tzParseFecha(r.fecha);
    if(fd && fIso && fIso < fd) return false;
    if(fh && fIso && fIso > fh) return false;
    if(q){
      const blob = `${r.ctg||""} ${r.cp||""} ${r.contrato_compra||""} ${r.contrato_venta||""} ${r.entregador||""} ${r.cerealera||""}`.toLowerCase();
      if(!blob.includes(q)) return false;
    }
    return true;
  });
}

function tzRenderKpis(rows){
  const totKg = rows.reduce((s,r) => s + (Number(r.peso_neto)||0), 0);
  const entregadores = new Set(rows.map(r => r.entregador).filter(Boolean));
  const cerealeras = new Set(rows.map(r => r.cerealera).filter(Boolean));
  const sinVenta = rows.filter(r => !r.contrato_venta).length;
  document.getElementById("tz-kpis").innerHTML = `
    <div class="kpi"><div class="lbl">CTGs (filtrados)</div><div class="val">${fmt.int(rows.length)}</div><div class="hint">de ${fmt.int(TZ_RAW.length)} totales</div></div>
    <div class="kpi green"><div class="lbl">Total Kg</div><div class="val">${fmt.num(totKg)}</div><div class="hint">${fmt.num(totKg/1000)} tn</div></div>
    <div class="kpi"><div class="lbl">Entregadores</div><div class="val">${fmt.int(entregadores.size)}</div></div>
    <div class="kpi"><div class="lbl">Cerealeras</div><div class="val">${fmt.int(cerealeras.size)}</div></div>
    <div class="kpi orange"><div class="lbl">Sin Cto Venta</div><div class="val">${fmt.int(sinVenta)}</div><div class="hint">aún en depósito o pte. vincular</div></div>
  `;
}

function tzRender(){
  const all = tzFiltered();
  document.getElementById("tz-count").textContent = `${all.length} / ${TZ_RAW.length}`;
  tzRenderKpis(all);

  // Header
  const head = TZ_COLS.map(c => {
    const arr = (TZ_SORT_K === c.k) ? (TZ_SORT_D>0?"▲":"▼") : "";
    const w = c.w ? `style="width:${c.w}"` : "";
    return `<th ${w} class="${c.num?'num':''}" data-sort-tz="${c.k}" style="cursor:pointer">${c.lbl} ${arr}</th>`;
  }).join("");
  document.getElementById("tz-head").innerHTML = `<th style="width:24px"></th>${head}`;

  // Sort
  const col = TZ_COLS.find(c => c.k === TZ_SORT_K);
  const sorted = all.slice().sort((a,b) => {
    let va = a[TZ_SORT_K], vb = b[TZ_SORT_K];
    if(TZ_SORT_K === "fecha"){ va = tzParseFecha(va); vb = tzParseFecha(vb); }
    if(col && col.num){ va = Number(va)||0; vb = Number(vb)||0; return (va-vb)*TZ_SORT_D; }
    va = String(va==null?"":va); vb = String(vb==null?"":vb);
    return va.localeCompare(vb, "es", {numeric:true}) * TZ_SORT_D;
  });

  // Body (limitamos a 1500 filas por performance)
  const body = sorted.slice(0, 400).map(r => {
    const exp = TZ_EXPANDED.has(r.ctg);
    const cells = TZ_COLS.map(c => {
      let v = r[c.k];
      if(c.k === "peso_neto") return `<td class="num">${fmt.num(v)}</td>`;
      if(c.k === "contrato_compra" && v) return `<td><span style="background:#dcfce7;color:#15803d;padding:2px 7px;border-radius:5px;font-size:11px;font-weight:600">${tzEscape(v)}</span></td>`;
      if(c.k === "contrato_venta" && v)  return `<td><span style="background:#dbeafe;color:#1e40af;padding:2px 7px;border-radius:5px;font-size:11px;font-weight:600">${tzEscape(v)}</span></td>`;
      if(c.k === "contrato_venta" && !v) return `<td><span style="color:var(--orange);font-size:11px">— sin venta</span></td>`;
      return `<td>${tzEscape(v||"")}</td>`;
    }).join("");
    let row = `<tr class="tz-row" data-ctg="${tzEscape(r.ctg)}" style="cursor:pointer"><td style="text-align:center;color:var(--blue)">${exp ? "▼" : "▶"}</td>${cells}</tr>`;
    if(exp){
      row += `<tr class="tz-detail" data-ctg-detail="${tzEscape(r.ctg)}"><td colspan="${TZ_COLS.length+1}" style="padding:0;background:#f8fafc"><div class="tz-detail-content" id="tz-det-${tzEscape(r.ctg)}" style="padding:14px 18px;font-size:12.5px">${tzDetailPlaceholder(r)}</div></td></tr>`;
    }
    return row;
  }).join("");
  document.getElementById("tz-body").innerHTML = body || `<tr><td colspan="${TZ_COLS.length+1}" style="text-align:center;padding:30px;color:var(--muted)">Sin CTGs para los filtros aplicados</td></tr>`;

  // Footer: total kg
  const totKg = all.reduce((s,r)=>s+(Number(r.peso_neto)||0), 0);
  document.getElementById("tz-foot").innerHTML = `<tr style="background:#eef2ff;font-weight:700">
    <td></td>
    <td colspan="5">TOTAL · ${all.length} CTGs</td>
    <td class="num">${fmt.num(totKg)}</td>
    <td colspan="3" style="color:var(--muted);font-size:11px;text-align:right">${fmt.num(totKg/1000)} tn</td>
  </tr>`;

  // Sort listeners
  document.querySelectorAll("#tz-head [data-sort-tz]").forEach(th => {
    th.addEventListener("click", () => {
      const k = th.getAttribute("data-sort-tz");
      if(TZ_SORT_K === k) TZ_SORT_D = -TZ_SORT_D;
      else { TZ_SORT_K = k; TZ_SORT_D = -1; }
      tzRender();
    });
  });

  // Click en fila → expandir/colapsar + lazy fetch detalle
  document.querySelectorAll(".tz-row").forEach(tr => {
    tr.addEventListener("click", async () => {
      const ctg = tr.getAttribute("data-ctg");
      if(TZ_EXPANDED.has(ctg)) TZ_EXPANDED.delete(ctg);
      else { TZ_EXPANDED.add(ctg); }
      tzRender();
      if(TZ_EXPANDED.has(ctg)){
        // Lazy fetch del detalle si no esta cacheado
        await tzFetchDetail(ctg);
      }
    });
  });
}

function tzDetailPlaceholder(r){
  const v = (x) => (x==null || x==="") ? "—" : x;
  return `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
      <div style="padding:10px;background:#fff;border-radius:8px;border-left:3px solid #16a34a">
        <div style="font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">🌾 LADO COMPRA (entrada)</div>
        <div><b>Entregador:</b> ${tzEscape(v(r.entregador))}</div>
        <div><b>Contrato:</b> ${tzEscape(v(r.contrato_compra))}</div>
        <div><b>Subtipo:</b> <span style="color:var(--muted);font-size:11.5px">${tzEscape(v(r.subtipo_compra))}</span></div>
        <div><b>Trans. ID:</b> <span style="color:var(--muted);font-size:11.5px">${tzEscape(v(r.transaccion_compra))}</span></div>
      </div>
      <div style="padding:10px;background:#fff;border-radius:8px;border-left:3px solid #3b82f6">
        <div style="font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">📦 LADO VENTA (salida)</div>
        <div><b>Cerealera:</b> ${tzEscape(v(r.cerealera))}</div>
        <div><b>Contrato:</b> ${tzEscape(v(r.contrato_venta))}</div>
        <div><b>Subtipo:</b> <span style="color:var(--muted);font-size:11.5px">${tzEscape(v(r.subtipo_venta))}</span></div>
        <div><b>Trans. ID:</b> <span style="color:var(--muted);font-size:11.5px">${tzEscape(v(r.transaccion_venta))}</span></div>
        <div><b>Destinatario:</b> ${tzEscape(v(r.destinatario))}</div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px">
      <div style="padding:10px;background:#fff;border-radius:8px;border-left:3px solid #94a3b8">
        <div style="font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">⚖️ Pesos</div>
        <div><b>Neto:</b> ${fmt.num(r.peso_neto)} kg</div>
        <div><b>Neto s/mermas:</b> ${fmt.num(r.peso_neto_sin_mermas)} kg</div>
        <div><b>Entregador:</b> ${fmt.num(r.peso_entregador)} kg</div>
        <div><b>Factor:</b> <span style="background:#fef3c7;color:#92400e;padding:1px 6px;border-radius:4px;font-weight:600">${tzEscape(v(r.factor))}</span></div>
      </div>
      <div style="padding:10px;background:#fff;border-radius:8px;border-left:3px solid #84cc16">
        <div style="font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">📑 Certificación</div>
        <div><b>Cert. 1116a:</b> ${tzEscape(v(r.certificado_1116a))}</div>
        <div><b>Comp. 1116a:</b> ${tzEscape(v(r.comprobante_1116a))}</div>
        <div><b>Cert. RT:</b> ${tzEscape(v(r.certificado_rt))}</div>
        <div><b>Comp. RT:</b> ${tzEscape(v(r.comprobante_rt))}</div>
      </div>
      <div style="padding:10px;background:#fff;border-radius:8px;border-left:3px solid #f59e0b">
        <div style="font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">🚛 Logística</div>
        <div><b>Titular CP:</b> ${tzEscape(v(r.titular))}</div>
        <div><b>Representante:</b> ${tzEscape(v(r.representante))}</div>
        <div><b>Pagador Flete:</b> ${tzEscape(v(r.pagador_flete))}</div>
        <div><b>Transportista:</b> ${tzEscape(v(r.transportista))}</div>
        <div><b>Chofer:</b> ${tzEscape(v(r.chofer))}</div>
        <div><b>KM:</b> ${tzEscape(v(r.kilometros))} · <b>Tarifa:</b> ${tzEscape(v(r.tarifa_transporte))} · <b>$:</b> ${tzEscape(v(r.importe_transporte))}</div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
      <div style="padding:10px;background:#fff;border-radius:8px;border-left:3px solid #8b5cf6">
        <div style="font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">📍 Origen → Destino</div>
        <div><b>Establecimiento:</b> ${tzEscape(v(r.establecimiento))}</div>
        <div><b>Origen:</b> ${tzEscape(v(r.localidad_origen))} ${r.provincia_origen?'('+tzEscape(r.provincia_origen)+')':''}</div>
        <div><b>Destino:</b> ${tzEscape(v(r.localidad_destino))} ${r.provincia_destino?'('+tzEscape(r.provincia_destino)+')':''}</div>
        <div><b>Partida:</b> ${tzEscape(v(r.fecha_partida))} ${r.hora_partida?tzEscape(r.hora_partida):''} · <b>Arribo:</b> ${tzEscape(v(r.fecha_arribo))}</div>
        <div><b>Descarga:</b> ${tzEscape(v(r.fecha_descarga))} · <b>Doc CV:</b> ${tzEscape(v(r.documento_cv))}</div>
      </div>
      <div style="padding:10px;background:#fff;border-radius:8px;border-left:3px solid #ec4899">
        <div style="font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">🤝 Corredores · Cosecha</div>
        <div><b>Cosecha:</b> ${tzEscape(v(r.cosecha))}</div>
        <div><b>Corredor primario:</b> ${tzEscape(v(r.corredor_primario))}</div>
        <div><b>Corredor secundario:</b> ${tzEscape(v(r.corredor_secundario))}</div>
        <div><b>Estado CTG:</b> ${tzEscape(v(r.estado_ctg))}</div>
      </div>
    </div>

    <div id="tz-det-coe-${tzEscape(r.ctg)}" style="margin-top:10px;padding:10px;background:#fefce8;border-radius:8px;border-left:3px solid #ca8a04">
      <div style="font-size:10.5px;font-weight:700;color:#854d0e;text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">🔍 Detalle Finnegans (COE + liquidación)</div>
      <div style="color:var(--muted);font-size:12px">⏳ Consultando Finnegans…</div>
    </div>
  `;
}

const TZ_DETAIL_CACHE = {};

async function tzFetchDetail(ctg){
  // Cache local
  if(TZ_DETAIL_CACHE[ctg]){
    tzRenderDetailExtra(ctg, TZ_DETAIL_CACHE[ctg]);
    return;
  }
  if(!API_AVAILABLE){
    tzRenderDetailExtra(ctg, {error: "Sin conexión al Worker (entrá por tablero-agronasaja.workers.dev)"});
    return;
  }
  try{
    const r = await fetch(`/api/finnegans/ctg/${encodeURIComponent(ctg)}`, {credentials:"include"});
    if(r.ok){
      const data = await r.json();
      TZ_DETAIL_CACHE[ctg] = data;
      tzRenderDetailExtra(ctg, data);
    } else if(r.status === 404){
      tzRenderDetailExtra(ctg, {error: "Endpoint no disponible aún. La Fase 2 (lazy fetch) requiere agregar Finnegans secrets al Worker."});
    } else {
      tzRenderDetailExtra(ctg, {error: `HTTP ${r.status}`});
    }
  } catch(e){
    tzRenderDetailExtra(ctg, {error: e.message});
  }
}

// Indice de liquidaciones por COE (rapido O(1) lookup)
const TZ_LIQ_BY_COE = (() => {
  const m = {};
  const liqs = (PAYLOAD && Array.isArray(PAYLOAD.liquidaciones)) ? PAYLOAD.liquidaciones : [];
  liqs.forEach(l => {
    const coe = l.numerocoe || l.numerodocumento;
    if(coe) m[String(coe)] = l;
  });
  const secs = (PAYLOAD && Array.isArray(PAYLOAD.liquidaciones_secu)) ? PAYLOAD.liquidaciones_secu : [];
  secs.forEach(l => {
    const coe = l.numerocoe || l.numerodocumento;
    if(coe && m[String(coe)]){
      m[String(coe)].importegravado = l.importegravado;
      m[String(coe)].importeotros = l.importeotros;
      m[String(coe)].importetotal = l.importetotal;
      m[String(coe)].numerocontratointermediario = l.numerocontratointermediario;
    }
  });
  return m;
})();

// Indice de contratos compra/venta por nombre (resumen_de_contrato_de_compra_de_granos / venta)
const TZ_CTO_COMPRA = (() => {
  const m = {};
  ((PAYLOAD && PAYLOAD.compra) || []).forEach(c => {
    if(c.contrato) m[c.contrato] = c;
    if(c.nombre)   m[c.nombre]   = c;
  });
  return m;
})();
const TZ_CTO_VENTA = (() => {
  const m = {};
  ((PAYLOAD && PAYLOAD.pilot) || []).forEach(c => {
    if(c.contrato) m[c.contrato] = c;
    if(c.nombre)   m[c.nombre]   = c;
  });
  return m;
})();

// Indices Cargill (movements por CTG/coe, invoices por COE, payments por contrato)
// Nota: el legalDocument de Cargill viene como "0000000000461-10132363698"
// donde la parte despues del ultimo "-" es el CTG.
function tzExtractCargillCtg(legalDoc){
  if(!legalDoc) return null;
  const s = String(legalDoc).trim();
  if(s.includes("-")){
    const parts = s.split("-");
    return parts[parts.length - 1].trim();
  }
  return s;
}
const TZ_CARGILL_MOV_BY_CTG = {};
const TZ_CARGILL_MOV_BY_COE = {};
const TZ_CARGILL_MOV_BY_CONTRATO = {};
((PAYLOAD && PAYLOAD.cargill_movements) || []).forEach(m => {
  const ctg = tzExtractCargillCtg(m.legalDocument);
  if(ctg){
    (TZ_CARGILL_MOV_BY_CTG[ctg] = TZ_CARGILL_MOV_BY_CTG[ctg] || []).push(m);
  }
  // Tambien indexar por el legalDocument completo por si acaso
  if(m.legalDocument){
    const k = String(m.legalDocument).trim();
    if(k !== ctg) (TZ_CARGILL_MOV_BY_CTG[k] = TZ_CARGILL_MOV_BY_CTG[k] || []).push(m);
  }
  if(m.coeNumber && String(m.coeNumber).trim()){
    const k = String(m.coeNumber).trim();
    (TZ_CARGILL_MOV_BY_COE[k] = TZ_CARGILL_MOV_BY_COE[k] || []).push(m);
  }
  if(m.contractNumber){
    const k = String(m.contractNumber).trim();
    (TZ_CARGILL_MOV_BY_CONTRATO[k] = TZ_CARGILL_MOV_BY_CONTRATO[k] || []).push(m);
  }
});

const TZ_CARGILL_INV_BY_CONTRATO = {};
const TZ_CARGILL_INV_BY_COE = {};
((PAYLOAD && PAYLOAD.cargill_invoices) || []).forEach(inv => {
  if(inv.contractNumber){
    const k = String(inv.contractNumber).trim();
    (TZ_CARGILL_INV_BY_CONTRATO[k] = TZ_CARGILL_INV_BY_CONTRATO[k] || []).push(inv);
  }
  if(inv.externalDocumentReference){
    const k = String(inv.externalDocumentReference).trim();
    (TZ_CARGILL_INV_BY_COE[k] = TZ_CARGILL_INV_BY_COE[k] || []).push(inv);
  }
});

const TZ_CARGILL_PAY_BY_CONTRATO = {};
((PAYLOAD && PAYLOAD.cargill_payments) || []).forEach(pay => {
  if(pay.contractNumber){
    const k = String(pay.contractNumber).trim();
    (TZ_CARGILL_PAY_BY_CONTRATO[k] = TZ_CARGILL_PAY_BY_CONTRATO[k] || []).push(pay);
  }
});

// Indices LDC: CTGs entregados a LDC desde el DW indexados por numerodocumentoadicional (=CTG)
const TZ_LDC_BY_CTG = {};
((PAYLOAD && PAYLOAD.ldc_ctgs) || []).forEach(c => {
  const ctg = c.numerodocumentoadicional;
  if(!ctg) return;
  const k = String(ctg).trim();
  (TZ_LDC_BY_CTG[k] = TZ_LDC_BY_CTG[k] || []).push(c);
});
// Liquidaciones LDC indexadas por ContractNumber + por settlementID
const TZ_LDC_SETTLE_BY_CONTRATO = {};
((PAYLOAD && PAYLOAD.ldc_settlements) || []).forEach(s => {
  if(s.ContractNumber){
    const k = String(s.ContractNumber).trim();
    (TZ_LDC_SETTLE_BY_CONTRATO[k] = TZ_LDC_SETTLE_BY_CONTRATO[k] || []).push(s);
  }
});
const TZ_LDC_FIX_BY_CONTRATO = {};
((PAYLOAD && PAYLOAD.ldc_fixations) || []).forEach(f => {
  if(f.ContractNumber){
    const k = String(f.ContractNumber).trim();
    (TZ_LDC_FIX_BY_CONTRATO[k] = TZ_LDC_FIX_BY_CONTRATO[k] || []).push(f);
  }
});

// Indice ACA: CTGs entregados a ACA Asoc de Cooperativas desde el DW
const TZ_ACA_BY_CTG = {};
((PAYLOAD && PAYLOAD.aca_ctgs) || []).forEach(c => {
  const ctg = c.numerodocumentoadicional;
  if(!ctg) return;
  const k = String(ctg).trim();
  (TZ_ACA_BY_CTG[k] = TZ_ACA_BY_CTG[k] || []).push(c);
});

// Indice Allaria: CTGs donde Allaria figura como corredor/destino
const TZ_ALLARIA_BY_CTG = {};
((PAYLOAD && PAYLOAD.allaria_ctgs) || []).forEach(c => {
  const ctg = c.numerodocumentoadicional;
  if(!ctg) return;
  const k = String(ctg).trim();
  (TZ_ALLARIA_BY_CTG[k] = TZ_ALLARIA_BY_CTG[k] || []).push(c);
});

// Indice FYO: CTGs entregados a FYO Acopio o con corredor FYO
const TZ_FYO_BY_CTG = {};
((PAYLOAD && PAYLOAD.fyo_ctgs) || []).forEach(c => {
  const ctg = c.numerodocumentoadicional;
  if(!ctg) return;
  const k = String(ctg).trim();
  (TZ_FYO_BY_CTG[k] = TZ_FYO_BY_CTG[k] || []).push(c);
});

// Indice Intagro: CTGs con Intagro como corredor
const TZ_INTAGRO_BY_CTG = {};
((PAYLOAD && PAYLOAD.intagro_ctgs) || []).forEach(c => {
  const ctg = c.numerodocumentoadicional;
  if(!ctg) return;
  const k = String(ctg).trim();
  (TZ_INTAGRO_BY_CTG[k] = TZ_INTAGRO_BY_CTG[k] || []).push(c);
});

// Indice Bunge: CTGs con destino BUNGE ARGENTINA
const TZ_BUNGE_BY_CTG = {};
((PAYLOAD && PAYLOAD.bunge_ctgs) || []).forEach(c => {
  const ctg = c.numerodocumentoadicional;
  if(!ctg) return;
  const k = String(ctg).trim();
  (TZ_BUNGE_BY_CTG[k] = TZ_BUNGE_BY_CTG[k] || []).push(c);
});

// Indice COFCO: CTGs con destino COFCO INTERNATIONAL ARGENTINA
const TZ_COFCO_BY_CTG = {};
((PAYLOAD && PAYLOAD.cofco_ctgs) || []).forEach(c => {
  const ctg = c.numerodocumentoadicional;
  if(!ctg) return;
  const k = String(ctg).trim();
  (TZ_COFCO_BY_CTG[k] = TZ_COFCO_BY_CTG[k] || []).push(c);
});

function tzRenderDetailExtra(ctg, data){
  const el = document.getElementById("tz-det-coe-" + ctg);
  if(!el) return;
  if(data.error){
    el.innerHTML = `<div style="font-size:10.5px;font-weight:700;color:#854d0e;text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">🔍 Detalle Finnegans (COE + liquidación)</div><div style="color:var(--red);font-size:12px">⚠️ ${tzEscape(data.error)}</div>`;
    return;
  }
  const cartaPorteRows = (data.cartaPorte || []);
  // Collect unique COEs from the cartaPorte response
  const coesEncontrados = new Set();
  cartaPorteRows.forEach(r => {
    const coe = r.COE || r.coe;
    if(coe) coesEncontrados.add(String(coe));
  });

  // Buscar las liquidaciones que matchean esos COEs
  const liquidacionesMatched = [...coesEncontrados]
    .map(coe => TZ_LIQ_BY_COE[coe])
    .filter(Boolean);

  const cpRowsHtml = cartaPorteRows.map(r => {
    const coe = r.COE || r.coe || "";
    const liq = coe ? TZ_LIQ_BY_COE[String(coe)] : null;
    const liqBadge = liq ?
      `<span style="background:#dcfce7;color:#15803d;padding:2px 7px;border-radius:5px;font-size:10.5px;font-weight:600">✓ Liquidado</span>` :
      (coe ? `<span style="background:#fee2e2;color:#991b1b;padding:2px 7px;border-radius:5px;font-size:10.5px;font-weight:600">⚠ Con COE sin match</span>` : `<span style="color:var(--muted);font-size:10.5px">— sin COE aún</span>`);
    return `<tr>
      <td style="padding:5px">${tzEscape(r.IDENTIFICACIONEXTERNA || r.IDENTIFICACION || r.identificacion || "")}</td>
      <td style="padding:5px">${tzEscape(r["CARTA DE PORTE"] || r.cartaPorte || "")}</td>
      <td style="padding:5px;font-family:monospace;font-size:11px">${tzEscape(coe || "—")}</td>
      <td style="padding:5px" class="num">${fmt.num(r.PESONETO || r.pesoNeto || 0)}</td>
      <td style="padding:5px">${liqBadge}</td>
    </tr>`;
  }).join("");

  // Bloque de liquidaciones encontradas
  let liqHtml = "";
  if(liquidacionesMatched.length > 0){
    liqHtml = `
      <div style="margin-top:10px;padding:10px;background:#dcfce7;border-radius:8px;border-left:3px solid #15803d">
        <div style="font-size:10.5px;font-weight:700;color:#14532d;text-transform:uppercase;letter-spacing:.3px;margin-bottom:8px">💰 Liquidaciones matched (${liquidacionesMatched.length})</div>
        <table style="width:100%;font-size:11.5px;border-collapse:collapse">
          <thead><tr style="background:#bbf7d0">
            <th style="text-align:left;padding:5px">Documento</th>
            <th style="text-align:left;padding:5px">Tipo</th>
            <th style="text-align:left;padding:5px">Fecha</th>
            <th style="text-align:left;padding:5px">Cerealera</th>
            <th class="num" style="text-align:right;padding:5px">Gravado</th>
            <th class="num" style="text-align:right;padding:5px">Otros</th>
            <th class="num" style="text-align:right;padding:5px">Total</th>
          </tr></thead>
          <tbody>
            ${liquidacionesMatched.map(l => {
              const tipo = (l.transaccionsubtiponombre||"").replace("Liquidación ","").replace(" Venta de Granos","");
              return `<tr>
                <td style="padding:5px">${tzEscape(l.documento||"")}</td>
                <td style="padding:5px"><span style="background:#1e40af;color:#fff;padding:1px 6px;border-radius:4px;font-size:10px">${tzEscape(tipo)}</span> ${l.tipoliquidacion?'<span style="font-size:10px;color:var(--muted)">·'+tzEscape(l.tipoliquidacion)+'</span>':''}</td>
                <td style="padding:5px">${tzEscape(l.fecha||"")}</td>
                <td style="padding:5px">${tzEscape((l.organizacionnombre||"").slice(0,28))}</td>
                <td style="padding:5px" class="num">${l.importegravado!=null?fmt.num(l.importegravado):'—'}</td>
                <td style="padding:5px" class="num">${l.importeotros!=null?fmt.num(l.importeotros):'—'}</td>
                <td style="padding:5px" class="num"><b>${l.importetotal!=null?fmt.num(l.importetotal):'—'}</b></td>
              </tr>`;
            }).join("")}
          </tbody>
        </table>
        <div style="font-size:10.5px;color:#365314;margin-top:6px">💡 Importes solo en liquidaciones secundarias. Las primarias muestran "—".</div>
      </div>
    `;
  } else if(coesEncontrados.size > 0){
    liqHtml = `<div style="margin-top:10px;padding:10px;background:#fef3c7;border-radius:8px;font-size:11.5px;color:#78350f">⚠ Tiene COE(s) <code>${[...coesEncontrados].join(", ")}</code> pero no encontré liquidación matched en DW. Puede ser que se haya emitido hoy y el DW aún no sincronizó.</div>`;
  } else {
    liqHtml = `<div style="margin-top:10px;padding:10px;background:#f1f5f9;border-radius:8px;font-size:11.5px;color:var(--muted)">Sin COE asignado todavía — el CTG aún no se liquidó.</div>`;
  }

  // Info del contrato compra/venta agregada (de PAYLOAD.compra / PAYLOAD.pilot)
  // El CTG conoce los nombres de contrato; los buscamos en el indice.
  // Necesito acceder a la fila TZ_RAW correspondiente al CTG para obtener los nombres
  const tzRow = TZ_RAW.find(r => r.ctg === ctg);
  let contratoHtml = "";
  if(tzRow){
    const cc = tzRow.contrato_compra ? (TZ_CTO_COMPRA[tzRow.contrato_compra] || TZ_CTO_COMPRA[tzRow.contrato_compra.split(" - ")[0] + " - " + tzRow.contrato_compra.split(" - ")[1]]) : null;
    const cv = tzRow.contrato_venta  ? (TZ_CTO_VENTA[tzRow.contrato_venta] || TZ_CTO_VENTA[tzRow.contrato_venta.split(" - ")[0] + " - " + tzRow.contrato_venta.split(" - ")[1]]) : null;
    const ctoBlock = (cto, side, color) => {
      if(!cto) return "";
      const aj = Number(cto.cantidadmax)||0;
      const ent = Number(cto.cantidadentregada)||0;
      const liq = Number(cto.cantidadliquidada)||0;
      const pdtLiq = Number(cto.cantidadpendienteliquidar)||(ent-liq);
      const importeLiq = Number(cto.importeliquidado)||0;
      const importePdt = Number(cto.importependienteliquidar)||0;
      const precFij = Number(cto.preciopromediofijado)||0;
      const precLiq = Number(cto.precioliquidado)||0;
      const moneda = cto.moneda || "";
      const pctLiq = aj>0 ? (liq/aj*100).toFixed(1) : "0.0";
      return `
        <div style="padding:10px;background:#fff;border-radius:8px;border-left:3px solid ${color}">
          <div style="font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">📋 Contrato ${side}: ${tzEscape(cto.contrato||cto.nombre)}</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px">
            <div><b>Ajustadas:</b> ${fmt.num(aj)} tn</div>
            <div><b>Entregadas:</b> ${fmt.num(ent)} tn</div>
            <div><b>Liquidadas:</b> <span style="color:#15803d"><b>${fmt.num(liq)} tn (${pctLiq}%)</b></span></div>
            <div><b>Pdte. Liquidar:</b> <span style="color:#dc2626"><b>${fmt.num(pdtLiq)} tn</b></span></div>
            <div><b>Precio Fij.:</b> ${precFij ? fmt.num2(precFij) : '—'} ${tzEscape(moneda)}</div>
            <div><b>Precio Liq.:</b> ${precLiq ? fmt.num2(precLiq) : '—'} ${tzEscape(moneda)}</div>
            <div><b>Importe Liq.:</b> ${fmt.num(importeLiq)} ${tzEscape(moneda)}</div>
            <div><b>Importe Pdte.:</b> ${fmt.num(importePdt)} ${tzEscape(moneda)}</div>
          </div>
          ${cto.tipocontrato ? `<div style="font-size:11px;color:var(--muted);margin-top:6px">${tzEscape(cto.tipocontrato)} · ${tzEscape(cto.campana||cto.cosecha||'')}</div>` : ''}
        </div>
      `;
    };
    const cbCompra = ctoBlock(cc, "COMPRA", "#16a34a");
    const cbVenta  = ctoBlock(cv, "VENTA", "#3b82f6");
    if(cbCompra || cbVenta){
      contratoHtml = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">${cbCompra}${cbVenta}</div>`;
    }
  }

  // Bloque CARGILL: matchear este CTG con la data scrapeada de Cargill GPS
  // Estrategias de match: 1) por CTG (legalDocument), 2) por COE, 3) por contrato venta
  let cargillHtml = "";
  if(tzRow){
    let cargMovs = [];
    // Match por CTG
    if(TZ_CARGILL_MOV_BY_CTG[ctg]) cargMovs = TZ_CARGILL_MOV_BY_CTG[ctg];
    // Match por COE encontrado en cartaPorte
    if(!cargMovs.length){
      for(const coe of coesEncontrados){
        if(TZ_CARGILL_MOV_BY_COE[coe]){
          cargMovs = TZ_CARGILL_MOV_BY_COE[coe]; break;
        }
      }
    }
    // Match por contrato venta (puede tener muchos movements; tomamos solo los relevantes al CTG si los hay)
    if(!cargMovs.length && tzRow.contrato_venta){
      // Intentar diferentes formas del contrato venta (Finnegans usa "CTO-VTA-GRA - 836", Cargill usa el numero externo)
      const vcLong = tzRow.contrato_venta;
      const vcShort = vcLong.replace(/[^0-9]/g,"");
      cargMovs = (TZ_CARGILL_MOV_BY_CONTRATO[vcLong] || TZ_CARGILL_MOV_BY_CONTRATO[vcShort] || []);
    }

    if(cargMovs.length){
      const mainMov = cargMovs[0];
      // Buscar invoices y payments por contrato Cargill
      const cargContract = mainMov.contractNumber;
      const invs = cargContract ? (TZ_CARGILL_INV_BY_CONTRATO[String(cargContract).trim()] || []) : [];
      const pays = cargContract ? (TZ_CARGILL_PAY_BY_CONTRATO[String(cargContract).trim()] || []) : [];

      // Detalle (qualityAnalysis + services) si esta disponible
      const cargDetails = (PAYLOAD && PAYLOAD.cargill_details) || {};

      // Sumar descuentos
      const totDisc = cargMovs.reduce((s,m)=>s+(Number(m.totalDiscount)||0), 0);
      const totNeto = cargMovs.reduce((s,m)=>s+(Number(m.netWeight)||0), 0);
      const totBruto = cargMovs.reduce((s,m)=>s+(Number(m.grossWeight)||0), 0);

      // Generar tabla de descargas. Si hay detalle disponible (qualityAnalysis), usar humedad real del detalle
      const movRows = cargMovs.slice(0, 10).map(m => {
        const det = cargDetails[m.movementNumber];
        // Sacar humedad del qualityAnalysis del detalle (si esta)
        let humedadReal = m.humedad;
        if(det && det.qualityAnalysis){
          const hum = det.qualityAnalysis.find(a => (a.analysisType||"").toUpperCase().includes("HUMEDAD"));
          if(hum) humedadReal = parseFloat(String(hum.valueCargill||"").replace(",",".")) || humedadReal;
        }
        // Quality string como fallback (formato "12,70%")
        if(!humedadReal && m.quality){
          const match = String(m.quality).match(/[\d,]+/);
          if(match) humedadReal = parseFloat(match[0].replace(",",".")) || null;
        }
        return `<tr>
          <td style="padding:4px;font-family:monospace;font-size:10.5px">${tzEscape(m.movementNumber||"")}</td>
          <td style="padding:4px">${tzEscape(m.deliveryDate||"")}</td>
          <td style="padding:4px;font-family:monospace">${tzEscape(tzExtractCargillCtg(m.legalDocument)||"")}</td>
          <td style="padding:4px;font-family:monospace">${tzEscape(m.coeNumber||"").trim()||"—"}</td>
          <td style="padding:4px" class="num">${fmt.num(m.grossWeight)}</td>
          <td style="padding:4px" class="num">${fmt.num(m.tareWeight)}</td>
          <td style="padding:4px" class="num"><b>${fmt.num(m.netWeight)}</b></td>
          <td style="padding:4px" class="num">${humedadReal?fmt.num2(humedadReal)+'%':'—'}</td>
          <td style="padding:4px" class="num" style="color:#b45309">${fmt.num(m.totalDiscount)}</td>
        </tr>`;
      }).join("");

      // Detalle inline por cada movement con detalle bajado
      const movsConDetail = cargMovs.filter(m => cargDetails[m.movementNumber]);
      let detailHtml = "";
      if(movsConDetail.length){
        // Agregar TOTALES sumarizando descuentos kg + gastos comerciales totales
        let totalDescuentoKg = 0;
        let serviciosResumen = {};  // serviceName -> { count, totalUnitPrice (acumulado), currency }
        movsConDetail.forEach(m => {
          const det = cargDetails[m.movementNumber];
          (det.qualityAnalysis||[]).forEach(a => {
            totalDescuentoKg += (parseFloat(a.discount)||0);
          });
          (det.services||[]).forEach(s => {
            const k = (s.serviceName||"OTRO").trim();
            const up = parseFloat(String(s.unitPrice||"0").replace(",",".")) || 0;
            const cur = s.billingCurrency || "USD";
            if(!serviciosResumen[k]) serviciosResumen[k] = { count:0, totalUp:0, cur };
            serviciosResumen[k].count++;
            serviciosResumen[k].totalUp += up;
          });
        });

        // KPIs Resumen Análisis + Servicios
        const totalSvcEntries = Object.keys(serviciosResumen).length;
        const kpisDetail = `
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px">
            <div style="background:#dcfce7;padding:8px;border-radius:6px;border-left:3px solid #16a34a">
              <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Mov. c/ Detalle</div>
              <div style="font-size:15px;font-weight:700">${movsConDetail.length} / ${cargMovs.length}</div>
            </div>
            <div style="background:#fee2e2;padding:8px;border-radius:6px;border-left:3px solid #dc2626">
              <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Descuento kg (calidad)</div>
              <div style="font-size:15px;font-weight:700;color:#dc2626">${fmt.num(totalDescuentoKg)} kg</div>
            </div>
            <div style="background:#dbeafe;padding:8px;border-radius:6px;border-left:3px solid #3b82f6">
              <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Tipos de servicio</div>
              <div style="font-size:15px;font-weight:700">${totalSvcEntries}</div>
            </div>
          </div>`;

        // Render detalle per movement (collapsible)
        const detailBlocks = movsConDetail.slice(0, 20).map(m => {
          const det = cargDetails[m.movementNumber];
          const qa = det.qualityAnalysis || [];
          const svc = det.services || [];
          const qaWithValues = qa.filter(a => {
            const v = parseFloat(String(a.valueCargill||"").replace(",",".")) || 0;
            return v !== 0 || (parseFloat(a.discount)||0) > 0;
          });
          const qaRows = qaWithValues.map(a => {
            const v = parseFloat(String(a.valueCargill||"").replace(",",".")) || 0;
            const factor = parseFloat(a.cargillQualityDescription) || 0;
            return `<tr>
              <td style="padding:3px 5px">${tzEscape(a.analysisType||"")}</td>
              <td style="padding:3px 5px" class="num"><b>${fmt.num2(v)}</b> ${tzEscape(a.analysisUnit||"%")}</td>
              <td style="padding:3px 5px" class="num" style="color:${factor>0?'#b45309':'var(--muted)'}">${factor||""}</td>
              <td style="padding:3px 5px" class="num" style="color:${a.discount>0?'#dc2626':'var(--muted)'}"><b>${a.discount||""}</b></td>
            </tr>`;
          }).join("");
          const svcRows = svc.map(s => `<tr>
            <td style="padding:3px 5px">${tzEscape(s.serviceName||"")}</td>
            <td style="padding:3px 5px" class="num"><b>${tzEscape(s.unitPrice||"")}</b> ${tzEscape(s.billingCurrency||"USD")}</td>
            <td style="padding:3px 5px" style="font-size:10.5px;color:var(--muted)">${tzEscape(s.calculationType||"")}</td>
            <td style="padding:3px 5px">${tzEscape(s.invoiceCarrierName||s.serviceCommodityCode||"—")}</td>
          </tr>`).join("");

          return `<details style="margin-top:8px;padding:8px;background:#fff;border-radius:6px;border-left:2px solid #16a34a">
            <summary style="cursor:pointer;font-size:11.5px;font-weight:700;color:#15803d">
              🧪 ${tzEscape(m.movementNumber)} — ${tzEscape(m.deliveryDate||"")} · ${tzEscape(tzExtractCargillCtg(m.legalDocument)||"")}
              ${det.cargillAnalysisId ? ` · Anal#${tzEscape(det.cargillAnalysisId)}` : ''}
              ${det.cargillAgency ? ` · ${tzEscape(det.cargillAgency)}` : ''}
            </summary>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:6px">
              <div>
                <div style="font-size:10px;font-weight:700;color:#15803d;text-transform:uppercase;margin-bottom:3px">Análisis de Calidad</div>
                ${qaRows ? `<table style="width:100%;font-size:10.5px;border-collapse:collapse;background:#f0fdf4">
                  <thead><tr style="background:#dcfce7">
                    <th style="text-align:left;padding:3px 5px">Tipo</th>
                    <th class="num" style="text-align:right;padding:3px 5px">Valor</th>
                    <th class="num" style="text-align:right;padding:3px 5px">Factor</th>
                    <th class="num" style="text-align:right;padding:3px 5px">Desc kg</th>
                  </tr></thead>
                  <tbody>${qaRows}</tbody>
                </table>` : '<div style="font-size:10.5px;color:var(--muted)">sin análisis</div>'}
              </div>
              <div>
                <div style="font-size:10px;font-weight:700;color:#1e40af;text-transform:uppercase;margin-bottom:3px">Servicios / Gastos</div>
                ${svcRows ? `<table style="width:100%;font-size:10.5px;border-collapse:collapse;background:#eff6ff">
                  <thead><tr style="background:#dbeafe">
                    <th style="text-align:left;padding:3px 5px">Servicio</th>
                    <th class="num" style="text-align:right;padding:3px 5px">Precio Unit.</th>
                    <th class="num" style="text-align:right;padding:3px 5px">Cálculo</th>
                    <th style="text-align:left;padding:3px 5px">Carrier</th>
                  </tr></thead>
                  <tbody>${svcRows}</tbody>
                </table>` : '<div style="font-size:10.5px;color:var(--muted)">sin servicios</div>'}
              </div>
            </div>
          </details>`;
        }).join("");

        detailHtml = `${kpisDetail}
          <details open style="margin-top:6px">
            <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#7c2d12;text-transform:uppercase">🧪 Análisis de Calidad + Servicios por Mov (${movsConDetail.length})</summary>
            ${detailBlocks}
            ${movsConDetail.length > 20 ? `<div style="font-size:10.5px;color:#7c2d12;margin-top:4px">... y ${movsConDetail.length - 20} más</div>` : ''}
          </details>`;
      } else {
        detailHtml = `<div style="margin-top:8px;padding:8px;background:#fff7ed;border-radius:6px;font-size:11.5px;color:#9a3412">
          💡 Detalle completo (análisis de calidad + servicios) aún no descargado para estos movements.
          Correr <code>py scripts/cargill_download_details.py</code>.
        </div>`;
      }

      const invRows = invs.slice(0, 10).map(inv => `<tr>
        <td style="padding:4px">${tzEscape(inv.invoiceTypeCode||"")}</td>
        <td style="padding:4px">${tzEscape(inv.invoiceNumber||"")}</td>
        <td style="padding:4px">${tzEscape(inv.documentCreationDate||"")}</td>
        <td style="padding:4px" class="num">${fmt.num(inv.commodityQuantity)}</td>
        <td style="padding:4px" class="num">${fmt.num2(inv.unitPrice)}</td>
        <td style="padding:4px" class="num"><b>${fmt.num(inv.amountLocalCurrency)}</b> ${tzEscape(inv.localCurrencyCode||"")}</td>
        <td style="padding:4px">${tzEscape(inv.externalDocumentReference||"")}</td>
      </tr>`).join("");

      const payRows = pays.slice(0, 10).map(p => `<tr>
        <td style="padding:4px">${tzEscape(p.documentNumber||"")}</td>
        <td style="padding:4px">${tzEscape(p.paymentDate||"")}</td>
        <td style="padding:4px">${tzEscape(p.paymentType||"")}</td>
        <td style="padding:4px" class="num">${fmt.num(p.amountLocalCurrency)} ${tzEscape(p.localCurrency||"")}</td>
        <td style="padding:4px" class="num">${fmt.num(p.netPaymentAmount)}</td>
        <td style="padding:4px">${tzEscape(p.status||"")}</td>
        <td style="padding:4px">${tzEscape(p.voucherNumber||"")}</td>
      </tr>`).join("");

      cargillHtml = `
        <div style="margin-top:14px;border-radius:10px;background:linear-gradient(135deg,#fff7ed,#fef3c7);border-left:4px solid #ea580c;padding:14px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <span style="font-size:11px;font-weight:700;color:#7c2d12;text-transform:uppercase;letter-spacing:.3px">🏢 Cargill GPS (scraped de mycargill.com)</span>
            <span style="background:#ea580c;color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;font-weight:600">${cargMovs.length} mov · ${invs.length} fc · ${pays.length} pagos</span>
            ${cargContract ? `<span style="font-size:11px;color:#7c2d12">Contrato Cargill: <b>${tzEscape(cargContract)}</b></span>` : ''}
          </div>

          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px">
            <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #16a34a">
              <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Bruto</div>
              <div style="font-size:15px;font-weight:700">${fmt.num(totBruto)} kg</div>
            </div>
            <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #3b82f6">
              <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Neto</div>
              <div style="font-size:15px;font-weight:700">${fmt.num(totNeto)} kg</div>
            </div>
            <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #b45309">
              <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Descuentos Total</div>
              <div style="font-size:15px;font-weight:700;color:#b45309">${fmt.num(totDisc)} kg</div>
            </div>
            <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #6366f1">
              <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Producto</div>
              <div style="font-size:13px;font-weight:600">${tzEscape(mainMov.productName||"")}</div>
            </div>
          </div>

          <details open style="margin-bottom:8px">
            <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#7c2d12;text-transform:uppercase">📦 Descargas (${cargMovs.length})</summary>
            <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
              <thead><tr style="background:#fed7aa">
                <th style="text-align:left;padding:5px">Mov</th><th style="text-align:left;padding:5px">Fecha</th>
                <th style="text-align:left;padding:5px">CTG</th><th style="text-align:left;padding:5px">COE</th>
                <th class="num" style="text-align:right;padding:5px">Bruto kg</th>
                <th class="num" style="text-align:right;padding:5px">Tara kg</th>
                <th class="num" style="text-align:right;padding:5px">Neto kg</th>
                <th class="num" style="text-align:right;padding:5px">Humedad</th>
                <th class="num" style="text-align:right;padding:5px">Descuento</th>
              </tr></thead>
              <tbody>${movRows}</tbody>
            </table>
            ${cargMovs.length > 10 ? `<div style="font-size:10.5px;color:#7c2d12;margin-top:4px">... y ${cargMovs.length - 10} más</div>` : ''}
            ${detailHtml}
          </details>

          ${invs.length ? `<details style="margin-bottom:8px">
            <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#7c2d12;text-transform:uppercase">📄 Facturas / Liquidaciones (${invs.length})</summary>
            <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
              <thead><tr style="background:#fed7aa">
                <th style="text-align:left;padding:5px">Tipo</th><th style="text-align:left;padding:5px">N°</th>
                <th style="text-align:left;padding:5px">Fecha</th>
                <th class="num" style="text-align:right;padding:5px">Cant.</th>
                <th class="num" style="text-align:right;padding:5px">Precio U.</th>
                <th class="num" style="text-align:right;padding:5px">Total</th>
                <th style="text-align:left;padding:5px">Ref. Ext.</th>
              </tr></thead>
              <tbody>${invRows}</tbody>
            </table>
            ${invs.length > 10 ? `<div style="font-size:10.5px;color:#7c2d12;margin-top:4px">... y ${invs.length - 10} más</div>` : ''}
          </details>` : ''}

          ${pays.length ? `<details>
            <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#7c2d12;text-transform:uppercase">💸 Pagos (${pays.length})</summary>
            <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
              <thead><tr style="background:#fed7aa">
                <th style="text-align:left;padding:5px">N°</th><th style="text-align:left;padding:5px">Fecha</th>
                <th style="text-align:left;padding:5px">Tipo</th>
                <th class="num" style="text-align:right;padding:5px">Importe</th>
                <th class="num" style="text-align:right;padding:5px">Neto</th>
                <th style="text-align:left;padding:5px">Estado</th>
                <th style="text-align:left;padding:5px">Voucher</th>
              </tr></thead>
              <tbody>${payRows}</tbody>
            </table>
          </details>` : ''}
        </div>
      `;
    }
  }

  // BLOQUE LDC: aparece si el CTG fue entregado a LDC (registro en DW)
  let ldcHtml = "";
  const ldcDeliveries = TZ_LDC_BY_CTG[String(ctg).trim()] || [];
  if(ldcDeliveries.length){
    const ldcRows = ldcDeliveries.map(d => `<tr>
      <td style="padding:4px">${tzEscape(d.documento||"")}</td>
      <td style="padding:4px">${tzEscape(d.fechadescarga||d.fechaarribo||d.fecha||"")}</td>
      <td style="padding:4px">${tzEscape(d.organizacionnombre||"")}</td>
      <td style="padding:4px;font-size:10.5px">${tzEscape(d.representante||"")}</td>
      <td style="padding:4px" class="num"><b>${fmt.num(d.pesoneto)}</b></td>
      <td style="padding:4px" class="num">${fmt.num(d.pesoentregador)}</td>
      <td style="padding:4px">${tzEscape(d.localidadorigen||"")}→${tzEscape(d.localidaddestino||"")}</td>
    </tr>`).join("");
    const totalPesoNeto = ldcDeliveries.reduce((s,d)=>s+(Number(d.pesoneto)||0),0);
    const totalPesoEntr = ldcDeliveries.reduce((s,d)=>s+(Number(d.pesoentregador)||0),0);
    const cosechas = [...new Set(ldcDeliveries.map(d => d.cosecha).filter(Boolean))].join(", ");
    const granos = [...new Set(ldcDeliveries.map(d => d.grano).filter(Boolean))].join(", ");

    ldcHtml = `
      <div style="margin-top:14px;border-radius:10px;background:linear-gradient(135deg,#eff6ff,#dbeafe);border-left:4px solid #1d4ed8;padding:14px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;color:#1e3a8a;text-transform:uppercase;letter-spacing:.3px">🌾 LDC (Louis Dreyfus) — entregado</span>
          <span style="background:#1d4ed8;color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;font-weight:600">${ldcDeliveries.length} traslado${ldcDeliveries.length>1?'s':''}</span>
          ${granos ? `<span style="font-size:11px;color:#1e3a8a">Grano: <b>${tzEscape(granos)}</b></span>` : ''}
          ${cosechas ? `<span style="font-size:11px;color:#1e3a8a">Cosecha: <b>${tzEscape(cosechas)}</b></span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px">
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #16a34a">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Neto Total</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoNeto)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #3b82f6">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Entregador</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoEntr)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #f59e0b">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Diferencia (merma)</div>
            <div style="font-size:15px;font-weight:700;color:${(totalPesoEntr-totalPesoNeto)>0?'#dc2626':'#16a34a'}">${fmt.num(totalPesoEntr-totalPesoNeto)} kg</div>
          </div>
        </div>
        <details open style="margin-bottom:8px">
          <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#1e3a8a;text-transform:uppercase">📦 Traslados a LDC (${ldcDeliveries.length})</summary>
          <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
            <thead><tr style="background:#bfdbfe">
              <th style="text-align:left;padding:5px">Documento</th>
              <th style="text-align:left;padding:5px">Fecha</th>
              <th style="text-align:left;padding:5px">Destino</th>
              <th style="text-align:left;padding:5px">Representante</th>
              <th class="num" style="text-align:right;padding:5px">Peso Neto kg</th>
              <th class="num" style="text-align:right;padding:5px">Entregador kg</th>
              <th style="text-align:left;padding:5px">Origen → Destino</th>
            </tr></thead>
            <tbody>${ldcRows}</tbody>
          </table>
        </details>
        <div style="font-size:10.5px;color:#1e3a8a;background:#dbeafe;padding:6px 8px;border-radius:6px">
          ℹ️ El portal LDC (mildc.com) no expone aplicaciones/análisis por CTG a nivel cliente — sólo Liquidaciones por contrato (ver solapa <b>LDC</b>).
        </div>
      </div>`;
  }

  // BLOQUE ACA (verde) — datos del DW, no del portal (cuenta agronasaja sin atributos)
  let acaHtml = "";
  const acaDeliveries = TZ_ACA_BY_CTG[String(ctg).trim()] || [];
  if(acaDeliveries.length){
    const acaRows = acaDeliveries.map(d => `<tr>
      <td style="padding:4px">${tzEscape(d.documento||"")}</td>
      <td style="padding:4px">${tzEscape(d.fechadescarga||d.fechaarribo||d.fecha||"")}</td>
      <td style="padding:4px">${tzEscape(d.organizacionnombre||d.destino||"")}</td>
      <td style="padding:4px;font-size:10.5px">${tzEscape(d.representante||"")}</td>
      <td style="padding:4px" class="num"><b>${fmt.num(d.pesoneto)}</b></td>
      <td style="padding:4px" class="num">${fmt.num(d.pesoentregador)}</td>
      <td style="padding:4px">${tzEscape(d.localidadorigen||"")}→${tzEscape(d.localidaddestino||"")}</td>
    </tr>`).join("");
    const totalPesoNeto = acaDeliveries.reduce((s,d)=>s+(Number(d.pesoneto)||0),0);
    const totalPesoEntr = acaDeliveries.reduce((s,d)=>s+(Number(d.pesoentregador)||0),0);
    const cosechas = [...new Set(acaDeliveries.map(d => d.cosecha).filter(Boolean))].join(", ");
    const granos = [...new Set(acaDeliveries.map(d => d.grano).filter(Boolean))].join(", ");

    acaHtml = `
      <div style="margin-top:14px;border-radius:10px;background:linear-gradient(135deg,#f0fdf4,#dcfce7);border-left:4px solid #16a34a;padding:14px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;color:#14532d;text-transform:uppercase;letter-spacing:.3px">🌾 ACA (Asoc Cooperativas Argentinas) — entregado</span>
          <span style="background:#16a34a;color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;font-weight:600">${acaDeliveries.length} traslado${acaDeliveries.length>1?'s':''}</span>
          ${granos ? `<span style="font-size:11px;color:#14532d">Grano: <b>${tzEscape(granos)}</b></span>` : ''}
          ${cosechas ? `<span style="font-size:11px;color:#14532d">Cosecha: <b>${tzEscape(cosechas)}</b></span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px">
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #16a34a">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Neto Total</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoNeto)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #3b82f6">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Entregador</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoEntr)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #f59e0b">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Merma</div>
            <div style="font-size:15px;font-weight:700;color:${(totalPesoEntr-totalPesoNeto)>0?'#dc2626':'#16a34a'}">${fmt.num(totalPesoEntr-totalPesoNeto)} kg</div>
          </div>
        </div>
        <details open style="margin-bottom:8px">
          <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#14532d;text-transform:uppercase">📦 Traslados a ACA (${acaDeliveries.length})</summary>
          <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
            <thead><tr style="background:#bbf7d0">
              <th style="text-align:left;padding:5px">Documento</th>
              <th style="text-align:left;padding:5px">Fecha</th>
              <th style="text-align:left;padding:5px">Destino</th>
              <th style="text-align:left;padding:5px">Representante</th>
              <th class="num" style="text-align:right;padding:5px">Peso Neto kg</th>
              <th class="num" style="text-align:right;padding:5px">Entregador kg</th>
              <th style="text-align:left;padding:5px">Origen → Destino</th>
            </tr></thead>
            <tbody>${acaRows}</tbody>
          </table>
        </details>
        <div style="font-size:10.5px;color:#14532d;background:#dcfce7;padding:6px 8px;border-radius:6px">
          ℹ️ Cuenta 'agronasaja' en acabase.com.ar tiene permisos de mercados/pizarra solamente — sin acceso a movimientos/liquidaciones del cliente.
        </div>
      </div>`;
  }

  // BLOQUE COFCO (cyan) — datos DW: CTGs con destino COFCO INTERNATIONAL
  let cofcoHtml = "";
  const cofcoRows = TZ_COFCO_BY_CTG[String(ctg).trim()] || [];
  if(cofcoRows.length){
    const cRows = cofcoRows.map(d => `<tr>
      <td style="padding:4px">${tzEscape(d.documento||"")}</td>
      <td style="padding:4px">${tzEscape(d.fechadescarga||d.fechaarribo||d.fecha||"")}</td>
      <td style="padding:4px">${tzEscape(d.destino||d.organizacionnombre||"")}</td>
      <td style="padding:4px;font-size:10.5px">${tzEscape(d.representante||"")}</td>
      <td style="padding:4px" class="num"><b>${fmt.num(d.pesoneto)}</b></td>
      <td style="padding:4px" class="num">${fmt.num(d.pesoentregador)}</td>
      <td style="padding:4px">${tzEscape(d.localidadorigen||"")}→${tzEscape(d.localidaddestino||"")}</td>
    </tr>`).join("");
    const totalPesoNeto = cofcoRows.reduce((s,d)=>s+(Number(d.pesoneto)||0),0);
    const totalPesoEntr = cofcoRows.reduce((s,d)=>s+(Number(d.pesoentregador)||0),0);
    const granos = [...new Set(cofcoRows.map(d => d.grano).filter(Boolean))].join(", ");

    cofcoHtml = `
      <div style="margin-top:14px;border-radius:10px;background:linear-gradient(135deg,#ecfeff,#a5f3fc);border-left:4px solid #0891b2;padding:14px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;color:#155e75;text-transform:uppercase;letter-spacing:.3px">🌾 COFCO Intl Argentina — entregado</span>
          <span style="background:#0891b2;color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;font-weight:600">${cofcoRows.length} traslado${cofcoRows.length>1?'s':''}</span>
          ${granos ? `<span style="font-size:11px;color:#155e75">Grano: <b>${tzEscape(granos)}</b></span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px">
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #16a34a">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Neto</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoNeto)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #3b82f6">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Entregador</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoEntr)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #f59e0b">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Merma</div>
            <div style="font-size:15px;font-weight:700;color:${(totalPesoEntr-totalPesoNeto)>0?'#dc2626':'#16a34a'}">${fmt.num(totalPesoEntr-totalPesoNeto)} kg</div>
          </div>
        </div>
        <details open style="margin-bottom:8px">
          <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#155e75;text-transform:uppercase">📦 Traslados a COFCO (${cofcoRows.length})</summary>
          <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
            <thead><tr style="background:#a5f3fc">
              <th style="text-align:left;padding:5px">Documento</th>
              <th style="text-align:left;padding:5px">Fecha</th>
              <th style="text-align:left;padding:5px">Destino</th>
              <th style="text-align:left;padding:5px">Representante</th>
              <th class="num" style="text-align:right;padding:5px">Peso Neto</th>
              <th class="num" style="text-align:right;padding:5px">Entregador</th>
              <th style="text-align:left;padding:5px">Origen → Destino</th>
            </tr></thead>
            <tbody>${cRows}</tbody>
          </table>
        </details>
        <div style="font-size:10.5px;color:#155e75;background:#a5f3fc;padding:6px 8px;border-radius:6px">
          ℹ️ Portal SAP Build Work Zone — pendiente login (a retomar después).
        </div>
      </div>`;
  }

  // BLOQUE BUNGE (indigo) — datos DW: CTGs con destino BUNGE ARGENTINA
  let bungeHtml = "";
  const bungeRows = TZ_BUNGE_BY_CTG[String(ctg).trim()] || [];
  if(bungeRows.length){
    const bRows = bungeRows.map(d => `<tr>
      <td style="padding:4px">${tzEscape(d.documento||"")}</td>
      <td style="padding:4px">${tzEscape(d.fechadescarga||d.fechaarribo||d.fecha||"")}</td>
      <td style="padding:4px">${tzEscape(d.destino||d.organizacionnombre||"")}</td>
      <td style="padding:4px;font-size:10.5px">${tzEscape(d.representante||"")}</td>
      <td style="padding:4px" class="num"><b>${fmt.num(d.pesoneto)}</b></td>
      <td style="padding:4px" class="num">${fmt.num(d.pesoentregador)}</td>
      <td style="padding:4px">${tzEscape(d.localidadorigen||"")}→${tzEscape(d.localidaddestino||"")}</td>
    </tr>`).join("");
    const totalPesoNeto = bungeRows.reduce((s,d)=>s+(Number(d.pesoneto)||0),0);
    const totalPesoEntr = bungeRows.reduce((s,d)=>s+(Number(d.pesoentregador)||0),0);
    const granos = [...new Set(bungeRows.map(d => d.grano).filter(Boolean))].join(", ");

    bungeHtml = `
      <div style="margin-top:14px;border-radius:10px;background:linear-gradient(135deg,#eef2ff,#c7d2fe);border-left:4px solid #4338ca;padding:14px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;color:#312e81;text-transform:uppercase;letter-spacing:.3px">🌾 BUNGE Argentina — entregado</span>
          <span style="background:#4338ca;color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;font-weight:600">${bungeRows.length} traslado${bungeRows.length>1?'s':''}</span>
          ${granos ? `<span style="font-size:11px;color:#312e81">Grano: <b>${tzEscape(granos)}</b></span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px">
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #16a34a">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Neto</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoNeto)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #3b82f6">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Entregador</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoEntr)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #f59e0b">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Merma</div>
            <div style="font-size:15px;font-weight:700;color:${(totalPesoEntr-totalPesoNeto)>0?'#dc2626':'#16a34a'}">${fmt.num(totalPesoEntr-totalPesoNeto)} kg</div>
          </div>
        </div>
        <details open style="margin-bottom:8px">
          <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#312e81;text-transform:uppercase">📦 Traslados a Bunge (${bungeRows.length})</summary>
          <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
            <thead><tr style="background:#c7d2fe">
              <th style="text-align:left;padding:5px">Documento</th>
              <th style="text-align:left;padding:5px">Fecha</th>
              <th style="text-align:left;padding:5px">Destino</th>
              <th style="text-align:left;padding:5px">Representante</th>
              <th class="num" style="text-align:right;padding:5px">Peso Neto</th>
              <th class="num" style="text-align:right;padding:5px">Entregador</th>
              <th style="text-align:left;padding:5px">Origen → Destino</th>
            </tr></thead>
            <tbody>${bRows}</tbody>
          </table>
        </details>
        <div style="font-size:10.5px;color:#312e81;background:#c7d2fe;padding:6px 8px;border-radius:6px">
          ⚠️ Portal Bunge tiene CAPTCHA — scraping requiere intervención humana cada login.
        </div>
      </div>`;
  }

  // BLOQUE INTAGRO (teal) — datos DW: CTGs con Intagro como corredor
  let intagroHtml = "";
  const intagroRows = TZ_INTAGRO_BY_CTG[String(ctg).trim()] || [];
  if(intagroRows.length){
    const iRows = intagroRows.map(d => `<tr>
      <td style="padding:4px">${tzEscape(d.documento||"")}</td>
      <td style="padding:4px">${tzEscape(d.fechadescarga||d.fechaarribo||d.fecha||"")}</td>
      <td style="padding:4px;font-size:10.5px">${tzEscape(d.corredorprimario||d.corredorsecundario||"")}</td>
      <td style="padding:4px">${tzEscape(d.destino||d.organizacionnombre||"")}</td>
      <td style="padding:4px" class="num"><b>${fmt.num(d.pesoneto)}</b></td>
      <td style="padding:4px" class="num">${fmt.num(d.pesoentregador)}</td>
    </tr>`).join("");
    const totalPesoNeto = intagroRows.reduce((s,d)=>s+(Number(d.pesoneto)||0),0);
    const totalPesoEntr = intagroRows.reduce((s,d)=>s+(Number(d.pesoentregador)||0),0);
    const granos = [...new Set(intagroRows.map(d => d.grano).filter(Boolean))].join(", ");

    intagroHtml = `
      <div style="margin-top:14px;border-radius:10px;background:linear-gradient(135deg,#f0fdfa,#a7f3d0);border-left:4px solid #0d9488;padding:14px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;color:#134e4a;text-transform:uppercase;letter-spacing:.3px">🌿 Intagro — corredor</span>
          <span style="background:#0d9488;color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;font-weight:600">${intagroRows.length} traslado${intagroRows.length>1?'s':''}</span>
          ${granos ? `<span style="font-size:11px;color:#134e4a">Grano: <b>${tzEscape(granos)}</b></span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:10px">
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #16a34a">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Neto</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoNeto)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #3b82f6">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Entregador</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoEntr)} kg</div>
          </div>
        </div>
        <details open style="margin-bottom:8px">
          <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#134e4a;text-transform:uppercase">📦 Traslados via Intagro (${intagroRows.length})</summary>
          <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
            <thead><tr style="background:#a7f3d0">
              <th style="text-align:left;padding:5px">Documento</th>
              <th style="text-align:left;padding:5px">Fecha</th>
              <th style="text-align:left;padding:5px">Corredor</th>
              <th style="text-align:left;padding:5px">Destino</th>
              <th class="num" style="text-align:right;padding:5px">Peso Neto</th>
              <th class="num" style="text-align:right;padding:5px">Entregador</th>
            </tr></thead>
            <tbody>${iRows}</tbody>
          </table>
        </details>
        <div style="font-size:10.5px;color:#134e4a;background:#a7f3d0;padding:6px 8px;border-radius:6px">
          ⚠️ Portal portal.intagro.com pide email — credencial 'agronasaja' rechazada. Solo datos via DW.
        </div>
      </div>`;
  }

  // BLOQUE FYO (pink/rosa) — datos DW: CTGs entregados a FYO Acopio o con corredor FYO
  let fyoHtml = "";
  const fyoRows = TZ_FYO_BY_CTG[String(ctg).trim()] || [];
  if(fyoRows.length){
    const fRows = fyoRows.map(d => `<tr>
      <td style="padding:4px">${tzEscape(d.documento||"")}</td>
      <td style="padding:4px">${tzEscape(d.fechadescarga||d.fechaarribo||d.fecha||"")}</td>
      <td style="padding:4px;font-size:10.5px">${tzEscape(d.corredorprimario||d.corredorsecundario||"")}</td>
      <td style="padding:4px">${tzEscape(d.destino||d.organizacionnombre||"")}</td>
      <td style="padding:4px" class="num"><b>${fmt.num(d.pesoneto)}</b></td>
      <td style="padding:4px" class="num">${fmt.num(d.pesoentregador)}</td>
    </tr>`).join("");
    const totalPesoNeto = fyoRows.reduce((s,d)=>s+(Number(d.pesoneto)||0),0);
    const totalPesoEntr = fyoRows.reduce((s,d)=>s+(Number(d.pesoentregador)||0),0);
    const granos = [...new Set(fyoRows.map(d => d.grano).filter(Boolean))].join(", ");

    fyoHtml = `
      <div style="margin-top:14px;border-radius:10px;background:linear-gradient(135deg,#fdf2f8,#fbcfe8);border-left:4px solid #db2777;padding:14px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;color:#831843;text-transform:uppercase;letter-spacing:.3px">📊 FYO (Futuros y Opciones)</span>
          <span style="background:#db2777;color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;font-weight:600">${fyoRows.length} traslado${fyoRows.length>1?'s':''}</span>
          ${granos ? `<span style="font-size:11px;color:#831843">Grano: <b>${tzEscape(granos)}</b></span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:10px">
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #16a34a">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Neto</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoNeto)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #3b82f6">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Entregador</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoEntr)} kg</div>
          </div>
        </div>
        <details open style="margin-bottom:8px">
          <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#831843;text-transform:uppercase">📦 Traslados FYO (${fyoRows.length})</summary>
          <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
            <thead><tr style="background:#fbcfe8">
              <th style="text-align:left;padding:5px">Documento</th>
              <th style="text-align:left;padding:5px">Fecha</th>
              <th style="text-align:left;padding:5px">Corredor</th>
              <th style="text-align:left;padding:5px">Destino</th>
              <th class="num" style="text-align:right;padding:5px">Peso Neto</th>
              <th class="num" style="text-align:right;padding:5px">Entregador</th>
            </tr></thead>
            <tbody>${fRows}</tbody>
          </table>
        </details>
        <div style="font-size:10.5px;color:#831843;background:#fbcfe8;padding:6px 8px;border-radius:6px">
          ⚠️ Portal fyo.com requiere verificación 2FA por email — no se puede scrapear automáticamente. Datos via DW Finnegans.
        </div>
      </div>`;
  }

  // BLOQUE ALLARIA (purple/violeta) — datos DW: CTGs donde Allaria figura como corredor
  let allariaHtml = "";
  const allariaRows = TZ_ALLARIA_BY_CTG[String(ctg).trim()] || [];
  if(allariaRows.length){
    const aRows = allariaRows.map(d => `<tr>
      <td style="padding:4px">${tzEscape(d.documento||"")}</td>
      <td style="padding:4px">${tzEscape(d.fechadescarga||d.fechaarribo||d.fecha||"")}</td>
      <td style="padding:4px;font-size:10.5px">${tzEscape(d.corredorprimario||d.corredorsecundario||"")}</td>
      <td style="padding:4px">${tzEscape(d.destino||d.organizacionnombre||"")}</td>
      <td style="padding:4px" class="num"><b>${fmt.num(d.pesoneto)}</b></td>
      <td style="padding:4px" class="num">${fmt.num(d.pesoentregador)}</td>
    </tr>`).join("");
    const totalPesoNeto = allariaRows.reduce((s,d)=>s+(Number(d.pesoneto)||0),0);
    const totalPesoEntr = allariaRows.reduce((s,d)=>s+(Number(d.pesoentregador)||0),0);
    const granos = [...new Set(allariaRows.map(d => d.grano).filter(Boolean))].join(", ");

    allariaHtml = `
      <div style="margin-top:14px;border-radius:10px;background:linear-gradient(135deg,#faf5ff,#e9d5ff);border-left:4px solid #7c3aed;padding:14px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span style="font-size:11px;font-weight:700;color:#581c87;text-transform:uppercase;letter-spacing:.3px">🤝 Allaria — corredor</span>
          <span style="background:#7c3aed;color:#fff;padding:2px 8px;border-radius:4px;font-size:10.5px;font-weight:600">${allariaRows.length} traslado${allariaRows.length>1?'s':''}</span>
          ${granos ? `<span style="font-size:11px;color:#581c87">Grano: <b>${tzEscape(granos)}</b></span>` : ''}
        </div>
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:10px">
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #16a34a">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Peso Neto</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoNeto)} kg</div>
          </div>
          <div style="background:#fff;padding:8px;border-radius:6px;border-left:3px solid #3b82f6">
            <div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase">Entregador</div>
            <div style="font-size:15px;font-weight:700">${fmt.num(totalPesoEntr)} kg</div>
          </div>
        </div>
        <details open style="margin-bottom:8px">
          <summary style="cursor:pointer;font-size:11px;font-weight:700;color:#581c87;text-transform:uppercase">📦 Traslados via Allaria (${allariaRows.length})</summary>
          <table style="width:100%;font-size:11px;border-collapse:collapse;margin-top:6px;background:#fff;border-radius:6px;overflow:hidden">
            <thead><tr style="background:#ddd6fe">
              <th style="text-align:left;padding:5px">Documento</th>
              <th style="text-align:left;padding:5px">Fecha</th>
              <th style="text-align:left;padding:5px">Corredor</th>
              <th style="text-align:left;padding:5px">Destino</th>
              <th class="num" style="text-align:right;padding:5px">Peso Neto</th>
              <th class="num" style="text-align:right;padding:5px">Entregador</th>
            </tr></thead>
            <tbody>${aRows}</tbody>
          </table>
        </details>
        <div style="font-size:10.5px;color:#581c87;background:#ddd6fe;padding:6px 8px;border-radius:6px">
          ℹ️ Allaria es corredor de granos — sus operaciones aparecen como contrapartida en LDC y otros destinos. Ver Posición de Campaña y Cuenta Corriente en su portal.
        </div>
      </div>`;
  }

  el.innerHTML = `
    <div style="font-size:10.5px;font-weight:700;color:#854d0e;text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px">🔍 Cadena completa CP → COE → Liquidación</div>
    <table style="width:100%;font-size:11.5px;border-collapse:collapse">
      <thead><tr style="background:#fef3c7"><th style="text-align:left;padding:5px">Paso</th><th style="text-align:left;padding:5px">CP</th><th style="text-align:left;padding:5px">COE</th><th class="num" style="text-align:right;padding:5px">Peso Neto</th><th style="text-align:left;padding:5px">Estado</th></tr></thead>
      <tbody>${cpRowsHtml}</tbody>
    </table>
    ${liqHtml}
    ${contratoHtml}
    ${cargillHtml}
    ${ldcHtml}
    ${acaHtml}
    ${allariaHtml}
    ${fyoHtml}
    ${intagroHtml}
    ${bungeHtml}
    ${cofcoHtml}
  `;
}

// Wire-up
(function tzInit(){
  if(!document.getElementById("tz-tbl")) return;
  tzInitFilters();
  const FIDS = ["tz-ent","tz-cer","tz-prod","tz-ccomp","tz-fdesde","tz-fhasta","tz-q"];
  FIDS.forEach(id => {
    const el = document.getElementById(id);
    if(el) el.addEventListener(id === "tz-q" ? "input" : "change", tzRender);
  });
  const clr = document.getElementById("tz-clear");
  if(clr) clr.addEventListener("click", () => {
    FIDS.forEach(id => document.getElementById(id).value = "");
    TZ_EXPANDED.clear();
    tzRender();
  });
  tzRender();
})();


/* ============================================================
   ============  CONTRATOS · Códigos de Contratos  =============
   ============================================================
   Estructura en KV bajo key "contratos" (shared, todos los internos):
     { compra: [{id, numero, beneficiario}], venta: [...], actualizado: "..." }
   Editable inline, autosave debounced al KV. */

let CT_DATA = { compra: [], venta: [], actualizado: "" };
let CT_ACTIVE_SUB = "compra";   // "compra" | "venta"
const CT_STORAGE_KEY = "tablero-granos-ct-v1";

function ctSetStatus(state){
  const el = document.getElementById("ct-last-save");
  if(!el) return;
  if(state === "pending") el.textContent = "guardando...";
  else if(state === "saving") el.textContent = "subiendo...";
  else if(state === "saved") el.textContent = new Date().toLocaleTimeString('es-AR',{hour:'2-digit',minute:'2-digit'});
  else if(state === "error") el.textContent = "⚠️ error";
}

// Próximo número de contrato (autocompletar): max numérico existente + 1
function ctNextNumero(sub){
  // Toma el código con el mayor número y le suma 1, PRESERVANDO el prefijo y el
  // relleno de ceros. Ej: "VEN209" -> "VEN210", "COMP-0045" -> "COMP-0046".
  let bestNum = -1, bestPrefix = "", bestPad = 0;
  (CT_DATA[sub] || []).forEach(r => {
    const m = String(r.numero || "").trim().match(/^(.*?)(\d+)\s*$/);  // prefijo + número final
    if(!m) return;
    const num = parseInt(m[2], 10);
    if(!isNaN(num) && num > bestNum){ bestNum = num; bestPrefix = m[1]; bestPad = m[2].length; }
  });
  if(bestNum < 0) return "";
  return bestPrefix + String(bestNum + 1).padStart(bestPad, "0");
}

// Mergea el estado remoto (KV) con el local SIN pisar: une filas por id, prefiere
// la versión con ts más nuevo, respeta la fila que se está editando y los borrados (tombstones).
function ctMerge(remote){
  if(!remote) return false;
  const ae = document.activeElement;
  const editingId = (ae && ae.tagName === "INPUT" && ae.dataset) ? ae.dataset.id : null;
  let changed = false;
  CT_DATA._del = CT_DATA._del || {};
  const rdel = remote._del || {};
  Object.keys(rdel).forEach(id => {
    if((rdel[id] || 0) > (CT_DATA._del[id] || 0)){ CT_DATA._del[id] = rdel[id]; changed = true; }
  });
  const tomb = CT_DATA._del;
  ["compra","venta"].forEach(sub => {
    const local = Array.isArray(CT_DATA[sub]) ? CT_DATA[sub] : [];
    const rem   = Array.isArray(remote[sub]) ? remote[sub] : [];
    const map = new Map();
    local.forEach(r => map.set(r.id, r));
    rem.forEach(rr => {
      const lr = map.get(rr.id);
      if(!lr){ map.set(rr.id, rr); changed = true; }
      else if(rr.id !== editingId && (rr.ts || 0) > (lr.ts || 0)){ map.set(rr.id, rr); changed = true; }
    });
    // aplicar borrados (salvo que la fila se haya re-editado después del borrado, o se esté editando)
    Array.from(map.keys()).forEach(id => {
      const r = map.get(id);
      if(tomb[id] && tomb[id] >= (r.ts || 0) && id !== editingId){ map.delete(id); changed = true; }
    });
    CT_DATA[sub] = Array.from(map.values());
  });
  return changed;
}

let _ctSaveTimer = null;
// Guarda con read-merge-write: trae lo último del KV, mergea, y recién ahí sube (no se pisan).
async function ctFlushSave(){
  if(_ctSaveTimer){ clearTimeout(_ctSaveTimer); _ctSaveTimer = null; }
  try{ localStorage.setItem(CT_STORAGE_KEY, JSON.stringify(CT_DATA)); }catch(e){}
  if(!API_AVAILABLE) return true;
  ctSetStatus("saving");
  const remote = await apiLoad("contratos");
  const merged = remote ? ctMerge(remote) : false;
  CT_DATA.actualizado = new Date().toISOString();
  const ok = await apiSave("contratos", CT_DATA);
  ctSetStatus(ok ? "saved" : "error");
  // si el merge trajo filas del compañero, refrescar la tabla (si no se está editando)
  if(merged && !(document.activeElement && document.activeElement.tagName === "INPUT")) ctRender();
  return ok;
}
function ctSave(){
  try{ localStorage.setItem(CT_STORAGE_KEY, JSON.stringify(CT_DATA)); }catch(e){}
  if(!API_AVAILABLE) return;
  ctSetStatus("pending");
  if(_ctSaveTimer) clearTimeout(_ctSaveTimer);
  _ctSaveTimer = setTimeout(() => { _ctSaveTimer = null; ctFlushSave(); }, 1500);
}

// Refresco en vivo: cada 12s trae el KV y mergea para ver los cambios del compañero.
let _ctPollTimer = null;
function ctStartPolling(){
  if(!API_AVAILABLE || _ctPollTimer) return;
  _ctPollTimer = setInterval(async () => {
    if(_ctSaveTimer) return;                 // hay un guardado pendiente, esperar
    const remote = await apiLoad("contratos");
    if(!remote) return;
    const changed = ctMerge(remote);
    if(changed && !(document.activeElement && document.activeElement.tagName === "INPUT")){
      ctRender();
    }
  }, 8000);
}

async function ctLoadInitial(){
  const fromApi = await apiLoad("contratos");
  if(fromApi && (Array.isArray(fromApi.compra) || Array.isArray(fromApi.venta))){
    CT_DATA = {
      compra: Array.isArray(fromApi.compra) ? fromApi.compra : [],
      venta:  Array.isArray(fromApi.venta)  ? fromApi.venta  : [],
      actualizado: fromApi.actualizado || "",
      _del: (fromApi._del && typeof fromApi._del === "object") ? fromApi._del : {}
    };
    try{ localStorage.setItem(CT_STORAGE_KEY, JSON.stringify(CT_DATA)); }catch(e){}
    return;
  }
  // Fallback: localStorage
  try{
    const ls = JSON.parse(localStorage.getItem(CT_STORAGE_KEY) || "null");
    if(ls && (Array.isArray(ls.compra) || Array.isArray(ls.venta))){
      CT_DATA = ls;
    }
  } catch(e){}
}

function ctFiltered(sub){
  const q = (document.getElementById("ct-q").value || "").toLowerCase().trim();
  const arr = (CT_DATA[sub] || []);
  if(!q) return arr;
  return arr.filter(r =>
    (r.numero || "").toLowerCase().includes(q) ||
    (r.beneficiario || "").toLowerCase().includes(q)
  );
}

function ctEscape(s){
  return String(s||"").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
}

function ctRenderTable(sub){
  const tbl = document.getElementById("ct-tbl-" + sub);
  if(!tbl) return;
  const tbody = tbl.querySelector("tbody");
  const rows = ctFiltered(sub);
  if(!rows.length){
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:30px;color:var(--muted)">Sin contratos para los filtros aplicados</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r, idx) => `
    <tr data-id="${ctEscape(r.id)}">
      <td style="color:var(--muted);font-size:11px;text-align:center">${idx+1}</td>
      <td><input type="text" data-id="${ctEscape(r.id)}" data-k="numero" value="${ctEscape(r.numero)}" style="width:100%;border:1px solid transparent;background:transparent;padding:4px 6px;font-size:12.5px;font-family:inherit;border-radius:4px"/></td>
      <td><input type="text" data-id="${ctEscape(r.id)}" data-k="beneficiario" value="${ctEscape(r.beneficiario)}" style="width:100%;border:1px solid transparent;background:transparent;padding:4px 6px;font-size:12.5px;font-family:inherit;border-radius:4px"/></td>
      <td style="text-align:center">
        <button class="row-btn" data-id="${ctEscape(r.id)}" data-act="del" style="border:1px solid #fecaca;background:#fff;color:#dc2626;cursor:pointer;padding:3px 9px;border-radius:5px;font-size:11px">🗑️</button>
      </td>
    </tr>
  `).join("");

  tbody.querySelectorAll("input").forEach(inp => {
    inp.addEventListener("focus", () => { inp.style.border = "1px solid var(--blue)"; inp.style.background = "#fff"; });
    inp.addEventListener("blur", () => {
      inp.style.border = "1px solid transparent"; inp.style.background = "transparent";
      const arr = CT_DATA[sub];
      const r = arr.find(x => x.id === inp.dataset.id);
      if(!r) return;
      const v = inp.value.trim();
      if(r[inp.dataset.k] === v) return;
      r[inp.dataset.k] = v;
      r.ts = Date.now();
      ctSave();
      ctRenderKpis();
    });
    inp.addEventListener("mouseover", () => { if(document.activeElement !== inp){ inp.style.border = "1px solid var(--line)"; inp.style.background = "#fff"; } });
    inp.addEventListener("mouseout",  () => { if(document.activeElement !== inp){ inp.style.border = "1px solid transparent"; inp.style.background = "transparent"; } });
  });
  tbody.querySelectorAll("button[data-act='del']").forEach(b => {
    b.addEventListener("click", () => {
      const arr = CT_DATA[sub];
      const idx = arr.findIndex(x => x.id === b.dataset.id);
      if(idx < 0) return;
      if(!confirm(`¿Borrar contrato "${arr[idx].numero || '(sin nº)'}" — ${arr[idx].beneficiario || '(sin beneficiario)'}?`)) return;
      CT_DATA._del = CT_DATA._del || {};
      CT_DATA._del[arr[idx].id] = Date.now();   // tombstone para que el borrado se respete entre usuarios
      arr.splice(idx, 1);
      ctSave();
      ctRender();
    });
  });
}

function ctRenderKpis(){
  document.getElementById("ct-tot-compra").textContent = (CT_DATA.compra || []).length;
  document.getElementById("ct-tot-venta").textContent  = (CT_DATA.venta || []).length;
  document.getElementById("ct-total").textContent = (CT_DATA.compra || []).length + (CT_DATA.venta || []).length;
  document.getElementById("cnt-ct-compra").textContent = (CT_DATA.compra || []).length;
  document.getElementById("cnt-ct-venta").textContent  = (CT_DATA.venta || []).length;
  const cnt = document.getElementById("cnt-contratos");
  if(cnt) cnt.textContent = (CT_DATA.compra || []).length + (CT_DATA.venta || []).length;
  const arr = CT_DATA[CT_ACTIVE_SUB] || [];
  const filt = ctFiltered(CT_ACTIVE_SUB);
  document.getElementById("ct-count").textContent = `${filt.length} / ${arr.length}`;
}

function ctRender(){
  // detectar sub-pestaña activa
  const activeSub = document.querySelector('.panel[data-panel="contratos"] .subpanel.active');
  if(activeSub){
    const sp = activeSub.getAttribute("data-sub-panel") || "ct-compra";
    CT_ACTIVE_SUB = sp.replace("ct-", "") || "compra";
  }
  ctRenderKpis();
  ctRenderTable("compra");
  ctRenderTable("venta");
}

// Wire-up
(function ctInit(){
  const q = document.getElementById("ct-q");
  if(q) q.addEventListener("input", ctRender);
  const clr = document.getElementById("ct-clear");
  if(clr) clr.addEventListener("click", () => { q.value = ""; ctRender(); });
  const add = document.getElementById("ct-add");
  if(add) add.addEventListener("click", () => {
    const arr = CT_DATA[CT_ACTIVE_SUB];
    if(!arr) return;
    const newId = (CT_ACTIVE_SUB === "compra" ? "c" : "v") + "-" + Date.now();
    arr.push({ id: newId, numero: ctNextNumero(CT_ACTIVE_SUB), beneficiario: "", ts: Date.now() });
    ctSave();
    ctRender();
    // foco al numero recien creado
    requestAnimationFrame(() => {
      const inp = document.querySelector(`#ct-tbl-${CT_ACTIVE_SUB} tr[data-id="${newId}"] input[data-k="numero"]`);
      if(inp){ inp.scrollIntoView({behavior:"smooth", block:"center"}); inp.focus(); }
    });
  });
  // Guardar ahora: flushea el debounce y hace upload inmediato al KV
  const saveBtn = document.getElementById("ct-save");
  if(saveBtn) saveBtn.addEventListener("click", async () => {
    // Hacer blur al input activo asi se commitea el valor en curso
    if(document.activeElement && document.activeElement.tagName === "INPUT"){
      document.activeElement.blur();
    }
    saveBtn.disabled = true;
    saveBtn.textContent = "⏳ Guardando…";
    const ok = await ctFlushSave();   // read-merge-write: no pisa los cambios del compañero
    saveBtn.textContent = ok ? "✓ Guardado" : "⚠️ Error";
    setTimeout(() => { saveBtn.textContent = "💾 Guardar ahora"; saveBtn.disabled = false; }, 2200);
  });
  // sub-tab listeners
  document.querySelectorAll('.panel[data-panel="contratos"] .subtab').forEach(st => {
    st.addEventListener("click", () => setTimeout(ctRender, 30));
  });
  (async () => {
    await ctLoadInitial();
    ctRender();
    ctStartPolling();   // refresco en vivo para ver los cambios del compañero
  })();
})();

/* ============================================================
   ============  TAQUEO CTG · Seguimiento fino  ================
   ============================================================ */
(function tqInit(){
  const T = PAYLOAD.taqueo || {};
  const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const n = v => (v==null?0:Number(v)).toLocaleString('es-AR',{maximumFractionDigits:1});
  function render(){
    if(!document.getElementById("tq-seg")) return;
    const vent = T.ventana ? `${T.ventana[0]} → ${T.ventana[1]}` : "—";
    const gel = document.getElementById("tq-ventana"); if(gel) gel.textContent = `Ventana: ${vent}`;
    const seg = T.seguimiento || {};
    const pl  = T.pendiente_liquidar || {};
    const fv  = T.falta_vincular || {};

    // ---- KPIs ----
    let totDup = 0, totFV = 0, totDescalce = 0;
    Object.values(seg).forEach(g => {
      totDup += (g.propio?.duplicados?.length||0)+(g.compra?.duplicados?.length||0)+(g.venta?.duplicados?.length||0);
      totDescalce += (g.compra_sin_venta?.length||0)+(g.venta_sin_compra?.length||0);
    });
    Object.values(fv).forEach(c => totFV += (c.falta_en_finnegans?.length||0)+(c.falta_en_extranet?.length||0));
    const kpis = [
      {lbl:"Pendiente de liquidar", val:n(pl.total_tn)+" tn", cls:"orange", hint:"de lo entregado"},
      {lbl:"CTGs duplicados", val:totDup, cls: totDup?"red":"", hint:"misma carta 2 veces"},
      {lbl:"Falta vincular", val:totFV, cls: totFV?"red":"", hint:"en extranet, no en Finnegans"},
      {lbl:"Descalce compra↔venta", val:totDescalce, cls: totDescalce?"red":"green", hint:"consignación sin cerrar"},
    ];
    document.getElementById("tq-kpis").innerHTML = kpis.map(k =>
      `<div class="kpi ${k.cls}"><div class="lbl">${k.lbl}</div><div class="val">${k.val}</div><div class="hint">${k.hint}</div></div>`).join("");

    // ---- Seguimiento por grano × flujo ----
    const th = `<tr><th>Grano</th><th>Sale de campo (propio)</th><th>Consignación compra</th><th>Consignación venta</th><th>Cruce</th></tr>`;
    const body = ["Soja","Maíz","Trigo Pan"].map(g => {
      const s = seg[g]; if(!s) return "";
      const desc = (s.compra_sin_venta?.length||0)+(s.venta_sin_compra?.length||0);
      const cruce = desc ? `<span style="color:#dc2626;font-weight:600">⚠ ${desc} sin cerrar</span>` : `<span style="color:#16a34a">✓ ${s.cierran} cierran</span>`;
      const cell = (o) => `${o.ctgs} CTG · ${n(o.tn)} tn${o.duplicados&&o.duplicados.length?` <span style="color:#dc2626">⚠${o.duplicados.length}dup</span>`:""}`;
      return `<tr><td style="font-weight:600">${g}</td><td>${cell(s.propio)}</td><td>${cell(s.compra)}</td><td>${cell(s.venta)}</td><td>${cruce}</td></tr>`;
    }).join("");
    const segTbl = document.getElementById("tq-seg");
    segTbl.querySelector("thead").innerHTML = th;
    segTbl.querySelector("tbody").innerHTML = body || `<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:16px">Sin datos</td></tr>`;

    // ---- Pendiente de liquidar por cerealera (LAZY: el detalle de CTGs se arma al desplegar) ----
    const pc = pl.por_cerealera || {};
    const cers = Object.keys(pc);
    const pend = document.getElementById("tq-pend");
    pend.innerHTML = cers.length ? cers.map((cer,i) => {
      const c = pc[cer];
      return `<details data-cer="${i}" style="margin-bottom:8px;border:1px solid var(--line);border-radius:8px;overflow:hidden">
        <summary style="cursor:pointer;padding:10px 14px;background:#f8fafc;font-weight:600;display:flex;justify-content:space-between">
          <span>${esc(cer)}</span><span>${n(c.tn)} tn · ${c.n_contratos} contratos</span></summary>
        <div class="tq-cer-body" style="padding:8px;color:var(--muted);font-size:12px">cargando…</div>
      </details>`;
    }).join("") : '<div style="color:var(--muted);padding:14px">Sin pendientes.</div>';
    // armar el detalle recién al abrir cada cerealera (evita construir miles de CTG de una)
    pend.querySelectorAll("details[data-cer]").forEach(d => {
      d.addEventListener("toggle", () => {
        if(!d.open || d.dataset.done) return;
        d.dataset.done = "1";
        const c = pc[cers[+d.dataset.cer]];
        const rows = (c.contratos||[]).map(ct => {
          const ctgs = (ct.ctgs||[]).map(x=>x.ctg);
          const ctgStr = ctgs.length ? ctgs.join(", ") : '<span style="color:var(--muted)">— sin CTG linkeado</span>';
          return `<tr><td>${esc(ct.num)}</td><td>${esc(ct.grano||"")}</td><td style="text-align:right;font-weight:600">${n(ct.tn)}</td><td style="font-size:11px;color:#475569;word-break:break-word">${ctgStr}</td></tr>`;
        }).join("");
        d.querySelector(".tq-cer-body").outerHTML =
          `<div style="overflow-x:auto"><table class="tbl" style="margin:0"><thead><tr><th>Contrato</th><th>Grano</th><th style="text-align:right">Tn pend.</th><th>CTGs</th></tr></thead><tbody>${rows}</tbody></table></div>`;
      });
    });

    // ---- Entregado sin liquidar por contrato (Finnegans BSA) ----
    renderLiq();

    // ---- Taqueo por rango: se calcula aparte (crossRange) según las fechas elegidas ----
    const hhEl = document.getElementById("tq-hasta");
    if(hhEl && !hhEl.value){ hhEl.value = new Date().toISOString().slice(0,10); }
    crossRange();
  }

  // Entregado SIN liquidar: vista filtrable (campaña / organización / cultivo) -> tabla de CTG
  let _liqRows = null;
  function campShort(c){ const m=String(c||"").match(/(\d{2}\s*[-\/]\s*\d{2})/); return m?m[1].replace(/\s/g,""):String(c||""); }
  function renderLiq(){
    const cont = document.getElementById("tq-liq");
    if(!cont) return;
    const L = PAYLOAD.taqueo_liq || {};
    const cts = L.contratos || [];
    const kp = document.getElementById("tq-liq-kpis");
    const bdg = document.getElementById("tq-liq-badge");
    if(!cts.length){
      if(kp) kp.innerHTML = "";
      cont.innerHTML = '<div style="color:var(--muted);padding:14px">Sin datos. Corré <code>scripts/finn_taqueo_ctg.py</code> (sesión Finnegans GO abierta) y recommiteá <code>data/taqueo_liquidar.json</code>.</div>';
      return;
    }
    if(bdg && L.generated_at) bdg.textContent = "actualizado " + String(L.generated_at).slice(0,10);

    // aplanar a filas de CTG (una vez)
    if(!_liqRows){
      _liqRows = [];
      cts.forEach(c => {
        const base = {camp:c.cosecha||"", campS:campShort(c.cosecha), org:c.org||"", cer:c.cerealera||"",
                      prod:c.producto||"", contrato:c.contrato};
        (c.sin_liquidar||[]).forEach(x => _liqRows.push({...base, ctg:x.ctg, cp:x.cp, fecha:x.fecha, tn:x.tn, rara:false}));
        (c.sin_liquidar_raras||[]).forEach(x => _liqRows.push({...base, ctg:x.ctg, cp:x.cp, fecha:x.fecha, tn:x.tn, rara:true}));
      });
    }

    // poblar filtros (una vez)
    const selC=document.getElementById("tql-camp"), selO=document.getElementById("tql-org"), selP=document.getElementById("tql-prod");
    const q=document.getElementById("tql-q"), reset=document.getElementById("tql-reset");
    if(selC && !selC.dataset.init){
      selC.dataset.init="1";
      const uniq=(k)=>[...new Set(_liqRows.map(r=>r[k]).filter(Boolean))];
      const camps=[...new Set(_liqRows.map(r=>r.camp).filter(Boolean))].sort().reverse();
      camps.forEach(c=>selC.insertAdjacentHTML("beforeend",`<option value="${esc(c)}">${esc(campShort(c))}</option>`));
      uniq("org").sort().forEach(o=>selO.insertAdjacentHTML("beforeend",`<option value="${esc(o)}">${esc(o)}</option>`));
      uniq("prod").sort().forEach(p=>selP.insertAdjacentHTML("beforeend",`<option value="${esc(p)}">${esc(p)}</option>`));
      [selC,selO,selP].forEach(s=>s.addEventListener("change",applyLiq));
      if(q) q.addEventListener("input",applyLiq);
      if(reset) reset.addEventListener("click",()=>{selC.value="";selO.value="";selP.value="";if(q)q.value="";applyLiq();});
    }
    applyLiq();
  }

  function applyLiq(){
    const cont=document.getElementById("tq-liq"); if(!cont||!_liqRows) return;
    const cv=(document.getElementById("tql-camp")||{}).value||"";
    const ov=(document.getElementById("tql-org")||{}).value||"";
    const pv=(document.getElementById("tql-prod")||{}).value||"";
    const qv=((document.getElementById("tql-q")||{}).value||"").replace(/\D/g,"");
    const rows=_liqRows.filter(r =>
      (!cv||r.camp===cv) && (!ov||r.org===ov) && (!pv||r.prod===pv) &&
      (!qv || String(r.ctg).includes(qv) || String(r.cp).replace(/\D/g,"").includes(qv)));
    // KPIs dinámicos
    const kp=document.getElementById("tq-liq-kpis");
    const tn=rows.reduce((s,r)=>s+(r.tn||0),0);
    const nctos=new Set(rows.map(r=>r.contrato+"|"+r.camp)).size;
    if(kp) kp.innerHTML=[
      {lbl:"CTG sin liquidar",val:n(rows.length),cls:"orange",hint:"con el filtro actual"},
      {lbl:"Tn sin liquidar",val:n(tn)+" tn",cls:"orange",hint:"de lo entregado"},
      {lbl:"Contratos",val:n(nctos),cls:"",hint:"distintos"},
    ].map(k=>`<div class="kpi ${k.cls}"><div class="lbl">${k.lbl}</div><div class="val">${k.val}</div><div class="hint">${k.hint}</div></div>`).join("");
    // ordenar: campaña desc, org, contrato, fecha
    rows.sort((a,b)=> (b.camp||"").localeCompare(a.camp||"") || (a.org||"").localeCompare(b.org||"")
      || String(a.contrato).localeCompare(String(b.contrato),undefined,{numeric:true}) || String(a.fecha).localeCompare(String(b.fecha)));
    if(!rows.length){ cont.innerHTML='<div style="color:var(--muted);padding:14px">Sin CTG para ese filtro.</div>'; return; }
    const body=rows.map(r=>`<tr${r.rara?' style="background:#fffbeb"':''}>`+
      `<td style="font-size:11px">${esc(r.campS)}</td>`+
      `<td style="font-size:11px">${esc(r.prod.replace(/^Grano\s+/,""))}</td>`+
      `<td style="font-size:11px">${esc(r.org)}</td>`+
      `<td style="font-weight:600">${esc(r.contrato)}</td>`+
      `<td style="font-family:monospace;font-size:12px">${esc(r.ctg)}${r.rara?' <span title="no es carta de porte válida (12 díg.)" style="color:#b45309">⚠</span>':''}</td>`+
      `<td style="font-size:11px;color:#475569">${esc(r.cp)}</td>`+
      `<td style="font-size:11px">${esc(r.fecha)}</td>`+
      `<td style="text-align:right;font-weight:600">${n(r.tn)}</td></tr>`).join("");
    cont.innerHTML=`<div style="overflow-x:auto"><table class="tbl" style="margin:0">
      <thead><tr><th>Campaña</th><th>Cultivo</th><th>Organización</th><th>Contrato</th><th>CTG</th><th>Carta de porte</th><th>Fecha</th><th style="text-align:right">Tn</th></tr></thead>
      <tbody>${body}</tbody></table></div>`;
  }

  // Cruce en vivo por rango de fechas, desde los CTG crudos embebidos (T.raw)
  function crossRange(){
    const raw = T.raw || {};
    const ddEl=document.getElementById("tq-desde"), hhEl=document.getElementById("tq-hasta");
    const cont=document.getElementById("tq-alertas");
    if(!ddEl || !cont) return;
    const dd=ddEl.value, hh=hhEl.value;
    const inR = f => f && (!dd||f>=dd) && (!hh||f<=hh);
    const listC = a => a.slice(0,30).map(esc).join(", ") + (a.length>30?` (+${a.length-30})`:"");
    let al="", totFnn=0, totExt=0, totDup=0;
    const keys=Object.keys(raw);
    if(!keys.length){ cont.innerHTML='<div style="color:var(--muted);padding:10px">Sin datos crudos (rebuild pendiente).</div>'; return; }
    keys.forEach(cer => {
      const r=raw[cer], F={}, E={};
      (r.finnegans||[]).forEach(a=>{ if(inR(a[1])) F[a[0]]=(F[a[0]]||0)+1; });
      (r.extranet ||[]).forEach(a=>{ if(inR(a[1])) E[a[0]]=(E[a[0]]||0)+1; });
      const fK=Object.keys(F), eK=Object.keys(E);
      const faltaFnn=eK.filter(c=>!F[c]);      // en extranet, no en Finnegans
      const faltaExt=fK.filter(c=>!E[c]);      // en Finnegans, no en extranet
      const coinc=eK.filter(c=>F[c]).length;
      const dupF=fK.filter(c=>F[c]>1), dupE=eK.filter(c=>E[c]>1);
      totFnn+=faltaFnn.length; if(r.completo) totExt+=faltaExt.length; totDup+=dupF.length+dupE.length;
      let s=`<details style="margin-bottom:8px;border:1px solid var(--line);border-radius:8px;overflow:hidden" ${cer==="Cargill"?"open":""}>
        <summary style="cursor:pointer;padding:10px 14px;background:#f8fafc;font-weight:600">${esc(cer)}
          <span style="font-size:11px;color:var(--muted)">· ${esc(r.fuente)} · Finnegans ${fK.length} / extranet ${eK.length} · coinciden ${coinc}</span></summary>
        <div style="padding:10px 14px">`;
      s+=`<div style="margin-bottom:6px;color:${faltaFnn.length?'#dc2626':'#16a34a'}">→ falta ingresar en <b>Finnegans</b>: ${faltaFnn.length}${faltaFnn.length?` <span style="font-size:11px;color:#475569">${listC(faltaFnn)}</span>`:" ✓"}</div>`;
      if(r.completo){
        s+=`<div style="margin-bottom:6px;color:${faltaExt.length?'#dc2626':'#16a34a'}">→ falta ingresar en <b>${esc(cer)}</b>: ${faltaExt.length}${faltaExt.length?` <span style="font-size:11px;color:#475569">${listC(faltaExt)}</span>`:" ✓"}</div>`;
      } else {
        s+=`<div style="margin-bottom:6px;font-size:11px;color:var(--muted)">→ falta ingresar en ${esc(cer)}: necesito la descarga completa del extranet</div>`;
      }
      if(dupF.length||dupE.length) s+=`<div style="color:#dc2626;font-size:12px">🔴 duplicados en Finnegans: ${dupF.length}${dupF.length?" ("+listC(dupF)+")":""}${dupE.length?` · en extranet: ${dupE.length}`:""}</div>`;
      al+=s+"</div></details>";
    });
    cont.innerHTML=al;
    const inf=document.getElementById("tq-cruce-info");
    if(inf) inf.textContent=`faltan ${totFnn} en Finnegans · ${totExt} en cerealeras · ${totDup} duplicados`;
  }

  // LAZY: renderiza recién al abrir la subpestaña
  let _rendered = false;
  function showAndRender(){ _rendered = true; render(); }
  document.querySelectorAll('[data-go-sub="pn-taqueo"], .subtab[data-sub="pn-taqueo"]')
    .forEach(a => a.addEventListener("click", () => setTimeout(showAndRender, 50)));
  ["tq-desde","tq-hasta"].forEach(id => { const e=document.getElementById(id); if(e) e.addEventListener("change", crossRange); });
  const btn=document.getElementById("tq-cruzar"); if(btn) btn.addEventListener("click", crossRange);
})();

/* ============================================================
   ====  ANÁLISIS DE CANJE DE COMPRAS · pend. liquidar  =======
   Contratos de compra de GRANOS con entregado sin liquidar,
   agrupado por comercial, mostrando si tiene precio (fijación).
   ============================================================ */
(function clqInit(){
  const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const n = v => (v==null?0:Number(v)).toLocaleString('es-AR',{maximumFractionDigits:1});
  const norm = s => String(s||'').replace(/\s+/g,' ').trim().toUpperCase();
  const campShort = c => { const m=String(c||'').match(/(\d{2}\s*[-\/]\s*\d{2})/); return m?m[1].replace(/\s/g,''):String(c||''); };
  // clasificación de precio (espeja cpFijadoChip)
  function precioClass(r){
    const t=(r.tipocontrato||'').toLowerCase();
    const aj=r.cantidadmax||0, fj=r.cantidadfijada||0;
    if(t.includes('a precio')) return 'si';
    if(t.includes('contra entrega')) return 'si';
    if(aj>0 && fj>=aj*0.999) return 'si';
    if(fj>0) return 'parcial';
    return 'no';
  }
  let _rows = null;
  function build(){
    if(_rows) return _rows;
    const compra = PAYLOAD.compra || [];
    const saldos = PAYLOAD.saldos || [];
    // mapa organización -> comercial (vendedor) desde composición de saldos
    const com = {};
    saldos.forEach(s => { const o=norm(s.organizacion), v=s.vendedor; if(o && v && !com[o]) com[o]=v; });
    _rows = [];
    compra.forEach(c => {
      if(!String(c.producto||'').trim().toLowerCase().startsWith('grano')) return;   // solo granos
      if(String(c.estadoanulacion||'').toLowerCase().indexOf('no anul')<0 && String(c.estadoanulacion||'').toLowerCase().includes('anul')) return;
      const entregada = Number(c.cantidadentregada)||0, liquidada = Number(c.cantidadliquidada)||0;
      // pendiente = entregado - liquidado (calculado; el campo cantidadentregadapendienteliquidar
      // del API viene inestable/0). Es exactamente la diferencia que se quiere seguir.
      const pend = Math.max(0, entregada - liquidada);
      if(pend <= 0.05) return;
      _rows.push({
        comercial: com[norm(c.organizacion)] || '— sin comercial —',
        proveedor: c.organizacion||'', grano: (c.producto||'').replace(/^Grano\s+/i,''),
        campana: c.campana||'', campS: campShort(c.campana), contrato: c.numerointerno||c.contrato||'',
        entregada, liquidada,
        pend, precio: Number(c.preciopromediofijado)||0, precioClass: precioClass(c),
        chip: (typeof cpFijadoChip==='function') ? cpFijadoChip(c) : '', raw:c,
      });
    });
    return _rows;
  }
  function filtered(){
    const rows = build();
    const cv=(document.getElementById('clq-com')||{}).value||'';
    const gv=(document.getElementById('clq-grano')||{}).value||'';
    const kv=(document.getElementById('clq-camp')||{}).value||'';
    const pv=(document.getElementById('clq-precio')||{}).value||'';
    const qv=norm((document.getElementById('clq-q')||{}).value||'');
    return rows.filter(r =>
      (!cv||r.comercial===cv) && (!gv||r.grano===gv) && (!kv||r.campana===kv) &&
      (!pv||r.precioClass===pv) &&
      (!qv || norm(r.proveedor).includes(qv) || norm(r.contrato).includes(qv)));
  }
  function render(){
    if(!document.getElementById('clq-detalle')) return;
    const rows = build();
    // poblar filtros una vez
    const selC=document.getElementById('clq-com'), selG=document.getElementById('clq-grano'), selK=document.getElementById('clq-camp');
    if(selC && !selC.dataset.init){
      selC.dataset.init='1';
      [...new Set(rows.map(r=>r.comercial))].sort().forEach(v=>selC.insertAdjacentHTML('beforeend',`<option value="${esc(v)}">${esc(v)}</option>`));
      [...new Set(rows.map(r=>r.grano))].sort().forEach(v=>selG.insertAdjacentHTML('beforeend',`<option value="${esc(v)}">${esc(v)}</option>`));
      [...new Set(rows.map(r=>r.campana).filter(Boolean))].sort().reverse().forEach(v=>selK.insertAdjacentHTML('beforeend',`<option value="${esc(v)}">${esc(campShort(v))}</option>`));
      ['clq-com','clq-grano','clq-camp','clq-precio'].forEach(id=>{const e=document.getElementById(id); if(e) e.addEventListener('change',draw);});
      const q=document.getElementById('clq-q'); if(q) q.addEventListener('input',draw);
      const rst=document.getElementById('clq-reset'); if(rst) rst.addEventListener('click',()=>{['clq-com','clq-grano','clq-camp','clq-precio','clq-q'].forEach(id=>{const e=document.getElementById(id); if(e) e.value='';}); draw();});
    }
    draw();
  }
  function draw(){
    const rows = filtered();
    // KPIs
    const tnPend=rows.reduce((s,r)=>s+r.pend,0);
    const tnConPrecio=rows.filter(r=>r.precioClass==='si').reduce((s,r)=>s+r.pend,0);
    const tnSinPrecio=rows.filter(r=>r.precioClass==='no').reduce((s,r)=>s+r.pend,0);
    const kp=document.getElementById('clq-kpis');
    if(kp) kp.innerHTML=[
      {lbl:'Tn pend. liquidar',val:n(tnPend)+' tn',cls:'orange',hint:'entregado − liquidado'},
      {lbl:'Con precio',val:n(tnConPrecio)+' tn',cls:'green',hint:'fijación cerrada'},
      {lbl:'Sin precio',val:n(tnSinPrecio)+' tn',cls: tnSinPrecio?'red':'',hint:'falta fijar'},
      {lbl:'Contratos',val:n(rows.length),cls:'',hint:'con pendiente'},
    ].map(k=>`<div class="kpi ${k.cls}"><div class="lbl">${k.lbl}</div><div class="val">${k.val}</div><div class="hint">${k.hint}</div></div>`).join('');
    // resumen por comercial
    const by={};
    rows.forEach(r=>{ const b=by[r.comercial]=by[r.comercial]||{com:r.comercial,ctos:0,pend:0,conP:0,parc:0,sinP:0}; b.ctos++; b.pend+=r.pend;
      if(r.precioClass==='si') b.conP+=r.pend; else if(r.precioClass==='parcial') b.parc+=r.pend; else b.sinP+=r.pend; });
    const rt=document.getElementById('clq-resumen');
    const coms=Object.values(by).sort((a,b)=>b.pend-a.pend);
    rt.querySelector('thead').innerHTML='<tr><th>Comercial</th><th style="text-align:right">Contratos</th><th style="text-align:right">Tn pend. liq.</th><th style="text-align:right">Con precio</th><th style="text-align:right">Parcial</th><th style="text-align:right">Sin precio</th></tr>';
    rt.querySelector('tbody').innerHTML = coms.map(b=>
      `<tr class="clq-com-row" data-com="${esc(b.com)}" style="cursor:pointer">`+
      `<td style="font-weight:600">${esc(b.com)}</td><td style="text-align:right">${b.ctos}</td>`+
      `<td style="text-align:right;font-weight:600">${n(b.pend)}</td>`+
      `<td style="text-align:right;color:#16a34a">${n(b.conP)}</td>`+
      `<td style="text-align:right;color:#b45309">${n(b.parc)}</td>`+
      `<td style="text-align:right;color:${b.sinP?'#dc2626':'inherit'}">${n(b.sinP)}</td></tr>`).join('')
      || '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:16px">Sin datos</td></tr>';
    rt.querySelectorAll('.clq-com-row').forEach(tr => tr.addEventListener('click', () => {
      const s=document.getElementById('clq-com'); if(s){ s.value=tr.dataset.com; draw(); }
    }));
    // detalle por contrato
    rows.sort((a,b)=> a.comercial.localeCompare(b.comercial) || b.pend-a.pend);
    const dt=document.getElementById('clq-detalle');
    dt.querySelector('thead').innerHTML='<tr><th>Comercial</th><th>Proveedor</th><th>Grano</th><th>Campaña</th><th>Contrato</th><th style="text-align:right">Entregado</th><th style="text-align:right">Liquidado</th><th style="text-align:right">Pend. liq.</th><th>¿A precio?</th></tr>';
    dt.querySelector('tbody').innerHTML = rows.map(r=>
      `<tr><td style="font-size:11px">${esc(r.comercial)}</td>`+
      `<td style="font-size:11px">${esc(r.proveedor)}</td>`+
      `<td style="font-size:11px">${esc(r.grano)}</td>`+
      `<td style="font-size:11px">${esc(r.campS)}</td>`+
      `<td style="font-weight:600">${esc(r.contrato)}</td>`+
      `<td style="text-align:right">${n(r.entregada)}</td>`+
      `<td style="text-align:right">${n(r.liquidada)}</td>`+
      `<td style="text-align:right;font-weight:600">${n(r.pend)}</td>`+
      `<td>${r.chip}</td></tr>`).join('')
      || '<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:16px">Sin contratos para ese filtro</td></tr>';
    const meta=document.getElementById('clq-det-meta'); if(meta) meta.textContent=`${rows.length} contratos · ${n(rows.reduce((s,r)=>s+r.pend,0))} tn pend.`;
  }
  window.clqDraw = draw;
  document.querySelectorAll('[data-go-sub="cp-canje-liq"], .subtab[data-sub="cp-canje-liq"]')
    .forEach(a => a.addEventListener('click', () => setTimeout(render, 60)));
})();

/* ============================================================
   ====  FINALES PENDIENTES · cola de trabajo con semáforo  ===
   Compra de granos: entregado - liquidado = final a hacer.
   Estado: hecha(liquidada) / enviada(manual, localStorage) / pendiente.
   ============================================================ */
(function fpInit(){
  const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const n = v => (v==null?0:Number(v)).toLocaleString('es-AR',{maximumFractionDigits:1});
  const campShort = c => { const m=String(c||'').match(/(\d{2}\s*[-\/]\s*\d{2})/); return m?m[1].replace(/\s/g,''):String(c||''); };
  const LS='fp_enviadas';
  // Contratos que ya se mandaron a administración (semilla inicial, por nº de contrato).
  const SEED_ENVIADAS=['1020','1071','834','1007','1051','1040','1102','979','994','1091','1080','989','1106','1022','1090','975','1052','903','1008','1065','1002','983','984','1094','998','962','1016','972','996','976','991','974','997'];
  // Enviadas que estaban SOLO en el localStorage de otra compu (ej. Carla entrando por
  // github.io directo, fuera del KV) → hay que garantizarlas en el estado compartido.
  // Se GARANTIZAN en cada lectura/escritura (no "una sola vez"): así una lectura vieja del
  // KV (Cloudflare es eventually-consistent) no las puede revertir. Como contrapartida, estos
  // contratos no se pueden "des-enviar" (reaparecen); sí se pueden pasar a Hecha (Hecha gana).
  const FP_MERGE_ENVIADAS = [
    '1042','1068','1020','1074','1055','1066','829','828','834','903','1065','1071','1102','1022','1086','972','975','979','984','983',
    '989','990','991','996','997','998','994','1008','1007','1016','1094','1040','1052','1051','1090','1106','1080','1091','1107','1104'
  ];
  function fpEnsureMerge(){   // garantiza las enviadas de FP_MERGE en _fpEnv (salvo si ya están hechas)
    if(!_fpEnv) return;
    FP_MERGE_ENVIADAS.forEach(c => { const id=String(c); if(!(_fpHec && _fpHec.has(id))) _fpEnv.add(id); });
  }
  function initSeed(){
    if(!localStorage.getItem('fp_enviadas_init')){
      localStorage.setItem(LS, JSON.stringify(SEED_ENVIADAS));
      localStorage.setItem('fp_enviadas_init','1');
    }
  }
  const LSH='fp_hechas', KVKEY='finales_estado';
  // Estado compartido: enviadas (🟡) + hechas (🟢). En la nube (Worker KV) si estamos
  // detrás del Worker; si no (github.io directo), cae a localStorage. En memoria: _fpEnv/_fpHec.
  let _fpEnv=null, _fpHec=null;
  function getEnviadas(){ return _fpEnv || new Set(); }
  function getHechas(){ return _fpHec || new Set(); }
  function getEnviadasLS(){ try{ return new Set(JSON.parse(localStorage.getItem(LS)||'[]')); }catch(e){ return new Set(); } }
  function getHechasLS(){ try{ return new Set(JSON.parse(localStorage.getItem(LSH)||'[]')); }catch(e){ return new Set(); } }
  async function fpLoadState(){
    if(typeof API_AVAILABLE!=='undefined' && API_AVAILABLE){
      const r = await apiLoad(KVKEY);
      if(r && (Array.isArray(r.enviadas)||Array.isArray(r.hechas))){
        _fpEnv=new Set(r.enviadas||[]); _fpHec=new Set(r.hechas||[]);
        fpEnsureMerge();
        return;
      }
      // KV vacía (primera vez): migrar lo que ya haya en localStorage; si no hay nada, sembrar los 33
      const lsEnv=getEnviadasLS(), lsHec=getHechasLS();
      if(lsEnv.size || lsHec.size){ _fpEnv=lsEnv; _fpHec=lsHec; }
      else { _fpEnv=new Set(SEED_ENVIADAS); _fpHec=new Set(); }
      fpEnsureMerge();
      await apiSave(KVKEY,{enviadas:[..._fpEnv],hechas:[..._fpHec]});
      return;
    }
    // fallback local (sin Worker)
    initSeed(); _fpEnv=getEnviadasLS(); _fpHec=getHechasLS(); fpEnsureMerge();
  }
  function applyLocal(cid,act,env,hec){
    if(act==='env'){ env.add(cid); hec.delete(cid); }
    else if(act==='unenv'){ env.delete(cid); }
    else if(act==='hecha'){ hec.add(cid); env.delete(cid); }
    else if(act==='unhecha'){ hec.delete(cid); }
  }
  async function fpPersist(cid,act){
    applyLocal(cid,act,_fpEnv,_fpHec);   // optimista (para que la UI responda ya)
    if(typeof API_AVAILABLE!=='undefined' && API_AVAILABLE){
      // read-modify-write: traigo lo último de KV, aplico mi cambio y subo (no piso a otros)
      const r = await apiLoad(KVKEY) || {enviadas:[],hechas:[]};
      const env=new Set(r.enviadas||[]), hec=new Set(r.hechas||[]);
      applyLocal(cid,act,env,hec);
      _fpEnv=env; _fpHec=hec;
      fpEnsureMerge();   // garantizar las de Carla aunque el KV haya devuelto una versión vieja
      await apiSave(KVKEY,{enviadas:[..._fpEnv],hechas:[..._fpHec]});
      draw();
    } else {
      localStorage.setItem(LS,JSON.stringify([..._fpEnv]));
      localStorage.setItem(LSH,JSON.stringify([..._fpHec]));
    }
  }
  function adminEmail(){
    let e=localStorage.getItem('fp_admin_email');
    if(!e){ e=(prompt('Email de administración (se guarda para las próximas):','')||'').trim(); if(e) localStorage.setItem('fp_admin_email',e); }
    return e;
  }
  function fpMail(cid){
    const r=(_rows||[]).find(x=>x.cid===cid); if(!r) return;
    const to=adminEmail(); if(!to) return;
    const subj=`Final para cargar · contrato ${r.contrato} · ${r.proveedor}`;
    const body=`Hola,\n\nPor favor cargar la final de compra del siguiente contrato:\n\nContrato: ${r.contrato}\nProveedor: ${r.proveedor}\nGrano: ${r.grano}\nCampaña: ${r.campana}\nEntregado: ${r.ent} tn\nLiquidado: ${r.liq} tn\nPendiente de liquidar: ${r.pend} tn\n\nGracias.`;
    window.location.href=`mailto:${to}?subject=${encodeURIComponent(subj)}&body=${encodeURIComponent(body)}`;
  }
  let _rows=null;
  function build(){
    if(_rows) return _rows;
    _rows=[];
    (PAYLOAD.compra||[]).forEach(c=>{
      if(!String(c.producto||'').trim().toLowerCase().startsWith('grano')) return;
      const est=String(c.estadoanulacion||'').toLowerCase();
      if(est.includes('anul') && !est.includes('no anul')) return;
      const ent=Number(c.cantidadentregada)||0, liq=Number(c.cantidadliquidada)||0;
      const pend=Math.max(0,ent-liq);
      // FINAL a hacer = grano LIQUIDADO: entregado > 0 y entregado pendiente de liquidar = 0.
      // Los que tienen pendiente > 0 (sin liquidar o parcial) NO van: todavía no se puede hacer la final.
      if(ent<=0.05 || liq<=0.05 || pend>0.05) return;
      const num=String(c.numerointerno||c.contrato||'');
      _rows.push({cid:num, contrato:num, proveedor:c.organizacion||'',
        grano:(c.producto||'').replace(/^Grano\s+/i,''), campana:c.campana||'', campS:campShort(c.campana),
        fecha:(c.fecha||'').slice(0,10), tn:liq });
    });
    return _rows;
  }
  // estado: hecha (marcada por vos) > enviada (a admin) > pendiente (liquidada, falta hacer la final)
  function estado(r,env,hec){ if(hec.has(r.cid)) return 'hecha'; if(env.has(r.cid)) return 'enviada'; return 'pendiente'; }
  let _fpPoll=null;
  function fpStartPolling(){
    if(_fpPoll || !(typeof API_AVAILABLE!=='undefined' && API_AVAILABLE)) return;
    _fpPoll=setInterval(async ()=>{
      const r=await apiLoad(KVKEY);
      if(r && (Array.isArray(r.enviadas)||Array.isArray(r.hechas))){
        const nEnv=new Set(r.enviadas||[]), nHec=new Set(r.hechas||[]);
        // garantizar las de Carla sobre lo que devolvió el KV (evita que una lectura vieja las borre)
        FP_MERGE_ENVIADAS.forEach(c=>{ const id=String(c); if(!nHec.has(id)) nEnv.add(id); });
        const a=[...nEnv].sort().join(','), b=[...nHec].sort().join(',');
        const pa=[...getEnviadas()].sort().join(','), pb=[...getHechas()].sort().join(',');
        if(a!==pa || b!==pb){ _fpEnv=nEnv; _fpHec=nHec; draw(); }
      }
    }, 15000);
  }
  function render(){
    if(!document.getElementById('fp-tabla')) return;
    // Estado inicial inmediato (localStorage) para NO quedar en blanco si la nube tarda/falla
    if(!_fpEnv){ _fpEnv=getEnviadasLS(); _fpHec=getHechasLS(); }
    const rows=build();
    const selG=document.getElementById('fp-grano'), selK=document.getElementById('fp-camp');
    if(selG && !selG.dataset.init){
      selG.dataset.init='1';
      [...new Set(rows.map(r=>r.grano))].sort().forEach(v=>selG.insertAdjacentHTML('beforeend',`<option value="${esc(v)}">${esc(v)}</option>`));
      const camps=[...new Set(rows.map(r=>r.campana).filter(Boolean))].sort().reverse();
      camps.forEach(v=>selK.insertAdjacentHTML('beforeend',`<option value="${esc(v)}">${esc(campShort(v))}</option>`));
      if(camps.length) selK.value=camps[0];   // default: campaña más reciente (evita ruido de viejas ya hechas)
      ['fp-estado','fp-grano','fp-camp'].forEach(id=>{const e=document.getElementById(id); if(e) e.addEventListener('change',draw);});
      const q=document.getElementById('fp-q'); if(q) q.addEventListener('input',draw);
      const rst=document.getElementById('fp-reset'); if(rst) rst.addEventListener('click',()=>{['fp-estado','fp-grano','fp-q'].forEach(id=>{const e=document.getElementById(id); if(e) e.value='';}); if(camps.length) selK.value=camps[0]; draw();});
    }
    draw();   // dibuja YA, con lo que haya
    // Luego traigo el estado compartido de la nube y redibujo (sin bloquear la vista)
    (async()=>{ try{ await fpLoadState(); fpStartPolling(); draw(); }catch(e){ console.warn('[finales] estado nube:',e); } })();
  }
  function draw(){
    const rows=build(), env=getEnviadas(), hec=getHechas();
    const ev=(document.getElementById('fp-estado')||{}).value||'';
    const gv=(document.getElementById('fp-grano')||{}).value||'';
    const kv=(document.getElementById('fp-camp')||{}).value||'';
    const qv=String((document.getElementById('fp-q')||{}).value||'').toLowerCase();
    const withEst=rows.map(r=>({...r,est:estado(r,env,hec)}));
    // el universo para KPIs respeta el filtro de campaña (para ver la cola de la campaña elegida)
    const uni=withEst.filter(r=>(!kv||r.campana===kv));
    const filt=uni.filter(r=>
      (!ev||r.est===ev) && (!gv||r.grano===gv) &&
      (!qv || String(r.proveedor).toLowerCase().includes(qv) || String(r.contrato).toLowerCase().includes(qv)));
    const pend=uni.filter(r=>r.est==='pendiente'), envd=uni.filter(r=>r.est==='enviada'), hech=uni.filter(r=>r.est==='hecha');
    const totTn=uni.reduce((s,r)=>s+r.tn,0), hechTn=hech.reduce((s,r)=>s+r.tn,0);
    const pct = totTn>0 ? (hechTn/totTn*100) : 0;
    const kp=document.getElementById('fp-kpis');
    if(kp) kp.innerHTML=[
      {lbl:'🔴 Final por hacer',val:n(pend.length),cls:'red',hint:n(pend.reduce((s,r)=>s+r.tn,0))+' tn'},
      {lbl:'🟡 Enviadas a admin',val:n(envd.length),cls:'orange',hint:n(envd.reduce((s,r)=>s+r.tn,0))+' tn en curso'},
      {lbl:'🟢 Hechas',val:n(hech.length),cls:'green',hint:n(hechTn)+' tn cargadas'},
      {lbl:'% finales hechas (por tn)',val:pct.toFixed(1)+'%',cls: pct>=99?'green':'',hint:n(hechTn)+' / '+n(totTn)+' tn'},
    ].map(k=>`<div class="kpi ${k.cls}"><div class="lbl">${k.lbl}</div><div class="val">${k.val}</div><div class="hint">${k.hint}</div></div>`).join('');
    // orden: pendiente(0) < enviada(1) < hecha(2); dentro por fecha asc (más viejo primero)
    const rank={pendiente:0,enviada:1,hecha:2};
    filt.sort((a,b)=> rank[a.est]-rank[b.est] || String(a.fecha).localeCompare(String(b.fecha)) || b.tn-a.tn);
    const chipEst={pendiente:'<span class="chip err">🔴 Por hacer</span>',enviada:'<span class="chip warn">🟡 Enviada</span>',hecha:'<span class="chip ok">🟢 Hecha</span>'};
    const cur=m=>(m==='Pesos'||m==='ARS'||m==='PESOS')?'$':'US$';
    function gastoCell(num){
      const fg=(PAYLOAD.finales_gastos||{})[num];
      if(!fg) return '<span style="color:var(--muted)">—</span>';
      if(!fg.por_tipo||!fg.por_tipo.length){
        return `<span style="color:#b45309" title="entregador: ${esc((fg.cerealeras||[]).join(', '))} · falta scrapear sus gastos">— falta extranet</span>`;
      }
      const byC={}; fg.por_tipo.forEach(t=>{byC[t.moneda]=(byC[t.moneda]||0)+t.importe;});
      const tot=Object.entries(byC).map(([mo,v])=>`${cur(mo)}${n(v)}`).join(' · ');
      const falta=fg.ctgs_sin_datos?` <span style="color:#b45309" title="${fg.ctgs_sin_datos} CTG de otros entregadores sin gastos scrapeados">+${fg.ctgs_sin_datos}?</span>`:'';
      return `<span class="fp-gasto-lnk" data-num="${esc(num)}" style="cursor:pointer;color:#15803d;font-weight:600" title="ver detalle de gastos">🔎 ${tot}${falta}</span>`;
    }
    const t=document.getElementById('fp-tabla');
    t.querySelector('thead').innerHTML='<tr><th>Estado</th><th>Contrato</th><th>Proveedor</th><th>Grano</th><th>Camp.</th><th style="text-align:right">Tn liq.</th><th>Fecha</th><th>Gastos a descontar</th><th>Acciones</th></tr>';
    t.querySelector('tbody').innerHTML = filt.map(r=>{
      let btn='';
      if(r.est==='pendiente') btn=`<button class="clear fp-btn" data-cid="${esc(r.cid)}" data-act="env" style="padding:3px 9px;font-size:11px">→ Enviar a admin</button> <button class="clear fp-btn" data-cid="${esc(r.cid)}" data-act="hecha" style="padding:3px 9px;font-size:11px">✓ Hecha</button>`;
      else if(r.est==='enviada') btn=`<button class="clear fp-btn" data-cid="${esc(r.cid)}" data-act="hecha" style="padding:3px 9px;font-size:11px">✓ Hecha</button> <button class="clear fp-btn" data-cid="${esc(r.cid)}" data-act="unenv" style="padding:3px 9px;font-size:11px">↩</button>`;
      else btn=`<button class="clear fp-btn" data-cid="${esc(r.cid)}" data-act="unhecha" style="padding:3px 9px;font-size:11px">↩ Reabrir</button>`;
      const bg = r.est==='pendiente'?'':(r.est==='enviada'?' style="background:#fffbeb"':' style="background:#f0fdf4"');
      return `<tr${bg}><td>${chipEst[r.est]}</td><td style="font-weight:600">${esc(r.contrato)}</td>`+
        `<td style="font-size:11px">${esc(r.proveedor)}</td><td style="font-size:11px">${esc(r.grano)}</td>`+
        `<td style="font-size:11px">${esc(r.campS)}</td>`+
        `<td style="text-align:right;font-weight:600">${n(r.tn)}</td>`+
        `<td style="font-size:11px">${esc(r.fecha)}</td>`+
        `<td style="font-size:11px">${gastoCell(r.contrato)}</td>`+
        `<td style="white-space:nowrap">${btn}</td></tr>`;
    }).join('') || '<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:16px">Sin contratos para ese filtro</td></tr>';
    t.querySelectorAll('.fp-btn').forEach(b=>b.addEventListener('click',()=>{
      const cid=b.dataset.cid, act=b.dataset.act;
      if(act==='env') fpMail(cid);
      fpPersist(cid,act);
      draw();
    }));
    // desplegar detalle de gastos por contrato
    t.querySelectorAll('.fp-gasto-lnk').forEach(el=>el.addEventListener('click',()=>{
      const tr=el.closest('tr'); const nx=tr.nextElementSibling;
      if(nx && nx.classList.contains('fp-gasto-det')){ nx.remove(); return; }
      const fg=(PAYLOAD.finales_gastos||{})[el.dataset.num]; if(!fg) return;
      const rows=(fg.por_tipo||[]).map(x=>`<tr><td style="padding:2px 8px">${esc(x.tipo)}</td><td style="padding:2px 8px;text-align:right;font-weight:600">${cur(x.moneda)}${n(x.importe)}</td><td style="padding:2px 8px;font-size:10px;color:var(--muted)">${esc(x.moneda)}</td></tr>`).join('');
      const nota=fg.ctgs_sin_datos?`<div style="font-size:11px;color:#b45309;margin-top:6px">⚠ ${fg.ctgs_sin_datos} CTG de otros entregadores (${esc((fg.cerealeras||[]).join(', '))}) sin gastos scrapeados aún.</div>`:'';
      const det=document.createElement('tr'); det.className='fp-gasto-det';
      det.innerHTML=`<td colspan="9" style="background:#f0fdf4;padding:10px 18px"><div style="font-weight:600;margin-bottom:4px">💰 Gastos a descontar · contrato ${esc(el.dataset.num)} <span style="font-weight:400;color:var(--muted)">(${fg.ctgs_con_gastos} CTG con datos de Cargill)</span></div><table class="tbl" style="margin:0;max-width:420px"><thead><tr><th>Concepto</th><th style="text-align:right">Importe</th><th>Mon.</th></tr></thead><tbody>${rows}</tbody></table>${nota}</td>`;
      tr.after(det);
    }));
    const meta=document.getElementById('fp-meta'); if(meta) meta.textContent=`${filt.length} contratos`;
  }
  document.querySelectorAll('[data-go-sub="cp-finales-pend"], .subtab[data-sub="cp-finales-pend"]')
    .forEach(a => a.addEventListener('click', () => setTimeout(render, 60)));
})();

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
  if(MB_OWNER_MODE){
    // Auto-save a Cloudflare KV via Worker (debounce 1.5s)
    if(API_AVAILABLE){
      apiSaveDebounced("bandeja_ehussen", () => MB_DATA, (state) => {
        const el = document.getElementById("mb-backup-info");
        if(!el) return;
        if(state === "pending") el.textContent = "✏️ guardando…";
        else if(state === "saving") el.textContent = "⏳ guardando en servidor…";
        else if(state === "saved") el.textContent = "✓ guardado " + new Date().toLocaleTimeString('es-AR',{hour:'2-digit',minute:'2-digit'});
        else if(state === "error") el.textContent = "⚠️ error guardando (queda en local)";
      });
    } else {
      mbAutoBackup();  // fallback al backup viejo via GitHub PAT
    }
  }
}

async function mbLoadInitial(){
  // 1) API (Cloudflare KV via Worker) — fuente canonica para el usuario logueado
  const fromApi = await apiLoad("bandeja_ehussen");
  if(Array.isArray(fromApi) && fromApi.length){
    MB_DATA = fromApi;
    try{ localStorage.setItem(MB_STORAGE_KEY, JSON.stringify(MB_DATA)); }catch(e){}
    return;
  }
  // 2) Repo (legacy / fallback)
  let fromRepo = null;
  try{
    const r = await fetch(`./${MB_REPO_PATH}?t=${Date.now()}`, {cache:"no-store"});
    if(r.ok) fromRepo = await r.json();
  } catch(e){}

  if(Array.isArray(fromRepo) && fromRepo.length){
    MB_DATA = fromRepo;
  } else {
    // 3) localStorage
    try{
      const ls = JSON.parse(localStorage.getItem(MB_STORAGE_KEY) || "null");
      if(Array.isArray(ls) && ls.length) MB_DATA = ls;
      else MB_DATA = JSON.parse(JSON.stringify(MB_DEFAULTS));
    } catch(e){ MB_DATA = JSON.parse(JSON.stringify(MB_DEFAULTS)); }
  }

  // Migracion: si la API esta lista pero vacia, pushear los datos para inicializarla
  if(API_AVAILABLE && Array.isArray(fromApi) && fromApi.length === 0 && MB_DATA.length > 0 && MB_OWNER_MODE){
    apiSave("bandeja_ehussen", MB_DATA).then(ok => {
      if(ok) console.log("[migracion] localStorage/repo → KV (bandeja):", MB_DATA.length, "cards");
    });
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

/* Actualizar manual: pulla las cards nuevas del repo (additive merge — no toca lo editado local) */
const mbRefreshBtn = document.getElementById("mb-refresh");
if(mbRefreshBtn) mbRefreshBtn.addEventListener("click", async () => {
  mbRefreshBtn.disabled = true;
  mbRefreshBtn.textContent = "Actualizando…";
  let added = 0, updated = 0;
  try{
    const r = await fetch(`./${MB_REPO_PATH}?t=${Date.now()}`, {cache:"no-store"});
    if(r.ok){
      const fromRepo = await r.json();
      if(Array.isArray(fromRepo)){
        const byId = new Map(MB_DATA.map(c => [c.id, c]));
        fromRepo.forEach(rc => {
          const local = byId.get(rc.id);
          if(!local){
            MB_DATA.push(rc);
            added++;
          } else {
            // Card ya existe local. Si NO tiene edits del usuario (nota+accion iguales al repo), refresco metadata.
            // Si tiene edits, no la toco.
            const localEdited = (local.nota || "").trim() !== (rc.nota || "").trim()
                              || (local.accion || "").trim() !== (rc.accion || "").trim();
            if(!localEdited){
              // refrescar metadata canonica (urgencia, fecha, etc) sin perder estado/fijado del usuario
              ["asunto","remitente","fecha","urgencia","categoria","bandeja","outlook"].forEach(k => {
                if(rc[k] !== undefined && rc[k] !== local[k]){ local[k] = rc[k]; updated++; }
              });
            }
          }
        });
        if(added || updated){
          mbSave();
          mbRender();
        }
        mbRefreshBtn.textContent = added > 0 ? `✓ +${added} nuevas` : (updated > 0 ? "✓ Metadata actualizada" : "✓ Al día");
      } else {
        mbRefreshBtn.textContent = "✗ Repo sin datos";
      }
    } else if(r.status === 404){
      mbRefreshBtn.textContent = "ℹ Aún sin backup en repo";
    } else {
      mbRefreshBtn.textContent = "✗ Error " + r.status;
    }
  } catch(e){
    mbRefreshBtn.textContent = "✗ Sin conexión";
  }
  setTimeout(() => {
    mbRefreshBtn.textContent = "🔄 Actualizar bandeja";
    mbRefreshBtn.disabled = false;
  }, 2800);
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
_DW_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")

def dw_query(table_name: str, date_cols: set | None = None) -> list[dict] | None:
    """Query del DW Postgres. Devuelve filas con keys lowercase, strings convertidos a int/float
    cuando parecen numericos, fechas yyyy-mm-dd HH:MM:SS.SSS recortadas a yyyy-mm-dd,
    empty strings -> None. Devuelve None si DW no esta disponible."""
    if not all(os.environ.get(k) for k in ("FNN_DW_HOST", "FNN_DW_USER", "FNN_DW_PASS")):
        return None
    try:
        import psycopg2, psycopg2.extras
    except ImportError:
        print(f"    [!] psycopg2 no instalado — pip install psycopg2-binary")
        return None
    try:
        cn = psycopg2.connect(
            host=os.environ["FNN_DW_HOST"],
            dbname=os.environ.get("FNN_DW_DB", "finnegansbi"),
            user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
            port=int(os.environ.get("FNN_DW_PORT", "5432")),
            sslmode="require", connect_timeout=20,
        )
        cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT * FROM public.{table_name}")
        raw = cur.fetchall()
        cn.close()
    except Exception as e:
        print(f"    [!] DW {table_name} error: {type(e).__name__}: {str(e)[:120]}")
        return None
    date_cols = date_cols or set()
    out = []
    for r in raw:
        nr = {}
        for k, v in r.items():
            if v == "" or v is None:
                nr[k] = None
            elif k in date_cols and isinstance(v, str):
                nr[k] = v[:10] if len(v) >= 10 else v
            elif isinstance(v, str) and _DW_NUM_RE.match(v):
                # Numerico: parsear
                try:
                    nr[k] = float(v) if "." in v else int(v)
                except ValueError:
                    nr[k] = v
            else:
                nr[k] = v
        # alias campana <- cosecha si falta (para compat HTML)
        if "campana" not in nr and nr.get("cosecha"):
            nr["campana"] = nr["cosecha"]
        out.append(nr)
    return out


def main() -> int:
    # Intentar primero el DATAWAREHOUSE Postgres. Si no esta disponible o falla,
    # se cae a la API REST (codigo original). El DW es la fuente preferida porque:
    # - es read-only (sin riesgo)
    # - tiene mas detalle (factor, 1116a, fletes, etc.)
    # - no tiene rate limits
    USE_DW = bool(os.environ.get("FNN_DW_HOST") and os.environ.get("FNN_DW_USER") and os.environ.get("FNN_DW_PASS"))
    if USE_DW:
        print(f"[+] DW Postgres disponible (FNN_DW_HOST seteado)", flush=True)

    print(f"[+] Autenticando contra API de Finnegans ...", flush=True)
    api.get_token()
    print(f"[+] Token OK", flush=True)

    counts = {"compra": 0, "venta": 0, "posicion": 0}
    pilot_rows: list[dict] = []
    compra_rows: list[dict] = []
    pilot_norm: list[dict] = []
    compra_norm: list[dict] = []
    used_dw_pilot = False
    used_dw_compra = False

    DATE_COLS_CONTRATOS = {"fecha","fechaminentrega","fechamaxentrega","fechaestliq"}

    # Contratos COMPRA y VENTA: SIEMPRE del reporte REST de Finnegans (NO del DW).
    # El DW traía el 'entregado' desactualizado (no coincidía con el reporte de Finnegans;
    # ej. soja 25/26 DW=33.482 vs reporte=32.893). El REST da entregado/pendiente exactos.
    # Dejando used_dw_* en False, el fallback REST de abajo baja compra y venta (sin anulados).
    print(f"[+] Contratos COMPRA/VENTA: usando reporte REST de Finnegans (no DW)", flush=True)

    # Fallback API REST para los que no se pudieron bajar del DW
    if not (used_dw_pilot and used_dw_compra):
        print(f"[+] Fallback API REST para contratos faltantes...", flush=True)
        for label, endpoint, params, tab in DATASETS:
            if endpoint == PILOT_ENDPOINT and used_dw_pilot: continue
            if endpoint == COMPRA_ENDPOINT and used_dw_compra: continue
            print(f"  [{tab:<8}] {label:<42}  -> GET {endpoint}", flush=True)
            try:
                data = api.call(endpoint, params)
            except Exception as e:
                print(f"    [!] ERROR: {e}")
                continue
            if not isinstance(data, list):
                data = []
            raw_n = len(data)
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
            elif endpoint == COMPRA_ENDPOINT:
                compra_rows = data
        if not used_dw_pilot:
            pilot_norm = normalize_pilot(pilot_rows)
        if not used_dw_compra:
            compra_norm = normalize_pilot(compra_rows)

    # Filtrar contratos ANULADOS (la API trae todos; el cierre solo cuenta los No Anulado)
    def _no_anul(r):
        v = (r.get("estadoanulacion") or "").strip().lower()
        return v != "anulado"
    _ant_pilot = len(pilot_norm); _ant_compra = len(compra_norm)
    pilot_norm  = [r for r in pilot_norm  if _no_anul(r)]
    compra_norm = [r for r in compra_norm if _no_anul(r)]
    print(f"[+] Filtro Anulado: venta {_ant_pilot}->{len(pilot_norm)}  compra {_ant_compra}->{len(compra_norm)}")

    # Composicion de Saldos para modulo Canjes — usamos API REST con getCurrentDate
    # (el DW tiene historia completa de saldos, no filtra al snapshot actual; no nos sirve)
    print(f"\n[+] Bajando Composicion Saldo Cliente (snapshot actual via API REST)...", flush=True)
    saldos_raw = api.call("/reports/composicionSaldoCliente",
                          {"PARAMWEBREPORT_fecha": "getCurrentDate"})
    if not isinstance(saldos_raw, list):
        saldos_raw = []
    saldos_norm = [{k.lower(): v for k, v in r.items()} for r in saldos_raw]
    print(f"    -> {len(saldos_norm)} filas")
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

    # Trazabilidad de Compra desde DATAWAREHOUSE POSTGRES (no API REST).
    # FUENTE PRIMARIA: traslado_venta_granos_carta_porte_cruce (mas actualizada, sync OK)
    # ENRIQUECIDA con: traslado_de_granos (factor, certif 1116a, fletes, transportista — cuando existan)
    print(f"\n[+] Bajando Trazabilidad desde datawarehouse...", flush=True)
    traza_list = []
    dw_host = os.environ.get("FNN_DW_HOST")
    dw_user = os.environ.get("FNN_DW_USER")
    dw_pass = os.environ.get("FNN_DW_PASS")
    if dw_host and dw_user and dw_pass:
        try:
            import psycopg2, psycopg2.extras
            cn = psycopg2.connect(host=dw_host, dbname=os.environ.get("FNN_DW_DB","finnegansbi"),
                                   user=dw_user, password=dw_pass,
                                   port=int(os.environ.get("FNN_DW_PORT","5432")),
                                   sslmode="require", connect_timeout=20)
            cr = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # 1) Trayendo la tabla CRUCE (primaria, mas actualizada)
            cr.execute("""
                SELECT numerodocumentoadicional AS ctg,
                       numerodocumento          AS cp,
                       fecha, grano AS producto,
                       transaccionsubtiponombre AS subtipo,
                       organizacionnombre       AS organizacion,
                       nombrecontrato           AS contrato,
                       pesoneto, pesoentregador,
                       descripcion, destino,
                       documento, estado_ctg,
                       fechaarribo, fechadescarga, fechapartida, horapartida,
                       cosecha,
                       provinciaorigen, provinciadestino,
                       localidadorigen, localidaddestino,
                       corredorprimario, corredorsecundario,
                       intermediarioflete, pagadorflete,
                       representante, titular
                FROM public.agronasajasrl_traslado_venta_granos_carta_porte_cruce
                WHERE numerodocumentoadicional IS NOT NULL
                  AND numerodocumentoadicional != ''
            """)
            cruce_rows = cr.fetchall()
            print(f"    -> {len(cruce_rows)} filas raw tabla CRUCE")

            # 2) Enriquecer con traslado_de_granos (puede no tener todos los CTGs si la sync fallo)
            cr.execute("""
                SELECT numerodocumentoadicional AS ctg,
                       transaccionsubtiponombre AS subtipo, operaciontipo,
                       factor, certificado1116a, comprobantecertificado1116a,
                       certificadort, comprobantecertificadort,
                       transportista, chofer,
                       tarifatransporte, importetransporte, cantidadkilometros,
                       establecimiento,
                       destinatario,
                       transaccionid, estado, codigocancelacionctg
                FROM public.agronasajasrl_traslado_de_granos
                WHERE numerodocumentoadicional IS NOT NULL
                  AND numerodocumentoadicional != ''
            """)
            tg_rows = cr.fetchall()
            cn.close()
            print(f"    -> {len(tg_rows)} filas raw tabla traslado_de_granos (enriquecimiento)")

            # Indexar enriquecimiento por (ctg, operaciontipo)
            tg_by_ctg = {}
            for r in tg_rows:
                ctg = (r.get("ctg") or "").strip()
                opt = (r.get("operaciontipo") or "").strip()
                key = (ctg, opt)
                tg_by_ctg[key] = r

            # Construir dw_rows fusionando: CRUCE como base + enrichment cuando hay match
            # En CRUCE no tenemos operaciontipo, lo derivamos del subtipo
            def derive_optipo(subtipo):
                s = (subtipo or "").upper()
                if "COMPRA" in s or "RECEPC" in s: return "Compra"
                if "VENTA" in s or "TRASLADO" in s and "VTA" not in s: return "Venta"  # heuristica
                if "VTA" in s: return "Venta"
                return ""
            dw_rows = []
            for r in cruce_rows:
                d = dict(r)
                ctg = (d.get("ctg") or "").strip()
                opt = derive_optipo(d.get("subtipo"))
                d["operaciontipo"] = opt
                # Enriquecer con traslado_de_granos si hay match
                enr = tg_by_ctg.get((ctg, opt)) or tg_by_ctg.get((ctg, ""))
                if enr:
                    for k in ("factor","certificado1116a","comprobantecertificado1116a",
                              "certificadort","comprobantecertificadort",
                              "transportista","chofer",
                              "tarifatransporte","importetransporte","cantidadkilometros",
                              "establecimiento","destinatario","transaccionid","codigocancelacionctg"):
                        if enr.get(k): d[k] = enr[k]
                # Tambien usar pesonetosinmermas si vino del enrichment
                # (no esta en CRUCE)
                if enr and enr.get("pesonetosinmermas"): d["pesonetosinmermas"] = enr["pesonetosinmermas"]
                dw_rows.append(d)
            print(f"    -> {len(dw_rows)} filas fusionadas (CRUCE primaria + traslado_de_granos enrich)")

            def _safe_float(v):
                """Convierte a float manejando 'NULL', None, '', y strings sin float."""
                if v is None: return 0.0
                if isinstance(v, (int, float)): return float(v)
                s = str(v).strip()
                if not s or s.upper() == "NULL": return 0.0
                try: return float(s)
                except ValueError: return 0.0

            traza_by_ctg = {}
            for r in dw_rows:
                ctg = (r.get("ctg") or "").strip()
                if not ctg: continue
                opt = (r.get("operaciontipo") or "").strip()
                contrato = (r.get("contrato") or "").strip()
                org = (r.get("organizacion") or "").strip()
                titular = (r.get("titular") or "").strip()
                if ctg not in traza_by_ctg:
                    traza_by_ctg[ctg] = {
                        "ctg": ctg, "cp": r.get("cp"),
                        "fecha": (r.get("fecha") or "")[:10],
                        "producto": r.get("producto"),
                        "peso_neto": _safe_float(r.get("pesoneto")),
                        "peso_neto_sin_mermas": _safe_float(r.get("pesonetosinmermas")),
                        "peso_entregador": _safe_float(r.get("pesoentregador")),
                        # Campos de traslado_de_granos (enrichment, pueden ser None)
                        "factor": r.get("factor"),
                        "certificado_1116a": r.get("certificado1116a"),
                        "comprobante_1116a": r.get("comprobantecertificado1116a"),
                        "certificado_rt": r.get("certificadort"),
                        "comprobante_rt": r.get("comprobantecertificadort"),
                        "establecimiento": r.get("establecimiento"),
                        "transportista": r.get("transportista"),
                        "chofer": r.get("chofer"),
                        "tarifa_transporte": r.get("tarifatransporte"),
                        "importe_transporte": r.get("importetransporte"),
                        "kilometros": r.get("cantidadkilometros"),
                        # Campos de la tabla CRUCE (siempre disponibles)
                        "titular": titular or None,
                        "representante": r.get("representante"),
                        "pagador_flete": r.get("pagadorflete"),
                        "intermediario_flete": r.get("intermediarioflete"),
                        "documento_cv": r.get("documento"),
                        "descripcion": r.get("descripcion"),
                        "destinatario": r.get("destinatario") or r.get("destino"),
                        "cosecha": r.get("cosecha"),
                        "estado_ctg": r.get("estado_ctg") or r.get("estado"),
                        "fecha_arribo": (r.get("fechaarribo") or "")[:10],
                        "fecha_descarga": (r.get("fechadescarga") or "")[:10],
                        "fecha_partida": (r.get("fechapartida") or "")[:10],
                        "hora_partida": r.get("horapartida"),
                        "provincia_origen": r.get("provinciaorigen"),
                        "provincia_destino": r.get("provinciadestino"),
                        "localidad_origen": r.get("localidadorigen"),
                        "localidad_destino": r.get("localidaddestino"),
                        "corredor_primario": r.get("corredorprimario"),
                        "corredor_secundario": r.get("corredorsecundario"),
                        # Datos por lado (poblados abajo)
                        "entregador": None, "cerealera": None,
                        "contrato_compra": None, "contrato_venta": None,
                        "subtipo_compra": None, "subtipo_venta": None,
                        "transaccion_compra": None, "transaccion_venta": None,
                    }
                item = traza_by_ctg[ctg]
                # operaciontipo es la verdad — Compra = entrega del productor, Venta = traslado a cerealera
                if opt == "Compra":
                    if contrato and not item["contrato_compra"]:
                        item["contrato_compra"] = contrato
                    if org and not item["entregador"]:
                        item["entregador"] = org
                    if r.get("subtipo") and not item["subtipo_compra"]:
                        item["subtipo_compra"] = r.get("subtipo")
                    if r.get("transaccionid") and not item["transaccion_compra"]:
                        item["transaccion_compra"] = r.get("transaccionid")
                elif opt == "Venta":
                    if contrato and not item["contrato_venta"]:
                        item["contrato_venta"] = contrato
                    if org and not item["cerealera"]:
                        item["cerealera"] = org
                    if r.get("subtipo") and not item["subtipo_venta"]:
                        item["subtipo_venta"] = r.get("subtipo")
                    if r.get("transaccionid") and not item["transaccion_venta"]:
                        item["transaccion_venta"] = r.get("transaccionid")
                    if r.get("destinatario") and not item.get("destinatario"):
                        item["destinatario"] = r.get("destinatario")

                # Asegurar entregador: si no se detectó por lado COMPRA, usar TITULAR (siempre es el productor)
                if not item.get("entregador") and titular:
                    item["entregador"] = titular

            traza_list = list(traza_by_ctg.values())
            sin_compra = sum(1 for c in traza_list if not c.get("contrato_compra"))
            sin_venta  = sum(1 for c in traza_list if not c.get("contrato_venta"))
            con_ambos  = sum(1 for c in traza_list if c.get("contrato_compra") and c.get("contrato_venta"))
            print(f"    -> {len(traza_list)} CTGs unicos: {con_ambos} con ambos contratos, {sin_compra} sin compra, {sin_venta} sin venta")
        except Exception as e:
            print(f"    [!] error DW: {type(e).__name__}: {str(e)[:200]}")
            traza_list = []
    else:
        print("    [.] FNN_DW_HOST/USER/PASS no seteados, fallback a INFORMETRASGRNAPI con heuristica")

    # Fallback: si no se pudo bajar del DW, usamos la API REST con heuristica
    if not traza_list:
        try:
            traza_raw = api.call("/reports/INFORMETRASGRNAPI", {
                "PARAMFechaDesde": "2024-01-01",
                "PARAMFechaHasta": "2030-12-31",
            })
        except Exception as e:
            print(f"    [!] error INFORMETRASGRNAPI fallback: {e}")
            traza_raw = []
        if not isinstance(traza_raw, list):
            traza_raw = []
        print(f"    -> {len(traza_raw)} filas raw (API REST fallback)")

        def _traza_side(row):
            org = (row.get("ORGANIZACION") or "").strip().upper()
            sol = (row.get("SOLICITANTE") or "").strip().upper()
            if org and sol:
                return "compra" if org == sol else "venta"
            c = (row.get("CONTRATO") or "").upper()
            if c.startswith("COMP") or "CPRA" in c: return "compra"
            if c.startswith("VEN") or "VTA" in c:   return "venta"
            return None

        traza_by_ctg = {}
        for r in traza_raw:
            ctg = r.get("CTG")
            if not ctg: continue
            if ctg not in traza_by_ctg:
                traza_by_ctg[ctg] = {
                    "ctg": ctg, "cp": r.get("NUMERODOCUMENTO"),
                    "fecha": r.get("FECHA"), "producto": r.get("PRODUCTO"),
                    "peso_neto": float(r.get("PESONETO") or 0),
                    "peso_neto_sin_mermas": float(r.get("PESONETOSINMERMAS") or 0),
                    "peso_entregador": 0,
                    "entregador": None, "cerealera": None,
                    "contrato_compra": None, "contrato_venta": None,
                    "factor": None, "certificado_1116a": None, "comprobante_1116a": None,
                    "certificado_rt": None, "comprobante_rt": None,
                    "destinatario": None, "cosecha": None,
                    "transportista": None, "representante": None, "chofer": None,
                    "tarifa_transporte": None, "importe_transporte": None, "kilometros": None,
                    "fecha_arribo": None, "fecha_descarga": None,
                    "provincia_origen": None, "provincia_destino": None,
                    "localidad_origen": None, "localidad_destino": None,
                    "establecimiento": None,
                    "corredor_primario": None, "corredor_secundario": None,
                    "subtipo_compra": None, "subtipo_venta": None,
                    "transaccion_compra": None, "transaccion_venta": None,
                    "estado_ctg": None,
                }
            item = traza_by_ctg[ctg]
            contrato = (r.get("CONTRATO") or "").strip()
            org = r.get("ORGANIZACION")
            side = _traza_side(r)
            if side == "compra":
                if contrato and not item["contrato_compra"]: item["contrato_compra"] = contrato
                if org and not item["entregador"]: item["entregador"] = org
            elif side == "venta":
                if contrato and not item["contrato_venta"]: item["contrato_venta"] = contrato
                if org and not item["cerealera"]: item["cerealera"] = org

        traza_list = list(traza_by_ctg.values())
        print(f"    -> {len(traza_list)} CTGs unicos via fallback")

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

    # Data scrapeada de Cargill GPS (movements + payments + invoices + details)
    # Se actualiza con: py scripts/cargill_api_final.py + cargill_download_details.py
    print(f"\n[+] Cargando data Cargill GPS (si existe)...", flush=True)
    cargill_movements = []
    cargill_invoices = []
    cargill_payments = []
    cargill_details = {}  # dict por movementNumber → detalle (qualityAnalysis + services)
    cargill_dir = Path(__file__).resolve().parent / "data" / "cargill"
    for fname, target in [("movements.json", "cargill_movements"),
                           ("invoices.json", "cargill_invoices"),
                           ("payments.json", "cargill_payments"),
                           ("movements_detail.json", "cargill_details")]:
        fp = cargill_dir / fname
        if fp.exists():
            try:
                rows = json.loads(fp.read_text(encoding="utf-8"))
                if target == "cargill_movements": cargill_movements = rows
                elif target == "cargill_invoices": cargill_invoices = rows
                elif target == "cargill_payments": cargill_payments = rows
                elif target == "cargill_details": cargill_details = rows
                ncount = len(rows) if isinstance(rows, list) else len(rows.keys())
                print(f"    -> {fname}: {ncount} entradas")
            except Exception as e:
                print(f"    [!] {fname} error: {e}")
        else:
            print(f"    [.] {fp} no existe (correr scripts/cargill_api_final.py + cargill_download_details.py)")

    # ---- Gastos a descontar por contrato de compra (para Finales Pendientes) ----
    # Montos reales por CTG desde Cargill (movements_detail.services: secada/flete/comisión/etc).
    # Otros entregadores: falta scrapear sus gastos -> se marca cuántos CTG quedan sin datos.
    print(f"\n[+] Calculando gastos a descontar por contrato (Cargill services)...", flush=True)
    def _ctgn(x): return re.sub(r"\D", "", str(x or "")).lstrip("0")
    def _cnum(s):
        m = re.search(r"-\s*(\d+)", str(s or "")); return m.group(1) if m else None
    _ctg_serv = {}   # ctg -> [{name, importe, moneda}]
    _cd = cargill_details.values() if isinstance(cargill_details, dict) else (cargill_details or [])
    for mov in _cd:
        m = re.search(r"(\d{11})$", str(mov.get("legalDocument") or ""))
        if not m: continue
        ctg = m.group(1).lstrip("0")
        for s in (mov.get("services") or []):
            name = str(s.get("serviceName") or "").strip()
            if not name or re.match(r"^\d{4}-\d\d-\d\d", name): continue
            cur = s.get("currencyCode") or s.get("billingCurrency") or ""
            # importe: el calculationType ya trae "precio x cantidad TN" (o "x UN").
            # Uso esos números (netWeight viene en KG, no sirve para el cálculo).
            calc = str(s.get("calculationType") or "")
            nums = re.findall(r"\d+(?:[.,]\d+)?", calc)
            if "TN" in calc.upper() and len(nums) >= 2:
                importe = round(float(nums[0].replace(",", ".")) * float(nums[1].replace(",", ".")), 2)
            else:
                try: importe = round(float(str(s.get("unitPrice") or "0").replace(",", ".")), 2)
                except Exception: importe = 0.0
            _ctg_serv.setdefault(ctg, []).append({"name": name, "importe": importe, "moneda": cur})
    _fg = {}   # contrato_num -> agregado
    for r in traza_list:
        num = _cnum(r.get("contrato_compra"))
        ctg = _ctgn(r.get("ctg"))
        if not num or not ctg: continue
        e = _fg.setdefault(num, {"ctgs": [], "cerealeras": set(), "por_tipo": {}, "sin_datos": 0})
        cer = (r.get("cerealera") or r.get("destinatario") or "")[:26]
        if cer: e["cerealeras"].add(cer)
        serv = _ctg_serv.get(ctg)
        if serv:
            e["ctgs"].append({"ctg": ctg, "cerealera": cer, "gastos": serv})
            for g in serv:
                k = (g["name"], g["moneda"])
                e["por_tipo"][k] = round(e["por_tipo"].get(k, 0.0) + g["importe"], 2)
        else:
            e["sin_datos"] += 1
    finales_gastos = {}
    for num, e in _fg.items():
        finales_gastos[num] = {
            "ctgs": e["ctgs"], "cerealeras": sorted(e["cerealeras"]),
            "por_tipo": [{"tipo": k[0], "moneda": k[1], "importe": v} for k, v in sorted(e["por_tipo"].items())],
            "ctgs_con_gastos": len(e["ctgs"]), "ctgs_sin_datos": e["sin_datos"],
        }
    _con = sum(1 for v in finales_gastos.values() if v["ctgs_con_gastos"])
    print(f"    -> {len(finales_gastos)} contratos con CTG en traza · {_con} con gastos de Cargill")

    # Data LDC (Louis Dreyfus) - mildc.com/webportal
    # Se actualiza con: py scripts/ldc_fetch_all.py
    print(f"\n[+] Cargando data LDC (si existe)...", flush=True)
    ldc_settlements = []
    ldc_fixations = []
    ldc_ctgs = []  # CTGs LDC desde el DW
    ldc_dir = Path(__file__).resolve().parent / "data" / "ldc"
    for fname, target in [("settlements.json", "ldc_settlements"),
                           ("fixations.json", "ldc_fixations"),
                           ("ldc_ctgs.json", "ldc_ctgs")]:
        fp = ldc_dir / fname
        if fp.exists():
            try:
                rows = json.loads(fp.read_text(encoding="utf-8"))
                # Si tiene estructura {List: [...]} extraer la lista
                if isinstance(rows, dict) and "List" in rows: rows = rows["List"]
                if target == "ldc_settlements": ldc_settlements = rows
                elif target == "ldc_fixations": ldc_fixations = rows
                elif target == "ldc_ctgs": ldc_ctgs = rows
                ncount = len(rows) if isinstance(rows, (list, dict)) else 0
                print(f"    -> {fname}: {ncount} entradas")
            except Exception as e:
                print(f"    [!] {fname} error: {e}")

    # Data ACA (Asociacion de Cooperativas Argentinas) - SÓLO datos DW
    # El portal acabase.com.ar con la cuenta 'agronasaja' no expone operaciones
    print(f"\n[+] Cargando data ACA (si existe)...", flush=True)
    aca_ctgs = []
    aca_dir = Path(__file__).resolve().parent / "data" / "aca"
    fp = aca_dir / "aca_ctgs.json"
    if fp.exists():
        try:
            aca_ctgs = json.loads(fp.read_text(encoding="utf-8"))
            print(f"    -> aca_ctgs.json: {len(aca_ctgs)} entradas")
        except Exception as e:
            print(f"    [!] aca_ctgs.json error: {e}")

    # Data FYO (Futuros y Opciones) - solo DW (portal requiere 2FA email)
    print(f"\n[+] Cargando data FYO (si existe)...", flush=True)
    fyo_ctgs = []
    fyo_dir = Path(__file__).resolve().parent / "data" / "fyo"
    fp = fyo_dir / "fyo_ctgs.json"
    if fp.exists():
        try:
            fyo_ctgs = json.loads(fp.read_text(encoding="utf-8"))
            print(f"    -> fyo_ctgs.json: {len(fyo_ctgs)} entradas")
        except Exception as e:
            print(f"    [!] fyo_ctgs.json: {e}")

    # Data Intagro - sólo DW (credencial 'agronasaja' rechazada por portal.intagro.com — necesita email)
    # Intagro = Argentrading (misma empresa, mismo sistema)
    print(f"\n[+] Cargando data Intagro (si existe)...", flush=True)
    intagro_ctgs = []
    intagro_dir = Path(__file__).resolve().parent / "data" / "intagro"
    fp = intagro_dir / "intagro_ctgs.json"
    if fp.exists():
        try:
            intagro_ctgs = json.loads(fp.read_text(encoding="utf-8"))
            print(f"    -> intagro_ctgs.json: {len(intagro_ctgs)} entradas")
        except Exception as e:
            print(f"    [!] intagro_ctgs.json: {e}")

    # Data Bunge - sólo DW (portal con CAPTCHA, scraping no automatizable)
    print(f"\n[+] Cargando data Bunge (si existe)...", flush=True)
    bunge_ctgs = []
    bunge_dir = Path(__file__).resolve().parent / "data" / "bunge"
    fp = bunge_dir / "bunge_ctgs.json"
    if fp.exists():
        try:
            bunge_ctgs = json.loads(fp.read_text(encoding="utf-8"))
            print(f"    -> bunge_ctgs.json: {len(bunge_ctgs)} entradas")
        except Exception as e:
            print(f"    [!] bunge_ctgs.json: {e}")

    # Data COFCO - sólo DW (portal SAP Fiori pendiente login)
    print(f"\n[+] Cargando data COFCO (si existe)...", flush=True)
    cofco_ctgs = []
    cofco_dir = Path(__file__).resolve().parent / "data" / "cofco"
    fp = cofco_dir / "cofco_ctgs.json"
    if fp.exists():
        try:
            cofco_ctgs = json.loads(fp.read_text(encoding="utf-8"))
            print(f"    -> cofco_ctgs.json: {len(cofco_ctgs)} entradas")
        except Exception as e:
            print(f"    [!] cofco_ctgs.json: {e}")

    # Data Allaria (corredor con 292 CTGs DW + posicion campaña + cuenta corriente)
    print(f"\n[+] Cargando data Allaria (si existe)...", flush=True)
    allaria_ctgs = []
    allaria_mercaderias = []
    allaria_cuenta_corriente = {}
    allaria_dir = Path(__file__).resolve().parent / "data" / "allaria"
    for fname, target in [("allaria_ctgs.json", "ctgs"),
                           ("mercaderias.json", "mercaderias"),
                           ("cuenta_corriente.json", "cuenta_corriente")]:
        fp = allaria_dir / fname
        if fp.exists():
            try:
                rows = json.loads(fp.read_text(encoding="utf-8"))
                if target == "ctgs": allaria_ctgs = rows
                elif target == "mercaderias": allaria_mercaderias = rows
                elif target == "cuenta_corriente": allaria_cuenta_corriente = rows
                n = len(rows) if isinstance(rows, list) else len(rows.keys()) if isinstance(rows, dict) else 0
                print(f"    -> {fname}: {n} entradas")
            except Exception as e:
                print(f"    [!] {fname}: {e}")

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

    # Liquidaciones VENTA (DW) — para matchear con CTGs via COE en Trazabilidad
    print(f"\n[+] Bajando Liquidaciones VENTA desde DW...", flush=True)
    liquidaciones_dw = []
    liquidaciones_secu_dw = []
    if USE_DW:
        try:
            import psycopg2, psycopg2.extras
            cn = psycopg2.connect(host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
                                  user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
                                  port=5432, sslmode="require", connect_timeout=20)
            cr = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # 2231 liquidaciones venta (primaria + secundaria) con COE
            cr.execute("""SELECT transaccionsubtiponombre, fecha, documento, numerodocumento,
                                 descripcion, organizacionnombre, fechacomprobante, estado,
                                 tipoliquidacion, grano, corredor, numerocoe
                          FROM public.agronasajasrl_liquidacion_venta_granos
                          WHERE numerocoe IS NOT NULL AND numerocoe != ''""")
            for r in cr.fetchall():
                d = {}
                for k, v in r.items():
                    if v == "" or v is None: d[k] = None
                    elif k in ("fecha","fechacomprobante") and isinstance(v, str): d[k] = v[:10]
                    else: d[k] = v
                liquidaciones_dw.append(d)
            print(f"    -> {len(liquidaciones_dw)} liquidaciones venta con COE")
            # 63 secundarias con importes
            cr.execute("""SELECT transaccionsubtiponombre, fecha, documento, numerodocumento,
                                 descripcion, organizacionnombre, fechacomprobante, estado,
                                 tipoliquidacion, grano, corredor, numerocoe,
                                 numerodocumentoadicional, numerocontratointermediario, transaccionid,
                                 importegravado, importeotros, importetotal
                          FROM public.agronasajasrl_liquidacionventagranos
                          WHERE numerocoe IS NOT NULL AND numerocoe != ''""")
            for r in cr.fetchall():
                d = {}
                for k, v in r.items():
                    if v == "" or v is None: d[k] = None
                    elif k in ("fecha","fechacomprobante") and isinstance(v, str): d[k] = v[:10]
                    elif isinstance(v, str) and _DW_NUM_RE.match(v):
                        try: d[k] = float(v) if "." in v else int(v)
                        except: d[k] = v
                    else: d[k] = v
                liquidaciones_secu_dw.append(d)
            print(f"    -> {len(liquidaciones_secu_dw)} liquidaciones secundarias con importes")
            cn.close()
        except Exception as e:
            print(f"    [!] error DW liquidaciones: {e}")

    # Stock por Deposito -> categorizar por tipo de deposito y agregar por producto (kg -> tn)
    # DW Postgres primero, fallback a API REST
    print(f"\n[+] Bajando Stock por Deposito (DW primero)...", flush=True)
    stock_silo, stock_silobolsa, stock_bolsas, stock_descarte = {}, {}, {}, {}
    stock_detalle = {}   # {producto: [{dep, cat, tn}]} para el drill-down
    try:
        stock_raw = None
        if USE_DW:
            dw_stock = dw_query("agronasajasrl_reporte_stock_por_deposito")
            if dw_stock is not None:
                # En DW las columnas son lowercase
                stock_raw = [{"DEPOSITO": r.get("deposito"), "PRODUCTO": r.get("producto"), "CANTIDAD1": r.get("cantidad1")} for r in dw_stock]
                print(f"    -> {len(stock_raw)} filas desde DW")
        if stock_raw is None:
            stock_raw = api.call("/reports/USR_RESSTOCKDEP", {"PARAMWEBREPORT_fecha":"getCurrentDate"})
            if not isinstance(stock_raw, list): stock_raw = []
            print(f"    -> {len(stock_raw)} filas desde API REST (fallback)")

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
        # detalle por deposito (para el drill-down de la Posicion Granaria):
        #   {producto: [{"dep": nombre, "cat": SILO|SILOBOLSA|BOLSAS, "tn": x}, ...]}
        stock_det_acum = {}   # (producto, cat, deposito) -> kg
        for row in stock_raw:
            cat = categorizar(row.get("DEPOSITO"))
            if not cat: continue
            prod = row.get("PRODUCTO") or ""
            if not prod: continue
            try: kg = float(row.get("CANTIDAD1") or 0)
            except: kg = 0.0
            acums[cat][prod] = acums[cat].get(prod, 0.0) + kg
            if cat in ("SILO", "SILOBOLSA", "BOLSAS"):
                key = (prod, cat, (row.get("DEPOSITO") or "").strip())
                stock_det_acum[key] = stock_det_acum.get(key, 0.0) + kg

        # convertir a tn y filtrar ceros
        stock_silo      = {p: round(kg/1000.0, 4) for p, kg in acums["SILO"].items() if kg}
        stock_silobolsa = {p: round(kg/1000.0, 4) for p, kg in acums["SILOBOLSA"].items() if kg}
        stock_bolsas    = {p: round(kg/1000.0, 4) for p, kg in acums["BOLSAS"].items() if kg}
        stock_descarte  = {p: round(kg/1000.0, 4) for p, kg in acums["DESCARTE"].items() if kg}
        # armar stock_detalle por producto (lista de depositos con tn, ordenada desc)
        for (prod, cat, dep), kg in stock_det_acum.items():
            if abs(kg) < 1: continue
            stock_detalle.setdefault(prod, []).append({"dep": dep, "cat": cat, "tn": round(kg/1000.0, 4)})
        for prod in stock_detalle:
            stock_detalle[prod].sort(key=lambda d: -d["tn"])
        print(f"    -> SILO:      {len(stock_silo)} productos")
        print(f"    -> SILOBOLSA: {len(stock_silobolsa)} productos")
        print(f"    -> BOLSAS:    {len(stock_bolsas)} productos")
        print(f"    -> DESCARTE:  {len(stock_descarte)} productos")
    except Exception as e:
        print(f"    [!] Error stock por deposito: {e}")

    # DEM-SUP Soja — fuente de datos del Extranet Agronasaja (vista /vistas/ops-demsup-soja).
    # Mismas queries al DW que la vista del extranet (ver scripts/demsup_soja.py). El granel
    # en campo (col C, silobolsa con merma) alimenta la columna Silo Bolsa de la semilla soja
    # en la Posición Granaria; el resto del payload queda preparado para cuando el tablero
    # pase a formar parte del extranet (mismo shape que serviría una API route de ahí).
    print(f"\n[+] Bajando DEM-SUP Soja (fuente Extranet Agronasaja / DW)...", flush=True)
    demsup_soja_data = None
    try:
        import demsup_soja
        demsup_soja_data = demsup_soja.fetch()
        if demsup_soja_data:
            _tt = demsup_soja_data["tot_tn"]
            print(f"    -> granel en campo (silo bolsa): {_tt['C']:,.1f} tn · en semillero: {_tt['D']:,.1f} tn"
                  f" · clasificado: {_tt['K']:,.1f} tn · venta pendiente: {_tt['O']:,.1f} tn")
    except Exception as e:
        print(f"    [!] Error DEM-SUP Soja: {e}")

    # Finales de Compra: análisis + factor desde la Balanza (api.agronasaja.com)
    print(f"\n[+] Bajando Finales de Compra (balanza) + calculando factor...", flush=True)
    try:
        finales = balanza_finales.fetch_finales()
        from collections import Counter as _C
        _est = _C(r["estado"] for r in finales)
        print(f"    -> {len(finales)} liquidaciones · estados: {dict(_est)}")
    except Exception as e:
        print(f"    [!] error finales: {e}")
        finales = []

    # Taqueo / Seguimiento fino de CTG (por grano × flujo, falta vincular, pendiente liquidar)
    print(f"\n[+] Calculando Taqueo CTG (seguimiento fino + pendiente liquidar)...", flush=True)
    try:
        import taqueo
        taqueo_data = taqueo.compute(pilot_norm, desde="2026-01-01")
        _pl = taqueo_data.get("pendiente_liquidar", {})
        print(f"    -> pendiente liquidar: {_pl.get('total_tn')} tn · duplicados/falta-vincular calculados")
    except Exception as e:
        print(f"    [!] error taqueo: {e}")
        taqueo_data = {}

    # Taqueo "entregado sin liquidar" por contrato (fuente: Finnegans GO/BSA, scrapeado
    # localmente con scripts/finn_taqueo_ctg.py -> data/taqueo_liquidar.json). No se puede
    # generar en CI (requiere sesión logueada de Finnegans GO); se refresca a mano y commitea.
    print(f"\n[+] Cargando Taqueo entregado-sin-liquidar (si existe)...", flush=True)
    taqueo_liq = {}
    tliq_path = Path(__file__).resolve().parent / "data" / "taqueo_liquidar.json"
    if tliq_path.exists():
        try:
            taqueo_liq = json.loads(tliq_path.read_text(encoding="utf-8"))
            print(f"    -> taqueo_liquidar.json: {taqueo_liq.get('total_contratos')} contratos · {taqueo_liq.get('total_ctg')} CTG · {taqueo_liq.get('total_tn')} tn")
        except Exception as e:
            print(f"    [!] taqueo_liquidar.json: {e}")

    # Producción por campaña/cultivo desde el Portal de Producción de Agronasaja (app pública)
    print(f"\n[+] Bajando Producción (Portal de Producción Agronasaja)...", flush=True)
    produccion_camp, produccion_pend_det = fetch_produccion()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "produccion_camp": produccion_camp,
        "produccion_pend_det": produccion_pend_det,
        "finales": finales,
        "taqueo": taqueo_data,
        "taqueo_liq": taqueo_liq,
        "finales_gastos": finales_gastos,
        "pilot":  pilot_norm,
        "compra": compra_norm,
        "saldos": saldos_norm,
        "bcr":    bcr,
        "cruces": cruces_list,
        "traza": traza_list,
        "liquidaciones": liquidaciones_dw,
        "liquidaciones_secu": liquidaciones_secu_dw,
        "cargill_movements": cargill_movements,
        "cargill_invoices": cargill_invoices,
        "cargill_payments": cargill_payments,
        "cargill_details": cargill_details,
        "ldc_settlements": ldc_settlements,
        "ldc_fixations": ldc_fixations,
        "ldc_ctgs": ldc_ctgs,
        "aca_ctgs": aca_ctgs,
        "fyo_ctgs": fyo_ctgs,
        "intagro_ctgs": intagro_ctgs,
        "bunge_ctgs": bunge_ctgs,
        "cofco_ctgs": cofco_ctgs,
        "allaria_ctgs": allaria_ctgs,
        "allaria_mercaderias": allaria_mercaderias,
        "allaria_cuenta_corriente": allaria_cuenta_corriente,
        "pagos_iniciales": pagos_iniciales,
        "stock_silo":      stock_silo,
        "stock_silobolsa": stock_silobolsa,
        "stock_bolsas":    stock_bolsas,
        "stock_descarte":  stock_descarte,
        "stock_detalle":   stock_detalle,
        "cosechado":       cosechado,
        "demsup_soja":     demsup_soja_data,
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
