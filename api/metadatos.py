"""Textos y respuestas documentadas de la API (lo que se ve en Swagger), el formato de error estándar y las constantes compartidas."""

from fastapi.responses import JSONResponse

from . import esquemas as es
from .contexto import id_peticion

VERSION_API = "1.0.0"
MEDIA_PROBLEMA = "application/problem+json"
PATRON_UUID = r"^(?:[0-9a-fA-F\-]{36}|[Aa][Ff]-[0-9A-Za-z]{4}-[0-9A-Za-z]{4})$"  # el identificador interno o el de seguimiento (AF-XXXX-XXXX)

DESCRIPCION = """
API para decidir, **antes de aprobar un pago**, si una transacción se **aprueba**, se **rechaza** o queda **en revisión**.

## Cómo se usa
1. Tu equipo recibe una **clave de API** (una por comercio) y la envía en la cabecera `X-API-Key` en cada llamada.
2. Por cada pago, llamas a `POST /v1/evaluaciones` con los datos del pago.
3. La respuesta trae la `decision`:
   * **APROBADA**: procesa el pago.
   * **RECHAZADA**: no lo proceses.
   * **EN_REVISION**: retén el pago. Un analista lo resolverá; consulta `GET /v1/evaluaciones/{id}` hasta que `decision_final` deje de ser `null` (será APROBADA o RECHAZADA).
4. Cuando sepas qué pasó de verdad con el pago (fraude confirmado, contracargo o legítimo), avísalo con `POST /v1/evaluaciones/{id}/resultado`: así el modelo mejora.

## Cómo probar desde esta página (Swagger)
1. Pulsa **Authorize** y pega tu clave de API (si ya viene autorizada, no hace falta).
2. Abre `POST /v1/evaluaciones` → **Try it out**.
3. En el menú **Examples** elige un pago de ejemplo (aprobado, en revisión, rechazado, viaje imposible, modo degradado...) y pulsa **Execute**.
4. Para evaluar otro pago parecido, **cambia `id_transaccion`**: repetir el mismo id devuelve la respuesta guardada (`repetida: true`).

## Reglas importantes
* **Nunca envíes datos de tarjeta** (número, vencimiento, CVV). La API los rechaza y no los guarda.
* **Estado del dispositivo (opcional):** `dispositivo.estado` lo sabe el sistema de cuentas del comercio (¿este cliente ya usó este equipo?). Si no lo envías, la API usa un modelo entrenado sin ese dato (algo menos preciso: en las pruebas con datos sintéticos, más falsas alarmas) y la respuesta lo indica en `estado_origen`.
* **IP del comprador:** envía `red.ip` (la que ve tu servidor). La API la ubica y la evalúa (país, tipo de conexión, reputación) y **no la guarda, no la devuelve ni la registra**. Lo demás de `red` es opcional y, si lo envías, **manda sobre** lo que diga el proveedor. La respuesta indica de dónde salieron los datos de red (`red_origen`).
* **Reintentos seguros:** repetir una llamada con el mismo `id_transaccion` y los mismos datos devuelve la misma respuesta, sin contarla dos veces. Con datos distintos responde `409`.
* **Si la API no responde o devuelve 5xx**, trata el pago como **EN_REVISION** en tu sistema.
* **Modo degradado:** si el modelo no se puede usar (faltan datos o hay una falla), la respuesta trae `modo: degradado` y el pago queda en revisión, salvo que una regla de seguridad lo rechace. Nunca se aprueba a ciegas.
* Los errores usan el formato estándar `application/problem+json`.

> Los modelos actuales se entrenaron con **datos sintéticos**. No son una promesa de desempeño sobre fraude real.

**Créditos.** La ubicación por IP usa las bases gratuitas de [DB-IP.com](https://db-ip.com) (licencia CC BY 4.0). La API nunca devuelve la ubicación de la IP en la respuesta de una evaluación.
"""

ETIQUETAS = [
    {"name": "Evaluaciones", "description": "Decidir sobre un pago y consultar la decisión."},
    {"name": "Resultados", "description": "Informar qué pasó finalmente con un pago (mejora continua)."},
    {"name": "Revisiones", "description": "Cola de revisión manual para analistas."},
    {"name": "Lista negra", "description": "Clientes que tu comercio quiere rechazar siempre."},
    {"name": "Administración", "description": "Alta de comercios y claves (solo administradores)."},
    {"name": "Estado", "description": "Salud del servicio y modelo en uso."},
]

