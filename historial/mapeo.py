"""El archivo de mapeo: le dice al adaptador qué columna del archivo del comercio corresponde a cada campo de la API."""
import json
from pathlib import Path
from typing import Dict, List, Optional

from .campos import CAMPOS, POR_RUTA, REQUERIDOS, normalizar


class ErrorDeMapeo(Exception):
    pass


FORMATO_POR_DEFECTO = {"separador": "auto", "codificacion": "utf-8-sig", "hoja": None, "decimal": ".", "zona_horaria_origen": "America/Bogota",
                       "formato_fecha": None, "escala_monto": 1}


def plantilla() -> Dict:
    """Un mapeo vacío con todos los campos y sus opciones, para llenar a mano."""
    return {
        "descripcion": "Mapeo del archivo de pagos del comercio. En «columnas» escribe el nombre EXACTO de la columna del archivo para cada campo (déjalo vacío si no la tienes). "
                       "En «valores» traduce los textos del comercio a los códigos de la API. En «por_defecto» pon un valor fijo solo si es verdad para TODO el archivo.",
        "formato": dict(FORMATO_POR_DEFECTO),
        "columnas": {c.ruta: "" for c in CAMPOS},
        "valores": {c.ruta: {"texto del comercio (en minúsculas)": "UNA_DE: " + ", ".join(c.opciones)} for c in CAMPOS if c.tipo == "enum"},
        "por_defecto": {},
        "resultado": {"columna": "", "fraude": ["1", "si", "fraude", "contracargo"], "legitima": ["0", "no", "legitima"], "fecha_columna": ""},
        "ayuda_de_campos": {c.ruta: c.ayuda + (" Opciones: " + ", ".join(c.opciones) if c.opciones else "") for c in CAMPOS},
    }


def sugerir(columnas: List[str]) -> Dict:
    """Propone qué columna corresponde a cada campo mirando sus nombres. Solo propone: la persona debe revisar."""
    norm = {normalizar(c): c for c in columnas if c}
    m = plantilla()
    usadas = set()
    for campo in CAMPOS:
        for s in (normalizar(campo.ruta.split(".")[-1]),) + tuple(normalizar(x) for x in campo.sinonimos):
            if s in norm and norm[s] not in usadas:
                m["columnas"][campo.ruta] = norm[s]; usadas.add(norm[s]); break
    for s in ("contracargo", "chargeback", "fraude", "fraud", "es fraude", "is fraud", "resultado", "etiqueta"):
        if s in norm and norm[s] not in usadas:
            m["resultado"]["columna"] = norm[s]; break
    m.pop("ayuda_de_campos", None)
    return m


def guardar(m: Dict, ruta: Path) -> None:
    Path(ruta).write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cargar(ruta: Path) -> Dict:
    try:
        m = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ErrorDeMapeo(f"No encuentro el archivo de mapeo: {Path(ruta).name}")
    except json.JSONDecodeError as e:
        raise ErrorDeMapeo(f"El archivo de mapeo no es un JSON válido (línea {e.lineno}).")
    out = {"formato": dict(FORMATO_POR_DEFECTO, **(m.get("formato") or {})), "columnas": {k: v for k, v in (m.get("columnas") or {}).items() if v},
           "valores": {}, "por_defecto": dict(m.get("por_defecto") or {}), "resultado": m.get("resultado") or {}}
    for ruta_campo, tabla in (m.get("valores") or {}).items():
        out["valores"][ruta_campo] = {normalizar(k): str(v) for k, v in (tabla or {}).items() if not str(v).startswith("UNA_DE")}
    for r in list(out["columnas"]) + list(out["por_defecto"]):
        if r not in POR_RUTA:
            raise ErrorDeMapeo(f"«{r}» no es un campo de la API. Revisa el nombre (las opciones están en la plantilla).")
    return out


def verificar(m: Dict, columnas_del_archivo: List[str]) -> Dict[str, List[str]]:
    """Errores (no se puede continuar) y avisos (se puede, pero el resultado será más pobre)."""
    errores, avisos = [], []
    existen = set(columnas_del_archivo)
    for ruta, col in m["columnas"].items():
        if col not in existen:
            errores.append(f"La columna «{col}» (para {ruta}) no está en el archivo.")
    for ruta in REQUERIDOS:
        if ruta not in m["columnas"] and ruta not in m["por_defecto"]:
            errores.append(f"Falta el campo obligatorio {ruta}: indica su columna o un valor por defecto.")
    if "red.ip" not in m["columnas"] and "red.tipo_ip" not in m["columnas"] and "red.tipo_ip" not in m["por_defecto"]:
        errores.append("Falta la red: indica la columna de la IP (red.ip) o la del tipo de IP (red.tipo_ip).")
    for ruta, val in m["por_defecto"].items():
        c = POR_RUTA[ruta]
        if c.tipo == "enum" and normalizar(val) not in {normalizar(o) for o in c.opciones} and normalizar(val) not in m["valores"].get(ruta, {}):
            errores.append(f"El valor por defecto de {ruta} no es válido. Opciones: {', '.join(c.opciones)}.")
        if c.requerido:
            avisos.append(f"{ruta} tiene un valor fijo para todo el archivo: eso le resta valor al diagnóstico.")
    sin = [c.ruta for c in CAMPOS if not c.requerido and c.ruta not in m["columnas"] and c.ruta not in m["por_defecto"]]
    if sin:
        avisos.append("Campos opcionales sin columna (mejorarían el resultado si el comercio los tiene): " + ", ".join(sin))
    if not m["resultado"].get("columna"):
        avisos.append("No hay columna de resultado (fraude o contracargo): se evaluará cada pago pero no se podrá medir cuánto fraude se detectó.")
    elif m["resultado"]["columna"] not in existen:
        errores.append(f"La columna de resultado «{m['resultado']['columna']}» no está en el archivo.")
    return {"errores": errores, "avisos": avisos}
