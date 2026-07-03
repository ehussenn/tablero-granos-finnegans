"""Cliente API de Finnegans (Teamplace) — autenticacion + endpoints.

Auth:  GET https://api.finneg.com/api/oauth/token?grant_type=client_credentials&client_id=...&client_secret=...
       -> devuelve solo el token en plano (no JSON).
Use:   header  Authorization: Bearer <token>  contra https://api.finneg.com/api/<endpoint>?...

Probado: /reports/resumenContratosVentaGranos funciona.
"""
from __future__ import annotations
import os, sys, json, time, urllib.parse, urllib.request
from pathlib import Path

BASE = "https://api.finneg.com/api"

# Cargar credenciales desde:
#  1) env vars (lo que usa GitHub Actions)
#  2) archivo .env en la raiz del proyecto (uso local)
def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

_load_dotenv()

CLIENT_ID = os.environ.get("FINNEGANS_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("FINNEGANS_CLIENT_SECRET", "")

if not CLIENT_ID or not CLIENT_SECRET:
    sys.stderr.write(
        "[!] Falta FINNEGANS_CLIENT_ID o FINNEGANS_CLIENT_SECRET.\n"
        "    Cargalas en un archivo .env en la raiz del proyecto, o en env vars.\n"
    )

_token_cache: dict = {"token": None, "expires_at": 0}


def get_token(force_refresh: bool = False) -> str:
    if not force_refresh and _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]
    url = f"{BASE}/oauth/token?grant_type=client_credentials&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        token = resp.read().decode("utf-8").strip()
    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + 50 * 60  # asumimos 1h, refrescamos a los 50min
    return token


def call(path: str, params: dict | None = None, timeout: int = 120) -> dict | list:
    token = get_token()
    qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None and v != ""})
    url = f"{BASE}{path}" + ("?" + qs if qs else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    # Reintentos ante timeouts / errores de red transitorios (la API de Finnegans se pone
    # flaky y hacía fallar todo el build de CI). 3 intentos con backoff.
    last_err = None
    for intento in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            return json.loads(body)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            if 500 <= e.code < 600 and intento < 2:      # 5xx: reintentar
                last_err = e; time.sleep(2 * (intento + 1)); continue
            raise RuntimeError(f"HTTP {e.code} en {url}\n  body: {err_body[:500]}") from None
        except Exception as e:                            # timeout / URLError / socket: reintentar
            last_err = e
            if intento < 2:
                sys.stderr.write(f"    [retry {intento+1}/3] {path}: {str(e)[:60]}\n")
                time.sleep(2 * (intento + 1)); continue
            raise
    raise last_err


# Mapeo de cada dataset a su endpoint + params default
ENDPOINTS = {
    "resumenContratosVentaGranos": {
        "path": "/reports/resumenContratosVentaGranos",
        "default_params": {
            "PARAMWEBREPORT_FechaDesde": "2022-01-01",
            "PARAMWEBREPORT_FechaHasta": "2030-12-31",
            "PARAMWEBREPORT_FechaEntregaMin": "2022-01-01",
            "PARAMWEBREPORT_FechaEntregaMax": "2030-12-31",
            "PARAMWEBREPORT_Empresa": "Agronasaja",
        },
    },
    "ResumenContratoCompraGranos": {
        "path": "/reports/ResumenContratoCompraGranos",
        "default_params": {
            "PARAMWEBREPORT_FechaDesde": "2022-01-01",
            "PARAMWEBREPORT_FechaHasta": "2030-12-31",
            "PARAMWEBREPORT_FechaEntregaMin": "2022-01-01",
            "PARAMWEBREPORT_FechaEntregaMax": "2030-12-31",
            "PARAMWEBREPORT_Empresa": "Agronasaja",
        },
    },
    "USR_RESSTOCKDEP": {
        "path": "/reports/USR_RESSTOCKDEP",
        "default_params": {
            "PARAMWEBREPORT_fecha": "getCurrentDate",
            "PARAMWEBREPORT_MonedaID": "PESOS",
        },
    },
    "USR_ComposicionSaldosResumenParaEmail_API": {
        "path": "/reports/USR_ComposicionSaldosResumenParaEmail_API",
        "default_params": {"PARAMWEBREPORT_FechaCorte": "getCurrentDate"},
    },
}


if __name__ == "__main__":
    print(f"Token: {get_token()[:20]}...")
    # Probamos sin Empresa primero (el filtro espera codigo, no nombre)
    params = {
        "PARAMWEBREPORT_FechaDesde": "2022-01-01",
        "PARAMWEBREPORT_FechaHasta": "2030-12-31",
        "PARAMWEBREPORT_FechaEntregaMin": "2022-01-01",
        "PARAMWEBREPORT_FechaEntregaMax": "2030-12-31",
    }
    print(f"\nProbando sin Empresa...")
    data = call("/reports/resumenContratosVentaGranos", params)
    if isinstance(data, list):
        print(f"  Filas: {len(data)}")
        if data:
            print(f"  Empresas distintas: {sorted(set(r.get('EMPRESA') for r in data))[:10]}")
            print(f"  Cosechas distintas: {sorted(set(r.get('COSECHA') or '' for r in data))[:10]}")
            # Filtrar a Agronasaja en python
            agro = [r for r in data if r.get("EMPRESA") == "Agronasaja"]
            print(f"  Filas EMPRESA=Agronasaja: {len(agro)}")
            # Total 2026
            r2026 = [r for r in agro if r.get("FECHA","").endswith("-2026") or "2026" in (r.get("FECHA") or "")]
            print(f"  Filas Agronasaja con fecha 2026: {len(r2026)}")
            # Conteo por cosecha en Agronasaja
            from collections import Counter
            print(f"\n  Cosechas Agronasaja:")
            for c, n in Counter(r.get("COSECHA","—") for r in agro).items():
                print(f"    {c!r}  {n}")
    else:
        print(f"  Respuesta no es lista: {type(data)}")
        print(f"  Contenido: {str(data)[:500]}")
