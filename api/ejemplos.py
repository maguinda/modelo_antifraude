"""Pagos de ejemplo para probar la API desde Swagger (aparecen en el menú «Examples» de `POST /v1/evaluaciones`).

Ninguno lleva `fecha_pago`: se usa la hora del servidor, así nunca quedan viejos. Los resultados `ESPERADOS`
son los del modelo en uso al escribir esto; una prueba los vuelve a comprobar con el modelo actual.
"""
from typing import Any, Dict


def _pago(id_tx: str, cliente: str, **cambios: Any) -> Dict:
    p: Dict[str, Any] = {
        "id_transaccion": id_tx, "id_cliente": cliente, "monto_cop": 180000, "moneda": "COP", "metodo_pago": "NEQUI",
        "canal_pago": "APP_MOVIL", "categoria": "ROPA", "ciudad": "Bogotá",
        "dispositivo": {"tipo": "IPHONE", "estado": "CONOCIDO", "huella": f"FP-{cliente}", "sistema_operativo": "iOS",
                        "navegador": "App nativa", "resolucion_pantalla": "390x844", "zona_horaria": "America/Bogota",
                        "es_emulador": False},
        "red": {"tipo_ip": "MOVIL", "pais": "Colombia", "ciudad_estimada": "Bogotá", "distancia_a_ciudad_km": 6,
                "es_datacenter": False, "reputacion": 90},
        "identidad": {"kyc_nivel": "COMPLETO", "edad_cuenta_dias": 700, "autenticacion_2fa": "NO_EXIGIDA",
                      "intentos_fallidos_24h": 0, "cambio_credenciales_7d": False},
        "comportamiento": {"tiempo_formulario_seg": 40, "velocidad_tecleo_ms": 170, "pego_numero_tarjeta": False}}
    for k, v in cambios.items():
        p[k] = {**p[k], **v} if isinstance(v, dict) and isinstance(p.get(k), dict) else v
    for bloque in p.values():                                         # un None en un ejemplo significa «no mandes este campo»
        if isinstance(bloque, dict):
            for clave in [c for c, x in bloque.items() if x is None]:
                del bloque[clave]
    return p


_WEB = dict(canal_pago="WEB", metodo_pago="VISA", categoria="ELECTRONICA")

