"""Despliegue: copias de seguridad cifradas, vigilancia, protección del secreto y archivos de configuración."""
import argparse
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from modelo_ia.api import app as app_mod
from modelo_ia.api.ajustes import PEPPER_DE_DESARROLLO, Ajustes
from modelo_ia.api.almacen import Almacen
from modelo_ia.despliegue import respaldo, vigilancia

RAIZ = Path(__file__).resolve().parents[1]
FRASE = "una-frase-de-prueba-bien-larga-123"


@pytest.fixture(autouse=True)
def _rapido(monkeypatch):
    monkeypatch.setattr(respaldo, "ITERACIONES", 2000)             # el valor real (600.000) es lento a propósito; aquí no hace falta


@pytest.fixture
def entorno(tmp_path):
    bd = tmp_path / "api.sqlite3"
    a = Almacen(bd, "p" * 40)
    a.inicializar()
    for n in ("Tienda A", "Tienda B"):
        a.crear_comercio(n)
    a.crear_clave(2, "comercio")
    frase = tmp_path / "frase"
    frase.write_text(FRASE); os.chmod(frase, 0o600)
    return {"bd": bd, "frase": frase, "dir": tmp_path / "resp", "tmp": tmp_path}


def _copia(e, ahora=None):
    return respaldo.crear(e["bd"], e["dir"], e["frase"], ahora)


# ------------------------------------------------------------------ copias de seguridad
def test_crear_y_verificar_una_copia_y_que_no_deje_nada_en_claro(entorno):
    r = _copia(entorno)
    crudo = r["archivo"].read_bytes()
    assert r["archivo"].name.startswith("antifraude_") and r["archivo"].suffix == ".afbk" and r["tablas"]["comercios"] == 3
    assert (r["archivo"].stat().st_mode & 0o777) == 0o600 and (entorno["dir"].stat().st_mode & 0o777) == 0o700
    assert b"SQLite format" not in crudo and b"comercios" not in crudo and b"Tienda A" not in crudo        # todo cifrado
    assert respaldo.verificar(r["archivo"], entorno["frase"]) == r["tablas"]
    assert not list(entorno["dir"].glob("afbk_*")) and not list(entorno["dir"].glob("*.tmp"))              # sin restos temporales (ni copias en claro)


def test_restaurar_deja_la_base_igual_y_usable(entorno):
    r = _copia(entorno)
    nuevo = entorno["tmp"] / "restaurada.sqlite3"
    x = respaldo.restaurar(r["archivo"], nuevo, entorno["frase"])
    assert x["tablas"] == r["tablas"] and (nuevo.stat().st_mode & 0o777) == 0o600
    a = Almacen(nuevo, "p" * 40); a.inicializar()                                                          # el servicio la abre sin problema
    assert a.autenticar("clave-inexistente") is None
    con = sqlite3.connect(nuevo)
    assert [r[0] for r in con.execute("SELECT nombre FROM comercios ORDER BY id")] == ["PLATAFORMA", "Tienda A", "Tienda B"]
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restaurar_no_pisa_una_base_existente_sin_permiso_y_la_conserva_si_se_fuerza(entorno):
    r = _copia(entorno)
    with pytest.raises(respaldo.ErrorDeRespaldo, match="--forzar"):
        respaldo.restaurar(r["archivo"], entorno["bd"], entorno["frase"])
    antes = entorno["bd"].read_bytes()
    x = respaldo.restaurar(r["archivo"], entorno["bd"], entorno["frase"], forzar=True)
    assert x["apartada"].exists() and x["apartada"].read_bytes() == antes                                  # la anterior no se pierde


