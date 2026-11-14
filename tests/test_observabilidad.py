"""Observabilidad: identificador de petición, registros (logs) y métricas.

Lo más importante que vigilan: (1) un registro NUNCA lleva la clave de API, el cuerpo, el query string ni la IP; (2) un identificador hostil del cliente se
reemplaza; (3) las métricas solo las ve un administrador y no crecen sin límite (rutas como plantilla, sin series por comercio).
"""

import io
import json
import logging
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from modelo_ia.api import observabilidad as ob
from modelo_ia.tests.test_api import artefacto, construir_contexto, pago  # noqa: F401  (artefacto es un fixture)

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RAIZ = Path(__file__).resolve().parents[2]  # la carpeta que contiene modelo_ia/


@pytest.fixture
def ctx(tmp_path, artefacto):  # noqa: F811
    return construir_contexto(tmp_path / "o", artefacto)


@pytest.fixture
def registro():
    """Deja los registros en JSON dentro de un texto que la prueba puede leer, y al terminar quita ese manejador."""
    flujo = io.StringIO()
    manejador = ob.configurar_logs("json", flujo=flujo)
    yield flujo
    for nombre in ("modelo_ia", "antifraude"):
        logger = logging.getLogger(nombre)
        logger.handlers = [h for h in logger.handlers if h is not manejador]


def lineas(flujo):
    return [json.loads(x) for x in flujo.getvalue().splitlines() if x.strip()]


def peticiones(flujo):
    return [x for x in lineas(flujo) if x.get("evento") == "peticion"]


def metricas_texto(ctx):
    r = ctx.cliente.get("/v1/metricas", headers=ctx.h("admin"))
    assert r.status_code == 200
    return r.text


def valor(texto, serie):
    """El valor de una serie exacta (con todas sus etiquetas) o 0 si todavía no existe."""
    m = re.search(r"^" + re.escape(serie) + r" ([0-9.e+-]+)$", texto, re.M)
    return float(m.group(1)) if m else 0.0


# ------------------------------------------------------------------ identificador de petición
def test_cada_respuesta_trae_x_request_id_y_solo_se_respeta_uno_valido(ctx):
    r = ctx.cliente.get("/v1/salud")
    assert UUID.match(r.headers["x-request-id"])
    assert (
        ctx.cliente.get("/v1/salud", headers={"X-Request-ID": "mi-id-de-prueba-123"}).headers["x-request-id"]
        == "mi-id-de-prueba-123"
    )
    hostiles = [
        "corto",
        "x" * 65,
        "<script>alert(1)</script>",
        "con espacios aqui 123",
        "a;b=c&d1234567",
        "%0d%0aSet-Cookie:x12",
        "ñ" * 10,
        "../../etc/passwd",
    ]
    for malo in hostiles:
        try:
            r = ctx.cliente.get("/v1/salud", headers={"X-Request-ID": malo})
        except (UnicodeEncodeError, ValueError):  # el cliente ni siquiera puede enviarlo: tampoco llegaría al servidor
            continue
        assert UUID.match(r.headers["x-request-id"]) and r.headers["x-request-id"] != malo, malo
    assert len({ctx.cliente.get("/v1/salud").headers["x-request-id"] for _ in range(5)}) == 5  # uno nuevo por petición


def test_un_id_con_salto_de_linea_al_final_se_reemplaza_aunque_la_expresion_use_signo_de_fin():
    """Un servidor HTTP real no entrega un salto de línea dentro de una cabecera, pero un cliente ASGI directo sí; `$` lo habría aceptado."""
    import asyncio

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    for hostil in (b"abcdefgh\n", b"abcdefgh\r\n", b"\nabcdefgh", b"abcd\nefgh"):
        mensajes = []

        async def enviar(m):
            mensajes.append(m)

        async def recibir():
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {"type": "http", "method": "GET", "path": "/x", "headers": [(b"x-request-id", hostil)]}
        asyncio.run(ob.ObservabilidadMiddleware(app)(scope, recibir, enviar))
        devuelto = dict(mensajes[0]["headers"])[b"x-request-id"].decode()
        assert UUID.match(devuelto), (hostil, devuelto)


