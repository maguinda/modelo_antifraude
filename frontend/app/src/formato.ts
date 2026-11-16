const COP = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });

export const pesos = (v: number | null | undefined): string => COP.format(v ?? 0);

export function fecha(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("es-CO", { dateStyle: "short", timeStyle: "short" });
}

export const DECISIONES: Record<string, string> = { APROBADA: "Aprobada", EN_REVISION: "En revisión", RECHAZADA: "Rechazada" };
