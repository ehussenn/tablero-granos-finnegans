"""Carga credenciales desde el .env de la raiz del proyecto y las expone via need().

Uso en cualquier script de scripts/:

    from _env import need
    USER = need("BALANZA_USER")
    PASS = need("BALANZA_PASS")

Regla permanente (incidente 2026-08): NUNCA una credencial hardcodeada en un
script — siempre en el .env local (gitignoreado) o en variables de entorno.
"""
from __future__ import annotations
import os
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def need(key: str) -> str:
    """Devuelve el valor de la variable, o corta con un mensaje claro si falta."""
    v = os.environ.get(key, "").strip()
    if not v:
        raise SystemExit(f"[!] Falta {key} en el .env de la raiz del proyecto.")
    return v
