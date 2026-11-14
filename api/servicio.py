"""Lógica del servicio: evaluar un pago, revisiones, resultados y lista negra.

Principio: una evaluación se hace dentro de UNA transacción de base de datos (BEGIN IMMEDIATE), de modo que
dos pagos del mismo cliente nunca se evalúan «a la vez» sobre el mismo historial y un reintento no se cuenta dos veces.
"""
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .. import config, predecir, variables
from ..entrenar import ESTADO_RELLENO
from ..reglas import SEVERIDAD, MotorReglas
from ..reglas.seguridad import construir_reglas_seguridad
from . import proveedores_ip, traduccion
from .ajustes import Ajustes
from .almacen import PLATAFORMA, Almacen, Principal, ahora_iso, id_seguimiento_libre

log = logging.getLogger("modelo_ia.api")

# Columnas que pueden venir vacías sin que el modelo deje de funcionar (no siempre se miden)
OPCIONALES_NULOS = {"tiempo_formulario_seg", "velocidad_tecleo_ms", "pego_numero_tarjeta", "mouse_natural",
                    "distancia_ip_ciudad_km"}
NUMERICAS_CRUDAS = ["monto_cop", "es_emulador", "distancia_ip_ciudad_km", "ip_es_datacenter", "ip_reputacion",
                    "edad_cuenta_dias", "intentos_fallidos_24h", "cambio_credenciales_7d", "tiempo_formulario_seg",
                    "velocidad_tecleo_ms", "pego_numero_tarjeta", "mouse_natural", "mes", "hora"]


class ErrorAPI(Exception):
    def __init__(self, status: int, titulo: str, detalle: str, cabeceras: Optional[Dict[str, str]] = None):
        super().__init__(detalle)
        self.status, self.titulo, self.detalle, self.cabeceras = status, titulo, detalle, cabeceras or {}


class DatosInsuficientes(Exception):
    def __init__(self, faltan: List[str]):
        super().__init__(", ".join(faltan))
        self.faltan = faltan


def _py(valor: Any) -> Any:
    if hasattr(valor, "item"):
        return valor.item()
    return valor


