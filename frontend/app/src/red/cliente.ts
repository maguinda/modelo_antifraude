// Cliente de la API: solo lo que necesita el panel. La clave vive en memoria (nunca en el almacenamiento del navegador).

export class ErrorApi extends Error {
  constructor(
    public readonly estado: number,
    mensaje: string,
    /** Se acabó el tiempo de espera: no se reintenta (un servidor que no contesta en 20 s no mejora a los 20 s siguientes). */
    public readonly agotado = false,
  ) {
    super(mensaje);
    this.name = "ErrorApi";
  }
  /** ¿Tiene sentido reintentar? Cortes del servidor o de la red, no errores del usuario. */
  get transitorio(): boolean {
    return (this.estado === 0 && !this.agotado) || this.estado === 502 || this.estado === 503 || this.estado === 504;
  }
}

export type Parametros = Record<string, string | number | null | undefined>;

export function consulta(parametros: Parametros = {}): string {
  const q = Object.entries(parametros)
    .filter(([, v]) => v !== "" && v !== null && v !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");
  return q ? `?${q}` : "";
}

export interface OpcionesCliente {
  /** Milisegundos antes de dar por perdida una petición. */
  tiempoMax?: number;
  /** Esperas entre reintentos de un corte transitorio (solo GET). */
  esperas?: readonly number[];
  fetcher?: typeof fetch;
}

const ESPERAS = [500, 1500] as const;

function dormir(ms: number, senal?: AbortSignal): Promise<void> {
  return new Promise((resolver, rechazar) => {
    if (senal?.aborted) return rechazar(new DOMException("Cancelada", "AbortError"));
    const alCancelar = () => {
      clearTimeout(t);
      rechazar(new DOMException("Cancelada", "AbortError"));
    };
    const t = setTimeout(() => {
      senal?.removeEventListener("abort", alCancelar);
      resolver();
    }, ms);
    senal?.addEventListener("abort", alCancelar, { once: true });
  });
}

export function crearCliente(clave: string, opciones: OpcionesCliente = {}) {
  const { tiempoMax = 20000, esperas = ESPERAS, fetcher = (...a) => fetch(...a) } = opciones;

  async function una(ruta: string, senal?: AbortSignal): Promise<unknown> {
    const control = new AbortController();
    const cancelar = () => control.abort();
    senal?.addEventListener("abort", cancelar, { once: true });
    if (senal?.aborted) control.abort();
    const reloj = setTimeout(() => control.abort(), tiempoMax);
    try {
      const r = await fetcher(ruta, { method: "GET", headers: { "X-API-Key": clave }, credentials: "omit", cache: "no-store", signal: control.signal });
      const texto = await r.text();
      let cuerpo: unknown = null;
      try {
        cuerpo = texto ? JSON.parse(texto) : null;
      } catch {
        cuerpo = null;
      }
      if (!r.ok) {
        const c = cuerpo as { detail?: unknown; title?: unknown } | null;
        const detalle = typeof c?.detail === "string" ? c.detail : typeof c?.title === "string" ? c.title : "";
        throw new ErrorApi(r.status, detalle || `Error ${r.status}`);
      }
      return cuerpo;
    } catch (e) {
      if (e instanceof ErrorApi) throw e;
      if (senal?.aborted) throw new DOMException("Cancelada", "AbortError"); // la pidió quien llamó
      if (control.signal.aborted) throw new ErrorApi(0, "La respuesta tardó demasiado.", true);
      throw new ErrorApi(0, "No se pudo conectar con el servidor.");
    } finally {
      clearTimeout(reloj);
      senal?.removeEventListener("abort", cancelar);
    }
  }

  /** GET con reintentos SOLO ante cortes transitorios (502/503/504, red, tiempo). Nunca reintenta un error de usuario (401, 403, 404, 422). */
  async function obtener<T>(ruta: string, parametros?: Parametros, senal?: AbortSignal): Promise<T> {
    // Solo rutas del mismo origen: la clave nunca debe poder ir a otro servidor. Se valida ANTES del bucle: no es un corte, no se reintenta.
    if (!ruta.startsWith("/") || ruta.startsWith("//")) throw new ErrorApi(0, "Ruta no permitida.", true);
    const url = ruta + consulta(parametros);
    for (let intento = 0; ; intento++) {
      try {
        return (await una(url, senal)) as T;
      } catch (e) {
        const espera = esperas[intento];
        if (!(e instanceof ErrorApi) || !e.transitorio || espera === undefined) throw e;
        await dormir(espera, senal);
      }
    }
  }

  return { obtener };
}

export type Cliente = ReturnType<typeof crearCliente>;
