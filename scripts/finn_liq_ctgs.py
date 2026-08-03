"""Prueba end-to-end del contrato abierto: entregas (004) vs liquidados (detalle de cada
liquidación por pk, sacados del grid 007). Identifica CTG SIN LIQUIDAR."""
import sys, re, glob
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
SN="scripts/scraper/out/finn_sniff"
def norm(c): c=re.sub(r'\D','',str(c or '')); return c.lstrip('0')
# 1) entregas (004): CTGs del contrato
t04=open(glob.glob(f'{SN}/004_*.txt')[0],encoding='utf-8',errors='replace').read()
entregas=set(norm(x) for x in re.findall(r'\b\d{11}\b', t04))
# 2) pks de liquidaciones (007): primer campo de cada fila del CDATA
t07=open(glob.glob(f'{SN}/007_*.txt')[0],encoding='utf-8',errors='replace').read()
m=re.search(r'<data><!\[CDATA\[(.*?)\]\]>', t07, re.S)
pks=[]
if m:
    for row in m.group(1).split(';'):
        f=row.split(',')
        if f and f[0].strip().isdigit(): pks.append(f[0].strip())
print(f"contrato abierto: {len(entregas)} CTG entregados · {len(pks)} liquidaciones (pks: {pks[:6]}...)")
# 3) bajar detalle de cada liquidación y extraer CTGs liquidados
liquidados=set()
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9340")
    ctx=b.contexts[0]; page=ctx.pages[0]
    for pk in pks:
        url=f"https://oneteam.finneg.com/BSA/standardDF_def_and_data?standardXml=claseVO=LiquidacionGranosVentaVO&pk={pk}&fromDFVIEWER=1"
        try:
            r=page.request.get(url,timeout=45000); body=r.text()
            found=set(norm(x) for x in re.findall(r'<Descripcion[^>]*>(\d{11})/', body))
            liquidados|=found
            print(f"   liq {pk}: {len(found)} CTG liquidados")
        except Exception as e: print(f"   liq {pk}: ERR {str(e)[:50]}")
    b.close()
sin_liq=sorted(entregas - liquidados)
print(f"\n=== RESULTADO contrato abierto ===")
print(f"entregados: {len(entregas)} | liquidados: {len(liquidados & entregas)} | SIN LIQUIDAR: {len(sin_liq)}")
print("CTG sin liquidar:", ', '.join(sin_liq) if sin_liq else 'ninguno (todo liquidado)')
