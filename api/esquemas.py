"""Contrato de la API: lo que recibe y lo que responde (de aquí sale el OpenAPI y la página Swagger)."""
import ipaddress
import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PATRON_ID = r"^[A-Za-z0-9_\-:.]{1,64}$"
_CLAVES_DE_TARJETA = ("tarjeta", "card", "cvv", "cvc", "pan", "expir", "vencimiento")
MENSAJE_TARJETA = ("No envíes datos de tarjeta (número, vencimiento ni CVV): esta API no los acepta ni los guarda. "
                   "Identifica al cliente con un código tuyo, no con datos de la tarjeta.")


def _luhn(digitos: str) -> bool:
    total, alterna = 0, False
    for d in reversed(digitos):
        n = int(d)
        if alterna:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
        alterna = not alterna
    return total % 10 == 0


def contiene_numero_de_tarjeta(texto: str) -> bool:
    for m in re.finditer(r"(?:\d[ -]?){13,19}", texto):
        digitos = re.sub(r"\D", "", m.group())
        if 13 <= len(digitos) <= 19 and _luhn(digitos):
            return True
    return False


CAMPOS_PERMITIDOS = {"pego_numero_tarjeta"}      # lleva la palabra «tarjeta» pero es solo un sí/no


def _revisar_tarjeta(dato: Any) -> None:
    if isinstance(dato, dict):
        for k, v in dato.items():
            if str(k) not in CAMPOS_PERMITIDOS and any(p == str(k).lower() or str(k).lower().startswith(p + "_") or str(k).lower().endswith("_" + p)
                   or (len(p) > 3 and p in str(k).lower()) for p in _CLAVES_DE_TARJETA):
                raise ValueError(MENSAJE_TARJETA)
            _revisar_tarjeta(v)
    elif isinstance(dato, (list, tuple)):
        for v in dato:
            _revisar_tarjeta(v)
    elif isinstance(dato, str) and contiene_numero_de_tarjeta(dato):
        raise ValueError(MENSAJE_TARJETA)


# ------------------------------------------------------------------- códigos
class Decision(str, Enum):
    APROBADA = "APROBADA"
    RECHAZADA = "RECHAZADA"
    EN_REVISION = "EN_REVISION"


class Canal(str, Enum):
    WEB = "WEB"; APP_MOVIL = "APP_MOVIL"; POS = "POS"; API = "API"


class MetodoPago(str, Enum):
    VISA = "VISA"; MASTERCARD = "MASTERCARD"; PSE = "PSE"; NEQUI = "NEQUI"; DAVIPLATA = "DAVIPLATA"; PAYPAL = "PAYPAL"


class Categoria(str, Enum):
    ELECTRONICA = "ELECTRONICA"; FARMACIA = "FARMACIA"; HOTEL = "HOTEL"; RESTAURANTE = "RESTAURANTE"; ROPA = "ROPA"
    SERVICIOS_PUBLICOS = "SERVICIOS_PUBLICOS"; SUPERMERCADO = "SUPERMERCADO"
    SUSCRIPCION_DIGITAL = "SUSCRIPCION_DIGITAL"; TIENDA_ONLINE = "TIENDA_ONLINE"; TRANSPORTE = "TRANSPORTE"; OTRA = "OTRA"


class TipoIp(str, Enum):
    RESIDENCIAL = "RESIDENCIAL"; MOVIL = "MOVIL"; EMPRESARIAL = "EMPRESARIAL"; VPN = "VPN"; PROXY = "PROXY"
    DESCONOCIDA = "DESCONOCIDA"


class EstadoDispositivo(str, Enum):
    CONOCIDO = "CONOCIDO"; NUEVO = "NUEVO"; NO_RECONOCIDO = "NO_RECONOCIDO"


class TipoDispositivo(str, Enum):
    IPHONE = "IPHONE"; ANDROID = "ANDROID"; TABLET_ANDROID = "TABLET_ANDROID"; WINDOWS_PC = "WINDOWS_PC"; MACBOOK = "MACBOOK"