@pytest.mark.parametrize("donde", ["magia", "iteraciones", "sal", "mac", "inicio_cifrado", "medio", "final"])
def test_una_copia_alterada_se_rechaza_antes_de_descifrar(entorno, donde):
    r = _copia(entorno)
    b = bytearray(r["archivo"].read_bytes()); m = len(respaldo.MAGIA)
    pos = {"magia": 1, "iteraciones": m + 2, "sal": m + 10, "mac": m + 30, "inicio_cifrado": m + 52 + 3, "medio": len(b) // 2, "final": len(b) - 1}[donde]
    b[pos] ^= 0x01
    r["archivo"].write_bytes(bytes(b))
    with pytest.raises(respaldo.ErrorDeRespaldo):
        respaldo.verificar(r["archivo"], entorno["frase"])


def test_archivos_truncados_ajenos_o_con_otra_frase_se_rechazan(entorno):
    r = _copia(entorno)
    crudo = r["archivo"].read_bytes()
    for contenido in (b"", b"hola", crudo[:60], crudo[:-5], crudo + b"extra", os.urandom(500)):
        r["archivo"].write_bytes(contenido)
        with pytest.raises(respaldo.ErrorDeRespaldo):
            respaldo.verificar(r["archivo"], entorno["frase"])
    r["archivo"].write_bytes(crudo)
    otra = entorno["tmp"] / "otra"; otra.write_text("otra-frase-diferente-pero-larga-99"); os.chmod(otra, 0o600)
    with pytest.raises(respaldo.ErrorDeRespaldo, match="alterada o la frase"):
        respaldo.verificar(r["archivo"], otra)
    assert respaldo.verificar(r["archivo"], entorno["frase"])                                              # y con la frase buena vuelve a servir


def test_la_frase_debe_ser_larga_de_una_linea_y_privada(entorno):
    f = entorno["tmp"] / "f"
    for texto, mensaje in (("corta", "demasiado corta"), ("una frase larga\notra linea que no debería estar ahí", "una sola línea")):
        f.write_text(texto); os.chmod(f, 0o600)
        with pytest.raises(respaldo.ErrorDeRespaldo, match=mensaje):
            respaldo.leer_frase(f)
    f.write_text(FRASE); os.chmod(f, 0o644)
    with pytest.raises(respaldo.ErrorDeRespaldo, match="chmod 600"):
        respaldo.leer_frase(f)
    os.chmod(f, 0o600)
    assert respaldo.leer_frase(f) == FRASE.encode()
    with pytest.raises(respaldo.ErrorDeRespaldo):
        respaldo.leer_frase(entorno["tmp"] / "no_existe")


def test_la_frase_nunca_va_en_la_linea_de_comandos(entorno, monkeypatch):
    vistos = []
    original = subprocess.run
    monkeypatch.setattr(respaldo.subprocess, "run", lambda args, *a, **k: (vistos.append(list(args)), original(args, *a, **k))[1])
    r = _copia(entorno)
    respaldo.verificar(r["archivo"], entorno["frase"])
    assert vistos and not any(FRASE in " ".join(map(str, a)) for a in vistos)
    assert any(f"file:{entorno['frase']}" in a for a in vistos[0])


def test_la_copia_es_consistente_aunque_el_servicio_siga_escribiendo(entorno):
    parar = threading.Event()

    def escribir():
        con = sqlite3.connect(entorno["bd"], timeout=30)
        while not parar.is_set():
            con.execute("INSERT INTO comercios (nombre, creado) VALUES ('x', 'y')"); con.commit()
    h = threading.Thread(target=escribir); h.start()
    try:
        copias = [_copia(entorno) for _ in range(4)]
    finally:
        parar.set(); h.join()
    for c in copias:
        assert sum(1 for _ in [respaldo.verificar(c["archivo"], entorno["frase"])]) == 1                   # pasa la revisión de integridad
    assert [c["tablas"]["comercios"] for c in copias] == sorted(c["tablas"]["comercios"] for c in copias)


def test_si_la_base_esta_danada_no_se_guarda_una_copia_mala(entorno):
    entorno["bd"].write_bytes(b"esto no es una base de datos" * 100)
    with pytest.raises(Exception):
        _copia(entorno)
    assert not list(entorno["dir"].glob("*.afbk"))


# ------------------------------------------------------------------ rotación y copia externa
def _falsas(carpeta, momentos):
    carpeta.mkdir(parents=True, exist_ok=True)
    for m in momentos:
        (carpeta / m.strftime("antifraude_%Y%m%d_%H%M%S.afbk")).write_bytes(b"x")


def test_la_rotacion_conserva_diarias_semanales_y_mensuales_y_nunca_la_mas_reciente(tmp_path):
    ahora = datetime(2026, 11, 14, 12, 0, 0)
    momentos = [ahora - timedelta(hours=6 * k) for k in range(0, 4 * 150)]            # 150 días, 4 copias al día
    _falsas(tmp_path, momentos)
    sim = respaldo.rotar(tmp_path, 7, 4, 3, simular=True)
    assert len(list(tmp_path.glob("*.afbk"))) == len(momentos) and len(sim["borradas"]) > 0                # simular no borra
    r = respaldo.rotar(tmp_path, 7, 4, 3)
    quedan = sorted(p.name for p in tmp_path.glob("*.afbk"))
    assert max(quedan) == momentos[0].strftime("antifraude_%Y%m%d_%H%M%S.afbk")                              # la más reciente sigue ahí
    assert 7 <= len(quedan) <= 7 + 4 + 3
    dias = {n[len('antifraude_'):len('antifraude_') + 8] for n in quedan}
    assert all((ahora - timedelta(days=d)).strftime("%Y%m%d") in dias for d in range(7))                    # un día cada uno de los últimos 7
    assert len(r["conservadas"]) == len(quedan)


def test_la_rotacion_ignora_archivos_ajenos_y_con_una_sola_copia_no_borra_nada(tmp_path):
    _falsas(tmp_path, [datetime(2026, 1, 1)])
    (tmp_path / "notas.txt").write_text("no me toques"); (tmp_path / "antifraude_malnombre.afbk").write_text("x")
    assert respaldo.rotar(tmp_path, 1, 1, 1)["borradas"] == []
    assert (tmp_path / "notas.txt").exists() and (tmp_path / "antifraude_malnombre.afbk").exists()


def test_la_copia_externa_se_ejecuta_sin_shell_y_si_falla_se_dice(entorno, monkeypatch):
    r = _copia(entorno)
    comando = f"{shlex.quote(sys.executable)} -c \"import sys; open(sys.argv[1] + '.copiado', 'w').write('ok')\" {{archivo}}"
    monkeypatch.setenv("AFBK_COMANDO_DESPUES", comando)
    respaldo.copiar_afuera(r["archivo"])
    assert Path(str(r["archivo"]) + ".copiado").read_text() == "ok"
    monkeypatch.setenv("AFBK_COMANDO_DESPUES", f"{shlex.quote(sys.executable)} -c \"import sys; sys.exit(3)\"")
    with pytest.raises(respaldo.ErrorDeRespaldo, match="FALLÓ"):
        respaldo.copiar_afuera(r["archivo"])
    monkeypatch.setenv("AFBK_COMANDO_DESPUES", "echo hola; rm -rf /tmp/no_existe_x")                         # el «;» no se interpreta como un segundo comando
    respaldo.copiar_afuera(r["archivo"])


def test_cada_vuelta_anota_si_salio_bien_o_mal_para_la_vigilancia(entorno):
    respaldo.una_vuelta(entorno["bd"], entorno["dir"], entorno["frase"], 7, 4, 3)
    e = json.loads((entorno["dir"] / respaldo.ESTADO).read_text())
    assert e["ok"] is True and e["archivo"].endswith(".afbk") and e["error"] is None
    with pytest.raises(respaldo.ErrorDeRespaldo):
        respaldo.una_vuelta(entorno["tmp"] / "no_existe.sqlite3", entorno["dir"], entorno["frase"], 7, 4, 3)
    e = json.loads((entorno["dir"] / respaldo.ESTADO).read_text())
    assert e["ok"] is False and e["error"]
    assert not any(FRASE in x.read_text(errors="ignore") for x in entorno["dir"].glob("*.json"))


def test_la_linea_de_comandos_de_respaldo(entorno, capsys):
    base = ["--bd", str(entorno["bd"]), "--carpeta", str(entorno["dir"]), "--frase-archivo", str(entorno["frase"])]
    assert respaldo.main(base + ["crear"]) == 0
    archivo = next(entorno["dir"].glob("*.afbk"))
    assert respaldo.main(base + ["verificar", str(archivo)]) == 0 and "VÁLIDA" in capsys.readouterr().out
    assert respaldo.main(base + ["restaurar", str(archivo), "--destino", str(entorno["tmp"] / "r.sqlite3")]) == 0
    assert respaldo.main(base + ["restaurar", str(archivo), "--destino", str(entorno["tmp"] / "r.sqlite3")]) == 2          # no pisa
    assert respaldo.main(base + ["rotar", "--simular"]) == 0


# ------------------------------------------------------------------ vigilancia
class _Servidor:
    """Un servicio falso que responde /v1/salud como se le diga, y que anota los avisos que recibe."""
    def __init__(self):
        self.salud, self.demora, self.avisos = {"estado": "ok", "advertencias": []}, 0.0, []
        yo = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                time.sleep(yo.demora)
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps(yo.salud).encode())

            def do_POST(self):
                yo.avisos.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])))); self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        self.http = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.http.server_port}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def cerrar(self):
        self.http.shutdown(); self.http.server_close()


