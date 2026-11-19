import type { Parametros } from "./red/cliente";

// Los filtros de la pantalla Resumen. Viven en la dirección (`?vista=resumen&comercio_id=2&desde=2026-11-01`): se pueden copiar y «atrás» funciona.
// Lo que llegue de la dirección se SANEA aquí: un valor raro se descarta y nunca llega al servidor.

export interface FiltrosResumen {
  comercio: string;
  desde: string;
  hasta: string;
}

export const FILTROS_RESUMEN_INICIALES: FiltrosResumen = { comercio: "", desde: "", hasta: "" };

const FECHA = /^\d{4}-\d{2}-\d{2}$/;

export function leerFiltrosResumen(p: URLSearchParams): FiltrosResumen {
  const comercio = p.get("comercio_id") ?? "";
  const desde = p.get("desde") ?? "";
  const hasta = p.get("hasta") ?? "";
  return {
    comercio: /^\d{1,9}$/.test(comercio) ? comercio : "",
    desde: FECHA.test(desde) ? desde : "",
    hasta: FECHA.test(hasta) ? hasta : "",
  };
}

/** El mismo criterio que el servidor: «Desde» no puede ser posterior a «Hasta». Devuelve el mensaje, o "" si está bien. */
export function validarFechas(f: Pick<FiltrosResumen, "desde" | "hasta">): string {
  return f.desde && f.hasta && f.desde > f.hasta ? "«Desde» no puede ser posterior a «Hasta»." : "";
}

/** Lo que se le pide al servidor. Un rango al revés (por un enlace viejo) se ignora en vez de provocar un error. */
export function aParametrosResumen(f: FiltrosResumen): Parametros {
  const p: Parametros = {};
  if (f.comercio) p.comercio_id = f.comercio;
  if (!validarFechas(f)) {
    if (f.desde) p.desde = f.desde;
    if (f.hasta) p.hasta = f.hasta;
  }
  return p;
}

/** Para la dirección: solo lo que se puso (null borra el parámetro). */
export function filtrosResumenAUrl(f: FiltrosResumen): Record<string, string | null> {
  return { comercio_id: f.comercio || null, desde: f.desde || null, hasta: f.hasta || null };
}

export const hayFiltrosResumen = (f: FiltrosResumen): boolean => Boolean(f.comercio || f.desde || f.hasta);

// ---- números y etiquetas

/** «62 %», o «—» si no hay nada que dividir (un periodo sin pagos no debe mostrar «NaN %»). */
export function porcentaje(parte: number, total: number): string {
  return total ? `${Math.round((100 * parte) / total)} %` : "—";
}

/** Un decimal y su unidad, o «—» si el dato no existe. */
export function conUnidad(v: number | null | undefined, unidad: string): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(1)} ${unidad}`;
}

/** El valor más alto, y nunca menos de 1 (para dividir sin miedo cuando todo vale 0). */
export function maximo(valores: number[]): number {
  return Math.max(1, ...valores);
}

/** «2026-11-05» → «05/11» (el eje de la gráfica). */
export function etiquetaDia(dia: string): string {
  return /^\d{4}-\d{2}-\d{2}/.test(dia) ? `${dia.slice(8, 10)}/${dia.slice(5, 7)}` : dia;
}

/** Cuáles de `n` días llevan etiqueta en el eje: todas si caben, y si no, una de cada tantas (siempre la primera). */
export function indicesConEtiqueta(n: number, maxEtiquetas: number): Set<number> {
  if (n <= maxEtiquetas) return new Set(Array.from({ length: n }, (_, i) => i));
  const salto = Math.ceil(n / maxEtiquetas);
  return new Set(Array.from({ length: n }, (_, i) => i).filter((i) => i % salto === 0));
}

/** El ancho en que se dibujan las gráficas: en pantallas angostas más estrecho, para que su texto no se encoja. */
export function anchoGrafica(normal: number, anchoVentana: number): number {
  return anchoVentana < 520 ? 300 : normal;
}
