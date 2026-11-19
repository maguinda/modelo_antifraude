// ARCHIVO GENERADO: no lo edites a mano.
// Sale de api/openapi.json con `python -m modelo_ia.api.exportar_tipos_ts` (una prueba del repo falla si no está al día).
// Un campo opcional (`?`) es uno que la API a veces no manda; `| null` es uno que puede venir vacío.


export interface AcuerdoHistorial {
  dias_para_vencer?: number | null;
  /** `sin_acuerdo`, `vigente`, `vence_pronto` (30 días o menos), `vencido` o `reemplazado` (uno más nuevo lo sustituyó). */
  estado: string;
  firmado_en?: string | null;
  referencia: string;
  registrado: string;
  version?: string | null;
  vigente_hasta?: string | null;
}

export type Autenticacion2fa = "NO_EXIGIDA" | "EXIGIDA_APROBADA" | "EXIGIDA_FALLIDA" | "NO_DISPONIBLE" | "NO_APLICA";

export interface CampoRecibido {
  /** false si el comercio no lo mandó y el sistema usó un valor de reemplazo. */
  enviado: boolean;
  /** El valor recibido (o calculado). Su tipo depende del campo. */
  valor?: unknown;
}

export type Canal = "WEB" | "APP_MOVIL" | "POS" | "API";

export type Categoria = "ELECTRONICA" | "FARMACIA" | "HOTEL" | "RESTAURANTE" | "ROPA" | "SERVICIOS_PUBLICOS" | "SUPERMERCADO" | "SUSCRIPCION_DIGITAL" | "TIENDA_ONLINE" | "TRANSPORTE" | "OTRA";

export interface ComercioPanel {
  id: number;
  nombre?: string | null;
  pagos: number;
  suspendido: number;
}

export interface ComportamientoEntrada {
  /** 0 = movimiento de bot, 1 = movimiento humano. Solo se mide en web. */
  mouse_natural?: number | null;
  /** Si el comprador pegó (no tecleó) el número de tarjeta. Solo el hecho de pegar; nunca el número. */
  pego_numero_tarjeta?: boolean | null;
  tiempo_formulario_seg?: number | null;
  velocidad_tecleo_ms?: number | null;
}

export interface CubetaDeriva {
  desde: number;
  hasta: number;
  observada?: number | null;
  referencia?: number | null;
}

export interface CubetaPuntaje {
  desde: number;
  hasta: number;
  pagos: number;
}

export type Decision = "APROBADA" | "RECHAZADA" | "EN_REVISION";

export type DecisionAnalista = "APROBADA" | "RECHAZADA";

export interface DecisionDelPago {
  /** Quién decidió: modelo, regla de seguridad, analista, pendiente de analista… */
  decidio: string;
  del_modelo?: Decision | null;
  /** Vacía mientras la revisión sigue pendiente. */
  final?: Decision | null;
  inicial: Decision;
}

export interface DispositivoEntrada {
  es_emulador?: boolean | null;
  /** Si el comercio ya conocía este dispositivo para el cliente (lo sabe el sistema de cuentas del comercio). **Opcional:** si no lo envías, la API usa un modelo entrenado sin este dato (algo menos preciso) y lo indica en `estado_origen`. */
  estado?: EstadoDispositivo | null;
  /** Huella (identificador) del dispositivo que calcula tu SDK. No uses el IMEI ni datos personales. */
  huella?: string | null;
  navegador?: string | null;
  resolucion_pantalla?: string | null;
  sistema_operativo?: string | null;
  /** Tipo de equipo desde el que se paga. */
  tipo: TipoDispositivo;
  zona_horaria?: string | null;
}

export interface EntradaAuditoria {
  accion: string;
  actor: string;
  /** Datos de la acción; su forma depende de `accion`. */
  detalle: Record<string, unknown>;
  momento: string;
}

export interface EntradaListaNegra {
  creada_en: string;
  id_cliente: string;
  motivo?: string | null;
}

export interface EstadoAcuerdo {
  dias_para_vencer?: number | null;
  /** `sin_acuerdo`, `vigente`, `vence_pronto` (30 días o menos), `vencido` o `reemplazado` (uno más nuevo lo sustituyó). */
  estado: string;
  firmado_en?: string | null;
  version?: string | null;
  vigente_hasta?: string | null;
}

export type EstadoDispositivo = "CONOCIDO" | "NUEVO" | "NO_RECONOCIDO";

export interface FechasPago {
  evaluada_en: string;
  fecha_pago: string;
  latencia_ms?: number | null;
}

