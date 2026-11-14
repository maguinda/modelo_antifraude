"""El identificador de la petición que se está atendiendo, disponible en cualquier parte del código sin pasarlo de mano en mano.

Lo fija `observabilidad.ObservabilidadMiddleware` al empezar cada petición. Lo leen los registros (logs) y los errores `problem+json`.
"""

from contextvars import ContextVar
from typing import Optional

id_peticion: ContextVar[Optional[str]] = ContextVar("id_peticion", default=None)
