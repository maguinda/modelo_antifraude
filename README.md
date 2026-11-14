# modelo_ia – Modelo antifraude para pagos de pymes

Modelo de aprendizaje automático que, dado un pago, calcula la probabilidad de que sea fraude y decide **aprobar, revisar o bloquear**. Es la base técnica del producto antifraude para pymes de comercio electrónico.

**Estado actual (versión 0.1.0, etapa 1):** línea base con regresión logística, entrenada con datos **sintéticos** (inventados). No es un producto listo para vender; es el punto de partida sobre el que se va mejorando, etapa por etapa.

## Cómo usarlo

**Clona con este nombre de carpeta.** El código se importa como `modelo_ia` (`python -m modelo_ia...`), así que la carpeta debe llamarse exactamente así, aunque el repositorio en GitHub se llame `modelo_antifraude`:

```bash
mkdir proyecto && cd proyecto
git clone https://github.com/maguinda/modelo_antifraude.git modelo_ia
```

Los datasets grandes no se suben a GitHub: se regeneran con los scripts de `modelo_ia/datos/generadores/` (semilla fija).

Desde la carpeta `proyecto/` (la que contiene `modelo_ia/`):

```bash
# 1. Una sola vez: crear el entorno e instalar lo necesario
python3 -m venv .venv
source .venv/bin/activate
pip install -r modelo_ia/requirements.txt

# 2. Entrenar el modelo (regresión logística) y dejarlo como el modelo en uso
python -m modelo_ia.entrenar --como-actual

# 3. Probar una decisión con dos pagos de ejemplo
python -m modelo_ia.predecir --ejemplo

# 4. Comparar varios algoritmos (todo queda anotado en la bitácora)
python -m modelo_ia.comparar

# 4b. Comparar el modelo contra reglas fijas (hipótesis H1)
python -m modelo_ia.comparar_h1

# 4c. Medir cuánto aporta cada grupo de variables, y si sirve reentrenar cada mes
python -m modelo_ia.comparar_variables --datos modelo_ia/datos/dataset_antifraude_1000000_transacciones.csv.gz
python -m modelo_ia.reentrenamiento --datos modelo_ia/datos/dataset_antifraude_1000000_transacciones.csv.gz

# 5. Correr las pruebas del código
python -m pytest modelo_ia/tests

# 6. Levantar la API (ver la sección «API REST» más abajo)
python -m modelo_ia.api.servidor            # o: python -m modelo_ia.api.demo  (con panel y tienda de prueba)
```

Por defecto lee `datos/dataset_antifraude_500000_transacciones.csv`. Con otro archivo: `--datos ruta.csv` (también lee `.csv.gz`). Para el dataset de 1 millón: `--datos modelo_ia/datos/dataset_antifraude_1000000_transacciones.csv.gz`.

## Qué hace cada archivo

| Archivo | Para qué sirve |
|---|---|
| `config.py` | Todos los ajustes en un solo lugar: meses de cada conjunto, variables, costos, umbrales. |
| `datos.py` | Carga el CSV y revisa que sirva (columnas, ceros y unos, montos válidos). |
| `particion.py` | Divide por fecha de corte (en `config.py`): entrenamiento hasta el 30 de junio de 2026, validación julio y agosto, prueba de septiembre en adelante. |
| `variables.py` | Crea variables nuevas: hora e historial del cliente (solo con compras anteriores). |
| `modelos.py` | Fábrica de modelos. Para probar un algoritmo nuevo se agrega una línea al `REGISTRO`. |
| `evaluacion.py` | Recall, precisión, calidad del ranking, costo para la pyme y elección del umbral. |
| `entrenar.py` | Entrena, elige el umbral en validación, evalúa en prueba y guarda el modelo. |
| `comparar.py` | Prueba varios modelos con los mismos datos. |
| `predecir.py` | Carga un modelo guardado y decide sobre un pago. |
| `explicacion.py` | Dice por qué el modelo sospecha de un pago (por ahora, solo modelos lineales). |
| `experimentos.py` | Bitácora: cada entrenamiento deja una fila en `resultados/experimentos.csv`. |
| `reglas/motor.py` | Motor de reglas: una regla es «si pasa esto, entonces revisar o bloquear»; gana la más severa y dice qué regla se activó. |
| `reglas/reglas_base.py` | Reglas fijas de la línea base para H1: versión ingenua (umbrales redondos) y versión ajustada con la validación. |
| `reglas/seguridad.py` | Red de seguridad para producción: reglas duras de política (lista negra, ráfaga extrema, monto muy alto...). |
| `comparar_h1.py` | Compara el modelo con las reglas fijas, con intervalos de confianza del 95 % por remuestreo (hipótesis H1). |
| `etiquetas.py` | Etiquetas tardías: al entrenar con corte en una fecha, solo se usan los fraudes ya confirmados ese día. |
| `comparar_variables.py` | Mide cuánto aporta cada grupo de variables (sumándolo y quitándolo). |
| `reentrenamiento.py` | Simula si conviene reentrenar el modelo cada mes. |
| `api/` | API REST con OpenAPI/Swagger: decide pagos, cola de revisión, resultados y lista negra. Incluye el panel de analistas (`/panel`), el SDK y la tienda de prueba, y la documentación servida en local. |
| `comparar_alcance.py` | Mide si el historial por comercio o con toda la plataforma junta da mejor resultado. |
| `pruebas_navegador/` | Recorridos automáticos en Chromium y WebKit (documentación, panel de analistas, tienda de prueba). |
| `datos/` | Los datasets sintéticos (500, 100 mil, 500 mil y 1 millón de transacciones) y, en `datos/generadores/`, los scripts que los crean. |
| `tests/` | 219 pruebas automáticas. |
| `modelos_guardados/` | Los modelos entrenados (`.joblib`) con su ficha (`.json`). |

## Datos incluidos (`datos/`)

Todos son **sintéticos**: inventados por los generadores de `datos/generadores/` (semilla fija, así que se pueden volver a crear idénticos). No tienen datos de personas.

| Archivo | Filas | Periodo | Para qué |
|---|---|---|---|
| `dataset_antifraude_500_transacciones.csv` | 500 | 2026 | El ejemplo inicial, hecho a mano. Tiene pistas perfectas: sirve para entender, no para evaluar. |
| `dataset_antifraude_100000_transacciones.csv` | 100.000 | ene–oct 2026 | Primera versión con casos difíciles. |
| `dataset_antifraude_500000_transacciones.csv` | 500.000 | 1 ene–12 nov 2026 | **El que usa el modelo actual.** |
| `dataset_antifraude_1000000_transacciones.csv.gz` | 1.000.000 | 1 ene 2025–12 nov 2026 | El más completo: 42 columnas, 12 tipos de fraude y el fraude cambia con el tiempo. |

**El dataset de 1 millón** agrega columnas para poder construir y probar los componentes que faltan:

| Para | Columnas nuevas |
|---|---|
| Varias pymes | `id_comercio` (250 pymes, con tasa de fraude distinta cada una) |
| Huella del dispositivo | `huella_dispositivo`, `sistema_operativo`, `navegador`, `resolucion_pantalla`, `zona_horaria_dispositivo`, `es_emulador` |
| Geolocalización frente a IP | `ip_pais`, `ip_ciudad_estimada`, `distancia_ip_ciudad_km`, `ip_es_datacenter`, `ip_reputacion` |
| Identidad (KYC, 2FA) | `kyc_nivel`, `edad_cuenta_dias`, `autenticacion_2fa`, `intentos_fallidos_24h`, `cambio_credenciales_7d` |
| Biometría de comportamiento | `tiempo_formulario_seg`, `velocidad_tecleo_ms`, `pego_numero_tarjeta`, `mouse_natural` (solo en web y app) |
| Revisión manual y mejora continua | `revision_manual`, `resultado_revision`, `minutos_revision`, `contracargo`, `dias_hasta_contracargo`, `fecha_confirmacion_fraude` |

Fraudes nuevos: granja de dispositivos (una huella contra muchos clientes), navegador anti-detección, bot que prueba tarjetas, cuenta nueva con 2FA fallido y fraude amistoso. Legítimos nuevos que se parecen al fraude: viajero en el exterior, VPN de empresa, 2FA fallido por error, cliente nuevo y cambio de credenciales.

**Cosas que hay que saber de estos datos:**
- **Las columnas de «después del pago» no pueden ser entrada del modelo** (revisión, contracargo, fecha de confirmación): están en `COLUMNAS_PROHIBIDAS` y hay una prueba que lo vigila. `id_comercio` y `huella_dispositivo` tampoco entran tal cual; sirven para construir variables, por ejemplo cuántos clientes distintos usan una misma huella.
- **Las etiquetas llegan tarde.** El 6,6 % de los fraudes nunca se confirma (el cliente no reclama) y los contracargos tardan hasta 110 días (mediana de 51). Al reentrenar hay que usar solo lo que ya se sabía en esa fecha.
- **El fraude cambia con el tiempo:** el anti-detección crece, la prueba de tarjetas cae y la granja de dispositivos tiene una ola a mediados del periodo.
- **Las columnas nuevas todavía no se usan en ningún modelo.** Hoy el modelo ve las mismas variables de siempre. Medir cuánto aporta cada grupo es el siguiente paso.
- **Un error conocido en los datasets de 100 mil y 500 mil:** su calendario marca el Día de la Madre el 31 de mayo de 2026. En Colombia es el segundo domingo de mayo (10 de mayo). El de 1 millón sí usa las fechas correctas. No lo corregí en los otros dos porque cambiaría los resultados ya reportados.
- **Todo es inventado con reglas nuestras:** lo que aprenda un modelo con estas columnas son esas reglas, no el fraude real.

## Cómo crece el modelo

- **Probar un algoritmo nuevo:** agregarlo al `REGISTRO` de `modelos.py` y correr `comparar.py`.
- **Probar una variable nueva:** escribir la función en `variables.py`, listarla en un conjunto nuevo de `config.py` (por ejemplo `"historial_v2"`) y entrenar con `--conjunto historial_v2`.
- **Ver si mejoró:** abrir `resultados/experimentos.csv`. Cada fila trae modelo, variables, recall, precisión, falsas alarmas y costo en la prueba.

### Reglas que no se rompen

1. **La prueba (septiembre en adelante) no se usa para elegir nada.** El umbral se elige en validación (jul–ago). Hay una prueba automática que lo vigila.
2. **El historial del cliente usa solo compras anteriores.** Nada del futuro entra en una fila. También hay prueba automática.
3. **Estas columnas nunca son entrada del modelo:** `es_fraude`, `patron`, `nivel_riesgo`, `id_cliente`, `numero_transaccion`. El código se niega a entrenar si alguna se cuela.
4. **Cada modelo guardado dice con qué datos se entrenó** (huella del archivo) y que eran sintéticos.

## Resultados actuales (prueba: septiembre a 12 de noviembre, datos sintéticos)

Sin modelo, el fraude de la prueba cuesta unos **5.484 millones de COP**.

| Modelo | Calidad del ranking | Costo (millones COP) | Falsas alarmas |
|---|---|---|---|
| Regresión logística (modelo actual) | 0,85 | 337 | 9.019 |
| Árbol de decisión | 0,84 | 338 | 5.960 |
| Random Forest | 0,92 | 201 | 7.105 |
| Gradient Boosting | 0,94 | 165 | 4.475 |

El costo usa un supuesto de 15.000 COP por cada falsa alarma. Gradient Boosting es el más barato; la regresión logística es el modelo actual porque explica cada decisión y entrena en menos de un segundo.

## Modelos entrenados con toda la información (dataset de 1 millón)

Se agregaron grupos de variables construidas con las columnas nuevas: `perfil` (qué tan habitual es la IP y la franja horaria para ese cliente), `huella` (cuántos clientes usan la misma huella, emulador, equipo que no cuadra), `geo_ip`, `identidad` y `comportamiento`. Todo se mide en la prueba (septiembre en adelante) con el umbral elegido en la validación.

| Modelo | Variables | Calidad del ranking (AP) | Costo (millones COP) |
|---|---|---|---|
| Regresión logística | historial (antes) | 0,819 | 447,5 |
| Regresión logística | **completo** | **0,872** | **396,0** |
| Gradient Boosting | historial (antes) | 0,904 | 301,7 |
| Gradient Boosting | **completo** | **0,920** | **273,9** |

Sin ningún modelo, el fraude de la prueba cuesta 4.927 millones.

**Cuánto aporta cada grupo** (cambio en AP al sumarlo al historial):

| Grupo | Logística | Gradient Boosting |
|---|---|---|
| identidad (KYC, 2FA, antigüedad de la cuenta) | +0,031 | +0,009 |
| geo_ip (país, distancia, reputación de la IP) | +0,023 | +0,010 |
| comportamiento (tiempo en el formulario, teclado, mouse) | +0,022 | +0,005 |
| huella del dispositivo | +0,019 | +0,007 |
| perfil habitual (IP y hora) | +0,001 | +0,001 |

**Cómo leerlo con cuidado:**
- **La mejora real es moderada.** Con Gradient Boosting, todo junto sube el AP 0,016. La regresión logística mejora más (0,053) porque Gradient Boosting ya captaba parte de eso por su cuenta.
- **El perfil habitual no aporta nada** (+0,001) y se podría quitar.
- **Casi todos los fraudes ya se detectaban** (granja, bots, navegador anti-detección: recall cercano a 1,00 incluso antes). Esto pasa porque el generador dio a esos fraudes señales fuertes en las columnas viejas. Lo que sí mejora es la precisión (menos falsas alarmas) y el fraude disimulado (recall de 0,65 a 0,73 con Gradient Boosting).
- **El fraude amistoso casi no se detecta** (recall de 0,14 a 0,21) y está hecho así a propósito: se ve como una compra normal.
- **Las diferencias de costo de ±10 a 15 millones son ruido** de elegir el umbral; el AP es más confiable.
- **Reentrenar cada mes no mejoró nada** en estos datos (AP de 0,919 contra 0,919). El mecanismo existe y se puede simular, pero no demostró su beneficio, así que «modelo adaptativo» sigue sin cerrarse.
- **Las etiquetas tardías no empeoraron el resultado** (costo de 265,9 millones entrenando solo con lo ya confirmado, frente a 273,9 con todas las etiquetas): la diferencia está dentro del ruido.

