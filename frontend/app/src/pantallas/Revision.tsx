import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FiltrosRevisionForm } from "../componentes/FiltrosRevision";
import { Paginacion } from "../componentes/Paginacion";
import { TarjetaRevision, resultadoDeError, type ResultadoResolver } from "../componentes/TarjetaRevision";
import { aParametrosApi, filtrosAUrl, hayFiltros, leerFiltros, type FiltrosRevision } from "../filtrosRevision";
import { hace, numero, pesos, pesosCompactos } from "../formato";
import { useConsulta } from "../hooks/useConsulta";
import { useParametrosUrl } from "../hooks/useParametrosUrl";
import { ajustarPagina, limitarPagina, tamanoValido } from "../paginacion";
import { ErrorApi, type Cliente } from "../red/cliente";
import type { ComercioFiltro, DecisionAnalista, PaginaRevisiones, Revision as TipoRevision } from "../red/tipos";
import { veredictoSinConfirmar, type SinConfirmar } from "../veredicto";
import estilos from "./Revision.module.css";

const CADA_CUANTO_VIGILAR_MS = 20000;
const CLAVE_SIN_LISTA = "\u0000sin-lista";

/** Lo que llegó + CON QUÉ se pidió: las etiquetas de la pantalla salen de aquí, no de los filtros de ahora (mientras llega lo nuevo se ve lo viejo, con sus propias etiquetas). */
type PaginaCargada = PaginaRevisiones & { clave: string; pagina: number; porPagina: number; estado: FiltrosRevision["estado"]; conFiltros: boolean };
interface Aviso {
  texto: string;
  error: boolean;
}

interface Props {
  cliente: Cliente;
  /** La clave ya no sirve (401/403): se vuelve a pedir. */
  alExpirar: () => void;
}

const esAuth = (e: unknown) => e instanceof ErrorApi && (e.estado === 401 || e.estado === 403);

