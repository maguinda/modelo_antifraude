"""Vigilancia del servicio: una revisión corta que dice si algo anda mal. Solo usa la librería estándar.

    python -m modelo_ia.despliegue.vigilancia --url-interna http://api:8000 --bd /datos/api.sqlite3 --respaldos /respaldos --url-publica https://antifraude.tudominio.com
    ...  --cada-minutos 5          # se queda corriendo (es lo que hace el contenedor «vigilancia»)

Qué revisa: que el servicio responda y rápido, que no tenga advertencias, el vencimiento del certificado, el espacio en disco, la integridad de la base
y, lo más importante, que las copias de seguridad SIGAN haciéndose. Códigos de salida: 0 todo bien, 1 aviso, 2 crítico.
Si defines MODELO_IA_ALERTA_URL manda un aviso (POST con JSON) solo cuando el estado CAMBIA, para no llenarte de mensajes; no incluye datos de clientes.
"""
import argparse
import json
import os
import shutil
import socket
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

OK, AVISO, CRITICO = 0, 1, 2
NOMBRES = {OK: "OK", AVISO: "AVISO", CRITICO: "CRÍTICO"}
Resultado = Tuple[int, str]


def revisar_salud(url_interna: str, lento_seg: float = 1.5, muy_lento_seg: float = 5.0, abrir: Optional[Callable] = None) -> List[Resultado]:
    abrir = abrir or (lambda u, t: urllib.request.urlopen(u, timeout=t))
    t0 = time.perf_counter()
    try:
        with abrir(url_interna.rstrip("/") + "/v1/salud", muy_lento_seg + 2) as r:
            cuerpo = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, socket.timeout) as e:
        return [(CRITICO, f"El servicio no responde en /v1/salud ({type(e).__name__}).")]
    seg = time.perf_counter() - t0
    salida: List[Resultado] = []
    if cuerpo.get("estado") != "ok":
        salida.append((CRITICO, f"El servicio dice que su estado es «{cuerpo.get('estado')}»."))
    else:
        salida.append((OK, f"El servicio responde ({seg * 1000:.0f} ms)."))
    if seg > muy_lento_seg:
        salida.append((CRITICO, f"Responde muy lento: {seg:.1f} s."))
    elif seg > lento_seg:
        salida.append((AVISO, f"Responde lento: {seg:.1f} s."))
    for a in cuerpo.get("advertencias") or []:
        salida.append((AVISO, "Advertencia del servicio: " + str(a)[:200]))
    return salida


def dias_para_vencer(not_after: str, ahora: Optional[datetime] = None) -> float:
    vence = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=timezone.utc)
    return ((ahora or datetime.now(timezone.utc)) - vence).total_seconds() / -86400.0


def revisar_certificado(url_publica: str, avisar_dias: int = 14, critico_dias: int = 5) -> List[Resultado]:
    u = urlparse(url_publica)
    if u.scheme != "https" or not u.hostname:
        return [(AVISO, "La dirección pública no es https: no se puede revisar el certificado.")]
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((u.hostname, u.port or 443), timeout=8) as s, ctx.wrap_socket(s, server_hostname=u.hostname) as t:
            dias = dias_para_vencer(t.getpeercert()["notAfter"])
    except ssl.SSLError as e:
        return [(CRITICO, f"El certificado de {u.hostname} no es válido ({type(e).__name__}).")]
    except (OSError, KeyError) as e:
        return [(CRITICO, f"No se pudo revisar el certificado de {u.hostname} ({type(e).__name__}).")]
    nivel = CRITICO if dias < critico_dias else AVISO if dias < avisar_dias else OK
    return [(nivel, f"El certificado vence en {dias:.0f} días.")]


def revisar_disco(bd: Path, aviso_pct: float = 15.0, critico_pct: float = 5.0) -> List[Resultado]:
    try:
        uso = shutil.disk_usage(Path(bd).parent)
    except OSError:
        return [(CRITICO, "No se pudo medir el espacio en disco.")]
    libre = 100.0 * uso.free / uso.total
    nivel = CRITICO if libre < critico_pct else AVISO if libre < aviso_pct else OK
    return [(nivel, f"Espacio libre en disco: {libre:.0f} %.")]


def revisar_base(bd: Path, wal_aviso_mb: int = 256) -> List[Resultado]:
    bd = Path(bd)
    if not bd.is_file():
        return [(CRITICO, "No existe la base de datos.")]
    salida: List[Resultado] = []
    try:
        con = sqlite3.connect(str(bd), timeout=5)
        try:
            ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            con.close()
        salida.append((OK, "La base de datos pasa la revisión rápida de integridad.") if ok else (CRITICO, "La base de datos NO pasa la revisión de integridad."))
    except sqlite3.Error as e:
        salida.append((CRITICO, f"No se pudo revisar la base de datos ({type(e).__name__})."))
    wal = bd.with_name(bd.name + "-wal")
    if wal.exists() and wal.stat().st_size > wal_aviso_mb * 1024 * 1024:
        salida.append((AVISO, "El archivo de registro de la base (WAL) está muy grande: puede haber una conexión que no cierra."))
    return salida


