"""Mira el HTML buildeado para extraer una muestra de los contratos de venta
y entender los valores de preciopromediofijado / cantidadfijada / moneda / importefijado."""
import re, json, sys
sys.stdout.reconfigure(encoding='utf-8')

with open(r"C:\Users\Public\Documents\Granos\tablero-granos-finnegans\index.html", "r", encoding="utf-8") as f:
    html = f.read()

# El payload esta embebido como: const PAYLOAD = {...};
m = re.search(r"const PAYLOAD\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
if not m:
    print("NO encontre PAYLOAD"); sys.exit(1)

# Es muy grande - parseo cuidadosamente
payload = json.loads(m.group(1))
print(f"keys: {list(payload.keys())}")

# venta data
venta = payload.get("venta") or payload.get("pilot") or payload.get("DATA") or []
print(f"venta length: {len(venta)}")
if not venta:
    print(f"buscando en otras claves...");
    for k, v in payload.items():
        if isinstance(v, list) and len(v) > 100:
            print(f"  {k}: {len(v)} items, first item keys: {list(v[0].keys())[:20] if v else []}")

# Tomamos los primeros 5 con preciopromediofijado > 0
ejemplos = [r for r in venta if (r.get('preciopromediofijado') or 0) > 0]
print(f"\nContratos venta con preciopromediofijado > 0: {len(ejemplos)}")

# Agrupar por moneda
from collections import defaultdict
byMon = defaultdict(lambda: {"count":0, "sum_tn":0, "sum_w":0, "samples":[]})
for r in venta:
    pr = r.get('preciopromediofijado') or 0
    tn = r.get('cantidadfijada') or 0
    if pr <= 0 and tn <= 0: continue
    mon = r.get('moneda') or "—"
    byMon[mon]["count"] += 1
    byMon[mon]["sum_tn"] += tn or 0
    byMon[mon]["sum_w"] += (tn or 0) * (pr or 0)
    if len(byMon[mon]["samples"]) < 3:
        byMon[mon]["samples"].append({
            "n": r.get('numerointerno'),
            "org": r.get('organizacion'),
            "prod": r.get('producto'),
            "tn_fij": pr, "tn": tn,
            "precio": pr,
            "importe": r.get('importefijado'),
        })

for m, v in byMon.items():
    avg = v["sum_w"]/v["sum_tn"] if v["sum_tn"]>0 else 0
    print(f"\nMoneda: {m} | {v['count']} contratos | tot Tn: {v['sum_tn']:,.2f} | avg ponderado: {avg:,.4f}")
    for s in v["samples"]:
        print(f"  ej: {s}")