RESPUESTAS_ERROR = {
    401: {"model": es.Problema, "description": "Falta la clave de API o no es válida."},
    403: {
        "model": es.Problema,
        "description": "La clave no tiene permiso para esta operación, o su comercio está suspendido (o, si el servicio lo exige, no tiene acuerdo de datos vigente).",
    },
    404: {"model": es.Problema, "description": "No existe (o no pertenece a tu comercio)."},
    409: {
        "model": es.Problema,
        "description": "Conflicto: el recurso ya existe con datos distintos o ya fue resuelto.",
    },
    413: {"model": es.Problema, "description": "La petición es demasiado grande."},
    422: {"model": es.Problema, "description": "Datos inválidos (incluye el envío de datos de tarjeta)."},
    429: {"model": es.Problema, "description": "Demasiadas peticiones. Respeta la cabecera `Retry-After`."},
    503: {"model": es.Problema, "description": "Servicio no disponible: trata el pago como EN_REVISION y reintenta."},
}


def problema(status: int, titulo: str, detalle: str, instancia: str, errores=None, cabeceras=None) -> JSONResponse:
    cuerpo = {"type": "about:blank", "title": titulo, "status": status, "detail": detalle, "instance": instancia}
    cabeceras = dict(cabeceras or {})
    rid = id_peticion.get()
    if rid:  # para citarlo en soporte y encontrar la petición en los registros
        cuerpo["request_id"] = rid
        cabeceras["X-Request-ID"] = rid
    if errores:
        cuerpo["errores"] = errores
    return JSONResponse(cuerpo, status_code=status, media_type=MEDIA_PROBLEMA, headers=cabeceras)


EJEMPLOS_RESPUESTA = {
    "aprobada": {
        "summary": "Aprobada",
        "value": {
            "id_evaluacion": "3f1c9a52-8e0b-4d1f-9a54-0c6f6c2a7b11",
            "id_seguimiento": "AF-7K3Q-9M2X",
            "id_transaccion": "PED-100234",
            "decision": "APROBADA",
            "decision_final": "APROBADA",
            "puntaje_riesgo": 0.053,
            "motivos": [],
            "reglas_activadas": [],
            "modo": "normal",
            "modelo": {"nombre": "gradient_boosting", "version": "0.2.0", "conjunto_variables": "completo"},
            "revision": None,
            "resultado_informado": None,
            "evaluada_en": "2026-11-12T18:30:00.123456",
            "latencia_ms": 18.4,
            "repetida": False,
        },
    },
    "rechazada": {
        "summary": "Rechazada por el modelo y una regla de seguridad",
        "value": {
            "id_evaluacion": "7a2d41f0-1b7c-4b0e-8d2a-52e2f5b3c9aa",
            "id_seguimiento": "AF-4H8D-2WZN",
            "id_transaccion": "PED-100235",
            "decision": "RECHAZADA",
            "decision_final": "RECHAZADA",
            "puntaje_riesgo": 0.997,
            "motivos": [],
            "reglas_activadas": [
                {
                    "regla": "dispositivo_no_reconocido_con_ip_anonima",
                    "accion": "BLOQUEAR",
                    "motivo": "Dispositivo no reconocido y desde una IP anónima",
                }
            ],
            "modo": "normal",
            "modelo": {"nombre": "gradient_boosting", "version": "0.2.0", "conjunto_variables": "completo"},
            "revision": None,
            "resultado_informado": None,
            "evaluada_en": "2026-11-12T03:12:01.004211",
            "latencia_ms": 21.7,
            "repetida": False,
        },
    },
    "en_revision": {
        "summary": "En revisión: espera la decisión de un analista",
        "value": {
            "id_evaluacion": "b9e0c8d3-5a64-49c1-b0a7-1f3d2e4c5d66",
            "id_seguimiento": "AF-B6TC-0R5J",
            "id_transaccion": "PED-100237",
            "decision": "EN_REVISION",
            "decision_final": None,
            "puntaje_riesgo": 0.41,
            "motivos": [],
            "reglas_activadas": [],
            "modo": "normal",
            "modelo": {"nombre": "gradient_boosting", "version": "0.2.0", "conjunto_variables": "completo"},
            "revision": {"estado": "pendiente", "decision_analista": None, "nota": None, "resuelta_en": None},
            "resultado_informado": None,
            "evaluada_en": "2026-11-12T18:31:00.000000",
            "latencia_ms": 19.9,
            "repetida": False,
        },
    },
}