def test_los_errores_llevan_el_mismo_request_id_en_el_cuerpo_y_en_la_cabecera(ctx):
    pedidos = [
        ("get", "/v1/yo", {}, 401),  # sin clave
        ("get", "/v1/metricas", {"headers": ctx.h("A")}, 403),  # un comercio en ruta de administrador
        ("get", "/no/existe", {}, 404),
        ("get", "/v1/evaluaciones/00000000-0000-4000-8000-000000000000", {"headers": ctx.h("A")}, 404),
        ("post", "/v1/evaluaciones", {"headers": ctx.h("A"), "json": {"id_transaccion": "x"}}, 422),
        (
            "post",
            "/v1/evaluaciones",
            {"headers": ctx.h("A"), "content": b"x" * 200_000},
            413,
        ),  # lo corta otro middleware: el nuestro va por fuera
    ]
    for metodo, ruta, kw, esperado in pedidos:
        r = getattr(ctx.cliente, metodo)(ruta, **kw)
        assert r.status_code == esperado, (ruta, r.status_code)
        assert r.headers["content-type"].startswith("application/problem+json")
        assert UUID.match(r.headers["x-request-id"]) and r.json()["request_id"] == r.headers["x-request-id"], ruta
    r = ctx.cliente.get("/no/existe", headers={"X-Request-ID": "reclamo-del-cliente-42"})
    assert r.json()["request_id"] == "reclamo-del-cliente-42"  # el id que mandó el cliente es el que se cita


def test_un_error_500_tambien_lleva_request_id_y_su_registro(ctx, registro):
    @ctx.app.get("/_falla_de_prueba")
    def falla():
        raise RuntimeError("falla simulada")

    r = TestClient(ctx.app, raise_server_exceptions=False).get(
        "/_falla_de_prueba", headers={"X-Request-ID": "id-del-error-500"}
    )
    assert (
        r.status_code == 500
        and r.json()["request_id"] == "id-del-error-500"
        and r.headers["x-request-id"] == "id-del-error-500"
    )
    registros = [x for x in lineas(registro) if x.get("request_id") == "id-del-error-500"]
    # el detalle técnico queda en el registro, con su traza para poder encontrar dónde falló
    assert any(x["mensaje"] == "error inesperado" and x["excepcion"] == "RuntimeError" for x in registros)
    assert any("RuntimeError: falla simulada" in x.get("traza", "") for x in registros)
    assert any(x.get("evento") == "peticion" and x["estado"] == 500 and x["nivel"] == "WARNING" for x in registros)
    assert "falla simulada" not in r.text  # y el cliente no ve nada técnico


def test_el_identificador_no_se_mezcla_entre_peticiones_simultaneas(ctx, registro):
    ids = [f"concurrente-{i:03d}-abcdef" for i in range(24)]

    def pedir(i):
        r = TestClient(ctx.app).get("/v1/yo", headers={"X-Request-ID": i})
        return i, r.headers["x-request-id"], r.json()["request_id"]

    with ThreadPoolExecutor(8) as pool:
        resultados = list(pool.map(pedir, ids))
    assert all(a == b == c for a, b, c in resultados)
    vistos = sorted(x["request_id"] for x in peticiones(registro))
    assert vistos == sorted(ids)  # cada petición dejó exactamente un registro, con SU identificador


