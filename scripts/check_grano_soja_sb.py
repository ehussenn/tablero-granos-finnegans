"""Verifica el total Grano Soja en silo bolsa (con DEP COSECHA incluido)."""
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
h = (ROOT / "index.html").read_text(encoding="utf-8")
m = re.search(r'const PAYLOAD\s*=\s*(\{.*?\});\s*\n', h, re.S)
P = json.loads(m.group(1))
sb = P.get("stock_silobolsa") or {}
si = P.get("stock_silo") or {}
bo = P.get("stock_bolsas") or {}
de = P.get("stock_descarte") or {}

interesantes = ["Grano Soja", "Grano Maíz", "Grano Trigo Pan", "Grano Arveja", "Grano Girasol", "Maiz Segunda"]
print(f"{'Producto':<35} {'SILO':>10} {'SILOBOLSA':>12} {'BOLSAS':>10} {'DESCARTE':>10} {'TOTAL':>10}")
for p in interesantes:
    s = si.get(p, 0); sbv = sb.get(p, 0); b = bo.get(p, 0); d = de.get(p, 0)
    print(f"{p:<35} {s:>10.2f} {sbv:>12.2f} {b:>10.2f} {d:>10.2f} {s+sbv+b+d:>10.2f}")
