// Accesibilidad de la cola de revisión: foco tras guardar, regiones de estado que se anuncian y rol del medidor de riesgo.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { crearCliente } from "../red/cliente";
import { Revision } from "../pantallas/Revision";
import { respuesta, servidorDeRevisiones } from "./ayudas";

function montar(fetcher: typeof fetch) {
  render(<Revision cliente={crearCliente("K", { fetcher, esperas: [] })} alExpirar={vi.fn()} />);
}
const esperar = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const primera = async () => (await screen.findAllByRole("listitem"))[0] as HTMLElement;

beforeEach(() => window.history.replaceState(null, "", "/app?vista=revision"));

describe("foco al resolver un pago", () => {
  it("tras guardar, el foco va a la región de avisos (no se pierde en el <body> cuando la tarjeta desaparece)", async () => {
    montar(servidorDeRevisiones(2).fetcher);
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(800);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    await screen.findByText("Pago TX1: aprobado.");
    await screen.findByText("1 pendiente"); // la lista ya se recargó y la tarjeta salió
    const region = screen.getByRole("status", { name: "Avisos de la cola" });
    await waitFor(() => expect(document.activeElement).toBe(region));
    expect(document.body.contains(t)).toBe(false);
  });

  it("si NO se guardó (error 500), el foco se queda donde estaba: en el botón, para poder reintentar", async () => {
    const s = servidorDeRevisiones(1);
    s.fallos.resolver = () => new Response(JSON.stringify({ detail: "se cayó" }), { status: 500 });
    montar(s.fetcher);
    const t = await primera();
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(800);
    const confirmar = within(t).getByRole("button", { name: "Confirmar: aprobar" });
    await userEvent.click(confirmar);
    await screen.findByText(/No se pudo guardar: se cayó/);
    // el foco sigue en EL MISMO botón que se pulsó (no cae al <body> ni salta a los avisos)
    await waitFor(() => expect(document.activeElement).toBe(confirmar));
    await waitFor(() => expect(document.activeElement).toBe(within(t).getByRole("button", { name: "Aprobar" })));
  });

  it("si otra persona ya la resolvió (409), la tarjeta desaparece al recargar y el foco va a los avisos, no al <body>", async () => {
    const s = servidorDeRevisiones(2);
    montar(s.fetcher);
    const t = await primera();
    s.todas[0]!.estado = "resuelta"; // otra persona la resolvió mientras esta pantalla la mostraba
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    await esperar(800);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    await screen.findByText(/ya estaba resuelta/);
    await screen.findByText("1 pendiente"); // la lista se recargó y la tarjeta salió
    expect(document.body.contains(t)).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("status", { name: "Avisos de la cola" })));
  });

  it("si no se sabe si se guardó (corte), el foco también va a los avisos: la tarjeta puede salir al mirar cómo quedó", async () => {
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
    await esperar(800);
    await userEvent.click(within(t).getByRole("button", { name: "Confirmar: aprobar" }));
    await screen.findByText(/TX1 ya no aparece como pendiente/);
    await screen.findByText("1 pendiente");
    expect(document.body.contains(t)).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("status", { name: "Avisos de la cola" })));
  });
});

describe("regiones que se anuncian", () => {
  it("la región de estado de la tarjeta EXISTE desde el principio (vacía) y solo cambia su contenido al armar la confirmación", async () => {
    montar(servidorDeRevisiones(1).fetcher);
    const t = await primera();
    const region = within(t).getByRole("status");
    expect(region.textContent).toBe("");
    await userEvent.click(within(t).getByRole("button", { name: "Aprobar" }));
    expect(within(t).getByRole("status")).toBe(region); // el MISMO nodo: un lector detecta el cambio de texto
    expect(region.textContent).toMatch(/Confirmar: aprobar/);
    await userEvent.click(within(t).getByRole("button", { name: "Cancelar" }));
    expect(region.textContent).toBe("");
  });

  it("el medidor de riesgo tiene rol de imagen con su nombre (un aria-label sobre un <span> sin rol no es fiable)", async () => {
    montar(servidorDeRevisiones(1).fetcher);
    const t = await primera();
    expect(within(t).getByRole("img", { name: "Riesgo 70 por ciento" })).toBeTruthy();
  });
});

