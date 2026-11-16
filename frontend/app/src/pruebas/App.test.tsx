import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { respuesta, servidorDeRevisiones } from "./ayudas";

let servidor: ReturnType<typeof servidorDeRevisiones>;
let estadoClave: number;

beforeEach(() => {
  window.history.replaceState(null, "", "/app");
  servidor = servidorDeRevisiones(2);
  estadoClave = 200;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (e: RequestInfo | URL, i?: RequestInit) => {
      const url = new URL(String(e), "http://x");
      if (url.pathname === "/v1/revisiones" && estadoClave !== 200) return respuesta(estadoClave, { detail: "x" });
      if (url.pathname === "/v1/panel/transacciones") return respuesta(200, { total: 0, transacciones: [] });
      return servidor.fetcher(e, i);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

async function entrar(clave = "una-clave") {
  await userEvent.type(screen.getByLabelText("Clave de analista"), clave);
  await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
}

describe("App", () => {
  it("una clave inválida (401) se dice en el acceso, con el mismo texto del panel actual", async () => {
    estadoClave = 401;
    render(<App />);
    await entrar();
    expect(await screen.findByText("Esa clave no es válida.")).toBeTruthy();
    expect(screen.getByLabelText("Clave de analista")).toBeTruthy();
  });

  it("una clave que no es de analista (403) también", async () => {
    estadoClave = 403;
    render(<App />);
    await entrar();
    expect(await screen.findByText("Esa clave no es de analista.")).toBeTruthy();
  });

  it("una clave buena entra, muestra la sesión y la pantalla de transacciones; «Salir» vuelve al acceso", async () => {
    render(<App />);
    await entrar();
    expect(await screen.findByText("Sesión de Ana")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Transacciones" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Salir" }));
    expect(screen.getByLabelText("Clave de analista")).toBeTruthy();
  });

  it("la navegación cambia de pantalla y limpia los parámetros de la anterior", async () => {
    window.history.replaceState(null, "", "/app?pagina=3&orden=monto_desc");
    render(<App />);
    await entrar();
    await userEvent.click(await screen.findByRole("link", { name: "Revisión manual" }));
    expect(await screen.findByText("Pagos")).toBeTruthy();
    expect(window.location.search).toBe("?vista=revision");
    expect(screen.getByRole("link", { name: "Revisión manual" }).getAttribute("aria-current")).toBe("page");
    await userEvent.click(screen.getByRole("link", { name: "Transacciones" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Transacciones" })).toBeTruthy());
    expect(window.location.search).toBe("");
  });

  it("la clave nunca se guarda en el almacenamiento del navegador ni en la dirección", async () => {
    render(<App />);
    await entrar("clave-secreta-123");
    await screen.findByText("Sesión de Ana");
    expect(JSON.stringify({ ...localStorage })).not.toContain("clave-secreta-123");
    expect(JSON.stringify({ ...sessionStorage })).not.toContain("clave-secreta-123");
    expect(window.location.href).not.toContain("clave-secreta-123");
  });
});
