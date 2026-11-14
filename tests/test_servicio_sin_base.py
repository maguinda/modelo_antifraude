"""La lógica del servicio de evaluaciones probada SIN base de datos: con repositorios falsos que modelan las transacciones (lo escrito solo cuenta si hubo commit).

Esto es lo que ganamos al sacar el SQL del servicio: una carrera («llegó otro pago mientras se calculaba») ya no depende de hilos y suerte, se SIMULA de forma exacta,
intento por intento. Las pruebas con base de datos real (test_api.py, test_comercios.py…) siguen existiendo; estas prueban la lógica, aquellas el SQL.
"""
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from modelo_ia.api import esquemas as es
from modelo_ia.api.ajustes import Ajustes
from modelo_ia.api.almacen import ComercioNoAutorizado, Principal
from modelo_ia.api.servicio import ErrorAPI, ServicioEvaluacion
from modelo_ia.tests.test_api import RELOJ, artefacto, pago, pago_minimo  # noqa: F401  (artefacto es un fixture)

COMERCIO, OTRO_COMERCIO = 2, 5
PRINCIPAL = Principal(1, COMERCIO, "comercio", "Tienda F")
ANALISTA = Principal(9, 1, "analista", "PLATAFORMA", "Ana Pérez")


# ------------------------------------------------------------------ los falsos
class FalsaBase:
    """Una «base» que solo sabe de transacciones: lo escrito queda pendiente hasta el commit y se descarta con un rollback."""
    def __init__(self):
        self.eventos, self.pendientes, self.confirmadas, self.en_transaccion = [], [], [], False
        self.con = SimpleNamespace(nombre="conexion de mentira")

    @contextmanager
    def transaccion(self):
        self.eventos.append("begin")
        self.en_transaccion = True
        try:
            yield self.con
            self.eventos.append("commit")
            self.confirmadas += self.pendientes
        except BaseException:
            self.eventos.append("rollback")
            raise
        finally:
            self.pendientes, self.en_transaccion = [], False

    @contextmanager
    def lectura(self, instantanea=False):
        self.eventos.append("lectura")
        yield self.con


class FalsasEvaluaciones:
    def __init__(self, base):
        self.base = base
        self.marcas_programadas, self.llamadas_marcas = [], 0
        self.previos_en_transaccion = []                  # ¿el cálculo del historial ocurrió con la transacción abierta?
        self.por_tx, self.fallo_insert, self.al_fallar = {}, None, {}
        self.filas_extra = []
        self.args_marcas, self.args_previos_cliente, self.args_previos_huella = [], [], []     # con qué argumentos lo llamó el servicio

    def _todas(self):
        return [d for tipo, d in self.base.confirmadas + self.base.pendientes if tipo == "evaluacion"] + self.filas_extra

    def por_transaccion(self, con, comercio_id, id_transaccion):
        return self.por_tx.get((comercio_id, id_transaccion))

    def por_id(self, con, id_evaluacion):
        return next((r for r in self._todas() if r["id"] == id_evaluacion), None)

    def por_identificador(self, con, identificador):
        return next((r for r in self._todas() if identificador in (r["id"], r.get("id_seguimiento"))), None)

    def seguimiento_existe(self, con, candidato):
        return False

    def marcas(self, con, comercio_id, id_cliente, huella):
        self.llamadas_marcas += 1
        self.args_marcas.append((comercio_id, id_cliente, huella))
        return self.marcas_programadas.pop(0) if self.marcas_programadas else (0, 0)

    def previos_cliente(self, con, comercio_id, id_cliente, fecha, limite):
        self.previos_en_transaccion.append(self.base.en_transaccion)
        self.args_previos_cliente.append((comercio_id, id_cliente, fecha, limite))
        return []

    def previos_huella(self, con, comercio_id, huella, id_cliente, fecha, limite):
        self.args_previos_huella.append((comercio_id, huella, id_cliente, fecha, limite))
        return []

    def insertar(self, con, **valores):
        if self.fallo_insert is not None:
            self.por_tx.update(self.al_fallar)            # lo que «otro» ya había guardado y que el servicio podrá leer después del error
            raise self.fallo_insert
        self.base.pendientes.append(("evaluacion", dict(valores)))


