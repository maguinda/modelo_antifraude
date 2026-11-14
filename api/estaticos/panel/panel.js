(function () {
  "use strict";
  // Revisión manual. Todo el texto que viene de la API se muestra con textContent: nunca como HTML.
  var CFG = window.PANEL_CONFIG || {};
  var clave = null, temporizador = null, mostrados = 0, pagina = 0, ultimaCuenta = 0;
  var POR_PAGINA = 25;
  var $ = function (id) { return document.getElementById(id); };
  var ETIQUETAS = { fecha: "Fecha del pago", id_cliente: "Cliente", monto_cop: "Monto", metodo_pago: "Método de pago", canal_pago: "Canal",
    categoria: "Categoría", localizacion: "Ciudad", dispositivo: "Dispositivo", estado_dispositivo: "Estado del dispositivo", tipo_ip: "Tipo de IP",
    ip_pais: "País de la IP", kyc_nivel: "Verificación (KYC)", edad_cuenta_dias: "Antigüedad de la cuenta (días)", autenticacion_2fa: "2FA",
    transacciones_ultimas_24h: "Compras en 24 h", es_emulador: "Emulador" };
  var COP = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
  var COPc = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", notation: "compact", maximumFractionDigits: 1 });
  var NUM = new Intl.NumberFormat("es-CO");

  function el(tag, clase, texto) {
    var e = document.createElement(tag);
    if (clase) e.className = clase;
    if (texto !== undefined && texto !== null) e.textContent = String(texto);
    return e;
  }
  function add(padre) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) padre.appendChild(arguments[i]); return padre; }
  function consulta(params) {
    var q = Object.keys(params).filter(function (k) { return params[k] !== "" && params[k] !== null && params[k] !== undefined; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]); }).join("&");
    return q ? "?" + q : "";
  }
  function api(ruta, metodo, cuerpo) {
    var cab = { "X-API-Key": clave };
    if (cuerpo) cab["Content-Type"] = "application/json";
    return fetch(ruta, { method: metodo || "GET", headers: cab, body: cuerpo ? JSON.stringify(cuerpo) : undefined }).then(function (r) {
      return r.text().then(function (t) {
        var j = null;
        try { j = t ? JSON.parse(t) : null; } catch (e) { j = null; }
        if (!r.ok) { var err = new Error((j && (j.detail || j.title)) || ("Error " + r.status)); err.status = r.status; throw err; }
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
    if (min < 1440) return "hace " + Math.floor(min / 60) + " h " + (min % 60) + " min";
    return "hace " + Math.floor(min / 1440) + " d";
  }
  function valor(k, v) {
    if (k === "monto_cop") return COP.format(v);
    if (k === "fecha") { var f = new Date(v); return isNaN(f) ? String(v) : f.toLocaleString("es-CO", { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }); }
    if (typeof v === "boolean" || k === "es_emulador") return v ? "Sí" : "No";
    return String(v);
  }
  function nivel(pct) { return pct === null ? "" : pct >= 60 ? "alto" : pct >= 35 ? "medio" : "bajo"; }

  // ---------------------------------------------------------------- filtros
  function parametros() {
    var p = { estado: $("estado").value, limite: POR_PAGINA, desplazamiento: pagina * POR_PAGINA, orden: $("orden").value };
    if ($("q").value.trim()) p.q = $("q").value.trim();
    if ($("comercio").value) p.comercio_id = $("comercio").value;
    if ($("monto_min").value !== "") p.monto_min = $("monto_min").value;
    if ($("desde").value) p.desde = $("desde").value;
    if ($("hasta").value) p.hasta = $("hasta").value;
    var r = $("riesgo").value;
    if (r === "alto") p.riesgo_min = 0.6; else if (r === "medio") { p.riesgo_min = 0.35; p.riesgo_max = 0.5999; } else if (r === "bajo") p.riesgo_max = 0.3499;
    return p;
  }
  function validar() {
    var ok = true, m = $("monto_min").value, d = $("desde").value, h = $("hasta").value;
    var em = m !== "" && !(Number(m) >= 0) ? "Escribe un número igual o mayor que 0." : "";
    var ef = d && h && d > h ? "«Desde» no puede ser posterior a «Hasta»." : "";
    [["error-monto", em], ["error-fechas", ef]].forEach(function (x) { $(x[0]).textContent = x[1]; $(x[0]).hidden = !x[1]; if (x[1]) ok = false; });
    if (!ok) (em ? $("monto_min") : $("hasta")).focus();
    return ok;
  }
  function hayFiltros() { return !!($("q").value.trim() || $("comercio").value || $("monto_min").value !== "" || $("desde").value || $("hasta").value || $("riesgo").value); }
  function limpiar() {
    ["q", "monto_min", "desde", "hasta"].forEach(function (id) { $(id).value = ""; });
    $("comercio").value = ""; $("riesgo").value = ""; $("orden").value = "antiguos"; $("error-monto").hidden = true; $("error-fechas").hidden = true;
  }

  // ---------------------------------------------------------------- una fila de la cola
  function tarjeta(rev) {
    var li = el("li"), d = el("details", "item"), s = el("summary");
    var p = rev.puntaje_riesgo, pct = p === null || p === undefined ? null : Math.round(p * 100);
    var fila = el("div", "fila-cola");
    fila.appendChild(el("span", "medidor-riesgo " + nivel(pct), pct === null ? "—" : pct + "%"));
    var cuerpo = el("div", "cuerpo-cola"), l1 = el("div", "linea1");
    add(l1, el("span", "monto", COP.format((rev.resumen_pago || {}).monto_cop || 0)), el("span", "chip", rev.comercio || ("Comercio " + rev.comercio_id)),
      pct === null ? el("span", "chip falta", "Sin puntaje (modo degradado)") : null);
    var meta = (rev.id_seguimiento ? rev.id_seguimiento + " · " : "") + rev.id_transaccion + " · " + ((rev.resumen_pago || {}).id_cliente || "") + " · " + hace(rev.creada_en);
    cuerpo.appendChild(l1); cuerpo.appendChild(el("div", "meta", meta));
    if (rev.reglas_activadas && rev.reglas_activadas.length) {
      var rg = el("div", "reglas");
      rev.reglas_activadas.forEach(function (x) { rg.appendChild(el("span", "chip regla", x.regla)); });
      cuerpo.appendChild(rg);
    }
    add(fila, cuerpo, el("span", "abrir", "›"));
    s.appendChild(fila); d.appendChild(s);

    var det = el("div", "detalle"), izq = el("div", "col-izq");
    var porque = el("section"); porque.appendChild(el("h3", null, "Por qué dudó"));
    var hay = window.AntifraudeSenales.pintar(porque, rev.senales, rev.puntaje_riesgo);       // lo que más pesó, en palabras
    (rev.motivos || []).forEach(function (m) { porque.appendChild(add(el("div", "motivo"), el("strong", null, m))); hay = true; });
    (rev.reglas_activadas || []).forEach(function (x) { porque.appendChild(add(el("div", "motivo"), el("strong", null, x.regla), el("p", null, x.motivo + " (" + x.accion + ")"))); hay = true; });
    if (!hay) porque.appendChild(el("p", "meta", "El modelo no vio una causa concreta: la probabilidad quedó en la zona de duda."));
    izq.appendChild(porque);
    var datos = el("section"); datos.appendChild(el("h3", null, "Datos del pago"));
    var dl = el("dl", "rejilla-datos");
    Object.keys(ETIQUETAS).forEach(function (k) {
      var v = (rev.resumen_pago || {})[k];
      if (v !== undefined && v !== null) dl.appendChild(add(el("div", "dato"), el("dt", "et", ETIQUETAS[k]), el("dd", null, valor(k, v))));
    });
    datos.appendChild(dl); izq.appendChild(datos); det.appendChild(izq);

    var der = el("section", "decision");
    der.appendChild(el("h3", null, "Tu decisión"));
    if (rev.estado === "pendiente") {
      var idNota = "nota-" + rev.id_evaluacion;
      var lab = el("label", null, "Nota (opcional)"); lab.setAttribute("for", idNota);
      var nota = el("textarea"); nota.id = idNota; nota.maxLength = 500;
      add(der, lab, nota);
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
      add(botones, bA, bR); der.appendChild(botones);
    } else {
      var quien = rev.resuelta_por ? "Resuelta por " + rev.resuelta_por + (rev.resuelta_en ? " · " + hace(rev.resuelta_en) : "") + "." : "Este pago ya fue resuelto.";
      der.appendChild(el("p", "meta", quien));
    }
    if (rev.id_seguimiento) { var ver = el("a", "btn suave", "Ver todo en el dashboard →"); ver.href = "/dashboard#/pago/" + encodeURIComponent(rev.id_seguimiento); der.appendChild(ver); }
    det.appendChild(der); d.appendChild(det); li.appendChild(d);
    return li;
  }

  // ---------------------------------------------------------------- lista, indicadores y páginas
  function indicadores(r) {
    var k = $("kpis"); while (k.firstChild) k.removeChild(k.firstChild);
    var altos = r.revisiones.filter(function (x) { return x.puntaje_riesgo !== null && x.puntaje_riesgo >= 0.6; }).length;
    var masAntigua = r.revisiones.reduce(function (a, x) { return a === null || Date.parse(x.creada_en) < Date.parse(a) ? x.creada_en : a; }, null);
    var pend = $("estado").value === "pendiente";
    [["", pend ? "Por revisar" : "Resueltas", NUM.format(r.total), hayFiltros() ? "con estos filtros" : "en total"],
     ["", "Monto en la cola", COPc.format(r.monto_total_cop || 0), COP.format(r.monto_total_cop || 0)],
     ["oscuro", "Riesgo alto", NUM.format(altos), "en esta página"],
     ["verde", pend ? "La que más espera" : "Última resuelta", masAntigua ? hace(masAntigua).replace("hace ", "") : "—", "en esta página"]].forEach(function (x) {
      add(k, add(el("div", "kpi " + x[0]), el("span", "etiqueta", x[1]), el("span", "valor", x[2]), el("span", "nota", x[3])));
    });
  }
  function cargar() {
    return api("/v1/revisiones" + consulta(parametros())).then(function (r) {
      var lista = $("lista");
      while (lista.firstChild) lista.removeChild(lista.firstChild);
      r.revisiones.forEach(function (rev) { lista.appendChild(tarjeta(rev)); });
      mostrados = r.total; ultimaCuenta = r.revisiones.length;
      $("vacio").hidden = r.revisiones.length > 0;
      var estado = $("estado").value;
      $("contador").textContent = estado === "pendiente" ? r.total + " pendiente" + (r.total === 1 ? "" : "s") : r.total + " resuelta" + (r.total === 1 ? "" : "s");
      indicadores(r);
      var paginas = Math.max(1, Math.ceil(r.total / POR_PAGINA));
      $("paginacion").hidden = paginas <= 1;
      $("pagina").textContent = "Página " + (pagina + 1) + " de " + paginas;
      $("anterior").disabled = pagina === 0; $("siguiente").disabled = pagina + 1 >= paginas;
    }).catch(function (e) { if (e.status === 401 || e.status === 403) salir("Tu clave ya no sirve."); else aviso("No se pudo cargar: " + e.message, true); });
  }
  function aplicar() { aviso(""); pagina = 0; return cargar(); }
  function vigilar() {   // solo cuenta; no vuelve a dibujar la lista (así no se pierde lo que estás escribiendo)
    var p = parametros(); p.limite = 1; p.desplazamiento = 0;
    api("/v1/revisiones" + consulta(p)).then(function (r) {
      if ($("estado").value === "pendiente" && r.total !== mostrados) aviso("Cambió la lista (" + r.total + " pendientes). Pulsa Actualizar.");
    }).catch(function () {});
  }

  // ---------------------------------------------------------------- acceso
  function cargarComercios() {
    return api("/v1/panel/comercios").then(function (lista) {
      var sel = $("comercio");
      lista.forEach(function (c) { var o = el("option", null, c.nombre); o.value = String(c.id); sel.appendChild(o); });
      $("campoComercio").hidden = lista.length < 2;
    }).catch(function () { $("campoComercio").hidden = true; });
  }
  function entrar(c) {
    clave = c;
    return api("/v1/revisiones?estado=pendiente&limite=1").then(function () {
      try { sessionStorage.setItem("clave_analista", c); } catch (e) { /* sin almacenamiento: no pasa nada */ }
      $("acceso").hidden = true; $("panel").hidden = false; $("actualizar").hidden = false; $("salir").hidden = false; $("errorAcceso").hidden = true;
      $("clave").value = "";
      clearInterval(temporizador); temporizador = setInterval(vigilar, 20000);
      api("/v1/yo").then(function (y) { var u = $("usuario"); u.textContent = "Sesión de " + (y.usuario || "una clave sin nombre"); u.hidden = false; }).catch(function () { /* no es indispensable */ });
      return cargarComercios().then(cargar);
    }).catch(function (e) {
      clave = null;
      var t = $("errorAcceso");
      t.textContent = e.status === 401 ? "Esa clave no es válida." : e.status === 403 ? "Esa clave no es de analista." : "No se pudo entrar: " + e.message;
      t.hidden = false;
    });
  }
  function salir(texto) {
    clave = null; clearInterval(temporizador);
    try { sessionStorage.removeItem("clave_analista"); } catch (e) { /* nada */ }
    $("panel").hidden = true; $("acceso").hidden = false; $("actualizar").hidden = true; $("salir").hidden = true; $("contador").textContent = ""; $("usuario").hidden = true;
    var t = $("errorAcceso"); t.textContent = texto || ""; t.hidden = !texto;
  }

  $("formAcceso").addEventListener("submit", function (ev) { ev.preventDefault(); entrar($("clave").value.trim()); });
  $("formFiltros").addEventListener("submit", function (ev) { ev.preventDefault(); if (validar()) aplicar(); });
  $("limpiar").addEventListener("click", function () { limpiar(); aplicar(); });
  $("estado").addEventListener("change", function () { if (validar()) aplicar(); });
  $("orden").addEventListener("change", function () { if (validar()) aplicar(); });
  $("riesgo").addEventListener("change", function () { if (validar()) aplicar(); });
  $("comercio").addEventListener("change", function () { if (validar()) aplicar(); });
  $("actualizar").addEventListener("click", function () { aviso(""); cargar(); });
  $("anterior").addEventListener("click", function () { if (pagina > 0) { pagina -= 1; cargar(); window.scrollTo(0, 0); } });
  $("siguiente").addEventListener("click", function () { pagina += 1; cargar(); window.scrollTo(0, 0); });
  $("salir").addEventListener("click", function () { aviso(""); salir(""); });
  $("alternar-filtros").addEventListener("click", function () {
    var abrir = $("cuerpoFiltros").hidden;
    $("cuerpoFiltros").hidden = !abrir; $("alternar-filtros").setAttribute("aria-expanded", String(abrir));
  });
  var guardada = null;
  try { guardada = sessionStorage.getItem("clave_analista"); } catch (e) { guardada = null; }
  var inicial = CFG.claveDemo || guardada;
  if (inicial) entrar(inicial);
})();