def revisar_respaldos(carpeta: Path, max_horas: float = 13.0, ahora: Optional[datetime] = None) -> List[Resultado]:
    ruta = Path(carpeta) / "ultimo_respaldo.json"
    if not ruta.is_file():
        return [(CRITICO, "Todavía no hay ninguna copia de seguridad registrada.")]
    try:
        e = json.loads(ruta.read_text(encoding="utf-8"))
        momento = datetime.fromisoformat(e["momento"])
    except (OSError, ValueError, KeyError):
        return [(CRITICO, "El registro de copias de seguridad está dañado.")]
    horas = ((ahora or datetime.now(timezone.utc)) - momento).total_seconds() / 3600
    if not e.get("ok"):
        return [(CRITICO, f"La última copia FALLÓ hace {horas:.0f} h: {str(e.get('error'))[:150]}")]
    if horas > 2 * max_horas:
        return [(CRITICO, f"La última copia buena tiene {horas:.0f} h: las copias dejaron de hacerse.")]
    if horas > max_horas:
        return [(AVISO, f"La última copia buena tiene {horas:.0f} h (se esperaba una cada pocas horas).")]
    return [(OK, f"Última copia buena hace {horas:.1f} h.")]


def ejecutar(a: argparse.Namespace) -> Tuple[int, List[Resultado]]:
    res: List[Resultado] = []
    res += revisar_salud(a.url_interna)
    if a.url_publica:
        res += revisar_certificado(a.url_publica)
    res += revisar_disco(a.bd)
    res += revisar_base(a.bd)
    res += revisar_respaldos(a.respaldos, a.max_horas_respaldo)
    return max(n for n, _ in res), res


def notificar(url: str, nivel: int, res: List[Resultado]) -> None:
    u = urlparse(url)
    if u.scheme != "https" and u.hostname not in ("127.0.0.1", "localhost"):
        print("Aviso: MODELO_IA_ALERTA_URL debe ser https; no se envió la alerta.", file=sys.stderr)
        return
    cuerpo = json.dumps({"servicio": "antifraude", "estado": NOMBRES[nivel], "mensajes": [m for n, m in res if n > OK] or ["Todo volvió a la normalidad."]}).encode("utf-8")
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=cuerpo, headers={"Content-Type": "application/json"}), timeout=10).read()
    except (urllib.error.URLError, OSError) as e:
        print(f"Aviso: no se pudo enviar la alerta ({type(e).__name__}).", file=sys.stderr)


def una_vez(a: argparse.Namespace) -> int:
    nivel, res = ejecutar(a)
    for n, m in res:
        print(f"[{NOMBRES[n]}] {m}")
    alerta = os.environ.get("MODELO_IA_ALERTA_URL", "").strip()
    estado = Path(a.estado_archivo)
    previo = None
    try:
        previo = int(estado.read_text().strip())
    except (OSError, ValueError):
        pass
    if alerta and previo is not None and previo != nivel:                       # solo cuando CAMBIA el estado
        notificar(alerta, nivel, res)
    try:
        estado.write_text(str(nivel))
    except OSError:
        pass
    return nivel


def main(argv=None) -> int:
    e = os.environ.get
    ap = argparse.ArgumentParser(prog="python -m modelo_ia.despliegue.vigilancia", description="Revisión de salud del servicio antifraude.")
    ap.add_argument("--url-interna", default=e("MODELO_IA_URL_INTERNA", "http://127.0.0.1:8000"))
    ap.add_argument("--url-publica", default=e("MODELO_IA_URL_PUBLICA", ""))
    ap.add_argument("--bd", type=Path, default=Path(e("MODELO_IA_BD", "/datos/api.sqlite3")))
    ap.add_argument("--respaldos", type=Path, default=Path(e("MODELO_IA_RESPALDOS", "/respaldos")))
    ap.add_argument("--max-horas-respaldo", type=float, default=float(e("MODELO_IA_MAX_HORAS_RESPALDO", "13")))
    ap.add_argument("--estado-archivo", default=e("MODELO_IA_VIGILANCIA_ESTADO", "/tmp/vigilancia_estado"))
    ap.add_argument("--cada-minutos", type=float, default=0.0, help="0 = una sola revisión y salir")
    a = ap.parse_args(argv)
    if a.cada_minutos <= 0:
        return una_vez(a)
    while True:
        una_vez(a)
        sys.stdout.flush()
        time.sleep(max(30.0, a.cada_minutos * 60))


if __name__ == "__main__":
    sys.exit(main())
