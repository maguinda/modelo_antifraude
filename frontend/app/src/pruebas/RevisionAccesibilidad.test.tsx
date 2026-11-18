// Accesibilidad de la cola de revisión: foco tras guardar, regiones de estado que se anuncian y rol del medidor de riesgo.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { crearCliente } from "../red/cliente";
import { Revision } from "../pantallas/Revision";
import { servidorDeRevisiones } from "./ayudas";

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
    expect(document.activeElement).toBe(region);
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
    expect(document.activeElement).not.toBe(screen.getByRole("status", { name: "Avisos de la cola" }));
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