Modelos guardados (`modelos_guardados/`). **El modelo en uso es `gradient_boosting_completo_v0.3.0`**: entrenado con el historial **por comercio** (como lo calcula la API) y con etiquetas tardías, sobre el dataset de versión 2 con 30 % de movilidad. En la prueba: calidad (AP) 0,922, recall 0,930, precisión 0,451, 4.957 falsas alarmas y 244 millones de costo (sin modelo: 4.928). Junto a él queda `logistica_completo_v0.3.0`, que es explicable (AP 0,876, costo 389 millones). Las versiones 0.1.0 y 0.2.0 se conservan como historial. Gradient Boosting todavía **no da motivos** por variable; la logística sí. Las reglas activadas siempre se informan.

## Historial por comercio frente a red compartida

La API calcula el historial de un cliente (y de una huella) **dentro de cada comercio**: así cada comercio solo ve lo suyo. Pero hasta la versión 0.2.0 el modelo se había entrenado con todos los comercios juntos, y eso hacía que ciertas variables valieran menos en producción. Ahora cada modelo guarda con qué alcance se entrenó (`--alcance comercio` o `plataforma`) y `GET /v1/salud` **avisa** si el modelo y la API no coinciden. Para medir el efecto se creó una versión 2 del dataset (`--afinidad`: cada cliente compra sobre todo en 1 a 3 comercios habituales) y variantes con `--movilidad=` (parte de las compras legítimas que caen en un comercio nuevo para el cliente). Mismo modelo, mismos datos; solo cambia el alcance (`python -m modelo_ia.comparar_alcance`):

| Compras legítimas en comercios nuevos | AP plataforma | AP por comercio | Costo plataforma / por comercio (millones) |
|---|---|---|---|
| 0 % | 0,917 | 0,940 | 265 / 208 |
| 30 % | 0,917 | 0,922 | 267 / 244 |
| 60 % | 0,917 | 0,914 | 271 / 266 |

**Cómo leerlo:** con 0 % la ventaja del historial por comercio es un **artefacto**: si los clientes legítimos nunca compran en comercios nuevos, «cliente sin historial en este comercio» delata al fraude, algo que en la vida real no pasa. A medida que el dato se parece más a la realidad, la ventaja desaparece y ambos empatan (con 60 % la plataforma gana por 0,003). Conclusión: en estos datos sintéticos, aislar por comercio **casi no cuesta nada**, y el beneficio de compartir historial entre comercios **no se puede demostrar** sin datos reales. Compartir exigiría además identificar al cliente igual en todos los comercios y resolver la Ley 1581: es una decisión de negocio que se toma con datos reales.

## API REST (OpenAPI / Swagger)

Los comercios consumen el modelo por HTTP. Por cada pago reciben **APROBADA**, **RECHAZADA** o **EN_REVISION**.

```bash
# 1. Una sola vez: un secreto propio y las claves (se muestran UNA vez en tu terminal; no las pegues en ningún chat)
export MODELO_IA_PEPPER="un-secreto-largo-que-solo-tu-conoces"
python -m modelo_ia.api.admin crear-clave --rol admin
python -m modelo_ia.api.admin crear-clave --rol analista
python -m modelo_ia.api.admin crear-comercio --nombre "Mi Tienda"
python -m modelo_ia.api.admin crear-clave --rol comercio --comercio-id 2

# 2. Levantar el servicio (solo se ve desde tu computador) y abrir la documentación
python -m modelo_ia.api.servidor          # http://127.0.0.1:8000/docs  (Swagger)  y  /redoc

# 3. El contrato para entregar a los comercios
python -m modelo_ia.api.exportar_openapi  # escribe modelo_ia/api/openapi.json
```

| Endpoint | Quién | Para qué |
|---|---|---|
| `POST /v1/evaluaciones` | comercio | Decidir un pago. Reintentos seguros con el mismo `id_transaccion`. |
| `GET /v1/evaluaciones/{id}` | comercio, analista | Consultar la decisión; en revisión, mira `decision_final`. |
| `POST /v1/evaluaciones/{id}/resultado` | comercio | Informar fraude confirmado, contracargo o legítimo. |
| `GET /v1/revisiones`, `POST /v1/revisiones/{id}/resolver` | analista | Cola de revisión manual y su resolución (una sola vez). |
| `GET/PUT/DELETE /v1/lista-negra` | comercio | Clientes que se rechazan siempre en ese comercio. |
| `POST /v1/comercios` | admin | Crear un comercio y su clave. |
| `GET /v1/modelo`, `GET /v1/salud` | cualquiera / sin clave | Modelo en uso y salud del servicio. |

### Probarla en tu computador (modo demo)

```bash
python -m modelo_ia.api.demo               # abre http://127.0.0.1:8000/docs
python -m modelo_ia.api.demo --reiniciar   # borra la base de demostración y empieza de cero
```

| Página | Qué es |
|---|---|
| `/docs` | Swagger ya autorizado como **comercio**: elige un ejemplo en `POST /v1/evaluaciones` → **Try it out** → **Examples** → **Execute**. |
| `/docs-analista`, `/docs-admin` | Swagger ya autorizado como analista y como administrador. |
| `/panel` | **Pantalla de analistas**: cola de revisión, detalle del pago y botones Aprobar / Rechazar con confirmación. En la demo entra sola. |
| `/tienda` | **Tienda de prueba** con el SDK: llenas una compra y ves la decisión de la API. |
| `/redoc` | Documentación de lectura. |

- Hay 12 pagos de ejemplo con su resultado esperado: aprobada, dos en revisión, rechazada, viaje imposible (dos pasos), modo degradado, lista negra, un error por enviar datos de tarjeta, dos donde **solo se manda la IP** y uno donde **no se manda el estado del dispositivo**. Una prueba comprueba que cada uno siga dando lo que dice con el modelo en uso (así la prueba avisó cuando cambié de modelo y un ejemplo dejó de cumplirse).
- **Para repetir un ejemplo, cambia `id_transaccion`** (si no, devuelve la respuesta guardada). Y si quieres el mismo resultado del primer intento, cambia también `id_cliente`: al segundo pago, ese cliente ya tiene historial y el modelo lo trata distinto.
- Las claves de la demo están en `modelo_ia/api/demo/claves_demo.txt` (solo las puede leer tu usuario). Sirven únicamente para esa base de demostración. La demo solo escucha en `127.0.0.1`: no se puede exponer a la red, y nunca debe usarse con datos reales.

**El panel de analistas** (`/panel`): se entra con la clave de analista (se guarda solo mientras la pestaña esté abierta, nunca en `localStorage`), lista los pagos pendientes con riesgo, monto y tiempo en cola, y cada uno abre con motivos, reglas activadas y datos del pago. Aprobar o rechazar pide una **segunda confirmación** y admite una nota. Si otra persona resuelve primero, avisa y actualiza. Todo texto que viene de los comercios se muestra como texto, nunca como HTML (hay una prueba que lo impide y una prueba de ataque en navegador).

**El SDK y la tienda de prueba:** `estaticos/sdk/antifraude.js` corre en el navegador del comprador y arma la huella del dispositivo, el sistema, el navegador y el comportamiento al llenar el formulario (tiempo, velocidad de tecleo, si se pegó la tarjeta, movimiento del mouse). **Nunca lee lo que el comprador escribe** (una prueba lo vigila) y no envía nada por su cuenta. La tienda muestra la arquitectura correcta: el navegador manda las señales al servidor de la tienda y **es ese servidor quien llama a la API con su clave**; la clave jamás llega al navegador. La tienda manda **solo la IP** del comprador y la API la ubica y la evalúa. El KYC y el 2FA están **simulados** y marcados como tal.

