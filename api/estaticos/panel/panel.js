(function () {
  "use strict";
  // Todo el texto que viene de la API se muestra con textContent: nunca como HTML.
  var CFG = window.PANEL_CONFIG || {};
  var clave = null, temporizador = null, mostrados = 0;
  var $ = function (id) { return document.getElementById(id); };
  var ETIQUETAS = { fecha: "Fecha del pago", id_cliente: "Cliente", monto_cop: "Monto", metodo_pago: "Método de pago", canal_pago: "Canal",
    categoria: "Categoría", localizacion: "Ciudad", dispositivo: "Dispositivo", estado_dispositivo: "Estado del dispositivo", tipo_ip: "Tipo de IP",
    ip_pais: "País de la IP", kyc_nivel: "Verificación (KYC)", edad_cuenta_dias: "Antigüedad de la cuenta (días)", autenticacion_2fa: "2FA",
    transacciones_ultimas_24h: "Compras en 24 h", es_emulador: "Emulador" };
  var COP = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });

  function el(tag, clase, texto) {
    var e = document.createElement(tag);
    if (clase) e.className = clase;
    if (texto !== undefined && texto !== null) e.textContent = String(texto);
    return e;
  }
  function api(ruta, metodo, cuerpo) {
    var cab = { "X-API-Key": clave };
    if (cuerpo) cab["Content-Type"] = "application/json";
    return fetch(ruta, { method: metodo || "GET", headers: cab, body: cuerpo ? JSON.stringify(cuerpo) : undefined }).then(function (r) {
      return r.text().then(function (t) {
        var j = null;
        try { j = t ? JSON.parse(t) : null; } catch (e) { j = null; }
        if (!r.ok) { var err = new Error((j && j.detail) || ("Error " + r.status)); err.status = r.status; throw err; }
        return j;
      });
    });
  }
  function aviso(texto, esError) {
    var a = $("mensaje");
    a.textContent = texto || "";
    a.hidden = !texto;
    a.className = esError ? "aviso error" : "aviso";
  }
  function hace(iso) {
    var min = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
    if (min < 1) return "hace menos de 1 min";
    if (min < 60) return "hace " + min + " min";
    return "hace " + Math.floor(min / 60) + " h " + (min % 60) + " min";
  }
  function valor(k, v) {
    if (k === "monto_cop") return COP.format(v);
    if (typeof v === "boolean" || k === "es_emulador") return v ? "Sí" : "No";
    return String(v);
  }

  function tarjeta(rev) {
    var li = el("li");
    var d = el("details", "item");
    var s = el("summary");
    var f1 = el("div", "fila1");
    f1.appendChild(el("span", "monto", COP.format((rev.resumen_pago || {}).monto_cop || 0)));
    var p = rev.puntaje_riesgo;
    var pct = p === null || p === undefined ? null : Math.round(p * 100);
    var r = el("span", "riesgo " + (pct === null ? "" : pct >= 60 ? "alto" : pct >= 35 ? "medio" : "bajo"), pct === null ? "Sin puntaje (modo degradado)" : "Riesgo " + pct + " %");
    f1.appendChild(r);
    s.appendChild(f1);
    if (pct !== null) { var b = el("div", "barra-riesgo"); var i = el("span"); i.style.width = pct + "%"; b.appendChild(i); s.appendChild(b); }
    s.appendChild(el("div", "meta", rev.id_transaccion + " · comercio " + rev.comercio_id + " · " + hace(rev.creada_en)));
    d.appendChild(s);

    var det = el("div", "detalle");
    if (rev.motivos && rev.motivos.length) { var h = el("div"); h.appendChild(el("h3", null, "Motivos")); var ul = el("ul"); rev.motivos.forEach(function (m) { ul.appendChild(el("li", null, m)); }); h.appendChild(ul); det.appendChild(h); }
    if (rev.reglas_activadas && rev.reglas_activadas.length) { var h2 = el("div"); h2.appendChild(el("h3", null, "Reglas activadas")); var ul2 = el("ul"); rev.reglas_activadas.forEach(function (x) { ul2.appendChild(el("li", null, x.motivo + " (" + x.accion + ")")); }); h2.appendChild(ul2); det.appendChild(h2); }
    var h3 = el("div"); h3.appendChild(el("h3", null, "Datos del pago"));
    var dl = el("dl", "datos");
    Object.keys(ETIQUETAS).forEach(function (k) { var v = (rev.resumen_pago || {})[k]; if (v !== undefined && v !== null) { dl.appendChild(el("dt", null, ETIQUETAS[k])); dl.appendChild(el("dd", null, valor(k, v))); } });
    h3.appendChild(dl); det.appendChild(h3);

    if (rev.estado === "pendiente") {
      var idNota = "nota-" + rev.id_evaluacion;
      var bloque = el("div");
      var lab = el("label", null, "Nota (opcional)"); lab.setAttribute("for", idNota);
      var nota = el("textarea"); nota.id = idNota; nota.maxLength = 500;
      bloque.appendChild(lab); bloque.appendChild(nota); det.appendChild(bloque);
      var botones = el("div", "botones");
      var bA = el("button", "ok", "Aprobar"), bR = el("button", "mal", "Rechazar");
      bA.type = "button"; bR.type = "button";
      var pendiente = null;
      function reset() { pendiente = null; bA.textContent = "Aprobar"; bR.textContent = "Rechazar"; bA.className = "ok"; bR.className = "mal"; bA.disabled = bR.disabled = false; }
      function pulsar(decision, boton, otro, etiqueta, clase) {
        if (pendiente !== decision) { reset(); pendiente = decision; boton.textContent = "Confirmar: " + etiqueta; boton.className = clase + " confirmar"; otro.textContent = "Cancelar"; otro.className = ""; return; }
        bA.disabled = bR.disabled = true;
        api("/v1/revisiones/" + encodeURIComponent(rev.id_evaluacion) + "/resolver", "POST", { decision: decision, nota: nota.value || null })
          .then(function () { aviso("Pago " + rev.id_transaccion + ": " + (decision === "APROBADA" ? "aprobado" : "rechazado") + "."); return cargar(); })
          .catch(function (e) { reset(); if (e.status === 409) { aviso("Esa revisión ya la resolvió otra persona. Actualizando la lista.", true); cargar(); } else if (e.status === 401 || e.status === 403) { salir("Tu clave ya no sirve."); } else { aviso("No se pudo guardar: " + e.message, true); } });
      }
      bA.addEventListener("click", function () { if (pendiente === "RECHAZADA") { reset(); return; } pulsar("APROBADA", bA, bR, "aprobar", "ok"); });
      bR.addEventListener("click", function () { if (pendiente === "APROBADA") { reset(); return; } pulsar("RECHAZADA", bR, bA, "rechazar", "mal"); });
      botones.appendChild(bA); botones.appendChild(bR); det.appendChild(botones);
    } else if (rev.revision) { det.appendChild(el("p", null, "Resuelta.")); }
    d.appendChild(det); li.appendChild(d);
    return li;
  }

  function cargar() {
    var estado = $("estado").value;
    return api("/v1/revisiones?estado=" + encodeURIComponent(estado) + "&limite=100").then(function (r) {
      var lista = $("lista");
      while (lista.firstChild) lista.removeChild(lista.firstChild);
      r.revisiones.forEach(function (rev) { lista.appendChild(tarjeta(rev)); });
      mostrados = r.total;
      $("vacio").hidden = r.revisiones.length > 0;
      $("contador").textContent = estado === "pendiente" ? r.total + " pendiente" + (r.total === 1 ? "" : "s") : r.total + " resuelta" + (r.total === 1 ? "" : "s");
    }).catch(function (e) { if (e.status === 401 || e.status === 403) salir("Tu clave ya no sirve."); else aviso("No se pudo cargar: " + e.message, true); });
  }
  function vigilar() {   // solo cuenta; no vuelve a dibujar la lista (así no se pierde lo que estás escribiendo)
    api("/v1/revisiones?estado=pendiente&limite=1").then(function (r) {
      if ($("estado").value === "pendiente" && r.total !== mostrados) aviso("Cambió la lista (" + r.total + " pendientes). Pulsa Actualizar.");
    }).catch(function () {});
  }
  function entrar(c) {
    clave = c;
    return api("/v1/revisiones?estado=pendiente&limite=1").then(function () {
      try { sessionStorage.setItem("clave_analista", c); } catch (e) {}
      $("acceso").hidden = true; $("panel").hidden = false; $("actualizar").hidden = false; $("salir").hidden = false; $("errorAcceso").hidden = true;
      $("clave").value = "";
      clearInterval(temporizador); temporizador = setInterval(vigilar, 20000);
      return cargar();
    }).catch(function (e) {
      clave = null;
      var t = $("errorAcceso");
      t.textContent = e.status === 401 ? "Esa clave no es válida." : e.status === 403 ? "Esa clave no es de analista." : "No se pudo entrar: " + e.message;
      t.hidden = false;
    });
  }
  function salir(texto) {
    clave = null; clearInterval(temporizador);
    try { sessionStorage.removeItem("clave_analista"); } catch (e) {}
    $("panel").hidden = true; $("acceso").hidden = false; $("actualizar").hidden = true; $("salir").hidden = true; $("contador").textContent = "";
    var t = $("errorAcceso"); t.textContent = texto || ""; t.hidden = !texto;
  }

  $("formAcceso").addEventListener("submit", function (ev) { ev.preventDefault(); entrar($("clave").value.trim()); });
  $("actualizar").addEventListener("click", function () { aviso(""); cargar(); });
  $("estado").addEventListener("change", function () { aviso(""); cargar(); });
  $("salir").addEventListener("click", function () { aviso(""); salir(""); });
  var guardada = null;
  try { guardada = sessionStorage.getItem("clave_analista"); } catch (e) {}
  var inicial = CFG.claveDemo || guardada;
  if (inicial) entrar(inicial);
})();