class NivelKyc(str, Enum):
    COMPLETO = "COMPLETO"; BASICO = "BASICO"; SIN_VERIFICAR = "SIN_VERIFICAR"


class Autenticacion2fa(str, Enum):
    NO_EXIGIDA = "NO_EXIGIDA"; EXIGIDA_APROBADA = "EXIGIDA_APROBADA"; EXIGIDA_FALLIDA = "EXIGIDA_FALLIDA"
    NO_DISPONIBLE = "NO_DISPONIBLE"; NO_APLICA = "NO_APLICA"


class ResultadoPago(str, Enum):
    FRAUDE_CONFIRMADO = "FRAUDE_CONFIRMADO"
    CONTRACARGO = "CONTRACARGO"
    LEGITIMA = "LEGITIMA"


class DecisionAnalista(str, Enum):
    APROBADA = "APROBADA"
    RECHAZADA = "RECHAZADA"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------ solicitud
class DispositivoEntrada(_Base):
    tipo: TipoDispositivo = Field(description="Tipo de equipo desde el que se paga.")
    estado: Optional[EstadoDispositivo] = Field(None, description="Si el comercio ya conocía este dispositivo para el cliente (lo sabe el sistema de cuentas del comercio). "
                                                "**Opcional:** si no lo envías, la API usa un modelo entrenado sin este dato (algo menos preciso) y lo indica en `estado_origen`.")
    huella: Optional[str] = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$",
                                  description="Huella (identificador) del dispositivo que calcula tu SDK. No uses el IMEI ni datos personales.")
    sistema_operativo: Optional[str] = Field(None, max_length=30)
    navegador: Optional[str] = Field(None, max_length=40)
    resolucion_pantalla: Optional[str] = Field(None, pattern=r"^\d{3,5}x\d{3,5}$", examples=["390x844"])
    zona_horaria: Optional[str] = Field(None, max_length=40, pattern=r"^[A-Za-z0-9_/+\-]+$", examples=["America/Bogota"])
    es_emulador: Optional[bool] = None


class RedEntrada(_Base):
    ip: Optional[str] = Field(None, max_length=45, description="IP del comprador (IPv4 o IPv6) tal como la ve TU servidor (p. ej. la cabecera X-Forwarded-For). "
                              "La API la ubica y la evalúa y **no la guarda, no la devuelve ni la registra**. Si la envías, el resto del bloque `red` es opcional.",
                              examples=["190.24.10.55"])
    tipo_ip: Optional[TipoIp] = Field(None, description="Clasificación de la IP. Opcional si envías `ip`; si lo envías, **manda sobre** lo que diga el proveedor.")
    pais: Optional[str] = Field(None, min_length=2, max_length=40, description="País donde se ubica la IP.", examples=["Colombia"])
    ciudad_estimada: Optional[str] = Field(None, max_length=60)
    distancia_a_ciudad_km: Optional[float] = Field(None, ge=0, le=25000, description="Distancia entre la ubicación de la IP y la ciudad declarada.")
    es_datacenter: Optional[bool] = None
    reputacion: Optional[int] = Field(None, ge=0, le=100, description="Reputación de la IP (100 = muy buena).")

    @field_validator("ip")
    @classmethod
    def _ip_valida(cls, v):
        if v is None:
            return v
        try:
            return str(ipaddress.ip_address(v.strip()))              # forma canónica (IPv6 siempre igual)
        except ValueError:
            raise ValueError("`ip` no es una dirección IPv4 o IPv6 válida.")

    @model_validator(mode="after")
    def _ip_o_tipo(self):
        if self.ip is None and self.tipo_ip is None:
            raise ValueError("En `red` envía la `ip` (la API la ubica y la evalúa) o, al menos, su clasificación `tipo_ip`.")
        return self


