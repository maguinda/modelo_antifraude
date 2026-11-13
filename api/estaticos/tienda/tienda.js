(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var PRODUCTOS = [{ monto: 80000, categoria: "ROPA" }, { monto: 350000, categoria: "ELECTRONICA" }, { monto: 1400000, categoria: "ELECTRONICA" },
                   { monto: 2400000, categoria: "HOTEL" }, { monto: 4800000, categoria: "ELECTRONICA" }];
  var COP = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
  function el(tag, texto) { var e = document.createElement(tag); e.textContent = String(texto); return e; }

  AntifraudeSDK.observar($("pago"), $("tarjeta"));
  $("pago").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var boton = $("pagar"); boton.disabled = true; $("error").hidden = true;
    var p = PRODUCTOS[Number($("producto").value)];
    AntifraudeSDK.recolectar().then(function (sdk) {
      var cuerpo = { monto_cop: p.monto, categoria: p.categoria, ciudad: $("ciudad").value, metodo_pago: $("metodo").value, id_cliente: $("cliente").value.trim(),
        simulacion: { estado_dispositivo: $("estado").value || null, ip_prueba: $("ip").value, kyc_nivel: $("kyc").value,
                      edad_cuenta_dias: Number($("edad").value), autenticacion_2fa: $("tfa").value, cambio_credenciales_7d: $("cred").checked },
        sdk: sdk };
      $("senales").textContent = JSON.stringify(sdk, null, 2);
      return fetch("/demo/tienda/pagar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) });
    }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || "Error " + r.status); return j; });
    }).then(function (j) {
      var d = $("decision"); d.textContent = j.decision; d.className = "decision " + j.decision;
      var datos = $("datos"); while (datos.firstChild) datos.removeChild(datos.firstChild);
      [["Riesgo", j.puntaje_riesgo === null ? "sin puntaje (modo " + j.modo + ")" : Math.round(j.puntaje_riesgo * 100) + " %"], ["Decisión que debe aplicar la tienda", j.decision_final || "espera al analista"],
       ["Modo", j.modo], ["Datos de red", j.red_origen], ["Estado del dispositivo", j.estado_origen === "no_enviado" ? "no enviado (modelo sin estado)" : "lo mandó la tienda"], ["Modelo", j.modelo.nombre + " v" + j.modelo.version], ["Tiempo de respuesta", j.latencia_ms + " ms"], ["Pago", j.id_transaccion]].forEach(function (par) {
        datos.appendChild(el("dt", par[0])); datos.appendChild(el("dd", par[1])); });
      var ul = $("motivos"); while (ul.firstChild) ul.removeChild(ul.firstChild);
      j.reglas_activadas.forEach(function (x) { ul.appendChild(el("li", "Regla: " + x.motivo + " (" + x.accion + ")")); });
      j.motivos.forEach(function (m) { ul.appendChild(el("li", m)); });
      if (!ul.firstChild) ul.appendChild(el("li", "Sin reglas ni motivos: el modelo no vio nada raro."));
      $("resultado").hidden = false; $("resultado").scrollIntoView({ block: "nearest" });
    }).catch(function (e) { var er = $("error"); er.textContent = "No se pudo evaluar: " + e.message; er.hidden = false; }).then(function () { boton.disabled = false; });
  });
})();
