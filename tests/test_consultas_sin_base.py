"""El panel (api/consultas.py) probado SIN base de datos: con un repositorio de lecturas FALSO que anota con qué lo llaman.

Aquí se vigila lo que es del panel y no del SQL: quién ve qué (el comercio no ve señales ni nombres del personal), cómo se arma cada respuesta y que el alcance
de un comercio llegue al repositorio. El SQL se prueba con la base real (test_consultas_sqlite.py y el recorrido de test_permisos_por_ruta.py).
"""

import inspect
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from modelo_ia.api import consultas as modulo
from modelo_ia.api.almacen import Principal
from modelo_ia.api.consultas import ConsultasPanel, Filtros
from modelo_ia.api.servicio import ErrorAPI

COMERCIO = 2
COMERCIO_P = Principal(1, COMERCIO, "comercio", "Tienda A")
ANALISTA = Principal(9, 1, "analista", "PLATAFORMA", "Ana Pérez")
ADMIN = Principal(8, 1, "admin", "PLATAFORMA", "Root")
SENALES = {
    "suben": [{"texto": "IP de un proxy", "puntos": 31.6}],
    "bajan": [{"texto": "Cliente antiguo", "puntos": -8.2}],
}


def evaluacion(**cambios):
    e = {
        "id": "E1",
        "id_seguimiento": "AF-AAAA-BBBB",
        "id_transaccion": "T1",
        "comercio_id": COMERCIO,
        "id_cliente": "C1",
        "fecha": "2026-11-01T10:00:00",
        "creada": "2026-11-01T10:00:01",
        "latencia_ms": 12.0,
        "monto_cop": 100000.0,
        "decision": "RECHAZADA",
        "decision_modelo": "EN_REVISION",
        "probabilidad": 0.81,
        "motivos_json": "[]",
        "reglas_json": json.dumps([{"regla": "R1", "accion": "RECHAZAR", "motivo": "m"}]),
        "modelo_nombre": "m",
        "modelo_version": "1",
        "modelo_conjunto": "c",
        "modo": "normal",
        "estado_origen": "enviado",
        "red_origen": "comercio",
        "fila_json": json.dumps({"monto_cop": 100000.0, "metodo_pago": "VISA"}),
        "no_enviados_json": "[]",
        "senales_json": json.dumps(SENALES),
        "ip_codigo": "AAAA0001",
        "disp_codigo": None,
        "rev_estado": None,
        "rev_decision": None,
        "comercio_nombre": "Tienda A",
    }
    e.update(cambios)
    return e


class FalsasConsultas:
    """Cumple la interfaz `ConsultasRepo` (una prueba lo comprueba). Anota cada llamada y devuelve lo programado."""

    def __init__(self):
        self.llamadas = []
        self.revision = None
        self.resultado = None
        self.auditoria = []
        self.n_codigo = (
            3,
            [{"id_seguimiento": "AF-1", "id_cliente": "C9", "decision": "APROBADA", "fecha": "2026-10-30T09:00:00"}],
        )
        self.pagos_listado = []
        self.datos_resumen = None
        self.datos_vinculos = None
        self.comercios = []

    def listar(self, con, f, orden, limite, desplazamiento):
        self.llamadas.append(("listar", f, orden, limite, desplazamiento))
        return len(self.pagos_listado), self.pagos_listado

    def revision_de(self, con, id_evaluacion):
        self.llamadas.append(("revision_de", id_evaluacion))
        return self.revision

    def resultado_de(self, con, id_evaluacion):
        return self.resultado

    def nombre_comercio(self, con, comercio_id):
        return "Tienda A"

    def auditoria_de(self, con, id_evaluacion):
        return self.auditoria

    def pagos_con_codigo(self, con, columna, valor, id_evaluacion, alcance_comercio):
        self.llamadas.append(("pagos_con_codigo", columna, valor, id_evaluacion, alcance_comercio))
        return self.n_codigo

    def otros_pagos_del_cliente(self, con, comercio_id, id_cliente, id_evaluacion):
        self.llamadas.append(("otros_pagos_del_cliente", comercio_id, id_cliente, id_evaluacion))
        return 4

    def resumen(self, con, f, con_analistas):
        self.llamadas.append(("resumen", f, con_analistas))
        return self.datos_resumen

    def vinculos(self, con, f, minimo):
        self.llamadas.append(("vinculos", f, minimo))
        return self.datos_vinculos

    def comercios_con_pagos(self, con):
        return self.comercios


