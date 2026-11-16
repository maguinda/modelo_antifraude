import { useCallback, useEffect, useState } from "react";

/** Los parámetros de la dirección (`?pagina=2&limite=25`) como estado: se puede copiar el enlace y el botón «atrás» funciona. */
export function useParametrosUrl(): [URLSearchParams, (cambios: Record<string, string | null>) => void] {
  const [params, setParams] = useState(() => new URLSearchParams(window.location.search));

  useEffect(() => {
    const alVolver = () => setParams(new URLSearchParams(window.location.search));
    window.addEventListener("popstate", alVolver);
    return () => window.removeEventListener("popstate", alVolver);
  }, []);

  const cambiar = useCallback((cambios: Record<string, string | null>) => {
    const nuevos = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(cambios)) {
      if (v === null || v === "") nuevos.delete(k);
      else nuevos.set(k, v);
    }
    const texto = nuevos.toString();
    window.history.pushState(null, "", window.location.pathname + (texto ? `?${texto}` : ""));
    setParams(nuevos);
  }, []);

  return [params, cambiar];
}
