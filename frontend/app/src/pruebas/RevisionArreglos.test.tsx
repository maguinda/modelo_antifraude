// Arreglos de la cola de revisión tras la revisión del PR #59: doble toque, avisos que no se limpian y resultado desconocido al escribir.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    let intentos = 0; // escrituras que SALIERON del navegador (se cuentan aquí: el corte nunca llega al servidor falso)
    const caida = (async (e: RequestInfo | URL, i?: RequestInit) => {
      if ((i?.method ?? "GET") === "POST") {
        intentos += 1;
        throw new TypeError("Failed to fetch"); // corte: no se sabe
      }
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
    expect(intentos).toBe(1); // ningún intento más salió de la tarjeta bloqueada
    expect(within(t).queryByRole("button", { name: "Confirmar: aprobar" })).toBeNull(); // ni siquiera dejó armar la confirmación
    soltar();
    await waitFor(() => expect(desactivado(within(t).getByRole("button", { name: "Aprobar" }))).toBe(false));
    expect(await screen.findByText(/TX1 sigue pendiente: no se ve guardada la decisión todavía/)).toBeTruthy();
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

describe("4. el veredicto de «no se sabe» solo vale para la MISMA consulta", () => {
  /** Un servidor cuya escritura da 504 sin guardar y cuya lectura n.º `retenerDesde` tarda hasta que se suelte. */
  function conResolverCaido() {
    const s = servidorDeRevisiones(2, [{ estado: "resuelta" }]);
    s.fallos.resolver = () => respuesta(504, { detail: "proxy" });
    return s;
  }

  it("si cambias de filtro con la tarjeta bloqueada, NO se afirma «se guardó»; al volver, se mira y se dice cómo quedó", async () => {
    const s = conResolverCaido();
    let soltar!: () => void;
    const compuerta = new Promise<void>((r) => (soltar = r));
    let hubo504 = false;
    let retenida = false;
    const f = (async (e: RequestInfo | URL, i?: RequestInit) => {
      const url = new URL(String(e), "http://x");
      const metodo = i?.method ?? "GET";
      if (metodo === "POST") hubo504 = true;
      if (hubo504 && !retenida && url.pathname === "/v1/revisiones" && metodo === "GET") {
        retenida = true;
        await compuerta; // la PRIMERA lectura después de la escritura (la de verificación) tarda
      }
      return s.fetcher(e, i);
    }) as typeof fetch;
    montar(f);
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(PAUSA_DE_CONFIRMAR);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    await screen.findByText(/No se pudo confirmar si se guardó/);
    // antes de que llegue la verificación, cambia a «Resueltas»: ese pago no está ahí, y eso NO significa que se guardó
    await userEvent.selectOptions(screen.getByLabelText("Mostrar"), "resuelta");
    await screen.findByText("1 resuelta");
    expect(screen.queryByText(/ya no aparece como pendiente/)).toBeNull();
    expect(screen.queryByText(/se guardó una decisión/)).toBeNull();
    // al volver a «Pendientes» se mira de verdad: el servidor NO lo guardó, sigue pendiente
    await userEvent.selectOptions(screen.getByLabelText("Mostrar"), "pendiente");
    expect(await screen.findByText(/TX1 sigue pendiente: no se ve guardada la decisión todavía/)).toBeTruthy();
    expect(desactivado(within((await screen.findAllByRole("listitem"))[0] as HTMLElement).getByRole("button", { name: "Aprobar" }))).toBe(false);
    soltar();
  });

  it("si la recarga falla, la tarjeta sigue bloqueada, y «Actualizar» la desbloquea", async () => {
    const s = conResolverCaido();
    montar(s.fetcher);
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(PAUSA_DE_CONFIRMAR);
    s.fallos.lista = () => respuesta(500, { detail: "se cayó" }); // la verificación fallará
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    await screen.findByText(/No se pudo cargar: se cayó/);
    expect(desactivado(within(t).getByRole("button", { name: "Aprobar" }))).toBe(true);
    delete s.fallos.lista;
    await userEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    await waitFor(() => expect(desactivado(within(t).getByRole("button", { name: "Aprobar" }))).toBe(false));
  });
});

describe("5. accesibilidad de la pausa de confirmación", () => {
  it("durante la pausa «Confirmar» se anuncia como no disponible, y después sí", async () => {
    montar(servidorDeRevisiones(1).fetcher);
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    expect(desactivado(within(t).getByRole("button", { name: "Confirmar: aprobar" }))).toBe(true);
    await waitFor(() => expect(desactivado(within(t).getByRole("button", { name: "Confirmar: aprobar" }))).toBe(false), { timeout: 2000 });
  });

  it("una tecla Enter mantenida (auto-repetición) no activa los botones", async () => {
    montar(servidorDeRevisiones(1).fetcher);
    const t = await primera();
    const boton = within(t).getByRole("button", { name: "Aprobar" });
    expect(fireEvent.keyDown(boton, { key: "Enter", repeat: true })).toBe(false); // se cancela la repetición
    expect(fireEvent.keyDown(boton, { key: "Enter", repeat: false })).toBe(true); // una pulsación normal no se toca
  });
});