@pytest.fixture
def servicio():
    s = _Servidor()
    yield s
    s.cerrar()


def test_la_vigilancia_distingue_sano_lento_con_advertencias_y_caido(servicio):
    assert max(n for n, _ in vigilancia.revisar_salud(servicio.url)) == vigilancia.OK
    servicio.salud = {"estado": "ok", "advertencias": ["La inteligencia de IP es SIMULADA"]}
    r = vigilancia.revisar_salud(servicio.url)
    assert max(n for n, _ in r) == vigilancia.AVISO and any("SIMULADA" in m for _, m in r)
    servicio.salud, servicio.demora = {"estado": "ok", "advertencias": []}, 0.5
    assert max(n for n, _ in vigilancia.revisar_salud(servicio.url, lento_seg=0.2, muy_lento_seg=5)) == vigilancia.AVISO
    assert max(n for n, _ in vigilancia.revisar_salud(servicio.url, lento_seg=0.1, muy_lento_seg=0.3)) == vigilancia.CRITICO
    servicio.salud, servicio.demora = {"estado": "degradado"}, 0
    assert max(n for n, _ in vigilancia.revisar_salud(servicio.url)) == vigilancia.CRITICO
    puerto_cerrado = "http://127.0.0.1:1"
    r = vigilancia.revisar_salud(puerto_cerrado)
    assert r[0][0] == vigilancia.CRITICO and "no responde" in r[0][1]