export interface FilaComercio {
  acuerdo: EstadoAcuerdo;
  claves_activas: number;
  creado: string;
  /** `activo` o `suspendido`. */
  estado: string;
  id: number;
  motivo_suspension?: string | null;
  /** Solo lo ve un administrador (en una persona natural es su cédula). */
  nit?: string | null;
  nombre: string;
  pagos: number;
  razon_social?: string | null;
  suspendido_en?: string | null;
  ultimo_uso?: string | null;
}

export interface FilaRevision {
  /** Nombre del comercio. */
  comercio?: string | null;
  comercio_id: number;
  creada_en: string;
  estado: string;
  id_evaluacion: string;
  id_seguimiento?: string | null;
  id_transaccion: string;
  motivos: string[];
  puntaje_riesgo: number | null;
  reglas_activadas: ReglaActivada[];
  resuelta_en?: string | null;
  /** Quién resolvió la revisión (nombre de la persona). Solo lo ven analistas y administradores. */
  resuelta_por?: string | null;
  /** Datos del pago que el analista necesita para decidir (sin datos de tarjeta ni huella). */
  resumen_pago?: Record<string, unknown>;
  /** Las señales que más pesaron en el puntaje (solo para pagos que no se aprobaron). Explican al modelo, no prueban fraude. */
  senales?: Senales;
}

export interface FiltroCliente {
  comercio_id: number;
  id_cliente: string;
}

export interface FiltroDispositivo {
  dispositivo: string;
}

export interface FiltroIp {
  ip: string;
}

export interface GrupoRelacionadoDispositivo {
  codigo: string;
  filtro: FiltroDispositivo;
  otros_pagos: number;
  ultimos: PagoRelacionado[];
}

export interface GrupoRelacionadoIp {
  codigo: string;
  filtro: FiltroIp;
  otros_pagos: number;
  ultimos: PagoRelacionado[];
}

export interface HayMasVinculos {
  cliente_con_varias_ips: boolean;
  misma_ip_varios_clientes: boolean;
  mismo_dispositivo_varios_clientes: boolean;
  rafagas_por_ip: boolean;
}

export interface IdentidadEntrada {
  autenticacion_2fa?: Autenticacion2fa | null;
  cambio_credenciales_7d?: boolean | null;
  edad_cuenta_dias?: number | null;
  intentos_fallidos_24h?: number | null;
  kyc_nivel?: NivelKyc | null;
}

export interface IdentificadoresPago {
  comercio?: string | null;
  comercio_id: number;
  id_cliente: string;
  id_evaluacion: string;
  id_seguimiento?: string | null;
  id_transaccion: string;
}

export interface InfoModelo {
  conjunto_variables: string;
  nombre: string;
  version: string;
}

export interface InfoRevision {
  decision_analista?: DecisionAnalista | null;
  /** `pendiente` o `resuelta`. */
  estado: string;
  nota?: string | null;
  resuelta_en?: string | null;
}

export interface ListaRevisiones {
  /** Suma de los montos de esas revisiones. */
  monto_total_cop?: number;
  revisiones: FilaRevision[];
  /** Cuántas revisiones cumplen los filtros. */
  total: number;
}

export type MetodoPago = "VISA" | "MASTERCARD" | "PSE" | "NEQUI" | "DAVIPLATA" | "PAYPAL";

export interface ModeloDelPago {
  conjunto_variables?: string | null;
  estado_origen?: string | null;
  modo: string;
  nombre?: string | null;
  red_origen?: string | null;
  version?: string | null;
}

export interface ModeloDeriva {
  conjunto?: string | null;
  huella_verificada: boolean;
  nombre?: string | null;
  version?: string | null;
}

export interface ModeloRegistrado {
  archivado?: string | null;
  conjunto: string;
  en_ejecucion: boolean;
  estado: string;
  huella: string;
  id: number;
  nombre: string;
  promovido?: string | null;
  registrado: string;
  rol: string;
  tiene_referencia: boolean;
  version: string;
}

export type NivelKyc = "COMPLETO" | "BASICO" | "SIN_VERIFICAR";

export interface PaginaTransaccionesPanel {
  total: number;
  transacciones: TransaccionPanel[];
}

export interface PaginacionVinculos {
  desplazamiento: number;
  hay_mas: HayMasVinculos;
  limite: number;
}

export interface PagoRelacionado {
  decision: Decision;
  fecha: string;
  id_cliente: string;
  id_seguimiento?: string | null;
}

export interface PasoDecision {
  detalle: string;
  paso: string;
}