**La IP del comprador y los datos de red.** Un comercio normal no sabe clasificar una IP (¿es VPN?, ¿de qué país?, ¿qué reputación tiene?), así que **solo manda `red.ip`**, la que ve su servidor, y la API hace el resto con un *proveedor de IP*: tipo de conexión, país, ciudad, si es un centro de datos, reputación y distancia a la ciudad declarada. Reglas:
- **La IP cruda no se guarda, no se devuelve y no se registra.** Se usa en memoria; en la base solo queda una huella irreversible (HMAC con el secreto del servicio) para reconocer repeticiones. Hay pruebas que buscan la IP en la base, en las respuestas, en la auditoría y en los registros.
- **Lo que mande el comercio manda.** Si envía también `tipo_ip`, `pais`, etc., eso se respeta y el proveedor solo completa lo que falta. Un comercio que ya tenga su propio proveedor puede seguir mandando todo y no mandar la IP.
- La respuesta dice de dónde salió cada dato (`red_origen`: `comercio`, `proveedor:<nombre>` o `proveedor_fallo`). Si el proveedor falla, la API no se cae: usa lo que mandó el comercio y, si no alcanza, queda en modo degradado.
- El proveedor es una **interfaz** (`api/proveedores_ip.py`): para usar uno real se escribe una clase con `consultar(ip)` y se elige con `MODELO_IA_PROVEEDOR_IP`. El que viene (`simulado`) es solo de demostración: reconoce rangos de IP reservados para documentación (`192.0.2.x`, `198.51.100.x`, `203.0.113.x`) y nada más. `GET /v1/salud` avisa mientras se use.
- Una IP puede ser dato personal: cada comercio debe cubrir este tratamiento en su política (Ley 1581). Es una decisión legal, no solo técnica.

**El estado del dispositivo es opcional** (`dispositivo.estado`: «Conocido», «Nuevo» o «No reconocido»). Lo sabe el sistema de cuentas del comercio (¿este cliente ya usó este equipo?), no el SDK. Si no llega, la API usa un **segundo modelo entrenado sin esa variable** (`modelo_actual_sin_estado.joblib`, se entrena con `--sin-estado --como-actual-sin-estado`) y la respuesta dice `estado_origen: no_enviado`. Si ese modelo no existe, el pago queda en modo degradado y `/v1/salud` lo avisa.

La primera idea fue que la API *dedujera* el estado del historial y se lo diera al modelo normal. Se midió antes de hacerlo (`python -m modelo_ia.comparar_estado`, dataset de versión 2 con 30 % de movilidad, historial por comercio):

| Qué recibe el modelo como estado del dispositivo | Calidad (AP) | Costo (millones) | Falsas alarmas |
|---|---|---|---|
| Lo que manda el comercio | 0,922 | 244 | 4.957 |
| Deducido del historial (modelo reentrenado así) | 0,903 | 269 | 5.986 |
| Sin la variable (**lo que se implementó**) | 0,902 | 265 | 7.634 |
| Deducido del historial, **sin reentrenar** el modelo normal | 0,797 | 451 | 9.989 |

- **Dárselo deducido al modelo normal lo empeora mucho** (casi duplica el costo), porque la variable cambia de significado: en los datos originales «Nuevo» es el 9 % de las compras y deducido sale el 55 %.
- **Deducirlo no aporta nada** frente a no tenerlo (0,903 contra 0,902): el historial ya dice lo mismo (`dispositivo_nuevo_cliente`, `n_compras_previas`). Por eso se eligió el modelo sin estado en vez de inventarle un valor al modelo normal.
- **Lo que se pierde sin el dato** (unos 0,02 de calidad y más falsas alarmas) es información que el sistema de cuentas del comercio sí tiene y la API no puede deducir, sobre todo el «no reconocido». En estos datos sintéticos parte de esa ventaja es un artefacto: ese campo lo asigné yo según el tipo de fraude. Con datos reales la diferencia podría ser otra.
- **Ojo con el efecto en un pago concreto:** un pago de primera compra con señales algo raras (tarjeta pegada, formulario rápido) dio 0,39 (EN_REVISION) con «Conocido» y 0,82 (RECHAZADA) sin estado. Sin el dato, el modelo castiga más a los clientes nuevos con señales dudosas. Por eso la tienda de prueba usa «Conocido» por defecto.
- Recomendación para los comercios: **mandarlo si pueden** (el pago se evalúa con el modelo más preciso) y no preocuparse si no.

**Qué garantiza:**
- **Cada comercio solo ve lo suyo.** Pedir algo de otro comercio responde `404`, no `403`, para no revelar que existe.
- **Nunca acepta datos de tarjeta.** Rechaza campos de tarjeta y números que pasan la verificación de tarjetas, y no repite lo enviado en el mensaje de error.
- **Un reintento no se cuenta dos veces y el historial no se corrompe.** El cálculo se hace sin bloquear la base y se guarda con un bloqueo corto; si entra otro pago del mismo cliente mientras tanto, se recalcula.
- **Las variables en línea son las del entrenamiento**, también con dos comercios a la vez: una prueba compara miles de valores contra el cálculo por lotes y son iguales.
- **Si falla el modelo o faltan datos:** modo `degradado`; el pago queda en revisión (salvo que una regla de seguridad lo rechace). Nunca se aprueba a ciegas.
- Claves guardadas con *hash*, límite de peticiones por clave, tamaño máximo de petición, registro de auditoría que no se puede editar ni borrar, cabeceras de seguridad y errores en formato estándar `application/problem+json`.
- **Swagger y ReDoc se sirven desde el propio servidor** (sin CDN), con la huella de cada archivo de terceros en `estaticos/MANIFIESTO.txt` (una prueba avisa si alguno cambia). Se probó que no pidan nada a internet y que no se desborden a 320 px.

**Servidor local con HTTPS y varios procesos:**

```bash
python -m modelo_ia.api.servidor --https         # certificado de prueba autofirmado (nunca para producción)
python -m modelo_ia.api.servidor --workers 4     # varios procesos
```

HSTS queda **apagado por defecto** (`MODELO_IA_HSTS`), porque en `localhost` el navegador lo recordaría durante meses. La política de contenido de las páginas (`/docs`, `/panel`, `/tienda`) está en modo **«solo observar»**: con unos días de uso sin avisos en la consola de Chromium, se pasa a bloquear con `MODELO_IA_CSP=enforce`. `MODELO_IA_DURABILIDAD=FULL` fuerza cada guardado a disco (más lenta).

**Medido** en un computador, con el modelo en uso y pagos de ejemplo (`python -m modelo_ia.api.prueba_carga`, solo contra una base de prueba), con 20 % de reintentos del mismo pago:

| Procesos | A la vez | Pagos por segundo | Mediana | p95 | p99 | Errores |
|---|---|---|---|---|---|---|
| 1 | 1 | 65 | 20 ms | 23 ms | 27 ms | 0 |
| 1 | 8 | 66 | 160 ms | 192 ms | 330 ms | 0 |
| 1 | 32 | 57 | 602 ms | 1,2 s | 1,75 s | 0 |
| 4 | 8 | 89 | 108 ms | 157 ms | 274 ms | 0 |
| 4 | 32 | 77 | 448 ms | 929 ms | 1,4 s | 0 |

