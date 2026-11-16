import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { crearCliente } from "../red/cliente";
import { Transacciones } from "../pantallas/Transacciones";
import { respuesta, servidorFalso } from "./ayudas";

function montar(fetcher: typeof fetch, alExpirar = vi.fn()) {
  render(<Transacciones cliente={crearCliente("K", { fetcher, esperas: [] })} alExpirar={alExpirar} />);
  return alExpirar;
}

beforeEach(() => window.history.replaceState(null, "", "/app"));

describe("pantalla de transacciones", () => {
  it("pide la primera página con el tamaño por omisión y la muestra", async () => {
    const s = servidorFalso(60);
    montar(s.fetcher);
    expect(await screen.findByText("AF-0001")).toBeTruthy();
    expect(s.pedidas[0]?.searchParams.get("limite")).toBe("25");
    expect(s.pedidas[0]?.searchParams.get("desplazamiento")).toBe("0");
    expect(screen.getByText("Mostrando 1–25 de 60")).toBeTruthy();
    expect(screen.getByText("Página 1 de 3")).toBeTruthy();
  });

  it("Siguiente pide el desplazamiento correcto y la URL lo recuerda", async () => {
    const s = servidorFalso(60);
    montar(s.fetcher);
    await screen.findByText("AF-0001");
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(await screen.findByText("AF-0026")).toBeTruthy();
    expect(s.pedidas.at(-1)?.searchParams.get("desplazamiento")).toBe("25");
    expect(window.location.search).toContain("pagina=2");
  });

  it("mientras llega la página siguiente la tabla NO se vacía y los botones se bloquean", async () => {
    const s = servidorFalso(60);
    let soltar!: () => void;
    const lenta = (async (e: RequestInfo | URL, i?: RequestInit) => {
      if (new URL(String(e), "http://x").searchParams.get("desplazamiento") === "25") await new Promise<void>((r) => (soltar = r));
      return s.fetcher(e, i);
    }) as typeof fetch;
    montar(lenta);
    await screen.findByText("AF-0001");
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Siguiente" }).getAttribute("aria-disabled")).toBe("true"));
    expect(screen.getByText("AF-0001")).toBeTruthy(); // sigue a la vista
    soltar();
    expect(await screen.findByText("AF-0026")).toBeTruthy();
  });

  it("al paginar el foco NO se pierde y un segundo clic durante la carga no manda otra petición", async () => {
    const s = servidorFalso(60);
    let soltar!: () => void;
    const lenta = (async (e: RequestInfo | URL, i?: RequestInit) => {
      if (new URL(String(e), "http://x").searchParams.get("desplazamiento") === "25") await new Promise<void>((r) => (soltar = r));
      return s.fetcher(e, i);
    }) as typeof fetch;
    montar(lenta);
    await screen.findByText("AF-0001");
    const siguiente = screen.getByRole("button", { name: "Siguiente" });
    siguiente.focus();
    await userEvent.click(siguiente);
    await waitFor(() => expect(siguiente.getAttribute("aria-disabled")).toBe("true"));
    expect(document.activeElement).toBe(siguiente); // con «disabled» el foco caería en el body
    const antes = s.pedidas.length;
    await userEvent.click(siguiente);
    expect(s.pedidas.length).toBe(antes);
    soltar();
    await screen.findByText("AF-0026");
    expect(document.activeElement).toBe(siguiente);
  });

  it("la corrección automática de una página fuera de rango REEMPLAZA la entrada del historial (atrás no queda atrapado)", async () => {
    window.history.replaceState(null, "", "/app?pagina=99");
    const empujar = vi.spyOn(window.history, "pushState");
    const reemplazar = vi.spyOn(window.history, "replaceState");
    montar(servidorFalso(30).fetcher);
    await screen.findByText("Página 2 de 2");
    await screen.findByText("AF-0030");
    expect(empujar).not.toHaveBeenCalled();
    expect(reemplazar).toHaveBeenCalled();
    empujar.mockRestore();
    reemplazar.mockRestore();
  });

  it("si falla al pasar de página, la barra sigue diciendo la página de las filas que se ven", async () => {
    const s = servidorFalso(60);
    let falla = false;
    const f = (async (e: RequestInfo | URL, i?: RequestInit) => (falla ? respuesta(500, { detail: "se cayó" }) : s.fetcher(e, i))) as typeof fetch;
    montar(f);
    await screen.findByText("AF-0001");
    falla = true;
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await screen.findByRole("alert");
    expect(screen.getByText("Página 1 de 3")).toBeTruthy();
    expect(screen.getByText("Mostrando 1–25 de 60")).toBeTruthy();
    expect(screen.getByText("AF-0001")).toBeTruthy();
  });

  it("«Siguiente» tras un fallo reintenta esa misma página sin llenar el historial", async () => {
    const s = servidorFalso(60);
    let falla = false;
    const f = (async (e: RequestInfo | URL, i?: RequestInit) => (falla ? respuesta(500, { detail: "se cayó" }) : s.fetcher(e, i))) as typeof fetch;
    montar(f);
    await screen.findByText("AF-0001");
    falla = true;
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await screen.findByRole("alert");
    falla = false;
    const empujar = vi.spyOn(window.history, "pushState");
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(await screen.findByText("AF-0026")).toBeTruthy();
    expect(empujar).not.toHaveBeenCalled();
    empujar.mockRestore();
  });

  it("si falla la PRIMERA carga no queda una tabla vacía: solo el aviso con «Reintentar», y reintentar funciona", async () => {
    const s = servidorFalso(10);
    let falla = true;
    const f = (async (e: RequestInfo | URL, i?: RequestInit) => (falla ? respuesta(500, { detail: "se cayó" }) : s.fetcher(e, i))) as typeof fetch;
    montar(f);
    const alerta = await screen.findByRole("alert");
    expect(screen.queryByRole("table")).toBeNull();
    falla = false;
    await userEvent.click(within(alerta).getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("AF-0001")).toBeTruthy();
  });

  it("al salir de la pantalla se cancela la petición en curso", async () => {
    let senal: AbortSignal | undefined;
    const colgada = ((_e: RequestInfo | URL, i?: RequestInit) => {
      senal = i?.signal ?? undefined;
      return new Promise<Response>(() => {});
    }) as typeof fetch;
    const { unmount } = render(<Transacciones cliente={crearCliente("K", { fetcher: colgada, esperas: [] })} alExpirar={vi.fn()} />);
    await waitFor(() => expect(senal).toBeDefined());
    unmount();
    expect(senal?.aborted).toBe(true);
  });

  it("una página más allá del final (el total bajó) vuelve a la última, sin error", async () => {
    window.history.replaceState(null, "", "/app?pagina=99");
    const s = servidorFalso(30);
    montar(s.fetcher);
    expect(await screen.findByText("Página 2 de 2")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(await screen.findByText("AF-0030")).toBeTruthy();
  });

  it("una página enorme en el enlace no pide un desplazamiento que el servidor rechace", async () => {
    window.history.replaceState(null, "", "/app?pagina=9999");
    const s = servidorFalso(60);
    montar(s.fetcher);
    await screen.findByText("Página 3 de 3");
    expect(Math.max(...s.pedidas.map((u) => Number(u.searchParams.get("desplazamiento"))))).toBeLessThanOrEqual(100000);
  });

  it("un parámetro de página basura no rompe nada", async () => {
    window.history.replaceState(null, "", "/app?pagina=abc&limite=9999&orden=drop");
    const s = servidorFalso(10);
    montar(s.fetcher);
    expect(await screen.findByText("AF-0001")).toBeTruthy();
    expect(s.pedidas[0]?.searchParams.get("limite")).toBe("25");
    expect(s.pedidas[0]?.searchParams.get("orden")).toBe("fecha_desc");
  });

  it("si falla, avisa con «Reintentar», conserva lo que había y reintentar funciona", async () => {
    const s = servidorFalso(60);
    let falla = false;
    const f = (async (e: RequestInfo | URL, i?: RequestInit) => (falla ? respuesta(500, { detail: "se cayó" }) : s.fetcher(e, i))) as typeof fetch;
    montar(f);
    await screen.findByText("AF-0001");
    falla = true;
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    const alerta = await screen.findByRole("alert");
    expect(within(alerta).getByText(/se cayó/)).toBeTruthy();
    expect(screen.getByText("AF-0001")).toBeTruthy();
    falla = false;
    await userEvent.click(within(alerta).getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("AF-0026")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("una clave vencida (401) avisa para volver a pedirla, sin mostrar el error", async () => {
    const f = (async () => respuesta(401, { detail: "no" })) as typeof fetch;
    const alExpirar = montar(f);
    await waitFor(() => expect(alExpirar).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sin pagos muestra el mensaje vacío y «Sin resultados»", async () => {
    montar(servidorFalso(0).fetcher);
    expect(await screen.findByText("No hay pagos para mostrar.")).toBeTruthy();
    expect(screen.getByText("Sin resultados")).toBeTruthy();
  });

  it("cambiar el tamaño vuelve a la primera página", async () => {
    window.history.replaceState(null, "", "/app?pagina=2");
    const s = servidorFalso(100);
    montar(s.fetcher);
    await screen.findByText("AF-0026");
    await userEvent.selectOptions(screen.getByLabelText("Por página"), "50");
    await waitFor(() => expect(s.pedidas.at(-1)?.searchParams.get("limite")).toBe("50"));
    expect(s.pedidas.at(-1)?.searchParams.get("desplazamiento")).toBe("0");
  });
});