/** Formato de error (RFC 7807). */
export interface Problema {
  detail: string;
  errores?: Record<string, string>[] | null;
  instance?: string | null;
  /** Identificador de esta petición (también en la cabecera `X-Request-ID`). Cítalo al pedir soporte. */
  request_id?: string | null;
  status: number;
  title: string;
  type?: string;
}

export interface RafagaPorIp {
  codigo: string;
  filtro: FiltroIp;
  pagos_en_ventana: number;
  pagos_totales: number;
}

export interface RedEntrada {
  ciudad_estimada?: string | null;
  /** Distancia entre la ubicación de la IP y la ciudad declarada. */
  distancia_a_ciudad_km?: number | null;
  es_datacenter?: boolean | null;
  /** IP del comprador (IPv4 o IPv6) tal como la ve TU servidor (p. ej. la cabecera X-Forwarded-For). La API la ubica y la evalúa y **no la guarda, no la devuelve ni la registra**. Si la envías, el resto del bloque `red` es opcional. */
  ip?: string | null;
  /** País donde se ubica la IP. */
  pais?: string | null;
  /** Reputación de la IP (100 = muy buena). */
  reputacion?: number | null;
  /** Clasificación de la IP. Opcional si envías `ip`; si lo envías, **manda sobre** lo que diga el proveedor. */
  tipo_ip?: TipoIp | null;
}

export interface ReferenciaDeriva {
  disponible: boolean;
  pagos?: number | null;
}

export interface ReglaActivada {
  accion: string;
  motivo: string;
  regla: string;
}

export interface ReglaMasActivada {
  rechazadas: number;
  regla: string;
  veces: number;
}

export interface RelacionadoCliente {
  filtro: FiltroCliente;
  id_cliente: string;
  otros_pagos: number;
}

export interface RelacionadosPago {
  misma_ip?: GrupoRelacionadoIp | null;
  mismo_cliente: RelacionadoCliente;
  mismo_dispositivo?: GrupoRelacionadoDispositivo | null;
}

export interface RespuestaAcuerdo {
  acuerdo: EstadoAcuerdo;
  id_acuerdo: number;
}

export interface RespuestaComercio {
  /** Se muestra UNA sola vez. Guárdala en un lugar seguro: no se puede recuperar. */
  clave_api: string;
  id_comercio: number;
  nombre: string;
}

export interface RespuestaDeriva {
  cubetas: CubetaDeriva[];
  /** muestra_insuficiente, sin_referencia, sin_pagos, estable, atencion o alerta. */
  estado: string;
  modelo: ModeloDeriva;
  nota: string;
  psi?: number | null;
  referencia: ReferenciaDeriva;
  umbrales: UmbralesDeriva;
  ventana: VentanaDeriva;
}

export interface RespuestaDetallePanel {
  auditoria: EntradaAuditoria[];
  campos_no_enviados: string[];
  como_se_decidio: PasoDecision[];
  /** Por grupo (pago, dispositivo, red, identidad, comportamiento, calculado_por_la_api) y por campo. */
  datos_recibidos: Record<string, Record<string, CampoRecibido>>;
  decision: DecisionDelPago;
  fechas: FechasPago;
  identificadores: IdentificadoresPago;
  modelo: ModeloDelPago;
  motivos: string[];
  puntaje_riesgo?: number | null;
  reglas_activadas: ReglaActivada[];
  relacionados: RelacionadosPago;
  resultado_informado?: ResultadoInformado | null;
  revision?: RevisionDelPago | null;
  /** Solo para el personal. */
  senales?: Senales | null;
  /** Vacío si hoy corre otro modelo distinto al que decidió este pago. */
  umbrales?: UmbralesPago | null;
}

export interface RespuestaEstadoComercio {
  /** `false` si ya estaba así (repetir la operación es seguro y no hace nada). */
  cambio: boolean;
  /** `activo` o `suspendido`, después de la operación. */
  estado: string;
  id_comercio: number;
}