class IdentidadEntrada(_Base):
    kyc_nivel: Optional[NivelKyc] = None
    edad_cuenta_dias: Optional[int] = Field(None, ge=0, le=36500)
    autenticacion_2fa: Optional[Autenticacion2fa] = None
    intentos_fallidos_24h: Optional[int] = Field(None, ge=0, le=1000)
    cambio_credenciales_7d: Optional[bool] = None


class ComportamientoEntrada(_Base):
    tiempo_formulario_seg: Optional[float] = Field(None, ge=0, le=86400)
    velocidad_tecleo_ms: Optional[float] = Field(None, ge=0, le=100000)
    pego_numero_tarjeta: Optional[bool] = Field(None, description="Si el comprador pegó (no tecleó) el número de tarjeta. Solo el hecho de pegar; nunca el número.")
    mouse_natural: Optional[float] = Field(None, ge=0, le=1, description="0 = movimiento de bot, 1 = movimiento humano. Solo se mide en web.")


class SolicitudEvaluacion(_Base):
    id_transaccion: str = Field(pattern=PATRON_ID, description="Identificador de la transacción en TU sistema. Sirve para que un reintento no se cuente dos veces.")
    id_cliente: str = Field(pattern=PATRON_ID, description="Código de tu cliente (un identificador tuyo; no uses documento, correo ni teléfono).")
    fecha_pago: Optional[datetime] = Field(None, description="Momento del pago (ISO 8601). Si no se envía, se usa la hora del servidor. Una fecha sin zona se toma como hora de Bogotá.")
    monto_cop: int = Field(gt=0, description="Monto en pesos colombianos, sin decimales.", examples=[250000])
    moneda: str = Field("COP", pattern=r"^COP$", description="Por ahora solo COP.")
    metodo_pago: MetodoPago
    canal_pago: Canal
    categoria: Categoria = Field(description="Rubro de la compra.")
    ciudad: str = Field(min_length=2, max_length=60, description="Ciudad declarada del comprador.", examples=["Bogotá"])
    dispositivo: DispositivoEntrada
    red: RedEntrada
    identidad: Optional[IdentidadEntrada] = None
    comportamiento: Optional[ComportamientoEntrada] = None

    @model_validator(mode="before")
    @classmethod
    def _sin_datos_de_tarjeta(cls, datos: Any):
        _revisar_tarjeta(datos)
        return datos


