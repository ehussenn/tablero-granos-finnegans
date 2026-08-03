"""Busca exhaustivamente cómo obtener Liquidaciones de COMPRA de Granos en Finnegans."""
import sys, os
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finnegans_api as api

# 1) GRANOS_LIQ_COMPRA con todos los rangos
print("="*100); print("/reports/GRANOS_LIQ_COMPRA — rango amplio"); print("="*100)
for desde, hasta in [("2020-01-01","2030-12-31"), ("2026-01-01","2026-12-31")]:
    try:
        r = api.call("/reports/GRANOS_LIQ_COMPRA",
                     {"PARAMWEBREPORT_FechaDesde": desde, "PARAMWEBREPORT_FechaHasta": hasta})
        print(f"  [{desde} → {hasta}]: {len(r) if isinstance(r,list) else type(r).__name__} filas")
        if isinstance(r, list) and r:
            print(f"     cols: {list(r[0].keys())[:30]}")
            for i, row in enumerate(r[:3]):
                print(f"     row{i}: {row}")
    except Exception as e:
        print(f"  ERR: {str(e)[:200]}")

# 2) PANEL_VINCULACION con CompraVenta=1 (asumimos 1=compra)
print("\n" + "="*100); print("/reports/PANEL_VINCULACION_TRANSACCIONES_GRANOS_LIQUIDACIONES — Compra"); print("="*100)
for cv in [1, 2]:  # probar 1 y 2 para saber cuál es compra/venta
    try:
        r = api.call("/reports/PANEL_VINCULACION_TRANSACCIONES_GRANOS_LIQUIDACIONES", {
            "PARAMWEBREPORT_FechaDesde": "2024-01-01",
            "PARAMWEBREPORT_FechaHasta": "2030-12-31",
            "PARAMWEBREPORT_EmpresaID": "48",  # Agronasaja
            "PARAMWEBREPORT_CompraVenta": cv,
            "PARAMWEBREPORT_TipoPendiente": 1,
        })
        n = len(r) if isinstance(r, list) else 0
        print(f"\n  CompraVenta={cv}: {n} filas")
        if isinstance(r, list) and r:
            print(f"    cols: {list(r[0].keys())[:30]}")
            print(f"    sample:")
            for k, v in r[0].items():
                print(f"      {k:<35} = {repr(v)[:70]}")
    except Exception as e:
        print(f"  ERR cv={cv}: {str(e)[:200]}")

# 3) /LiquidacionCompraGranos (singular sin codigo) — list endpoint
print("\n" + "="*100); print("/LiquidacionCompraGranos/list (si existe)"); print("="*100)
try:
    r = api.call("/LiquidacionCompraGranos/list", {})
    print(f"  {type(r).__name__}: {len(r) if isinstance(r,(list,dict)) else '?'}")
    if isinstance(r, list) and r: print(f"  sample: {r[0]}")
    elif isinstance(r, dict): print(f"  keys: {list(r.keys())[:20]}")
except Exception as e:
    print(f"  ERR: {str(e)[:200]}")

# 4) Probar APIConsultaContratosCompraGranos
print("\n" + "="*100); print("/reports/APIConsultaContratosCompraGranos"); print("="*100)
try:
    r = api.call("/reports/APIConsultaContratosCompraGranos", {})
    print(f"  {type(r).__name__}: {len(r) if isinstance(r,list) else '?'}")
    if isinstance(r, list) and r:
        print(f"  cols: {list(r[0].keys())[:30]}")
        for k, v in r[0].items():
            if v not in (None, '', 0): print(f"    {k:<35} = {repr(v)[:70]}")
except Exception as e:
    print(f"  ERR: {str(e)[:200]}")

# 5) Probar /reports/PANEL_VINCULACION_TRANSACCIONES_GRANOS_LIQUIDACIONES (DW table)
print("\n" + "="*100); print("Probar SQL tablas DW con 'liquid' o 'compra'"); print("="*100)
print("(Ya las exploramos - solo está liquidacion_venta_granos)")