class FalsasRevisiones:
    def __init__(self, base):
        self.base, self.fallo_crear, self.filas, self.cambiara = base, None, {}, 1
        self.totales, self.cola, self.ultima_consulta = (0, 0), [], None

    def por_evaluacion(self, con, id_evaluacion):
        return self.filas.get(id_evaluacion)

    def existe(self, con, id_evaluacion):
        return id_evaluacion in self.filas

    def crear_pendiente(self, con, id_evaluacion, comercio_id, creada):
        if self.fallo_crear is not None:
            raise self.fallo_crear
        self.base.pendientes.append(("revision", {"id_evaluacion": id_evaluacion, "comercio_id": comercio_id}))

    def resolver(self, con, id_evaluacion, decision, nota, clave_analista, resuelta):
        return self.cambiara

    def listar(self, con, estado, condiciones, parametros, orden, limite, desplazamiento):
        self.ultima_consulta = (estado, condiciones, tuple(parametros), orden, limite, desplazamiento)
        return self.totales, self.cola


class FalsosResultados:
    def __init__(self, base):
        self.base, self.previo = base, None

    def por_evaluacion(self, con, id_evaluacion):
        return self.previo

    def registrar(self, con, id_evaluacion, comercio_id, resultado, fecha_confirmacion, nota, recibido):
        self.base.pendientes.append(("resultado", {"id_evaluacion": id_evaluacion, "resultado": resultado}))


class FalsaListaNegra:
    def __init__(self, base):
        self.base, self.presentes, self.quitara = base, set(), 1

    def ids_de_cliente(self, con, comercio_id, id_cliente):
        return [id_cliente] if id_cliente in self.presentes else []

    def agregar(self, con, comercio_id, id_cliente, motivo, creada):
        self.base.pendientes.append(("lista_negra", id_cliente))

    def quitar(self, con, comercio_id, id_cliente):
        return self.quitara

    def listar(self, con, comercio_id):
        return []


class FalsoAlmacen:
    def __init__(self):
        self.base = FalsaBase()
        self.evaluaciones, self.revisiones = FalsasEvaluaciones(self.base), FalsasRevisiones(self.base)
        self.resultados, self.lista_negra = FalsosResultados(self.base), FalsaListaNegra(self.base)
        self.suspendido, self.auditoria = False, []

    def transaccion(self):
        return self.base.transaccion()

    def lectura(self, instantanea=False):
        return self.base.lectura(instantanea)

    def huella_ip(self, ip):
        return "f" * 16

    def codigo_ip(self, huella):
        return "CODIGOIP" if huella else None

    def codigo_dispositivo(self, huella):
        return "CODDISP1"

    def verificar_comercio(self, con, comercio_id, exigir_acuerdo=False, hoy=None):
        if self.suspendido:
            raise ComercioNoAutorizado("comercio_suspendido", "suspendido")

    def auditar(self, con, comercio_id, clave_id, accion, id_evaluacion, detalle):
        self.auditoria.append((accion, id_evaluacion, detalle))


def montar(artefacto):  # noqa: F811
    alm = FalsoAlmacen()
    return alm, ServicioEvaluacion(alm, artefacto, Ajustes(pepper="p" * 40), lambda: RELOJ)


def solicitud(tx="F1", cliente="CF1", minimo=False, **cambios):
    return es.SolicitudEvaluacion.model_validate((pago_minimo if minimo else pago)(tx, cliente, **cambios))


def tipos(alm):
    return [t for t, _ in alm.base.confirmadas]


