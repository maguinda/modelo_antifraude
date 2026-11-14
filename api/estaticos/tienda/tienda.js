(function () {
  "use strict";
  // Checkout de prueba. Lo que se escribe en tarjeta, nombre, vencimiento y CVV NUNCA se lee ni se envía: el SDK solo detecta si se PEGÓ el número.
  var $ = function (id) { return document.getElementById(id); };
  var PRODUCTOS = [{ nombre: "Camiseta", monto: 80000, categoria: "ROPA", etiqueta: "Ropa" }, { nombre: "Audífonos", monto: 350000, categoria: "ELECTRONICA", etiqueta: "Electrónica" },
                   { nombre: "Celular", monto: 1400000, categoria: "ELECTRONICA", etiqueta: "Electrónica" }, { nombre: "Reserva de hotel", monto: 2400000, categoria: "HOTEL", etiqueta: "Viajes" },
                   { nombre: "Pedido grande", monto: 4800000, categoria: "ELECTRONICA", etiqueta: "Electrónica" }];
  var TITULOS = { APROBADA: ["✓", "¡Pago aprobado!", "Tu pedido está confirmado. Te llegará un correo con los detalles."],
                  EN_REVISION: ["!", "Estamos revisando tu pago", "Un analista lo verificará en breve. No hace falta que hagas nada."],
                  RECHAZADA: ["✕", "No pudimos procesar el pago", "Prueba con otro método de pago o comunícate con tu banco."] };
  var COP = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
  function el(tag, texto) { var e = document.createElement(tag); e.textContent = String(texto); return e; }
  function vaciar(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function metodo() { var r = document.querySelector("input[name=metodo]:checked"); return r ? r.value : "VISA"; }
  function aviso(id, texto) { var e = $(id); e.textContent = texto || ""; e.hidden = !texto; }

  function pintarResumen() {
    var p = PRODUCTOS[Number($("producto").value)];
    $("nombre-producto").textContent = p.nombre; $("categoria-producto").textContent = p.etiqueta; $("ficha").textContent = p.nombre.charAt(0);
    $("precio-producto").textContent = COP.format(p.monto); $("subtotal").textContent = COP.format(p.monto); $("total").textContent = COP.format(p.monto);
    $("pagar").textContent = "Pagar " + COP.format(p.monto);
  }
  function pintarMetodo() {
    var tarjeta = metodo() === "VISA" || metodo() === "MASTERCARD";
    $("bloque-tarjeta").hidden = !tarjeta; $("bloque-otro").hidden = tarjeta;
  }
  $("producto").addEventListener("change", pintarResumen);
  Array.prototype.forEach.call(document.querySelectorAll("input[name=metodo]"), function (r) { r.addEventListener("change", pintarMetodo); });
  pintarResumen(); pintarMetodo();

  // ---- lo que se detecta solo (nada de esto se elige a mano)
  var CTX = { dispositivo: null, cliente: null, conexion: null };
  function pintarContexto() {
    var dl = $("contexto"); vaciar(dl);
    var d = CTX.dispositivo, c = CTX.cliente, x = CTX.conexion;
    var filas = [["Conexión", x ? x.descripcion : "Detectando…"],
      ["Dispositivo", d ? d.tipo + " · " + d.sistema_operativo + " · " + d.navegador : "Detectando…"],
      ["Pantalla y zona horaria", d ? (d.resolucion_pantalla || "—") + " · " + (d.zona_horaria || "—") : "Detectando…"],
      ["Cliente", c ? (c.es_nuevo ? "Cliente nuevo para la tienda" : "Cliente registrado") : "Consultando…"],
      ["Verificación (KYC)", c ? ({ COMPLETO: "Completa", BASICO: "Básica", SIN_VERIFICAR: "Sin verificar" }[c.kyc_nivel] || c.kyc_nivel) : "—"],
      ["Antigüedad de la cuenta", c ? c.edad_cuenta_dias + " días" : "—"],
      ["Doble factor (2FA)", c ? ({ NO_EXIGIDA: "No exigido", EXIGIDA_APROBADA: "Exigido y aprobado", EXIGIDA_FALLIDA: "Exigido y fallido", NO_DISPONIBLE: "No disponible" }[c.autenticacion_2fa] || c.autenticacion_2fa) : "—"],
      ["¿Ya usó este equipo?", c ? ({ CONOCIDO: "Sí", NUEVO: "No, es nuevo", NO_RECONOCIDO: "No reconocido" }[c.estado_dispositivo] || c.estado_dispositivo) : "—"]];
    filas.forEach(function (f) { dl.appendChild(el("dt", f[0])); dl.appendChild(el("dd", f[1])); });
  }
  function consultarContexto() {
    fetch("/demo/tienda/contexto?cliente=" + encodeURIComponent($("cliente").value.trim()), { headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); }).then(function (j) { CTX.cliente = j.cliente; CTX.conexion = j.conexion; pintarContexto(); })
      .catch(function () { CTX.cliente = null; pintarContexto(); });
  }
  AntifraudeSDK.recolectar().then(function (s) { CTX.dispositivo = s.dispositivo; pintarContexto(); }).catch(function () { /* sin SDK la tienda sigue: se evalúa sin esas señales */ });
  pintarContexto(); consultarContexto();
  $("cliente").addEventListener("change", consultarContexto);
  $("manual").addEventListener("change", function () { $("escenario").hidden = !$("manual").checked; });

  AntifraudeSDK.observar($("pago"), $("tarjeta"));
  $("pago").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var edad = $("edad").value.trim(), cliente = $("cliente").value.trim(), hayError = false;
    aviso("error-cliente", cliente ? "" : "Escribe el código del cliente."); if (!cliente) hayError = true;
    var n = Number(edad), manual = $("manual").checked;
    aviso("error-edad", !manual || (edad !== "" && n >= 0 && n <= 36500 && Math.floor(n) === n) ? "" : "Escribe un número de días entre 0 y 36500."); if ($("error-edad").textContent) hayError = true;
    if (hayError) { (cliente ? $("edad") : $("cliente")).focus(); return; }
    var boton = $("pagar"); boton.disabled = true; $("error").hidden = true;
    var p = PRODUCTOS[Number($("producto").value)];
    AntifraudeSDK.recolectar().then(function (sdk) {
      var cuerpo = { monto_cop: p.monto, categoria: p.categoria, ciudad: $("ciudad").value, metodo_pago: metodo(), id_cliente: cliente,
        simulacion: manual ? { modo: "manual", estado_dispositivo: $("estado").value || null, ip_prueba: $("ip").value, kyc_nivel: $("kyc").value,
                               edad_cuenta_dias: n, autenticacion_2fa: $("tfa").value, cambio_credenciales_7d: $("cred").checked } : { modo: "auto" },
        sdk: sdk };
      $("senales").textContent = JSON.stringify(sdk, null, 2);
      return fetch("/demo/tienda/pagar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) });
    }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || "Error " + r.status); return j; });
    }).then(function (j) {
      var t = TITULOS[j.decision] || ["?", j.decision, ""];
      var d = $("decision"); d.textContent = j.decision; d.className = "estado " + j.decision;
      var icono = $("icono-resultado"); icono.textContent = t[0]; icono.className = "icono-resultado " + j.decision;
      $("titulo-resultado").textContent = t[1]; $("texto-resultado").textContent = t[2];
      $("seguimiento").textContent = j.id_seguimiento; $("enlace-detalle").href = "/dashboard#/pago/" + encodeURIComponent(j.id_seguimiento);
      var datos = $("datos"); vaciar(datos);
      [["Riesgo", j.puntaje_riesgo === null ? "sin puntaje (modo " + j.modo + ")" : Math.round(j.puntaje_riesgo * 100) + " %"], ["Decisión que debe aplicar la tienda", j.decision_final || "espera al analista"],
       ["Modo", j.modo], ["Datos de red", j.red_origen], ["Estado del dispositivo", j.estado_origen === "no_enviado" ? "no enviado (modelo sin estado)" : "lo mandó la tienda"], ["Modelo", j.modelo.nombre + " v" + j.modelo.version], ["Tiempo de respuesta", j.latencia_ms + " ms"], ["Pago", j.id_transaccion]].forEach(function (par) {
        datos.appendChild(el("dt", par[0])); datos.appendChild(el("dd", par[1])); });
      var ul = $("motivos"); vaciar(ul);
      j.reglas_activadas.forEach(function (x) { ul.appendChild(el("li", "Regla: " + x.motivo + " (" + x.accion + ")")); });
      j.motivos.forEach(function (m) { ul.appendChild(el("li", m)); });
      if (!ul.firstChild) ul.appendChild(el("li", "Sin reglas ni motivos: el modelo no vio nada raro."));
      $("etapa-pago").className = "hecho"; $("etapa-pago").removeAttribute("aria-current"); $("etapa-fin").className = "activo"; $("etapa-fin").setAttribute("aria-current", "step");
      $("resultado").hidden = false; $("resultado").scrollIntoView({ block: "start" });
    }).catch(function (e) { var er = $("error"); er.textContent = "No se pudo evaluar: " + e.message; er.hidden = false; }).then(function () { boton.disabled = false; });
  });
})();