def montar(e=None):
    consultas = FalsasConsultas()
    visibles = {"E1": e or evaluacion()}

    @contextmanager
    def lectura(instantanea=False):
        yield SimpleNamespace()

    def visible(con, principal, id_eval):
        fila = visibles.get(id_eval)
        if fila is None or (principal.rol == "comercio" and fila["comercio_id"] != principal.comercio_id):
            raise ErrorAPI(404, "No encontrada", "No existe esa evaluación.")
        return fila

    srv = SimpleNamespace(
        meta={"modelo": "m", "version": "1", "umbral_revisar": 0.5, "umbral_bloquear": 0.8}, _evaluacion_visible=visible
    )
    return consultas, ConsultasPanel(SimpleNamespace(lectura=lectura, consultas=consultas), srv)


# ------------------------------------------------------------------ detalle
def test_el_detalle_muestra_las_senales_y_quien_resolvio_solo_al_personal():
    consultas, panel = montar()
    consultas.revision = {
        "estado": "resuelta",
        "decision_analista": "RECHAZADA",
        "nota": "n",
        "creada": "c",
        "resuelta": "r",
        "por": "Ana Pérez",
    }
    consultas.auditoria = [
        {
            "momento": "t",
            "accion": "resolver_revision",
            "detalle": "{}",
            "actor_rol": "analista",
            "actor_nombre": "Ana Pérez",
        },
        {"momento": "t", "accion": "evaluar", "detalle": None, "actor_rol": "comercio", "actor_nombre": "Tienda A"},
        {"momento": "t", "accion": "x", "detalle": "{}", "actor_rol": None, "actor_nombre": None},
    ]
    personal = panel.detalle(ANALISTA, "E1")
    assert personal["senales"] == SENALES and personal["revision"]["resuelta_por"] == "Ana Pérez"
    assert [a["actor"] for a in personal["auditoria"]] == ["Ana Pérez", "el comercio", "el sistema"]
    assert "Señales que más pesaron" in [p["paso"] for p in personal["como_se_decidio"]]
    comercio = panel.detalle(COMERCIO_P, "E1")
    assert comercio["senales"] is None and "resuelta_por" not in comercio["revision"]
    assert [a["actor"] for a in comercio["auditoria"]] == [
        "un analista",
        "tu comercio",
        "el sistema",
    ]  # sin nombres de empleados de la plataforma
    assert "Señales que más pesaron" not in [p["paso"] for p in comercio["como_se_decidio"]]
    assert "Ana Pérez" not in json.dumps(comercio, ensure_ascii=False)


def test_el_detalle_de_un_pago_ajeno_es_404_y_no_pregunta_nada_mas_al_repositorio():
    consultas, panel = montar(evaluacion(comercio_id=99))
    with pytest.raises(ErrorAPI) as e:
        panel.detalle(COMERCIO_P, "E1")
    assert e.value.status == 404 and consultas.llamadas == []
    with pytest.raises(ErrorAPI):
        panel.detalle(COMERCIO_P, "NO-EXISTE")


def test_un_comercio_cuenta_solo_sus_pagos_relacionados_y_el_personal_todos():
    consultas, panel = montar(evaluacion(disp_codigo="BBBB0002"))
    d = panel.detalle(COMERCIO_P, "E1")
    asked = [c for c in consultas.llamadas if c[0] == "pagos_con_codigo"]
    assert [(c[1], c[2], c[4]) for c in asked] == [
        ("ip_codigo", "AAAA0001", COMERCIO),
        ("disp_codigo", "BBBB0002", COMERCIO),
    ]
    assert d["relacionados"]["misma_ip"] == {
        "codigo": "AAAA0001",
        "otros_pagos": 3,
        "filtro": {"ip": "AAAA0001"},
        "ultimos": [
            {"id_seguimiento": "AF-1", "id_cliente": "C9", "decision": "APROBADA", "fecha": "2026-10-30T09:00:00"}
        ],
    }
    assert d["relacionados"]["mismo_cliente"] == {
        "id_cliente": "C1",
        "otros_pagos": 4,
        "filtro": {"id_cliente": "C1", "comercio_id": COMERCIO},
    }
    consultas.llamadas.clear()
    panel.detalle(ADMIN, "E1")
    assert {c[4] for c in consultas.llamadas if c[0] == "pagos_con_codigo"} == {
        None
    }  # el personal no se limita a un comercio


