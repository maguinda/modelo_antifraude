"""Descarga y verifica las bases públicas que usa el proveedor de IP «propio» (ubicación, operador, centros de datos y salidas de Tor).

    python -m modelo_ia.api.ip_datos descargar     # baja lo que falta o lo que tiene más de --dias días (decenas de MB)
    python -m modelo_ia.api.ip_datos estado        # qué hay, de cuándo es y si se puede leer

Todo queda en `modelo_ia/datos_ip/` (fuera de git) con un `manifiesto.json` (fuente, licencia, fecha, tamaño y huella SHA-256 de cada archivo).
Las descargas son atómicas: se baja a un archivo temporal, se comprueba que se puede leer y solo entonces reemplaza al anterior; si algo falla,
queda la versión vieja. DB-IP publica una base nueva cada mes, y cada mes cambia el nombre del archivo (por eso se prueba el mes actual y el anterior).
"""

import argparse
import gzip
import hashlib
import ipaddress
import json
import os
import sys
import tempfile
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

CARPETA = Path(__file__).resolve().parents[1] / "datos_ip"
TOPE_MB = 200  # ningún archivo de estas fuentes debería pasar de esto: si pasa, algo cambió y no se baja
AGENTE = "antifraude-pyme/1.0"  # identificador genérico, sin datos del proyecto ni de personas

FUENTES: Dict[str, Dict] = {
    "ciudad": {
        "url": "https://download.db-ip.com/free/dbip-city-lite-{mes}.mmdb.gz",
        "mensual": True,
        "gz": True,
        "archivo": "dbip-city-lite.mmdb",
        "licencia": "CC BY 4.0 (DB-IP.com): exige atribución",
    },
    "asn": {
        "url": "https://download.db-ip.com/free/dbip-asn-lite-{mes}.mmdb.gz",
        "mensual": True,
        "gz": True,
        "archivo": "dbip-asn-lite.mmdb",
        "licencia": "CC BY 4.0 (DB-IP.com): exige atribución",
    },
    "aws": {
        "url": "https://ip-ranges.amazonaws.com/ip-ranges.json",
        "archivo": "aws-ip-ranges.json",
        "licencia": "sin licencia publicada (revisar con abogado)",
    },
    "google": {
        "url": "https://www.gstatic.com/ipranges/cloud.json",
        "archivo": "google-cloud.json",
        "licencia": "sin licencia publicada (revisar con abogado)",
    },
    "tor": {
        "url": "https://check.torproject.org/torbulkexitlist",
        "archivo": "tor-salidas.txt",
        "licencia": "sin términos publicados (revisar con abogado)",
    },
}


def _meses(hoy: date) -> List[str]:
    ant = date(hoy.year - (hoy.month == 1), 12 if hoy.month == 1 else hoy.month - 1, 1)
    return [f"{hoy.year}-{hoy.month:02d}", f"{ant.year}-{ant.month:02d}"]