La primera prueba de carga **encontró un problema real**: con muchas peticiones a la vez, la velocidad *bajaba* a unas 35 por segundo, el peor caso tardaba hasta 30 segundos y aparecían errores 503, porque cada evaluación mantenía la base bloqueada mientras calculaba. Se corrigió calculando fuera del bloqueo (ver arriba). Ahora no hay errores, pero el límite es el cálculo: **unos 65 pagos por segundo por proceso** (más de 5 millones al día), de los cuales ~13 ms son construir las variables con pandas y ~8 ms el modelo y las reglas. Más procesos ayudan poco porque SQLite sigue siendo un solo escritor; para crecer hay que pasar a PostgreSQL y acelerar el cálculo de variables. La documentación, el panel y la tienda se recorrieron en Chromium y WebKit (escritorio, 320 px, iPhone 13 y Pixel 5): `python modelo_ia/pruebas_navegador/panel.py` (ver `pruebas_navegador/README.md`).

**Lo que NO está hecho:**
- **No está desplegada.** Falta HTTPS con certificado real (y HSTS), un servidor de producción, PostgreSQL, copias de seguridad y monitoreo. Todo lo anterior se hizo y se probó **en local**.
- **El SDK solo existe para navegador**, no para apps móviles, y es de demostración (el «mouse natural» es una heurística simple).
- **No hay proveedores reales.** La ubicación y la reputación de las IP las da un proveedor **simulado** (no ubica IPs reales ni detecta VPN, proxy o centros de datos de verdad; hay que contratar uno real, que normalmente es de pago), y el KYC y el 2FA también están simulados.
- **La pantalla de analistas es una primera versión**: falta el protocolo de revisión, los tiempos máximos de respuesta, que cada analista tenga su propia identidad (hoy se entra con una clave de rol) y avisar al comercio cuando se resuelve (hoy consulta con `GET`).
- **Falta pasar la política de contenido a modo «bloquear»** tras unos días de uso, y los avisos de WebKit sobre ella son esperados (ese navegador no hace nada en modo «solo observar»).
- **Velocidad:** ~65 pagos por segundo por proceso; acelerar el cálculo de variables (sin pandas) es posible pero no se hizo.
- **La cola de revisión sería enorme** con el umbral actual: en una muestra de 2.000 pagos, el 13,6 % de los legítimos quedó en revisión. Hay que ajustar los umbrales con el costo real de revisar.
- **Gradient Boosting no devuelve motivos por variable** (solo las reglas activadas).
- **No se probó un celular real ni Android real**, solo perfiles emulados de Chromium y WebKit.

## Panel de control: ver todo, filtrar y rastrear (API de solo lectura)

**Identificador de seguimiento.** Cada pago recibe, además del `id_transaccion` que pone el comercio, un **`id_seguimiento` que asigna este sistema** (por ejemplo `AF-7K3Q-9M2X`: 8 caracteres legibles, sin letras que se confundan). Llega en la respuesta y en la cabecera `X-Id-Seguimiento`, se conserva igual si el comercio repite el pago, y sirve para consultar (`GET /v1/evaluaciones/AF-7K3Q-9M2X`), buscar en el panel y hablar con soporte. Quedan tres identificadores por pago: el del comercio, el de seguimiento (para personas) y el interno (`id_evaluacion`).

**Endpoints** (etiqueta «Panel de control» en Swagger; todos son `GET`):
- `/v1/panel/transacciones`: pagos con filtros por comercio, fechas, decisión, modo, monto, cliente, regla, IP y dispositivo; orden y paginación.
- `/v1/panel/transacciones/{id}`: **todo sobre un pago**: identificadores, datos recibidos y cuáles NO se mandaron, probabilidad y umbrales, reglas activadas y su efecto, la cadena de decisión paso a paso, revisión, resultado posterior, auditoría y pagos relacionados.
- `/v1/panel/resumen`: totales, por día, por comercio, reglas más activadas, distribución del puntaje, calidad (modo degradado, sin estado, latencia).
- `/v1/panel/vinculos`: varios clientes desde una misma IP, un mismo dispositivo con varios clientes, un cliente con varias IPs y ráfagas desde una IP.
- `/v1/panel/comercios`: lista para el filtro (solo analistas y administradores).

**Qué garantiza:** analistas y administradores ven todos los comercios; un comercio solo ve lo suyo (si pide otro, 404). La **IP y la huella del dispositivo nunca se muestran**: solo códigos cortos de 8 caracteres que permiten reconocer repeticiones. Nada del usuario entra al SQL como texto (parámetros, listas fijas y validación). No hay ninguna forma de escribir desde el panel.

**Las pantallas (`/dashboard`):** cuatro vistas sobre esa API, con la clave de analista (en la demo entra sola). **Resumen** (indicadores, pagos por día, reglas más activadas, distribución del puntaje, calidad y tabla por comercio), **Transacciones** (13 filtros, orden, paginación; cada fila lleva al detalle), **Detalle de un pago** (identificadores, decisión y umbrales, la decisión explicada paso a paso, reglas, datos recibidos con lo que NO se mandó, pagos relacionados, revisión y auditoría) y **Vínculos** (misma IP, mismo dispositivo, cliente con varias IPs, ráfagas; cada fila salta a sus pagos). Solo hace peticiones `GET`, sin librerías ni fuentes externas, y todo texto de la API se muestra como texto. El diseño parte de tres referencias de Dribbble (azul eléctrico con titulares enormes, serifa con botones píldora oscuros, verde profundo sobre crema); los colores, radios y espacios viven como variables en `dashboard.css`. Recorrido de navegador: `python modelo_ia/pruebas_navegador/dashboard.py`.

**Diseño compartido y filtros.** Las tres pantallas (dashboard, revisión manual y tienda) usan una sola hoja, `estaticos/comun/diseno.css`, con colores, radios, espacios y piezas repetidas como variables, en modo claro y oscuro: antes de escribir un estilo nuevo se busca ahí. La **revisión manual** (`/panel`) se rehízo con indicadores arriba (por revisar, monto en la cola, riesgo alto, la que más espera), **filtros** (buscar por ID o cliente, comercio, riesgo, monto mínimo, fechas, orden) y cada pago como una fila con su medallón de riesgo que se abre en dos columnas: «por qué dudó» y «tu decisión». Esos filtros son del servidor: `GET /v1/revisiones` acepta ahora `comercio_id`, `desde`, `hasta`, `monto_min`, `monto_max`, `riesgo_min`, `riesgo_max`, `regla`, `q` y `orden`, y responde también `monto_total_cop` y el nombre del comercio; `/v1/panel/*` acepta además `riesgo_min`, `riesgo_max` y `q`.

**La tienda (`/tienda`) es un checkout.** Contacto y entrega, método de pago (Visa, Mastercard, PSE, Nequi), resumen del pedido y, al pagar, una respuesta en palabras de cliente («¡Pago aprobado!», «Estamos revisando tu pago», «No pudimos procesar el pago») con el número de seguimiento y un enlace al detalle en el dashboard. **La simulación de proveedores ya no se llena a mano:** por defecto (modo `auto`) la conexión es la que ve el servidor (si pagas desde este mismo computador, una IP de prueba residencial, porque 127.0.0.1 no se puede ubicar; las cabeceras `X-Forwarded-For` no se creen), el dispositivo lo detecta el SDK y la verificación, la antigüedad, el 2FA y si ya usó ese equipo salen de los registros de la tienda (`CLIENTES_DEMO`; un código desconocido es un cliente nuevo). El navegador no puede cambiar nada de eso. Para probar un escenario (VPN, cuenta nueva) hay un interruptor «Quiero probar otro escenario (solo demo)». El nombre, el vencimiento y el CVV escritos nunca se leen ni se envían (lo vigila una prueba y el recorrido de navegador).