# ------------------------------------------------------------------ registros
def test_un_registro_por_peticion_con_la_ruta_como_plantilla_y_el_rol(ctx, registro):
    a = ctx.evaluar(pago("T-1", "C-1"), "A")
    assert a.status_code == 201
    ident = a.json()["id_evaluacion"]
    ctx.cliente.get(f"/v1/evaluaciones/{ident}", headers=ctx.h("A"))
    ctx.cliente.get("/v1/evaluaciones/00000000-0000-4000-8000-000000000000", headers=ctx.h("A"))
    ctx.cliente.get("/ruta/inventada/12345")
    ctx.cliente.get("/v1/panel/resumen", headers=ctx.h("admin"))
    ctx.cliente.get("/v1/salud")
    reg = peticiones(registro)
    assert [(x["metodo"], x["ruta"], x["estado"]) for x in reg] == [
        ("POST", "/v1/evaluaciones", 201),
        ("GET", "/v1/evaluaciones/{id_evaluacion}", 200),
        ("GET", "/v1/evaluaciones/{id_evaluacion}", 404),  # dos ids distintos, UNA sola plantilla
        ("GET", "desconocida", 404),  # nunca el valor real de una ruta inventada
    ] + [("GET", "/v1/panel/resumen", 200)]  # /v1/salud no se registra (ruido de monitoreo)
    assert [x.get("rol") for x in reg] == ["comercio", "comercio", "comercio", None, "admin"]
    assert [x.get("comercio_id") for x in reg] == [ctx.comercios["A"]] * 3 + [
        None,
        None,
    ]  # el número de comercio solo para comercios
    assert all(
        isinstance(x["duracion_ms"], float) and x["ts"].endswith("Z") and UUID.match(x["request_id"]) for x in reg
    )
    # lista cerrada: un campo nuevo (clave, query, cuerpo, IP...) hace fallar esto aunque el filtro de claves lo tapara
    permitidos = {
        "ts",
        "nivel",
        "logger",
        "mensaje",
        "request_id",
        "evento",
        "metodo",
        "ruta",
        "estado",
        "duracion_ms",
        "rol",
        "comercio_id",
    }
    for x in reg:
        assert set(x) <= permitidos, set(x) - permitidos
    assert ident not in registro.getvalue()  # ni el id real de la evaluación aparece


def test_un_registro_nunca_lleva_clave_cuerpo_query_ni_ip(ctx, registro):
    clave = ctx.h("A")["X-API-Key"]
    clave_falsa = "mia_comercio_" + "CANARIOCLAVEFALSA0123456789ABCDEFGH"
    cuerpo = pago("T-CANARIO-TX", "CLIENTE-CANARIO-XYZ", ciudad="CIUDAD-CANARIO")
    cuerpo["dispositivo"] = dict(cuerpo["dispositivo"], huella="FP-CANARIO-HUELLA")
    cuerpo["red"] = {"ip": "203.0.113.77"}
    assert ctx.cliente.post("/v1/evaluaciones", json=cuerpo, headers=ctx.h("A")).status_code == 201
    ctx.cliente.post(
        "/v1/evaluaciones", json={"id_cliente": "CLIENTE-CANARIO-XYZ"}, headers=ctx.h("A")
    )  # 422 con datos del cliente
    ctx.cliente.get("/v1/yo", headers={"X-API-Key": clave_falsa})  # 401 con una clave inventada
    ctx.cliente.get(
        "/v1/panel/transacciones?q=CANARIO-QUERY&ip=ABCDEF01&id_cliente=CLIENTE-CANARIO-XYZ", headers=ctx.h("admin")
    )
    ctx.cliente.get("/v1/panel/transacciones?ip=NO-ES-UN-CODIGO", headers=ctx.h("admin"))  # 422
    texto = registro.getvalue()
    assert texto.count("\n") >= 5
    for secreto in (
        clave,
        clave_falsa,
        ctx.h("admin")["X-API-Key"],
        "CANARIO",
        "203.0.113.77",
        "CIUDAD",
        "ABCDEF01",
        "NO-ES-UN-CODIGO",
        "Authorization",
    ):
        assert secreto not in texto, f"el registro contiene «{secreto}»"


def test_el_filtro_tapa_una_clave_que_se_cuele_en_un_mensaje_o_una_traza(registro):
    clave = "mia_admin_" + "Z" * 43
    assert ob.redactar(f"falló con {clave} y siguió") == "falló con [clave-oculta] y siguió"
    log = logging.getLogger("modelo_ia.api")
    try:
        raise ValueError(f"error con la clave {clave}")
    except ValueError:
        log.exception("se cayó usando %s", clave)
    texto = registro.getvalue()
    assert (
        "ZZZZ" not in texto and "[clave-oculta]" in texto and "ValueError" in texto
    )  # se ve el tipo del error, no la clave


