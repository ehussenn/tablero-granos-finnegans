"""Conector a la Balanza (api.agronasaja.com) para el módulo "Finales de Compra".

- Loguea con BALANZA_USER / BALANZA_PASS (env).
- Trae todas las liquidaciones de compra.
- Parsea el análisis del texto libre de `observaciones` (factor, daños, verdes,
  quebrados, materia extraña, humedad).
- Calcula el FACTOR OFICIAL de cámara (soja) y compara con el de la cerealera.

Devuelve lista de dicts lista para embeber en el tablero.
"""
from __future__ import annotations
import os, re, json
import urllib.request

API = "https://api.agronasaja.com/api"

def _login(user: str, pwd: str) -> str | None:
    try:
        req = urllib.request.Request(f"{API}/auth/login",
            data=json.dumps({"name": user, "password": pwd}).encode(),
            headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=30).read()).get("token")
    except Exception as e:
        print(f"    [!] balanza login: {e}")
        return None

def _page(tok: str, p: int) -> dict:
    req = urllib.request.Request(f"{API}/liquidacionescompras/page?page={p}&pageSize=200",
        headers={"Authorization": "Bearer " + tok})
    return json.loads(urllib.request.urlopen(req, timeout=45).read())

def _num(rx: str, s: str):
    m = re.search(rx, s, re.I)
    if not m: return None
    try: return float(m.group(1).replace(".", "").replace(",", ".")) if m.group(1).count(",")==1 and m.group(1).count(".")>1 else float(m.group(1).replace(",", "."))
    except ValueError: return None

def parse_analisis(obs: str) -> dict:
    o = obs or ""
    N = r"\s*([0-9]+(?:[.,][0-9]+)?)"
    return {
        "factorCereal": _num(r"F[CA]+TOR" + N, o),
        "danos":   _num(r"(?:TOTAL\s*)?DA[NÑ\\]?ADOS" + N, o),
        "verdes":  _num(r"VERDES?" + N, o),
        "quebrados": _num(r"QUEBRAD[A-ZÑ\\]*(?:[/A-ZÑ\\ ]*?CHUZOS?)?" + N, o),
        "matExtrana": _num(r"(?:MATERIAS?\s*EXTRA[NÑ\\]?AS?|CUERPOS?\s*EXTRA[NÑ\\]?OS?)" + N, o),
        "pesoHl":  _num(r"PESO\s*HECTOLITRICO" + N, o),
        "picados": _num(r"PICADOS?" + N, o),
        "materiaGrasa": _num(r"MATERIA\s*GRASA" + N, o),
        "proteina": _num(r"PROTE[IÍ]NAS?" + N, o),
        "humedad": _num(r"HUMEDAD" + N, o),
    }

def factor_oficial(a: dict) -> float | None:
    """Factor de merma de cámara para SOJA, a partir del análisis parseado.
       Bases: daños 5% (−1/pt), verdes 5% (−0,2/pt), quebrados 20% (~0,25/pt),
       materia extraña 1% (~1/pt). Devuelve None si no hay ningún rubro."""
    if all(a.get(k) is None for k in ("danos", "verdes", "quebrados", "matExtrana")):
        return None
    r = 0.0
    d = a.get("danos");      r += max(0.0, d - 5) if d else 0
    v = a.get("verdes");     r += max(0.0, (v - 5) * 0.2) if v else 0
    q = a.get("quebrados");  r += max(0.0, (q - 20) * 0.25) if q else 0
    m = a.get("matExtrana"); r += max(0.0, (m - 1) * 1.0) if m else 0
    return round(100 - r, 2)

try:
    import bccba_sim
except Exception:
    bccba_sim = None
