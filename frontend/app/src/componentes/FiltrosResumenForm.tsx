import { useEffect, useRef, useState, type FormEvent } from "react";
import { FILTROS_RESUMEN_INICIALES, validarFechas, type FiltrosResumen } from "../resumen";
import type { ComercioFiltro } from "../red/tipos";

interface Props {
  /** Los filtros ya aplicados (los de la dirección). */
  aplicados: FiltrosResumen;
  comercios: ComercioFiltro[];
  alAplicar: (f: FiltrosResumen) => void;
}

export function FiltrosResumenForm({ aplicados, comercios, alAplicar }: Props) {
  const [borrador, setBorrador] = useState(aplicados);
  const [abierto, setAbierto] = useState(false);
  const [errorFechas, setErrorFechas] = useState("");
  const hastaRef = useRef<HTMLInputElement>(null);

  // Si los filtros cambian desde fuera (botón «atrás», enlace), el formulario los refleja.
  useEffect(() => {
    setBorrador(aplicados);
    setErrorFechas("");
  }, [aplicados]);

  const intentar = (f: FiltrosResumen): void => {
    const e = validarFechas(f);
    setErrorFechas(e);
    if (e) {
      hastaRef.current?.focus();
      return;
    }
    alAplicar(f);
  };
  const enviar = (ev: FormEvent) => {
    ev.preventDefault();
    intentar(borrador);
  };

  return (
    <section className="tarjeta pila" aria-label="Filtros del resumen">
      <button type="button" className="btn suave alternar-filtros" aria-expanded={abierto} aria-controls="cuerpo-filtros-resumen" onClick={() => setAbierto(!abierto)}>
        Filtros
      </button>
      <form onSubmit={enviar} noValidate autoComplete="off">
        <div id="cuerpo-filtros-resumen" className="filtros filtros-cuerpo" hidden={!abierto}>
          {comercios.length >= 2 ? (
            <div className="campo">
              <label htmlFor="r-comercio">Comercio</label>
              <select
                id="r-comercio"
                value={borrador.comercio}
                onChange={(e) => {
                  const f = { ...borrador, comercio: e.target.value };
                  setBorrador(f);
                  intentar(f);
                }}
              >
                <option value="">Todos los comercios</option>
                {comercios.map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {c.nombre}
                    {c.suspendido ? " (suspendido)" : ""}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          <div className="campo">
            <label htmlFor="r-desde">Desde</label>
            <input id="r-desde" type="date" value={borrador.desde} onChange={(e) => setBorrador({ ...borrador, desde: e.target.value })} />
          </div>
          <div className="campo">
            <label htmlFor="r-hasta">Hasta</label>
            <input
              id="r-hasta"
              ref={hastaRef}
              type="date"
              value={borrador.hasta}
              aria-invalid={Boolean(errorFechas)}
              aria-describedby={errorFechas ? "error-fechas-resumen" : undefined}
              onChange={(e) => setBorrador({ ...borrador, hasta: e.target.value })}
            />
            {errorFechas ? (
              <span id="error-fechas-resumen" className="error-campo" role="alert">
                {errorFechas}
              </span>
            ) : null}
          </div>
          <div className="campo">
            <div className="filtros-acciones">
              <button type="submit" className="btn azul">
                Aplicar
              </button>
              <button
                type="button"
                className="btn suave"
                onClick={() => {
                  setBorrador(FILTROS_RESUMEN_INICIALES);
                  setErrorFechas("");
                  alAplicar(FILTROS_RESUMEN_INICIALES);
                }}
              >
                Limpiar
              </button>
            </div>
          </div>
        </div>
      </form>
    </section>
  );
}