# ------------------------------------------------------------------ evaluar: lo básico
def test_un_pago_normal_se_guarda_una_vez_y_dentro_de_una_transaccion(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    r = s.evaluar(PRINCIPAL, solicitud())
    assert r["repetida"] is False and r["decision"] == "APROBADA" and tipos(alm) == ["evaluacion"]
    guardada = alm.base.confirmadas[0][1]
    assert guardada["comercio_id"] == COMERCIO and guardada["id_transaccion"] == "F1" and guardada["decision"] == "APROBADA" and guardada["id"] == r["id_evaluacion"]
    assert alm.auditoria == [("evaluacion", r["id_evaluacion"], {"decision": "APROBADA", "modo": "normal", "latencia_ms": guardada["latencia_ms"]})]
    assert alm.base.eventos[-2:] == ["begin", "commit"]


def test_un_pago_en_revision_guarda_su_revision_en_la_misma_transaccion_y_uno_aprobado_no(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    r = s.evaluar(PRINCIPAL, solicitud("R1", "CR1", minimo=True))
    assert r["decision"] == "EN_REVISION" and tipos(alm) == ["evaluacion", "revision"] and alm.base.confirmadas[1][1] == {"id_evaluacion": r["id_evaluacion"], "comercio_id": COMERCIO}
    alm2, s2 = montar(artefacto)
    assert s2.evaluar(PRINCIPAL, solicitud()) and tipos(alm2) == ["evaluacion"]


def test_si_falla_guardar_la_revision_no_queda_ni_la_evaluacion(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.revisiones.fallo_crear = RuntimeError("falla al crear la revisión")
    with pytest.raises(RuntimeError, match="falla al crear"):
        s.evaluar(PRINCIPAL, solicitud("R2", "CR2", minimo=True))
    assert alm.base.confirmadas == [] and alm.base.eventos[-1] == "rollback"                   # todo o nada: la evaluación ya insertada se deshizo con la revisión


def test_un_cliente_en_la_lista_negra_se_rechaza(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.lista_negra.presentes = {"CF-MALO"}
    r = s.evaluar(PRINCIPAL, solicitud("LN1", "CF-MALO"))
    assert r["decision"] == "RECHAZADA" and tipos(alm) == ["evaluacion"]


# ------------------------------------------------------------------ evaluar: carreras, simuladas intento por intento
def test_si_nadie_mas_llega_se_calcula_una_sola_vez(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    s.evaluar(PRINCIPAL, solicitud())
    assert alm.evaluaciones.previos_en_transaccion == [False] and alm.evaluaciones.llamadas_marcas == 2         # leer y comprobar, y nada más


def test_el_servicio_le_pide_a_cada_repositorio_justo_lo_que_corresponde(artefacto):  # noqa: F811
    """Los falsos registran con qué argumentos los llamaron: si el servicio cruzara el cliente con la huella o el comercio con otro dato, aquí se vería."""
    alm, s = montar(artefacto)
    s.evaluar(PRINCIPAL, solicitud("F1", "CF1"))
    assert alm.evaluaciones.args_marcas == [(COMERCIO, "CF1", "FP-CF1")] * 2                        # marcas: el comercio, el cliente y la huella del dispositivo
    tope = s.aj                                                                                       # cada historial usa SU propio límite de los ajustes
    assert alm.evaluaciones.args_previos_cliente == [(COMERCIO, "CF1", "2026-11-12T11:55:00", tope.max_historial_cliente)]          # comercio, cliente, fecha del pago y límite
    assert alm.evaluaciones.args_previos_huella == [(COMERCIO, "FP-CF1", "CF1", "2026-11-12T11:55:00", tope.max_historial_huella)]  # comercio, huella, cliente a EXCLUIR, fecha y límite
    assert tope.max_historial_cliente != tope.max_historial_huella                                   # (si fueran iguales, esta prueba no distinguiría un límite del otro)


def test_si_llega_otro_pago_mientras_se_calcula_se_recalcula_y_se_guarda_una_vez(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.evaluaciones.marcas_programadas = [(0, 0), (1, 0), (1, 0), (1, 0)]          # intento 1: al comprobar, el historial cambió; intento 2: estable
    s.evaluar(PRINCIPAL, solicitud())
    assert alm.evaluaciones.previos_en_transaccion == [False, False] and alm.evaluaciones.llamadas_marcas == 4
    assert tipos(alm) == ["evaluacion"]                                               # y se guardó UNA vez, con el historial nuevo


def test_si_llega_otro_pago_en_todos_los_intentos_el_ultimo_se_calcula_dentro_del_bloqueo_y_siempre_progresa(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.evaluaciones.marcas_programadas = [(0, 0), (1, 0), (1, 0), (2, 0), (2, 0), (3, 0), (3, 0)]    # un intruso en cada uno de los 3 primeros intentos
    r = s.evaluar(PRINCIPAL, solicitud())
    assert alm.evaluaciones.previos_en_transaccion == [False, False, False, True]      # el 4.º se calcula con la transacción ABIERTA: nadie puede colarse
    assert tipos(alm) == ["evaluacion"] and r["repetida"] is False                    # y aun así termina y guarda una sola vez
    assert alm.base.eventos.count("begin") == 4 and alm.base.eventos.count("rollback") == 0


# ------------------------------------------------------------------ evaluar: repeticiones y errores
def guardar_primero(alm, s, **cambios):
    """Hace una evaluación real contra los falsos y deja visible su fila como si otra petición la hubiera guardado antes."""
    r = s.evaluar(PRINCIPAL, solicitud(**cambios))
    fila = alm.base.confirmadas[0][1]
    return r, fila


def test_un_pago_repetido_devuelve_lo_guardado_sin_guardar_otra_vez(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    primera, fila = guardar_primero(alm, s)
    alm.evaluaciones.por_tx[(COMERCIO, "F1")] = fila
    segunda = s.evaluar(PRINCIPAL, solicitud())
    assert segunda["repetida"] is True and segunda["id_evaluacion"] == primera["id_evaluacion"] and tipos(alm) == ["evaluacion"]


def test_el_mismo_id_con_datos_distintos_da_409_y_no_guarda_nada_nuevo(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    _, fila = guardar_primero(alm, s)
    alm.evaluaciones.por_tx[(COMERCIO, "F1")] = fila
    with pytest.raises(ErrorAPI) as e:
        s.evaluar(PRINCIPAL, solicitud(monto_cop=999999))
    assert e.value.status == 409 and tipos(alm) == ["evaluacion"]


def test_dos_peticiones_identicas_a_la_vez_la_segunda_recibe_la_guardada_por_la_primera(artefacto):  # noqa: F811
    """El choque de «único» al insertar (la otra petición ganó la carrera) se resuelve devolviendo lo que ya está guardado."""
    alm, s = montar(artefacto)
    _, fila = guardar_primero(alm, s)
    alm.base.confirmadas.clear()
    alm.evaluaciones.fallo_insert = sqlite3.IntegrityError("UNIQUE constraint failed")
    alm.evaluaciones.al_fallar = {(COMERCIO, "F1"): fila}                           # cuando el insert falla, ya se ve lo que guardó la otra
    r = s.evaluar(PRINCIPAL, solicitud())
    assert r["repetida"] is True and r["id_evaluacion"] == fila["id"]


def test_el_choque_de_unico_con_datos_distintos_da_409(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    _, fila = guardar_primero(alm, s)
    alm.evaluaciones.fallo_insert = sqlite3.IntegrityError("UNIQUE constraint failed")
    alm.evaluaciones.al_fallar = {(COMERCIO, "F1"): fila}
    with pytest.raises(ErrorAPI) as e:
        s.evaluar(PRINCIPAL, solicitud(monto_cop=777777))
    assert e.value.status == 409


def test_si_la_base_no_esta_disponible_responde_503_con_la_instruccion_de_tratarlo_como_en_revision(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.evaluaciones.fallo_insert = sqlite3.OperationalError("database is locked")
    with pytest.raises(ErrorAPI) as e:
        s.evaluar(PRINCIPAL, solicitud())
    assert e.value.status == 503 and "EN_REVISION" in e.value.detalle and alm.base.confirmadas == []


def test_un_comercio_suspendido_no_guarda_nada_aunque_pase_la_autenticacion(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.suspendido = True
    with pytest.raises(ErrorAPI) as e:
        s.evaluar(PRINCIPAL, solicitud())
    assert e.value.status == 403 and e.value.titulo == "Comercio suspendido" and alm.base.confirmadas == [] and alm.base.eventos[-1] == "rollback"


# ------------------------------------------------------------------ consultar y aislamiento entre comercios
def fila_de(id_, comercio, seg="AF-AAAA-BBBB"):
    return {"id": id_, "comercio_id": comercio, "id_seguimiento": seg, "id_transaccion": "T", "decision": "APROBADA", "probabilidad": 0.1, "motivos_json": "[]", "reglas_json": "[]",
            "modo": "normal", "modelo_nombre": "m", "modelo_version": "1", "modelo_conjunto": "c", "red_origen": "comercio", "estado_origen": "comercio", "creada": "2026-11-12T10:00:00",
            "latencia_ms": 1.0}


def test_un_comercio_no_ve_las_evaluaciones_de_otro_y_responde_404_igual_que_si_no_existieran(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.evaluaciones.filas_extra = [fila_de("ID-AJENA", OTRO_COMERCIO)]
    for ident in ("ID-AJENA", "AF-AAAA-BBBB", "af-aaaa-bbbb", "ID-QUE-NO-EXISTE"):
        with pytest.raises(ErrorAPI) as e:
            s.obtener(PRINCIPAL, ident)
        assert e.value.status == 404 and e.value.detalle == "No existe esa evaluación."        # el mismo mensaje exacto: no revela si existe
    assert s.obtener(ANALISTA, "ID-AJENA")["id_evaluacion"] == "ID-AJENA"                       # el personal sí la ve
    assert s.obtener(Principal(1, OTRO_COMERCIO, "comercio", "Otra"), "ID-AJENA")["id_evaluacion"] == "ID-AJENA"      # y su dueño también


# ------------------------------------------------------------------ resolver revisiones, resultados y lista negra
SOL_DECISION = SimpleNamespace(decision=SimpleNamespace(value="APROBADA"), nota="ok")


def test_resolver_una_revision_solo_funciona_una_vez(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.evaluaciones.filas_extra = [fila_de("E1", COMERCIO)]
    alm.revisiones.filas = {"E1": {"estado": "pendiente", "decision_analista": None, "nota": None, "resuelta": None}}
    assert s.resolver_revision(ANALISTA, "E1", SOL_DECISION)["id_evaluacion"] == "E1" and alm.auditoria[-1][0] == "revision_resuelta"
    alm.revisiones.cambiara = 0                                                          # la segunda vez, ya no hay una pendiente que cambiar
    with pytest.raises(ErrorAPI) as e:
        s.resolver_revision(ANALISTA, "E1", SOL_DECISION)
    assert e.value.status == 409


def test_resolver_una_evaluacion_sin_revision_o_inexistente_da_404(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.evaluaciones.filas_extra = [fila_de("E2", COMERCIO)]                              # existe, pero sin revisión
    for ident in ("E2", "NO-EXISTE"):
        with pytest.raises(ErrorAPI) as e:
            s.resolver_revision(ANALISTA, ident, SOL_DECISION)
        assert e.value.status == 404
    assert alm.auditoria == []


def test_la_cola_de_revision_entrega_lo_que_pide_el_panel_y_solo_dice_quien_resolvio_lo_resuelto(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    base = {**fila_de("E5", COMERCIO), "comercio_nombre": "Tienda F", "rev_creada": "2026-11-12T10:00:00", "rev_resuelta": None, "senales_json": None, "fila_json": "{}"}
    alm.revisiones.totales = (2, 300000.0)
    alm.revisiones.cola = [{**base, "rev_estado": "pendiente", "rev_por": "clave #3"}, {**base, "id": "E6", "rev_estado": "resuelta", "rev_por": "Ana Pérez", "rev_resuelta": "2026-11-12T11:00:00"}]
    r = s.listar_revisiones("pendiente", 25, 50, ["e.comercio_id = ?"], (COMERCIO,), "riesgo_desc")
    assert alm.revisiones.ultima_consulta == ("pendiente", ["e.comercio_id = ?"], (COMERCIO,), "riesgo_desc", 25, 50)           # lo pedido llega tal cual al repositorio
    assert (r["total"], r["monto_total_cop"]) == (2, 300000.0) and [x["id_evaluacion"] for x in r["revisiones"]] == ["E5", "E6"]
    assert [x["resuelta_por"] for x in r["revisiones"]] == [None, "Ana Pérez"]                                               # quién resolvió: solo si ya está resuelta
    assert r["revisiones"][0]["senales"] == {"suben": [], "bajan": []}


def test_informar_el_mismo_resultado_es_seguro_y_uno_distinto_da_409(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.evaluaciones.filas_extra = [fila_de("E3", COMERCIO)]
    sol = lambda valor: SimpleNamespace(resultado=SimpleNamespace(value=valor), fecha_confirmacion=None, nota=None)
    assert s.registrar_resultado(PRINCIPAL, "E3", sol("LEGITIMA"))["repetida"] is False and tipos(alm) == ["resultado"]
    alm.resultados.previo = {"resultado": "LEGITIMA"}
    assert s.registrar_resultado(PRINCIPAL, "E3", sol("LEGITIMA"))["repetida"] is True and tipos(alm) == ["resultado"]        # no se guarda dos veces
    with pytest.raises(ErrorAPI) as e:
        s.registrar_resultado(PRINCIPAL, "E3", sol("CONTRACARGO"))
    assert e.value.status == 409


def test_quitar_a_alguien_que_no_esta_en_la_lista_negra_da_404_y_agregar_deja_auditoria(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    s.agregar_lista_negra(PRINCIPAL, "CLI-X", "contracargos")
    assert tipos(alm) == ["lista_negra"] and alm.auditoria[-1][0] == "lista_negra_agregar"
    alm.lista_negra.quitara = 0
    with pytest.raises(ErrorAPI) as e:
        s.quitar_lista_negra(PRINCIPAL, "CLI-X")
    assert e.value.status == 404 and alm.auditoria[-1][0] == "lista_negra_agregar"        # no se audita lo que no pasó


def test_un_comercio_suspendido_tampoco_puede_tocar_resultados_ni_lista_negra(artefacto):  # noqa: F811
    alm, s = montar(artefacto)
    alm.suspendido = True
    alm.evaluaciones.filas_extra = [fila_de("E4", COMERCIO)]
    for accion in (lambda: s.agregar_lista_negra(PRINCIPAL, "CLI-X", None), lambda: s.quitar_lista_negra(PRINCIPAL, "CLI-X"),
                   lambda: s.registrar_resultado(PRINCIPAL, "E4", SimpleNamespace(resultado=SimpleNamespace(value="LEGITIMA"), fecha_confirmacion=None, nota=None))):
        with pytest.raises(ErrorAPI) as e:
            accion()
        assert e.value.status == 403
    assert alm.base.confirmadas == [] and alm.auditoria == []
