"""Scraper de precios pizarra de la Camara Arbitral de Cereales (BCR).
Fuente: https://www.cac.bcr.com.ar/es/precios-de-pizarra (HTML publico).
"""
from __future__ import annotations
import re, urllib.request, ssl
from datetime import datetime

URL = "https://www.cac.bcr.com.ar/es/precios-de-pizarra"

GRANOS = ["soja", "maiz", "trigo", "girasol", "sorgo"]


def _to_float(s: str) -> float | None:
    if s is None:
        return None
    s = re.sub(r"[\$\sUS]+", "", s)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _fetch_html() -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (tablero-granos)"})
    with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_pizarra() -> dict:
    html = _fetch_html()
    out_granos = {}

    # Estrategia robusta: aislamos primero el bloque de cada grano (hasta el proximo "<div class=\"board")
    # y dentro buscamos los precios. Asi evitamos que un grano vacio se "robe" los valores del siguiente.
    for grano in GRANOS:
        start_re = re.compile(r'<div\s+class="board\s+board-' + grano + r'(\s+estimative)?\s*">', re.IGNORECASE)
        m = start_re.search(html)
        if not m:
            continue
        estimativo = bool(m.group(1))
        # buscar el siguiente <div class="board ..."> o cierre del contenedor general
        rest = html[m.end():]
        end = re.search(r'<div\s+class="board\s+board-|<div\s+class="price-board-footer', rest, re.IGNORECASE)
        block = rest[:end.start()] if end else rest

        ars_m = re.search(r'<div\s+class="price">\s*([\$\s.,0-9]+?)\s*</div>', block, re.DOTALL)
        usd_m = re.search(r'<strong>US\$</strong>\s*([\s.,0-9]+)', block, re.DOTALL)
        ars = _to_float(ars_m.group(1)) if ars_m else None
        usd = _to_float(usd_m.group(1)) if usd_m else None
        out_granos[grano] = {"ars": ars, "usd": usd, "estimativo": estimativo}

    # TC explicito desde el footer del cuadro
    tc_explicit = None
    tc_match = re.search(r"TC BNA Divisas[^<]*</strong>\s*Comprador[^:]*:\s*<strong>\s*\$\s*([\d.,]+)", html, re.IGNORECASE)
    if tc_match:
        tc_explicit = _to_float(tc_match.group(1))

    # Si no hay TC explicito, lo derivamos
    if not tc_explicit:
        tcs = [d["ars"]/d["usd"] for d in out_granos.values()
               if d.get("ars") and d.get("usd")]
        tc_explicit = round(sum(tcs)/len(tcs), 2) if tcs else None

    # Fecha del informe (Rosario, XX de MMMM del YYYY)
    fecha_informe = None
    fm = re.search(r"Rosario,\s*(\d{1,2})\s*de\s*(\w+)\s*del?\s*(\d{4})", html, re.IGNORECASE)
    if fm:
        fecha_informe = f"{fm.group(1)} {fm.group(2)} {fm.group(3)}"

    return {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source": URL,
        "fecha_informe": fecha_informe,
        "tc_usd_ars": tc_explicit,
        "granos": out_granos,
    }


if __name__ == "__main__":
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(fetch_pizarra(), ensure_ascii=False, indent=2))
