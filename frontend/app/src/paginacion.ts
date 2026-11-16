export const TAMANOS = [10, 25, 50] as const;
export const TAMANO_INICIAL = 25;

/** El backend rechaza (422) un desplazamiento mayor: más allá de esta fila se afina con filtros, no con páginas. */
export const MAX_DESPLAZAMIENTO = 100_000;

export function paginasDe(total: number, porPagina: number): number {
  const p = Math.max(1, porPagina);
  const alcanzables = Math.floor(MAX_DESPLAZAMIENTO / p) + 1;
  return Math.min(Math.max(1, Math.ceil(total / p)), alcanzables);
}

/** ¿Hay pagos que las páginas ya no alcanzan (hay que filtrar para verlos)? */
export function hayMasAllaDelTope(total: number, porPagina: number): boolean {
  return total > paginasDe(total, porPagina) * porPagina;
}

/** La última página que el servidor deja pedir con este tamaño (su tope de desplazamiento). */
export function paginaMaxima(porPagina: number): number {
  return Math.floor(MAX_DESPLAZAMIENTO / Math.max(1, porPagina)) + 1;
}

/** Una página pedida (texto de la dirección) llevada a un número válido SIN conocer el total todavía: nunca pide más allá del tope del servidor. */
export function limitarPagina(valor: unknown, porPagina: number): number {
  const n = Math.floor(Number(valor));
  return Math.min(Math.max(1, Number.isFinite(n) ? n : 1), paginaMaxima(porPagina));
}

/** Una página siempre válida: texto raro, cero, negativos o pasada del final (el total pudo bajar) caen en un límite, nunca en un error. */
export function ajustarPagina(valor: unknown, total: number, porPagina: number): number {
  const n = Math.floor(Number(valor));
  const pagina = Number.isFinite(n) ? n : 1;
  return Math.min(Math.max(1, pagina), paginasDe(total, porPagina));
}

export function tamanoValido(valor: unknown): number {
  const n = Number(valor);
  return (TAMANOS as readonly number[]).includes(n) ? n : TAMANO_INICIAL;
}

export function rango(pagina: number, total: number, porPagina: number): [number, number] {
  if (total === 0) return [0, 0];
  const desde = (pagina - 1) * porPagina + 1;
  return [desde, Math.min(total, pagina * porPagina)];
}
