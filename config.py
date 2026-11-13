"""Configuración central del modelo antifraude.

Todo lo que se puede querer cambiar (rutas, meses de cada conjunto, variables,
costos, umbrales) vive aquí. Los demás archivos no tienen números escritos a mano.
"""
from pathlib import Path

VERSION = "0.3.0"
SEMILLA = 42

# --- Rutas ------------------------------------------------------------------
RAIZ = Path(__file__).resolve().parent
DATOS_POR_DEFECTO = RAIZ / "datos" / "dataset_antifraude_500000_transacciones.csv"
CARPETA_MODELOS = RAIZ / "modelos_guardados"
ARCHIVO_EXPERIMENTOS = RAIZ / "resultados" / "experimentos.csv"
ARCHIVO_H1 = RAIZ / "resultados" / "h1_resultado.json"

# --- Partición por fecha (el modelo siempre decide sobre compras del futuro) --
# Cortes por FECHA (incluyen el día indicado). Sirve igual para datos de un año o de dos.
#   entrenamiento: todo hasta FIN_ENTRENAMIENTO
#   validación:    hasta FIN_VALIDACION (sirve para elegir ajustes, como el umbral, sin mirar la prueba)
#   prueba:        todo lo posterior (nota final: solo se mira al final)
FIN_ENTRENAMIENTO = "2026-06-30"
FIN_VALIDACION = "2026-08-31"

# --- Columnas del archivo de datos ---------------------------------------------
COLUMNAS_OBLIGATORIAS = [
    "numero_transaccion", "fecha_pago", "dispositivo", "localizacion", "monto_cop",
    "detalle_pago", "metodo_pago", "canal_pago", "tipo_ip", "estado_dispositivo",
    "transacciones_ultimas_24h", "nivel_riesgo", "es_fraude", "id_cliente",
]
COLUMNA_OBJETIVO = "es_fraude"

# Nunca pueden entrar al modelo como variable de entrada:
#  - es_fraude: es lo que se predice.
#  - patron: dice qué tipo de fraude es (en la vida real no se sabe).
#  - nivel_riesgo: alerta calculada con las otras señales; repetiría reglas ajenas.
#  - id_cliente y numero_transaccion: identificadores (el historial se construye aparte).
#  - Columnas que solo se conocen DESPUÉS del pago (revisión manual, contracargos): usarlas
#    como entrada sería mirar el futuro.
#  - id_comercio y huella_dispositivo: identificadores; se usan para construir variables
#    (por ejemplo, cuántos clientes distintos usan una misma huella), nunca tal cual.
COLUMNAS_PROHIBIDAS = ["es_fraude", "patron", "nivel_riesgo", "id_cliente",
                       "numero_transaccion", "fecha_pago", "detalle_pago",
                       "id_comercio", "huella_dispositivo",
                       "revision_manual", "resultado_revision", "minutos_revision",
                       "contracargo", "dias_hasta_contracargo", "fecha_confirmacion_fraude"]

# --- Variables por grupo y por conjunto (cada etapa de mejora puede agregar uno nuevo) -----
CATEGORICAS = ["dispositivo", "localizacion", "metodo_pago", "canal_pago",
               "tipo_ip", "estado_dispositivo", "categoria"]
NUMERICAS_BASE = ["monto_cop", "transacciones_ultimas_24h", "hora"]
NUMERICAS_HORA = ["es_madrugada", "es_fin_semana"]
NUMERICAS_HISTORIAL = ["minutos_desde_anterior", "ciudad_distinta_anterior",
                       "n_compras_previas", "monto_vs_promedio",
                       "dispositivo_nuevo_cliente"]
# Grupos que usan las columnas nuevas del dataset de 1 millón:
NUMERICAS_PERFIL = ["ip_frecuencia_previa", "franja_frecuencia_previa"]
NUMERICAS_HUELLA = ["huella_clientes_distintos", "huella_compras_previas", "es_emulador",
                    "so_distinto_del_dispositivo", "navegador_headless", "zona_horaria_no_colombia"]
