// Arreglos de la cola de revisión tras la revisión del PR #59: doble toque, avisos que no se limpian y resultado desconocido al escribir.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { crearCliente } from "../red/cliente";
import { Revision } from "../pantallas/Revision";
import { respuesta, revision, servidorDeRevisiones } from "./ayudas";

function montar(fetcher: typeof fetch) {
  render(<Revision cliente={crearCliente("K", { fetcher, esperas: [] })} alExpirar={vi.fn()} />);
}
const posts = (s: ReturnType<typeof servidorDeRevisiones>) => s.llamadas.filter((l) => l.metodo === "POST");
const lecturas = (s: ReturnType<typeof servidorDeRevisiones>) => s.llamadas.filter((l) => l.url.pathname === "/v1/revisiones" && l.metodo === "GET");
const esperar = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
/** Tiempo real que la pantalla exige entre «armar» la confirmación y poder confirmar (con margen). */
const PAUSA_DE_CONFIRMAR = 800;

beforeEach(() => window.history.replaceState(null, "", "/app?vista=revision"));
afterEach(() => vi.useRealTimers());

async function primera() {
  return (await screen.findAllByRole("listitem"))[0] as HTMLElement;
}
const desactivado = (b: HTMLElement) => b.getAttribute("aria-disabled") === "true";

describe("1. un doble toque NO salta la confirmación", () => {
  it("dos toques seguidos sobre «Aprobar» solo arman la confirmación: no se escribe nada", async () => {
    const s = servidorDeRevisiones(1);
    montar(s.fetcher);
    const t = await primera();
    const aprobar = within(t).getByRole("button", { name: "Aprobar" });
    await userEvent.click(aprobar);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" })); // segundo toque, de inmediato
    await esperar(50);
    expect(posts(s)).toHaveLength(0);
    expect(within(t).getByRole("button", { name: "Confirmar: aprobar" })).toBeTruthy(); // sigue pidiendo confirmar
  });

  it("lo mismo con «Rechazar»", async () => {
    const s = servidorDeRevisiones(1);
    montar(s.fetcher);
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Rechazar" }));
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: rechazar" }));
    await esperar(50);
    expect(posts(s)).toHaveLength(0);
  });

  it("control: pasado el tiempo mínimo, un toque en «Confirmar» SÍ guarda (una sola vez)", async () => {
    const s = servidorDeRevisiones(1);
    montar(s.fetcher);
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(PAUSA_DE_CONFIRMAR);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    expect(await screen.findByText("Pago TX1: aprobado.")).toBeTruthy();
    expect(posts(s)).toHaveLength(1);
  });
});

describe("2. los avisos no se quedan pegados", () => {
  async function resolverPrimera(s: ReturnType<typeof servidorDeRevisiones>) {
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(PAUSA_DE_CONFIRMAR);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    await screen.findByText("Pago TX1: aprobado.");
    return s;
  }

  it("«Pago X: aprobado.» desaparece al cambiar de filtro", async () => {
    const s = servidorDeRevisiones(2, [{ estado: "resuelta" }]);
    montar(s.fetcher);
    await resolverPrimera(s);
    await userEvent.selectOptions(screen.getByLabelText("Mostrar"), "resuelta");
    await screen.findByText("2 resueltas");
    await waitFor(() => expect(screen.queryByText("Pago TX1: aprobado.")).toBeNull());
  });

  it("«Pago X: aprobado.» desaparece al cambiar de página", async () => {
    const s = servidorDeRevisiones(60);
    montar(s.fetcher);
    await resolverPrimera(s);
    await userEvent.click(await screen.findByRole("button", { name: "Siguiente" }));
    await waitFor(() => expect(screen.queryByText("Pago TX1: aprobado.")).toBeNull());
  });

  it("el aviso de «Cambió la lista» sale aunque haya un aviso de éxito anterior", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    await resolverPrimera(s);
    await screen.findByText("1 pendiente");
    s.todas.push(revision(99)); // llegó otro pago mientras tanto
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20500);
    });
    expect(await screen.findByText(/Cambió la lista \(2 pendientes\)/)).toBeTruthy();
  });
});

