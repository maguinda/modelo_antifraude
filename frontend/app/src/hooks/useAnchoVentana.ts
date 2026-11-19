import { useEffect, useState } from "react";

/** El ancho de la ventana, al día cuando se gira el celular o se cambia el tamaño. */
export function useAnchoVentana(): number {
  const [ancho, setAncho] = useState(() => window.innerWidth);
  useEffect(() => {
    const alCambiar = () => setAncho(window.innerWidth);
    window.addEventListener("resize", alCambiar);
    return () => window.removeEventListener("resize", alCambiar);
  }, []);
  return ancho;
}
