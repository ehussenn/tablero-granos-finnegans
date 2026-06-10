"""Verifica el CTG del screenshot y los subtipos de transaccion para Trazabilidad."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2, psycopg2.extras

conn = psycopg2.connect(
    host=os.environ["FNN_DW_HOST"], dbname="finnegansbi",
    user=os.environ["FNN_DW_USER"], password=os.environ["FNN_DW_PASS"],
    port=5432, sslmode="require", connect_timeout=20,
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
T = "agronasajasrl_traslado_de_granos"

# 1) Subtipos únicos con sus counts
print("=== SUBTIPOS de traslado ===");
cur.execute(f"SELECT transaccionsubtiponombre, operaciontipo, COUNT(*) c FROM public.{T} GROUP BY 1,2 ORDER BY c DESC")
for r in cur.fetchall():
    print(f"  {r['c']:>6}  {r['operaciontipo']:<10} {r['transaccionsubtiponombre']}")

# 2) Buscar CTG 1013227524 (el del screenshot)
print("\n=== CTG 1013227524 ===");
cur.execute(f"""SELECT transaccionsubtiponombre, operaciontipo, numerodocumento, organizacionnombre,
                       nombrecontrato, pesoneto, fecha, factor, certificado1116a, destinatario
                FROM public.{T} WHERE numerodocumentoadicional = '1013227524'""")
rows = cur.fetchall()
print(f"  Filas: {len(rows)}")
for r in rows:
    print(f"  {r['operaciontipo']:<7} | {r['transaccionsubtiponombre']:<40} | CP={r['numerodocumento']} | org={r['organizacionnombre']:<40} | cto={r['nombrecontrato']} | kg={r['pesoneto']} | factor={r['factor']}")

# 3) Buscar BENAYAS para confirmar
print("\n=== CTGs de BENAYAS en mayo 2026 ===");
cur.execute(f"""SELECT numerodocumentoadicional, transaccionsubtiponombre, operaciontipo, numerodocumento,
                       organizacionnombre, nombrecontrato, pesoneto, fecha
                FROM public.{T}
                WHERE organizacionnombre ILIKE '%BENAYAS%' AND fecha LIKE '2026-05%'
                ORDER BY numerodocumentoadicional""")
for r in cur.fetchall():
    print(f"  CTG={r['numerodocumentoadicional']} | {r['operaciontipo']:<7} | {r['transaccionsubtiponombre']:<40} | CP={r['numerodocumento']} | org={r['organizacionnombre']} | cto={r['nombrecontrato']} | kg={r['pesoneto']}")

conn.close()
