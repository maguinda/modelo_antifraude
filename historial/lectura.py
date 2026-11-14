"""Lectura de archivos de pagos: CSV (cualquier separador común) y Excel (.xlsx)."""
import csv
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple


class ErrorDeArchivo(Exception):
    pass


def _separador(muestra: str, pedido: str) -> str:
    if pedido and pedido != "auto":
        return {"tab": "\t", "\\t": "\t"}.get(pedido, pedido)
    try:
        return csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def encabezados(ruta: Path, formato: Optional[Dict] = None) -> List[str]:
    for n, fila in leer(ruta, formato or {}, solo_encabezado=True):
        return list(fila)
    return []


def leer(ruta: Path, formato: Dict, solo_encabezado: bool = False) -> Iterator[Tuple[int, Dict[str, str]]]:
    """Entrega (número de fila en el archivo, {columna: texto}). El número de fila cuenta el encabezado como fila 1."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ErrorDeArchivo(f"No encuentro el archivo: {ruta.name}")
    if ruta.suffix.lower() in (".xlsx", ".xlsm"):
        yield from _leer_excel(ruta, formato, solo_encabezado)
        return
    if ruta.suffix.lower() in (".xls", ".numbers", ".ods"):
        raise ErrorDeArchivo("Ese formato no se lee. Guarda el archivo como CSV o como .xlsx.")
    codificacion = formato.get("codificacion", "utf-8-sig")
    try:
        with open(ruta, "r", encoding=codificacion, newline="") as f:
            muestra = f.read(8192); f.seek(0)
            lector = csv.DictReader(f, delimiter=_separador(muestra, formato.get("separador", "auto")))
            if not lector.fieldnames:
                raise ErrorDeArchivo("El archivo está vacío o no tiene encabezados.")
            if solo_encabezado:
                yield 1, {c: "" for c in lector.fieldnames}
                return
            for i, fila in enumerate(lector, start=2):
                yield i, {k: ("" if v is None else str(v)) for k, v in fila.items() if k is not None}
    except UnicodeDecodeError:
        raise ErrorDeArchivo(f"El archivo no está en la codificación «{codificacion}». Prueba con \"codificacion\": \"latin-1\" en el mapeo.")


def _leer_excel(ruta: Path, formato: Dict, solo_encabezado: bool):
    try:
        import openpyxl
    except ImportError:
        raise ErrorDeArchivo("Para leer Excel hace falta instalar openpyxl:  pip install openpyxl  (o guarda el archivo como CSV).")
    libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    hoja = libro[formato["hoja"]] if formato.get("hoja") else libro.worksheets[0]
    filas = hoja.iter_rows(values_only=True)
    cab = next(filas, None)
    if not cab or not any(cab):
        raise ErrorDeArchivo("La hoja está vacía o no tiene encabezados.")
    cab = [str(c) if c is not None else "" for c in cab]
    if solo_encabezado:
        yield 1, {c: "" for c in cab}
        return
    for i, fila in enumerate(filas, start=2):
        if fila is None or not any(v is not None and str(v).strip() for v in fila):
            continue
        yield i, {cab[j]: ("" if v is None else (v.isoformat(sep=" ") if hasattr(v, "isoformat") else str(v))) for j, v in enumerate(fila) if j < len(cab) and cab[j]}
