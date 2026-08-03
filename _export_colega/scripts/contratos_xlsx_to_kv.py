"""Carga el Excel de Codigos de Contratos, lo convierte a JSON y lo sube al KV de Cloudflare."""
import openpyxl, json, sys, os
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
XLSX = r"C:\Users\Public\Documents\Granos\Codigo de Contratos.xlsx"
CFT = os.environ.get("CFT") or sys.exit("falta CFT env var")
ACCT = "<CLOUDFLARE_API_KEY>"
KV_ID = "<CLOUDFLARE_TOKEN>"
KEY = "contratos"

wb = openpyxl.load_workbook(XLSX, data_only=True)
compra = []
venta = []
for s in wb.worksheets:
    bucket = compra if "COMPRA" in s.title.upper() else venta
    for i, row in enumerate(s.iter_rows(values_only=True)):
        if i == 0: continue  # header
        num, ben = (row[0], row[1]) if len(row) >= 2 else (None, None)
        if not num and not ben: continue
        if num is None and ben is None: continue
        bucket.append({
            "id": f"{('c' if bucket is compra else 'v')}-{i}",
            "numero": (str(num).strip() if num else ""),
            "beneficiario": (str(ben).strip() if ben else ""),
        })

# Filter empty
compra = [c for c in compra if c["numero"] or c["beneficiario"]]
venta  = [v for v in venta  if v["numero"] or v["beneficiario"]]

print(f"COMPRA: {len(compra)} filas")
print(f"VENTA:  {len(venta)} filas")
print(f"Sample compra[0..3]: {compra[:3]}")
print(f"Sample venta[0..3]:  {venta[:3]}")

payload = {"compra": compra, "venta": venta, "actualizado": "2026-06-10"}

# Upload to KV via CF API
url = f"https://api.cloudflare.com/client/v4/accounts/{ACCT}/storage/kv/namespaces/{KV_ID}/values/{KEY}"
data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(url, data=data, method="PUT", headers={
    "Authorization": f"Bearer {CFT}",
    "Content-Type": "text/plain",  # KV stores text; we're storing JSON as text
})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
        print(f"PUT {r.status}: {body[:200]}")
except urllib.error.HTTPError as e:
    print(f"ERROR {e.code}: {e.read().decode()[:300]}")
    sys.exit(1)

# Verify
get_req = urllib.request.Request(url, headers={"Authorization": f"Bearer {CFT}"})
with urllib.request.urlopen(get_req, timeout=10) as r:
    got = r.read().decode()
print(f"Verificado: {len(got)} bytes leidos del KV")