def test_la_vigilancia_de_copias_grita_cuando_dejan_de_hacerse(entorno):
    ahora = datetime.now(timezone.utc)
    carpeta = entorno["dir"]; carpeta.mkdir()

    def estado(ok, horas, error=None):
        (carpeta / "ultimo_respaldo.json").write_text(json.dumps({"ok": ok, "momento": (ahora - timedelta(hours=horas)).isoformat(), "archivo": "x.afbk", "error": error}))
        return vigilancia.revisar_respaldos(carpeta, 13, ahora)[0][0]
    assert vigilancia.revisar_respaldos(carpeta, 13, ahora)[0][0] == vigilancia.CRITICO                      # no hay registro
    assert estado(True, 2) == vigilancia.OK and estado(True, 14) == vigilancia.AVISO and estado(True, 30) == vigilancia.CRITICO
    assert estado(False, 1, "se llenó el disco") == vigilancia.CRITICO
    (carpeta / "ultimo_respaldo.json").write_text("esto no es json")
    assert vigilancia.revisar_respaldos(carpeta, 13, ahora)[0][0] == vigilancia.CRITICO


def test_la_vigilancia_revisa_base_disco_y_certificado(entorno, monkeypatch):
    assert max(n for n, _ in vigilancia.revisar_base(entorno["bd"])) == vigilancia.OK
    assert vigilancia.revisar_base(entorno["tmp"] / "no_existe.sqlite3")[0][0] == vigilancia.CRITICO
    entorno["bd"].write_bytes(b"basura" * 1000)
    assert any(n == vigilancia.CRITICO for n, _ in vigilancia.revisar_base(entorno["bd"]))
    uso = type("U", (), {})
    for libre, esperado in ((50, vigilancia.OK), (10, vigilancia.AVISO), (2, vigilancia.CRITICO)):
        monkeypatch.setattr(vigilancia.shutil, "disk_usage", lambda p, l=libre: type("U", (), {"free": l, "total": 100})())
        assert vigilancia.revisar_disco(entorno["bd"])[0][0] == esperado
    ahora = datetime(2029, 12, 22, tzinfo=timezone.utc)
    assert round(vigilancia.dias_para_vencer("Jan  1 00:00:00 2030 GMT", ahora)) == 10
    assert vigilancia.revisar_certificado("http://sitio")[0][0] == vigilancia.AVISO                           # sin https no hay certificado que revisar


