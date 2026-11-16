import { useCallback, useEffect, useState } from "react";

/** Los parámetros de la dirección (`?pagina=2&limite=25`) como estado: se puede copiar el enlace y el botón «atrás» funciona. */
export type Cambiar = (cambios: Record<string, string | null>, opciones?: { reemplazar?: boolean }) => void;

export function useParametrosUrl(): [URLSearchParams, Cambiar] {
  const [params, setParams] = useState(() => new URLSearchParams(window.location.search));

  useEffect(() => {
    const alVolver = () => setParams(new URLSearchParams(window.location.search));
    window.addEventListener("popstate", alVolver);
    return () => window.removeEventListener("popstate", alVolver);
  }, []);

  const cambiar = useCallback<Cambiar>((cambios, opciones) => {
    const nuevos = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(cambios)) {
      if (v === null || v === "") nuevos.delete(k);
      else nuevos.set(k, v);
    }
    const texto = nuevos.toString();
    const destino = window.location.pathname + (texto ? `?${texto}` : "");
    // Una corrección automática REEMPLAZA la entrada: si empujara otra, «atrás» volvería a la dirección mala y quedaría atrapado.
    if (opciones?.reemplazar) window.history.replaceState(null, "", destino);
    else window.history.pushState(null, "", destino);
    setParams(nuevos);
  }, []);

  return [params, cambiar];
}