def test_sin_codigo_de_dispositivo_no_se_busca_ese_grupo():
    consultas, panel = montar()
    d = panel.detalle(ANALISTA, "E1")
    assert d["relacionados"]["mismo_dispositivo"] is None and [
        c[1] for c in consultas.llamadas if c[0] == "pagos_con_codigo"
    ] == ["ip_codigo"]


@pytest.mark.parametrize(
    "revision,res_decision,quien",
    [
        (None, "RECHAZADA", "regla de seguridad"),
        (
            {
                "estado": "pendiente",
                "decision_analista": None,
                "nota": None,
                "creada": "c",
                "resuelta": None,
                "por": None,
            },
            None,
            "pendiente de analista",
        ),
        (
            {
                "estado": "resuelta",
                "decision_analista": "APROBADA",
                "nota": None,
                "creada": "c",
                "resuelta": "r",
                "por": "x",
            },
            "APROBADA",
            "analista",
        ),
    ],
)
def test_quien_decidio_y_la_decision_final(revision, res_decision, quien):
    consultas, panel = montar()
    consultas.revision = revision
    d = panel.detalle(ANALISTA, "E1")["decision"]
    assert (d["final"], d["decidio"]) == (res_decision, quien)


# ------------------------------------------------------------------ listado, resumen, vínculos, comercios
def test_el_listado_pasa_los_filtros_y_el_orden_tal_cual_y_da_forma_a_cada_fila():
    consultas, panel = montar()
    consultas.pagos_listado = [
        evaluacion(rev_estado="resuelta", rev_decision="APROBADA"),
        evaluacion(id="E2", rev_estado="pendiente"),
    ]
    f = Filtros(ANALISTA, decision="RECHAZADA")
    r = panel.transacciones(f, "monto_desc", 25, 50)
    assert consultas.llamadas == [("listar", f, "monto_desc", 25, 50)] and consultas.llamadas[0][1] is f
    assert (
        r["total"] == 2
        and [t["decision_final"] for t in r["transacciones"]] == ["APROBADA", None]
        and r["transacciones"][0]["reglas"] == ["R1"]
    )
    assert "senales" not in r["transacciones"][0]


def test_el_resumen_da_forma_a_los_datos_y_pide_el_detalle_por_analista_solo_al_personal():
    consultas, panel = montar()
    t = {
        "pagos": 4,
        "aprobadas": 2,
        "en_revision": None,
        "rechazadas": 2,
        "monto": 400.0,
        "monto_rechazado": 50.0,
        "monto_en_revision": 0,
        "degradados": None,
        "sin_estado": 1,
        "latencia_media": 10.5,
    }
    consultas.datos_resumen = {
        "totales": t,
        "p95": 20.0,
        "por_dia": [{"dia": "2026-11-01", "aprobadas": 2, "en_revision": 0, "rechazadas": 2}],
        "por_comercio": [],
        "reglas": [{"regla": "R1", "veces": 2, "rechazadas": 1}],
        "histograma": [{"cubeta": 8, "pagos": 3}, {"cubeta": 9, "pagos": 1}],
        "por_analista": [{"analista": "Ana", "resueltas": 1}],
        "pendientes": 7,
    }
    r = panel.resumen(Filtros(ADMIN))
    assert [c for c in consultas.llamadas if c[0] == "resumen"][0][2] is True
    assert r["totales"] == {
        "pagos": 4,
        "aprobadas": 2,
        "en_revision": 0,
        "rechazadas": 2,
        "monto_cop": 400.0,
        "monto_rechazado_cop": 50.0,
        "monto_en_revision_cop": 0,
        "modo_degradado": 0,
        "sin_estado_del_dispositivo": 1,
        "revisiones_pendientes": 7,
        "latencia_media_ms": 10.5,
        "latencia_p95_ms": 20.0,
    }
    assert [d["pagos"] for d in r["distribucion_del_puntaje"]] == [0] * 8 + [3, 1] and r["distribucion_del_puntaje"][
        8
    ] == {"desde": 0.8, "hasta": 0.9, "pagos": 3}
    assert r["por_analista"] == [{"analista": "Ana", "resueltas": 1}] and r["reglas_mas_activadas"] == [
        {"regla": "R1", "veces": 2, "rechazadas": 1}
    ]
    consultas.llamadas.clear()
    panel.resumen(Filtros(COMERCIO_P))
    assert [c for c in consultas.llamadas if c[0] == "resumen"][0][2] is False


