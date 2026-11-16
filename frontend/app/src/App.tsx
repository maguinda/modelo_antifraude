import { useCallback, useMemo, useState } from "react";
import { crearCliente } from "./red/cliente";
import { Acceso } from "./pantallas/Acceso";
import { Transacciones } from "./pantallas/Transacciones";

export function App() {
  const [clave, setClave] = useState<string | null>(null);
  const [mensaje, setMensaje] = useState<string | undefined>();
  const cliente = useMemo(() => (clave ? crearCliente(clave) : null), [clave]);
  const alExpirar = useCallback(() => {
    setClave(null);
    setMensaje("Tu clave ya no sirve. Entra de nuevo.");
  }, []);

  return (
    <>
      <header className="barra">
        <a className="marca" href="/dashboard#/resumen" aria-label="Antifraude, ir al panel de control">
          <span className="marca-punto" aria-hidden="true" />
          antifraude
        </a>
        <div className="barra-acciones">
          {cliente ? (
            <button type="button" className="btn suave" onClick={() => setClave(null)}>
              Salir
            </button>
          ) : null}
        </div>
      </header>
      <main className="contenido">
        {cliente ? (
          <Transacciones cliente={cliente} alExpirar={alExpirar} />
        ) : (
          <Acceso
            mensaje={mensaje}
            alEntrar={(c) => {
              setMensaje(undefined);
              setClave(c);
            }}
          />
        )}
      </main>
    </>
  );
}