_SIM_CACHE = {}
def factor_sim(grano: str, a: dict):
    """Factor oficial vía simulador de la Bolsa (para maíz/trigo/girasol/sorgo)."""
    if bccba_sim is None: return None
    g = (grano or "").lower()
    if "soja" in g:
        gk = "soja"; fields = {"GranosDañados": a["danos"], "GranosVerdes": a["verdes"],
                               "GranosQuebradosPartidos": a["quebrados"], "MateriaExtraña": a["matExtrana"]}
    elif "ma" in g and "z" in g:
        gk = "maiz"; fields = {"GranosDañados": a["danos"], "PesoHectolitrico": a["pesoHl"],
                               "GranosQuebrados": a["quebrados"], "MateriaExtraña": a["matExtrana"], "GranosPicados": a["picados"]}
    elif "trigo" in g:
        gk = "trigo"; fields = {"TotalDañados": a["danos"], "PesoHectolitrico": a["pesoHl"],
                                "GranosQuebradosChuzos": a["quebrados"], "MateriasExtrañas": a["matExtrana"],
                                "ContenidoProteico": a["proteina"], "GranosPicados": a["picados"]}
    elif "girasol" in g:
        gk = "girasol"; fields = {"ContenidoMateriaGrasa": a["materiaGrasa"], "MateriasExtrañas": a["matExtrana"]}
    elif "sorgo" in g:
        gk = "sorgo"; fields = {"GranosDañados": a["danos"], "PesoHectolitrico": a["pesoHl"],
                                "GranosQuebrados": a["quebrados"], "MateriaExtraña": a["matExtrana"]}
    else:
        return None
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields: return None
    # maíz/trigo/sorgo necesitan peso hectolítrico para un factor correcto; girasol necesita materia grasa
    if gk in ("maiz", "trigo", "sorgo") and "PesoHectolitrico" not in fields: return None
    if gk == "girasol" and "ContenidoMateriaGrasa" not in fields: return None
    key = (gk, tuple(sorted(fields.items())))
    if key in _SIM_CACHE: return _SIM_CACHE[key]
    try:
        r = bccba_sim.simular(gk, **fields)
        f = r.get("factor") if r else None
    except Exception:
        f = None
    _SIM_CACHE[key] = f
    return f


def fetch_finales() -> list[dict]:
    user = os.environ.get("BALANZA_USER"); pwd = os.environ.get("BALANZA_PASS")
    if not (user and pwd):
        print("    [!] sin BALANZA_USER/BALANZA_PASS — salteo finales")
        return []
    tok = _login(user, pwd)
    if not tok: return []
    items = []; p = 1
    while True:
        d = _page(tok, p); items += d.get("items", [])
        if len(items) >= d.get("totalCount", 0) or not d.get("items"): break
        p += 1
        if p > 60: break
    out = []
    for i in items:
        obs = str(i.get("observaciones") or "")
        a = parse_analisis(obs)
        grano = str(i.get("grano") or "")
        # El factor oficial SOLO está implementado para SOJA (bases propias por cultivo).
        # Para otros granos no calculamos oficial (no inventamos) hasta tener su tabla.
        of = factor_oficial(a) if "soja" in grano.lower() else factor_sim(grano, a)
        fc = a["factorCereal"]
        if fc is None and of is None:
            estado = "sin_factor"
        elif fc is None:
            estado = "calc_only"      # calculamos pero la cereal no lo cargó
        elif of is None:
            estado = "solo_cereal"    # cereal cargó factor pero sin análisis para chequear
        else:
            estado = "ok" if abs(fc - of) <= 0.25 else "revisar"
        out.append({
            "contrato": i.get("contrato"), "ctg": i.get("numeroCtg"),
            "cliente": i.get("cliente"), "comprador": i.get("comprador"),
            "grano": i.get("grano"), "campana": i.get("campana"),
            "precio": i.get("precioCliente"), "moneda": i.get("moneda"),
            "comision": i.get("comision"),
            "condCamara": i.get("condicionCamaraDescuento"),
            "condFlete": i.get("condicionFlete"),
            "fleteCorto": i.get("fleteCorto"), "fleteLargo": i.get("fleteLargo"),
            "kgDescarga": i.get("kgDescarga"), "kgAplicar": i.get("kgAplicar"),
            "factorCereal": fc, "factorOficial": of, "estado": estado,
            "danos": a["danos"], "verdes": a["verdes"], "quebrados": a["quebrados"],
            "matExtrana": a["matExtrana"], "humedad": a["humedad"] if a["humedad"] is not None else i.get("humedad"),
            "observaciones": obs,
        })
    return out

if __name__ == "__main__":
    # prueba directa: cargar .env y resumir
    from pathlib import Path
    envf = Path(__file__).resolve().parent.parent / ".env"
    if envf.exists():
        for ln in envf.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
    rows = fetch_finales()
    print(f"finales: {len(rows)}")
    from collections import Counter
    print("por grano:", Counter(str(r['grano']) for r in rows).most_common(6))
    soja = [r for r in rows if 'soja' in str(r['grano']).lower()]
    print("estado (soja):", Counter(r['estado'] for r in soja).most_common())
    print("--- ejemplos REVISAR ---")
    n = 0
    for r in soja:
        if r['estado'] == 'revisar':
            print(f"  {str(r['contrato'])[:20]:20} CTG {r['ctg']} | danos {r['danos']} verd {r['verdes']} queb {r['quebrados']} ME {r['matExtrana']} | cereal {r['factorCereal']} vs oficial {r['factorOficial']}")
            n += 1
            if n >= 6: break
