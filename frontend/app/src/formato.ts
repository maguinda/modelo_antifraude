const COP = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });

export const pesos = (v: number | null | undefined): string => COP.format(v ?? 0);

export function fecha(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("es-CO", { dateStyle: "short", timeStyle: "short" });
}

export const DECISIONES: Record<string, string> = { APROBADA: "Aprobada", EN_REVISION: "En revisión", RECHAZADA: "Rechazada" };

const COP_COMPACTO = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", notation: "compact", maximumFractionDigits: 1 });
const NUM = new Intl.NumberFormat("es-CO");
export const pesosCompactos = (v: number | null | undefined): string => COP_COMPACTO.format(v ?? 0);
export const numero = (v: number): string => NUM.format(v);

/** «hace 5 min», «hace 2 h 10 min», «hace 3 d». `ahora` se puede fijar (pruebas). */
export function hace(iso: string, ahora: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const min = Math.max(0, Math.round((ahora - t) / 60000));
  if (min < 1) return "hace menos de 1 min";
  if (min < 60) return `hace ${min} min`;
  if (min < 1440) return `hace ${Math.floor(min / 60)} h ${min % 60} min`;
  return `hace ${Math.floor(min / 1440)} d`;
}

export const ETIQUETAS_DATO: Record<string, string> = {
  fecha: "Fecha del pago",
  id_cliente: "Cliente",
  monto_cop: "Monto",
  metodo_pago: "Método de pago",
  canal_pago: "Canal",
  categoria: "Categoría",
  localizacion: "Ciudad",
  dispositivo: "Dispositivo",
  estado_dispositivo: "Estado del dispositivo",
  tipo_ip: "Tipo de IP",
  ip_pais: "País de la IP",
  kyc_nivel: "Verificación (KYC)",
  edad_cuenta_dias: "Antigüedad de la cuenta (días)",
  autenticacion_2fa: "2FA",
  transacciones_ultimas_24h: "Compras en 24 h",
  es_emulador: "Emulador",
};

/** El valor de un dato del pago, en texto. Todo lo que viene de la API se muestra como TEXTO (React lo escapa). */
export function valorDato(clave: string, v: unknown): string {
  if (clave === "monto_cop") return pesos(typeof v === "number" ? v : Number(v));
  if (clave === "fecha") {
    const f = typeof v === "string" ? new Date(v) : null;
    return f && !Number.isNaN(f.getTime())
      ? f.toLocaleString("es-CO", { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" })
      : String(v);
  }
  if (typeof v === "boolean" || clave === "es_emulador") return v ? "Sí" : "No";
  return String(v);
}

export function nivelRiesgo(puntaje: number | null | undefined): "" | "alto" | "medio" | "bajo" {
  if (puntaje === null || puntaje === undefined) return "";
  const pct = Math.round(puntaje * 100);
  return pct >= 60 ? "alto" : pct >= 35 ? "medio" : "bajo";
}
