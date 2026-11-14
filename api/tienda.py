"""Tienda de prueba (SOLO en el modo demo, en tu computador).

Imita a un comercio real: el navegador manda sus señales al servidor de la tienda (esta ruta) y el servidor de la tienda
llama al servicio antifraude con su clave. La clave NUNCA llega al navegador.
La IP del comprador la manda la tienda tal cual y la API la ubica y la evalúa (con su proveedor de IP, aquí simulado).
Lo que en producción darían otros proveedores (KYC, 2FA) aquí lo simula `proveedores_simulados`.

Por defecto (modo `auto`) NADA se elige a mano: la IP es la de la conexión que llegó al servidor, el dispositivo lo detecta el SDK del navegador y lo que sabe
la tienda de su cliente (verificación, antigüedad de la cuenta, 2FA, si ya usó este equipo) sale de los registros de la tienda (aquí, `CLIENTES_DEMO`).
El modo `manual` existe solo para probar escenarios (VPN, cuenta nueva...) desde la pantalla.
"""
import ipaddress
import uuid
from typing import Any, Dict, Tuple

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from . import esquemas as es
from .documentacion import ESTATICOS
from .panel import cabecera_panel

# Conexiones de prueba que el comprador puede «tener» (IPs reservadas para documentación: las reconoce el proveedor de IP simulado)
IPS_DE_PRUEBA = {"192.0.2.10": "Casa en Bogotá (residencial)", "198.51.100.20": "Celular en Medellín (móvil)", "203.0.113.10": "Oficina en Bogotá (empresarial)",
                 "203.0.113.70": "VPN (centro de datos, EE. UU.)", "203.0.113.140": "Proxy (Países Bajos)", "203.0.113.200": "VPN desde Rusia",
                 "10.0.0.5": "Red privada, no se puede ubicar"}


# «Registros de la tienda»: lo que una tienda real sabría de sus clientes. Un código que no está aquí es un cliente NUEVO.
CLIENTES_DEMO = {"cliente-demo-1": {"kyc_nivel": "COMPLETO", "edad_cuenta_dias": 700, "autenticacion_2fa": "NO_EXIGIDA", "estado_dispositivo": "CONOCIDO"},
                 "cliente-demo-2": {"kyc_nivel": "BASICO", "edad_cuenta_dias": 90, "autenticacion_2fa": "EXIGIDA_APROBADA", "estado_dispositivo": "CONOCIDO"}}
CLIENTE_NUEVO = {"kyc_nivel": "BASICO", "edad_cuenta_dias": 3, "autenticacion_2fa": "NO_EXIGIDA", "estado_dispositivo": "NUEVO"}
IP_DE_PRUEBA_LOCAL = "192.0.2.10"          # lo que se usa cuando quien paga está en este mismo computador (su IP real, 127.0.0.1, no se puede ubicar)


def perfil_cliente(codigo: str) -> Dict[str, Any]:
    """Lo que la tienda sabe de su cliente (SIMULADO con CLIENTES_DEMO). Un código desconocido es un cliente nuevo, sin historial."""
    conocido = CLIENTES_DEMO.get(str(codigo).strip())
    return dict(conocido if conocido else CLIENTE_NUEVO, cambio_credenciales_7d=False, es_nuevo=conocido is None)


def conexion_del_comprador(request: Request) -> Tuple[str, bool]:
    """(IP, es_de_prueba). La IP real de la conexión; si es local o privada (se paga desde este mismo computador), una IP de prueba residencial.
    Solo se mira `request.client.host`: no se confía en cabeceras como X-Forwarded-For porque cualquiera las puede escribir."""
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return IP_DE_PRUEBA_LOCAL, True
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
        return IP_DE_PRUEBA_LOCAL, True
    return str(ip), False


def proveedores_simulados(sim: Dict[str, Any]) -> Dict[str, Any]:
    """SIMULADO: lo que un proveedor real de 2FA le diría a la tienda (la IP ya no se simula aquí: la evalúa la API)."""
    fallido = sim.get("autenticacion_2fa") == "EXIGIDA_FALLIDA"
    return {"intentos_fallidos_24h": 2 if fallido else 0}