def test_la_vigilancia_avisa_solo_cuando_el_estado_cambia_y_sin_datos_de_clientes(servicio, entorno, monkeypatch, capsys):
    ahora = datetime.now(timezone.utc)
    entorno["dir"].mkdir()
    (entorno["dir"] / "ultimo_respaldo.json").write_text(json.dumps({"ok": True, "momento": ahora.isoformat(), "archivo": "x", "error": None}))
    monkeypatch.setenv("MODELO_IA_ALERTA_URL", servicio.url)
    args = argparse.Namespace(url_interna=servicio.url, url_publica="", bd=entorno["bd"], respaldos=entorno["dir"], max_horas_respaldo=13, estado_archivo=str(entorno["tmp"] / "estado"))
    assert vigilancia.una_vez(args) == vigilancia.OK and servicio.avisos == []                                # primera vez: no hay cambio
    assert vigilancia.una_vez(args) == vigilancia.OK and servicio.avisos == []                                # sigue bien: no avisa
    servicio.salud = {"estado": "degradado"}
    assert vigilancia.una_vez(args) == vigilancia.CRITICO and len(servicio.avisos) == 1                       # cambió: avisa
    assert vigilancia.una_vez(args) == vigilancia.CRITICO and len(servicio.avisos) == 1                       # sigue mal: no repite
    servicio.salud = {"estado": "ok", "advertencias": []}
    vigilancia.una_vez(args)
    assert len(servicio.avisos) == 2 and servicio.avisos[1]["estado"] == "OK"                                 # volvió a la normalidad
    assert set(servicio.avisos[0]) == {"servicio", "estado", "mensajes"}


def test_la_vigilancia_no_manda_alertas_a_direcciones_http_ajenas(monkeypatch, capsys):
    vigilancia.notificar("http://ejemplo.com/aviso", vigilancia.CRITICO, [(vigilancia.CRITICO, "algo")])
    assert "https" in capsys.readouterr().err