**Para llenar la demo:** `python -m modelo_ia.api.cargar_ejemplos` crea 3 comercios de ejemplo y envía ~2.000 pagos **por la API real**, con escenarios sospechosos (misma IP, mismo dispositivo, un cliente con muchas IPs, viaje imposible, ráfagas, proxy). Todo es inventado.

**Límites:** el análisis entre comercios (misma IP en comercios distintos) es lo que dejó pendiente la decisión de la Ley 1581: aquí lo ve el analista, no el comercio. Las ráfagas usan una ventana fija de 10 minutos. Con millones de filas habrá que medir el rendimiento de los resúmenes (hoy hay índices, pero se probó con unos miles).

## Despliegue en un servidor real (preparado, todavía no desplegado)

La carpeta `despliegue/` deja todo listo para llevar el servicio a un servidor, con su manual paso a paso en `despliegue/LEEME.md`: imagen del servicio sin permisos de administrador y con el código en solo lectura (`Dockerfile`), cuatro piezas con `docker-compose.yml` (la API, Caddy con HTTPS automático, copias de seguridad y vigilancia), versiones exactas de las librerías (`requirements-produccion.txt`) y un ejemplo de variables (`.env.ejemplo`).

- **Secretos.** En producción (`MODELO_IA_ENTORNO=produccion`) el servicio **se niega a arrancar** si el secreto es el de desarrollo o tiene menos de 32 caracteres, y puede leerlo de un archivo (`MODELO_IA_PEPPER_ARCHIVO`) para que no viaje en una variable de entorno. La API queda en una red **sin salida a internet**.
- **Copias de seguridad** (`python -m modelo_ia.despliegue.respaldo`): copia consistente aunque el servicio esté escribiendo, revisión de integridad antes de guardarla, cifrado AES-256 y autenticación con HMAC (un archivo alterado, truncado o con otra frase se rechaza antes de abrirlo), frase leída de un archivo privado y nunca de la línea de comandos, permisos 600, rotación (7 diarias, 4 semanales, 3 mensuales) y un gancho para copiarlas fuera del servidor.
- **Vigilancia** (`python -m modelo_ia.despliegue.vigilancia`): servicio, tiempo de respuesta, certificado, disco, integridad de la base y, sobre todo, que las copias **sigan haciéndose**; avisa solo cuando el estado cambia.
- **Ensayo de desastre hecho** con el servicio real en modo producción (2 procesos): 25 pagos guardados, copia con el servicio funcionando, base destruida, restauración y arranque. Se recuperaron los 25 pagos con la misma decisión y el mismo identificador de seguimiento, y las claves siguieron sirviendo. Con un secreto distinto, las claves guardadas dejan de servir (por eso el secreto se guarda aparte).
- **Ensayo con Docker (en un Mac con Colima, dominio `localhost`, certificado local de prueba).** Se construyó la imagen (Python 3.12, 672 MB; sin datos, pruebas ni bases adentro), se validó el Caddyfile con la imagen oficial de Caddy y se levantaron las cuatro piezas con `docker compose`. Resultado: HTTPS con HSTS y redirección, lo de demostración cerrado (404) y tope de 64 KB (413); la API **no puede salir a internet**, no escribe en su código y corre sin privilegios; se **reinicia sola** si el proceso se cae; el modelo da **exactamente los mismos puntajes** con Python 3.12 (contenedor) y 3.9 (desarrollo); una copia hecha en el contenedor (OpenSSL 3.5) se abre en el Mac (LibreSSL 3.3); y tras **destruir el volumen de datos**, la restauración devolvió los pagos, las claves y los identificadores de seguimiento. El ensayo encontró un error del manual (restaurar sin `--no-deps` arranca una API con base vacía); ya está corregido y vigilado por una prueba.
- **Qué NO se probó todavía:** un certificado real con un dominio público, un servidor Linux de verdad (en Linux los secretos de 600 pueden necesitar `chmod 644`, ver el manual), la copia hacia otro lugar y la vigilancia mirando el certificado público desde afuera.
- **Límites:** un solo servidor (pérdida máxima = el intervalo entre copias), SQLite en vez de PostgreSQL (alcanza para un piloto: ~65 evaluaciones por segundo por proceso) y el límite de peticiones se cuenta por proceso.

## Adaptador de historial: el diagnóstico para un comercio (`python -m modelo_ia.historial`)

Sirve para el «Camino A» del plan de piloto: un comercio entrega un archivo con sus pagos pasados (CSV o Excel, con los nombres de columna que él use) y se le devuelve cuánto fraude habría detectado el sistema y cuánto dinero habría ahorrado, **sin tocar su operación ni tu base real**.

```bash
python -m modelo_ia.historial ejemplo   --salida carpeta            # archivo INVENTADO de una tienda imaginaria, para probar
python -m modelo_ia.historial sugerir   pagos.csv --salida mapeo.json   # propone qué columna es cada campo (revísalo)
python -m modelo_ia.historial verificar pagos.csv --mapeo mapeo.json    # cuántas filas sirven y qué falta (no evalúa)
python -m modelo_ia.historial evaluar   pagos.csv --mapeo mapeo.json --salida carpeta
```

**Cómo funciona.** (1) El *mapeo* (un JSON) dice qué columna del archivo es cada campo de la API, cómo se traducen sus textos («Tarjeta crédito Visa» → `VISA`) y cuál es la columna de resultado (contracargo o fraude). (2) Cada fila se convierte con las mismas reglas de la API, incluido el rechazo de números de tarjeta. (3) Los pagos se reproducen en **orden de fecha** por el mismo servicio de la API, sobre una base temporal que se borra al terminar, moviendo el reloj a la fecha de cada pago: las señales de historial (compras en 24 h, cliente nuevo, viaje imposible) salen como habrían salido ese día. (4) Se mide contra los resultados reales y se escribe `informe.md` (para leer), `informe.json` y `resultados.csv` (decisión por pago, identificado por su fila en el archivo).

**Elige solo el modelo.** Un modelo con más señales detecta más pero exige más datos; si faltan, el servicio entra en modo degradado y manda todo a revisión (el primer intento con el archivo de ejemplo dio 100 % degradado). El adaptador prueba, del más completo al menos, cuál tiene TODO lo que pide con al menos 95 % de cobertura: `completo`, `perfil` (hora, historial y perfil de red) o `historial`. El informe dice cuáles señales no se usaron y qué datos pedirle al comercio para usarlas. Con el archivo de ejemplo se eligió `perfil`.

**Privacidad.** Los códigos de cliente, pedido y dispositivo se vuelven códigos irreversibles solo de letras antes de evaluar (con dígitos, a veces parecían un número de tarjeta y la API los rechazaba); las IP no se guardan; los motivos de rechazo hablan de campos, nunca de valores; la consola solo imprime conteos. Con `--sin-seudonimos` se mantienen los originales (solo para depurar).

