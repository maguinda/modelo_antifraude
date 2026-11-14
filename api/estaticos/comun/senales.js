(function () {
  "use strict";
  // «Las señales que más pesaron»: lo usan la revisión manual y el dashboard. Todo texto se muestra con textContent, nunca como HTML.
  function el(tag, clase, texto) {
    var e = document.createElement(tag);
    if (clase) e.className = clase;
    if (texto !== undefined && texto !== null) e.textContent = String(texto);
    return e;
  }
  function pct(x) { return x === null || x === undefined ? "—" : (100 * x).toLocaleString("es-CO", { maximumFractionDigits: x < 0.1 ? 1 : 0 }) + " %"; }
  function fila(s, clase, maximo) {
    var li = el("li", "senal " + clase), pista = el("span", "pista-senal"), barra = el("span", "barra-senal");
    barra.style.setProperty("--ancho", Math.max(6, Math.round(100 * Math.abs(s.puntos) / maximo)) + "%");
    pista.appendChild(barra);
    var cab = el("div", "texto-senal");
    cab.appendChild(el("span", null, s.texto));
    cab.appendChild(el("strong", "peso-senal", (s.puntos > 0 ? "+" : "−") + Math.abs(s.puntos).toLocaleString("es-CO", { maximumFractionDigits: 0 }) + " pts"));
    li.appendChild(cab); li.appendChild(pista);
    return li;
  }
  // Devuelve true si había señales que mostrar (si no, quien llama muestra otra explicación).
  function pintar(contenedor, senales, puntaje) {
    if (!senales || (!(senales.suben || []).length && !(senales.bajan || []).length)) return false;
    var maximo = Math.max.apply(null, (senales.suben || []).concat(senales.bajan || []).map(function (s) { return Math.abs(s.puntos); })) || 1;
    if (senales.puntaje_tipico !== null && senales.puntaje_tipico !== undefined && puntaje !== null && puntaje !== undefined)
      contenedor.appendChild(el("p", "meta", "Un pago típico tendría " + pct(senales.puntaje_tipico) + " de probabilidad de fraude; este tiene " + pct(puntaje) + ". Esto es lo que más empujó la diferencia:"));
    if ((senales.suben || []).length) {
      var ul = el("ul", "senales"); senales.suben.forEach(function (s) { ul.appendChild(fila(s, "sube", maximo)); }); contenedor.appendChild(ul);
    }
    if ((senales.bajan || []).length) {
      contenedor.appendChild(el("p", "meta", "A favor del pago:"));
      var ub = el("ul", "senales"); senales.bajan.forEach(function (s) { ub.appendChild(fila(s, "baja", maximo)); }); contenedor.appendChild(ub);
    }
    contenedor.appendChild(el("p", "meta aclaracion", "Son las señales que más pesaron en el modelo: ayudan a decidir, no prueban que haya fraude. Los puntos son una estimación y pueden variar unos pocos."));
    return true;
  }
  window.AntifraudeSenales = { pintar: pintar };
})();
