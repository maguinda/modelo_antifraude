(function () {
  "use strict";
  // Dashboard de control: SOLO LEE (todas las peticiones son GET). Todo texto que viene de la API se muestra con textContent, nunca como HTML.
  var CFG = window.PANEL_CONFIG || {};
  var clave = null, esStaff = false, comercios = [];
  var NS = "http://www.w3.org/2000/svg";
  var COP = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
  var COPc = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", notation: "compact", maximumFractionDigits: 1 });
  var NUM = new Intl.NumberFormat("es-CO");
  var $ = function (id) { return document.getElementById(id); };
  var DECISIONES = { APROBADA: "Aprobada", EN_REVISION: "En revisión", RECHAZADA: "Rechazada" };
  var ETIQ = { monto_cop: "Monto", metodo_pago: "Método de pago", canal_pago: "Canal", categoria: "Categoría", localizacion: "Ciudad del pago", fecha: "Fecha",
    hora: "Hora", mes: "Mes", dispositivo: "Tipo de dispositivo", estado_dispositivo: "Estado del dispositivo", sistema_operativo: "Sistema operativo",
    navegador: "Navegador", resolucion_pantalla: "Pantalla", zona_horaria_dispositivo: "Zona horaria", es_emulador: "Es emulador", tipo_ip: "Tipo de IP",
    ip_pais: "País de la IP", ip_ciudad_estimada: "Ciudad de la IP", distancia_ip_ciudad_km: "Distancia IP–ciudad (km)", ip_es_datacenter: "IP de centro de datos",
    ip_reputacion: "Reputación de la IP", kyc_nivel: "Verificación (KYC)", edad_cuenta_dias: "Antigüedad de la cuenta (días)", autenticacion_2fa: "2FA",
    intentos_fallidos_24h: "Intentos fallidos 24 h", cambio_credenciales_7d: "Cambió credenciales (7 d)", tiempo_formulario_seg: "Tiempo en el formulario (s)",
    velocidad_tecleo_ms: "Velocidad de tecleo (ms)", pego_numero_tarjeta: "Pegó el número de tarjeta", mouse_natural: "Movimiento natural del mouse",
    transacciones_ultimas_24h: "Pagos en 24 h (calculado)", codigo: "Código" };
  var GRUPOS = { pago: "Pago", dispositivo: "Dispositivo", red: "Red", identidad: "Identidad", comportamiento: "Comportamiento", calculado_por_la_api: "Calculado por la API", otros: "Otros" };

  // ---------------------------------------------------------------- utilidades
  function el(tag, clase, texto) {
    var e = document.createElement(tag);
    if (clase) e.className = clase;
    if (texto !== undefined && texto !== null) e.textContent = String(texto);
    return e;
  }
  function svg(tag, attrs, texto) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (texto !== undefined) e.textContent = texto;
    return e;
  }
  function add(padre) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) padre.appendChild(arguments[i]); return padre; }
  function fecha(iso) {
    var d = new Date(iso);
    return isNaN(d) ? String(iso || "—") : d.toLocaleString("es-CO", { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
  }
  function valor(v) { return v === null || v === undefined ? "—" : v === true ? "Sí" : v === false ? "No" : typeof v === "number" && !isFinite(v) ? "—" : String(v); }
  function etiqueta(k) { return ETIQ[k] || k.replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); }); }
  function pct(a, b) { return b ? Math.round((100 * a) / b) + " %" : "—"; }
  function estado(dec) { return el("span", "estado " + dec, DECISIONES[dec] || dec || "Pendiente"); }
  function consulta(params) {
    var q = Object.keys(params || {}).filter(function (k) { return params[k] !== "" && params[k] !== null && params[k] !== undefined; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]); }).join("&");
    return q ? "?" + q : "";
  }
  function enlace(ruta, params) { return "#/" + ruta + consulta(params); }

  // ---------------------------------------------------------------- API (solo GET)
  function api(ruta, params) {
    return fetch(ruta + consulta(params), { method: "GET", headers: { "X-API-Key": clave } }).then(function (r) {
      return r.text().then(function (t) {
        var j = null;
        try { j = t ? JSON.parse(t) : null; } catch (e) { j = null; }
        if (!r.ok) {
          var err = new Error((j && (j.detail || j.title)) || ("Error " + r.status));
          err.status = r.status;
          throw err;
        }
        return j;
      });
    });
  }

  // ---------------------------------------------------------------- filtros
  var CAMPOS = {
    comercio_id: { et: "Comercio", tipo: "comercio" }, desde: { et: "Desde", tipo: "date" }, hasta: { et: "Hasta", tipo: "date" },
    decision: { et: "Decisión", tipo: "select", op: [["", "Todas"], ["APROBADA", "Aprobada"], ["EN_REVISION", "En revisión"], ["RECHAZADA", "Rechazada"]] },
    modo: { et: "Modo", tipo: "select", op: [["", "Todos"], ["normal", "Normal"], ["degradado", "Degradado"]] },
    riesgo: { et: "Riesgo", tipo: "select", op: [["", "Todos"], ["alto", "Alto (60 % o más)"], ["medio", "Medio (35 % a 59 %)"], ["bajo", "Bajo (menos de 35 %)"]] },
    monto_min: { et: "Monto mínimo", tipo: "number" }, monto_max: { et: "Monto máximo", tipo: "number" },
    id_cliente: { et: "Cliente", tipo: "text", max: 64 }, id_seguimiento: { et: "ID de seguimiento", tipo: "text", max: 16, marca: "AF-XXXX-XXXX" },
    id_transaccion: { et: "ID del comercio", tipo: "text", max: 64 }, regla: { et: "Regla activada", tipo: "text", max: 64 },
    ip: { et: "IP (código)", tipo: "text", max: 8, marca: "8 caracteres" }, dispositivo: { et: "Dispositivo (código)", tipo: "text", max: 8, marca: "8 caracteres" },
    minimo: { et: "Mínimo para listar", tipo: "select", op: [["3", "3"], ["5", "5"], ["10", "10"], ["20", "20"]] }
  };
  function riesgoParams(r) { return r === "alto" ? { riesgo_min: 0.6 } : r === "medio" ? { riesgo_min: 0.35, riesgo_max: 0.5999 } : r === "bajo" ? { riesgo_max: 0.3499 } : {}; }
  function riesgoNivel(p) { return p.riesgo_min === "0.6" ? "alto" : p.riesgo_min === "0.35" ? "medio" : p.riesgo_max === "0.3499" ? "bajo" : ""; }
  function validar(v) {
    var e = {}, hex = /^[0-9A-Fa-f]{8}$/;
    if (v.desde && v.hasta && v.desde > v.hasta) e.desde = "«Desde» no puede ser posterior a «Hasta».";
    if (v.monto_min !== "" && v.monto_max !== "" && v.monto_min !== undefined && v.monto_max !== undefined && Number(v.monto_min) > Number(v.monto_max)) e.monto_min = "El mínimo no puede superar al máximo.";
    ["monto_min", "monto_max"].forEach(function (k) { if (v[k] && !(Number(v[k]) >= 0)) e[k] = "Escribe un número igual o mayor que 0."; });
    ["ip", "dispositivo"].forEach(function (k) { if (v[k] && !hex.test(v[k])) e[k] = "Son 8 caracteres entre 0-9 y A-F."; });
    if (v.id_seguimiento && !/^AF-[0-9A-Za-z]{4}-[0-9A-Za-z]{4}$/i.test(v.id_seguimiento)) e.id_seguimiento = "Usa el formato AF-XXXX-XXXX.";
    return e;
  }
  function formularioFiltros(nombres, valores, ruta, fijos) {
    var caja = el("section", "tarjeta");
    var alternar = el("button", "btn suave alternar-filtros", "Filtros");
    alternar.type = "button";
    alternar.setAttribute("aria-expanded", "false");
    var form = el("form");
    form.noValidate = true;
    var cuerpo = el("div", "filtros filtros-cuerpo");
    cuerpo.hidden = true;
    var campos = {};
    nombres.forEach(function (n) {
      var d = CAMPOS[n];
      if (n === "comercio_id" && !esStaff) return;
      var c = el("div", "campo"), id = "f_" + n, ctl;
      add(c, (function () { var l = el("label", null, d.et); l.htmlFor = id; return l; })());
      if (d.tipo === "select" || d.tipo === "comercio") {
        ctl = el("select");
        (d.tipo === "comercio" ? [["", "Todos los comercios"]].concat(comercios.map(function (x) { return [String(x.id), x.nombre]; })) : d.op).forEach(function (o) {
          var op = el("option", null, o[1]); op.value = o[0]; ctl.appendChild(op);
        });
      } else {
        ctl = el("input");
        ctl.type = d.tipo === "number" ? "number" : d.tipo === "date" ? "date" : "text";
        if (d.tipo === "number") { ctl.min = "0"; ctl.step = "any"; ctl.inputMode = "decimal"; }
        if (d.max) ctl.maxLength = d.max;
        if (d.marca) ctl.placeholder = d.marca;
        ctl.autocomplete = "off";
      }
      ctl.id = id; ctl.name = n; ctl.value = valores[n] || (n === "minimo" ? "3" : "");
      var err = el("span", "error-campo"); err.setAttribute("role", "alert"); err.hidden = true;
      campos[n] = { ctl: ctl, err: err };
      add(c, ctl, err);
      cuerpo.appendChild(c);
    });
    var acciones = el("div", "filtros-acciones");
    var aplicar = el("button", "btn azul", "Aplicar"); aplicar.type = "submit";
    var limpiar = el("a", "btn suave", "Limpiar"); limpiar.href = enlace(ruta, fijos || {});
    add(acciones, aplicar, limpiar);
    cuerpo.appendChild(el("div", "campo")).appendChild(acciones);
    form.appendChild(cuerpo);
    alternar.addEventListener("click", function () {
      var abierto = cuerpo.hidden;
      cuerpo.hidden = !abierto;
      alternar.setAttribute("aria-expanded", String(abierto));
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var v = {};
      Object.keys(campos).forEach(function (n) { v[n] = campos[n].ctl.value.trim(); });
      var errores = validar(v);
      Object.keys(campos).forEach(function (n) { campos[n].err.textContent = errores[n] || ""; campos[n].err.hidden = !errores[n]; });
      if (Object.keys(errores).length) {
        var primero = Object.keys(errores)[0];
        campos[primero].ctl.focus();
        return;
      }
      if (v.riesgo !== undefined) { Object.assign(v, riesgoParams(v.riesgo)); delete v.riesgo; }
      Object.keys(fijos || {}).forEach(function (k) { if (v[k] === undefined) v[k] = fijos[k]; });
      if (v.ip) v.ip = v.ip.toUpperCase();
      if (v.dispositivo) v.dispositivo = v.dispositivo.toUpperCase();
      if (v.id_seguimiento) v.id_seguimiento = v.id_seguimiento.toUpperCase();
      location.hash = enlace(ruta, v);
    });
    if (Object.keys(valores).some(function (k) { return valores[k] && nombres.indexOf(k) >= 0 && k !== "minimo"; })) cuerpo.hidden = false;
    add(caja, alternar, form);
    return caja;
  }
  function soloFiltros(p, nombres) { var o = {}; nombres.forEach(function (n) { if (p[n]) o[n] = p[n]; }); return o; }

  // ---------------------------------------------------------------- piezas de pantalla
  function hero(ojo, titulo, acento, texto) {
    var h = el("section", "hero");
    var t = el("h1"); t.appendChild(document.createTextNode(titulo + " ")); t.appendChild(el("em", null, acento));
    return add(h, el("span", "ojo", ojo), t, texto ? el("p", null, texto) : null);
  }
  function kpi(clase, etiq, val, nota) { return add(el("div", "kpi " + clase), el("span", "etiqueta", etiq), el("span", "valor", val), nota ? el("span", "nota", nota) : null); }
  function tarjeta(titulo, sub) { var t = el("section", "tarjeta"); if (titulo) t.appendChild(el("h2", null, titulo)); if (sub) t.appendChild(el("p", "sub", sub)); return t; }
  function vacio(texto) { return el("p", "vacio", texto); }

  function anchoGrafica(normal) { return window.innerWidth < 520 ? 300 : normal; }       // en pantallas angostas la gráfica se dibuja más estrecha para que su texto no se encoja
  function graficaPorDia(dias) {
    if (!dias.length) return vacio("No hay pagos en este periodo.");
    var W = anchoGrafica(640), H = 230, ml = 34, mb = 24, mt = 8, ancho = W - ml - 6;
    var max = Math.max.apply(null, dias.map(function (d) { return d.aprobadas + d.en_revision + d.rechazadas; })) || 1;
    var g = svg("svg", { "class": "grafica", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Pagos por día según la decisión" });
    [0, .5, 1].forEach(function (f) {
      var y = mt + (H - mb - mt) * (1 - f);
      g.appendChild(svg("line", { "class": "eje", x1: ml, x2: W - 6, y1: y, y2: y }));
      g.appendChild(svg("text", { x: ml - 6, y: y + 4, "text-anchor": "end" }, NUM.format(Math.round(max * f))));
    });
    var paso = ancho / dias.length, bw = Math.max(3, Math.min(28, paso * 0.68));
    dias.forEach(function (d, i) {
      var x = ml + paso * i + (paso - bw) / 2, base = H - mb, alto = H - mb - mt;
      [["APROBADA", d.aprobadas], ["EN_REVISION", d.en_revision], ["RECHAZADA", d.rechazadas]].forEach(function (par) {
        var h = (par[1] / max) * alto;
        if (h <= 0) return;
        base -= h;
        var r = svg("rect", { "class": "b-" + par[0], x: x, y: base, width: bw, height: h, rx: 2 });
        r.appendChild(svg("title", {}, d.dia + " · " + DECISIONES[par[0]] + ": " + par[1]));
        g.appendChild(r);
      });
      if (dias.length <= 12 || i % Math.ceil(dias.length / (W < 400 ? 5 : 8)) === 0) g.appendChild(svg("text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" }, d.dia.slice(8, 10) + "/" + d.dia.slice(5, 7)));
    });
    var caja = el("div");
    caja.appendChild(g);
    var ley = el("ul", "leyenda");
    ["APROBADA", "EN_REVISION", "RECHAZADA"].forEach(function (k) { ley.appendChild(el("li", k, DECISIONES[k])); });
    caja.appendChild(ley);
    return caja;
  }
  function graficaPuntaje(cubetas) {
    var W = anchoGrafica(420), H = 190, ml = 8, mb = 24, mt = 8;
    var max = Math.max.apply(null, cubetas.map(function (c) { return c.pagos; })) || 1;
    var g = svg("svg", { "class": "grafica", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Distribución del puntaje de riesgo" });
    var paso = (W - ml * 2) / cubetas.length;
    cubetas.forEach(function (c, i) {
      var h = (c.pagos / max) * (H - mb - mt), x = ml + paso * i + 3;
      var r = svg("rect", { "class": "b-azul", x: x, y: H - mb - h, width: paso - 6, height: Math.max(h, 1), rx: 3 });
      r.appendChild(svg("title", {}, "Puntaje " + c.desde.toFixed(1) + " a " + c.hasta.toFixed(1) + ": " + NUM.format(c.pagos) + " pagos"));
      g.appendChild(r);
      g.appendChild(svg("text", { x: x + (paso - 6) / 2, y: H - 7, "text-anchor": "middle" }, c.desde.toFixed(1)));
    });
    return g;
  }
  function listaBarras(filas, claseRelleno) {
    if (!filas.length) return vacio("Todavía no se activó ninguna.");
    var max = Math.max.apply(null, filas.map(function (f) { return f.valor; })) || 1, ul = el("ul", "barras");
    filas.forEach(function (f) {
      var li = el("li"), pista = el("span", "pista"), rel = el("span", "relleno" + (claseRelleno ? " " + claseRelleno : ""));
      rel.style.setProperty("--ancho", Math.max(2, Math.round((100 * f.valor) / max)) + "%");
      pista.appendChild(rel);
      add(li, add(el("div", "fila"), el("span", null, f.etiqueta), el("strong", null, NUM.format(f.valor))), pista);
      ul.appendChild(li);
    });
    return ul;
  }

  // ---------------------------------------------------------------- pantallas
  function vistaResumen(p) {
    var nombres = ["comercio_id", "desde", "hasta"], filtros = soloFiltros(p, nombres);
    return api("/v1/panel/resumen", filtros).then(function (r) {
      var t = r.totales, c = el("div");
      add(c, hero("Resumen", "Decisiones", "a la vista", "Qué se aprobó, qué quedó en revisión y qué se rechazó, con las reglas que más pesaron."),
        formularioFiltros(nombres, filtros, "resumen"));
      var k = el("div", "rejilla kpis pila");
      add(k, kpi("", "Pagos", NUM.format(t.pagos), "evaluados en el periodo"),
        kpi("verde", "Aprobadas", pct(t.aprobadas, t.pagos), NUM.format(t.aprobadas) + " pagos"),
        kpi("", "En revisión", pct(t.en_revision, t.pagos), NUM.format(t.en_revision) + " pagos"),
        kpi("oscuro", "Rechazadas", pct(t.rechazadas, t.pagos), NUM.format(t.rechazadas) + " pagos"),
        kpi("", "Monto rechazado", COPc.format(t.monto_rechazado_cop), COP.format(t.monto_rechazado_cop) + " de " + COPc.format(t.monto_cop) + " en total"),
        kpi("", "Por revisar", NUM.format(t.revisiones_pendientes), "pagos esperando a un analista"));
      c.appendChild(k);
      var fila = el("div", "rejilla dos pila");
      var a = tarjeta("Pagos por día", "Cada barra es un día, dividida por la decisión.");
      a.appendChild(graficaPorDia(r.por_dia));
      var b = tarjeta("Reglas más activadas", "Cuántos pagos tocó cada regla de seguridad.");
      b.appendChild(listaBarras(r.reglas_mas_activadas.map(function (x) { return { etiqueta: x.regla, valor: x.veces }; })));
      add(fila, a, b);
      c.appendChild(fila);
      var fila2 = el("div", "rejilla dos pila");
      var d = tarjeta("Distribución del puntaje", "Cuántos pagos cayeron en cada tramo de probabilidad de fraude (0 a 1).");
      d.appendChild(graficaPuntaje(r.distribucion_del_puntaje));
      var q = tarjeta("Calidad del servicio", "Señales de que el sistema trabaja con buenos datos.");
      var dl = el("dl", "datos");
      [["Modo degradado", pct(t.modo_degradado, t.pagos) + " (" + NUM.format(t.modo_degradado) + ")"], ["Sin estado del dispositivo", pct(t.sin_estado_del_dispositivo, t.pagos)],
        ["Latencia media", t.latencia_media_ms === null ? "—" : t.latencia_media_ms.toFixed(1) + " ms"], ["Latencia p95", t.latencia_p95_ms === null ? "—" : t.latencia_p95_ms.toFixed(1) + " ms"]]
        .forEach(function (x) { add(dl, el("dt", null, x[0]), el("dd", null, x[1])); });
      q.appendChild(dl);
      add(fila2, d, q);
      c.appendChild(fila2);
      if (esStaff && (r.por_analista || []).length) {
        var eq = tarjeta("Equipo de revisión", "Qué resolvió cada persona en este periodo y cuánto tardó, en promedio, desde que el pago quedó en revisión.");
        var cj = el("div", "tabla-caja"), tq = el("table", "tabla"), cq = el("tr");
        ["Analista", "Resueltas", "Aprobadas", "Rechazadas", "Minutos promedio"].forEach(function (h, i) { var th = el("th", i ? "num" : null, h); th.scope = "col"; cq.appendChild(th); });
        add(tq, add(el("thead"), cq));
        var cuq = el("tbody");
        r.por_analista.forEach(function (x) {
          var tr = el("tr");
          [["Analista", x.analista, 0], ["Resueltas", NUM.format(x.resueltas), 1], ["Aprobadas", NUM.format(x.aprobadas), 1], ["Rechazadas", NUM.format(x.rechazadas), 1], ["Minutos promedio", x.minutos_promedio === null ? "—" : NUM.format(x.minutos_promedio), 1]]
            .forEach(function (v) { var c2 = el("td", v[2] ? "num" : null, v[1]); c2.dataset.etiqueta = v[0]; tr.appendChild(c2); });
          cuq.appendChild(tr);
        });
        add(tq, cuq); cj.appendChild(tq); eq.appendChild(cj);
        c.appendChild(add(el("div", "pila"), eq));
      }
      if (esStaff && r.por_comercio.length) {
        var pc = tarjeta("Por comercio", "Toca un comercio para ver sus pagos.");
        var caja = el("div", "tabla-caja"), tb = el("table", "tabla"), cab = el("tr");
        ["Comercio", "Pagos", "Aprobadas", "En revisión", "Rechazadas", "Monto rechazado"].forEach(function (h, i) { var th = el("th", i ? "num" : null, h); th.scope = "col"; cab.appendChild(th); });
        add(tb, add(el("thead"), cab));
        var cu = el("tbody");
        r.por_comercio.forEach(function (x) {
          var tr = el("tr"), td = el("td"), a2 = el("a", null, x.nombre);
          td.dataset.etiqueta = "Comercio"; a2.href = enlace("transacciones", { comercio_id: x.comercio_id }); td.appendChild(a2); tr.appendChild(td);
          [["Pagos", NUM.format(x.pagos)], ["Aprobadas", NUM.format(x.aprobadas)], ["En revisión", NUM.format(x.en_revision)], ["Rechazadas", NUM.format(x.rechazadas)], ["Monto rechazado", COP.format(x.monto_rechazado)]]
            .forEach(function (v) { var c2 = el("td", "num", v[1]); c2.dataset.etiqueta = v[0]; tr.appendChild(c2); });
          cu.appendChild(tr);
        });
        add(tb, cu); caja.appendChild(tb); pc.appendChild(caja);
        c.appendChild(add(el("div", "pila"), pc));
      }
      return c;
    });
  }

  function vistaTransacciones(p) {
    var nombres = ["comercio_id", "desde", "hasta", "decision", "modo", "riesgo", "monto_min", "monto_max", "id_cliente", "id_seguimiento", "id_transaccion", "regla", "ip", "dispositivo"];
    var filtros = soloFiltros(p, nombres.concat(["riesgo_min", "riesgo_max"])), limite = 25, desp = Math.max(0, parseInt(p.d || "0", 10) || 0), orden = p.orden || "fecha_desc";
    var params = Object.assign({}, filtros, { orden: orden, limite: limite, desplazamiento: desp });
    return api("/v1/panel/transacciones", params).then(function (r) {
      var c = el("div");
      add(c, hero("Transacciones", "Cada pago,", "rastreable", "Filtra por comercio, fechas, decisión, cliente o regla. Toca un pago para ver cómo se decidió."));
      var f = formularioFiltros(nombres, Object.assign({}, filtros, { riesgo: riesgoNivel(p) }), "transacciones");
      c.appendChild(f);
      var caja = tarjeta(null);
      caja.classList.add("pila");
      var barra = el("div", "paginacion");
      var sel = el("select"); sel.id = "orden"; sel.setAttribute("aria-label", "Ordenar por");
      [["fecha_desc", "Más recientes"], ["fecha_asc", "Más antiguos"], ["monto_desc", "Mayor monto"], ["riesgo_desc", "Mayor riesgo"]].forEach(function (o) { var op = el("option", null, o[1]); op.value = o[0]; sel.appendChild(op); });
      sel.value = orden;
      var campoOrden = add(el("div", "campo"), sel);
      sel.addEventListener("change", function () { location.hash = enlace("transacciones", Object.assign({}, filtros, { orden: sel.value })); });
      add(barra, el("span", "cuenta", NUM.format(r.total) + " pagos" + (r.total ? " · mostrando " + (desp + 1) + "–" + (desp + r.transacciones.length) : "")), campoOrden);
      caja.appendChild(barra);
      if (!r.transacciones.length) caja.appendChild(vacio("No hay pagos con esos filtros."));
      else {
        var cont = el("div", "tabla-caja"), tb = el("table", "tabla"), cab = el("tr");
        [["Seguimiento", 0], ["Fecha", 0], ["Comercio", 0], ["Cliente", 0], ["Monto", 1], ["Decisión", 0], ["Riesgo", 1], ["Reglas", 0], ["IP", 0]].forEach(function (h) { var th = el("th", h[1] ? "num" : null, h[0]); th.scope = "col"; cab.appendChild(th); });
        add(tb, add(el("thead"), cab));
        var cu = el("tbody");
        r.transacciones.forEach(function (x) {
          var tr = el("tr");
          function td(etq, nodo, clase) { var c2 = el("td", clase); c2.dataset.etiqueta = etq; if (typeof nodo === "string") c2.textContent = nodo; else c2.appendChild(nodo); tr.appendChild(c2); }
          var seg = el("a", "seg", x.id_seguimiento); seg.href = enlace("pago/" + x.id_seguimiento, {});
          td("Seguimiento", seg); td("Fecha", fecha(x.fecha)); td("Comercio", x.comercio); td("Cliente", x.id_cliente); td("Monto", COP.format(x.monto_cop || 0), "num");
          var dec = el("span", "decision-celda"); dec.appendChild(estado(x.decision_final || x.decision));
          if (x.decision_final === null && x.decision === "EN_REVISION") dec.appendChild(el("span", "chip", "pendiente"));
          else if (x.decision_final && x.decision_final !== x.decision) dec.appendChild(el("span", "chip", "antes: " + (DECISIONES[x.decision] || x.decision)));
          td("Decisión", dec);
          td("Riesgo", x.puntaje_riesgo === null ? "—" : x.puntaje_riesgo.toFixed(2) + (x.modo === "degradado" ? " (deg.)" : ""), "num");
          var rg = el("div", "reglas-celda");
          if (!x.reglas.length) rg.textContent = "—"; else x.reglas.forEach(function (n) { rg.appendChild(el("span", "chip regla", n)); });
          td("Reglas", rg);
          if (x.ip) { var ip = el("a", "codigo", x.ip); ip.href = enlace("transacciones", { ip: x.ip }); ip.title = "Ver los pagos con esta IP"; td("IP", ip); } else td("IP", "—");
          cu.appendChild(tr);
        });
        add(tb, cu); cont.appendChild(tb); caja.appendChild(cont);
        var pag = el("div", "paginacion");
        var ant = el("a", "btn suave", "← Anterior"), sig = el("a", "btn suave", "Siguiente →");
        ant.href = enlace("transacciones", Object.assign({}, filtros, { orden: orden, d: Math.max(0, desp - limite) || "" }));
        sig.href = enlace("transacciones", Object.assign({}, filtros, { orden: orden, d: desp + limite }));
        if (desp === 0) { ant.setAttribute("aria-disabled", "true"); ant.removeAttribute("href"); }
        if (desp + limite >= r.total) { sig.setAttribute("aria-disabled", "true"); sig.removeAttribute("href"); }
        add(pag, ant, el("span", "cuenta", "Página " + (Math.floor(desp / limite) + 1) + " de " + Math.max(1, Math.ceil(r.total / limite))), sig);
        caja.appendChild(pag);
      }
      c.appendChild(add(el("div", "pila"), caja));
      return c;
    });
  }

  function medidor(puntaje, u) {
    var W = 520, H = 70, x0 = 8, ancho = W - 16, g = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Puntaje de riesgo " + puntaje.toFixed(3) });
    var a = u ? u.revisar : 0.3, b = u ? u.bloquear : 0.7;
    [["APROBADA", 0, a], ["EN_REVISION", a, b], ["RECHAZADA", b, 1]].forEach(function (z) {
      g.appendChild(svg("rect", { "class": "b-" + z[0], x: x0 + ancho * z[1], y: 26, width: Math.max(1, ancho * (z[2] - z[1])), height: 14, rx: 7, opacity: ".35" }));
    });
    var mx = x0 + ancho * Math.min(1, Math.max(0, puntaje));
    g.appendChild(svg("polygon", { "class": "b-azul", points: (mx - 7) + ",14 " + (mx + 7) + ",14 " + mx + ",26" }));
    g.appendChild(svg("rect", { "class": "b-azul", x: mx - 2, y: 24, width: 4, height: 18, rx: 2 }));
    [[0, "0"], [a, u ? "revisar " + a : ""], [b, u ? "bloquear " + b : ""], [1, "1"]].forEach(function (t, i) {
      if (t[1]) g.appendChild(svg("text", { x: x0 + ancho * t[0], y: 62, "text-anchor": i === 0 ? "start" : i === 3 ? "end" : "middle" }, t[1]));
    });
    return add(el("div", "medidor"), g);
  }

  function vistaDetalle(id) {
    return api("/v1/panel/transacciones/" + encodeURIComponent(id)).then(function (d) {
      var c = el("div"), idn = d.identificadores, dec = d.decision;
      var h = hero("Detalle del pago", "Así se", "decidió", "Todo sobre este pago: qué llegó, qué pensó el modelo, qué reglas pesaron y qué pasó después.");
      c.appendChild(h);
      var cab = tarjeta(null), fila = el("div", "identidad");
      var segGrande = el("span", "seg-grande", idn.id_seguimiento);
      var copiar = el("button", "btn suave", "Copiar ID"); copiar.type = "button";
      copiar.addEventListener("click", function () {
        try { navigator.clipboard.writeText(idn.id_seguimiento).then(function () { copiar.textContent = "Copiado"; }, function () { copiar.textContent = "No se pudo copiar"; }); } catch (e) { copiar.textContent = "No se pudo copiar"; }
      });
      var volver = el("a", "btn suave", "← Volver a transacciones"); volver.href = enlace("transacciones", {});
      add(fila, segGrande, estado(dec.final || (dec.decidio === "pendiente de analista" ? "pendiente" : dec.inicial)), copiar, volver);
      cab.appendChild(fila);
      var ids = el("dl", "datos separado");
      [["ID del comercio", idn.id_transaccion], ["Comercio", idn.comercio], ["Cliente", idn.id_cliente], ["ID interno", idn.id_evaluacion], ["Fecha del pago", fecha(d.fechas.fecha_pago)], ["Evaluado", fecha(d.fechas.evaluada_en) + " · " + d.fechas.latencia_ms.toFixed(0) + " ms"]]
        .forEach(function (x) { add(ids, el("dt", null, x[0]), el("dd", null, x[1])); });
      cab.appendChild(ids);
      c.appendChild(add(el("div", "pila"), cab));

      var fila1 = el("div", "rejilla dos pila");
      var dc = tarjeta("Decisión");
      var gd = el("div", "decisiones");
      [["Inicial", dec.inicial ? estado(dec.inicial) : "—"], ["Del modelo", dec.del_modelo ? estado(dec.del_modelo) : "Sin modelo"], ["Final", dec.final ? estado(dec.final) : "Pendiente"], ["Decidió", dec.decidio]]
        .forEach(function (x) { var dt = el("div", "dato"); dt.appendChild(el("span", null, x[0])); if (typeof x[1] === "string") dt.appendChild(el("strong", null, x[1])); else dt.appendChild(x[1]); gd.appendChild(dt); });
      dc.appendChild(gd);
      if (d.puntaje_riesgo !== null) { dc.appendChild(el("p", "sub", "Probabilidad de fraude según el modelo: " + d.puntaje_riesgo.toFixed(3))); dc.appendChild(medidor(d.puntaje_riesgo, d.umbrales)); }
      else dc.appendChild(el("p", "aviso", "No hubo puntaje: el pago se evaluó en modo degradado."));
      var mc = tarjeta("Modelo y red");
      var dm = el("dl", "datos");
      [["Modelo", d.modelo.nombre + " v" + d.modelo.version], ["Variables", d.modelo.conjunto_variables], ["Modo", d.modelo.modo], ["Estado del dispositivo", d.modelo.estado_origen === "no_enviado" ? "no enviado (modelo sin estado)" : "lo mandó el comercio"],
        ["Datos de red", d.modelo.red_origen]].forEach(function (x) { add(dm, el("dt", null, x[0]), el("dd", null, x[1])); });
      mc.appendChild(dm);
      add(fila1, dc, mc);
      c.appendChild(fila1);

      var pasos = tarjeta("Cómo se decidió", "En el orden en que el sistema lo fue resolviendo.");
      var ol = el("ol", "pasos");
      d.como_se_decidio.forEach(function (x) {
        var li = el("li", x.paso.indexOf("Regla:") === 0 ? "regla" : x.paso === "Decisión inicial" ? "final" : "");
        add(li, el("strong", null, x.paso), el("span", null, x.detalle));
        ol.appendChild(li);
      });
      pasos.appendChild(ol);
      var rtar = tarjeta("Reglas activadas", d.reglas_activadas.length ? "Cada una puede subir o fijar la decisión." : null);
      if (!d.reglas_activadas.length) rtar.appendChild(vacio("Ninguna regla de seguridad se activó."));
      else d.reglas_activadas.forEach(function (r) { var b = el("div", "relacionado"); add(b, add(el("div"), el("span", "chip regla", r.regla), el("p", "sub", r.motivo)), el("strong", null, r.accion)); rtar.appendChild(b); });
      c.appendChild(add(el("div", "rejilla dos pila"), pasos, rtar));

      var dat = tarjeta("Datos recibidos", d.campos_no_enviados.length ? "Los marcados «No enviado» no los mandó el comercio: se rellenaron con un valor neutro." : "El comercio mandó todos los datos.");
      Object.keys(d.datos_recibidos).forEach(function (g) {
        var campos = d.datos_recibidos[g];
        if (!Object.keys(campos).length) return;
        var caja = el("div", "datos-grupo"), dl = el("dl", "datos");
        caja.appendChild(el("h3", null, GRUPOS[g] || g));
        Object.keys(campos).forEach(function (k) {
          var dd = el("dd", null, valor(campos[k].valor));
          if (!campos[k].enviado) dd.appendChild(document.createTextNode(" ")), dd.appendChild(el("span", "chip falta", "No enviado"));
          if (k === "codigo" && campos[k].valor) { var ref = g === "red" ? { ip: campos[k].valor } : { dispositivo: campos[k].valor }; var a = el("a", "codigo", campos[k].valor); a.href = enlace("transacciones", ref); dd.textContent = ""; dd.appendChild(a); }
          add(dl, el("dt", null, etiqueta(k)), dd);
        });
        caja.appendChild(dl); dat.appendChild(caja);
      });
      var rel = tarjeta("Pagos relacionados", "Otros pagos que comparten algo con este.");
      var rr = el("div", "relacionados"), r = d.relacionados;
      function relBloque(titulo, info, params) {
        var b = el("div", "relacionado");
        add(b, add(el("div"), el("strong", null, titulo), el("p", "sub", info.otros_pagos ? NUM.format(info.otros_pagos) + " pagos más" : "Sin otros pagos")));
        if (info.otros_pagos) { var a = el("a", "btn suave", "Ver pagos"); a.href = enlace("transacciones", params); b.appendChild(a); }
        rr.appendChild(b);
      }
      if (r.misma_ip) relBloque("Misma IP (" + r.misma_ip.codigo + ")", r.misma_ip, { ip: r.misma_ip.codigo });
      if (r.mismo_dispositivo) relBloque("Mismo dispositivo (" + r.mismo_dispositivo.codigo + ")", r.mismo_dispositivo, { dispositivo: r.mismo_dispositivo.codigo });
      relBloque("Mismo cliente (" + r.mismo_cliente.id_cliente + ")", r.mismo_cliente, { id_cliente: r.mismo_cliente.id_cliente, comercio_id: r.mismo_cliente.filtro.comercio_id });
      rel.appendChild(rr);
      c.appendChild(add(el("div", "rejilla dos pila"), dat, rel));

      var cola = el("div", "rejilla iguales pila");
      var rv = tarjeta("Revisión y resultado");
      if (!d.revision && !d.resultado_informado) rv.appendChild(vacio("Este pago no pasó por revisión ni tiene resultado informado."));
      else {
        var dr = el("dl", "datos");
        if (d.revision) [["Revisión", d.revision.estado], ["Decisión del analista", d.revision.decision_analista || "—"], ["Resuelta por", d.revision.resuelta_por || "—"], ["Nota", d.revision.nota || "—"], ["Resuelta", d.revision.resuelta ? fecha(d.revision.resuelta) : "—"]].forEach(function (x) { add(dr, el("dt", null, x[0]), el("dd", null, x[1])); });
        if (d.resultado_informado) [["Resultado informado", d.resultado_informado.resultado], ["Confirmado", fecha(d.resultado_informado.fecha_confirmacion)]].forEach(function (x) { add(dr, el("dt", null, x[0]), el("dd", null, x[1])); });
        rv.appendChild(dr);
      }
      var au = tarjeta("Auditoría", "Registro que no se puede editar.");
      var ul = el("ul", "auditoria");
      d.auditoria.forEach(function (x) { add(ul, add(el("li"), el("strong", null, x.accion + " · " + (x.actor || "")), el("span", null, fecha(x.momento)))); });
      au.appendChild(ul);
      add(cola, rv, au);
      c.appendChild(cola);
      document.title = idn.id_seguimiento + " · Panel de control";
      return c;
    });
  }

  function vistaVinculos(p) {
    var nombres = ["comercio_id", "desde", "hasta", "minimo"], filtros = soloFiltros(p, nombres);
    return api("/v1/panel/vinculos", Object.assign({}, filtros, { minimo: filtros.minimo || 3 })).then(function (v) {
      var c = el("div");
      add(c, hero("Vínculos", "Lo que", "se repite", "Pagos que comparten IP, dispositivo o cliente: la forma más rápida de ver redes de fraude."), formularioFiltros(nombres, filtros, "vinculos"));
      function bloque(titulo, sub, filas, fabrica) {
        var t = tarjeta(titulo, sub);
        if (!filas.length) t.appendChild(vacio("Nada llega al mínimo elegido."));
        filas.forEach(function (x) { t.appendChild(fabrica(x)); });
        return t;
      }
      function chips(x, campos) { var m = el("div", "metricas"); campos.forEach(function (k) { m.appendChild(el("span", "chip", k[0] + ": " + valor(x[k[1]]))); }); return m; }
      function fila(izq, derecha, params) {
        var f = el("div", "vinculo"), a = el("a", "btn suave", "Ver pagos"); a.href = enlace("transacciones", params);
        return add(f, add(el("div"), izq), derecha, a);
      }
      var cod = function (t) { return el("span", "codigo", t); };
      var g1 = el("div", "rejilla iguales pila");
      add(g1, bloque("Misma IP, varios clientes", "Una dirección usada por distintas personas.", v.misma_ip_varios_clientes, function (x) {
        return fila(cod(x.codigo), chips(x, [["clientes", "clientes"], ["pagos", "pagos"], ["rechazadas", "rechazadas"], ["comercios", "comercios"]]), x.filtro); }),
        bloque("Mismo dispositivo, varios clientes", "Un equipo que aparece con distintas cuentas.", v.mismo_dispositivo_varios_clientes, function (x) {
          return fila(cod(x.codigo), chips(x, [["clientes", "clientes"], ["pagos", "pagos"], ["rechazadas", "rechazadas"]]), x.filtro); }));
      var g2 = el("div", "rejilla iguales pila");
      add(g2, bloque("Un cliente, varias IPs", "Una misma cuenta pagando desde muchos lugares.", v.cliente_con_varias_ips, function (x) {
        return fila(add(el("div"), el("strong", null, x.id_cliente), el("p", "sub", x.comercio)), chips(x, [["IPs", "ips"], ["pagos", "pagos"], ["rechazadas", "rechazadas"]]), x.filtro); }),
        bloque("Ráfagas desde una IP", "Muchos pagos en " + v.ventana_rafaga_minutos + " minutos desde la misma IP.", v.rafagas_por_ip, function (x) {
          return fila(cod(x.codigo), chips(x, [["en la ventana", "pagos_en_ventana"], ["en total", "pagos_totales"]]), x.filtro); }));
      add(c, g1, g2);
      return c;
    });
  }

  // ---------------------------------------------------------------- enrutador
  function leerHash() {
    var h = location.hash.replace(/^#\/?/, ""), i = h.indexOf("?"), ruta = i < 0 ? h : h.slice(0, i), p = {};
    if (i >= 0) h.slice(i + 1).split("&").forEach(function (par) { var kv = par.split("="); if (kv[0]) { try { p[decodeURIComponent(kv[0])] = decodeURIComponent((kv[1] || "").replace(/\+/g, " ")); } catch (e) { /* parámetro mal formado: se ignora */ } } });
    return { ruta: ruta || "resumen", p: p };
  }
  var generacion = 0;
  function pintar() {
    if (!clave) return;
    var h = leerHash(), mia = ++generacion, base = h.ruta.split("/")[0], sitio = $("contenido");
    document.querySelectorAll("#nav a[data-ruta]").forEach(function (a) { if (a.dataset.ruta === (base === "pago" ? "transacciones" : base)) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
    sitio.textContent = "";
    sitio.appendChild(el("p", "cargando", "Cargando…"));
    document.title = "Panel de control · antifraude";
    var vista = base === "transacciones" ? vistaTransacciones(h.p) : base === "pago" ? vistaDetalle(decodeURIComponent(h.ruta.split("/")[1] || "")) :
      base === "vinculos" ? vistaVinculos(h.p) : vistaResumen(h.p);
    vista.then(function (nodo) { if (mia !== generacion) return; sitio.textContent = ""; sitio.appendChild(nodo); window.scrollTo(0, 0); }).catch(function (e) {
      if (mia !== generacion) return;
      if (e.status === 401) { salir("Tu clave ya no sirve. Entra de nuevo."); return; }
      sitio.textContent = "";
      var t = tarjeta(e.status === 404 ? "No encontrado" : "No se pudo cargar");
      t.appendChild(el("p", e.status === 404 ? null : "error", e.status === 404 ? "No existe ese pago, o no tienes acceso a él." : e.message));
      var a = el("a", "btn suave", "Volver al resumen"); a.href = "#/resumen"; t.appendChild(a);
      sitio.appendChild(t);
    });
  }

  // ---------------------------------------------------------------- acceso
  function entrar(c) {
    clave = c;
    return api("/v1/panel/comercios").then(function (lista) { esStaff = true; comercios = lista; }, function (e) {
      if (e.status === 403) { esStaff = false; comercios = []; return; }
      throw e;
    }).then(function () {
      try { sessionStorage.setItem("clave_analista", c); } catch (e) { /* sin almacenamiento: no pasa nada */ }
      $("acceso").hidden = true; $("nav").hidden = false; $("salir").hidden = false;
      api("/v1/yo").then(function (y) { var u = $("usuario"); u.textContent = y.rol === "comercio" ? y.comercio : "Sesión de " + (y.usuario || "una clave sin nombre"); u.hidden = false; }).catch(function () { /* no es indispensable */ });
      pintar();
    }).catch(function (e) {
      clave = null;
      var m = $("errorAcceso");
      m.textContent = e.status === 401 ? "Esa clave no es válida." : (e.message || "No se pudo entrar.");
      m.hidden = false; $("acceso").hidden = false; $("contenido").textContent = "";
    });
  }
  function salir(texto) {
    clave = null; esStaff = false; comercios = [];
    try { sessionStorage.removeItem("clave_analista"); } catch (e) { /* nada */ }
    $("nav").hidden = true; $("salir").hidden = true; $("usuario").hidden = true; $("contenido").textContent = ""; $("acceso").hidden = false;
    var m = $("errorAcceso"); m.textContent = texto || ""; m.hidden = !texto; $("clave").value = "";
  }
  $("formAcceso").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var v = $("clave").value.trim(), m = $("errorAcceso");
    if (!v) { m.textContent = "Escribe tu clave."; m.hidden = false; return; }
    m.hidden = true;
    entrar(v);
  });
  $("salir").addEventListener("click", function () { salir(""); });
  window.addEventListener("hashchange", pintar);
  var guardada = null;
  try { guardada = sessionStorage.getItem("clave_analista"); } catch (e) { guardada = null; }
  var inicial = CFG.claveDemo || guardada;
  if (inicial) entrar(inicial); else $("acceso").hidden = false;
})();