def test_los_formatos_de_registro_y_su_validacion(monkeypatch):
    assert isinstance(ob.configurar_logs("auto", "produccion", flujo=io.StringIO()).formatter, ob.FormatoJSON)
    assert isinstance(ob.configurar_logs("auto", None, flujo=io.StringIO()).formatter, ob.FormatoTexto)
    assert isinstance(ob.configurar_logs("json", None, flujo=io.StringIO()).formatter, ob.FormatoJSON)
    assert isinstance(ob.configurar_logs("texto", "produccion", flujo=io.StringIO()).formatter, ob.FormatoTexto)
    with pytest.raises(RuntimeError, match="MODELO_IA_LOG_FORMATO"):
        ob.configurar_logs("xml")
    flujo = io.StringIO()
    manejador = ob.configurar_logs("texto", flujo=flujo)
    logging.getLogger("modelo_ia.acceso").info("GET /v1/yo 200 1.0 ms")
    assert re.match(r"^\d\d:\d\d:\d\d INFO\s+\[-\] GET /v1/yo 200 1.0 ms\n$", flujo.getvalue())
    for nombre in ("modelo_ia", "antifraude"):
        logging.getLogger(nombre).handlers = [h for h in logging.getLogger(nombre).handlers if h is not manejador]
    monkeypatch.setenv("MODELO_IA_LOG_FORMATO", " JSON ")
    from modelo_ia.api.ajustes import Ajustes

    assert Ajustes.desde_entorno().log_formato == "json"


def test_configurar_logs_repetido_no_duplica_manejadores():
    for _ in range(3):
        ob.configurar_logs("json", flujo=io.StringIO())
    for nombre in ("modelo_ia", "antifraude"):
        assert sum(getattr(h, "_antifraude", False) for h in logging.getLogger(nombre).handlers) == 1
    for nombre in ("modelo_ia", "antifraude"):
        logging.getLogger(nombre).handlers = [
            h for h in logging.getLogger(nombre).handlers if not getattr(h, "_antifraude", False)
        ]


# ------------------------------------------------------------------ métricas
def test_las_metricas_solo_las_ve_un_administrador(ctx):
    assert ctx.cliente.get("/v1/metricas").status_code == 401
    assert ctx.cliente.get("/v1/metricas", headers={"X-API-Key": "falsa"}).status_code == 401
    for rol in ("A", "B", "analista"):
        assert ctx.cliente.get("/v1/metricas", headers=ctx.h(rol)).status_code == 403, rol
    r = ctx.cliente.get("/v1/metricas", headers=ctx.h("admin"))
    assert (
        r.status_code == 200
        and r.headers["content-type"].startswith("text/plain")
        and r.headers["cache-control"] == "no-store"
    )
    assert (
        "# TYPE antifraude_http_peticiones_total counter" in r.text
        and "antifraude_http_duracion_segundos_bucket" in r.text
    )


def test_los_contadores_suben_exactamente_lo_que_se_pidio_y_la_ruta_es_plantilla(ctx):
    serie = 'antifraude_http_peticiones_total{estado="200",metodo="GET",ruta="/v1/yo"}'
    antes = valor(metricas_texto(ctx), serie)
    for _ in range(3):
        assert ctx.cliente.get("/v1/yo", headers=ctx.h("A")).status_code == 200
    despues = metricas_texto(ctx)
    assert valor(despues, serie) - antes == 3
    ident = ctx.evaluar(pago("T-M", "C-M"), "A").json()["id_evaluacion"]
    otra = ctx.evaluar(pago("T-M2", "C-M2"), "A").json()["id_evaluacion"]
    for i in (ident, otra, "00000000-0000-4000-8000-000000000000"):
        ctx.cliente.get(f"/v1/evaluaciones/{i}", headers=ctx.h("A"))
    ctx.cliente.get("/camino/inventado/abc123")
    texto = metricas_texto(ctx)
    assert (
        not re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", texto) and ident not in texto
    )  # ningún id real se vuelve una serie
    assert (
        'ruta="/v1/evaluaciones/{id_evaluacion}"' in texto and 'ruta="desconocida"' in texto and "abc123" not in texto
    )
    assert "comercio_id" not in texto and 'comercio="' not in texto  # sin series por comercio