describe("3. si no se sabe si se guardó, la tarjeta no deja decidir otra vez a ciegas", () => {
  for (const estado of [502, 503, 504]) {
    it(`un ${estado} del proxy al resolver se trata como «no se sabe» (no como «no se guardó»)`, async () => {
      const s = servidorDeRevisiones(1);
      s.fallos.resolver = () => respuesta(estado, { detail: "proxy" });
      montar(s.fetcher);
      const t = await primera();
      const antes = lecturas(s).length;
      await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
      await esperar(PAUSA_DE_CONFIRMAR);
      await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
      expect(await screen.findByText(/No se pudo confirmar si se guardó/)).toBeTruthy();
      expect(screen.queryByText(/No se pudo guardar/)).toBeNull();
      await waitFor(() => expect(lecturas(s).length).toBeGreaterThan(antes)); // recarga para mirar cómo quedó
      expect(posts(s)).toHaveLength(1);
    });
  }

  it("mientras la recarga no termina, los botones siguen bloqueados; al terminar (y seguir pendiente) se desbloquean", async () => {
    const s = servidorDeRevisiones(1);
    const caida = (async (e: RequestInfo | URL, i?: RequestInit) => {
      if ((i?.method ?? "GET") === "POST") throw new TypeError("Failed to fetch"); // corte: no se sabe
      return s.fetcher(e, i);
    }) as typeof fetch;
    let soltar!: () => void;
    const compuerta = new Promise<void>((r) => (soltar = r));
    let lentas = 0;
    const f = (async (e: RequestInfo | URL, i?: RequestInit) => {
      const url = new URL(String(e), "http://x");
      if (url.pathname === "/v1/revisiones" && (i?.method ?? "GET") === "GET" && lentas === 1) {
        lentas += 1;
        await compuerta; // la 2.ª lectura (la de verificar) tarda
      } else if (url.pathname === "/v1/revisiones" && (i?.method ?? "GET") === "GET") lentas += 1;
      return caida(e, i);
    }) as typeof fetch;
    montar(f);
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(PAUSA_DE_CONFIRMAR);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    await screen.findByText(/No se pudo confirmar si se guardó/);
    expect(desactivado(within(t).getByRole("button", { name: "Aprobar" }))).toBe(true); // recarga en curso: bloqueada
    expect(desactivado(within(t).getByRole("button", { name: "Rechazar" }))).toBe(true);
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(PAUSA_DE_CONFIRMAR);
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    expect(posts(s)).toHaveLength(0); // el POST con corte nunca llegó al servidor falso; y no se mandó otro
    soltar();
    await waitFor(() => expect(desactivado(within(t).getByRole("button", { name: "Aprobar" }))).toBe(false));
  });

  it("si la recarga muestra que el pago YA NO está pendiente, lo dice (se guardó o lo resolvió otra persona)", async () => {
    const s = servidorDeRevisiones(2);
    const f = (async (e: RequestInfo | URL, i?: RequestInit) => {
      if ((i?.method ?? "GET") === "POST") {
        s.todas[0]!.estado = "resuelta"; // el servidor SÍ la guardó, pero la respuesta se perdió
        throw new TypeError("Failed to fetch");
      }
      return s.fetcher(e, i);
    }) as typeof fetch;
    montar(f);
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(PAUSA_DE_CONFIRMAR);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    expect(await screen.findByText(/TX1 ya no aparece como pendiente/)).toBeTruthy();
    expect(screen.queryByText(/No se pudo confirmar si se guardó/)).toBeNull();
    await screen.findByText("1 pendiente");
  });

  it("el 409 ya no culpa a «otra persona»: puede haber sido tu decisión anterior", async () => {
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    const t = await primera();
    s.todas[0]!.estado = "resuelta";
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(PAUSA_DE_CONFIRMAR);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    expect(await screen.findByText(/ya estaba resuelta/)).toBeTruthy();
    expect(screen.queryByText(/otra persona/)).toBeNull();
  });
});