EJEMPLOS_SOLICITUD: Dict[str, Dict] = {
    "01_aprobada_pago_normal": {
        "summary": "1. Pago normal de un cliente conocido  →  APROBADA",
        "description": "Compra pequeña, dispositivo conocido, IP móvil con buena reputación, cuenta antigua y verificada.",
        "value": _pago("DEMO-0001", "DEMO-CLIENTE-1")},
    "02_en_revision_comportamiento_poco_humano": {
        "summary": "2. Compra grande con comportamiento poco humano  →  EN_REVISION",
        "description": "Nada es muy raro por separado: dispositivo conocido, 2FA aprobado, cuenta de 700 días. Pero el monto es alto, la verificación es solo básica y el mouse se mueve de forma poco natural al llenar el formulario. El modelo duda y el pago queda para un analista.",
        "value": _pago("DEMO-0002", "DEMO-CLIENTE-2", monto_cop=1400000, **_WEB,
                       dispositivo={"navegador": "Safari"},
                       red={"tipo_ip": "EMPRESARIAL"},
                       identidad={"kyc_nivel": "BASICO", "autenticacion_2fa": "EXIGIDA_APROBADA"},
                       comportamiento={"tiempo_formulario_seg": 20, "mouse_natural": 0.2})},
    "03_en_revision_vpn_cliente_nuevo": {
        "summary": "3. Compra grande desde VPN con cuenta de 20 días  →  EN_REVISION",
        "description": "Dispositivo conocido, pero IP de VPN con mala reputación, cuenta reciente y un formulario llenado muy rápido.",
        "value": _pago("DEMO-0003", "DEMO-CLIENTE-3", monto_cop=1400000, **_WEB,
                       dispositivo={"navegador": "Safari"},
                       red={"tipo_ip": "VPN", "reputacion": 45, "distancia_a_ciudad_km": 150},
                       identidad={"edad_cuenta_dias": 20},
                       comportamiento={"tiempo_formulario_seg": 8})},
    "04_rechazada_pago_sospechoso": {
        "summary": "4. Pago grande desde VPN rusa con dispositivo no reconocido  →  RECHAZADA",
        "description": "Todo apunta a fraude: dispositivo no reconocido, VPN en otro país, 2FA fallido, cuenta de 2 días, formulario en 6 segundos y tarjeta pegada. Además se activa una regla de seguridad.",
        "value": _pago("DEMO-0004", "DEMO-CLIENTE-4", monto_cop=4800000, metodo_pago="VISA", canal_pago="WEB",
                       categoria="ELECTRONICA", ciudad="Medellín",
                       dispositivo={"tipo": "WINDOWS_PC", "estado": "NO_RECONOCIDO", "sistema_operativo": "Windows",
                                    "navegador": "Chrome", "resolucion_pantalla": "1920x1080", "zona_horaria": "Europe/Moscow"},
                       red={"tipo_ip": "VPN", "pais": "Rusia", "ciudad_estimada": "Moscú", "distancia_a_ciudad_km": 10700,
                            "es_datacenter": True, "reputacion": 35},
                       identidad={"kyc_nivel": "BASICO", "edad_cuenta_dias": 2, "autenticacion_2fa": "EXIGIDA_FALLIDA",
                                  "intentos_fallidos_24h": 3, "cambio_credenciales_7d": True},
                       comportamiento={"tiempo_formulario_seg": 6, "velocidad_tecleo_ms": 20, "pego_numero_tarjeta": True,
                                       "mouse_natural": 0.1})},
    "05_viaje_imposible_paso_1_de_2": {
        "summary": "5. Viaje imposible, paso 1 de 2: compra normal en Bogotá  →  APROBADA",
        "description": "Envíalo primero. Después, **enseguida**, envía el ejemplo 6 (mismo cliente, otra ciudad).",
        "value": _pago("DEMO-0005", "DEMO-VIAJERO")},
    "06_viaje_imposible_paso_2_de_2": {
        "summary": "6. Viaje imposible, paso 2 de 2: minutos después, desde Cali  →  RECHAZADA",
        "description": "El mismo cliente del ejemplo 5 paga desde Cali, con otro dispositivo, a los pocos minutos. Se activa la regla `viaje_imposible`. Si lo envías horas después de enviar el 5, ya no es imposible.",
        "value": _pago("DEMO-0006", "DEMO-VIAJERO", monto_cop=2400000, metodo_pago="VISA", canal_pago="WEB", categoria="ELECTRONICA",
                       ciudad="Cali",
                       dispositivo={"tipo": "ANDROID", "estado": "NUEVO", "huella": "FP-DEMO-VIAJERO-OTRO", "sistema_operativo": "Android",
                                    "navegador": "Chrome", "resolucion_pantalla": "412x915"},
                       red={"tipo_ip": "RESIDENCIAL", "ciudad_estimada": "Cali"})},
    "07_degradado_solo_campos_obligatorios": {
        "summary": "7. Solo los campos obligatorios  →  EN_REVISION (modo degradado)",
        "description": "Faltan datos que el modelo necesita (huella, identidad, geolocalización...). La API no aprueba a ciegas: responde `modo: degradado` y deja el pago en revisión.",
        "value": {"id_transaccion": "DEMO-0007", "id_cliente": "DEMO-CLIENTE-7", "monto_cop": 90000, "metodo_pago": "PSE",
                  "canal_pago": "WEB", "categoria": "FARMACIA", "ciudad": "Cali",
                  "dispositivo": {"tipo": "ANDROID", "estado": "CONOCIDO"}, "red": {"tipo_ip": "RESIDENCIAL"}}},
    "08_lista_negra": {
        "summary": "8. Cliente en tu lista negra  →  RECHAZADA (primero agrégalo con PUT /v1/lista-negra)",
        "description": "Antes de enviarlo, usa `PUT /v1/lista-negra/{id_cliente}` con `id_cliente = DEMO-CLIENTE-MALO`. Sin eso, este pago normal se aprobaría.",
        "value": _pago("DEMO-0008", "DEMO-CLIENTE-MALO")},
    "10_solo_la_ip_la_api_la_ubica": {
        "summary": "10. Solo mandas la IP: la API la ubica y la evalúa  →  APROBADA",
        "description": "En `red` va **solo `ip`**, la que ve tu servidor. La API la ubica (aquí: Bogotá, conexión residencial, buena reputación), completa los datos de red y evalúa. La respuesta trae `red_origen: proveedor:simulado`. La IP no se guarda ni se devuelve. "
                       "Los rangos de prueba que reconoce el proveedor simulado son 192.0.2.x (residencial, Bogotá), 198.51.100.x (móvil, Medellín) y 203.0.113.x (empresarial, VPN y proxy).",
        "value": _pago("DEMO-0010", "DEMO-CLIENTE-10", red={"ip": "192.0.2.10", "tipo_ip": None, "pais": None, "ciudad_estimada": None,
                                                           "distancia_a_ciudad_km": None, "es_datacenter": None, "reputacion": None})},
    "11_ip_de_un_proxy_en_otro_pais": {
        "summary": "11. Solo la IP, y es un proxy de otro país  →  EN_REVISION",
        "description": "Un pago por lo demás normal, pero la IP (203.0.113.150) la ubica el proveedor como un **proxy en Países Bajos** con mala reputación. El comercio no tuvo que clasificarla: la API lo hizo. El modelo duda y el pago queda para un analista.",
        "value": _pago("DEMO-0011", "DEMO-CLIENTE-11", red={"ip": "203.0.113.150", "tipo_ip": None, "pais": None, "ciudad_estimada": None,
                                                           "distancia_a_ciudad_km": None, "es_datacenter": None, "reputacion": None})},
    "12_sin_estado_del_dispositivo": {
        "summary": "12. No mandas el estado del dispositivo  →  APROBADA (con el modelo sin estado)",
        "description": "El comercio no sabe si el cliente ya usó este equipo, así que **no envía `dispositivo.estado`**. La API no le inventa un valor al modelo: usa el modelo entrenado sin ese dato y lo dice en `estado_origen: no_enviado`. Es algo menos preciso que cuando sí lo envías.",
        "value": _pago("DEMO-0012", "DEMO-CLIENTE-12", dispositivo={"estado": None})},
    "09_error_datos_de_tarjeta": {
        "summary": "9. Intento de enviar datos de tarjeta  →  error 422 (la API no los acepta)",
        "description": "Un pago normal al que se le agrega un número de tarjeta (número de prueba, no real). La API lo rechaza y **no repite** el número en el mensaje.",
        "value": {**_pago("DEMO-0009", "DEMO-CLIENTE-9"), "numero_tarjeta": "4111111111111111"}},
}

EJEMPLOS_SOLICITUD = dict(sorted(EJEMPLOS_SOLICITUD.items()))      # en el menú de Swagger salen en orden 01, 02... 11

# Qué debe responder el modelo en uso a cada ejemplo, en este orden (08 después de agregar al cliente a la lista negra)
ESPERADOS: Dict[str, Any] = {
    "01_aprobada_pago_normal": "APROBADA", "02_en_revision_comportamiento_poco_humano": "EN_REVISION",
    "03_en_revision_vpn_cliente_nuevo": "EN_REVISION", "04_rechazada_pago_sospechoso": "RECHAZADA",
    "05_viaje_imposible_paso_1_de_2": "APROBADA", "06_viaje_imposible_paso_2_de_2": "RECHAZADA",
    "07_degradado_solo_campos_obligatorios": "EN_REVISION", "08_lista_negra": "RECHAZADA", "09_error_datos_de_tarjeta": 422,
    "10_solo_la_ip_la_api_la_ubica": "APROBADA", "12_sin_estado_del_dispositivo": "APROBADA", "11_ip_de_un_proxy_en_otro_pais": "EN_REVISION",
}
