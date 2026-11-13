/*
 * SDK antifraude (versión de demostración). Corre en el navegador del comprador y arma, SIN leer ningún dato escrito
 * por el comprador, las señales que la API usa: huella del dispositivo, datos del equipo y comportamiento al llenar el formulario.
 *
 *   AntifraudeSDK.observar(formulario, campoTarjeta)   // empieza a medir (solo eventos: nunca lee lo que se escribe)
 *   AntifraudeSDK.recolectar().then(function (señales) { ... })   // { dispositivo: {...}, comportamiento: {...} }
 *
 * IMPORTANTE: la clave de API NUNCA va en el navegador. Estas señales se mandan al servidor del comercio, y ese servidor
 * llama a la API antifraude con su clave.
 */
(function (global) {
  "use strict";
  var ua = function () { return navigator.userAgent || ""; };

  function sha256Hex(texto) {
    return crypto.subtle.digest("SHA-256", new TextEncoder().encode(texto)).then(function (b) {
      return Array.prototype.map.call(new Uint8Array(b), function (x) { return ("0" + x.toString(16)).slice(-2); }).join("");
    });
  }
  function sistemaOperativo() {
    var u = ua();
    if (/iPhone|iPad|iPod/.test(u)) return "iOS";
    if (/Android/.test(u)) return "Android";
    if (/Windows/.test(u)) return "Windows";
    if (/Mac OS X|Macintosh/.test(u)) return "macOS";
    if (/Linux/.test(u)) return "Linux";
    return "Desconocido";
  }
  function navegador() {
    var u = ua();
    if (/HeadlessChrome/.test(u)) return "Chrome headless";
    if (/Edg\//.test(u)) return "Edge";
    if (/OPR\/|Opera/.test(u)) return "Opera";
    if (/SamsungBrowser/.test(u)) return "Samsung Internet";
    if (/Firefox\//.test(u)) return "Firefox";
    if (/Chrome\/|CriOS/.test(u)) return "Chrome";
    if (/Safari\//.test(u)) return "Safari";
    return "Desconocido";
  }
  function tipoDispositivo() {
    var so = sistemaOperativo(), ancho = Math.min(screen.width, screen.height);
    if (so === "iOS") return "IPHONE";
    if (so === "Android") return ancho >= 600 ? "TABLET_ANDROID" : "ANDROID";
    if (so === "macOS") return "MACBOOK";
    return "WINDOWS_PC";                     // Windows, Linux y desconocidos se agrupan como computador
  }
  function esEmulador() { return !!navigator.webdriver || /HeadlessChrome|PhantomJS/i.test(ua()); }

  function Observador(formulario, campoTarjeta) {
    var inicio = null, teclas = [], ultimaTecla = null, pego = false, mov = 0, cambios = 0, ultimoAngulo = null, ultimoPunto = null;
    function arrancar() { if (inicio === null) inicio = Date.now(); }
    formulario.addEventListener("focusin", arrancar);
    formulario.addEventListener("input", arrancar);
    formulario.addEventListener("keydown", function (ev) {
      if (ev.key && ev.key.length > 1 && ev.key !== "Backspace") return;          // teclas de control no cuentan
      var t = Date.now();
      if (ultimaTecla !== null && t - ultimaTecla < 2000) teclas.push(t - ultimaTecla);
      ultimaTecla = t;
    });
    if (campoTarjeta) campoTarjeta.addEventListener("paste", function () { pego = true; });   // solo el hecho de pegar
    document.addEventListener("mousemove", function (ev) {
      mov++;
      if (ultimoPunto) {
        var dx = ev.clientX - ultimoPunto[0], dy = ev.clientY - ultimoPunto[1];
        if (dx || dy) { var ang = Math.atan2(dy, dx); if (ultimoAngulo !== null && Math.abs(ang - ultimoAngulo) > 0.35) cambios++; ultimoAngulo = ang; }
      }
      ultimoPunto = [ev.clientX, ev.clientY];
    });
    this.medicion = function () {
      var c = {};
      if (inicio !== null) c.tiempo_formulario_seg = Math.round((Date.now() - inicio) / 100) / 10;
      if (teclas.length >= 3) c.velocidad_tecleo_ms = Math.round(teclas.reduce(function (a, b) { return a + b; }, 0) / teclas.length);
      c.pego_numero_tarjeta = pego;
      if (mov >= 5) c.mouse_natural = Math.round(Math.min(1, (cambios / mov) * 3) * 100) / 100;   // heurística de demostración
      return c;
    };
  }
  var observador = null;

  global.AntifraudeSDK = {
    version: "0.1.0-demo",
    observar: function (formulario, campoTarjeta) { observador = new Observador(formulario, campoTarjeta); return observador; },
    huella: function () {
      var partes = [ua(), navigator.platform || "", navigator.language || "", String(navigator.hardwareConcurrency || ""), String(navigator.deviceMemory || ""),
                    screen.width + "x" + screen.height + "x" + screen.colorDepth, Intl.DateTimeFormat().resolvedOptions().timeZone || "", String(navigator.maxTouchPoints || 0)];
      return sha256Hex(partes.join("|")).then(function (h) { return "FP-" + h.slice(0, 16); });
    },
    recolectar: function () {
      return global.AntifraudeSDK.huella().then(function (fp) {
        return {
          dispositivo: { tipo: tipoDispositivo(), huella: fp, sistema_operativo: sistemaOperativo(), navegador: navegador(),
                         resolucion_pantalla: screen.width + "x" + screen.height, zona_horaria: Intl.DateTimeFormat().resolvedOptions().timeZone || undefined,
                         es_emulador: esEmulador() },
          comportamiento: observador ? observador.medicion() : {}
        };
      });
    }
  };
})(window);
