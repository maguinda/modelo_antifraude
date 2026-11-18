import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { crearCliente } from "../red/cliente";
import { Revision } from "../pantallas/Revision";
import { respuesta, revision, servidorDeRevisiones } from "./ayudas";

function montar(fetcher: typeof fetch, alExpirar = vi.fn()) {
  render(<Revision cliente={crearCliente("K", { fetcher, esperas: [] })} alExpirar={alExpirar} />);
  return alExpirar;
}
const posts = (s: ReturnType<typeof servidorDeRevisiones>) => s.llamadas.filter((l) => l.metodo === "POST");
const lecturas = (s: ReturnType<typeof servidorDeRevisiones>) => s.llamadas.filter((l) => l.url.pathname === "/v1/revisiones" && l.metodo === "GET");

/** La pantalla exige una pausa entre armar la confirmación y confirmar (un doble toque no confirma a ciegas). */
const esperarConfirmar = () => new Promise<void>((r) => setTimeout(r, 800));

beforeEach(() => window.history.replaceState(null, "", "/app?vista=revision"));
afterEach(() => vi.useRealTimers());

/** En móvil los filtros están plegados: se abren con el botón «Filtros» (en escritorio el CSS los muestra siempre). */
async function abrirFiltros() {
  await userEvent.click(screen.getByRole("button", { name: "Filtros" }));
}

async function abrirPrimera() {
  const tarjeta = (await screen.findAllByRole("listitem"))[0] as HTMLElement;
  return tarjeta;
}

describe("cola de revisión: lista", () => {
  it("muestra los indicadores, las tarjetas y el contador", async () => {
    const s = servidorDeRevisiones(3);
    montar(s.fetcher);
    expect(await screen.findByText("3 pendientes")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("Por revisar")).toBeTruthy();
    expect(screen.getByText("Riesgo alto")).toBeTruthy();
    expect(s.llamadas.find((l) => l.url.pathname === "/v1/revisiones")?.url.searchParams.get("estado")).toBe("pendiente");
  });

  it("pagina: Siguiente pide el desplazamiento correcto", async () => {
    const s = servidorDeRevisiones(60);
    montar(s.fetcher);
    await screen.findByText("60 pendientes");
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await waitFor(() => expect(lecturas(s).at(-1)?.url.searchParams.get("desplazamiento")).toBe("25"));
    expect(window.location.search).toContain("pagina=2");
  });

  it("sin pagos muestra el mensaje vacío", async () => {
    montar(servidorDeRevisiones(0).fetcher);
    expect(await screen.findByText("No hay pagos con esos filtros.")).toBeTruthy();
  });

  it("un pago ya resuelto muestra quién lo resolvió y NO los botones de decidir", async () => {
    window.history.replaceState(null, "", "/app?vista=revision&estado=resuelta");
    const s = servidorDeRevisiones(0, [{ estado: "resuelta", resuelta_por: "Ana", resuelta_en: "2026-11-01T11:00:00" }]);
    montar(s.fetcher);
    const t = await abrirPrimera();
    expect(within(t).getByText(/Resuelta por Ana/)).toBeTruthy();
    expect(within(t).queryByRole("button", { name: "Aprobar" })).toBeNull();
  });

  it("el texto que viene de la API se muestra como TEXTO, nunca como HTML (XSS)", async () => {
    const malo = '<img src=x onerror="window.__xss=1">';
    const s = servidorDeRevisiones(0, [{ estado: "pendiente", motivos: [malo], comercio: malo, reglas_activadas: [{ regla: malo, motivo: malo, accion: malo }] }]);
    montar(s.fetcher);
    const t = await abrirPrimera();
    expect(t.querySelector("img")).toBeNull();
    expect(within(t).getAllByText(new RegExp("onerror")).length).toBeGreaterThan(0);
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
  });
});