def test_los_vinculos_agregan_el_filtro_para_saltar_al_listado_y_calculan_las_rafagas():
    consultas, panel = montar()
    consultas.datos_vinculos = {
        "ips": [{"codigo": "AAAA0001", "pagos": 3, "clientes": 3}],
        "dispositivos": [{"codigo": "BBBB0002", "pagos": 2, "clientes": 2}],
        "clientes": [{"comercio_id": 2, "comercio": "T", "id_cliente": "C1", "pagos": 3, "ips": 3}],
        "candidatas": [{"ip_codigo": "AAAA0001", "fecha": f"2026-11-01T10:0{m}:00"} for m in (0, 2, 4)]
        + [
            {"ip_codigo": "AAAA0001", "fecha": "2026-11-01T18:00:00"},
            {"ip_codigo": "CCCC0003", "fecha": "no es fecha"},
        ],
    }
    r = panel.vinculos(Filtros(ADMIN), 3, 10)
    assert r["misma_ip_varios_clientes"][0]["filtro"] == {"ip": "AAAA0001"} and r["mismo_dispositivo_varios_clientes"][
        0
    ]["filtro"] == {"dispositivo": "BBBB0002"}
    assert r["cliente_con_varias_ips"][0]["filtro"] == {"id_cliente": "C1", "comercio_id": 2}
    assert r["rafagas_por_ip"] == [
        {"codigo": "AAAA0001", "pagos_en_ventana": 3, "pagos_totales": 4, "filtro": {"ip": "AAAA0001"}}
    ]  # la fecha inválida se ignora
    assert r["minimo"] == 3 and r["ventana_rafaga_minutos"] == 10


def test_la_lista_de_comercios_sale_del_repositorio():
    consultas, panel = montar()
    consultas.comercios = [{"id": 2, "nombre": "A", "suspendido": 0, "pagos": 5}]
    assert panel.comercios() == [{"id": 2, "nombre": "A", "suspendido": 0, "pagos": 5}]


# ------------------------------------------------------------------ los filtros ya no tienen SQL
def test_filtros_valida_pero_no_trae_sql_ni_condiciones():
    f = Filtros(ADMIN, desde="2026-11-01", ip="aaaa0001")
    assert not any(hasattr(f, n) for n in ("condiciones", "donde", "parametros"))
    assert str(f.desde) == "2026-11-01" and f.ip == "aaaa0001" and f.es_personal is True
    with pytest.raises(ErrorAPI) as e:
        Filtros(COMERCIO_P, comercio_id=99)
    assert e.value.status == 404  # un comercio no puede pedir el de otro
    assert (
        Filtros(COMERCIO_P).comercio_id == COMERCIO
        and Filtros(COMERCIO_P, comercio_id=COMERCIO).comercio_id == COMERCIO
    )
    assert Filtros(COMERCIO_P).es_personal is False


def test_el_panel_no_tiene_sql_ni_toca_sqlite_para_leer():
    fuente = inspect.getsource(modulo)
    for palabra in (
        "SELECT ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "json_each",
        "julianday",
        ".execute(",
        "sqlite3.connect",
    ):
        assert palabra not in fuente, f"consultas.py no debe tener SQL: encontré «{palabra}»"