def test_un_verbo_raro_se_cuenta_como_OTRO_para_no_crear_series_sin_limite(ctx):
    ctx.cliente.request("PROPFIND", "/v1/yo")
    texto = metricas_texto(ctx)
    assert 'metodo="OTRO"' in texto and "PROPFIND" not in texto


def test_las_evaluaciones_se_cuentan_por_decision_y_modo_solo_la_primera_vez(ctx):
    antes = metricas_texto(ctx)
    r = ctx.evaluar(pago("T-EV-1", "C-EV-1"), "A")
    assert r.status_code == 201
    serie = f'antifraude_evaluaciones_total{{decision="{r.json()["decision"]}",modo="{r.json()["modo"]}"}}'
    despues = metricas_texto(ctx)
    assert valor(despues, serie) - valor(antes, serie) == 1
    repetida = ctx.evaluar(pago("T-EV-1", "C-EV-1"), "A")
    assert repetida.status_code == 200 and repetida.json()["repetida"] is True
    assert valor(metricas_texto(ctx), serie) == valor(
        despues, serie
    )  # un reintento idempotente no cuenta como otro pago
    fallo = ctx.evaluar({"id_transaccion": "malo"}, "A")
    assert fallo.status_code == 422 and valor(metricas_texto(ctx), serie) == valor(
        despues, serie
    )  # ni un pago rechazado por datos inválidos


def test_la_duracion_se_acumula_en_el_histograma(ctx):
    serie = 'antifraude_http_duracion_segundos_count{ruta="/v1/yo"}'
    antes = valor(metricas_texto(ctx), serie)
    ctx.cliente.get("/v1/yo", headers=ctx.h("A"))
    ctx.cliente.get("/v1/yo", headers=ctx.h("A"))
    texto = metricas_texto(ctx)
    assert valor(texto, serie) - antes == 2 and valor(texto, 'antifraude_http_duracion_segundos_sum{ruta="/v1/yo"}') > 0


def test_con_varios_procesos_las_metricas_se_suman(tmp_path):
    """Tres procesos distintos cuentan 40 peticiones cada uno; al leer desde un cuarto proceso salen 120 (no 40)."""
    entorno = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path), "PYTHONPATH": str(RAIZ)}
    codigo = "from modelo_ia.api import metricas\nfor _ in range(40): metricas.registrar_peticion('GET', '/x', 200, 0.01)\nmetricas.registrar_evaluacion('APROBADA', 'normal')"
    hijos = [subprocess.Popen([sys.executable, "-c", codigo], env=entorno, cwd=str(RAIZ)) for _ in range(3)]
    assert [h.wait(timeout=120) for h in hijos] == [0, 0, 0]
    leido = subprocess.run(
        [sys.executable, "-c", "from modelo_ia.api import metricas; print(metricas.texto()[0].decode())"],
        env=entorno,
        cwd=str(RAIZ),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert leido.returncode == 0, leido.stderr[-300:]
    assert valor(leido.stdout, 'antifraude_http_peticiones_total{estado="200",metodo="GET",ruta="/x"}') == 120
    assert valor(leido.stdout, 'antifraude_evaluaciones_total{decision="APROBADA",modo="normal"}') == 3
    assert valor(leido.stdout, 'antifraude_http_duracion_segundos_count{ruta="/x"}') == 120
    assert len(list(tmp_path.glob("*.db"))) >= 3  # cada proceso dejó su propio archivo


def test_el_middleware_va_por_fuera_de_los_demas(ctx):
    """Si quedara por dentro, no vería los errores 413 de otros middlewares ni las cabeceras de seguridad que ponen ellos."""
    nombres = [m.cls.__name__ for m in ctx.app.user_middleware]
    assert nombres[0] == "ObservabilidadMiddleware"
    r = ctx.cliente.post("/v1/evaluaciones", headers=ctx.h("A"), content=b"x" * 200_000)
    assert r.status_code == 413 and "x-request-id" in r.headers and r.headers["x-content-type-options"] == "nosniff"
