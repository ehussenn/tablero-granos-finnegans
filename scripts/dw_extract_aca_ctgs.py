"""Extrae CTGs con destino ACA / cooperativas argentinas desde el DW."""
import os, sys, json
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal
import psycopg2
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

OUT = ROOT / "data" / "aca"
OUT.mkdir(parents=True, exist_ok=True)

def fix(v):
    if isinstance(v, Decimal): return float(v)
    if isinstance(v, (date, datetime)): return str(v)
    return v

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], user=os.environ["FNN_DW_USER"],
    password=os.environ["FNN_DW_PASS"], dbname="finnegansbi", connect_timeout=30,
)
cur = conn.cursor()

# ACA estricto: solo "ASOC DE COOPERATIVAS ARGENTINAS COOP LTDA"
cur.execute("""
    SELECT * FROM agronasajasrl_traslado_venta_granos_carta_porte_cruce
    WHERE (UPPER(destino) LIKE '%ASOC DE COOPERATIVAS%' OR UPPER(organizacionnombre) LIKE '%ASOC DE COOPERATIVAS%')
      AND (fecha >= '2026-01-01' OR fechadescarga >= '2026-01-01' OR fechaarribo >= '2026-01-01')
""")
cols = [d[0] for d in cur.description]
rows = cur.fetchall()
ctgs = [{c:fix(v) for c,v in zip(cols, row)} for row in rows]
print(f"[+] {len(ctgs)} traslados ACA desde 01/01/2026")

unicos = list({c.get("numerodocumentoadicional") for c in ctgs if c.get("numerodocumentoadicional")})
print(f"[+] {len(unicos)} CTGs únicos")

(OUT/"aca_ctgs.json").write_text(json.dumps(ctgs, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[+] guardado en data/aca/aca_ctgs.json")

cur.close(); conn.close()
