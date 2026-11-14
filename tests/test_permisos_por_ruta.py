"""Cada operación de la API contra cada rol: qué código devuelve, con qué tipo de contenido y con qué forma.

Es una «foto» de cómo se comporta HOY la API (permisos, validación y forma de las respuestas). Sirve para mover código con seguridad: si una ruta pierde su
exigencia de rol, cambia el orden en que se validan los errores (401 antes que 422) o cambia la forma de una respuesta, esta prueba lo dice.
Si cambias la API a propósito, regenera la foto:   REGENERAR_PERMISOS=1 python -m pytest modelo_ia/tests/test_permisos_por_ruta.py
"""

import json
import os
import re
from pathlib import Path

import pytest

from modelo_ia.tests.test_api import artefacto, construir_contexto, pago, pago_minimo  # noqa: F401  (artefacto es un fixture)

FOTO = Path(__file__).with_name("permisos_por_ruta.json")
UUID0 = "00000000-0000-4000-8000-000000000000"
ROLES = [
    "sin_clave",
    "clave_falsa",
    "comercio_A",
    "comercio_B",
    "analista",
    "admin",
]  # admin al final: es quien suspende y cambia cosas


def _clave(ctx, rol):
    return {
        "sin_clave": None,
        "clave_falsa": "mia_falsa",
        "comercio_A": ctx.claves["A"],
        "comercio_B": ctx.claves["B"],
        "analista": ctx.claves["analista"],
        "admin": ctx.claves["admin"],
    }[rol]


def _forma(r):
    tipo = r.headers.get("content-type", "").split(";")[0]
    try:
        cuerpo = r.json()
    except ValueError:
        return tipo, ""
    return tipo, (sorted(cuerpo) if isinstance(cuerpo, dict) else "lista")


