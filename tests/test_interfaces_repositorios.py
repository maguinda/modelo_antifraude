"""Las interfaces de los repositorios (api/repositorios/interfaces.py) y quien las usa: Python no las exige al ejecutar, así que estas pruebas lo hacen."""
import ast
import inspect
from pathlib import Path

import pytest

from modelo_ia.api import servicio as modulo_servicio
from modelo_ia.api.almacen import Almacen
from modelo_ia.api.repositorios import interfaces as ifaz
from modelo_ia.api.repositorios.evaluaciones import EvaluacionesRepositorio
from modelo_ia.api.repositorios.lista_negra import ListaNegraRepositorio
from modelo_ia.api.repositorios.resultados import ResultadosRepositorio
from modelo_ia.api.repositorios.revisiones import RevisionesRepositorio
from modelo_ia.tests.test_servicio_sin_base import FalsaListaNegra, FalsasEvaluaciones, FalsasRevisiones, FalsosResultados

PARES = [("evaluaciones", ifaz.EvaluacionesRepo, EvaluacionesRepositorio, FalsasEvaluaciones), ("revisiones", ifaz.RevisionesRepo, RevisionesRepositorio, FalsasRevisiones),
         ("resultados", ifaz.ResultadosRepo, ResultadosRepositorio, FalsosResultados), ("lista_negra", ifaz.ListaNegraRepo, ListaNegraRepositorio, FalsaListaNegra)]


def metodos(clase):
    return {n: f for n, f in inspect.getmembers(clase, inspect.isfunction) if not n.startswith("_")}


def parametros(f):
    """Nombres, tipo de parámetro y valores por defecto (sin `self` ni los tipos): lo que de verdad hay que cumplir al llamar."""
    return [(p.name, p.kind.name, repr(p.default)) for n, p in inspect.signature(f).parameters.items() if n != "self"]


@pytest.mark.parametrize("nombre,interfaz,real,falsa", PARES)
def test_cada_implementacion_cumple_su_interfaz_y_no_trae_metodos_publicos_de_mas(nombre, interfaz, real, falsa):
    declarados = metodos(interfaz)
    implementados = metodos(real)
    assert set(declarados) == set(implementados), f"{nombre}: la interfaz y la implementación no tienen los mismos métodos ({set(declarados) ^ set(implementados)})"
    for m, f in declarados.items():
        assert parametros(f) == parametros(implementados[m]), f"{nombre}.{m}: los parámetros no coinciden con la interfaz"


@pytest.mark.parametrize("nombre,interfaz,real,falsa", PARES)
def test_los_repositorios_falsos_de_las_pruebas_cumplen_la_misma_interfaz(nombre, interfaz, real, falsa):
    """Si los falsos se desviaran de la interfaz, las pruebas sin base de datos probarían algo que ya no es cierto."""
    declarados = metodos(interfaz)
    falsos = metodos(falsa)
    assert set(declarados) <= set(falsos), f"{nombre}: al falso le faltan {set(declarados) - set(falsos)}"
    for m, f in declarados.items():
        if m == "insertar":                                           # el falso recibe los valores como **kwargs
            assert [p[0] for p in parametros(falsos[m])][:1] == ["con"]
            continue
        assert parametros(f) == parametros(falsos[m]), f"{nombre}.{m}: el falso no coincide con la interfaz"


def test_la_fachada_entrega_las_implementaciones_de_sqlite(tmp_path):
    a = Almacen(tmp_path / "i.sqlite3", "p" * 40)
    for nombre, _, real, _ in PARES:
        assert isinstance(getattr(a, nombre), real), nombre


def test_el_servicio_no_tiene_sql_y_solo_llama_metodos_declarados_en_las_interfaces():
    fuente = inspect.getsource(modulo_servicio)
    for palabra in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE ", "PRAGMA "):
        assert palabra not in fuente, f"servicio.py no debe tener SQL: encontré «{palabra.strip()}»"
    declarados = {nombre: set(metodos(interfaz)) for nombre, interfaz, _, _ in PARES}
    usados = set()
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.Attribute) and isinstance(nodo.value, ast.Attribute) and nodo.value.attr in declarados:
            base = nodo.value.value
            if isinstance(base, ast.Attribute) and base.attr == "alm" and isinstance(base.value, ast.Name) and base.value.id == "self":
                usados.add((nodo.value.attr, nodo.attr))
    assert usados, "no encontré ninguna llamada a los repositorios: ¿cambió la forma de llamarlos?"
    for repo, metodo in sorted(usados):
        assert metodo in declarados[repo], f"servicio.py llama {repo}.{metodo}, que la interfaz no declara: los falsos no lo tendrían"
    assert {r for r, _ in usados} == set(declarados)                    # y usa los cuatro


def test_ningun_archivo_del_servicio_importa_sqlite_para_hablar_con_la_base_salvo_para_sus_errores():
    """sqlite3 puede seguir en servicio.py solo para los tipos y las excepciones (cambiar eso es parte de pasar a otra base); nunca para abrir ni consultar."""
    for nodo in ast.walk(ast.parse(Path(inspect.getsourcefile(modulo_servicio)).read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.Attribute) and isinstance(nodo.value, ast.Name) and nodo.value.id == "sqlite3":
            assert nodo.attr in ("Connection", "Row", "IntegrityError", "OperationalError"), f"servicio.py usa sqlite3.{nodo.attr}"
