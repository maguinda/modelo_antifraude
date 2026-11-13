"""Tienda de prueba (SOLO en el modo demo, en tu computador).

Imita a un comercio real: el navegador manda sus señales al servidor de la tienda (esta ruta) y el servidor de la tienda
llama al servicio antifraude con su clave. La clave NUNCA llega al navegador.
La IP del comprador la manda la tienda tal cual y la API la ubica y la evalúa (con su proveedor de IP, aquí simulado).
Lo que en producción darían otros proveedores (KYC, 2FA) aquí lo simula `proveedores_simulados`.
"""
import uuid
from typing import Any, Dict

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

    @app.post("/demo/tienda/pagar", include_in_schema=False)
    def pagar(request: Request, cuerpo: Dict[str, Any] = Body(...)):
        try:
            ip = ip_del_comprador(cuerpo.get("simulacion") or {}, request)
        except ValueError:
            return JSONResponse({"detail": "Elige una de las conexiones de prueba de la lista."}, status_code=422)
        try:
            solicitud = es.SolicitudEvaluacion.model_validate(armar_solicitud(cuerpo, ip))
        except ValidationError as e:                                  # sin repetir lo enviado
            detalle = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
            return JSONResponse({"detail": "Datos inválidos: " + detalle[:300]}, status_code=422)
        return servicio.evaluar(principal, solicitud)
