import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { crearCliente } from "../red/cliente";
import { Resumen } from "../pantallas/Resumen";
import { respuesta, resumenEjemplo, servidorDeResumen } from "./ayudas";

function montar(fetcher: typeof fetch, alExpirar = vi.fn()) {
  render(<Resumen cliente={crearCliente("K", { fetcher, esperas: [] })} alExpirar={alExpirar} />);
  return alExpirar;
}

beforeEach(() => window.history.replaceState(null, "", "/app?vista=resumen"));

/** En el celular los filtros están plegados (como en la cola de revisión): se abren con «Filtros». */
const abrirFiltros = () => userEvent.click(screen.getByRole("button", { name: "Filtros" }));
const tarjeta = (titulo: string) => screen.getByRole("heading", { name: titulo }).closest("section") as HTMLElement;

describe("pantalla de resumen", () => {
  it("muestra los indicadores, las gráficas, la calidad y las tablas con los datos del servidor", async () => {
    const s = servidorDeResumen();
    montar(s.fetcher);
    expect(await screen.findByText("75 %")).toBeTruthy(); // 150 de 200 aprobadas
    expect(screen.getByText("15 %")).toBeTruthy(); // 30 en revisión
    expect(screen.getByText("10 %")).toBeTruthy(); // 20 rechazadas
    expect(screen.getByText("pagos esperando a un analista").previousSibling?.textContent).toBe("7");
    const calidad = within(tarjeta("Calidad del servicio"));
    expect(calidad.getByText("5 % (10)")).toBeTruthy();
    expect(calidad.getByText("19.5 ms")).toBeTruthy();
    expect(calidad.getByText("31.2 ms")).toBeTruthy();
    expect(within(tarjeta("Reglas más activadas")).getByText("monto_alto")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Pagos por día/ })).toBeTruthy();
    expect(screen.getByRole("img", { name: /Distribución del puntaje/ })).toBeTruthy();
    expect(within(tarjeta("Equipo de revisión")).getByText("Ana")).toBeTruthy();
    expect(within(tarjeta("Por comercio")).getByText("Tienda B")).toBeTruthy();
  });

  it("cada gráfica trae sus datos en una tabla (para quien no ve la gráfica)", async () => {
    montar(servidorDeResumen().fetcher);
    await screen.findByText("75 %");
    const porDia = within(tarjeta("Pagos por día"));
    expect(porDia.getByRole("table", { name: "Pagos por día según la decisión", hidden: true })).toBeTruthy();
    expect(porDia.getByText("2026-11-02")).toBeTruthy();
    expect(within(tarjeta("Distribución del puntaje")).getByRole("table", { name: /tramo de puntaje/, hidden: true })).toBeTruthy();
  });

  it("un periodo sin pagos no muestra «NaN» ni «null»: rayas y mensajes", async () => {
    const vacio = resumenEjemplo({
      totales: { ...resumenEjemplo().totales, pagos: 0, aprobadas: 0, en_revision: 0, rechazadas: 0, monto_cop: null, monto_rechazado_cop: null, modo_degradado: 0, sin_estado_del_dispositivo: 0, revisiones_pendientes: 0, latencia_media_ms: null, latencia_p95_ms: null },
      por_dia: [],
      por_comercio: [],
      por_analista: [],
      reglas_mas_activadas: [],
      distribucion_del_puntaje: [],
    });
    montar(servidorDeResumen(vacio).fetcher);
    expect(await screen.findAllByText("No hay pagos en este periodo.")).toHaveLength(2);
    expect(screen.getByText("Todavía no se activó ninguna.")).toBeTruthy();
    const calidad = within(tarjeta("Calidad del servicio"));
    expect(calidad.getAllByText("—").length).toBeGreaterThanOrEqual(2);
    expect(document.body.textContent).not.toMatch(/NaN|null|undefined/);
    expect(screen.queryByRole("heading", { name: "Equipo de revisión" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Por comercio" })).toBeNull();
  });

  it("un texto raro del servidor se muestra como TEXTO, nunca como HTML", async () => {
    const raro = resumenEjemplo({
      por_comercio: [{ comercio_id: 9, nombre: '<img src=x onerror="window.__xss=1">', pagos: 1, aprobadas: 1, en_revision: 0, rechazadas: 0, monto_rechazado: 0 }],
      reglas_mas_activadas: [{ regla: "<script>window.__xss=1</script>", veces: 1, rechazadas: 0 }],
    });
    montar(servidorDeResumen(raro).fetcher);
    await screen.findByText("75 %");
    expect(screen.getByText('<img src=x onerror="window.__xss=1">')).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
  });

  it("el nombre de cada comercio lleva a sus pagos con su id", async () => {
    montar(servidorDeResumen().fetcher);
    await screen.findByText("75 %");
    const enlace = within(tarjeta("Por comercio")).getByRole("link", { name: "Tienda A" });
    expect(enlace.getAttribute("href")).toBe("/dashboard#/transacciones?comercio_id=2");
  });
});

describe("filtros del resumen", () => {
  it("al aplicar un rango pide ese rango y lo deja en la dirección", async () => {
    const s = servidorDeResumen();
    montar(s.fetcher);
    await screen.findByText("75 %");
    expect(s.pedidas[0]?.search).toBe(""); // sin filtros: sin parámetros
    await abrirFiltros();
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2026-11-01" } });
    fireEvent.change(screen.getByLabelText("Hasta"), { target: { value: "2026-11-30" } });
    await userEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    await waitFor(() => expect(s.pedidas.at(-1)?.searchParams.get("desde")).toBe("2026-11-01"));
    expect(s.pedidas.at(-1)?.searchParams.get("hasta")).toBe("2026-11-30");
    expect(window.location.search).toContain("desde=2026-11-01");
    expect(window.location.search).toContain("vista=resumen"); // no se pierde la pantalla
  });

  it("un rango al revés se avisa en el formulario y NO se pide al servidor", async () => {
    const s = servidorDeResumen();
    montar(s.fetcher);
    await screen.findByText("75 %");
    const antes = s.pedidas.length;
    await abrirFiltros();
    fireEvent.change(screen.getByLabelText("Desde"), { target: { value: "2026-12-01" } });
    fireEvent.change(screen.getByLabelText("Hasta"), { target: { value: "2026-11-01" } });
    await userEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/no puede ser posterior/);
    expect(document.activeElement).toBe(screen.getByLabelText("Hasta"));
    expect(s.pedidas.length).toBe(antes);
  });

  it("con dos o más comercios se puede elegir uno, y se pide solo ese", async () => {
    const s = servidorDeResumen();
    montar(s.fetcher);
    await screen.findByText("75 %");
    await abrirFiltros();
    await userEvent.selectOptions(await screen.findByLabelText("Comercio"), "3");
    await waitFor(() => expect(s.pedidas.at(-1)?.searchParams.get("comercio_id")).toBe("3"));
  });

  it("con un solo comercio no hay selector de comercio", async () => {
    montar(servidorDeResumen(resumenEjemplo(), [{ id: 2, nombre: "Tienda A", suspendido: 0, pagos: 5 }]).fetcher);
    await screen.findByText("75 %");
    await abrirFiltros();
    expect(screen.queryByLabelText("Comercio")).toBeNull();
  });

  it("los filtros de la dirección se leen al abrir, y lo raro se descarta", async () => {
    window.history.replaceState(null, "", "/app?vista=resumen&comercio_id=3&desde=2026-11-01&hasta=ayer");
    const s = servidorDeResumen();
    montar(s.fetcher);
    await screen.findByText("75 %");
    expect(s.pedidas[0]?.searchParams.get("comercio_id")).toBe("3");
    expect(s.pedidas[0]?.searchParams.get("desde")).toBe("2026-11-01");
    expect(s.pedidas[0]?.searchParams.has("hasta")).toBe(false);
  });

  it("«Limpiar» quita los filtros y vuelve a pedir sin parámetros", async () => {
    window.history.replaceState(null, "", "/app?vista=resumen&desde=2026-11-01");
    const s = servidorDeResumen();
    montar(s.fetcher);
    await screen.findByText("75 %");
    await abrirFiltros();
    await userEvent.click(screen.getByRole("button", { name: "Limpiar" }));
    await waitFor(() => expect(s.pedidas.at(-1)?.search).toBe(""));
    expect(window.location.search).toBe("?vista=resumen");
  });
});

describe("errores del resumen", () => {
  it("si falla, avisa y «Reintentar» vuelve a pedir y muestra los datos", async () => {
    const s = servidorDeResumen();
    s.estado.fallo = () => respuesta(500, { detail: "se cayó" });
    montar(s.fetcher);
    expect((await screen.findByRole("alert")).textContent).toMatch(/No se pudo cargar/);
    expect(screen.queryByText("75 %")).toBeNull();
    s.estado.fallo = undefined;
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("75 %")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("si la clave ya no sirve (401) avisa a la aplicación para pedirla de nuevo", async () => {
    const s = servidorDeResumen();
    s.estado.fallo = () => respuesta(401, { detail: "clave inválida" });
    const alExpirar = montar(s.fetcher);
    await waitFor(() => expect(alExpirar).toHaveBeenCalled());
    expect(screen.queryByText(/No se pudo cargar/)).toBeNull(); // no se muestra como error de carga
  });
});
