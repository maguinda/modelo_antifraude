"""Los tipos de TypeScript del frontend salen del contrato y están al día.

`frontend/app/src/red/api.generado.ts` se genera con `python -m modelo_ia.api.exportar_tipos_ts`. Si cambia la API (o sus modelos) y no se regenera, esta prueba falla.
"""

import pytest

from modelo_ia.api import exportar_openapi, exportar_tipos_ts as ts


def test_los_tipos_publicados_coinciden_con_el_contrato():
    publicado = ts.RUTA.read_text(encoding="utf-8")
    assert publicado == ts.generar(exportar_openapi.generar()), (
        "api.generado.ts está desactualizado: corre `python -m modelo_ia.api.exportar_tipos_ts`"
    )


def test_el_contrato_publicado_y_el_generado_dan_los_mismos_tipos():
    import json

    publicado = json.loads(exportar_openapi.RUTA.read_text(encoding="utf-8"))
    assert ts.generar(publicado) == ts.generar(exportar_openapi.generar())


def test_un_campo_obligatorio_no_es_opcional_y_uno_con_valor_por_omision_si():
    salida = ts.generar(
        {
            "components": {
                "schemas": {
                    "Ej": {
                        "type": "object",
                        "required": ["a", "b"],
                        "properties": {
                            "a": {"type": "string"},
                            "b": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                            "c": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "number"}]}},
                            "d": {"type": "string", "enum": ["X", "Y"]},
                            "e": {"title": "Cualquiera"},
                        },
                    }
                }
            }
        }
    )
    assert "a: string;" in salida and "b: number | null;" in salida  # obligatorios: sin «?»
    assert "c?: (string | number)[];" in salida and 'd?: "X" | "Y";' in salida and "e?: unknown;" in salida


def test_una_construccion_no_prevista_hace_fallar_el_generador_en_vez_de_adivinar():
    with pytest.raises(TypeError):
        ts.generar({"components": {"schemas": {"Raro": {"type": "tuple", "prefixItems": []}}}})


def test_las_rutas_del_panel_tienen_su_tipo_en_el_archivo_generado():
    texto = ts.RUTA.read_text(encoding="utf-8")
    for nombre in (
        "PaginaTransaccionesPanel",
        "RespuestaResumenPanel",
        "RespuestaVinculosPanel",
        "RespuestaDetallePanel",
        "RespuestaDeriva",
        "ComercioPanel",
        "ModeloRegistrado",
    ):
        assert f"export interface {nombre} " in texto, nombre
