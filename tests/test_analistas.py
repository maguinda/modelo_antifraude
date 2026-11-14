"""Claves por persona: nombres, rotación, último uso y la línea de comandos de administración."""
import re
import sqlite3
from datetime import datetime, timedelta

import pytest

from modelo_ia.api import admin
from modelo_ia.api.almacen import PLATAFORMA, Almacen, normalizar_nombre


@pytest.fixture
def alm(tmp_path):
    a = Almacen(tmp_path / "a.sqlite3", "p" * 40)
    a.inicializar()
    return a


def _fila(alm, clave_id):
    con = sqlite3.connect(alm.ruta); con.row_factory = sqlite3.Row
    return con.execute("SELECT * FROM claves WHERE id=?", (clave_id,)).fetchone()


# ------------------------------------------------------------------ nombres
def test_el_nombre_se_limpia_y_se_valida():
    assert normalizar_nombre("  Ana   María \t Pérez ") == "Ana María Pérez"
    for malo in ("", "  ", "ab", "x" * 61, "Ana <script>", "Ana\x00", "Ana\nPérez\x07", None):
        with pytest.raises(ValueError):
            normalizar_nombre(malo)


def test_crear_usuario_solo_para_personas_y_sin_repetir_nombres_activos(alm):
    i, k = alm.crear_usuario("analista", "Ana Pérez")
    assert k.startswith("mia_analista_") and alm.autenticar(k).usuario == "Ana Pérez" and alm.autenticar(k).rol == "analista"
    for repetido in ("Ana Pérez", "ana pérez", "  ANA   PÉREZ "):
        with pytest.raises(ValueError, match="Ya hay una clave activa"):
            alm.crear_usuario("analista", repetido)
    with pytest.raises(ValueError, match="Ya hay una clave activa"):
        alm.crear_usuario("admin", "Ana Pérez")                                                       # analistas y administradores comparten el espacio de nombres
    with pytest.raises(ValueError, match="personas con nombre"):
        alm.crear_usuario("comercio", "Tienda X")
    alm.desactivar_clave(i)
    assert alm.autenticar(alm.crear_usuario("analista", "Ana Pérez")[1]).usuario == "Ana Pérez"       # tras la baja, el nombre vuelve a estar disponible


def test_las_claves_de_comercio_no_llevan_nombre_de_persona(alm):
    cid = alm.crear_comercio("Tienda A")
    p = alm.autenticar(alm.crear_clave(cid, "comercio", "alta por API"))
    assert p.rol == "comercio" and p.usuario == "" and p.nombre == "Tienda A"
    assert alm.autenticar("mia_comercio_no_existe") is None


# ------------------------------------------------------------------ último uso
def test_el_ultimo_uso_se_anota_una_vez_por_hora(alm):
    i, k = alm.crear_usuario("analista", "Ana Pérez")
    assert _fila(alm, i)["ultimo_uso"] is None
    alm.autenticar(k)
    primero = _fila(alm, i)["ultimo_uso"]
    assert primero is not None
    alm.autenticar(k)
    assert _fila(alm, i)["ultimo_uso"] == primero                                                    # dentro de la hora: no se vuelve a escribir
    hace_2h = (datetime.now() - timedelta(hours=2)).isoformat(timespec="microseconds")
    con = sqlite3.connect(alm.ruta); con.execute("UPDATE claves SET ultimo_uso=? WHERE id=?", (hace_2h, i)); con.commit(); con.close()
    alm.autenticar(k)
    assert _fila(alm, i)["ultimo_uso"] > hace_2h


def test_si_no_se_puede_anotar_el_uso_la_autenticacion_no_falla(alm, monkeypatch):
    _, k = alm.crear_usuario("analista", "Ana Pérez")
    def bloqueada():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(alm, "transaccion", bloqueada)
    assert alm.autenticar(k).usuario == "Ana Pérez"


# ------------------------------------------------------------------ rotación y listado
def test_rotar_apaga_la_clave_vieja_y_crea_otra_de_la_misma_persona(alm):
    i, vieja = alm.crear_usuario("analista", "Ana Pérez")
    n, nueva = alm.rotar_clave(i)
    assert n != i and alm.autenticar(vieja) is None and alm.autenticar(nueva).usuario == "Ana Pérez"
    assert _fila(alm, i)["activa"] == 0 and _fila(alm, n)["activa"] == 1
    with pytest.raises(ValueError, match="No existe una clave activa"):
        alm.rotar_clave(i)                                                                            # la vieja ya no se puede rotar otra vez
    with pytest.raises(ValueError):
        alm.rotar_clave(9999)
    acciones = [r[0] for r in sqlite3.connect(alm.ruta).execute("SELECT accion FROM auditoria ORDER BY id")]
    assert acciones.count("clave_creada") == 2 and "clave_desactivada" in acciones                    # todo queda en la auditoría


