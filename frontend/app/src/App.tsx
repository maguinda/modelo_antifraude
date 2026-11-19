import { useCallback, useEffect, useMemo, useState } from "react";
import { useParametrosUrl } from "./hooks/useParametrosUrl";
import { ErrorApi, crearCliente } from "./red/cliente";
import type { Yo } from "./red/tipos";
import { Acceso } from "./pantallas/Acceso";
import { Resumen } from "./pantallas/Resumen";
import { Revision } from "./pantallas/Revision";
import { Transacciones } from "./pantallas/Transacciones";

type Vista = "resumen" | "transacciones" | "revision";

// Cada pantalla tiene sus propios parámetros en la dirección: al cambiar de pantalla se limpian todos (un «orden» de una no vale en la otra).
const PARAMETROS_DE_PANTALLA = ["pagina", "limite", "orden", "estado", "q", "comercio_id", "monto_min", "desde", "hasta", "riesgo"];

function mensajeDeEntrada(e: unknown): string {
  if (e instanceof ErrorApi && e.estado === 401) return "Esa clave no es válida.";
  if (e instanceof ErrorApi && e.estado === 403) return "Esa clave no es de analista.";
  return `No se pudo entrar: ${e instanceof Error ? e.message : "error desconocido"}`;
}

export function App() {
  const [clave, setClave] = useState<string | null>(null);
  const [mensaje, setMensaje] = useState<string | undefined>();
  const [entrando, setEntrando] = useState(false);
  const [usuario, setUsuario] = useState<string | null>(null);
  const [params, cambiar] = useParametrosUrl();
  const vista: Vista = params.get("vista") === "revision" ? "revision" : params.get("vista") === "resumen" ? "resumen" : "transacciones";
  const cliente = useMemo(() => (clave ? crearCliente(clave) : null), [clave]);

  const alExpirar = useCallback(() => {
    setClave(null);
    setUsuario(null);
    setMensaje("Tu clave ya no sirve. Entra de nuevo.");
  }, []);

  const entrar = async (c: string) => {
    setEntrando(true);
    setMensaje(undefined);
    try {
      // La misma comprobación que el panel de revisión actual: una clave que no sirve se dice aquí, no a medio pantalla.
      await crearCliente(c).obtener("/v1/revisiones", { estado: "pendiente", limite: 1 });
      setClave(c);
    } catch (e) {
      setMensaje(mensajeDeEntrada(e));
    } finally {
      setEntrando(false);
    }
  };

  useEffect(() => {
    if (!cliente) return;
    const control = new AbortController();
    cliente
      .obtener<Yo>("/v1/yo", {}, control.signal)
      .then((y) => setUsuario(y.usuario ?? "una clave sin nombre"))
      .catch(() => {
        /* no es indispensable */
      });
    return () => control.abort();
  }, [cliente]);

  const irA = (v: Vista) => {
    const limpio: Record<string, string | null> = Object.fromEntries(PARAMETROS_DE_PANTALLA.map((k) => [k, null]));
    cambiar({ ...limpio, vista: v === "transacciones" ? null : v });
  };
  const enlace = (v: Vista, texto: string) => (
    <a
      className="pildora-nav"
      href={v === "transacciones" ? "?" : `?vista=${v}`}
      aria-current={vista === v ? "page" : undefined}
      onClick={(e) => {
        e.preventDefault();
        if (vista !== v) irA(v);
      }}
    >
      {texto}
    </a>
  );

  return (
    <>
      <header className="barra">
        <a className="marca" href="/dashboard#/resumen" aria-label="Antifraude, ir al panel de control">
          <span className="marca-punto" aria-hidden="true" />
          antifraude
        </a>
        <nav className="nav" aria-label="Secciones">
          {enlace("resumen", "Resumen")}
          {enlace("transacciones", "Transacciones")}
          <a className="pildora-nav" href="/dashboard#/vinculos">
            Vínculos
          </a>
          {enlace("revision", "Revisión manual")}
        </nav>
        <div className="barra-acciones">
          {usuario ? <span className="chip">Sesión de {usuario}</span> : null}
          {cliente ? (
            <button
              type="button"
              className="btn suave"
              onClick={() => {
                setClave(null);
                setUsuario(null);
              }}
            >
              Salir
            </button>
          ) : null}
        </div>
      </header>
      <main className="contenido">
        {cliente ? (
          vista === "revision" ? (
            <Revision cliente={cliente} alExpirar={alExpirar} />
          ) : vista === "resumen" ? (
            <Resumen cliente={cliente} alExpirar={alExpirar} />
          ) : (
            <Transacciones cliente={cliente} alExpirar={alExpirar} />
          )
        ) : (
          <Acceso mensaje={mensaje} ocupado={entrando} alEntrar={(c) => void entrar(c)} />
        )}
      </main>
    </>
  );
}