export interface RespuestaEvaluacion {
  /** Decisión inicial: APROBADA, RECHAZADA o EN_REVISION. */
  decision: Decision;
  /** La decisión que debes aplicar. Es igual a `decision` salvo en revisión: vale null mientras un analista no resuelve y luego APROBADA o RECHAZADA. */
  decision_final?: Decision | null;
  /** `comercio` si mandaste `dispositivo.estado`; `no_enviado` si no lo mandaste (en ese caso se usó el modelo que no lo necesita). */
  estado_origen?: string;
  evaluada_en: string;
  id_evaluacion: string;
  /** Identificador de seguimiento que asigna ESTE sistema a cada pago, legible (p. ej. `AF-7K3Q-9M2X`). Guárdalo junto a tu `id_transaccion`: sirve para rastrear el pago en el panel y para hablar de él con soporte. También llega en la cabecera `X-Id-Seguimiento`. */
  id_seguimiento: string;
  id_transaccion: string;
  latencia_ms: number;
  modelo: InfoModelo;
  /** `normal`, o `degradado` si no se pudo usar el modelo (queda en revisión salvo que una regla de seguridad rechace). */
  modo: string;
  motivos?: string[];
  /** Probabilidad estimada de fraude (0 a 1). Null si no se pudo puntuar. */
  puntaje_riesgo?: number | null;
  /** De dónde salieron los datos de red: `comercio` (los mandaste tú), `proveedor:<nombre>` (la API ubicó tu `ip`) o `proveedor_fallo` (el proveedor no respondió: se usó solo lo que mandaste). */
  red_origen?: string;
  reglas_activadas?: ReglaActivada[];
  /** True si es la respuesta guardada de una solicitud repetida (mismo `id_transaccion`). */
  repetida?: boolean;
  resultado_informado?: ResultadoPago | null;
  revision?: InfoRevision | null;
}

export interface RespuestaResumenPanel {
  distribucion_del_puntaje: CubetaPuntaje[];
  por_analista: ResumenAnalista[];
  por_comercio: ResumenComercio[];
  por_dia: ResumenDia[];
  reglas_mas_activadas: ReglaMasActivada[];
  totales: TotalesResumen;
}

export interface RespuestaSalud {
  /** Avisos de configuración (por ejemplo, si el modelo se entrenó de una forma distinta a como la API calcula sus variables). */
  advertencias?: string[];
  /** True si el servicio corre con el secreto de desarrollo (no usar así en producción). */
  configuracion_insegura: boolean;
  estado: string;
  modelo: InfoModelo;
  version_api: string;
}

export interface RespuestaVinculosPanel {
  cliente_con_varias_ips: VinculoClienteVariasIps[];
  minimo: number;
  misma_ip_varios_clientes: VinculoMismaIp[];
  mismo_dispositivo_varios_clientes: VinculoMismoDispositivo[];
  paginacion: PaginacionVinculos;
  rafagas_por_ip: RafagaPorIp[];
  ventana_rafaga_minutos: number;
}

export interface RespuestaYo {
  /** El comercio al que pertenece la clave (para analistas y administradores, la plataforma). */
  comercio: string;
  /** `comercio`, `analista` o `admin`. */
  rol: string;
  /** Para analistas y administradores: el nombre de la persona dueña de la clave. */
  usuario?: string | null;
}

export interface ResultadoInformado {
  fecha_confirmacion?: string | null;
  nota?: string | null;
  resultado: string;
}

export type ResultadoPago = "FRAUDE_CONFIRMADO" | "CONTRACARGO" | "LEGITIMA";

export interface ResumenAnalista {
  analista: string;
  aprobadas: number;
  minutos_promedio?: number | null;
  rechazadas: number;
  resueltas: number;
}

export interface ResumenComercio {
  aprobadas: number;
  comercio_id: number;
  en_revision: number;
  monto_rechazado?: number | null;
  nombre?: string | null;
  pagos: number;
  rechazadas: number;
}

export interface ResumenDia {
  aprobadas: number;
  dia: string;
  en_revision: number;
  rechazadas: number;
}

export interface RevisionDelPago {
  creada: string;
  decision_analista?: DecisionAnalista | null;
  estado: string;
  nota?: string | null;
  resuelta?: string | null;
  /** Solo para el personal; un comercio no recibe este campo. */
  resuelta_por?: string | null;
}

export interface Senal {
  clave: string;
  /** Cuántos puntos de probabilidad de fraude movió esa señal (positivo: empujó hacia fraude; negativo: a favor). */
  puntos: number;
  /** La señal en palabras, por ejemplo «El monto ($ 800.000) es 4,2 veces el promedio de este cliente». */
  texto: string;
}

export interface Senales {
  /** Las señales que más lo bajaron (a favor del pago). */
  bajan?: Senal[];
  /** La probabilidad de fraude que tendría un pago típico (la referencia contra la que se mide). */
  puntaje_tipico?: number | null;
  /** Las señales que más empujaron el riesgo hacia arriba. */
  suben?: Senal[];
}

export interface SolicitudAcuerdo {
  /** Fecha de firma (AAAA-MM-DD). No puede ser futura. */
  firmado_en: string;
  /** Cómo encontrar el archivo firmado (su nombre o su huella SHA-256). El documento NO se guarda en el sistema. */
  referencia: string;
  /** Versión del documento, por ejemplo `v1.2`. */
  version: string;
  /** Opcional. Si no se pone, no vence. */
  vigente_hasta?: string | null;
}

