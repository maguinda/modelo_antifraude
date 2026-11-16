import { useCallback, useEffect, useRef, useState } from "react";

export interface EstadoConsulta<T> {
  /** Último dato bueno. Se CONSERVA mientras llega el siguiente (la pantalla no se vacía ni salta). */
  datos: T | null;
  /** Hay una petición en curso (con o sin datos anteriores a la vista). */
  cargando: boolean;
  error: Error | null;
  recargar: () => void;
}

/**
 * Ejecuta `cargador` cada vez que cambia `clave`. Garantías:
 *  - una respuesta vieja nunca pisa a una nueva (se cancela la anterior y se ignora si llega tarde);
 *  - si falla, los datos anteriores siguen a la vista y el error se informa aparte;
 *  - al desmontar se cancela todo (sin avisos ni estados sobre un componente muerto).
 */
export function useConsulta<T>(clave: string | null, cargador: (senal: AbortSignal) => Promise<T>): EstadoConsulta<T> {
  const [datos, setDatos] = useState<T | null>(null);
  const [cargando, setCargando] = useState(clave !== null);
  const [error, setError] = useState<Error | null>(null);
  const [recarga, setRecarga] = useState(0);
  const ultimoCargador = useRef(cargador);
  useEffect(() => {
    ultimoCargador.current = cargador; // se declara antes del efecto de carga: ese ya ve el cargador de este render
  });

  useEffect(() => {
    if (clave === null) {
      setDatos(null);
      setCargando(false);
      return;
    }
    const control = new AbortController();
    setCargando(true);
    setError(null);
    ultimoCargador
      .current(control.signal)
      .then((r) => {
        if (control.signal.aborted) return;
        setDatos(r);
        setCargando(false);
      })
      .catch((e: unknown) => {
        if (control.signal.aborted) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setCargando(false);
      });
    return () => control.abort();
  }, [clave, recarga]);

  const recargar = useCallback(() => setRecarga((n) => n + 1), []);
  return { datos, cargando, error, recargar };
}
