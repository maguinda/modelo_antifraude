"""Genera los tipos de TypeScript del frontend a partir del contrato (`api/openapi.json`).

Uso:  python -m modelo_ia.api.exportar_tipos_ts

Escribe `frontend/app/src/red/api.generado.ts`. Ese archivo NO se edita a mano: si cambia la API, se regenera. Una prueba (`tests/test_tipos_ts.py`)
falla si el archivo publicado no coincide con el que sale del contrato.

Por qué un generador propio y no `openapi-typescript`: esa herramienta exige TypeScript 5 y el proyecto usa la 7 (forzar la instalación es arriesgado),
y el contrato usa pocas construcciones (objetos, listas, enums, `anyOf` con `null`, `$ref`, `additionalProperties`), así que son ~100 líneas sin dependencias
nuevas. Si una construcción no está prevista, el generador FALLA en vez de adivinar (`TypeError`).
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from . import exportar_openapi

RUTA = Path(__file__).resolve().parents[1] / "frontend" / "app" / "src" / "red" / "api.generado.ts"

_ENCABEZADO = """// ARCHIVO GENERADO: no lo edites a mano.
// Sale del contrato OpenAPI con el script `exportar_tipos_ts` (ver el README del frontend); una prueba del repo falla si no está al día.
// Un campo opcional (`?`) es uno que la API a veces no manda O que tiene valor por omisión (el contrato no distingue); `| null` es uno que puede venir vacío.
// Los pocos campos con valores fijos que la API declara como `string` (p. ej. `estado`) salen como `string`.

"""


def _nombre(esquema: str) -> str:
    """`Foo-Output` → `Foo_Output`: un nombre de tipo válido en TypeScript."""
    return re.sub(r"[^A-Za-z0-9_]", "_", esquema)


def _comentario(texto: str) -> str:
    """El texto cabe en un comentario `/** … */` (sin cerrarlo antes de tiempo)."""
    return texto.replace("*/", "*\\/")


def _literal(valor: Any) -> str:
    if isinstance(valor, (str, int, float, bool)):
        return json.dumps(valor, ensure_ascii=False)
    raise TypeError(f"valor de enum no previsto: {valor!r}")


def _tipo(s: Dict[str, Any], sangria: str) -> str:
    """El tipo de TypeScript de un esquema del contrato."""
    if "$ref" in s:
        return _nombre(s["$ref"].rsplit("/", 1)[-1])
    if "enum" in s:
        return " | ".join(_literal(v) for v in s["enum"])
    if "const" in s:
        return _literal(s["const"])
    if "anyOf" in s or "oneOf" in s:
        partes = [_tipo(p, sangria) for p in s.get("anyOf", s.get("oneOf", []))]
        unicas: List[str] = []
        for p in partes:
            if p not in unicas:
                unicas.append(p)
        return " | ".join(unicas)
    if "allOf" in s:
        return " & ".join(_tipo(p, sangria) for p in s["allOf"])
    t = s.get("type")
    if t == "string":
        return "string"
    if t in ("integer", "number"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        interior = _tipo(s["items"], sangria) if "items" in s else "unknown"
        return f"({interior})[]" if ("|" in interior or "&" in interior) else f"{interior}[]"
    if t == "object" or "properties" in s:
        return _objeto(s, sangria)
    if not (set(s) - {"title", "description", "default", "examples", "example"}):
        return "unknown"  # un campo `Any`: llega solo con título y descripción
    raise TypeError(f"construcción de esquema no prevista: {json.dumps(s)[:120]}")


def _objeto(s: Dict[str, Any], sangria: str) -> str:
    props: Dict[str, Any] = s.get("properties", {})
    extra = s.get("additionalProperties")
    if not props:
        if extra is True or extra is None:
            return "Record<string, unknown>"
        return f"Record<string, {_tipo(extra, sangria)}>" if isinstance(extra, dict) else "Record<string, never>"
    obligatorios = set(s.get("required", []))
    dentro = sangria + "  "
    lineas = []
    for nombre, p in sorted(props.items()):  # orden alfabético: la salida no depende del orden del código
        clave = nombre if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nombre) else json.dumps(nombre)
        lineas.append(f"{dentro}{clave}{'' if nombre in obligatorios else '?'}: {_tipo(p, dentro)};")
        if p.get("description"):
            lineas.insert(-1, f"{dentro}/** {_comentario(p['description'])} */")
    if isinstance(extra, dict):
        lineas.append(f"{dentro}[clave: string]: {_tipo(extra, dentro)};")
    return "{\n" + "\n".join(lineas) + f"\n{sangria}}}"


def generar(contrato: Dict[str, Any]) -> str:
    esquemas = contrato["components"]["schemas"]
    salida = [_ENCABEZADO]
    for nombre in sorted(esquemas):
        s = esquemas[nombre]
        cuerpo = _tipo(s, "")
        doc = f"/** {_comentario(s['description'].splitlines()[0])} */\n" if s.get("description") else ""
        if cuerpo.startswith("{"):
            salida.append(f"{doc}export interface {_nombre(nombre)} {cuerpo}\n")
        else:
            salida.append(f"{doc}export type {_nombre(nombre)} = {cuerpo};\n")
    return "\n".join(salida)


def main() -> None:
    RUTA.write_text(generar(exportar_openapi.generar()), encoding="utf-8")
    print("Tipos escritos en", RUTA)


if __name__ == "__main__":
    main()