NORMALIZAR = [
    (r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>"),
    (r"AF-[0-9A-Z]{4}-[0-9A-Z]{4}", "<seg>"),
    (r"\d{4}-\d{2}-\d{2}T[\d:.]+", "<fecha>"),
    (r'"latencia\w*_ms":\s*[0-9.]+', '"latencia_ms": <n>'),
    (r"mia_[A-Za-z_]+_[A-Za-z0-9_\-]+", "<clave>"),
    (r'"ultimo_uso":\s*"[^"]*"', '"ultimo_uso": "<fecha>"'),
]


def _normalizar(texto):
    for patron, reemplazo in NORMALIZAR:
        texto = re.sub(patron, reemplazo, texto)
    return texto


def recorrer(ctx, completo=False):
    """Devuelve {«METODO plantilla [variante] rol»: [código, tipo, forma]}; con completo=True, agrega el cuerpo normalizado (para comparar antes y después)."""
    c, alm = ctx.cliente, ctx.app.state.almacen
    segundo = iter(range(1, 3600))

    def fecha():  # cada pago con su fecha: así el orden de los listados es único y repetible
        n = next(segundo)
        return f"2026-11-12T11:{n // 60:02d}:{n % 60:02d}"

    montos = iter(range(100_000, 400_000, 1000))  # y con su monto: los empates se desempatan por un UUID aleatorio
    r = ctx.evaluar(pago("MX1", "CMX1", fecha_pago=fecha(), monto_cop=next(montos)))
    assert r.status_code == 201
    idev, seg = r.json()["id_evaluacion"], r.json()["id_seguimiento"]
    pendiente = ctx.evaluar(pago_minimo("MXP", "CMXP", fecha_pago=fecha(), monto_cop=next(montos))).json()[
        "id_evaluacion"
    ]
    spare = alm.crear_comercio("Tienda Spare")
    contador = iter(range(10**6))
    nuevo_pago = lambda: pago(
        f"MXN{next(contador)}", f"CMXN{next(contador)}", fecha_pago=fecha(), monto_cop=next(montos)
    )
    acuerdo = {"version": "v1.0", "firmado_en": "2026-11-01", "referencia": "acuerdo.pdf"}
    # (método, plantilla, variante, cuerpo o función que lo da)
    casos = [
        ("POST", "/v1/evaluaciones", "ok", nuevo_pago),
        ("POST", "/v1/evaluaciones", "cuerpo_malo", {"no": "valido"}),
        ("GET", "/v1/evaluaciones/{idev}", "ok", None),
        ("GET", "/v1/evaluaciones/{seg}", "por_seguimiento", None),
        ("GET", "/v1/evaluaciones/no-es-id", "id_malo", None),
        ("POST", "/v1/evaluaciones/{idev}/resultado", "ok", {"resultado": "LEGITIMA"}),
        ("POST", "/v1/evaluaciones/{idev}/resultado", "cuerpo_malo", {"resultado": "x"}),
        ("GET", "/v1/revisiones", "ok", None),
        ("GET", "/v1/revisiones?limite=0", "limite_malo", None),
        ("GET", "/v1/revisiones?estado=resuelta&orden=recientes", "filtros", None),
        ("POST", "/v1/revisiones/{pend}/resolver", "ok", {"decision": "APROBADA"}),
        ("POST", "/v1/revisiones/{uuid0}/resolver", "no_existe", {"decision": "APROBADA"}),
        ("POST", "/v1/revisiones/{pend}/resolver", "cuerpo_malo", {"decision": "TAL VEZ"}),
        ("GET", "/v1/panel/transacciones", "ok", None),
        ("GET", "/v1/panel/transacciones?limite=500", "limite_malo", None),
        ("GET", "/v1/panel/transacciones?decision=APROBADA&orden=monto_desc", "filtros", None),
        ("GET", "/v1/panel/transacciones/{idev}", "ok", None),
        ("GET", "/v1/panel/transacciones/{uuid0}", "no_existe", None),
        ("GET", "/v1/panel/resumen", "ok", None),
        ("GET", "/v1/panel/resumen?desde=hoy", "fecha_mala", None),
        ("GET", "/v1/panel/vinculos", "ok", None),
        ("GET", "/v1/panel/vinculos?minimo=1", "minimo_malo", None),
        ("GET", "/v1/panel/comercios", "ok", None),
        ("GET", "/v1/lista-negra", "ok", None),
        ("PUT", "/v1/lista-negra/CLI-1", "ok", {"motivo": "prueba"}),
        ("PUT", "/v1/lista-negra/CLI-1", "sin_cuerpo", None),
        ("PUT", "/v1/lista-negra/id%20malo", "id_malo", None),
        ("DELETE", "/v1/lista-negra/CLI-1", "ok", None),
        ("POST", "/v1/comercios", "ok", {"nombre": "Tienda M"}),
        ("POST", "/v1/comercios", "cuerpo_malo", {"nombre": "x"}),
        ("GET", "/v1/comercios", "ok", None),
        ("POST", "/v1/comercios/{spare}/suspender", "ok", {"motivo": "motivo de prueba"}),
        ("POST", "/v1/comercios/{spare}/suspender", "cuerpo_malo", {"motivo": ""}),
        ("POST", "/v1/comercios/1/suspender", "plataforma", {"motivo": "motivo de prueba"}),
        ("POST", "/v1/comercios/{spare}/reactivar", "ok", None),
        ("PUT", "/v1/comercios/{spare}/acuerdo", "ok", acuerdo),
        ("PUT", "/v1/comercios/{spare}/acuerdo", "cuerpo_malo", {"version": ""}),
        ("GET", "/v1/comercios/{spare}/acuerdos", "ok", None),
        ("GET", "/v1/comercios/99999/acuerdos", "no_existe", None),
        ("GET", "/v1/yo", "ok", None),
        ("GET", "/v1/modelo", "ok", None),
        ("GET", "/v1/salud", "ok", None),
    ]
    salida = {}
    for metodo, plantilla, variante, cuerpo in casos:
        ruta = plantilla.format(idev=idev, seg=seg, pend=pendiente, uuid0=UUID0, spare=spare)
        for rol in ROLES:
            clave = _clave(ctx, rol)
            cab = {"X-API-Key": clave} if clave else {}
            datos = cuerpo() if callable(cuerpo) else cuerpo
            resp = c.request(metodo, ruta, json=datos, headers=cab)
            tipo, forma = _forma(resp)
            fila = [resp.status_code, tipo, forma]
            if completo:
                fila.append(_normalizar(resp.text))
                fila.append(
                    sorted(
                        k
                        for k in resp.headers
                        if k.lower()
                        in (
                            "x-id-seguimiento",
                            "cache-control",
                            "retry-after",
                            "content-security-policy",
                            "x-frame-options",
                        )
                    )
                )
            salida[f"{metodo} {plantilla} [{variante}] {rol}"] = fila
    return salida


@pytest.fixture(scope="module")
def ctx_matriz(tmp_path_factory, artefacto):  # noqa: F811
    return construir_contexto(tmp_path_factory.mktemp("matriz"), artefacto)


def test_cada_operacion_se_comporta_igual_que_en_la_foto(ctx_matriz):
    ahora = recorrer(ctx_matriz)
    if os.environ.get("REGENERAR_PERMISOS") or not FOTO.exists():
        FOTO.write_text(json.dumps(ahora, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        pytest.skip("foto escrita: vuelve a correr la prueba para compararla")
    antes = json.loads(FOTO.read_text(encoding="utf-8"))
    distintos = {k: (antes.get(k), ahora.get(k)) for k in set(antes) | set(ahora) if antes.get(k) != ahora.get(k)}
    assert not distintos, "Cambió el comportamiento de:\n" + "\n".join(
        f"  {k}: antes {v[0]} -> ahora {v[1]}" for k, v in sorted(distintos.items())[:12]
    )


def test_la_foto_cubre_todas_las_operaciones_del_contrato(ctx_matriz):
    """Si agregas una ruta nueva sin agregarla al recorrido, esta prueba falla: ninguna operación puede quedar fuera de la comprobación de permisos."""
    cubiertas = {
        (k.split(" ")[0], re.sub(r"\?.*", "", k.split(" ")[1])) for k in json.loads(FOTO.read_text(encoding="utf-8"))
    }
    plantillas = {(m, re.sub(r"\{[^}]+\}", "*", p)) for m, p in cubiertas}
    contrato = {
        (m.upper(), re.sub(r"\{[^}]+\}", "*", ruta))
        for ruta, ops in ctx_matriz.app.openapi()["paths"].items()
        if ruta.startswith("/v1/")
        for m in ops
    }
    normal = lambda p: re.sub(r"(CLI-1|99999|no-es-id|id%20malo|\d+(?=/))", "*", p)
    cubiertas_n = {(m, normal(p)) for m, p in plantillas}
    contrato_n = {(m, normal(p)) for m, p in contrato}
    faltan = {
        x
        for x in contrato_n
        if x not in cubiertas_n
        and not any(x[0] == c[0] and x[1].replace("*", "") == c[1].replace("*", "") for c in cubiertas_n)
    }
    assert not faltan, f"Operaciones sin recorrido de permisos: {sorted(faltan)}"


ORDEN_EN_SWAGGER = [
    "evaluar_pago",
    "consultar_evaluacion",
    "informar_resultado",
    "listar_revisiones",
    "resolver_revision",
    "panel_transacciones",
    "panel_detalle",
    "panel_resumen",
    "panel_vinculos",
    "panel_comercios",
    "ver_lista_negra",
    "agregar_lista_negra",
    "quitar_lista_negra",
    "listar_comercios",
    "crear_comercio",
    "suspender_comercio",
    "reactivar_comercio",
    "registrar_acuerdo",
    "historial_acuerdos",
    "quien_soy",
    "modelo_en_uso",
    "salud",
]


def test_las_operaciones_salen_en_swagger_en_el_mismo_orden(ctx_matriz):
    """El archivo openapi.json se escribe con las llaves ordenadas y no delata el orden; el Swagger en vivo sí lo muestra. Cambiar el orden de los routers lo altera."""
    en_vivo = [
        o["operationId"]
        for ruta, ops in ctx_matriz.app.openapi()["paths"].items()
        if ruta.startswith("/v1/")
        for o in ops.values()
    ]
    assert en_vivo == ORDEN_EN_SWAGGER


def test_app_py_solo_arma_las_piezas_y_cada_ruta_vive_en_su_router():
    """La refactorización no debe deshacerse sin querer: app.py no define rutas, y cada router solo trae las de su tema."""
    import inspect
    from modelo_ia.api import app as app_mod

    fuente = inspect.getsource(app_mod)
    assert "@app." not in fuente and "def evaluar" not in fuente and len(fuente.splitlines()) < 100
    from modelo_ia.api.rutas import comercios, estado, evaluaciones, lista_negra, panel_datos, revisiones

    ops = lambda m: sorted(r.operation_id for r in m.router.routes)
    assert ops(evaluaciones) == ["consultar_evaluacion", "evaluar_pago", "informar_resultado"] and ops(revisiones) == [
        "listar_revisiones",
        "resolver_revision",
    ]
    assert ops(lista_negra) == ["agregar_lista_negra", "quitar_lista_negra", "ver_lista_negra"] and ops(estado) == [
        "modelo_en_uso",
        "quien_soy",
        "salud",
    ]
    assert len(ops(panel_datos)) == 5 and len(ops(comercios)) == 6
    assert (
        len(
            set(
                ops(evaluaciones) + ops(revisiones) + ops(lista_negra) + ops(estado) + ops(panel_datos) + ops(comercios)
            )
        )
        == 22
    )  # ninguna se repite ni se pierde


def test_el_identificador_de_seguimiento_viaja_tambien_en_una_cabecera(ctx_matriz):
    """Las tiendas lo leen de la cabecera sin abrir el cuerpo: al evaluar, al repetir y al consultar (por id interno y por id de seguimiento)."""
    c = ctx_matriz
    p = pago("HDR1", "CHDR1", fecha_pago="2026-11-12T11:59:00", monto_cop=777_000)
    nuevo = c.evaluar(p)
    repetido = c.evaluar(p)
    seg = nuevo.json()["id_seguimiento"]
    assert (
        nuevo.status_code == 201
        and repetido.status_code == 200
        and nuevo.headers["X-Id-Seguimiento"] == seg == repetido.headers["X-Id-Seguimiento"]
    )
    for ident in (nuevo.json()["id_evaluacion"], seg):
        r = c.cliente.get(f"/v1/evaluaciones/{ident}", headers=c.h("A"))
        assert r.status_code == 200 and r.headers["X-Id-Seguimiento"] == seg
