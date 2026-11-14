"""El SQL propio de SQLite vive solo en `repositorios/` y `migraciones.py` (la lista está en el README: «Lo propio de SQLite»).

Pasar a PostgreSQL es traducir esos archivos; esta prueba evita que se escape SQL nuevo a otro lado sin que nadie lo note.
"""
import re
from pathlib import Path

API = Path(__file__).resolve().parent.parent / "api"
PERMITIDOS = ("repositorios", "migraciones.py")
PROPIO_DE_SQLITE = re.compile(r"\browid\b|INSERT OR REPLACE|julianday|json_each|json_extract|\bsubstr\(|PRAGMA|AUTOINCREMENT|lastrowid|\.execute\(|sqlite3\.connect")


def sin_comentarios(texto):
    return "\n".join(re.sub(r"\s#.*$", "", linea) for linea in texto.splitlines())


def test_ningun_archivo_fuera_de_repositorios_y_migraciones_tiene_sql_de_sqlite():
    fuera = [p for p in API.rglob("*.py") if not any(parte in PERMITIDOS for parte in p.relative_to(API).parts)]
    assert fuera, "no encontré archivos de la API: ¿cambió la carpeta?"
    for p in fuera:
        hallado = PROPIO_DE_SQLITE.findall(sin_comentarios(p.read_text(encoding="utf-8")))
        assert not hallado, f"{p.relative_to(API)} tiene SQL de SQLite fuera de repositorios/: {sorted(set(hallado))}"


def test_la_lista_del_readme_menciona_cada_constructo_y_su_archivo():
    readme = (API.parent / "README.md").read_text(encoding="utf-8")
    seccion = readme.split("### Lo propio de SQLite", 1)[1].split("\n## ", 1)[0].split("\nPara agregar una ruta", 1)[0]
    for constructo in ("rowid", "INSERT OR REPLACE", "json_each", "julianday", "substr", "AUTOINCREMENT", "PRAGMA"):
        assert constructo in seccion, constructo
        archivos = [p for p in API.rglob("*.py") if constructo in sin_comentarios(p.read_text(encoding="utf-8"))]
        assert archivos, constructo
        for p in archivos:
            assert p.name in seccion, f"el README no dice que {constructo} está en {p.name}"