CATEGORICAS_HUELLA = ["sistema_operativo", "navegador", "resolucion_pantalla"]
NUMERICAS_GEO_IP = ["ip_fuera_de_colombia", "ip_sin_ubicacion", "distancia_ip_ciudad_km",
                    "ip_es_datacenter", "ip_reputacion", "zona_horaria_no_coincide_ip"]
CATEGORICAS_GEO_IP = ["ip_pais"]
NUMERICAS_IDENTIDAD = ["edad_cuenta_dias", "cuenta_nueva", "intentos_fallidos_24h", "cambio_credenciales_7d"]
CATEGORICAS_IDENTIDAD = ["kyc_nivel", "autenticacion_2fa"]
NUMERICAS_COMPORTAMIENTO = ["tiempo_formulario_seg", "velocidad_tecleo_ms", "pego_numero_tarjeta",
                            "mouse_natural", "sin_medicion_comportamiento", "sin_medicion_mouse"]
# Valores neutros para lo que no se mide (el comportamiento solo se mide en web y app; el mouse, solo en web)
NEUTRO_COMPORTAMIENTO = {"tiempo_formulario_seg": 45.0, "velocidad_tecleo_ms": 180.0,
                         "pego_numero_tarjeta": 0.0, "mouse_natural": 0.8}

# grupo -> (numéricas, categóricas)
GRUPOS_VARIABLES = {
    "hora": (NUMERICAS_HORA, []),
    "historial": (NUMERICAS_HISTORIAL, []),
    "perfil": (NUMERICAS_PERFIL, []),                       # necesita el grupo "historial"
    "huella": (NUMERICAS_HUELLA, CATEGORICAS_HUELLA),
    "geo_ip": (NUMERICAS_GEO_IP, CATEGORICAS_GEO_IP),
    "identidad": (NUMERICAS_IDENTIDAD, CATEGORICAS_IDENTIDAD),
    "comportamiento": (NUMERICAS_COMPORTAMIENTO, []),
}
# conjunto -> grupos que lo forman (siempre incluye las variables base)
CONJUNTOS_GRUPOS = {
    "base": [],
    "hora": ["hora"],
    "historial": ["hora", "historial"],
    "perfil": ["hora", "historial", "perfil"],
    "huella": ["hora", "historial", "huella"],
    "geo_ip": ["hora", "historial", "geo_ip"],
    "identidad": ["hora", "historial", "identidad"],
    "comportamiento": ["hora", "historial", "comportamiento"],
    "completo": ["hora", "historial", "perfil", "huella", "geo_ip", "identidad", "comportamiento"],
}
CONJUNTOS_VARIABLES = {nombre: NUMERICAS_BASE + [c for g in grupos for c in GRUPOS_VARIABLES[g][0]]
                       for nombre, grupos in CONJUNTOS_GRUPOS.items()}
CONJUNTO_POR_DEFECTO = "historial"
# Con qué se arma el historial de un cliente (y de una huella):
#   "plataforma": con las compras en TODOS los comercios juntos (red compartida: más señal, pero exige identificar al cliente
#                 igual en todos los comercios y compartir datos entre ellos; decisión de negocio y de Ley 1581).
#   "comercio":   solo con las compras de ESE comercio (aislado: es lo que calcula hoy la API).
ALCANCES_HISTORIAL = ("plataforma", "comercio")
ALCANCE_HISTORIAL = "plataforma"
TOPE_MINUTOS_HISTORIAL = 43_200   # 30 días: tope cuando no hay compra anterior

# --- Costos y decisión ---------------------------------------------------------
# SUPUESTO: cada falsa alarma cuesta 15.000 COP; un fraude que se escapa cuesta su monto.
COSTO_FALSA_ALARMA = 15_000
UMBRALES_A_PROBAR = [round(0.05 * k, 2) for k in range(1, 20)]   # 0,05 a 0,95
UMBRAL_BLOQUEAR_MINIMO = 0.70   # desde aquí se bloquea; entre el de revisión y este, se revisa