def _hash_solicitud(solicitud, ip_huella: Optional[str] = None) -> str:
    """Huella de la solicitud para detectar un id repetido con datos distintos. La IP cruda nunca entra: se cambia por su huella irreversible."""
    d = solicitud.model_dump(mode="json", exclude_none=True)
    if "ip" in d.get("red", {}):
        d["red"].pop("ip")
        d["red"]["ip_huella"] = ip_huella
    canonico = json.dumps(d, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# Nombres legibles de los datos que pueden faltar (para el mensaje del modo degradado)
NOMBRES_LEGIBLES = {
    "estado_dispositivo": "estado del dispositivo", "tipo_ip": "tipo de IP", "huella_dispositivo": "huella del dispositivo", "sistema_operativo": "sistema operativo", "navegador": "navegador",
    "resolucion_pantalla": "resolución de pantalla", "zona_horaria_dispositivo": "zona horaria del dispositivo",
    "es_emulador": "si es emulador", "ip_pais": "país de la IP", "ip_es_datacenter": "si la IP es de un centro de datos",
    "ip_reputacion": "reputación de la IP", "kyc_nivel": "nivel de verificación (KYC)", "edad_cuenta_dias": "antigüedad de la cuenta",
    "autenticacion_2fa": "resultado del 2FA", "intentos_fallidos_24h": "intentos de acceso fallidos",
    "cambio_credenciales_7d": "cambio reciente de credenciales"}

# Lo que ve el analista de cada pago (nunca la huella ni nada de tarjeta)
CAMPOS_RESUMEN = ("fecha", "id_cliente", "monto_cop", "metodo_pago", "canal_pago", "categoria", "localizacion", "dispositivo",
                  "estado_dispositivo", "tipo_ip", "ip_pais", "kyc_nivel", "edad_cuenta_dias", "autenticacion_2fa",
                  "transacciones_ultimas_24h", "es_emulador")


def _resumen(fila: Dict) -> Dict:
    r = {k: fila.get(k) for k in CAMPOS_RESUMEN if fila.get(k) is not None}
    if fila.get("estado_origen") == "no_enviado":          # el valor guardado es solo un relleno: no se le muestra al analista como si fuera un dato
        r.pop("estado_dispositivo", None)
    return r


class ServicioEvaluacion:
    def __init__(self, almacen: Almacen, artefacto: Dict, ajustes: Ajustes,
                 reloj: Optional[Callable[[], datetime]] = None, proveedor=None, art_sin_estado: Optional[Dict] = None):
        self.alm, self.art, self.aj = almacen, artefacto, ajustes
        self.art_sin_estado = art_sin_estado          # modelo para pagos donde el comercio no manda el estado del dispositivo
        self.proveedor = proveedor or proveedores_ip.crear_proveedor(ajustes.proveedor_ip)
        self.meta = artefacto["meta"]
        self.reloj = reloj or datetime.now
        self.grupos = config.CONJUNTOS_GRUPOS[self.meta["conjunto"]]

    # ----------------------------------------------------------- información
    @property
    def advertencias(self) -> List[str]:
        """Cosas que el equipo debe saber: p. ej. un modelo entrenado con un alcance de historial distinto al que usa la API."""
        avisos = []
        if self.proveedor.nombre == "simulado":
            avisos.append("La inteligencia de IP es SIMULADA (solo reconoce rangos de documentación): no ubica IPs reales ni detecta VPN, proxy "
                          "o centros de datos de verdad. Configura un proveedor real (MODELO_IA_PROVEEDOR_IP) antes de producción.")
        if self.art_sin_estado is None:
            avisos.append("No hay modelo para pagos SIN `dispositivo.estado`: esos pagos quedarán en modo degradado (EN_REVISION). "
                          "Entrénalo con `--sin-estado --como-actual-sin-estado`.")
        if self.meta.get("alcance_historial", "plataforma") != "comercio":
            avisos.append("El modelo se entrenó con el historial de TODOS los comercios juntos, pero la API calcula el historial por comercio: "
                          "las variables de historial, perfil y huella valdrán menos que en el entrenamiento. Reentrena con --alcance comercio.")
        return avisos

    @property
    def info_modelo(self) -> Dict[str, str]:
        return {"nombre": self.meta["modelo"], "version": self.meta["version"], "conjunto_variables": self.meta["conjunto"]}

    # ----------------------------------------------------------- evaluar
    def evaluar(self, principal: Principal, solicitud) -> Dict:
        t0 = time.perf_counter()
        ahora = self.reloj()
        fecha = traduccion.a_hora_bogota(solicitud.fecha_pago, ahora)
        if fecha < ahora - timedelta(days=self.aj.dias_pasado_maximo):
            raise ErrorAPI(422, "Fecha fuera de rango", f"fecha_pago no puede ser anterior a {self.aj.dias_pasado_maximo} días.")
        if fecha > ahora + timedelta(minutes=self.aj.minutos_futuro_maximo):
            raise ErrorAPI(422, "Fecha fuera de rango", "fecha_pago está en el futuro.")
        if solicitud.monto_cop > self.aj.monto_maximo_cop:
            raise ErrorAPI(422, "Monto fuera de rango", f"monto_cop no puede superar {self.aj.monto_maximo_cop}.")
        ip_huella = self.alm.huella_ip(solicitud.red.ip) if solicitud.red.ip else None
        huella_solicitud = _hash_solicitud(solicitud, ip_huella)
        id_eval = str(uuid.uuid4())
        red, red_origen = self._red_efectiva(solicitud)
        fila = traduccion.fila_cruda(solicitud, fecha, red)
        fila["ip_huella"] = ip_huella
        try:
            # Se calcula SIN bloquear la base (lo lento: variables y modelo) y se guarda con un bloqueo cortito. Antes de guardar se
            # verifica que no llegó otro pago del mismo cliente o dispositivo mientras tanto; si llegó, se recalcula (su historial cambió).
            for intento in range(4):
                ultimo = intento == 3
                if not ultimo:
                    with self.alm.lectura(instantanea=True) as con:
                        repetida = self._repetida(con, principal, solicitud, huella_solicitud)
                        if repetida is not None:
                            return repetida
                        marcas = self._marcas(con, principal.comercio_id, fila)
                        resultado, fila_con_24h, negra = self._calcular(con, principal.comercio_id, fila, solicitud, id_eval)
                    latencia = round((time.perf_counter() - t0) * 1000, 2)
                with self.alm.transaccion() as con:
                    repetida = self._repetida(con, principal, solicitud, huella_solicitud)
                    if repetida is not None:
                        return repetida
                    if ultimo:                          # último intento: se calcula dentro del bloqueo, así que siempre progresa
                        marcas = self._marcas(con, principal.comercio_id, fila)
                        resultado, fila_con_24h, negra = self._calcular(con, principal.comercio_id, fila, solicitud, id_eval)
                        latencia = round((time.perf_counter() - t0) * 1000, 2)
                    elif self._marcas(con, principal.comercio_id, fila) != marcas:
                        continue
                    return self._guardar(con, principal, solicitud, fila, id_eval, resultado, fila_con_24h, huella_solicitud, latencia, red_origen)
        except sqlite3.IntegrityError:
            # Dos solicitudes idénticas llegaron a la vez: la segunda ya no puede insertar; devolvemos la guardada.
            with self.alm.lectura() as con:
                previa = con.execute("SELECT * FROM evaluaciones WHERE comercio_id=? AND id_transaccion=?",
                                     (principal.comercio_id, solicitud.id_transaccion)).fetchone()
                if previa is not None and previa["solicitud_hash"] == huella_solicitud:
                    return self._respuesta(con, previa, repetida=True)
            raise ErrorAPI(409, "Transacción repetida con datos distintos", "Ya existe una evaluación con ese id_transaccion.")
        except sqlite3.OperationalError as e:
            log.exception("base de datos no disponible")
            raise ErrorAPI(503, "Servicio no disponible", "No se pudo guardar la evaluación. Trata el pago como EN_REVISION y reintenta.") from e

    def _red_efectiva(self, solicitud):
        """Datos de red completos: lo que mandó el comercio manda; lo que falta lo pone el proveedor a partir de la IP (que no se guarda)."""
        campos = traduccion.red_de(solicitud)
        ip = solicitud.red.ip
        if ip is None:
            return campos, "comercio"
        try:
            info = self.proveedor.consultar(ip)
        except Exception as e:                                   # no se registra el mensaje (podría traer la IP)
            log.error("falla del proveedor de IP (%s)", type(e).__name__)
            return campos, "proveedor_fallo"
        relleno = {"tipo_ip": info.tipo_ip, "pais": info.pais, "ciudad_estimada": info.ciudad_estimada, "es_datacenter": info.es_datacenter,
                   "reputacion": info.reputacion, "distancia_a_ciudad_km": proveedores_ip.distancia_km(solicitud.ciudad, info)}
        for clave, valor in relleno.items():
            if campos.get(clave) is None:
                campos[clave] = valor
        return campos, f"proveedor:{self.proveedor.nombre}"

    def _repetida(self, con: sqlite3.Connection, principal: Principal, solicitud, huella_solicitud: str) -> Optional[Dict]:
        previa = con.execute("SELECT * FROM evaluaciones WHERE comercio_id=? AND id_transaccion=?",
                             (principal.comercio_id, solicitud.id_transaccion)).fetchone()
        if previa is None:
            return None
        if previa["solicitud_hash"] != huella_solicitud:
            raise ErrorAPI(409, "Transacción repetida con datos distintos",
                           "Ya existe una evaluación con ese id_transaccion pero con datos diferentes.")
        return self._respuesta(con, previa, repetida=True)

    @staticmethod
    def _marcas(con: sqlite3.Connection, comercio_id: int, fila: Dict):
        """Último pago guardado del cliente y de la huella: si cambian entre calcular y guardar, el historial cambió."""
        cliente = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM evaluaciones WHERE comercio_id=? AND id_cliente=?",
                              (comercio_id, fila["id_cliente"])).fetchone()[0]
        huella = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM evaluaciones WHERE comercio_id=? AND huella=?",
                             (comercio_id, fila.get("huella_dispositivo"))).fetchone()[0] if fila.get("huella_dispositivo") else 0
        return cliente, huella

    def _calcular(self, con: sqlite3.Connection, comercio_id: int, fila: Dict, solicitud, id_eval: str):
        negra = [r["id_cliente"] for r in con.execute("SELECT id_cliente FROM lista_negra WHERE comercio_id=? AND id_cliente=?",
                                                       (comercio_id, solicitud.id_cliente))]
        resultado, fila_con_24h = self._decidir(con, comercio_id, fila, negra, id_eval)
        return resultado, fila_con_24h, negra

    def _guardar(self, con: sqlite3.Connection, principal: Principal, solicitud, fila: Dict, id_eval: str, resultado: Dict,
                 fila_con_24h: Dict, huella_solicitud: str, latencia: float, red_origen: str = "comercio") -> Dict:
        no_enviados = sorted(k for k in traduccion.NEUTROS if fila_con_24h.get(k) is None)         # lo que NO mandó el comercio (se rellenó con un valor neutro)
        guardada = traduccion.con_neutros(fila_con_24h, id_eval)
        id_seguimiento = id_seguimiento_libre(           # dentro del bloqueo de escritura: nadie más puede tomarlo entre la revisión y el guardado
            lambda c: con.execute("SELECT 1 FROM evaluaciones WHERE id_seguimiento=?", (c,)).fetchone() is not None)
        con.execute(
            "INSERT INTO evaluaciones (id, comercio_id, id_transaccion, id_cliente, fecha, huella, fila_json, solicitud_hash, "
            "decision, decision_modelo, probabilidad, motivos_json, reglas_json, modelo_nombre, modelo_version, "
            "modelo_conjunto, modo, latencia_ms, creada, red_origen, estado_origen, id_seguimiento, ip_codigo, disp_codigo, monto_cop, no_enviados_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (id_eval, principal.comercio_id, solicitud.id_transaccion, solicitud.id_cliente, fila["fecha"],
             guardada["huella_dispositivo"], json.dumps(guardada, ensure_ascii=False, default=str), huella_solicitud,
             resultado["decision"], resultado["decision_modelo"], resultado["probabilidad"],
             json.dumps(resultado["motivos"], ensure_ascii=False), json.dumps(resultado["reglas"], ensure_ascii=False),
             self.meta["modelo"], self.meta["version"], self.meta["conjunto"], resultado["modo"], latencia, ahora_iso(), red_origen, resultado["estado_origen"],
             id_seguimiento, self.alm.codigo_ip(fila_con_24h.get("ip_huella")),
             None if fila.get("huella_dispositivo") is None else self.alm.codigo_dispositivo(fila["huella_dispositivo"]),
             fila.get("monto_cop"), json.dumps(no_enviados)))
        if resultado["decision"] == "EN_REVISION":
            con.execute("INSERT INTO revisiones (id_evaluacion, comercio_id, estado, creada) VALUES (?,?,'pendiente',?)",
                        (id_eval, principal.comercio_id, ahora_iso()))
        self.alm.auditar(con, principal.comercio_id, principal.clave_id, "evaluacion", id_eval,
                         {"decision": resultado["decision"], "modo": resultado["modo"], "latencia_ms": latencia})
        return self._respuesta(con, con.execute("SELECT * FROM evaluaciones WHERE id=?", (id_eval,)).fetchone(), repetida=False)

    # ----------------------------------------------------------- decisión
    def _campos_faltantes(self, fila: Dict) -> List[str]:
        faltan = []
        for grupo in self.grupos:
            for c in variables.REQUIERE[grupo]:
                if c in OPCIONALES_NULOS or c in ("fecha", "hora"):
                    continue
                if fila.get(c) is None and c not in faltan:
                    faltan.append(c)
        if fila.get("tipo_ip") is None and "tipo_ip" not in faltan:       # variable base: sin IP ni clasificación no se puede puntuar
            faltan.append("tipo_ip")
        if fila.get("estado_dispositivo") is None:                        # solo falta si NO hay modelo sin estado (ver _decidir)
            faltan.append("estado_dispositivo")
        return faltan

    def _previos(self, con: sqlite3.Connection, consulta: str, parametros: tuple) -> List[Dict]:
        filas = [json.loads(r["fila_json"]) for r in con.execute(consulta, parametros)]
        return list(reversed(filas))                        # de la más vieja a la más nueva

    def _variables(self, con: sqlite3.Connection, comercio_id: int, fila: Dict, id_eval: str):
        """Calcula las variables con LAS MISMAS funciones del entrenamiento sobre el historial guardado."""
        fecha = fila["fecha"]
        previos_cliente = self._previos(
            con, "SELECT fila_json FROM evaluaciones WHERE comercio_id=? AND id_cliente=? AND fecha<=? "
                 "ORDER BY fecha DESC, rowid DESC LIMIT ?", (comercio_id, fila["id_cliente"], fecha, self.aj.max_historial_cliente))
        desde = (datetime.fromisoformat(fecha) - timedelta(hours=24)).isoformat()
        n24 = 1 + sum(1 for p in previos_cliente if p["fecha"] > desde)         # incluye la compra actual, como en el entrenamiento
        actual = dict(fila, transacciones_ultimas_24h=n24)
        filas = list(previos_cliente)
        if "huella" in self.grupos and fila.get("huella_dispositivo"):
            filas = self._previos(
                con, "SELECT fila_json FROM evaluaciones WHERE comercio_id=? AND huella=? AND id_cliente<>? AND fecha<=? "
                     "ORDER BY fecha DESC, rowid DESC LIMIT ?",
                (comercio_id, fila["huella_dispositivo"], fila["id_cliente"], fecha, self.aj.max_historial_huella)) + filas
        mini = pd.DataFrame(filas + [actual])
        mini["fecha"] = pd.to_datetime(mini["fecha"])
        for c in NUMERICAS_CRUDAS:
            if c in mini:
                mini[c] = pd.to_numeric(mini[c], errors="coerce")
        calculada = variables.construir(mini, self.meta["conjunto"]).iloc[-1]
        columnas = self.meta["numericas"] + self.meta["categoricas"]
        pago = {c: _py(calculada[c]) for c in columnas}
        for extra in ("id_cliente", "ciudad_distinta_anterior", "minutos_desde_anterior", "monto_cop",
                      "transacciones_ultimas_24h", "estado_dispositivo", "tipo_ip"):
            if extra in calculada.index and extra not in pago:
                pago[extra] = _py(calculada[extra])
        return pago, actual

    def _decidir(self, con: sqlite3.Connection, comercio_id: int, fila: Dict, negra: List[str], id_eval: str):
        """Devuelve (resultado, fila con variables de 24 h). Nunca falla: si algo se rompe, modo degradado.

        Si el comercio no mandó el estado del dispositivo, se usa el modelo entrenado SIN esa variable (no se le inventa un valor al
        modelo normal: eso empeoraba mucho el resultado); si ese modelo no existe, el pago queda en modo degradado."""
        fila = dict(fila)
        modelo, estado_origen = self.art, "comercio"
        if fila.get("estado_dispositivo") is None:
            estado_origen = "no_enviado"
            if self.art_sin_estado is not None:
                modelo = self.art_sin_estado
                fila["estado_dispositivo"] = ESTADO_RELLENO               # relleno fijo: ese modelo no lo usa
        fila["estado_origen"] = estado_origen
        fila_24h = dict(fila, transacciones_ultimas_24h=1)
        try:
            faltan = self._campos_faltantes(fila)
            if faltan:
                raise DatosInsuficientes(faltan)
            pago, fila_24h = self._variables(con, comercio_id, fila, id_eval)
            r = predecir.analizar_transaccion(pago, modelo, lista_negra=negra)
            return {"decision": traduccion.DECISION_API[r["decision"]], "decision_modelo": traduccion.DECISION_API[r["decision_modelo"]],
                    "probabilidad": r["probabilidad_fraude"], "motivos": r["motivos"], "reglas": r["reglas_activadas"],
                    "modo": "normal", "estado_origen": estado_origen}, fila_24h
        except DatosInsuficientes as e:
            razon = "Modo degradado: faltan datos que el modelo necesita (" + ", ".join(NOMBRES_LEGIBLES.get(c, c) for c in e.faltan) + ")."
        except Exception:
            log.exception("falla del modelo; se responde en modo degradado")
            razon = "Modo degradado: falla interna del modelo."
        # Modo degradado: reglas de seguridad con lo que haya; si no rechazan, queda EN_REVISION (nunca se aprueba a ciegas)
        parcial = dict(fila_24h)
        parcial["id_cliente"] = fila["id_cliente"]
        reglas = MotorReglas(construir_reglas_seguridad(negra)).explicar(
            {k: v for k, v in parcial.items() if v is not None})
        rechaza = SEVERIDAD[reglas["decision"]] >= SEVERIDAD["BLOQUEAR"]
        decision = "RECHAZADA" if rechaza else "EN_REVISION"
        return {"decision": decision, "decision_modelo": None, "probabilidad": None, "motivos": [razon],
                "reglas": reglas["reglas_activadas"], "modo": "degradado", "estado_origen": estado_origen}, fila_24h

    # ----------------------------------------------------------- respuestas
    def _respuesta(self, con: sqlite3.Connection, e: sqlite3.Row, repetida: bool) -> Dict:
        rev = con.execute("SELECT * FROM revisiones WHERE id_evaluacion=?", (e["id"],)).fetchone()
        res = con.execute("SELECT resultado FROM resultados WHERE id_evaluacion=?", (e["id"],)).fetchone()
        revision = None
        decision_final = e["decision"]
        if rev is not None:
            revision = {"estado": rev["estado"], "decision_analista": rev["decision_analista"], "nota": rev["nota"],
                        "resuelta_en": rev["resuelta"]}
            decision_final = rev["decision_analista"] if rev["estado"] == "resuelta" else None
        return {"id_evaluacion": e["id"], "id_seguimiento": e["id_seguimiento"], "id_transaccion": e["id_transaccion"], "decision": e["decision"],
                "decision_final": decision_final, "puntaje_riesgo": e["probabilidad"],
                "motivos": json.loads(e["motivos_json"]), "reglas_activadas": json.loads(e["reglas_json"]), "modo": e["modo"],
                "modelo": {"nombre": e["modelo_nombre"], "version": e["modelo_version"], "conjunto_variables": e["modelo_conjunto"]},
                "revision": revision, "resultado_informado": res["resultado"] if res else None, "red_origen": e["red_origen"], "estado_origen": e["estado_origen"],
                "evaluada_en": e["creada"], "latencia_ms": e["latencia_ms"], "repetida": repetida}

    def _evaluacion_visible(self, con: sqlite3.Connection, principal: Principal, id_eval: str) -> sqlite3.Row:
        columna = "id_seguimiento" if id_eval.upper().startswith("AF-") else "id"          # se acepta el identificador de seguimiento o el interno
        e = con.execute(f"SELECT * FROM evaluaciones WHERE {columna}=?", (id_eval.upper() if columna == "id_seguimiento" else id_eval,)).fetchone()
        if e is None or (principal.rol == "comercio" and e["comercio_id"] != principal.comercio_id):
            raise ErrorAPI(404, "No encontrada", "No existe esa evaluación.")        # 404 también si es de otro comercio
        return e

    def obtener(self, principal: Principal, id_eval: str) -> Dict:
        with self.alm.lectura() as con:
            return self._respuesta(con, self._evaluacion_visible(con, principal, id_eval), repetida=False)

    # ----------------------------------------------------------- resultados (mejora continua)
    def registrar_resultado(self, principal: Principal, id_eval: str, sol) -> Dict:
        with self.alm.transaccion() as con:
            e = self._evaluacion_visible(con, principal, id_eval)
            fecha = (sol.fecha_confirmacion or self.reloj()).isoformat()
            previo = con.execute("SELECT resultado FROM resultados WHERE id_evaluacion=?", (id_eval,)).fetchone()
            if previo is not None:
                if previo["resultado"] == sol.resultado.value:
                    return self._respuesta(con, e, repetida=True)
                raise ErrorAPI(409, "Resultado ya informado", "Esa evaluación ya tiene un resultado distinto registrado.")
            con.execute("INSERT INTO resultados (id_evaluacion, comercio_id, resultado, fecha_confirmacion, nota, recibido) VALUES (?,?,?,?,?,?)",
                        (id_eval, e["comercio_id"], sol.resultado.value, fecha, sol.nota, ahora_iso()))
            self.alm.auditar(con, principal.comercio_id, principal.clave_id, "resultado", id_eval, {"resultado": sol.resultado.value})
            return self._respuesta(con, e, repetida=False)

    # ----------------------------------------------------------- revisiones
    ORDEN_REVISIONES = {"antiguos": "r.creada, e.id", "recientes": "r.creada DESC, e.id", "riesgo_desc": "e.probabilidad DESC, r.creada", "monto_desc": "e.monto_cop DESC, r.creada"}

    def listar_revisiones(self, estado: str, limite: int, desplazamiento: int, condiciones: Optional[List[str]] = None,
                          parametros: tuple = (), orden: str = "antiguos") -> Dict:
        """La cola de revisión. `condiciones` y `parametros` vienen ya armados (y validados) por consultas.Filtros; el orden sale de una lista fija."""
        donde = " AND ".join(["r.estado = ?"] + list(condiciones or []))
        with self.alm.lectura() as con:
            tot = con.execute(f"SELECT COUNT(*), COALESCE(SUM(e.monto_cop), 0) FROM revisiones r JOIN evaluaciones e ON e.id = r.id_evaluacion WHERE {donde}",
                              (estado,) + tuple(parametros)).fetchone()
            filas = con.execute(
                "SELECT e.*, c.nombre AS comercio_nombre, r.estado AS rev_estado, r.creada AS rev_creada FROM revisiones r JOIN evaluaciones e ON e.id=r.id_evaluacion "
                f"JOIN comercios c ON c.id = e.comercio_id WHERE {donde} ORDER BY {self.ORDEN_REVISIONES[orden]} LIMIT ? OFFSET ?",
                (estado,) + tuple(parametros) + (limite, desplazamiento)).fetchall()
        return {"total": tot[0], "monto_total_cop": tot[1], "revisiones": [
            {"id_evaluacion": f["id"], "id_seguimiento": f["id_seguimiento"], "comercio_id": f["comercio_id"], "comercio": f["comercio_nombre"], "id_transaccion": f["id_transaccion"],
             "puntaje_riesgo": f["probabilidad"], "motivos": json.loads(f["motivos_json"]),
             "reglas_activadas": json.loads(f["reglas_json"]), "creada_en": f["rev_creada"], "estado": f["rev_estado"],
             "resumen_pago": _resumen(json.loads(f["fila_json"]))} for f in filas]}

    def resolver_revision(self, principal: Principal, id_eval: str, sol) -> Dict:
        with self.alm.transaccion() as con:
            e = con.execute("SELECT * FROM evaluaciones WHERE id=?", (id_eval,)).fetchone()
            if e is None or con.execute("SELECT 1 FROM revisiones WHERE id_evaluacion=?", (id_eval,)).fetchone() is None:
                raise ErrorAPI(404, "No encontrada", "Esa evaluación no tiene una revisión.")
            cur = con.execute("UPDATE revisiones SET estado='resuelta', decision_analista=?, nota=?, clave_analista=?, resuelta=? "
                              "WHERE id_evaluacion=? AND estado='pendiente'",
                              (sol.decision.value, sol.nota, principal.clave_id, ahora_iso(), id_eval))
            if cur.rowcount != 1:
                raise ErrorAPI(409, "Revisión ya resuelta", "Esa revisión ya fue resuelta.")
            self.alm.auditar(con, principal.comercio_id, principal.clave_id, "revision_resuelta", id_eval, {"decision": sol.decision.value})
            return self._respuesta(con, e, repetida=False)

    # ----------------------------------------------------------- lista negra (por comercio)
    def agregar_lista_negra(self, principal: Principal, id_cliente: str, motivo: Optional[str]) -> Dict:
        with self.alm.transaccion() as con:
            con.execute("INSERT OR REPLACE INTO lista_negra (comercio_id, id_cliente, motivo, creada) VALUES (?,?,?,?)",
                        (principal.comercio_id, id_cliente, motivo, ahora_iso()))
            self.alm.auditar(con, principal.comercio_id, principal.clave_id, "lista_negra_agregar", None, {"id_cliente": id_cliente})
        return {"id_cliente": id_cliente, "motivo": motivo, "creada_en": ahora_iso()}

    def quitar_lista_negra(self, principal: Principal, id_cliente: str) -> None:
        with self.alm.transaccion() as con:
            cur = con.execute("DELETE FROM lista_negra WHERE comercio_id=? AND id_cliente=?", (principal.comercio_id, id_cliente))
            if cur.rowcount != 1:
                raise ErrorAPI(404, "No encontrado", "Ese cliente no está en la lista negra.")
            self.alm.auditar(con, principal.comercio_id, principal.clave_id, "lista_negra_quitar", None, {"id_cliente": id_cliente})

    def listar_lista_negra(self, principal: Principal) -> List[Dict]:
        with self.alm.lectura() as con:
            return [{"id_cliente": r["id_cliente"], "motivo": r["motivo"], "creada_en": r["creada"]} for r in con.execute(
                "SELECT * FROM lista_negra WHERE comercio_id=? ORDER BY creada", (principal.comercio_id,))]