describe("cola de revisión: resolver un pago", () => {
  it("pide CONFIRMAR antes de guardar: el primer clic no escribe nada y «Cancelar» lo deshace", async () => {
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    const t = await abrirPrimera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    expect(within(t).getByRole("button", { name: "Confirmar: aprobar" })).toBeTruthy();
    expect(posts(s)).toHaveLength(0);
    await userEvent.click(within(t).getByRole("button", { name: "Cancelar" }));
    expect(within(t).getByRole("button", { name: "Aprobar" })).toBeTruthy();
    expect(within(t).getByRole("button", { name: "Rechazar" })).toBeTruthy();
    expect(posts(s)).toHaveLength(0);
  });

  it("al confirmar manda decisión y nota, avisa, y recarga la lista (el pago sale de pendientes)", async () => {
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    const t = await abrirPrimera();
    await userEvent.type(within(t).getByLabelText("Nota (opcional)"), "  cliente conocido  ");
    await userEvent.click(within(t).getByRole("button", { name: "Rechazar" }));
    await esperarConfirmar();
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: rechazar" }));
    expect(await screen.findByText("Pago TX1: rechazado.")).toBeTruthy();
    expect(posts(s)).toHaveLength(1);
    expect(posts(s)[0]?.url.pathname).toBe("/v1/revisiones/rev1/resolver");
    expect(posts(s)[0]?.cuerpo).toEqual({ decision: "RECHAZADA", nota: "  cliente conocido  " });
    await screen.findByText("1 pendiente");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("sin nota manda null (no una cadena vacía)", async () => {
    const s = servidorDeRevisiones(1);
    montar(s.fetcher);
    const t = await abrirPrimera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperarConfirmar();
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    await screen.findByText("Pago TX1: aprobado.");
    expect(posts(s)[0]?.cuerpo).toEqual({ decision: "APROBADA", nota: null });
  });

  it("un doble clic en «Confirmar» manda UNA sola escritura", async () => {
    const s = servidorDeRevisiones(1);
    const sueltan: Array<() => void> = [];
    let enviados = 0; // se cuenta AL SALIR del navegador, antes de esperar al servidor
    const lenta = (async (e: RequestInfo | URL, i?: RequestInit) => {
      if ((i?.method ?? "GET") === "POST") {
        enviados += 1;
        await new Promise<void>((r) => sueltan.push(r));
      }
      return s.fetcher(e, i);
    }) as typeof fetch;
    montar(lenta);
    const t = await abrirPrimera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    const confirmar = within(t).getByRole("button", { name: "Confirmar: aprobar" });
    await esperarConfirmar();
    await userEvent.click(confirmar);
    await userEvent.click(confirmar);
    await userEvent.click(confirmar);
    expect(enviados).toBe(1);
    sueltan.forEach((r) => r());
    await screen.findByText("Pago TX1: aprobado.");
    expect(posts(s)).toHaveLength(1);
  });

  it("si otra persona ya la resolvió (409): avisa, recarga y NO reintenta", async () => {
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    const t = await abrirPrimera();
    s.todas[0]!.estado = "resuelta"; // otra persona la resolvió mientras esta pantalla la mostraba
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperarConfirmar();
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    expect(await screen.findByText(/ya estaba resuelta/)).toBeTruthy();
    expect(posts(s)).toHaveLength(1);
    await screen.findByText("1 pendiente");
  });

  it("un error del servidor (500) se dice, la tarjeta vuelve a poder decidirse y no se reintenta solo", async () => {
    const s = servidorDeRevisiones(1);
    s.fallos.resolver = () => respuesta(500, { detail: "se cayó" });
    montar(s.fetcher);
    const t = await abrirPrimera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperarConfirmar();
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    expect(await screen.findByText("No se pudo guardar: se cayó")).toBeTruthy();
    expect(within(t).getByRole("button", { name: "Aprobar" }).getAttribute("aria-disabled")).toBe("false");
    expect(posts(s)).toHaveLength(1);
  });

  it("si no hay respuesta (corte) NO dice «guardado»: avisa que no se sabe y recarga para mirar cómo quedó", async () => {
    const s = servidorDeRevisiones(1);
    const f = (async (e: RequestInfo | URL, i?: RequestInit) => {
      if ((i?.method ?? "GET") === "POST") throw new TypeError("Failed to fetch");
      return s.fetcher(e, i);
    }) as typeof fetch;
    montar(f);
    const t = await abrirPrimera();
    const antes = lecturas(s).length;
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperarConfirmar();
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    expect(await screen.findByText(/No se pudo confirmar si se guardó/)).toBeTruthy();
    expect(screen.queryByText(/aprobado\./)).toBeNull();
    await waitFor(() => expect(lecturas(s).length).toBeGreaterThan(antes));
  });

  it("una clave vencida (401) al resolver avisa para volver a entrar", async () => {
    const s = servidorDeRevisiones(1);
    s.fallos.resolver = () => respuesta(401, { detail: "no" });
    const alExpirar = montar(s.fetcher);
    const t = await abrirPrimera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperarConfirmar();
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    await waitFor(() => expect(alExpirar).toHaveBeenCalled());
  });

  it("la nota tiene tope de 500 caracteres", async () => {
    montar(servidorDeRevisiones(1).fetcher);
    const t = await abrirPrimera();
    expect(within(t).getByLabelText("Nota (opcional)").getAttribute("maxlength")).toBe("500");
  });
});

describe("cola de revisión: filtros", () => {
  it("cambiar «Mostrar» a Resueltas pide estado=resuelta y se recuerda en la dirección", async () => {
    const s = servidorDeRevisiones(2, [{ estado: "resuelta" }]);
    montar(s.fetcher);
    await screen.findByText("2 pendientes");
    await userEvent.selectOptions(screen.getByLabelText("Mostrar"), "resuelta");
    expect(await screen.findByText("1 resuelta")).toBeTruthy();
    expect(window.location.search).toContain("estado=resuelta");
  });

  it("mientras llega la lista nueva, el contador NO mezcla el número viejo con la etiqueta nueva", async () => {
    const s = servidorDeRevisiones(5, [{ estado: "resuelta" }]);
    let soltar!: () => void;
    const lenta = (async (e: RequestInfo | URL, i?: RequestInit) => {
      if (new URL(String(e), "http://x").searchParams.get("estado") === "resuelta") await new Promise<void>((r) => (soltar = r));
      return s.fetcher(e, i);
    }) as typeof fetch;
    montar(lenta);
    await screen.findByText("5 pendientes");
    await abrirFiltros();
    await userEvent.selectOptions(screen.getByLabelText("Mostrar"), "resuelta");
    await waitFor(() => expect(window.location.search).toContain("estado=resuelta"));
    expect(screen.queryByText("5 resueltas")).toBeNull(); // antes se veía «5 resueltas» (número de pendientes, etiqueta de resueltas)
    expect(screen.getByText("5 pendientes")).toBeTruthy(); // lo viejo, con SU etiqueta, hasta que llegue lo nuevo
    soltar();
    expect(await screen.findByText("1 resuelta")).toBeTruthy();
    expect(screen.queryByText("5 pendientes")).toBeNull();
  });

  it("el riesgo alto se traduce al rango del servidor", async () => {
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    await screen.findByText("2 pendientes");
    await userEvent.selectOptions(screen.getByLabelText("Riesgo"), "alto");
    await waitFor(() => expect(lecturas(s).at(-1)?.url.searchParams.get("riesgo_min")).toBe("0.6"));
  });

  it("un monto negativo se rechaza con aviso y NO llega al servidor; corregirlo sí aplica", async () => {
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    await screen.findByText("2 pendientes");
    await abrirFiltros();
    const antes = lecturas(s).length;
    await userEvent.type(screen.getByLabelText("Monto mínimo"), "-5");
    await userEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    expect(await screen.findByText("Escribe un número igual o mayor que 0.")).toBeTruthy();
    expect(lecturas(s).length).toBe(antes);
    await userEvent.clear(screen.getByLabelText("Monto mínimo"));
    await userEvent.type(screen.getByLabelText("Monto mínimo"), "100");
    await userEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    await waitFor(() => expect(lecturas(s).at(-1)?.url.searchParams.get("monto_min")).toBe("100"));
  });

  it("«Desde» posterior a «Hasta» se avisa y no se aplica", async () => {
    const s = servidorDeRevisiones(1);
    montar(s.fetcher);
    await screen.findByText("1 pendiente");
    await abrirFiltros();
    const antes = lecturas(s).length;
    await userEvent.type(screen.getByLabelText("Desde"), "2026-12-01");
    await userEvent.type(screen.getByLabelText("Hasta"), "2026-11-01");
    await userEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    expect(await screen.findByText(/no puede ser posterior/)).toBeTruthy();
    expect(lecturas(s).length).toBe(antes);
  });

  it("«Limpiar» quita todos los filtros", async () => {
    window.history.replaceState(null, "", "/app?vista=revision&riesgo=alto&q=TX1&estado=resuelta");
    const s = servidorDeRevisiones(3);
    montar(s.fetcher);
    await screen.findByText("No hay pagos con esos filtros.");
    await abrirFiltros();
    await userEvent.click(screen.getByRole("button", { name: "Limpiar" }));
    expect(await screen.findByText("3 pendientes")).toBeTruthy();
    expect(window.location.search).not.toContain("riesgo");
  });

  it("parámetros basura en la dirección no rompen nada", async () => {
    window.history.replaceState(null, "", "/app?vista=revision&pagina=-3&limite=999&orden=drop&riesgo=zzz&monto_min=abc&desde=ayer");
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    expect(await screen.findByText("2 pendientes")).toBeTruthy();
    const p = lecturas(s)[0]!.url.searchParams;
    expect([p.get("limite"), p.get("orden"), p.get("riesgo_min"), p.get("monto_min"), p.get("desde")]).toEqual(["25", "antiguos", null, null, null]);
  });
});

describe("cola de revisión: fallos y vigilancia", () => {
  it("si falla la primera carga muestra el aviso con «Reintentar» y reintentar funciona", async () => {
    const s = servidorDeRevisiones(1);
    s.fallos.lista = () => respuesta(500, { detail: "se cayó" });
    montar(s.fetcher);
    const alerta = await screen.findByRole("alert");
    expect(within(alerta).getByText(/se cayó/)).toBeTruthy();
    delete s.fallos.lista;
    await userEvent.click(within(alerta).getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("1 pendiente")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("una clave vencida (401) al cargar avisa para volver a entrar y no muestra el error", async () => {
    const s = servidorDeRevisiones(1);
    s.fallos.lista = () => respuesta(401, { detail: "no" });
    const alExpirar = montar(s.fetcher);
    await waitFor(() => expect(alExpirar).toHaveBeenCalled());
    expect(screen.queryByText(/No se pudo cargar/)).toBeNull();
  });

  it("cada 20 s revisa cuántos hay y, si cambió, AVISA sin redibujar la lista (no se pierde una nota a medio escribir)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    await screen.findByText("2 pendientes");
    const t = await abrirPrimera();
    await userEvent.type(within(t).getByLabelText("Nota (opcional)"), "a medias");
    s.todas.push(revision(99)); // llegó un pago nuevo
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20500);
    });
    expect(await screen.findByText(/Cambió la lista \(3 pendientes\)/)).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2); // la lista NO se redibujó
    expect((within(t).getByLabelText("Nota (opcional)") as HTMLTextAreaElement).value).toBe("a medias");
    await userEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    expect(await screen.findByText("3 pendientes")).toBeTruthy();
  });

  it("no revisa mientras la pestaña está oculta", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const s = servidorDeRevisiones(1);
    montar(s.fetcher);
    await screen.findByText("1 pendiente");
    const antes = lecturas(s).length;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(41000);
    });
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    expect(lecturas(s).length).toBe(antes);
  });
});