**Límites que el informe repite.** (a) El proveedor de IP es simulado: con IP reales casi toda la red queda «desconocida», salvo que el archivo traiga tipo de IP, país y ciudad. (b) Los pagos de los últimos `--madurez-dias` (90) no se miden: sus contracargos todavía pueden llegar. (c) Con menos de 30 fraudes el margen de error es enorme y el informe lo dice. (d) Los modelos se entrenaron con datos sintéticos y sus umbrales no están calibrados a la tasa de fraude de un comercio: en el archivo de ejemplo el nivel `perfil` marcó como dudoso más del 40 % de los pagos. Esos umbrales hay que recalibrarlos con los datos reales del comercio. (e) Un modelo con identidad parcial (solo edad de la cuenta y verificación, sin doble factor) se probó y **se descartó porque no aporta** (ver «Qué señales aportan de verdad»).

## Qué señales aportan de verdad (y una idea que se descartó)

Cuando un comercio no tiene todos los datos, el adaptador de historial usa un modelo más pequeño. Para saber dónde conviene poner el esfuerzo, se midió cuánto aporta cada grupo de señales por separado, sumado al historial. Gradient Boosting, sin el estado del dispositivo, historial por comercio, conjunto de prueba sintético (datos inventados: sirve para comparar grupos entre sí, no para prometer resultados reales):

| Señales que usa el modelo, además del historial | Calidad (AP) | Costo (millones de COP) |
|---|---|---|
| Solo historial | 0,811 | 343 |
| + identidad parcial (edad de la cuenta y verificación) | 0,817 | 346 |
| + identidad completa (con doble factor, intentos fallidos y cambio de contraseña) | 0,858 | 331 |
| + huella del dispositivo | 0,856 | 313 |
| + ubicación de la IP | 0,864 | 306 |
| + comportamiento en el formulario | 0,861 | 315 |
| Todas juntas | 0,902 | 266 |

- **La identidad parcial se probó y se descartó.** Se construyó un grupo con solo la edad de la cuenta y el nivel de verificación y dos modelos que lo usaban. No mejoró nada (0,811 a 0,817; el costo incluso subió) y con el archivo de historial inventado el recall bajó de 0,84 a 0,72 (con 32 fraudes, los márgenes de error se solapan). El código y los modelos no se conservaron. La idea de partida, que era «lo que más ayudaría a un comercio típico», era una suposición sin medir.
- **El valor de la identidad está en el doble factor, los intentos fallidos y el cambio de contraseña**, las señales de toma de cuentas; la edad de la cuenta y la verificación casi no pesan.
- **Lo que más sube la calidad** son la ubicación de la IP, el comportamiento, la huella y la identidad completa; juntas llevan la calidad de 0,81 a 0,90. Por eso, para el piloto, las palancas son: un proveedor de IP real, instalar el SDK en la tienda del comercio (huella y comportamiento) y pedirle los datos de doble factor, intentos fallidos y cambios de contraseña.
- Son datos sintéticos: con datos reales de un comercio el orden podría cambiar, y eso es justo lo que el piloto debe medir.

## Prueba de la metodología con datos públicos y reales

`python -m modelo_ia.validacion_publica` repite la forma de trabajar (corte por tiempo, umbral elegido por costo en validación, logística frente a Gradient Boosting) sobre un conjunto **público y real**: 284.807 pagos con tarjeta de comerciantes europeos, 492 fraudes (copia de OpenML, id 1597). Se descarga solo y no se guarda en el repositorio.

**Qué valida y qué no.** Valida la metodología sobre fraude real, no inventado por nosotros. **No** valida nuestras 47 variables: este conjunto solo trae 28 columnas anónimas (V1…V28) y el monto, sin dispositivo, IP ni comportamiento. Los costos están en euros y el costo de una falsa alarma no es un dato real, por eso se prueban 1, 5 y 25 euros.

**Supuesto:** la copia no trae la hora, así que se usa el orden de las filas (60 % entrenar, 20 % validar, 20 % probar). Se comprobó que el orden es cronológico: la variable V3 baja con el tiempo en el archivo original, y aquí su correlación con el número de fila es −0,405 (con las filas mezcladas, 0,004).

| Corte por tiempo, prueba con 75 fraudes | Calidad (AP) | Costo con falsa alarma de 5 € | Sin modelo |
|---|---|---|---|
| Logística | 0,744 | 3.547 | 7.729 |
| Gradient Boosting | 0,758 | 3.927 | 7.729 |

- **El enfoque funciona con fraude real:** con cualquiera de los dos modelos el costo baja entre 47 % y 66 % frente a no tener modelo, según el costo de la falsa alarma.
- **Gradient Boosting no es claramente mejor que la logística** (0,758 frente a 0,744, con solo 75 fraudes en la prueba, dentro del ruido). La ventaja que vemos en los datos sintéticos no se confirma aquí. Lo razonable es seguir midiendo ambos con datos reales antes de decidir.
- **El umbral elegido con tan pocos fraudes es inestable:** con Gradient Boosting y una falsa alarma de 1 €, el umbral dio 908 falsas alarmas; con 5 €, solo 9. Hay que elegir el umbral con más fraudes (o con revisión de varios periodos).
- **El corte al azar no salió más «bonito»** de lo que esperaba (AP 0,68 frente a 0,74 por tiempo). No son comparables: la parte de prueba al azar tiene otros fraudes (13.578 € sin modelo frente a 7.729 €), así que no se concluye nada de ahí.

## Hipótesis H1: modelo frente a reglas fijas (con datos sintéticos)

H1 dice que el modelo detecta más fraude (recall) que las reglas fijas **sin subir** los falsos positivos. Se probó con `python -m modelo_ia.comparar_h1`, sobre la prueba (septiembre en adelante, 112.798 pagos y 4.575 fraudes). Las reglas se ajustaron con la validación, no con la prueba.

| Método | Recall | Precisión | Falsos positivos (tasa) | Falsas alarmas | Costo (millones COP) |
|---|---|---|---|---|---|
| Reglas ingenuas | 0,79 | 0,19 | 14,4 % | 15.542 | 634 |
| Reglas ajustadas | 0,81 | 0,14 | 20,3 % | 22.008 | 540 |
| Modelo (umbral de menor costo) | 0,93 | 0,32 | 8,3 % | 9.019 | 337 |
| Modelo igualando las falsas alarmas de las reglas ajustadas | 0,97 | 0,18 | 18,5 % | 19.991 | 384 |
| Modelo + red de seguridad | 0,93 | 0,30 | 9,1 % | 9.829 | 349 |

Con la misma tasa de falsos positivos que las reglas, el modelo detecta unos **16 puntos más de fraude** (IC 95 %: de +14,5 a +16,7 frente a las ajustadas, y de +15,1 a +17,4 frente a las ingenuas) y la tasa de falsos positivos no sube. En ambas comparaciones el resultado apoya H1.

**Cómo leerlo con cuidado:**
- **Son datos sintéticos.** El generador creó el fraude con señales parecidas a las de las reglas, y el modelo aun así les gana. Pero esto sirve para ensayar el método, no para afirmar H1 sobre fraude real. Falta repetirlo con datos reales (etapa 3).
- **No hay 3D Secure, AVS ni CVV** en los datos. Las reglas son de monto, velocidad, ubicación y dispositivo, no la validación completa de una pasarela.
- **La red de seguridad cuesta algo aquí:** sumó unas 810 falsas alarmas y 12 millones de costo sin detectar más fraude. Es esperable: es política para ataques que los datos no muestran. Sus umbrales conviene revisarlos con datos reales.
- Las reglas ajustadas apagaron la regla de «muchas compras en 24 horas», porque hay comercios legítimos con muchas ventas.

