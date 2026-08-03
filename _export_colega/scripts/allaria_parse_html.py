"""Parsea HTMLs de Allaria a JSON estructurado."""
import sys, json, re
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent
HTML_DIR = ROOT / "scripts" / "scraper" / "out" / "allaria"
DATA = ROOT / "data" / "allaria"
DATA.mkdir(parents=True, exist_ok=True)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("[!] pip install beautifulsoup4"); sys.exit(1)

def parse_html(path, encoding="windows-1252"):
    with open(path, "rb") as f:
        raw = f.read()
    try: html = raw.decode("utf-8")
    except: html = raw.decode(encoding, errors="replace")
    return BeautifulSoup(html, "html.parser")

# 1) Mercaderías
print("[+] Parseando clientes_fis.asp (Mercaderías)...")
soup = parse_html(HTML_DIR/"r_clientes_fis.html")
tables = soup.find_all("table")
print(f"   {len(tables)} tables")

mercaderias = []
for t in tables:
    rows = t.find_all("tr")
    for tr in rows:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if not cells: continue
        # Filtros: ignorar header
        if "Producto" in cells and "Tipo" in cells: continue
        mercaderias.append(cells)

# Limpiar y estructurar: detectar cosecha + producto + tipo + moneda + precio + 3 columnas
estructurado = []
cosecha_actual = None
producto_actual = None
for row in mercaderias:
    if not row: continue
    # Cosecha tipo "25/26" o "24/25"
    if len(row) == 1 and re.match(r'\d{2}/\d{2}', row[0]):
        cosecha_actual = row[0]; continue
    # Si tiene "TOTAL"
    if row[0].startswith("TOTAL "):
        # Subtotal: TOTAL Maiz: x y z
        parts_match = re.search(r'TOTAL ([A-Za-z]+)', row[0])
        if parts_match:
            estructurado.append({
                "cosecha": cosecha_actual, "producto": parts_match.group(1),
                "tipo": "TOTAL", "moneda": None, "precio": None,
                "contratado": row[1] if len(row) > 1 else None,
                "entregado": row[2] if len(row) > 2 else None,
                "facturado": row[3] if len(row) > 3 else None,
            })
        continue
    # Detectar producto: row[0] que es palabra (Trigo, Maíz, Soja...)
    if row[0] in ("Trigo","Maiz","Maíz","Soja","Sorgo","Girasol","Cebada"):
        producto_actual = row[0]
    # Detectar tipo (CE, AF, CG, CEFC, A.F.) en row[1] o row[0]
    if len(row) >= 6 and any(t in row[1] for t in ["CE","AF","CG","A.F.","CEFC","NEG"]):
        try:
            estructurado.append({
                "cosecha": cosecha_actual,
                "producto": row[0],
                "tipo": row[1],
                "moneda": row[2],
                "precio": row[3],
                "contratado": row[4],
                "entregado": row[5],
                "facturado": row[6] if len(row) > 6 else None,
            })
        except: pass

print(f"   {len(estructurado)} entradas estructuradas")
(DATA/"mercaderias.json").write_text(json.dumps(estructurado, ensure_ascii=False, indent=2), encoding="utf-8")

# 2) Cuenta Corriente
print("\n[+] Parseando clientes_cta.asp (Cuenta Corriente)...")
soup = parse_html(HTML_DIR/"r_clientes_cta.html")
tables = soup.find_all("table")
print(f"   {len(tables)} tables")

cta_data = []
for t in tables:
    for tr in t.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td","th"])]
        if cells and any(cells): cta_data.append(cells)

# Estructurar por cuenta
cuentas = {}
cuenta_actual = None
for row in cta_data:
    if not row: continue
    txt = row[0]
    # Header de cuenta tipo "MTR (113235615 )" o "VENDEDOR FISICO (...)"
    m = re.match(r'^([A-Z][A-Z ]+)\s*\((\d+)', txt)
    if m:
        cuenta_actual = m.group(1).strip()
        cuentas[cuenta_actual] = {"id": m.group(2), "saldos": []}
        continue
    if cuenta_actual and txt.startswith("Saldo Contable"):
        cuentas[cuenta_actual]["saldos"].append({
            "descripcion": txt,
            "pesos": row[1] if len(row) > 1 else None,
            "dolares": row[2] if len(row) > 2 else None,
            "equiv_pesos": row[3] if len(row) > 3 else None,
            "saldo": row[4] if len(row) > 4 else None,
        })

print(f"   {len(cuentas)} cuentas")
for nombre, info in cuentas.items():
    print(f"   {nombre} ({info['id']}): {len(info['saldos'])} saldos")
(DATA/"cuenta_corriente.json").write_text(json.dumps(cuentas, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\n[+] Out: {DATA}")
