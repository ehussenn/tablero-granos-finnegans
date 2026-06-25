"""Motor de cálculo de factor: usa el simulador OFICIAL de la Bolsa de Córdoba
(simulador.bccba.org.ar) como engine. Le manda el análisis de un grano y
devuelve el descuento/bonificación %, kilos netos y precio final OFICIALES.

Soporta: soja, maiz, trigo, girasol, sorgo.
"""
from __future__ import annotations
import re, urllib.request, urllib.parse

BASE = "https://simulador.bccba.org.ar/Home/"
H = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36",
    "Origin": "https://simulador.bccba.org.ar",
}

# Campos de cada formulario (los que NO se pasan quedan en 0 / vacío).
CAMPOS = {
    "soja":  ["MateriaExtraña","IncluidoTierra","GranosNegros","GranosQuebradosPartidos",
              "GranosDañados","IncluidoGranosQuemadosOAveria","GranosVerdes","Humedad",
              "SemillaChamico","Chamico","RevolcadoEnTierra","OloresObjetables","GranosAmohosados"],
    "maiz":  ["PesoHectolitrico","GranosDañados","GranosQuebrados","MateriaExtraña","Tipo",
              "Color","GranosPicados","Humedad","OloresObjetables","GranosAmohosados","Chamico"],
    "trigo": ["PesoHectolitrico","ContenidoProteico","GranosArdidosDañadosPorCalor","TotalDañados",
              "GranosPanzaBlanca","GranosPicados","GranosQuebradosChuzos","GranosConCarbon",
              "PuntaNegraPorCarbon","PuntaSombreadaPorTierra","TrebolDeOlor","MateriasExtrañas",
              "Humedad","RevolcadoEnTierra","OloresObjetables"],
    "girasol":["ContenidoMateriaGrasa","AcidezMateriaGrasa","MateriasExtrañas","Humedad","Chamico","FechaDescarga"],
    "sorgo": ["PesoHectolitrico","GranosDañados","GranosQuebrados","MateriaExtraña","Tañino",
              "Humedad","OloresObjetables","GranosAmohosados","Chamico"],
}
# URL de POST por grano (maíz va a la raíz del sitio)
URLS = {
    "soja":   "https://simulador.bccba.org.ar/Home/Soja",
    "maiz":   "https://simulador.bccba.org.ar/",
    "trigo":  "https://simulador.bccba.org.ar/Home/Trigo",
    "girasol":"https://simulador.bccba.org.ar/Home/Girasol",
    "sorgo":  "https://simulador.bccba.org.ar/Home/Sorgo",
}

def simular(grano: str, peso=100000, precio=100, **rubros) -> dict | None:
    """grano: soja|maiz|trigo|girasol|sorgo. rubros: valores del análisis (ej. GranosDañados=7).
       Devuelve {descuento_pct, kilos_netos, precio_final, factor}."""
    g = grano.lower().strip()
    if g not in CAMPOS: return None
    data = {"PesoInicial": str(peso), "PrecioInicial": str(precio)}
    for f in CAMPOS[g]:
        data[f] = str(rubros.get(f, 0) if rubros.get(f) is not None else 0)
    # overrides explícitos (por si pasan nombres exactos)
    for k, v in rubros.items():
        if v is not None: data[k] = str(v)
    body = urllib.parse.urlencode(data, encoding="utf-8").encode("utf-8")
    req = urllib.request.Request(URLS[g], data=body, headers=H)
    try:
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception as e:
        return {"error": str(e)}
    def grab(rx):
        m = re.search(rx, html, re.I)
        return m.group(1) if m else None
    desc = grab(r"Bonificaci[oó]n/descuento:</strong>\s*([\-0-9.,]+)")
    kn   = grab(r"Kilos netos:</strong>\s*([0-9.,]+)")
    pf   = grab(r"Precio final:</strong>\s*\$?\s*([0-9.,]+)")
    pfn  = float(pf.replace(",", ".")) if pf else None
    # factor robusto = precio_final / precio_inicial * 100 (refleja TODO el ajuste, incl. PH)
    factor = round(pfn / float(precio) * 100, 2) if (pfn is not None and float(precio)) else None
    return {
        "descuento_pct": (round(factor - 100, 2) if factor is not None else None),
        "factor": factor,
        "kilos_netos": kn, "precio_final": pf,
        "bonif_field": (float(desc.replace(",", ".")) if desc else None),
    }

if __name__ == "__main__":
    print("VALIDACIÓN soja:")
    for dn in [0, 5, 7, 10]:
        r = simular("soja", GranosDañados=dn)
        print(f"  daños {dn}% -> descuento {r['descuento_pct']} factor {r['factor']}")
    print("maiz daños 5 + PH 70:", simular("maiz", GranosDañados=5, PesoHectolitrico=70))
    print("trigo PH 76, proteina 11:", simular("trigo", PesoHectolitrico=76, ContenidoProteico=11))
    print("girasol mat.grasa 42:", simular("girasol", ContenidoMateriaGrasa=42))
