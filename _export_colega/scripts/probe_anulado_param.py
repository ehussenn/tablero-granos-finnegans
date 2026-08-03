"""Prueba distintos nombres de parametro para filtrar 'Estado Anulacion = No Anulado'."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import finnegans_api as api

BASE_PARAMS = {
    "PARAMWEBREPORT_FechaDesde": "2022-01-01",
    "PARAMWEBREPORT_FechaHasta": "2030-12-31",
    "PARAMWEBREPORT_FechaEntregaMin": "2022-01-01",
    "PARAMWEBREPORT_FechaEntregaMax": "2030-12-31",
}
# baseline: sin filtro
r0 = api.call("/reports/resumenContratosVentaGranos", BASE_PARAMS)
print(f"BASELINE venta (sin filtro anulado): {len(r0)} filas")

# Buscar campo de anulacion en los datos para identificar el nombre real
if r0:
    keys = list(r0[0].keys())
    for k in keys:
        if "ANUL" in k.upper() or "ESTADO" in k.upper():
            print(f"   campo encontrado: {k}")
    # Ver valores distintos del campo Estado / EstadoAnulacion
    for keyn in ["ESTADOANULACION","ESTADO","ESTADOCONTRATO","ANULADO"]:
        vals = set(r.get(keyn) for r in r0[:200])
        if vals - {None}:
            print(f"   valores de '{keyn}': {vals}")

# Probar params para filtrar
PARAM_NAMES = [
    "PARAMWEBREPORT_EstadoAnulacion",
    "PARAMEstadoAnulacion",
    "PARAMWEBREPORT_estado_anulacion",
    "PARAMWEBREPORT_EstadoDeAnulacion",
]
VALORES = ["No Anulado", "NoAnulado", "no_anulado", "NO_ANULADO"]

for pname in PARAM_NAMES:
    for pval in VALORES:
        try:
            p = dict(BASE_PARAMS); p[pname] = pval
            r = api.call("/reports/resumenContratosVentaGranos", p)
            n = len(r) if isinstance(r, list) else None
            if n is not None and n != len(r0):
                print(f"   [OK] {pname}='{pval}' -> {n} filas (diff vs baseline = {n-len(r0)})")
            elif n is not None:
                # mismo numero, el filtro no aplico
                pass
        except Exception as e:
            msg = str(e)[:120]
            if "Bad Request" not in msg:
                print(f"   {pname}='{pval}': {msg}")