def test_listar_claves_nunca_revela_claves_ni_hashes(alm):
    _, k = alm.crear_usuario("analista", "Ana Pérez")
    alm.crear_clave(alm.crear_comercio("Tienda A"), "comercio", "alta por API")
    filas = alm.listar_claves()
    assert {f["rol"] for f in filas} == {"analista", "comercio"} and filas[0]["activa"] == 1
    texto = repr(filas)
    assert k not in texto and "hash" not in texto and not re.search(r"[0-9a-f]{64}", texto)


def test_una_base_vieja_sin_las_columnas_nuevas_se_actualiza(tmp_path):
    ruta = tmp_path / "vieja.sqlite3"
    Almacen(ruta, "p" * 40).inicializar()
    con = sqlite3.connect(ruta)
    con.execute("ALTER TABLE claves DROP COLUMN ultimo_uso"); con.execute("ALTER TABLE claves DROP COLUMN creada_por"); con.commit(); con.close()
    a = Almacen(ruta, "p" * 40); a.inicializar()
    assert {"ultimo_uso", "creada_por"} <= {r[1] for r in sqlite3.connect(ruta).execute("PRAGMA table_info(claves)")}
    assert a.autenticar(a.crear_usuario("analista", "Ana Pérez")[1]).usuario == "Ana Pérez"


# ------------------------------------------------------------------ línea de comandos
@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MODELO_IA_BD", str(tmp_path / "c.sqlite3")); monkeypatch.setenv("MODELO_IA_PEPPER", "q" * 40)

    def correr(*argv):
        try:
            admin.main(list(argv)); codigo = 0
        except SystemExit as e:
            codigo = e.code if isinstance(e.code, int) else 2 if e.code else 0
            mensaje = e.code if isinstance(e.code, str) else ""                                       # «raise SystemExit("ERROR: ...")» lo imprime el intérprete
        else:
            mensaje = ""
        s = capsys.readouterr()
        return codigo, s.out, s.err + mensaje
    return correr


def test_la_cli_exige_nombre_para_personas_y_muestra_la_clave_una_vez(cli):
    codigo, _, err = cli("crear-clave", "--rol", "analista")
    assert codigo == 2 and "--nombre" in err
    codigo, out, _ = cli("crear-clave", "--rol", "analista", "--nombre", "Ana Pérez")
    assert codigo == 0 and "Ana Pérez" in out and re.search(r"^mia_analista_\S+$", out, re.M)
    codigo, _, err = cli("crear-clave", "--rol", "admin", "--nombre", "ana pérez")
    assert codigo != 0 and "Ya hay una clave activa" in err
    assert cli("crear-comercio", "--nombre", "Tienda A")[0] == 0
    assert cli("crear-clave", "--rol", "comercio", "--comercio-id", "2")[0] == 0                      # los comercios no necesitan nombre de persona


def test_la_cli_lista_sin_mostrar_claves_y_avisa_de_las_que_no_tienen_nombre(cli, tmp_path):
    cli("crear-clave", "--rol", "analista", "--nombre", "Ana Pérez")
    Almacen(tmp_path / "c.sqlite3", "q" * 40).crear_clave(PLATAFORMA, "analista", "")                   # una clave vieja, compartida y sin nombre
    codigo, out, _ = cli("listar-claves")
    assert codigo == 0 and "Ana Pérez" in out and "mia_" not in out and "nunca" in out
    assert "sin nombre" in out and "rotar-clave" in out


def test_la_cli_rota_y_desactiva(cli, tmp_path):
    _, out, _ = cli("crear-clave", "--rol", "analista", "--nombre", "Ana Pérez")
    vieja = re.search(r"^(mia_analista_\S+)$", out, re.M).group(1)
    codigo, out, _ = cli("rotar-clave", "--id", "1")
    nueva = re.search(r"^(mia_analista_\S+)$", out, re.M).group(1)
    a = Almacen(tmp_path / "c.sqlite3", "q" * 40)
    assert codigo == 0 and nueva != vieja and a.autenticar(vieja) is None and a.autenticar(nueva).usuario == "Ana Pérez"
    assert cli("desactivar-clave", "--id", "2")[0] == 0 and a.autenticar(nueva) is None
    assert cli("rotar-clave", "--id", "999")[0] != 0