## Hoja de ruta: de modelo de clase a producto

| Etapa | Qué se hace | Cómo se sabe que terminó |
|---|---|---|
| **1. Línea base** (hecha) | Regresión logística, partición por fecha, pruebas, bitácora. | Entrena, decide y se puede repetir el resultado. |
| **2. Mejorar el modelo** | Más variables (perfil habitual de IP y de hora), ajuste de parámetros (el motor de reglas ya está hecho), Gradient Boosting frente a logística, probabilidades calibradas, explicación para modelos de árboles, validación mes a mes dentro del código. | La bitácora muestra menos costo y menos falsas alarmas, sin perder detección, y el resultado se sostiene mes a mes. |
| **3. Datos reales** | Validar con datos reales: una pyme piloto o un conjunto público (PaySim, ULB). Consentimiento según la Ley 1581. Captura de contracargos confirmados. Reentrenar y comparar contra reglas fijas simples. | El modelo supera a las reglas fijas con datos reales (hipótesis H1 del anteproyecto). |
| **4. Servicio** | API que puntúa un pago en milisegundos, historial del cliente en tiempo real, huella del dispositivo y geolocalización frente a la IP, cola y herramienta de revisión manual, registro de cada decisión, vigilancia de que el modelo no se deteriore y reentrenamiento programado con los casos confirmados. | Responde rápido, no se cae y toda decisión se puede explicar y auditar. |
| **5. Producto vendible** | Varios clientes a la vez, validación de identidad (KYC, 2FA) con proveedores, seguridad (acceso, cifrado, alcance de PCI-DSS), controles alineados con ISO/IEC 27001, NIST y COSO, contrato y precios, soporte, y usar los contracargos reales para seguir aprendiendo. | Una o más pymes pagan por usarlo en un piloto. |

Del 3 en adelante el nivel de riesgo pasa a 🔴: hay dinero, datos personales y permisos de por medio.

## Brechas frente a un modelo antifraude completo

Un modelo antifraude de transacciones debería cubrir cinco capas: monitoreo en tiempo real, aprendizaje automático, datos de contexto, ciclo de gestión y respuesta, y cumplimiento normativo. Esta tabla compara esa lista con lo que existe hoy (versión 0.1.0) y dice en qué etapa se cierra cada brecha.

Estados: **Sí** (hecho y probado), **Parcial** (existe una parte), **No** (no existe).

| Capa | Componente | Estado | Qué hay hoy | Se cierra en |
|---|---|---|---|---|
| 1. Tiempo real | Análisis de cada pago antes de aprobarlo | Parcial | API REST con OpenAPI/Swagger, probada **en local** con muchas peticiones a la vez: unos 65 pagos por segundo por proceso, mediana de 20 ms, sin errores. HTTPS local verificado. No está desplegada ni usa una base de datos de producción. | Etapa 4 (despliegue) |
| 1. Tiempo real | Motor de reglas (monto, frecuencia, horario, cambio de ubicación) | Sí | `reglas/`: reglas fijas de línea base (ingenuas y ajustadas) y una red de seguridad que manda sobre el modelo en `analizar_transaccion`. Las reglas de la red son política y no están ajustadas con datos. | Hecho (etapa 2, parte 1) |
| 2. Aprendizaje automático | Modelo predictivo | Sí | Regresión logística, evaluada por fecha. Se comparan además árbol, Random Forest y Gradient Boosting. | Hecho (etapa 1) |
| 2. Aprendizaje automático | Modelo adaptativo (aprende de lo nuevo) | No | Se puede reentrenar con las etiquetas ya confirmadas y hay una simulación mensual, pero en los datos sintéticos no mejoró y no hay reentrenamiento automático. | Etapa 4 |
| 2. Aprendizaje automático | Comparar con el perfil habitual del cliente | Parcial | El modelo usa el historial del cliente, qué tan habitual es su IP y su franja horaria, y variables de comportamiento (tiempo en el formulario, teclado, mouse), todo con datos sintéticos. No hay captura real de la biometría de comportamiento. | Captura real: etapa 5 |
| 3. Contexto | Huella digital del dispositivo | Parcial | Hay un SDK de navegador (demostración) que calcula la huella y las señales del equipo, y el modelo las usa (con datos sintéticos). Falta el SDK móvil y la integración real en una tienda. | Etapa 4 |
| 3. Contexto | Geolocalización frente a la IP | Parcial | El comercio manda solo la IP y la API la ubica, calcula la distancia a la ciudad declarada y completa los datos de red; la IP no se guarda. El proveedor es una interfaz con uno **simulado**: falta contratar y conectar uno real. | Etapa 4 |
| 3. Contexto | Identidad (KYC, biometría facial, 2FA) | Parcial | El modelo usa el nivel KYC, la antigüedad de la cuenta y el resultado del 2FA que informa el comercio. No hay integración con un proveedor de KYC, biometría facial ni 2FA. | Etapa 5 |
| 4. Gestión y respuesta | Puntaje de riesgo y decisión (aprobar, revisar, bloquear) | Sí | Probabilidad, umbrales elegidos por costo en validación y motivos de cada decisión. | Hecho (etapa 1) |
| 4. Gestión y respuesta | Equipo y herramienta de revisión manual | Parcial | Hay una pantalla de analistas (`/panel`), probada en 6 perfiles de navegador, con cola, detalle, confirmación en dos pasos y control de conflictos. Faltan el equipo, el protocolo, los tiempos máximos y la identidad individual de cada analista. | Etapa 4 |
| 4. Gestión y respuesta | Mejora continua con casos confirmados | Parcial | La API recibe el resultado de cada pago (fraude confirmado, contracargo, legítimo) y el entrenamiento respeta cuándo se conoce cada etiqueta. Falta el reentrenamiento automático. | Etapa 4 |
| 5. Cumplimiento | ISO/IEC 27001, marcos NIST, control interno COSO | No | Hay controles técnicos sueltos (claves con hash, auditoría que no se edita, límite de peticiones, cabeceras de seguridad), pero ninguno de estos marcos está implementado ni se menciona Ley 1581 en el código. | Ley 1581: etapa 3. Resto: etapa 5 |

**Resumen:** 3 componentes hechos, 7 parciales y 2 sin hacer (de 12).

## Lo que NO existe todavía

- **Datos reales.** Todo se entrenó y se midió con datos sintéticos cuyas reglas de fraude definimos nosotros: los números muestran qué tan bien aprende un modelo esas reglas, no cuánto fraude real detectaría. H1, el valor de las variables nuevas y la conclusión sobre el historial compartido **hay que repetirlos con datos reales** (etapa 3).
- **Despliegue real y producto**: ver la lista de arriba y las etapas 4 y 5 de la hoja de ruta.
- **Reentrenamiento automático** y un modelo realmente adaptativo: en los datos sintéticos reentrenar cada mes no mejoró nada.
- **ISO/IEC 27001, NIST y COSO**: ningún marco está implementado; hay controles técnicos sueltos.
- La carpeta no está bajo control de versiones (git). Conviene hacerlo antes de seguir.