def ip_del_comprador(sim: Dict[str, Any], request: Request) -> str:
    """La IP de prueba elegida, o la IP real con la que el navegador llegó a este servidor."""
    elegida = sim.get("ip_prueba")
    if elegida in IPS_DE_PRUEBA:
        return elegida
    if elegida == "REAL":
        host = request.client.host if request.client else ""
        try:
            return str(__import__("ipaddress").ip_address(host))
        except ValueError:
            return "127.0.0.1"
    raise ValueError("ip_prueba")


def armar_solicitud(c: Dict[str, Any], ip: str) -> Dict[str, Any]:
    sdk, sim = c.get("sdk") or {}, c.get("simulacion") or {}
    prov = proveedores_simulados(sim)
    return {
        "id_transaccion": "TIENDA-" + uuid.uuid4().hex[:12], "id_cliente": c.get("id_cliente"), "monto_cop": c.get("monto_cop"),
        "metodo_pago": c.get("metodo_pago"), "canal_pago": "WEB", "categoria": c.get("categoria"), "ciudad": c.get("ciudad"),
        # sin SDK (p. ej. el comprador bloqueó JavaScript) la compra sigue: queda sin señales del equipo y la API responde en modo degradado
        "dispositivo": {"tipo": "WINDOWS_PC", **(sdk.get("dispositivo") or {}), "estado": sim.get("estado_dispositivo")},
        "red": {"ip": ip},                                           # solo la IP: la API la ubica y completa el resto
        "identidad": {"kyc_nivel": sim.get("kyc_nivel"), "edad_cuenta_dias": sim.get("edad_cuenta_dias"), "autenticacion_2fa": sim.get("autenticacion_2fa"),
                      "intentos_fallidos_24h": prov["intentos_fallidos_24h"], "cambio_credenciales_7d": bool(sim.get("cambio_credenciales_7d"))},
        "comportamiento": sdk.get("comportamiento") or {},
    }


def registrar_tienda(app: FastAPI, servicio, principal, modo_csp: str = "report-only") -> None:
    html = (ESTATICOS / "tienda" / "index.html").read_text(encoding="utf-8")
    cabeceras = cabecera_panel(modo_csp)
    app.add_api_route("/tienda", lambda: HTMLResponse(html, headers=cabeceras), methods=["GET"], include_in_schema=False, response_class=HTMLResponse)

    @app.get("/demo/tienda/contexto", include_in_schema=False)
    def contexto(request: Request, cliente: str = ""):
        """Lo que la tienda ya sabe antes de cobrar (para mostrarlo en pantalla). No devuelve ninguna IP."""
        _, de_prueba = conexion_del_comprador(request)
        perfil = perfil_cliente(cliente[:40])
        return JSONResponse({"conexion": {"de_prueba": de_prueba, "descripcion": "Conexión local: se usa una IP de prueba residencial en Bogotá" if de_prueba else "Tu conexión actual (se ubica al pagar)"},
                             "cliente": {k: perfil[k] for k in ("kyc_nivel", "edad_cuenta_dias", "autenticacion_2fa", "estado_dispositivo", "es_nuevo")}},
                            headers={"Cache-Control": "no-store"})

    @app.post("/demo/tienda/pagar", include_in_schema=False)
    def pagar(request: Request, cuerpo: Dict[str, Any] = Body(...)):
        sim = cuerpo.get("simulacion") or {}
        if sim.get("modo") == "auto":                                         # nada se elige a mano: conexión real + registros de la tienda
            ip, _ = conexion_del_comprador(request)
            cuerpo = dict(cuerpo, simulacion=dict(perfil_cliente(cuerpo.get("id_cliente") or ""), ip_prueba=ip))
        else:
            try:
                ip = ip_del_comprador(sim, request)
            except ValueError:
                return JSONResponse({"detail": "Elige una de las conexiones de prueba de la lista."}, status_code=422)
        try:
            solicitud = es.SolicitudEvaluacion.model_validate(armar_solicitud(cuerpo, ip))
        except ValidationError as e:                                  # sin repetir lo enviado
            detalle = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
            return JSONResponse({"detail": "Datos inválidos: " + detalle[:300]}, status_code=422)
        return servicio.evaluar(principal, solicitud)
