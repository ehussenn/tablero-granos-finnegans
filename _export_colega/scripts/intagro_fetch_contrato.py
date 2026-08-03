import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.stdout.reconfigure(encoding="utf-8")
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"scraper"/"out"/"intagro_nav"; OUT.mkdir(parents=True,exist_ok=True)
contrato=sys.argv[1] if len(sys.argv)>1 else "70923301"
prod=sys.argv[2] if len(sys.argv)>2 else "2503"
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp("http://localhost:9334")
    ctx=b.contexts[0]; page=ctx.pages[-1]
    r=page.request.post("https://portal.intagro.com/ajax_altocom/VerContratoAmpliado.php",
        form={"productor":prod,"contrato":contrato,"areanegocio":"GV"},
        headers={"X-Requested-With":"XMLHttpRequest"})
    html=r.text()
    (OUT/f"contrato_{contrato}.html").write_text(html,encoding="utf-8")
    print("status",r.status,"len",len(html))
    # localizar la tabla de analisis: filas con Rubro de 2 letras + Resultado + Rebaja + Descripcion
    # extraer todas las celdas por fila
    trs=re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S|re.I)
    print("filas tr totales:", len(trs))
    cnt=0
    for tr in trs:
        cells=[re.sub(r"<[^>]+>","",c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S|re.I)]
        # patrón análisis: [fecha, nAnalisis, nComprobante(CTG 11díg), rubro(2 letras), resultado, rebaja, desc]
        if len(cells)>=7 and re.match(r"\d{2}/\d{2}/\d{4}", cells[0]) and re.match(r"^\d{11}$", cells[2]) and re.match(r"^[A-Z]{2}$", cells[3]):
            print("  ", cells[:7]); cnt+=1
            if cnt>=12: break
    print("filas análisis detectadas (muestra):", cnt)
    b.close()
