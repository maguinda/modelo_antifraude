"""Certificado de PRUEBA para HTTPS en tu computador (autofirmado). No sirve para producción: allí lo pone un proxy con un certificado real."""
import os
import subprocess
import time
from pathlib import Path
from typing import Tuple

CARPETA = Path(__file__).resolve().parent / "certificados_locales"
VALIDEZ_DIAS = 30


def asegurar(carpeta: Path = CARPETA) -> Tuple[Path, Path]:
    """Devuelve (certificado, llave). Los crea con openssl si no existen o si les quedan menos de 7 días."""
    carpeta.mkdir(exist_ok=True)
    os.chmod(carpeta, 0o700)
    cert, llave = carpeta / "cert.pem", carpeta / "llave.pem"
    vigente = cert.exists() and llave.exists() and (time.time() - cert.stat().st_mtime) < (VALIDEZ_DIAS - 7) * 86400
    if not vigente:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(llave), "-out", str(cert),
                        "-days", str(VALIDEZ_DIAS), "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"], check=True, capture_output=True)
        os.chmod(llave, 0o600)
    return cert, llave