export function Revision({ cliente, alExpirar }: Props) {
  const [params, cambiar] = useParametrosUrl();
  const filtros = useMemo(() => leerFiltros(params), [params]);
  const porPagina = tamanoValido(params.get("limite"));
  const pedida = limitarPagina(params.get("pagina"), porPagina);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [cambioEnLaLista, setCambioEnLaLista] = useState<number | null>(null);
  /** Pagos cuya decisión quedó sin confirmar: no se deja decidir de nuevo hasta mirar cómo quedaron (otra lectura de la lista). */
  const [sinConfirmar, setSinConfirmar] = useState<Record<string, SinConfirmar>>({});
  const limpiarAvisos = useCallback(() => {
    setAviso(null);
    setCambioEnLaLista(null);
  }, []);

  const comercios = useConsulta<ComercioFiltro[]>("comercios", (senal) => cliente.obtener<ComercioFiltro[]>("/v1/panel/comercios", {}, senal));

  const clave = JSON.stringify([filtros, porPagina, pedida]);
  const { datos, cargando, error, recargar } = useConsulta<PaginaCargada>(clave, async (senal) => {
    const r = await cliente.obtener<PaginaRevisiones>("/v1/revisiones", aParametrosApi(filtros, porPagina, (pedida - 1) * porPagina), senal);
    return { ...r, clave, pagina: pedida, porPagina, estado: filtros.estado, conFiltros: hayFiltros(filtros) };
  });

  const total = datos?.total ?? 0;
  const mostrada = datos ? ajustarPagina(datos.pagina, total, datos.porPagina) : 1;
  const debida = datos ? ajustarPagina(pedida, total, porPagina) : pedida;

  // Si el total bajó (alguien resolvió pagos) y la página pedida quedó más allá del final, se vuelve a la última, sin error y sin ensuciar el historial.
  useEffect(() => {
    if (datos && !cargando && !error && debida !== pedida) cambiar({ pagina: String(debida) }, { reemplazar: true });
  }, [datos, cargando, error, debida, pedida, cambiar]);

  useEffect(() => {
    if (esAuth(error) || esAuth(comercios.error)) alExpirar();
  }, [error, comercios.error, alExpirar]);

  // Cada 20 s se mira SOLO cuántos hay (sin redibujar la lista: no se pierde lo que alguien está escribiendo en una nota).
  useEffect(() => {
    if (filtros.estado !== "pendiente" || !datos) return;
    const control = new AbortController();
    const total0 = datos.total;
    const id = setInterval(() => {
      if (document.hidden) return;
      cliente
        .obtener<PaginaRevisiones>("/v1/revisiones", { ...aParametrosApi(filtros, 1, 0) }, control.signal)
        .then((r) => setCambioEnLaLista(r.total !== total0 ? r.total : null))
        .catch(() => {
          /* es solo un aviso: si falla, no pasa nada */
        });
    }, CADA_CUANTO_VIGILAR_MS);
    return () => {
      clearInterval(id);
      control.abort();
    };
  }, [cliente, filtros, datos]);

  // Llegó una lectura nueva de la lista: se mira cómo quedaron los pagos sin confirmar DE ESA MISMA CONSULTA, se dice y se desbloquean.
  // (Los de otra consulta —otro filtro u otra página— siguen bloqueados: que no aparezcan ahí no prueba nada.)
  useEffect(() => {
    if (!datos || !Object.keys(sinConfirmar).length) return;
    const pendientesAhora = new Set(datos.revisiones.filter((r) => r.estado === "pendiente").map((r) => r.id_evaluacion));
    const { aviso: nuevo, resto } = veredictoSinConfirmar(sinConfirmar, datos.clave, pendientesAhora);
    if (!nuevo) return;
    setAviso(nuevo);
    setSinConfirmar(resto);
    // `sinConfirmar` no está en las dependencias A PROPÓSITO: solo debe correr cuando llega una lectura nueva; si dependiera de él, dispararía al marcar un pago (antes de la verificación).
  }, [datos]);

  const actualizar = useCallback(() => {
    limpiarAvisos();
    setSinConfirmar({}); // quien pulsa Actualizar ya sabe que no se confirmó: se desbloquea
    recargar();
  }, [recargar, limpiarAvisos]);

  const consultaVista = useRef<string | null>(null); // la consulta de la lista que se VE ahora (se lee al pulsar, antes de esperar al servidor)
  consultaVista.current = datos?.clave ?? null;

  const resolver = useCallback(
    async (rev: TipoRevision, decision: DecisionAnalista, nota: string | null): Promise<ResultadoResolver> => {
      // Sin lista a la vista (no debería pasar: la tarjeta sale de ella) se usa una clave que ninguna lectura real iguala: falla CERRADO, bloqueada hasta «Actualizar».
      const claveAlPulsar = consultaVista.current ?? CLAVE_SIN_LISTA;
      setAviso(null);
      try {
        await cliente.enviar(`/v1/revisiones/${encodeURIComponent(rev.id_evaluacion)}/resolver`, { decision, nota });
        setAviso({ texto: `Pago ${rev.id_transaccion}: ${decision === "APROBADA" ? "aprobado" : "rechazado"}.`, error: false });
        setCambioEnLaLista(null);
        recargar();
        return { tipo: "ok", decision };
      } catch (e) {
        if (esAuth(e)) {
          alExpirar();
          return { tipo: "error", mensaje: "Tu clave ya no sirve." };
        }
        const r = resultadoDeError(e);
        if (r.tipo === "conflicto") {
          setAviso({ texto: "Esa revisión ya estaba resuelta: puede ser tu decisión anterior o la de un colega. Actualizando la lista.", error: true });
          recargar();
        } else if (r.tipo === "sin_confirmar") {
          setAviso({ texto: r.mensaje, error: true });
          setSinConfirmar((x) => ({ ...x, [rev.id_evaluacion]: { transaccion: rev.id_transaccion, clave: claveAlPulsar } }));
          recargar(); // se mira cómo quedó antes de dejar que alguien reintente (la tarjeta sigue bloqueada hasta entonces)
        } else if (r.tipo === "error") {
          setAviso({ texto: `No se pudo guardar: ${r.mensaje}`, error: true });
        }
        return r;
      }
    },
    [cliente, recargar, alExpirar],
  );

  const aplicar = (f: FiltrosRevision) => {
    limpiarAvisos();
    cambiar(filtrosAUrl(f));
  };

  const items = datos?.revisiones ?? [];
  const altos = items.filter((x) => x.puntaje_riesgo !== null && x.puntaje_riesgo >= 0.6).length;
  const masAntigua = items.reduce<string | null>((a, x) => (a === null || Date.parse(x.creada_en) < Date.parse(a) ? x.creada_en : a), null);
  const pendientes = (datos?.estado ?? filtros.estado) === "pendiente"; // según lo que se VE, no según lo que se acaba de pedir
  const sinDatosAun = datos === null;
  const kpis: Array<[string, string, string, string]> = [
    ["", pendientes ? "Por revisar" : "Resueltas", numero(total), datos?.conFiltros ? "con estos filtros" : "en total"],
    ["", "Monto en la cola", pesosCompactos(datos?.monto_total_cop), pesos(datos?.monto_total_cop)],
    ["oscuro", "Riesgo alto", numero(altos), "en esta página"],
    ["verde", pendientes ? "La que más espera" : "Última resuelta", masAntigua ? hace(masAntigua).replace("hace ", "") : "—", "en esta página"],
  ];

  return (
    <section aria-label="Pagos para revisar" aria-busy={cargando} className={estilos.pantalla}>
      <section className="hero">
        <span className="ojo">Revisión manual</span>
        <h1>
          Pagos <em>por decidir</em>
        </h1>
        <p>Los pagos que el sistema dejó en revisión. Abre uno, mira por qué dudó y apruébalo o recházalo.</p>
      </section>

      {!sinDatosAun ? (
        <div className="rejilla kpis" aria-label="Resumen de la cola">
          {kpis.map(([clase, etiqueta, valor, nota]) => (
            <div key={etiqueta} className={`kpi ${clase}`}>
              <span className="etiqueta">{etiqueta}</span>
              <span className="valor">{valor}</span>
              <span className="nota">{nota}</span>
            </div>
          ))}
        </div>
      ) : null}

      <FiltrosRevisionForm aplicados={filtros} comercios={comercios.datos ?? []} alAplicar={aplicar} />

      <div className={estilos.encabezado}>
        <span className="chip" role="status" aria-live="polite">
          {sinDatosAun ? "" : `${numero(total)} ${pendientes ? "pendiente" : "resuelta"}${total === 1 ? "" : "s"}`}
        </span>
        <button type="button" className="btn suave" onClick={actualizar}>
          Actualizar
        </button>
      </div>

      <div role="status" aria-live="polite">
        {aviso ? <p className={`aviso ${aviso.error ? "error" : ""}`}>{aviso.texto}</p> : null}
        {cambioEnLaLista !== null ? <p className="aviso">Cambió la lista ({numero(cambioEnLaLista)} pendientes). Pulsa Actualizar.</p> : null}
      </div>

      {error && !esAuth(error) ? (
        <div className="aviso error" role="alert">
          <span>No se pudo cargar: {error.message} </span>
          <button type="button" className="btn suave" onClick={recargar}>
            Reintentar
          </button>
        </div>
      ) : null}

      {sinDatosAun && cargando ? <p className={estilos.cargando}>Cargando…</p> : null}
      {!sinDatosAun ? (
        <ul className={`${estilos.lista} ${cargando ? estilos.refrescando : ""}`}>
          {items.map((rev) => (
            <TarjetaRevision key={rev.id_evaluacion} rev={rev} alResolver={resolver} bloqueada={rev.id_evaluacion in sinConfirmar} />
          ))}
        </ul>
      ) : null}
      {!sinDatosAun && items.length === 0 && !cargando ? <p className={estilos.vacio}>No hay pagos con esos filtros.</p> : null}

      {!sinDatosAun ? (
        <Paginacion
          total={total}
          pagina={mostrada}
          porPagina={datos?.porPagina ?? porPagina}
          ocupado={cargando}
          alCambiarPagina={(p) => {
            if (p === pedida) recargar();
            else {
              limpiarAvisos();
              cambiar({ pagina: p <= 1 ? null : String(p) });
              window.scrollTo(0, 0);
            }
          }}
          alCambiarTamano={(t) => {
            limpiarAvisos();
            cambiar({ limite: String(t), pagina: null });
          }}
        />
      ) : null}
    </section>
  );
}
