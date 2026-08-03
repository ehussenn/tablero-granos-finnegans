"""Por qué los totales de Cruce Cliente x Comprador son $0: diagnosticar precios."""
import sys, json
sys.path.insert(0, r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\scripts")
import finnegans_api as api
sys.stdout.reconfigure(encoding="utf-8")

# 1) Bajar contratos de compra
print("Bajando contratos de compra...")
compra = api.call("/reports/ResumenContratoCompraGranos", {
    "PARAMWEBREPORT_FechaDesde": "2022-01-01",
    "PARAMWEBREPORT_FechaHasta": "2030-12-31",
    "PARAMWEBREPORT_FechaEntregaMin": "2022-01-01",
    "PARAMWEBREPORT_FechaEntregaMax": "2030-12-31",
})
print(f"Total contratos compra: {len(compra)}")

with_precio_fij = [c for c in compra if (c.get("PRECIOPROMEDIOFIJADO") or 0) > 0]
with_precio_liq = [c for c in compra if (c.get("PRECIOLIQUIDADO") or 0) > 0]
print(f"Contratos con PRECIOPROMEDIOFIJADO > 0: {len(with_precio_fij)}")
print(f"Contratos con PRECIOLIQUIDADO > 0:       {len(with_precio_liq)}")

# 2) Cuántos de los cruces tienen el contrato con precio?
print("\nBajando traslados COMPRA CV / VENTA CV...")
trasl = api.call("/reports/trasladoGranos", {
    "PARAMFechaDesde": "2026-01-01",
    "PARAMFechaHasta": "2030-12-31",
})
cv = [r for r in trasl if r.get("TRANSACCIONSUBTIPONOMBRE") in ("Recepción de Granos COMPRA CV", "Traslado de Granos VENTA CV")]
print(f"Total traslados CV: {len(cv)}")

# Index contratos compra por nombre
idx_compra = {c.get("CONTRATO"): c for c in compra}

# Por cada CTG agrupado, ver si tiene precio compra
from collections import defaultdict
por_ctg = defaultdict(dict)
for r in cv:
    ctg = r.get("NUMERODOCUMENTOADICIONAL")
    if not ctg: continue
    if r.get("OPERACIONTIPO") == "Compra":
        por_ctg[ctg]["contrato_compra"] = r.get("NOMBRECONTRATO")
    elif r.get("OPERACIONTIPO") == "Venta":
        por_ctg[ctg]["contrato_venta"] = r.get("NOMBRECONTRATO")

print(f"\nCTGs únicos: {len(por_ctg)}")

# de los CTGs, cuantos tienen contrato compra que tenga PRECIO > 0
sin_precio = 0
con_precio = 0
con_precio_liq = 0
ejemplos_sin = []
for ctg, datos in por_ctg.items():
    nc = datos.get("contrato_compra")
    if not nc: continue
    c = idx_compra.get(nc)
    if not c:
        sin_precio += 1
        if len(ejemplos_sin) < 5:
            ejemplos_sin.append(f"  CTG {ctg}  contrato '{nc}' -> NO encontrado en compra")
        continue
    pf = c.get("PRECIOPROMEDIOFIJADO") or 0
    pl = c.get("PRECIOLIQUIDADO") or 0
    if pf > 0 or pl > 0:
        con_precio += 1
        if pl > 0: con_precio_liq += 1
    else:
        sin_precio += 1
        if len(ejemplos_sin) < 5:
            ejemplos_sin.append(f"  CTG {ctg}  contrato '{nc}'  tipo={c.get('TIPOCONTRATO')}  pf={pf} pl={pl}  fijada={c.get('CANTIDADFIJADA')} liq={c.get('CANTIDADLIQUIDADA')}")

print(f"\nCTGs CON precio en contrato compra:  {con_precio} (de los cuales liquidados: {con_precio_liq})")
print(f"CTGs SIN precio en contrato compra:  {sin_precio}")
print("\nEjemplos sin precio:")
for e in ejemplos_sin:
    print(e)

# 3) Buscar liquidaciones de compra en API
print("\n\n=== Probando endpoint GRANOS_LIQ_COMPRA ===")
spec = json.load(open(r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\scripts\finnegans_swagger.json", encoding="utf-8"))
for path, ops in spec.get("paths", {}).items():
    if "GRANOS_LIQ_COMPRA" in path and isinstance(ops, dict):
        for method, op in ops.items():
            if isinstance(op, dict):
                print(f"[{method.upper()}] {path}")
                for p in op.get("parameters", []) or []:
                    print(f"   - {p.get('name')}  req={p.get('required')}  desc={(p.get('description') or '')[:70]}")

# Probar el endpoint
print("\nProbando llamada...")
try:
    liq = api.call("/reports/GRANOS_LIQ_COMPRA", {
        "PARAMWEBREPORT_FechaDesde": "2026-01-01",
        "PARAMWEBREPORT_FechaHasta": "2030-12-31",
    })
    print(f"Filas: {len(liq) if isinstance(liq,list) else 'no list'}")
    if isinstance(liq, list) and liq:
        print(f"Columnas: {list(liq[0].keys())}")
        print(f"\nPrimer registro:")
        for k, v in liq[0].items():
            print(f"  {k:<40} = {repr(v)[:60]}")
except Exception as e:
    print(f"ERROR: {str(e)[:200]}")