# ------------------------------------------------------------------ el secreto y el modo producción
def test_en_produccion_el_servicio_se_niega_a_arrancar_con_un_secreto_malo(tmp_path, monkeypatch):
    monkeypatch.setenv("MODELO_IA_ENTORNO", "produccion")
    for pepper in (PEPPER_DE_DESARROLLO, "corto", "x" * 31):
        with pytest.raises(RuntimeError, match="NO arranca"):
            app_mod.crear_app(Ajustes(ruta_bd=tmp_path / "a.sqlite3", pepper=pepper))
    assert not (tmp_path / "a.sqlite3").exists()                                                             # ni siquiera creó la base
    app_mod.crear_app(Ajustes(ruta_bd=tmp_path / "b.sqlite3", pepper="s" * 40))                               # con un secreto bueno, arranca
    monkeypatch.delenv("MODELO_IA_ENTORNO")
    app_mod.crear_app(Ajustes(ruta_bd=tmp_path / "c.sqlite3", pepper=PEPPER_DE_DESARROLLO))                  # fuera de producción se permite (desarrollo)


def test_los_avisos_de_produccion_no_impiden_arrancar_pero_se_dicen():
    p = Ajustes(pepper="s" * 40).problemas_de_produccion()
    assert p["bloqueantes"] == [] and len(p["avisos"]) == 3 and any("SIMULADO" in a for a in p["avisos"])
    ok = Ajustes(pepper="s" * 40, durabilidad="FULL", proveedor_ip="real", csp_modo="enforce").problemas_de_produccion()
    assert ok == {"bloqueantes": [], "avisos": []}


def test_el_secreto_puede_venir_de_un_archivo_y_ese_manda_sobre_la_variable(tmp_path, monkeypatch):
    f = tmp_path / "pepper"; f.write_text("  " + "a" * 48 + "\n")
    monkeypatch.setenv("MODELO_IA_PEPPER", "variable-de-entorno-" + "z" * 30); monkeypatch.setenv("MODELO_IA_PEPPER_ARCHIVO", str(f))
    assert Ajustes.desde_entorno().pepper == "a" * 48
    f.write_text("  \n")
    with pytest.raises(RuntimeError, match="vacío"):
        Ajustes.desde_entorno()
    monkeypatch.setenv("MODELO_IA_PEPPER_ARCHIVO", str(tmp_path / "no_existe"))
    with pytest.raises(RuntimeError, match="No se pudo leer") as e:
        Ajustes.desde_entorno()
    assert "no_existe" in str(e.value) and str(tmp_path) not in str(e.value)                                 # no revela la ruta completa


# ------------------------------------------------------------------ archivos de configuración
def _leer(nombre):
    return (RAIZ / "despliegue" / nombre).read_text(encoding="utf-8")


def test_el_dockerfile_no_corre_como_administrador_y_tiene_comprobacion_de_salud():
    d = _leer("Dockerfile")
    assert re.search(r"^USER antifraude$", d, re.M) and "10001" in d and "HEALTHCHECK" in d and ":latest" not in d
    assert re.search(r"^FROM python:3\.\d+-slim", d, re.M) and "MODELO_IA_ENTORNO=produccion" in d and "DURABILIDAD=FULL" in d
    assert "--workers" in d and "0.0.0.0" in d and "--https" not in d


def test_las_dependencias_de_produccion_estan_fijadas_y_sin_herramientas_de_desarrollo():
    lineas = [l.split("#")[0].strip() for l in _leer("requirements-produccion.txt").splitlines()]
    lineas = [l for l in lineas if l]
    assert lineas and all("==" in l for l in lineas)
    nombres = {re.split(r"[=;<> ]", l)[0].lower() for l in lineas}
    assert {"scikit-learn", "pandas", "numpy", "joblib", "fastapi", "uvicorn", "pydantic"} <= nombres
    assert not (nombres & {"pytest", "playwright", "httpx", "openpyxl", "pluggy"})
    import importlib.metadata as md
    for n, v in (("scikit-learn", None), ("numpy", None), ("pandas", None), ("joblib", None)):
        fijada = next(l.split("==")[1].split(";")[0] for l in lineas if l.lower().startswith(n + "=="))
        assert md.version(n) == fijada, (n, md.version(n), fijada)                                          # lo fijado es lo que se probó


