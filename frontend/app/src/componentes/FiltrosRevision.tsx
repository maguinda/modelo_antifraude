import { useEffect, useRef, useState, type FormEvent } from "react";
import { FILTROS_INICIALES, ORDENES_REVISION, validarFiltros, type FiltrosRevision, type OrdenRevision, type Riesgo } from "../filtrosRevision";
import type { ComercioFiltro } from "../red/tipos";

interface Props {
  /** Los filtros ya aplicados (los de la dirección). */
  aplicados: FiltrosRevision;
  comercios: ComercioFiltro[];
  alAplicar: (f: FiltrosRevision) => void;
}

export function FiltrosRevisionForm({ aplicados, comercios, alAplicar }: Props) {
  const [borrador, setBorrador] = useState(aplicados);
  const [abierto, setAbierto] = useState(false);
  const [errores, setErrores] = useState({ monto: "", fechas: "" });
  const montoRef = useRef<HTMLInputElement>(null);
  const hastaRef = useRef<HTMLInputElement>(null);

  // Si los filtros cambian desde fuera (botón «atrás», enlace), el formulario los refleja.
  useEffect(() => {
    setBorrador(aplicados);
    setErrores({ monto: "", fechas: "" });
  }, [aplicados]);

  const poner = <K extends keyof FiltrosRevision>(k: K, v: FiltrosRevision[K]): FiltrosRevision => ({ ...borrador, [k]: v });

  const intentar = (f: FiltrosRevision): void => {
    const e = validarFiltros(f);
    setErrores(e);
    if (e.monto || e.fechas) {
      (e.monto ? montoRef : hastaRef).current?.focus();
      return;
    }
    alAplicar(f);
  };
  const enviar = (ev: FormEvent) => {
    ev.preventDefault();
    intentar(borrador);
  };
  const cambiarSelect = (f: FiltrosRevision) => {
    setBorrador(f);
    intentar(f);
  };

  return (
    <section className="tarjeta pila" aria-label="Filtros">
      <button type="button" className="btn suave alternar-filtros" aria-expanded={abierto} aria-controls="cuerpo-filtros" onClick={() => setAbierto(!abierto)}>
        Filtros
      </button>
      <form onSubmit={enviar} noValidate autoComplete="off">
        <div id="cuerpo-filtros" className="filtros filtros-cuerpo" hidden={!abierto}>
          <div className="campo">
            <label htmlFor="f-q">Buscar por ID o cliente</label>
            <input id="f-q" type="text" maxLength={64} placeholder="AF-XXXX-XXXX o cliente" value={borrador.q} onChange={(e) => setBorrador(poner("q", e.target.value))} />
          </div>
          <div className="campo">
            <label htmlFor="f-estado">Mostrar</label>
            <select id="f-estado" value={borrador.estado} onChange={(e) => cambiarSelect(poner("estado", e.target.value as FiltrosRevision["estado"]))}>
              <option value="pendiente">Pendientes</option>
              <option value="resuelta">Resueltas</option>
            </select>
          </div>
          {comercios.length >= 2 ? (
            <div className="campo">
              <label htmlFor="f-comercio">Comercio</label>
              <select id="f-comercio" value={borrador.comercio} onChange={(e) => cambiarSelect(poner("comercio", e.target.value))}>
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
            <label htmlFor="f-riesgo">Riesgo</label>
            <select id="f-riesgo" value={borrador.riesgo} onChange={(e) => cambiarSelect(poner("riesgo", e.target.value as Riesgo))}>
              <option value="">Todos</option>
              <option value="alto">Alto (60 % o más)</option>
              <option value="medio">Medio (35 % a 59 %)</option>
              <option value="bajo">Bajo (menos de 35 %)</option>
            </select>
          </div>
          <div className="campo">
            <label htmlFor="f-monto">Monto mínimo</label>
            <input
              id="f-monto"
              ref={montoRef}
              type="number"
              min={0}
              step="any"
              inputMode="decimal"
              value={borrador.montoMin}
              aria-invalid={Boolean(errores.monto)}
              aria-describedby={errores.monto ? "error-monto" : undefined}
              onChange={(e) => setBorrador(poner("montoMin", e.target.value))}
            />
            {errores.monto ? (
              <span id="error-monto" className="error-campo" role="alert">
                {errores.monto}
              </span>
            ) : null}
          </div>
          <div className="campo">
            <label htmlFor="f-desde">Desde</label>
            <input id="f-desde" type="date" value={borrador.desde} onChange={(e) => setBorrador(poner("desde", e.target.value))} />
          </div>
          <div className="campo">
            <label htmlFor="f-hasta">Hasta</label>
            <input
              id="f-hasta"
              ref={hastaRef}
              type="date"
              value={borrador.hasta}
              aria-invalid={Boolean(errores.fechas)}
              aria-describedby={errores.fechas ? "error-fechas" : undefined}
              onChange={(e) => setBorrador(poner("hasta", e.target.value))}
            />
            {errores.fechas ? (
              <span id="error-fechas" className="error-campo" role="alert">
                {errores.fechas}
              </span>
            ) : null}
          </div>
          <div className="campo">
            <label htmlFor="f-orden">Ordenar por</label>
            <select id="f-orden" value={borrador.orden} onChange={(e) => cambiarSelect(poner("orden", e.target.value as OrdenRevision))}>
              {ORDENES_REVISION.map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
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
                  setBorrador(FILTROS_INICIALES);
                  setErrores({ monto: "", fechas: "" });
                  alAplicar(FILTROS_INICIALES);
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