class SolicitudResultado(_Base):
    resultado: ResultadoPago = Field(description="Qué pasó finalmente con ese pago.")
    fecha_confirmacion: Optional[datetime] = Field(None, description="Cuándo se confirmó (si no se envía, ahora).")
    nota: Optional[str] = Field(None, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def _sin_datos_de_tarjeta(cls, datos: Any):
        _revisar_tarjeta(datos)
        return datos


class SolicitudResolverRevision(_Base):
    decision: DecisionAnalista
    nota: Optional[str] = Field(None, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def _sin_datos_de_tarjeta(cls, datos: Any):
        _revisar_tarjeta(datos)
        return datos


class SolicitudComercio(_Base):
    nombre: str = Field(min_length=2, max_length=80)


class SolicitudListaNegra(_Base):
    motivo: Optional[str] = Field(None, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def _sin_datos_de_tarjeta(cls, datos: Any):
        _revisar_tarjeta(datos)
        return datos


# ------------------------------------------------------------ respuestas
class ReglaActivada(BaseModel):
    regla: str
    accion: str
    motivo: str


class InfoModelo(BaseModel):
    nombre: str = Field(examples=["gradient_boosting"])
    version: str = Field(examples=["0.2.0"])
    conjunto_variables: str = Field(examples=["completo"])


class InfoRevision(BaseModel):
    estado: str = Field(description="`pendiente` o `resuelta`.")
    decision_analista: Optional[DecisionAnalista] = None
    nota: Optional[str] = None
    resuelta_en: Optional[datetime] = None


class RespuestaEvaluacion(BaseModel):
    id_evaluacion: str
    id_seguimiento: str = Field(description="Identificador de seguimiento que asigna ESTE sistema a cada pago, legible (p. ej. `AF-7K3Q-9M2X`). Guárdalo junto a tu `id_transaccion`: "
                                            "sirve para rastrear el pago en el panel y para hablar de él con soporte. También llega en la cabecera `X-Id-Seguimiento`.")
    id_transaccion: str
    decision: Decision = Field(description="Decisión inicial: APROBADA, RECHAZADA o EN_REVISION.")
    decision_final: Optional[Decision] = Field(None, description="La decisión que debes aplicar. Es igual a `decision` salvo en revisión: vale null mientras un analista no resuelve y luego APROBADA o RECHAZADA.")
    puntaje_riesgo: Optional[float] = Field(None, ge=0, le=1, description="Probabilidad estimada de fraude (0 a 1). Null si no se pudo puntuar.")
    motivos: List[str] = []
    reglas_activadas: List[ReglaActivada] = []
    modo: str = Field(description="`normal`, o `degradado` si no se pudo usar el modelo (queda en revisión salvo que una regla de seguridad rechace).")
    modelo: InfoModelo
    revision: Optional[InfoRevision] = None
    resultado_informado: Optional[ResultadoPago] = None
    estado_origen: str = Field("comercio", description="`comercio` si mandaste `dispositivo.estado`; `no_enviado` si no lo mandaste (en ese caso se usó el modelo que no lo necesita).")
    red_origen: str = Field("comercio", description="De dónde salieron los datos de red: `comercio` (los mandaste tú), `proveedor:<nombre>` (la API ubicó tu `ip`) o `proveedor_fallo` (el proveedor no respondió: se usó solo lo que mandaste).")
    evaluada_en: datetime
    latencia_ms: float
    repetida: bool = Field(False, description="True si es la respuesta guardada de una solicitud repetida (mismo `id_transaccion`).")


class FilaRevision(BaseModel):
    id_evaluacion: str
    id_seguimiento: Optional[str] = None
    comercio: Optional[str] = Field(None, description="Nombre del comercio.")
    comercio_id: int
    id_transaccion: str
    puntaje_riesgo: Optional[float]
    motivos: List[str]
    reglas_activadas: List[ReglaActivada]
    creada_en: datetime
    estado: str
    resuelta_en: Optional[datetime] = None
    resuelta_por: Optional[str] = Field(None, description="Quién resolvió la revisión (nombre de la persona). Solo lo ven analistas y administradores.")
    resumen_pago: Dict[str, Any] = Field({}, description="Datos del pago que el analista necesita para decidir (sin datos de tarjeta ni huella).")


class ListaRevisiones(BaseModel):
    total: int = Field(description="Cuántas revisiones cumplen los filtros.")
    monto_total_cop: float = Field(0, description="Suma de los montos de esas revisiones.")
    revisiones: List[FilaRevision]


class RespuestaYo(BaseModel):
    rol: str = Field(description="`comercio`, `analista` o `admin`.")
    usuario: Optional[str] = Field(None, description="Para analistas y administradores: el nombre de la persona dueña de la clave.")
    comercio: str = Field(description="El comercio al que pertenece la clave (para analistas y administradores, la plataforma).")


class RespuestaComercio(BaseModel):
    id_comercio: int
    nombre: str
    clave_api: str = Field(description="Se muestra UNA sola vez. Guárdala en un lugar seguro: no se puede recuperar.")


class EntradaListaNegra(BaseModel):
    id_cliente: str
    motivo: Optional[str] = None
    creada_en: datetime


class RespuestaSalud(BaseModel):
    estado: str
    version_api: str
    modelo: InfoModelo
    configuracion_insegura: bool = Field(description="True si el servicio corre con el secreto de desarrollo (no usar así en producción).")
    advertencias: List[str] = Field([], description="Avisos de configuración (por ejemplo, si el modelo se entrenó de una forma distinta a como la API calcula sus variables).")


class Problema(BaseModel):
    """Formato de error (RFC 7807)."""
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: Optional[str] = None
    errores: Optional[List[Dict[str, str]]] = None