def test_el_compose_endurece_los_contenedores_y_solo_el_servidor_web_publica_puertos():
    c = _leer("docker-compose.yml")
    bloque = lambda nombre: re.search(rf"^  {nombre}:\n(.*?)(?=^  \w|^\w)", c, re.M | re.S).group(1)
    for base in ("read_only: true", 'cap_drop: ["ALL"]', "no-new-privileges:true"):
        assert base in c
    assert "ports:" not in bloque("api") and "ports:" not in bloque("respaldo") and "ports:" not in bloque("vigilancia") and 'ports: ["80:80", "443:443"' in bloque("caddy")
    assert "networks: [interna]" in bloque("api") and "internal: true" in c                               # la API no sale a internet
    assert "MODELO_IA_PEPPER_ARCHIVO" in bloque("api") and "MODELO_IA_PEPPER:" not in c                     # el secreto va en archivo
    assert "secrets/pepper" in c and "secrets/respaldo_frase" in c and "DOMINIO:?" in c


def test_el_caddyfile_cierra_lo_de_demostracion_y_limita_tamano_y_tiempos():
    c = _leer("Caddyfile")
    assert "{$DOMINIO}" in c and "respond @demo 404" in c and "/demo/*" in c and "/tienda" in c
    assert "max_size 64KB" in c and "read_body" in c and "Strict-Transport-Security" in c and "{$HSTS_SEGUNDOS:300}" in c
    assert "reverse_proxy api:8000" in c and "-Server" in c


def test_el_ejemplo_de_variables_no_trae_secretos_y_los_reales_estan_ignorados_por_git():
    e = _leer(".env.ejemplo")
    assert not re.search(r"^(?:MODELO_IA_PEPPER|.*FRASE|.*CLAVE|.*PASSWORD)\w*\s*=\s*\S+", e, re.M | re.I)
    assert "ejemplo.com" in e and "HSTS_SEGUNDOS=300" in e
    g = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert all(x in g for x in ("despliegue/.env", "despliegue/secretos/", "despliegue/respaldos/", "*.afbk"))
    di = (RAIZ / ".dockerignore").read_text(encoding="utf-8")
    assert all(x in di for x in ("datos", "tests", ".git", "**/*.sqlite3", "despliegue/secretos"))


def test_aunque_se_pida_conservar_cero_copias_la_mas_reciente_nunca_se_borra(tmp_path):
    _falsas(tmp_path, [datetime(2026, 1, k) for k in range(1, 6)])
    r = respaldo.rotar(tmp_path, 0, 0, 0)
    quedan = [p.name for p in tmp_path.glob("*.afbk")]
    assert quedan == ["antifraude_20260105_000000.afbk"] and len(r["borradas"]) == 4


def test_el_manual_explica_la_restauracion_sin_levantar_la_api_y_los_pasos_obligatorios():
    m = _leer("LEEME.md")
    comandos = re.findall(r"^\s*docker compose run [^\n]*restaurar[^\n]*$", m, re.M)
    assert comandos and all("--no-deps" in c for c in comandos)                       # sin --no-deps Compose arranca la API y se crea una base vacía
    assert "docker compose exec respaldo" in m and "[CRÍTICO]" in m and "chmod 644 secretos" in m and "NO lo reinicia" in m
    assert "No se pudo leer el archivo del secreto" in m and "Todavía no probado" in m


def test_el_caddyfile_deja_pasar_la_configuracion_de_las_pantallas_porque_en_produccion_solo_devuelve_vacio():
    linea = _leer("Caddyfile").split("@demo path")[1].split("\n")[0]
    assert "/init" not in linea and "/demo/*" in linea and "/tienda" in linea          # lo que devuelve /init en producción lo comprueba test_api