describe("el foco NO se le quita a quien ya está en otra tarjeta", () => {
  /** El servidor responde a la escritura solo cuando se suelta la compuerta: da tiempo a mover el foco. */
  function conEscrituraRetenida(s: ReturnType<typeof servidorDeRevisiones>, alSoltar: "pasar" | "cortar") {
    let soltar!: () => void;
    const compuerta = new Promise<void>((r) => (soltar = r));
    const fetcher = (async (e: RequestInfo | URL, i?: RequestInit) => {
      if ((i?.method ?? "GET") === "POST") {
        await compuerta;
        if (alSoltar === "cortar") throw new TypeError("Failed to fetch");
      }
      return s.fetcher(e, i);
    }) as typeof fetch;
    return { fetcher, soltar };
  }

  /** Confirma en la tarjeta A (la escritura queda retenida) y pasa a escribir una nota en la B. */
  async function confirmarEnAYEscribirEnB(antes: () => void = () => {}) {
    const [a, b] = (await screen.findAllByRole("listitem")) as [HTMLElement, HTMLElement];
    antes(); // p. ej. otra persona resuelve el pago mientras esta pantalla lo muestra
    await userEvent.click(within(a).getByRole("button", { name: "Aprobar" }));
    await esperar(800);
    await userEvent.click(within(a).getByRole("button", { name: "Confirmar: aprobar" }));
    const notaB = within(b).getByLabelText("Nota (opcional)") as HTMLTextAreaElement;
    await userEvent.click(notaB);
    expect(document.activeElement).toBe(notaB);
    return { a, notaB };
  }

  it("éxito: si mientras tanto estás escribiendo en OTRA tarjeta, la respuesta no te roba el foco", async () => {
    const s = servidorDeRevisiones(2);
    const { fetcher, soltar } = conEscrituraRetenida(s, "pasar");
    montar(fetcher);
    const { notaB } = await confirmarEnAYEscribirEnB();
    soltar();
    await screen.findByText("Pago TX1: aprobado.");
    await screen.findByText("1 pendiente");
    await waitFor(() => expect(document.activeElement).toBe(notaB));
  });

  it("409: lo mismo, el foco se queda en la nota de la otra tarjeta", async () => {
    const s = servidorDeRevisiones(2);
    const { fetcher, soltar } = conEscrituraRetenida(s, "pasar");
    montar(fetcher);
    const { notaB } = await confirmarEnAYEscribirEnB(() => {
      s.todas[0]!.estado = "resuelta"; // otra persona ya la resolvió
    });
    soltar();
    await screen.findByText(/ya estaba resuelta/);
    await screen.findByText("1 pendiente");
    await waitFor(() => expect(document.activeElement).toBe(notaB));
  });

  it("«no se sabe si se guardó» (corte): lo mismo", async () => {
    const s = servidorDeRevisiones(2);
    const { fetcher, soltar } = conEscrituraRetenida(s, "cortar");
    montar(fetcher);
    const { notaB } = await confirmarEnAYEscribirEnB();
    soltar();
    await screen.findByText(/TX1 sigue pendiente: no se ve guardada la decisión todavía/); // el veredicto final, no un texto intermedio
    await waitFor(() => expect(document.activeElement).toBe(notaB));
  });

  it("éxito: si la OTRA tarjeta (donde estabas) también sale de la lista al recargar, el foco no se queda en el <body>: va a los avisos", async () => {
    const s = servidorDeRevisiones(2);
    const { fetcher, soltar } = conEscrituraRetenida(s, "pasar");
    montar(fetcher);
    await confirmarEnAYEscribirEnB(() => {
      s.todas[1]!.estado = "resuelta"; // otra persona resuelve la tarjeta B mientras tanto
    });
    soltar();
    await screen.findByText("Pago TX1: aprobado.");
    await screen.findByText("No hay pagos con esos filtros."); // las dos tarjetas salieron
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("status", { name: "Avisos de la cola" })));
  });

  it("409: lo mismo cuando la otra tarjeta también sale de la lista", async () => {
    const s = servidorDeRevisiones(2);
    const { fetcher, soltar } = conEscrituraRetenida(s, "pasar");
    montar(fetcher);
    await confirmarEnAYEscribirEnB(() => {
      s.todas[0]!.estado = "resuelta"; // la A ya la resolvió otra persona (409)...
      s.todas[1]!.estado = "resuelta"; // ...y la B también
    });
    soltar();
    await screen.findByText(/ya estaba resuelta/);
    await screen.findByText("No hay pagos con esos filtros.");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("status", { name: "Avisos de la cola" })));
  });

  it("la segunda comprobación es de UNA sola vez: una recarga posterior no le quita el foco a nadie", async () => {
    const s = servidorDeRevisiones(3);
    const { fetcher, soltar } = conEscrituraRetenida(s, "pasar");
    montar(fetcher);
    const { notaB } = await confirmarEnAYEscribirEnB(); // el foco se deja en la nota de B: se anota «vigilar»
    soltar();
    await screen.findByText("Pago TX1: aprobado.");
    await screen.findByText("2 pendientes");
    await waitFor(() => expect(document.activeElement).toBe(notaB)); // B sigue en la lista: no se toca el foco y se consume la anotación
    notaB.blur(); // ahora no hay foco en ningún sitio...
    const lecturasAntes = s.llamadas.filter((l) => l.url.pathname === "/v1/revisiones" && l.metodo === "GET").length;
    fireEvent.click(screen.getByRole("button", { name: "Actualizar" })); // ...y llega otra lista (sin mover el foco al botón)
    await waitFor(() => expect(s.llamadas.filter((l) => l.url.pathname === "/v1/revisiones" && l.metodo === "GET").length).toBeGreaterThan(lecturasAntes));
    await waitFor(() => expect(document.querySelector('section[aria-busy="false"]')).not.toBeNull()); // la lista nueva ya está pintada
    await act(async () => {}); // deja correr los efectos pasivos: si la anotación siguiera armada, el foco ya se habría movido
    expect(document.activeElement).toBe(document.body); // nada lo robó: la anotación era de una sola vez
  });

  it("si la recarga tras guardar FALLA, la anotación de vigilar se descarta: la siguiente lista buena no le mueve el foco a nadie", async () => {
    const s = servidorDeRevisiones(3);
    const { fetcher, soltar } = conEscrituraRetenida(s, "pasar");
    montar(fetcher);
    const { notaB } = await confirmarEnAYEscribirEnB(); // el foco queda en B: se anota «vigilar»
    s.fallos.lista = () => respuesta(500, { detail: "se cayó" }); // la recarga que sigue a guardar falla
    const lecturasAntes = s.llamadas.filter((l) => l.url.pathname === "/v1/revisiones" && l.metodo === "GET").length;
    soltar();
    await screen.findByText("Pago TX1: aprobado.");
    await waitFor(() => expect(s.llamadas.filter((l) => l.url.pathname === "/v1/revisiones" && l.metodo === "GET").length).toBeGreaterThan(lecturasAntes));
    await act(async () => {
      await esperar(50); // el error de la recarga ya se procesó
    });
    delete s.fallos.lista; // ahora el servidor responde bien
    notaB.blur();
    expect(document.activeElement).toBe(document.body);
    fireEvent.click(screen.getByRole("button", { name: "Actualizar" }));
    await waitFor(() => expect(document.querySelector('section[aria-busy="false"]')).not.toBeNull());
    await screen.findByText("2 pendientes");
    await act(async () => {});
    expect(document.activeElement).toBe(document.body); // sin el arreglo, la anotación vieja lo llevaba a los avisos
  });

  it("control: si el foco se quedó en el <body> (no está en ninguna tarjeta), SÍ va a los avisos", async () => {
    const s = servidorDeRevisiones(2);
    const { fetcher, soltar } = conEscrituraRetenida(s, "pasar");
    montar(fetcher);
    await confirmarEnAYEscribirEnB();
    (document.activeElement as HTMLElement).blur(); // ya no hay foco en ningún sitio
    expect(document.activeElement).toBe(document.body);
    soltar();
    await screen.findByText("Pago TX1: aprobado.");
    await screen.findByText("1 pendiente");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("status", { name: "Avisos de la cola" })));
  });
});
