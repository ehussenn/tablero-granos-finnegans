# Tablero de Granos - Finnegans (base para replicar)

Genera un tablero HTML monolitico (una sola pagina) a partir de datos de Finnegans
(API + Data Warehouse Postgres), la balanza propia, pizarras BCR y los extranets de
las cerealeras. Se publica via GitHub Actions -> GitHub Pages, con un Cloudflare Worker
opcional adelante como portal de login + almacenamiento compartido (KV).

## Como funciona
- `build.py` baja todo y escribe `index.html` (todo el JS/CSS/datos embebidos como PAYLOAD).
- `scripts/finnegans_api.py`: cliente de la API de Finnegans (OAuth client_credentials).
- `scripts/*`: scrapers/integraciones por cerealera y utilidades (son EJEMPLOS de Agronasaja;
  el colega debe adaptarlos a sus propios extranets/credenciales).
- `.github/workflows/build-and-deploy.yml`: corre `build.py` con los secrets y publica.

## Requisitos
- Python 3.12+
- `pip install psycopg2-binary playwright openpyxl` (y `playwright install chromium` para los scrapers)
- Cuenta Finnegans (client_id/secret), acceso al DW Postgres, y las cuentas de cada extranet.

## Setup
1. Copiar `.env.example` a `.env` y completar TODAS las variables (credenciales propias).
2. `python build.py`  -> genera `index.html` localmente.
3. Para deploy automatico: crear repo en GitHub, cargar los mismos nombres de variable
   como *Secrets* del repo (Settings > Secrets and variables > Actions), y activar Pages.
4. (Opcional) Cloudflare Worker como portal de login: ver scripts `cf_*` (adaptar).

## IMPORTANTE
- Este paquete esta SANITIZADO: todas las credenciales/tokens fueron reemplazados por
  placeholders (`<PASSWORD>`, `<CLOUDFLARE_API_KEY>`, `<TOKEN>`, etc.). Poner las propias en `.env`.
- Los scrapers de cada cerealera dependen de la estructura del extranet de cada una y de la
  cuenta; hay que revisarlos/reescribirlos para el caso de uso del colega.
- NUNCA commitear el `.env` (ya esta en .gitignore).
