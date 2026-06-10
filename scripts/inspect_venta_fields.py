"""Lista TODOS los campos de un contrato de venta para encontrar campos de moneda/precio reales."""
import re, json, sys
sys.stdout.reconfigure(encoding='utf-8')

with open(r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\index.html", "r", encoding="utf-8") as f:
    html = f.read()

m = re.search(r"const PAYLOAD\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
payload = json.loads(m.group(1))
venta = payload.get("pilot") or []

# Mostrar todas las keys de un contrato cualquiera con preciopromediofijado > 0
sample = next((r for r in venta if (r.get('preciopromediofijado') or 0) > 0), None)
if sample:
    print("CAMPOS DEL CONTRATO:")
    for k, v in sample.items():
        print(f"  {k!r}: {v!r}")

# Buscar contratos donde moneda=PESOS y precio < 1000 (sospechosos)
sosp_pesos = [r for r in venta if r.get('moneda')=='PESOS' and 0 < (r.get('preciopromediofijado') or 0) < 1000]
print(f"\n\nSOSPECHOSOS — moneda=PESOS pero precio < 1000 (probablemente USD): {len(sosp_pesos)}")
for r in sosp_pesos[:5]:
    print(f"  {r.get('numerointerno')} | {r.get('organizacion','')[:30]} | {r.get('producto')} | mon={r.get('moneda')} | prec={r.get('preciopromediofijado')} | tn={r.get('cantidadfijada')}")

# Buscar contratos moneda=DOLARES con precio > 1000 (sospechosos)
sosp_usd = [r for r in venta if r.get('moneda')=='DOLARES' and (r.get('preciopromediofijado') or 0) > 1000]
print(f"\nSOSPECHOSOS — moneda=DOLARES pero precio > 1000 (probablemente ARS): {len(sosp_usd)}")
for r in sosp_usd[:5]:
    print(f"  {r.get('numerointerno')} | {r.get('organizacion','')[:30]} | {r.get('producto')} | mon={r.get('moneda')} | prec={r.get('preciopromediofijado')} | tn={r.get('cantidadfijada')}")