def _leer(url: str, tope_mb: int = TOPE_MB) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("solo se descarga por HTTPS")
    pet = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(pet, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        if total > tope_mb * 1024 * 1024:
            raise ValueError(f"el archivo pesa {total // 2**20} MB (tope {tope_mb} MB): no se baja")
        datos = r.read(tope_mb * 1024 * 1024 + 1)
    if len(datos) > tope_mb * 1024 * 1024:
        raise ValueError(f"el archivo supera {tope_mb} MB: no se baja")
    return datos


def verificar(clave: str, ruta: Path) -> str:
    """Comprueba que el archivo se puede LEER como lo que dice ser. Devuelve un texto corto de lo comprobado o lanza ValueError."""
    if clave in ("ciudad", "asn"):
        import maxminddb

        with maxminddb.open_database(str(ruta)) as bd:
            prueba = bd.get("8.8.8.8")
            if not isinstance(prueba, dict):
                raise ValueError("la base se abre, pero no devuelve datos de una IP conocida")
            return f"{bd.metadata().record_size} bits, {bd.metadata().node_count} nodos"
    if clave == "tor":
        ips = [l.strip() for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(ips) < 100:
            raise ValueError(f"la lista de Tor trae solo {len(ips)} direcciones (se esperan cientos)")
        for ip in ips:
            ipaddress.ip_address(ip)
        return f"{len(ips)} direcciones"
    doc = json.loads(ruta.read_text(encoding="utf-8"))
    if clave == "aws":
        n = len(doc["prefixes"])
    else:
        n = len(doc["prefixes"])
    if n < 100:
        raise ValueError(f"solo {n} rangos (se esperan miles)")
    return f"{n} rangos"


def _manifiesto(carpeta: Path) -> Dict:
    try:
        return json.loads((carpeta / "manifiesto.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def descargar(
    carpeta: Path = CARPETA,
    dias: int = 25,
    hoy: Optional[date] = None,
    leer: Callable[[str], bytes] = _leer,
    solo: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Baja lo que falta o está vencido. Devuelve {fuente: resultado}. Una falla en una fuente no borra ni toca lo ya descargado."""
    carpeta.mkdir(parents=True, exist_ok=True)
    hoy = hoy or date.today()
    man, resultado = _manifiesto(carpeta), {}
    for clave, f in FUENTES.items():
        if solo and clave not in solo:
            continue
        destino = carpeta / f["archivo"]
        previo = man.get(clave)
        if previo and destino.exists() and (hoy - date.fromisoformat(previo["descargado"][:10])).days < dias:
            resultado[clave] = "vigente"
            continue
        urls = [f["url"].format(mes=m) for m in _meses(hoy)] if f.get("mensual") else [f["url"]]
        error = None
        for url in urls:
            try:
                crudo = leer(url)
                datos = gzip.decompress(crudo) if f.get("gz") else crudo
                with tempfile.NamedTemporaryFile(dir=carpeta, delete=False, suffix=".parte") as t:
                    t.write(datos)
                    parte = Path(t.name)
                try:
                    detalle = verificar(clave, parte)
                    os.replace(parte, destino)
                finally:
                    if parte.exists():
                        parte.unlink()
                man[clave] = {
                    "fuente": url,
                    "licencia": f["licencia"],
                    "descargado": f"{hoy.isoformat()}T{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z",
                    "bytes": len(datos),
                    "sha256": hashlib.sha256(datos).hexdigest(),
                    "comprobado": detalle,
                }
                resultado[clave] = f"descargado ({len(datos) / 2**20:.1f} MB; {detalle})"
                error = None
                break
            except Exception as e:  # se prueba la siguiente dirección (el mes anterior); se conserva lo que ya había
                error = f"{type(e).__name__}: {str(e)[:120]}"
        if error:
            resultado[clave] = f"FALLÓ ({error}); " + (
                "se conserva la versión anterior" if destino.exists() else "no hay versión anterior"
            )
    tmp = carpeta / "manifiesto.json.parte"
    tmp.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, carpeta / "manifiesto.json")
    return resultado


def estado(carpeta: Path = CARPETA) -> Dict[str, str]:
    man, salida = _manifiesto(carpeta), {}
    for clave, f in FUENTES.items():
        ruta = carpeta / f["archivo"]
        if not ruta.exists():
            salida[clave] = "falta"
            continue
        try:
            salida[clave] = f"ok, {man.get(clave, {}).get('descargado', '?')[:10]}, {verificar(clave, ruta)}"
        except Exception as e:
            salida[clave] = f"DAÑADO ({type(e).__name__})"
    return salida


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bases públicas de IP para el proveedor «propio».")
    ap.add_argument("orden", choices=["descargar", "estado"])
    ap.add_argument("--dias", type=int, default=25, help="no volver a bajar lo que tenga menos de estos días")
    ap.add_argument("--carpeta", type=Path, default=CARPETA)
    a = ap.parse_args(argv)
    res = descargar(a.carpeta, a.dias) if a.orden == "descargar" else estado(a.carpeta)
    for k, v in res.items():
        print(f"{k:8s} {v}")
    return 1 if any(v.startswith(("FALLÓ", "DAÑADO")) for v in res.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
