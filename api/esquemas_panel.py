"""Modelos de RESPUESTA de las rutas del panel (`/v1/panel/*`).

Antes estas rutas decían «objeto libre» en el OpenAPI y el frontend tenía que adivinar su forma a mano. Aquí se describen con exactitud
para que el contrato sea real y los tipos de TypeScript se generen de él.

Reglas de este archivo (las vigila `tests/test_esquemas_panel.py`):
- NO cambian ni un byte de lo que responde la API: la prueba compara la respuesta de cada ruta con lo que devuelve `ConsultasPanel`.
- Un campo que a veces NO viene (p. ej. `resuelta_por`, solo para el personal) es opcional con `= None` y la ruta usa `response_model_exclude_unset`,
  así sigue ausente en vez de aparecer como `null`.
- No se usa `extra="allow"`: abriría el esquema y los tipos generados perderían precisión. Si el servidor agrega un campo y aquí falta,
  la prueba de igualdad falla en lugar de que el campo se pierda en silencio.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .esquemas import Decision, DecisionAnalista, ReglaActivada, Senales


class _Base(BaseModel):
    pass


# ------------------------------------------------------------------ /v1/panel/transacciones
class TransaccionPanel(_Base):
    id_evaluacion: str
    id_seguimiento: Optional[str] = None
    comercio_id: int
    comercio: Optional[str] = None
    id_transaccion: str
    id_cliente: str
    fecha: str
    monto_cop: Optional[float] = None
    decision: Decision
    decision_modelo: Optional[Decision] = Field(
        None, description="Lo que decidió el modelo antes de las reglas de seguridad."
    )
    decision_final: Optional[Decision] = Field(
        None, description="La decisión que quedó: la del analista si hubo revisión; vacía mientras está pendiente."
    )
    puntaje_riesgo: Optional[float] = None
    modo: str
    reglas: List[str] = []
    ip: Optional[str] = Field(None, description="Código corto de la IP (nunca la IP).")
    dispositivo: Optional[str] = Field(None, description="Código corto de la huella del dispositivo.")
    red_origen: Optional[str] = None
    estado_origen: Optional[str] = None


class PaginaTransaccionesPanel(_Base):
    total: int
    transacciones: List[TransaccionPanel]


# ------------------------------------------------------------------ /v1/panel/resumen
class TotalesResumen(_Base):
    pagos: int
    aprobadas: int
    en_revision: int
    rechazadas: int
    monto_cop: Optional[float] = None
    monto_rechazado_cop: Optional[float] = None
    monto_en_revision_cop: Optional[float] = None
    modo_degradado: int
    sin_estado_del_dispositivo: int
    revisiones_pendientes: int
    latencia_media_ms: Optional[float] = None
    latencia_p95_ms: Optional[float] = None


class ResumenDia(_Base):
    dia: str
    aprobadas: int
    en_revision: int
    rechazadas: int


class ResumenComercio(_Base):
    comercio_id: int
    nombre: Optional[str] = None
    pagos: int
    aprobadas: int
    en_revision: int
    rechazadas: int
    monto_rechazado: Optional[float] = None


class ReglaMasActivada(_Base):
    regla: str
    veces: int
    rechazadas: int


class ResumenAnalista(_Base):
    analista: str
    resueltas: int
    aprobadas: int
    rechazadas: int
    minutos_promedio: Optional[float] = None


class CubetaPuntaje(_Base):
    desde: float
    hasta: float
    pagos: int


class RespuestaResumenPanel(_Base):
    totales: TotalesResumen
    por_dia: List[ResumenDia]
    por_comercio: List[ResumenComercio]
    reglas_mas_activadas: List[ReglaMasActivada]
    por_analista: List[ResumenAnalista]
    distribucion_del_puntaje: List[CubetaPuntaje]


# ------------------------------------------------------------------ /v1/panel/vinculos
class FiltroIp(_Base):
    ip: str


class FiltroDispositivo(_Base):
    dispositivo: str


class FiltroCliente(_Base):
    id_cliente: str
    comercio_id: int


class VinculoPorCodigo(_Base):
    """Una IP o un dispositivo compartido por varios clientes. `filtro` es el que se pasa a `/v1/panel/transacciones` para ver esos pagos."""

    codigo: str
    pagos: int
    clientes: int
    comercios: int
    rechazadas: int
    en_revision: int
    primera: str
    ultima: str


class VinculoMismaIp(VinculoPorCodigo):
    filtro: FiltroIp


class VinculoMismoDispositivo(VinculoPorCodigo):
    filtro: FiltroDispositivo


class VinculoClienteVariasIps(_Base):
    comercio_id: int
    comercio: Optional[str] = None
    id_cliente: str
    pagos: int
    ips: int
    rechazadas: int
    primera: str
    ultima: str
    filtro: FiltroCliente


class RafagaPorIp(_Base):
    codigo: str
    pagos_en_ventana: int
    pagos_totales: int
    filtro: FiltroIp


class HayMasVinculos(_Base):
    misma_ip_varios_clientes: bool
    mismo_dispositivo_varios_clientes: bool
    cliente_con_varias_ips: bool
    rafagas_por_ip: bool


class PaginacionVinculos(_Base):
    limite: int
    desplazamiento: int
    hay_mas: HayMasVinculos


class RespuestaVinculosPanel(_Base):
    minimo: int
    misma_ip_varios_clientes: List[VinculoMismaIp]
    mismo_dispositivo_varios_clientes: List[VinculoMismoDispositivo]
    cliente_con_varias_ips: List[VinculoClienteVariasIps]
    rafagas_por_ip: List[RafagaPorIp]
    ventana_rafaga_minutos: int
    paginacion: PaginacionVinculos


# ------------------------------------------------------------------ /v1/panel/comercios
class ComercioPanel(_Base):
    id: int
    nombre: Optional[str] = None
    suspendido: int
    pagos: int


# ------------------------------------------------------------------ /v1/panel/transacciones/{id_pago}
class IdentificadoresPago(_Base):
    id_seguimiento: Optional[str] = None
    id_evaluacion: str
    id_transaccion: str
    comercio_id: int
    comercio: Optional[str] = None
    id_cliente: str


class FechasPago(_Base):
    fecha_pago: str
    evaluada_en: str
    latencia_ms: Optional[float] = None


class DecisionDelPago(_Base):
    inicial: Decision
    del_modelo: Optional[Decision] = None
    final: Optional[Decision] = Field(None, description="Vacía mientras la revisión sigue pendiente.")
    decidio: str = Field(description="Quién decidió: modelo, regla de seguridad, analista, pendiente de analista…")


class UmbralesPago(_Base):
    revisar: Optional[float] = None
    bloquear: Optional[float] = None


class PasoDecision(_Base):
    paso: str
    detalle: str


class ModeloDelPago(_Base):
    nombre: Optional[str] = None
    version: Optional[str] = None
    conjunto_variables: Optional[str] = None
    modo: str
    estado_origen: Optional[str] = None
    red_origen: Optional[str] = None


class CampoRecibido(_Base):
    valor: Any = Field(None, description="El valor recibido (o calculado). Su tipo depende del campo.")
    enviado: bool = Field(description="false si el comercio no lo mandó y el sistema usó un valor de reemplazo.")


class RevisionDelPago(_Base):
    estado: str
    decision_analista: Optional[DecisionAnalista] = None
    nota: Optional[str] = None
    creada: str
    resuelta: Optional[str] = None
    resuelta_por: Optional[str] = Field(None, description="Solo para el personal; un comercio no recibe este campo.")


class ResultadoInformado(_Base):
    resultado: str
    fecha_confirmacion: Optional[str] = None
    nota: Optional[str] = None


class EntradaAuditoria(_Base):
    momento: str
    accion: str
    detalle: Dict[str, Any] = Field(description="Datos de la acción; su forma depende de `accion`.")
    actor: str


class PagoRelacionado(_Base):
    id_seguimiento: Optional[str] = None
    id_cliente: str
    decision: Decision
    fecha: str


class GrupoRelacionadoIp(_Base):
    codigo: str
    otros_pagos: int
    filtro: FiltroIp
    ultimos: List[PagoRelacionado]


class GrupoRelacionadoDispositivo(_Base):
    codigo: str
    otros_pagos: int
    filtro: FiltroDispositivo
    ultimos: List[PagoRelacionado]


class RelacionadoCliente(_Base):
    id_cliente: str
    otros_pagos: int
    filtro: FiltroCliente


class RelacionadosPago(_Base):
    misma_ip: Optional[GrupoRelacionadoIp] = None
    mismo_dispositivo: Optional[GrupoRelacionadoDispositivo] = None
    mismo_cliente: RelacionadoCliente


class RespuestaDetallePanel(_Base):
    identificadores: IdentificadoresPago
    fechas: FechasPago
    decision: DecisionDelPago
    puntaje_riesgo: Optional[float] = None
    umbrales: Optional[UmbralesPago] = Field(
        None, description="Vacío si hoy corre otro modelo distinto al que decidió este pago."
    )
    motivos: List[str]
    reglas_activadas: List[ReglaActivada]
    senales: Optional[Senales] = Field(None, description="Solo para el personal.")
    como_se_decidio: List[PasoDecision]
    modelo: ModeloDelPago
    datos_recibidos: Dict[str, Dict[str, CampoRecibido]] = Field(
        description="Por grupo (pago, dispositivo, red, identidad, comportamiento, calculado_por_la_api) y por campo."
    )
    campos_no_enviados: List[str]
    revision: Optional[RevisionDelPago] = None
    resultado_informado: Optional[ResultadoInformado] = None
    auditoria: List[EntradaAuditoria]
    relacionados: RelacionadosPago


# ------------------------------------------------------------------ /v1/panel/deriva
class ModeloDeriva(_Base):
    nombre: Optional[str] = None
    version: Optional[str] = None
    conjunto: Optional[str] = None
    huella_verificada: bool


class ReferenciaDeriva(_Base):
    disponible: bool
    pagos: Optional[int] = None


class VentanaDeriva(_Base):
    dias: int
    desde: str
    pagos: int
    minimo_requerido: int


class UmbralesDeriva(_Base):
    atencion: float
    alerta: float


class CubetaDeriva(_Base):
    desde: float
    hasta: float
    referencia: Optional[float] = None
    observada: Optional[float] = None


class RespuestaDeriva(_Base):
    modelo: ModeloDeriva
    referencia: ReferenciaDeriva
    ventana: VentanaDeriva
    psi: Optional[float] = None
    estado: str = Field(description="muestra_insuficiente, sin_referencia, sin_pagos, estable, atencion o alerta.")
    umbrales: UmbralesDeriva
    cubetas: List[CubetaDeriva]
    nota: str


# ------------------------------------------------------------------ /v1/panel/modelos
class ModeloRegistrado(_Base):
    id: int
    rol: str
    estado: str
    nombre: str
    version: str
    conjunto: str
    huella: str
    registrado: str
    promovido: Optional[str] = None
    archivado: Optional[str] = None
    tiene_referencia: bool
    en_ejecucion: bool