export interface SolicitudComercio {
  /** Opcional. Ojo: en una persona natural el NIT es su cédula (dato personal); solo lo ve un administrador. */
  nit?: string | null;
  nombre: string;
  /** Opcional. Nombre legal del comercio. */
  razon_social?: string | null;
}

export interface SolicitudEvaluacion {
  canal_pago: Canal;
  /** Rubro de la compra. */
  categoria: Categoria;
  /** Ciudad declarada del comprador. */
  ciudad: string;
  comportamiento?: ComportamientoEntrada | null;
  dispositivo: DispositivoEntrada;
  /** Momento del pago (ISO 8601). Si no se envía, se usa la hora del servidor. Una fecha sin zona se toma como hora de Bogotá. */
  fecha_pago?: string | null;
  /** Código de tu cliente (un identificador tuyo; no uses documento, correo ni teléfono). */
  id_cliente: string;
  /** Identificador de la transacción en TU sistema. Sirve para que un reintento no se cuente dos veces. */
  id_transaccion: string;
  identidad?: IdentidadEntrada | null;
  metodo_pago: MetodoPago;
  /** Por ahora solo COP. */
  moneda?: string;
  /** Monto en pesos colombianos, sin decimales. */
  monto_cop: number;
  red: RedEntrada;
}

export interface SolicitudListaNegra {
  motivo?: string | null;
}

export interface SolicitudResolverRevision {
  decision: DecisionAnalista;
  nota?: string | null;
}

export interface SolicitudResultado {
  /** Cuándo se confirmó (si no se envía, ahora). */
  fecha_confirmacion?: string | null;
  nota?: string | null;
  /** Qué pasó finalmente con ese pago. */
  resultado: ResultadoPago;
}

export interface SolicitudSuspension {
  /** Por qué se suspende. Queda en la auditoría. */
  motivo: string;
}

export type TipoDispositivo = "IPHONE" | "ANDROID" | "TABLET_ANDROID" | "WINDOWS_PC" | "MACBOOK";

export type TipoIp = "RESIDENCIAL" | "MOVIL" | "EMPRESARIAL" | "VPN" | "PROXY" | "DESCONOCIDA";

export interface TotalesResumen {
  aprobadas: number;
  en_revision: number;
  latencia_media_ms?: number | null;
  latencia_p95_ms?: number | null;
  modo_degradado: number;
  monto_cop?: number | null;
  monto_en_revision_cop?: number | null;
  monto_rechazado_cop?: number | null;
  pagos: number;
  rechazadas: number;
  revisiones_pendientes: number;
  sin_estado_del_dispositivo: number;
}

export interface TransaccionPanel {
  comercio?: string | null;
  comercio_id: number;
  decision: Decision;
  /** La decisión que quedó: la del analista si hubo revisión; vacía mientras está pendiente. */
  decision_final?: Decision | null;
  /** Lo que decidió el modelo antes de las reglas de seguridad. */
  decision_modelo?: Decision | null;
  /** Código corto de la huella del dispositivo. */
  dispositivo?: string | null;
  estado_origen?: string | null;
  fecha: string;
  id_cliente: string;
  id_evaluacion: string;
  id_seguimiento?: string | null;
  id_transaccion: string;
  /** Código corto de la IP (nunca la IP). */
  ip?: string | null;
  modo: string;
  monto_cop?: number | null;
  puntaje_riesgo?: number | null;
  red_origen?: string | null;
  reglas?: string[];
}

export interface UmbralesDeriva {
  alerta: number;
  atencion: number;
}

export interface UmbralesPago {
  bloquear?: number | null;
  revisar?: number | null;
}

export interface VentanaDeriva {
  desde: string;
  dias: number;
  minimo_requerido: number;
  pagos: number;
}

export interface VinculoClienteVariasIps {
  comercio?: string | null;
  comercio_id: number;
  filtro: FiltroCliente;
  id_cliente: string;
  ips: number;
  pagos: number;
  primera: string;
  rechazadas: number;
  ultima: string;
}

export interface VinculoMismaIp {
  clientes: number;
  codigo: string;
  comercios: number;
  en_revision: number;
  filtro: FiltroIp;
  pagos: number;
  primera: string;
  rechazadas: number;
  ultima: string;
}

export interface VinculoMismoDispositivo {
  clientes: number;
  codigo: string;
  comercios: number;
  en_revision: number;
  filtro: FiltroDispositivo;
  pagos: number;
  primera: string;
  rechazadas: number;
  ultima: string;
}
