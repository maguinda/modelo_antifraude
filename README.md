# modelo_ia – Modelo antifraude para pagos de pymes

Modelo de aprendizaje automático que, dado un pago, calcula la probabilidad de que sea fraude y
decide **aprobar, revisar o bloquear**. Es la base técnica del producto antifraude para pymes de
comercio electrónico.

## Proyecto final de Inteligencia Artificial (leer esto primero)

**Equipo:** Marlon Aguinda y Harrinson Adelmo Mahecha Arias. Fundación Universitaria Compensar,
Ingeniería de Software. Docente: William Andrés Aguilar Rodríguez.

**Qué es:** un modelo que mira un pago de comercio electrónico y decide si se **aprueba**, se
**rechaza** o queda **en revisión** por una persona. Está dentro de una API (FastAPI) y la consumen
tres pantallas web: una tienda de prueba (el comprador), un dashboard y un panel de analistas.

**Problema:** las pymes pierden dinero por fraude en pagos y también por frenar compras legítimas
(falsos positivos). El valor del modelo se mide en pesos: lo que cuesta el fraude que se escapa más
lo que cuestan las falsas alarmas.

**Datos (importante):** son **sintéticos**, creados por nosotros con generadores de semilla fija
(`datos/generadores/`). No hay datos de personas reales. Todo lo medido aquí vale para probar el
método, **no** para afirmar cuánto fraude detectaría en un comercio real. Eso falta (piloto con datos
reales). División por **fecha**, no al azar: entrenamiento hasta junio, validación julio y agosto,
prueba de septiembre en adelante. El umbral de decisión se elige en validación y se aplica en prueba.

**Modelos:** se compararon cuatro (regresión logística, árbol de decisión, Random Forest y Gradient
Boosting) con el dataset de 500 mil pagos: sin modelo, el fraude de la prueba cuesta 5.484 millones de
COP y Gradient Boosting lo baja a 165 millones (tabla de «Resultados actuales»). El modelo **en uso**
(`gradient_boosting_completo_v0.3.0`) es la versión completa, entrenada con el dataset de **1 millón**
de pagos: calidad del ranking 0,922, recall 0,930, precisión 0,451 y costo de 244 millones de COP frente
a 4.927 millones sin modelo, sobre datos sintéticos de prueba. Por eso las dos tablas muestran cifras
distintas para Gradient Boosting: son datos y variables distintos, no un error. La regresión logística
queda guardada como alternativa porque explica cada decisión. Cifras completas en [Resultados actuales](#resultados-actuales-prueba-septiembre-a-12-de-noviembre-datos-sintéticos)
y [Modelos entrenados con toda la información](#modelos-entrenados-con-toda-la-información-dataset-de-1-millón).

**Cómo ejecutarlo (5 pasos, Python 3.9 o 3.12, las versiones probadas):**

```bash
# 1. Clonar con este nombre de carpeta (el código se importa como «modelo_ia»)
mkdir proyecto && cd proyecto
git clone https://github.com/maguinda/modelo_antifraude.git modelo_ia

# 2. Entorno e instalación
python3 -m venv .venv && source .venv/bin/activate
pip install -r modelo_ia/requirements.txt

# 3. Levantar la demo (los modelos entrenados ya vienen en el repositorio; no hace falta entrenar)
python -m modelo_ia.api.demo

# 4. Abrir en el navegador (la demo solo se ve desde tu computador)
#    http://127.0.0.1:8000/tienda      tienda de prueba: el comprador paga y ve la decisión
#    http://127.0.0.1:8000/docs        Swagger: probar la API con 12 pagos de ejemplo
#    http://127.0.0.1:8000/dashboard   resumen, transacciones y vínculos
#    http://127.0.0.1:8000/panel       analista: aprobar o rechazar pagos en revisión

# 5. Pruebas del código (opcional)
python -m pytest modelo_ia/tests
```

Las claves de la demo se guardan en `modelo_ia/api/demo/claves_demo.txt` (se crean al primer arranque;
solo sirven en esa base de demostración). En `/docs` el comercio ya viene autorizado.

**Arquitectura en una línea:** pantalla web → API REST (`POST /v1/evaluaciones`) → variables del
pago e historial del cliente → modelo → decisión y puntaje → si queda en revisión, la ve un analista.
Detalle en [Cómo está organizada la API](#cómo-está-organizada-la-api-api).

**Qué NO está hecho (lo decimos de frente):** no hay sitio desplegado en la nube (solo local);
los proveedores de IP, identidad y segundo factor son simulados; falta validar con datos reales de
fraude y probar en celular real. Lista completa en [Lo que NO existe todavía](#lo-que-no-existe-todavía).

**Estado del resto del documento:** de aquí en adelante es la documentación técnica completa del
proyecto, escrita por etapas. Si un texto de una etapa vieja dice «versión 0.1.0» o «modelo actual:
regresión logística», manda lo de arriba.

**Contenido**

- [Cómo usarlo](#cómo-usarlo)
- [Qué hace cada archivo](#qué-hace-cada-archivo)
- [Calidad y pruebas automáticas (CI)](#calidad-y-pruebas-automáticas-ci)
- [Migraciones de la base de datos](#migraciones-de-la-base-de-datos)
- [Cómo está organizada la API (api/)](#cómo-está-organizada-la-api-api)
- [Datos incluidos (datos/)](#datos-incluidos-datos)
- [Cómo crece el modelo](#cómo-crece-el-modelo)
- [Resultados actuales (prueba: septiembre a 12 de noviembre, datos sintéticos)](#resultados-actuales-prueba-septiembre-a-12-de-noviembre-datos-sintéticos)
- [Modelos entrenados con toda la información (dataset de 1 millón)](#modelos-entrenados-con-toda-la-información-dataset-de-1-millón)
- [Historial por comercio frente a red compartida](#historial-por-comercio-frente-a-red-compartida)
- [API REST (OpenAPI / Swagger)](#api-rest-openapi--swagger)
- [Proveedor de IP propio (bases públicas, sin enviar la IP a nadie)](#proveedor-de-ip-propio-bases-públicas-sin-enviar-la-ip-a-nadie)
- [Comercios: alta, suspensión y acuerdo de datos](#comercios-alta-suspensión-y-acuerdo-de-datos)
- [Panel de control: ver todo, filtrar y rastrear (API de solo lectura)](#panel-de-control-ver-todo-filtrar-y-rastrear-api-de-solo-lectura)
- [Registro de modelos y deriva](#registro-de-modelos-y-deriva)
- [Seguridad de la cuenta de administrador: segundo factor y claves inválidas](#seguridad-de-la-cuenta-de-administrador-segundo-factor-y-claves-inválidas)
- [Observabilidad: identificador de petición, registros y métricas](#observabilidad-identificador-de-petición-registros-y-métricas)
- [Despliegue en un servidor real (preparado, todavía no desplegado)](#despliegue-en-un-servidor-real-preparado-todavía-no-desplegado)
- [Adaptador de historial: el diagnóstico para un comercio (python -m modelo_ia.historial)](#adaptador-de-historial-el-diagnóstico-para-un-comercio-python--m-modelo_iahistorial)
- [Qué señales aportan de verdad (y una idea que se descartó)](#qué-señales-aportan-de-verdad-y-una-idea-que-se-descartó)
- [Prueba de la metodología con datos públicos y reales](#prueba-de-la-metodología-con-datos-públicos-y-reales)
- [Hipótesis H1: modelo frente a reglas fijas (con datos sintéticos)](#hipótesis-h1-modelo-frente-a-reglas-fijas-con-datos-sintéticos)
- [Hoja de ruta: de modelo de clase a producto](#hoja-de-ruta-de-modelo-de-clase-a-producto)
- [Brechas frente a un modelo antifraude completo](#brechas-frente-a-un-modelo-antifraude-completo)
- [Lo que NO existe todavía](#lo-que-no-existe-todavía)

## Cómo usarlo

**Clona con este nombre de carpeta.** El código se importa como `modelo_ia` (`python -m
modelo_ia...`), así que la carpeta debe llamarse exactamente así, aunque el repositorio en GitHub se
llame `modelo_antifraude`:

```bash
mkdir proyecto && cd proyecto
git clone https://github.com/maguinda/modelo_antifraude.git modelo_ia
```

Los datasets grandes no se suben a GitHub: se regeneran con los scripts de
`modelo_ia/datos/generadores/` (semilla fija).

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

Por defecto lee `datos/dataset_antifraude_500000_transacciones.csv`. Con otro archivo: `--datos
ruta.csv` (también lee `.csv.gz`). Para el dataset de 1 millón: `--datos
modelo_ia/datos/dataset_antifraude_1000000_transacciones.csv.gz`.

## Qué hace cada archivo

- `config.py` — Todos los ajustes en un solo lugar: meses de cada conjunto, variables, costos,
  umbrales.
- `datos.py` — Carga el CSV y revisa que sirva (columnas, ceros y unos, montos válidos).
- `particion.py` — Divide por fecha de corte (en `config.py`): entrenamiento hasta el 30 de junio de
  2026, validación julio y agosto, prueba de septiembre en adelante.
- `variables.py` — Crea variables nuevas: hora e historial del cliente (solo con compras
  anteriores).
- `modelos.py` — Fábrica de modelos. Para probar un algoritmo nuevo se agrega una línea al
  `REGISTRO`.
- `evaluacion.py` — Recall, precisión, calidad del ranking, costo para la pyme y elección del
  umbral.
- `entrenar.py` — Entrena, elige el umbral en validación, evalúa en prueba y guarda el modelo.
- `comparar.py` — Prueba varios modelos con los mismos datos.
- `predecir.py` — Carga un modelo guardado y decide sobre un pago.
- `explicacion.py` — Dice por qué el modelo sospecha de un pago (por ahora, solo modelos lineales).
- `explicacion_local.py` — Las «señales que más pesaron» en un pago que no se aprobó, para cualquier
  modelo (también Gradient Boosting): parte de un pago típico, pone las señales reales una a una
  (valores de Shapley estimados) y dice cuántos puntos de probabilidad aportó cada una, en frases
  verificables. Solo la ve el personal; se calcula al decidir y se guarda. Los modelos viejos
  reciben su referencia con `python -m modelo_ia.explicacion_local agregar-referencia`. Explican al
  modelo, no prueban fraude.
- `experimentos.py` — Bitácora: cada entrenamiento deja una fila en `resultados/experimentos.csv`.
- `reglas/motor.py` — Motor de reglas: una regla es «si pasa esto, entonces revisar o bloquear»;
  gana la más severa y dice qué regla se activó.
- `reglas/reglas_base.py` — Reglas fijas de la línea base para H1: versión ingenua (umbrales
  redondos) y versión ajustada con la validación.
- `reglas/seguridad.py` — Red de seguridad para producción: reglas duras de política (lista negra,
  ráfaga extrema, monto muy alto...).
- `comparar_h1.py` — Compara el modelo con las reglas fijas, con intervalos de confianza del 95 %
  por remuestreo (hipótesis H1).
- `etiquetas.py` — Etiquetas tardías: al entrenar con corte en una fecha, solo se usan los fraudes
  ya confirmados ese día.
- `comparar_variables.py` — Mide cuánto aporta cada grupo de variables (sumándolo y quitándolo).
- `reentrenamiento.py` — Simula si conviene reentrenar el modelo cada mes.
- `api/` — API REST con OpenAPI/Swagger: decide pagos, cola de revisión, resultados y lista negra.
  Incluye el panel de analistas (`/panel`), el SDK y la tienda de prueba, y la documentación servida
  en local.
- `comparar_alcance.py` — Mide si el historial por comercio o con toda la plataforma junta da mejor
  resultado.
- `pruebas_navegador/` — Recorridos automáticos en Chromium y WebKit (documentación, panel de
  analistas, tienda de prueba).
- `datos/` — Los datasets sintéticos (500, 100 mil, 500 mil y 1 millón de transacciones) y, en
  `datos/generadores/`, los scripts que los crean.
- `tests/` — 219 pruebas automáticas.
- `modelos_guardados/` — Los modelos entrenados (`.joblib`) con su ficha (`.json`).

## Calidad y pruebas automáticas (CI)
Cada `push` corre `.github/workflows/pruebas.yml` en GitHub (gratis, el repositorio es público):

| Trabajo | Qué hace | ¿Bloquea? |
|---|---|---|
| Pruebas (Python 3.9 y 3.12) | Las pruebas con las **versiones exactas de producción** (`requirements-dev.txt` incluye `despliegue/requirements-produccion.txt`). 3.9 es la de tu equipo hoy y 3.12 la de producción; cuando migres, se quita la 3.9. | Sí |
| Calidad · `ruff` | Solo errores reales: sintaxis, imports sobrantes, nombres sin definir, variables sin usar, argumentos mutables por defecto, escapes inválidos. No revisa estilo (el código usa líneas largas a propósito). | Sí |
| Calidad · formato | `ruff format --check`: el código Python debe estar con el formato automático (ancho 120). Si falla, se corrige con `ruff format modelo_ia`. | Sí |
| Calidad · `mypy` | Tipos en `api/`. Hoy hay **0 errores** y por eso **bloquea**: un cambio que meta uno nuevo no se une. | Sí |
| Calidad · archivos pesados | Falla si se versiona un archivo de más de 5 MB (datos pesados o personales subidos por error). | Sí |

El flujo no usa secretos, tiene permisos de solo lectura y sus acciones están fijadas por huella
(SHA), no por etiqueta. Se corre igual en tu equipo, desde la carpeta que contiene `modelo_ia/`:
```
pip install -r modelo_ia/requirements-dev.txt          # versiones exactas (a diferencia de requirements.txt, que es amplio)
ruff check modelo_ia                                   # errores reales
ruff format modelo_ia                                  # da formato al código (en la CI: ruff format --check)
(cd modelo_ia && mypy)                                 # tipos (bloquea en la CI)
python -m pytest modelo_ia/tests -q                    # las pruebas
```
Las pruebas que necesitan las bases de IP de 133 MB (`datos_ip/`) se saltan solas en la CI.

**Formato de las pantallas (HTML, CSS y JS propios en `frontend/`, ver `frontend/README.md`).** No hay un paso en la CI: se formatean a mano con
`npx prettier@3.3.3 --print-width 120 --write frontend/<carpeta>/<archivo>` (no incluye las librerías de terceros: Swagger y ReDoc).
Los documentos `.md` van con líneas de unos 100 caracteres y las tablas con celdas muy largas se escriben como listas.

## Migraciones de la base de datos
Los cambios de esquema son **migraciones numeradas** (`api/migraciones.py`; la lista está en
`Almacen._migraciones`). Cada una se aplica una sola vez, en su propia transacción, y queda
registrada en la tabla `migraciones`.
- **Base con datos:** antes de aplicar el primer cambio pendiente se guarda una copia
  (`<base>.antes-de-migrar-vN-FECHA.sqlite3`, un solo archivo, permisos 600). Si la copia falla, no
  se migra.
- **Dos procesos arrancando a la vez** (`--workers`): el estado se relee dentro del bloqueo de
  escritura, así ninguna migración corre dos veces (30 rondas con procesos reales lo comprueban).
  Además, un **candado de archivo** (`<base>.migrar.lock`) cubre toda la actualización: el segundo
  proceso espera a que el primero termine (hasta 600 s; si sigue ocupado, falla con un mensaje claro)
  y no repite ni la copia previa. Sin él, uno que llegaba
  entre dos migraciones del primero hacía una copia de más (lo encontró la CI; una prueba
  determinista lo reproduce). En Windows no hay candado de archivo y se mantiene solo el bloqueo de
  SQLite.
- **Base más nueva que el programa:** se rechaza con un mensaje claro y no se toca. No hay migración
  «hacia atrás»: se vuelve atrás restaurando una copia.
- **Bases de versiones anteriores** (sin tabla `migraciones`) se adoptan con la migración 1, que
  conserva todos los datos.
- `python -m modelo_ia.api.admin migraciones` muestra qué está aplicado (no modifica nada); `...
  admin migrar` aplica lo que falte.
- **Para cambiar el esquema no edites la migración 1** (una prueba lo impide): agrega la 2 con el
  cambio, y para una base nueva se aplican las dos en orden.

## Cómo está organizada la API (`api/`)
`crear_app` (en `app.py`, unas 60 líneas) solo arma la aplicación y registra las piezas; cada tema vive en su archivo:

- `rutas/evaluaciones.py` · `revisiones.py` · `panel_datos.py` · `lista_negra.py` · `comercios.py` ·
  `estado.py` · `metricas.py` — Las 23 operaciones, una por tema, cada una con su `APIRouter`.
- `dependencias.py` — Quién llama (clave y rol: `principal_actual`, `exigir`), los filtros del panel
  y el acceso a los servicios (`obtener_servicio`…). Los servicios viven en `app.state`; las rutas
  los piden como dependencias, así se pueden probar solas.
- `manejo.py` — Errores en `application/problem+json`, límite de tamaño y cabeceras de seguridad de
  todas las respuestas.
- `registro_modelos.py` · `deriva.py` (en la carpeta de arriba) · `rutas/modelos.py` — La huella de
  cada modelo, la carga verificada y la medición de deriva (ver «Registro de modelos y deriva»).
- `totp.py` · `ip_cliente.py` · `seguridad.py` — El segundo factor (TOTP, RFC 6238, sin dependencias),
  la IP real detrás de un proxy y los límites de peticiones (ver «Seguridad de la cuenta de
  administrador»).
- `observabilidad.py` · `metricas.py` · `contexto.py` — El identificador de cada petición, los
  registros (logs) en JSON y las métricas (ver «Observabilidad»). Es un middleware por fuera de los
  demás.
- `contrato.py` · `metadatos.py` — El contrato OpenAPI y los textos y respuestas documentadas de
  Swagger.
- `servicio.py` — La lógica de cada evaluación (idempotencia, reintento optimista, decisión,
  revisiones, resultados, lista negra). **No tiene SQL**: habla con la persistencia por las
  interfaces de `repositorios/interfaces.py`, así que se puede probar sin base de datos
  (`tests/test_servicio_sin_base.py`, con carreras simuladas intento por intento).
- `consultas.py` — El panel y el dashboard: valida los filtros (`Filtros`: roles, fechas, códigos;
  un comercio solo ve lo suyo) y da forma a cada respuesta. **No tiene SQL**: pide las filas a
  `repositorios/consultas.py` por la interfaz `ConsultasRepo` y se prueba sin base de datos
  (`tests/test_consultas_sin_base.py`).
- `almacen.py` — **Fachada** de la persistencia: los mismos métodos de siempre, cada uno delegando a
  su repositorio. Una prueba vigila que sus firmas no cambien.
- `repositorios/` — La persistencia dividida por tema: `base.py` (conexión, transacciones y el
  **único candado** de escritura que todos comparten), `seudonimos.py` (todo lo que usa el secreto),
  `comercios.py` (comercios, suspensión, acuerdos), `claves.py` (claves, personas, autenticación),
  `auditoria.py`, `esquema.py` (esquema y migraciones), y para el servicio de evaluaciones
  `evaluaciones.py`, `revisiones.py`, `resultados.py` y `lista_negra.py`, más `consultas.py` (todas
  las lecturas del panel) y `filtros_sql.py` (traduce los filtros validados a condiciones con
  parámetros), cada uno detrás de una interfaz (`interfaces.py`, con `typing.Protocol`). Los
  repositorios de evaluaciones reciben la conexión de quien llama: las transacciones y el orden de
  los bloqueos son del servicio. Los repositorios no dependen de la fachada y la fachada no tiene
  SQL.

### PostgreSQL (opcional; SQLite sigue siendo lo normal)

El mismo programa funciona sobre **PostgreSQL 16** con `MODELO_IA_BD_URL=postgresql://usuario:clave@host:5432/base` (o la dirección en un archivo, con
`MODELO_IA_BD_URL_ARCHIVO`, porque lleva la contraseña). Sin esa variable todo sigue como antes, con SQLite. No se pueden poner las dos (`MODELO_IA_BD` y
`MODELO_IA_BD_URL`): el programa se niega.

- **Cómo está hecho:** los repositorios no cambiaron. `repositorios/motor_pg.py` les da la misma interfaz que SQLite (marcadores `?`, filas por nombre o posición,
  `transaccion()` y `lectura()`); `repositorios/esquema_pg.py` trae el mismo esquema (tablas, índice parcial, triggers de «no se edita / no se borra» como funciones
  de PostgreSQL) y las mismas seis migraciones; `errores_bd.py` agrupa los errores de los dos motores.
- **Una escritura a la vez:** cada escritura toma un candado de aviso de PostgreSQL (`pg_advisory_xact_lock`) al empezar, así que las garantías de siempre
  (un pago repetido se evalúa una sola vez, una revisión se resuelve una sola vez, un modelo en uso por rol) valen igual **entre procesos y entre servidores**.
  Es lo más conservador: no se rediseñaron las carreras. Más adelante se podría pasar a candados por fila si el rendimiento lo pide (hoy no hay medida que lo pida).
- **Crear la base** con colación `C` (el orden de los textos igual al de SQLite): `CREATE DATABASE antifraude LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;`. Si no, el
  programa avisa en el registro. Las migraciones se aplican al arrancar, como en SQLite.
- **Copias de seguridad:** `python -m modelo_ia.despliegue.respaldo_pg crear | verificar | restaurar | rotar | ciclo` (ver `despliegue/LEEME.md`): el mismo formato cifrado (.afbk), la misma
  rotación y la misma copia afuera que el respaldo de SQLite, pero la copia la saca `pg_dump` (formato custom, consistente aunque el servicio escriba) y se restaura con
  `pg_restore` en **una transacción**. La contraseña viaja por el entorno del proceso, no por la línea de comandos. Hace falta `pg_dump`/`pg_restore` de la **misma
  versión mayor que el servidor o más nueva** (la imagen del servicio no los trae). Una copia de SQLite y una de PostgreSQL no se confunden: cada herramienta rechaza la del otro.
  Probado dentro de la imagen del servicio con `pg_dump` 16.16 contra PostgreSQL 16 (copia, verificación, restaurar tras perderlo todo, archivos alterados); no probado con una
  base grande. **Hazla también antes de actualizar** una base con datos: el programa no guarda copia previa en PostgreSQL. El respaldo de SQLite (`respaldo.py`) y la vigilancia de archivos solo sirven con SQLite; con PostgreSQL la vigilancia revisa la API, el certificado y la edad de la última copia.
- **Pasar los datos de SQLite a PostgreSQL** (`admin migrar-a-postgres`): copia TODO a una base NUEVA y vacía, y verifica antes de confirmar.
  Pasos: (1) pon la base de SQLite al día con `MODELO_IA_BD=/ruta/base.sqlite3 python -m modelo_ia.api.admin migrar`; (2) crea la base de PostgreSQL vacía (colación
  `C`); (3) con **el mismo `MODELO_IA_PEPPER`** que usaba SQLite (si cambia, las claves guardadas dejan de reconocerse) y `MODELO_IA_BD_URL` apuntando a la base nueva, corre
  `python -m modelo_ia.api.admin migrar-a-postgres --sqlite /ruta/base.sqlite3`. **Detén el servicio que usa el SQLite antes de copiar** (lo que se escriba después no estará en PostgreSQL; si el archivo cambia DURANTE la copia, se detecta, se deshace todo y se pide repetir). El archivo de SQLite se abre **solo para leer** y no cambia; se copia en una sola
  transacción en el orden de las claves foráneas, conservando los números (id) y dejando las secuencias después del último; y antes de confirmar se compara, tabla por tabla,
  el número de filas y una huella SHA-256 del contenido. Si algo no coincide, se deshace todo. **Se niega** si el destino ya tiene datos, si el SQLite no está al día o si las
  columnas no coinciden. Probado con la copia de la demo (1 956 pagos, 123 revisiones, 1 966 registros de auditoría): las diez tablas coinciden y los endpoints del panel dan
  lo mismo en los dos motores. **No hace** la vuelta (de PostgreSQL a SQLite) ni mezcla con datos que ya haya.
- **Dos roles (lo recomendado en producción):** el que **migra** (dueño de las tablas, con CREATE) y el de la **aplicación**, que solo lee y escribe filas. La
  aplicación arranca con el rol limitado siempre que la base ya esté al día (no intenta crear nada); quien migra corre `python -m modelo_ia.api.admin migrar`.
  Permisos del rol de la aplicación (sustituye `app` y `public`): `GRANT USAGE ON SCHEMA public TO app; GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA
  public TO app; GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app;` y **nada más** (sin TRUNCATE ni CREATE). La auditoría y los acuerdos no se editan,
  borran ni vacían (`TRUNCATE` también está bloqueado por un trigger), pero **el dueño de las tablas siempre puede quitar un trigger**: por eso la aplicación no
  debe ser la dueña.
- **Cifrado de la conexión:** fuera de tu propia máquina usa `?sslmode=verify-full&sslrootcert=/ruta/ca.pem` (`require` cifra pero no verifica el servidor). Si el servidor no es local y la
  dirección no lleva `sslmode` (o es `disable`, `allow` o `prefer`, que no exigen cifrado), el programa lo avisa al arrancar en producción.
- **Conexiones:** cada operación abre su conexión (sin agrupador). Con 2 procesos puede haber del orden de 80 conexiones a la vez frente a las 100 que PostgreSQL
  permite por omisión: si el servicio crece, pon **PgBouncer en modo sesión** delante (en modo transacción se pierden `search_path`, `lock_timeout` y el candado de la
  migración) o limita los procesos. Sin agrupador, agotar las conexiones se ve como un 503 «la base no responde», sin pérdida de datos.
- **Al agregar una migración nueva** hay que escribirla para los dos motores: una prueba falla si las dos listas no llevan los mismos números y nombres.
  `traducir()` solo entiende comillas simples (sin `?` dentro de identificadores con comillas dobles ni apóstrofes dentro de comentarios `--`).
- **Probado:** la suite completa corre contra PostgreSQL real en la CI (las pruebas que abren el archivo de SQLite o hacen copias cifradas se saltan solas);
  las 872 respuestas del panel son idénticas en los dos motores; servidor real con 2 procesos, tráfico concurrente con envíos repetidos, 8 analistas resolviendo
  la misma revisión y un corte y vuelta de PostgreSQL (la API responde 503 y se recupera sola).
- **No probado / no existe:** alta disponibilidad, réplicas, agrupador de conexiones (cada operación abre su conexión), migración de datos reales desde SQLite,
  PostgreSQL administrado en la nube, versiones distintas de la 16, ni cifrado en tránsito (usa `?sslmode=require` en la dirección si el servidor está fuera de tu red).

### Lo propio de SQLite (lista de trabajo para pasar a PostgreSQL)

Todo el SQL está en `repositorios/` (y las migraciones en `migraciones.py`); una prueba
(`tests/test_sqlite_confinado.py`) falla si aparece SQL propio de SQLite en otro lugar. El cambio 7a
ya reescribió a SQL portable lo que se podía (`seq` en vez de `rowid`, la tabla `evaluacion_reglas`
en vez de `json_each`, `minutos_resolucion` en vez de `julianday`, `ON CONFLICT` en vez de
`INSERT OR REPLACE`, `RETURNING id` en vez de `lastrowid`, `CASE WHEN` en vez de sumar condiciones,
`NULLS LAST` explícito y cubetas de puntaje con `CASE` en vez de `CAST`). Lo que queda, con su archivo:

| Constructo de SQLite | Dónde | Para qué sirve |
|---|---|---|
| `rowid` · `julianday` · `json_each` | `repositorios/esquema.py` (solo la migración 4) | Rellenar las columnas nuevas a partir de los datos que ya había; no se usan al atender peticiones. |
| `substr(fecha,1,10)` | `repositorios/consultas.py` | Agrupar por día (las fechas son texto ISO); PostgreSQL también tiene `substr`. |
| `AUTOINCREMENT` | `repositorios/esquema.py` (y `esquema_pg.py` lo traduce a una columna de identidad) | Identificadores numéricos nuevos (en PostgreSQL, `GENERATED ... AS IDENTITY`). |
| `PRAGMA` · `BEGIN IMMEDIATE` · WAL | `repositorios/base.py`, `esquema.py`, `migraciones.py`, `migrar_a_postgres.py` (este solo lee el SQLite de origen) | Conexión, un solo escritor y copias de seguridad. |
| `RETURNING` | `comercios.py`, `claves.py`, `modelos_registro.py` | Pide SQLite 3.35 o más reciente (PostgreSQL lo trae de siempre). |

Para agregar una ruta: ponla en el router de su tema, agrégala al recorrido de
`tests/test_permisos_por_ruta.py` (la prueba falla si una operación del contrato no está ahí) y
vuelve a exportar `openapi.json`. Esa prueba guarda una «foto» del código, el tipo y la forma de la
respuesta de **cada operación con cada rol**; si cambias la API a propósito, regenérala con
`REGENERAR_PERMISOS=1`.

## Datos incluidos (`datos/`)

Todos son **sintéticos**: inventados por los generadores de `datos/generadores/` (semilla fija, así
que se pueden volver a crear idénticos). No tienen datos de personas.

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

Fraudes nuevos: granja de dispositivos (una huella contra muchos clientes), navegador
anti-detección, bot que prueba tarjetas, cuenta nueva con 2FA fallido y fraude amistoso. Legítimos
nuevos que se parecen al fraude: viajero en el exterior, VPN de empresa, 2FA fallido por error,
cliente nuevo y cambio de credenciales.

**Cosas que hay que saber de estos datos:**
- **Las columnas de «después del pago» no pueden ser entrada del modelo** (revisión, contracargo,
  fecha de confirmación): están en `COLUMNAS_PROHIBIDAS` y hay una prueba que lo vigila.
  `id_comercio` y `huella_dispositivo` tampoco entran tal cual; sirven para construir variables, por
  ejemplo cuántos clientes distintos usan una misma huella.
- **Las etiquetas llegan tarde.** El 6,6 % de los fraudes nunca se confirma (el cliente no reclama)
  y los contracargos tardan hasta 110 días (mediana de 51). Al reentrenar hay que usar solo lo que
  ya se sabía en esa fecha.
- **El fraude cambia con el tiempo:** el anti-detección crece, la prueba de tarjetas cae y la granja
  de dispositivos tiene una ola a mediados del periodo.
- **Las columnas nuevas todavía no se usan en ningún modelo.** Hoy el modelo ve las mismas variables
  de siempre. Medir cuánto aporta cada grupo es el siguiente paso.
- **Un error conocido en los datasets de 100 mil y 500 mil:** su calendario marca el Día de la Madre
  el 31 de mayo de 2026. En Colombia es el segundo domingo de mayo (10 de mayo). El de 1 millón sí
  usa las fechas correctas. No lo corregí en los otros dos porque cambiaría los resultados ya
  reportados.
- **Todo es inventado con reglas nuestras:** lo que aprenda un modelo con estas columnas son esas
  reglas, no el fraude real.

## Cómo crece el modelo

- **Probar un algoritmo nuevo:** agregarlo al `REGISTRO` de `modelos.py` y correr `comparar.py`.
- **Probar una variable nueva:** escribir la función en `variables.py`, listarla en un conjunto
  nuevo de `config.py` (por ejemplo `"historial_v2"`) y entrenar con `--conjunto historial_v2`.
- **Ver si mejoró:** abrir `resultados/experimentos.csv`. Cada fila trae modelo, variables, recall,
  precisión, falsas alarmas y costo en la prueba.

### Reglas que no se rompen

1. **La prueba (septiembre en adelante) no se usa para elegir nada.** El umbral se elige en
   validación (jul–ago). Hay una prueba automática que lo vigila.
2. **El historial del cliente usa solo compras anteriores.** Nada del futuro entra en una fila.
   También hay prueba automática.
3. **Estas columnas nunca son entrada del modelo:** `es_fraude`, `patron`, `nivel_riesgo`,
   `id_cliente`, `numero_transaccion`. El código se niega a entrenar si alguna se cuela.
4. **Cada modelo guardado dice con qué datos se entrenó** (huella del archivo) y que eran sintéticos.

## Resultados actuales (prueba: septiembre a 12 de noviembre, datos sintéticos)

*Esta tabla es de la etapa comparativa (dataset de 500 mil pagos). El modelo en uso hoy es el de la sección de 1 millón, más abajo.*

Sin modelo, el fraude de la prueba cuesta unos **5.484 millones de COP**.

| Modelo | Calidad del ranking | Costo (millones COP) | Falsas alarmas |
|---|---|---|---|
| Regresión logística (modelo actual) | 0,85 | 337 | 9.019 |
| Árbol de decisión | 0,84 | 338 | 5.960 |
| Random Forest | 0,92 | 201 | 7.105 |
| Gradient Boosting | 0,94 | 165 | 4.475 |

El costo usa un supuesto de 15.000 COP por cada falsa alarma. Gradient Boosting es el más barato; la
regresión logística es el modelo actual porque explica cada decisión y entrena en menos de un
segundo.

## Modelos entrenados con toda la información (dataset de 1 millón)

Se agregaron grupos de variables construidas con las columnas nuevas: `perfil` (qué tan habitual es
la IP y la franja horaria para ese cliente), `huella` (cuántos clientes usan la misma huella,
emulador, equipo que no cuadra), `geo_ip`, `identidad` y `comportamiento`. Todo se mide en la prueba
(septiembre en adelante) con el umbral elegido en la validación.

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
- **La mejora real es moderada.** Con Gradient Boosting, todo junto sube el AP 0,016. La regresión
  logística mejora más (0,053) porque Gradient Boosting ya captaba parte de eso por su cuenta.
- **El perfil habitual no aporta nada** (+0,001) y se podría quitar.
- **Casi todos los fraudes ya se detectaban** (granja, bots, navegador anti-detección: recall
  cercano a 1,00 incluso antes). Esto pasa porque el generador dio a esos fraudes señales fuertes en
  las columnas viejas. Lo que sí mejora es la precisión (menos falsas alarmas) y el fraude
  disimulado (recall de 0,65 a 0,73 con Gradient Boosting).
- **El fraude amistoso casi no se detecta** (recall de 0,14 a 0,21) y está hecho así a propósito: se
  ve como una compra normal.
- **Las diferencias de costo de ±10 a 15 millones son ruido** de elegir el umbral; el AP es más confiable.
- **Reentrenar cada mes no mejoró nada** en estos datos (AP de 0,919 contra 0,919). El mecanismo
  existe y se puede simular, pero no demostró su beneficio, así que «modelo adaptativo» sigue sin
  cerrarse.
- **Las etiquetas tardías no empeoraron el resultado** (costo de 265,9 millones entrenando solo con
  lo ya confirmado, frente a 273,9 con todas las etiquetas): la diferencia está dentro del ruido.

Modelos guardados (`modelos_guardados/`). **El modelo en uso es
`gradient_boosting_completo_v0.3.0`**: entrenado con el historial **por comercio** (como lo calcula
la API) y con etiquetas tardías, sobre el dataset de versión 2 con 30 % de movilidad. En la prueba:
calidad (AP) 0,922, recall 0,930, precisión 0,451, 4.957 falsas alarmas y 244 millones de costo (sin
modelo: 4.928). Junto a él queda `logistica_completo_v0.3.0`, que es explicable (AP 0,876, costo 389
millones). Las versiones 0.1.0 y 0.2.0 se conservan como historial. Gradient Boosting todavía **no
da motivos** por variable; la logística sí. Las reglas activadas siempre se informan.

## Historial por comercio frente a red compartida

La API calcula el historial de un cliente (y de una huella) **dentro de cada comercio**: así cada
comercio solo ve lo suyo. Pero hasta la versión 0.2.0 el modelo se había entrenado con todos los
comercios juntos, y eso hacía que ciertas variables valieran menos en producción. Ahora cada modelo
guarda con qué alcance se entrenó (`--alcance comercio` o `plataforma`) y `GET /v1/salud` **avisa**
si el modelo y la API no coinciden. Para medir el efecto se creó una versión 2 del dataset
(`--afinidad`: cada cliente compra sobre todo en 1 a 3 comercios habituales) y variantes con
`--movilidad=` (parte de las compras legítimas que caen en un comercio nuevo para el cliente). Mismo
modelo, mismos datos; solo cambia el alcance (`python -m modelo_ia.comparar_alcance`):

| Compras legítimas en comercios nuevos | AP plataforma | AP por comercio | Costo plataforma / por comercio (millones) |
|---|---|---|---|
| 0 % | 0,917 | 0,940 | 265 / 208 |
| 30 % | 0,917 | 0,922 | 267 / 244 |
| 60 % | 0,917 | 0,914 | 271 / 266 |

**Cómo leerlo:** con 0 % la ventaja del historial por comercio es un **artefacto**: si los clientes
legítimos nunca compran en comercios nuevos, «cliente sin historial en este comercio» delata al
fraude, algo que en la vida real no pasa. A medida que el dato se parece más a la realidad, la
ventaja desaparece y ambos empatan (con 60 % la plataforma gana por 0,003). Conclusión: en estos
datos sintéticos, aislar por comercio **casi no cuesta nada**, y el beneficio de compartir historial
entre comercios **no se puede demostrar** sin datos reales. Compartir exigiría además identificar al
cliente igual en todos los comercios y resolver la Ley 1581: es una decisión de negocio que se toma
con datos reales.

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
| `POST /v1/comercios` | admin | Crear un comercio y su clave (razón social y NIT opcionales). |
| `GET /v1/comercios`, `GET /v1/comercios/{id}/acuerdos` | analista, admin | Comercios con su estado y acuerdo de datos; el NIT solo lo ve el admin. |
| `POST /v1/comercios/{id}/suspender`, `…/reactivar`, `PUT …/acuerdo` | admin | Suspender o reactivar un comercio y registrar su acuerdo de datos (ver «Comercios: alta, suspensión y acuerdo de datos»). |
| `GET /v1/modelo`, `GET /v1/salud` | cualquiera / sin clave | Modelo en uso y salud del servicio. |
| `GET /v1/metricas` | admin | Métricas en formato Prometheus (peticiones, duración, pagos por decisión). |

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

- Hay 12 pagos de ejemplo con su resultado esperado: aprobada, dos en revisión, rechazada, viaje
  imposible (dos pasos), modo degradado, lista negra, un error por enviar datos de tarjeta, dos
  donde **solo se manda la IP** y uno donde **no se manda el estado del dispositivo**. Una prueba
  comprueba que cada uno siga dando lo que dice con el modelo en uso (así la prueba avisó cuando
  cambié de modelo y un ejemplo dejó de cumplirse).
- **Para repetir un ejemplo, cambia `id_transaccion`** (si no, devuelve la respuesta guardada). Y si
  quieres el mismo resultado del primer intento, cambia también `id_cliente`: al segundo pago, ese
  cliente ya tiene historial y el modelo lo trata distinto.
- Las claves de la demo están en `modelo_ia/api/demo/claves_demo.txt` (solo las puede leer tu
  usuario). Sirven únicamente para esa base de demostración. La demo solo escucha en `127.0.0.1`: no
  se puede exponer a la red, y nunca debe usarse con datos reales.

**El panel de analistas** (`/panel`): se entra con la clave de analista (se guarda solo mientras la
pestaña esté abierta, nunca en `localStorage`), lista los pagos pendientes con riesgo, monto y
tiempo en cola, y cada uno abre con motivos, reglas activadas y datos del pago. Aprobar o rechazar
pide una **segunda confirmación** y admite una nota. Si otra persona resuelve primero, avisa y
actualiza. Todo texto que viene de los comercios se muestra como texto, nunca como HTML (hay una
prueba que lo impide y una prueba de ataque en navegador).

**El SDK y la tienda de prueba:** `frontend/sdk/antifraude.js` corre en el navegador del comprador
y arma la huella del dispositivo, el sistema, el navegador y el comportamiento al llenar el
formulario (tiempo, velocidad de tecleo, si se pegó la tarjeta, movimiento del mouse). **Nunca lee
lo que el comprador escribe** (una prueba lo vigila) y no envía nada por su cuenta. La tienda
muestra la arquitectura correcta: el navegador manda las señales al servidor de la tienda y **es ese
servidor quien llama a la API con su clave**; la clave jamás llega al navegador. La tienda manda
**solo la IP** del comprador y la API la ubica y la evalúa. El KYC y el 2FA están **simulados** y
marcados como tal.

**La IP del comprador y los datos de red.** Un comercio normal no sabe clasificar una IP (¿es VPN?,
¿de qué país?, ¿qué reputación tiene?), así que **solo manda `red.ip`**, la que ve su servidor, y la
API hace el resto con un *proveedor de IP*: tipo de conexión, país, ciudad, si es un centro de
datos, reputación y distancia a la ciudad declarada. Reglas:
- **La IP cruda no se guarda, no se devuelve y no se registra.** Se usa en memoria; en la base solo
  queda una huella irreversible (HMAC con el secreto del servicio) para reconocer repeticiones. Hay
  pruebas que buscan la IP en la base, en las respuestas, en la auditoría y en los registros.
- **Lo que mande el comercio manda.** Si envía también `tipo_ip`, `pais`, etc., eso se respeta y el
  proveedor solo completa lo que falta. Un comercio que ya tenga su propio proveedor puede seguir
  mandando todo y no mandar la IP.
- La respuesta dice de dónde salió cada dato (`red_origen`: `comercio`, `proveedor:<nombre>` o
  `proveedor_fallo`). Si el proveedor falla, la API no se cae: usa lo que mandó el comercio y, si no
  alcanza, queda en modo degradado.
- El proveedor es una **interfaz** (`api/proveedores_ip.py`): para usar uno real se escribe una
  clase con `consultar(ip)` y se elige con `MODELO_IA_PROVEEDOR_IP`. Hay dos: `simulado` (solo de
  demostración: reconoce rangos de IP reservados para documentación (`192.0.2.x`, `198.51.100.x`,
  `203.0.113.x`) y nada más; `GET /v1/salud` avisa mientras se use) y `propio` (bases públicas
  descargadas: ver «Proveedor de IP propio» más abajo).
- Una IP puede ser dato personal: cada comercio debe cubrir este tratamiento en su política (Ley
  1581). Es una decisión legal, no solo técnica.

**El estado del dispositivo es opcional** (`dispositivo.estado`: «Conocido», «Nuevo» o «No
reconocido»). Lo sabe el sistema de cuentas del comercio (¿este cliente ya usó este equipo?), no el
SDK. Si no llega, la API usa un **segundo modelo entrenado sin esa variable**
(`modelo_actual_sin_estado.joblib`, se entrena con `--sin-estado --como-actual-sin-estado`) y la
respuesta dice `estado_origen: no_enviado`. Si ese modelo no existe, el pago queda en modo degradado
y `/v1/salud` lo avisa.

La primera idea fue que la API *dedujera* el estado del historial y se lo diera al modelo normal. Se
midió antes de hacerlo (`python -m modelo_ia.comparar_estado`, dataset de versión 2 con 30 % de
movilidad, historial por comercio):

| Qué recibe el modelo como estado del dispositivo | Calidad (AP) | Costo (millones) | Falsas alarmas |
|---|---|---|---|
| Lo que manda el comercio | 0,922 | 244 | 4.957 |
| Deducido del historial (modelo reentrenado así) | 0,903 | 269 | 5.986 |
| Sin la variable (**lo que se implementó**) | 0,902 | 265 | 7.634 |
| Deducido del historial, **sin reentrenar** el modelo normal | 0,797 | 451 | 9.989 |

- **Dárselo deducido al modelo normal lo empeora mucho** (casi duplica el costo), porque la variable
  cambia de significado: en los datos originales «Nuevo» es el 9 % de las compras y deducido sale el
  55 %.
- **Deducirlo no aporta nada** frente a no tenerlo (0,903 contra 0,902): el historial ya dice lo
  mismo (`dispositivo_nuevo_cliente`, `n_compras_previas`). Por eso se eligió el modelo sin estado
  en vez de inventarle un valor al modelo normal.
- **Lo que se pierde sin el dato** (unos 0,02 de calidad y más falsas alarmas) es información que el
  sistema de cuentas del comercio sí tiene y la API no puede deducir, sobre todo el «no reconocido».
  En estos datos sintéticos parte de esa ventaja es un artefacto: ese campo lo asigné yo según el
  tipo de fraude. Con datos reales la diferencia podría ser otra.
- **Ojo con el efecto en un pago concreto:** un pago de primera compra con señales algo raras
  (tarjeta pegada, formulario rápido) dio 0,39 (EN_REVISION) con «Conocido» y 0,82 (RECHAZADA) sin
  estado. Sin el dato, el modelo castiga más a los clientes nuevos con señales dudosas. Por eso la
  tienda de prueba usa «Conocido» por defecto.
- Recomendación para los comercios: **mandarlo si pueden** (el pago se evalúa con el modelo más
  preciso) y no preocuparse si no.

**Qué garantiza:**
- **Cada comercio solo ve lo suyo.** Pedir algo de otro comercio responde `404`, no `403`, para no revelar que existe.
- **Nunca acepta datos de tarjeta.** Rechaza campos de tarjeta y números que pasan la verificación
  de tarjetas, y no repite lo enviado en el mensaje de error.
- **Un reintento no se cuenta dos veces y el historial no se corrompe.** El cálculo se hace sin
  bloquear la base y se guarda con un bloqueo corto; si entra otro pago del mismo cliente mientras
  tanto, se recalcula.
- **Las variables en línea son las del entrenamiento**, también con dos comercios a la vez: una
  prueba compara miles de valores contra el cálculo por lotes y son iguales.
- **Si falla el modelo o faltan datos:** modo `degradado`; el pago queda en revisión (salvo que una
  regla de seguridad lo rechace). Nunca se aprueba a ciegas.
- Claves guardadas con *hash*, límite de peticiones por clave, tamaño máximo de petición, registro
  de auditoría que no se puede editar ni borrar, cabeceras de seguridad y errores en formato
  estándar `application/problem+json`.
- **Swagger y ReDoc se sirven desde el propio servidor** (sin CDN), con la huella de cada archivo de
  terceros en `api/estaticos/MANIFIESTO.txt` (una prueba avisa si alguno cambia). Se probó que no pidan
  nada a internet y que no se desborden a 320 px.

**Servidor local con HTTPS y varios procesos:**

```bash
python -m modelo_ia.api.servidor --https         # certificado de prueba autofirmado (nunca para producción)
python -m modelo_ia.api.servidor --workers 4     # varios procesos
```

HSTS queda **apagado por defecto** (`MODELO_IA_HSTS`), porque en `localhost` el navegador lo
recordaría durante meses. La política de contenido de las páginas (`/docs`, `/panel`, `/tienda`)
está en modo **«solo observar»**: con unos días de uso sin avisos en la consola de Chromium, se pasa
a bloquear con `MODELO_IA_CSP=enforce`. `MODELO_IA_PANTALLAS=0` hace que la API no entregue las pantallas (las entrega otro servidor web; ver `frontend/README.md`) y `MODELO_IA_FRONTEND` cambia la carpeta de donde las lee. `MODELO_IA_DURABILIDAD=FULL` fuerza cada guardado a disco
(más lenta).

**Medido** en un computador, con el modelo en uso y pagos de ejemplo (`python -m
modelo_ia.api.prueba_carga`, solo contra una base de prueba), con 20 % de reintentos del mismo pago:

| Procesos | A la vez | Pagos por segundo | Mediana | p95 | p99 | Errores |
|---|---|---|---|---|---|---|
| 1 | 1 | 65 | 20 ms | 23 ms | 27 ms | 0 |
| 1 | 8 | 66 | 160 ms | 192 ms | 330 ms | 0 |
| 1 | 32 | 57 | 602 ms | 1,2 s | 1,75 s | 0 |
| 4 | 8 | 89 | 108 ms | 157 ms | 274 ms | 0 |
| 4 | 32 | 77 | 448 ms | 929 ms | 1,4 s | 0 |

La primera prueba de carga **encontró un problema real**: con muchas peticiones a la vez, la
velocidad *bajaba* a unas 35 por segundo, el peor caso tardaba hasta 30 segundos y aparecían errores
503, porque cada evaluación mantenía la base bloqueada mientras calculaba. Se corrigió calculando
fuera del bloqueo (ver arriba). Ahora no hay errores, pero el límite es el cálculo: **unos 65 pagos
por segundo por proceso** (más de 5 millones al día), de los cuales ~13 ms son construir las
variables con pandas y ~8 ms el modelo y las reglas. Más procesos ayudan poco porque SQLite sigue
siendo un solo escritor; para crecer hay que pasar a PostgreSQL y acelerar el cálculo de variables.
La documentación, el panel y la tienda se recorrieron en Chromium y WebKit (escritorio, 320 px,
iPhone 13 y Pixel 5): `python modelo_ia/pruebas_navegador/panel.py` (ver
`pruebas_navegador/README.md`).

**Lo que NO está hecho:**
- **No está desplegada.** Falta HTTPS con certificado real (y HSTS), un servidor de producción,
  PostgreSQL, copias de seguridad y monitoreo. Todo lo anterior se hizo y se probó **en local**.
- **El SDK solo existe para navegador**, no para apps móviles, y es de demostración (el «mouse
  natural» es una heurística simple).
- **No hay proveedor de pago.** Con `propio` la ubicación es de bases gratuitas (país y operador sí;
  ciudad poco fiable en Colombia; sin distinguir móvil de residencial; VPN comerciales no se
  detectan) y la reputación es una estimación fija por categoría, no una real. El KYC y el 2FA
  también están simulados.
- **La pantalla de analistas es una primera versión**: falta el protocolo de revisión, los tiempos
  máximos de respuesta, que cada analista tenga su propia identidad (hoy se entra con una clave de
  rol) y avisar al comercio cuando se resuelve (hoy consulta con `GET`).
- **Falta pasar la política de contenido a modo «bloquear»** tras unos días de uso, y los avisos de
  WebKit sobre ella son esperados (ese navegador no hace nada en modo «solo observar»).
- **Velocidad:** ~65 pagos por segundo por proceso; acelerar el cálculo de variables (sin pandas) es
  posible pero no se hizo.
- **La cola de revisión sería enorme** con el umbral actual: en una muestra de 2.000 pagos, el 13,6
  % de los legítimos quedó en revisión. Hay que ajustar los umbrales con el costo real de revisar.
- **Gradient Boosting no devuelve motivos por variable** (solo las reglas activadas).
- **No se probó un celular real ni Android real**, solo perfiles emulados de Chromium y WebKit.

## Proveedor de IP propio (bases públicas, sin enviar la IP a nadie)
`MODELO_IA_PROVEEDOR_IP=propio` ubica y clasifica la IP en el mismo servidor con bases descargadas
(`python -m modelo_ia.api.ip_datos descargar`, unos 70 MB que van a `datos_ip/`, fuera de git;
`estado` las comprueba):

| Dato | Fuente | Confianza |
|---|---|---|
| País y operador (ASN) | DB-IP Lite, mensual | país: buena; operador: regular |
| Ciudad | DB-IP Lite | solo se muestra; dentro de Colombia no se calcula distancia porque las IP de celular caen casi todas en la capital |
| Centro de datos | rangos publicados por AWS y Google Cloud + operadores de alojamiento conocidos por el nombre de su ASN | buena para esas nubes; heurística para el resto |
| Salidas de Tor | lista pública del Proyecto Tor | buena |
| Móvil / residencial, VPN comerciales, reputación global | no se sabe | la reputación es una estimación fija (normal 80, centro de datos 45, Tor 20, sin datos 50) |

Las descargas son atómicas y verificadas (si una falla se conserva la versión anterior); `GET
/v1/salud` avisa si las bases tienen más de 60 días. Para cambiar a un proveedor de pago basta otra
clase con `consultar(ip)`.

**Créditos y licencias de terceros.** La ubicación de IP usa las bases gratuitas de
[DB-IP.com](https://db-ip.com) (licencia CC BY 4.0: exige atribución, que se da en el pie del panel
y del dashboard y en esta documentación). Los rangos de AWS y Google Cloud y la lista de salidas de
Tor no publican licencia o términos: **pendiente de revisar con un abogado**, junto al acuerdo de
datos.

## Comercios: alta, suspensión y acuerdo de datos
- **Alta:** `POST /v1/comercios` (admin) o `admin crear-comercio`; la clave se muestra una sola vez.
  Razón social y NIT son opcionales; el NIT (en una persona natural es su cédula) solo lo ve un
  administrador y nunca va a la auditoría ni a otras respuestas.
- **Suspender / reactivar** (`POST /v1/comercios/{id}/suspender` con un `motivo`, y `…/reactivar`; o
  `admin suspender-comercio` y `reactivar-comercio`): todas las claves del comercio dan `403
  Comercio suspendido` al instante. La comprobación se hace **dentro del bloqueo de guardado** de
  cada escritura, así que ningún pago, resultado ni cambio de lista negra se guarda después de
  suspender (una prueba de 30 rondas concurrentes lo comprueba). Repetirlo es seguro. La plataforma
  (el equipo) **no se puede suspender**, y los analistas siguen viendo y resolviendo sus revisiones
  pendientes. Una clave inventada sigue dando 401.
- **Acuerdo de datos** (`PUT /v1/comercios/{id}/acuerdo` o `admin registrar-acuerdo`): se guardan
  **los datos** (versión, fecha de firma, vigencia y referencia al archivo firmado), no el
  documento. El historial no se edita (lo impiden triggers): el último registrado es el que vale.
  Estados: `sin_acuerdo`, `vigente`, `vence_pronto` (30 días o menos) y `vencido`.
- **Exigir el acuerdo:** `MODELO_IA_EXIGIR_ACUERDO=1` hace que un comercio sin acuerdo vigente
  reciba `403` al evaluar pagos (reportar resultados y usar su lista negra siguen permitidos). Viene
  **apagado** para no estorbar a la demo; `GET /v1/salud` avisa mientras lo esté. Activarlo al
  empezar el piloto.
- **Pendiente:** no valida que el acuerdo sea legalmente correcto (eso lo revisa el abogado), no hay
  contraseña ni segundo factor para el admin, el nombre del comercio no es único y los umbrales
  siguen siendo iguales para todos. Un comercio suspendido que no maneje el `403` en su checkout
  puede dejar de vender: la guía de integración debe decirlo.

## Panel de control: ver todo, filtrar y rastrear (API de solo lectura)

**Identificador de seguimiento.** Cada pago recibe, además del `id_transaccion` que pone el
comercio, un **`id_seguimiento` que asigna este sistema** (por ejemplo `AF-7K3Q-9M2X`: 8 caracteres
legibles, sin letras que se confundan). Llega en la respuesta y en la cabecera `X-Id-Seguimiento`,
se conserva igual si el comercio repite el pago, y sirve para consultar (`GET
/v1/evaluaciones/AF-7K3Q-9M2X`), buscar en el panel y hablar con soporte. Quedan tres
identificadores por pago: el del comercio, el de seguimiento (para personas) y el interno
(`id_evaluacion`).

**Endpoints** (etiqueta «Panel de control» en Swagger; todos son `GET`):
- `/v1/panel/transacciones`: pagos con filtros por comercio, fechas, decisión, modo, monto, cliente,
  regla, IP y dispositivo; orden y paginación.
- `/v1/panel/transacciones/{id}`: **todo sobre un pago**: identificadores, datos recibidos y cuáles
  NO se mandaron, probabilidad y umbrales, reglas activadas y su efecto, la cadena de decisión paso
  a paso, revisión, resultado posterior, auditoría y pagos relacionados.
- `/v1/panel/resumen`: totales, por día, por comercio, reglas más activadas, distribución del
  puntaje, calidad (modo degradado, sin estado, latencia).
- `/v1/panel/vinculos`: varios clientes desde una misma IP, un mismo dispositivo con varios
  clientes, un cliente con varias IPs y ráfagas desde una IP.
  Cada bloque se pagina con `limite` (1–200, por omisión 50) y `desplazamiento` (0–1000), y
  `paginacion.hay_mas` avisa si hay otra página. Las ráfagas se calculan sobre las 200 IPs con más
  pagos. Una página cuesta lo mismo que otra (el costo es la agregación): con 1 millón de pagos,
  5,7 s sin filtro y 0,6 s con 7 días; acota por fechas.
- `/v1/panel/comercios`: lista para el filtro (solo analistas y administradores).

**Qué garantiza:** analistas y administradores ven todos los comercios; un comercio solo ve lo suyo
(si pide otro, 404). La **IP y la huella del dispositivo nunca se muestran**: solo códigos cortos de
8 caracteres que permiten reconocer repeticiones. Nada del usuario entra al SQL como texto
(parámetros, listas fijas y validación). No hay ninguna forma de escribir desde el panel.

**Las pantallas (`/dashboard`):** cuatro vistas sobre esa API, con la clave de analista (en la demo
entra sola). **Resumen** (indicadores, pagos por día, reglas más activadas, distribución del
puntaje, calidad y tabla por comercio), **Transacciones** (13 filtros, orden, paginación; cada fila
lleva al detalle), **Detalle de un pago** (identificadores, decisión y umbrales, la decisión
explicada paso a paso, reglas, datos recibidos con lo que NO se mandó, pagos relacionados, revisión
y auditoría) y **Vínculos** (misma IP, mismo dispositivo, cliente con varias IPs, ráfagas; cada fila
salta a sus pagos). Solo hace peticiones `GET`, sin librerías ni fuentes externas, y todo texto de
la API se muestra como texto. El diseño parte de tres referencias de Dribbble (azul eléctrico con
titulares enormes, serifa con botones píldora oscuros, verde profundo sobre crema); los colores,
radios y espacios viven como variables en `dashboard.css`. Recorrido de navegador: `python
modelo_ia/pruebas_navegador/dashboard.py`.

**Diseño compartido y filtros.** Las tres pantallas (dashboard, revisión manual y tienda) usan una
sola hoja, `frontend/comun/diseno.css`, con colores, radios, espacios y piezas repetidas como
variables, en modo claro y oscuro: antes de escribir un estilo nuevo se busca ahí. La **revisión
manual** (`/panel`) se rehízo con indicadores arriba (por revisar, monto en la cola, riesgo alto, la
que más espera), **filtros** (buscar por ID o cliente, comercio, riesgo, monto mínimo, fechas,
orden) y cada pago como una fila con su medallón de riesgo que se abre en dos columnas: «por qué
dudó» y «tu decisión». Esos filtros son del servidor: `GET /v1/revisiones` acepta ahora
`comercio_id`, `desde`, `hasta`, `monto_min`, `monto_max`, `riesgo_min`, `riesgo_max`, `regla`, `q`
y `orden`, y responde también `monto_total_cop` y el nombre del comercio; `/v1/panel/*` acepta
además `riesgo_min`, `riesgo_max` y `q`.

**La tienda (`/tienda`) es un checkout.** Contacto y entrega, método de pago (Visa, Mastercard, PSE,
Nequi), resumen del pedido y, al pagar, una respuesta en palabras de cliente («¡Pago aprobado!»,
«Estamos revisando tu pago», «No pudimos procesar el pago») con el número de seguimiento y un enlace
al detalle en el dashboard. **La simulación de proveedores ya no se llena a mano:** por defecto
(modo `auto`) la conexión es la que ve el servidor (si pagas desde este mismo computador, una IP de
prueba residencial, porque 127.0.0.1 no se puede ubicar; las cabeceras `X-Forwarded-For` no se
creen), el dispositivo lo detecta el SDK y la verificación, la antigüedad, el 2FA y si ya usó ese
equipo salen de los registros de la tienda (`CLIENTES_DEMO`; un código desconocido es un cliente
nuevo). El navegador no puede cambiar nada de eso. Para probar un escenario (VPN, cuenta nueva) hay
un interruptor «Quiero probar otro escenario (solo demo)». El nombre, el vencimiento y el CVV
escritos nunca se leen ni se envían (lo vigila una prueba y el recorrido de navegador).

**Para llenar la demo:** `python -m modelo_ia.api.cargar_ejemplos` crea 3 comercios de ejemplo y
envía ~2.000 pagos **por la API real**, con escenarios sospechosos (misma IP, mismo dispositivo, un
cliente con muchas IPs, viaje imposible, ráfagas, proxy). Todo es inventado.

**Límites:** el análisis entre comercios (misma IP en comercios distintos) es lo que dejó pendiente
la decisión de la Ley 1581: aquí lo ve el analista, no el comercio. Las ráfagas usan una ventana
fija de 10 minutos. Con millones de filas habrá que medir el rendimiento de los resúmenes (hoy hay
índices, pero se probó con unos miles).

## Registro de modelos y deriva

**El problema.** Un modelo (`.joblib`) se carga con `joblib.load`, que **ejecuta código**: si
alguien cambia el archivo, ese código correría al arrancar la API. Y «el modelo en uso» era solo una
copia con el nombre `modelo_actual.joblib`: sin historia de quién lo cambió ni cuándo, y sin forma de
volver atrás.

**El registro** (tabla `modelos_registro`, migración 3). Cada modelo tiene su **huella SHA-256**, su
versión y un estado: `candidato` → `en_uso` → `archivado`. Hay **dos roles** (el modelo `principal` y
el `sin_estado`, para pagos donde el comercio no manda el estado del dispositivo) y, como mucho, un
modelo en uso por rol (lo garantiza un índice único de la base). Se administra desde la consola:

```
admin modelo-adoptar        # registra y pone EN USO los modelo_actual*.joblib de siempre
admin modelo-listar         # huella, estado y cuál está en uso
admin modelo-registrar --archivo gradient_boosting_completo_v0.3.0.joblib
admin modelo-promover --id 4    # lo pone en uso; el anterior queda archivado
admin modelo-revertir --rol principal   # vuelve al modelo anterior
admin modelo-verificar      # comprueba la huella de los que están en uso (sin cargarlos)
```

Promover y revertir son una sola transacción (nunca quedan dos modelos en uso ni ninguno a medias) y
quedan en la auditoría. Hay que **reiniciar el servicio** para que cargue el modelo nuevo.

**Verificación al arrancar.** Si el registro tiene **algún** modelo en uso, todo lo que se deserializa
se verifica: el archivo se lee **una sola vez**, se calcula su SHA-256 sobre esos mismos bytes y solo
si coincide se deserializa desde esos bytes (no hay una ventana entre «verificar» y «cargar»). Ya
cargado, se comprueba además que sus propios datos (modelo, versión y conjunto de variables)
coincidan con los registrados, porque el `.json` con que se registra no está protegido por la huella.
Si algo no coincide, o el archivo ya no está, **el servicio no arranca** y dice por qué. Lo que **no
está registrado no se carga**: sin modelo `sin_estado` registrado esa variante queda desactivada (con
un aviso), y un principal sin registrar es un error. Con el registro **totalmente vacío** se usa
`modelo_actual.joblib` como antes, con un aviso en el registro de eventos; con
`MODELO_IA_EXIGIR_REGISTRO=1` el servicio exige un modelo registrado (en producción avisa mientras
esté apagado). `MODELO_IA_MODELO` (un modelo elegido a mano) solo se acepta con el registro vacío y
sin exigirlo: con modelos en uso se rechaza (quita la variable para usar el modelo verificado).
`admin modelo-adoptar` no reemplaza un modelo que ya esté en uso y sea otro.

**Deriva.** `GET /v1/panel/deriva` (solo personal) compara la distribución de los **puntajes** de los
pagos de los últimos N días (7 por defecto) con la de la validación del modelo, en 10 cubetas de 0,1,
con el **PSI** (índice de estabilidad de la población): menos de 0,10 estable, de 0,10 a 0,25
atención, más de 0,25 alerta. Con menos de 500 pagos en la ventana
(`MODELO_IA_DERIVA_MIN_MUESTRA`) no se da estado, porque el PSI sería ruido. Solo cuentan los pagos
del modelo en uso, con puntaje, de la variante con estado del dispositivo. `/v1/metricas` suma
`antifraude_deriva_psi` y `antifraude_deriva_pagos`. `GET /v1/panel/modelos` lista el registro y marca
cuál se está ejecutando con la huella verificada.

La referencia (los puntajes de validación) la guarda `entrenar` en el `.json` del modelo. A los
modelos ya entrenados se les calcula con `admin modelo-referencia --id N --datos <archivo>`, que
puntúa su conjunto de validación con el modelo (antes verificado).

**Límites.** La referencia de la deriva es la del modelo que **está corriendo** (por su número en el
registro), no la del último promovido: tras `modelo-promover` y antes de reiniciar, la deriva sigue
midiendo el modelo viejo. La consulta de deriva se hace en cada petición y en cada lectura de
`/v1/metricas`; desde la migración 5 se responde solo con un índice (`ix_eval_modelo_deriva`, con las seis columnas que lee): con 600 000 pagos pasó de
78 a 5 ms en SQLite (ventana de 7 días) y de 38 a 11 ms en PostgreSQL. Cuesta unos 0,03 ms más por pago guardado y ~14 % más de espacio. Mide si los **puntajes cambiaron**, no si el modelo acierta (eso exige las etiquetas, que
llegan tarde). Con datos sintéticos solo demuestra el mecanismo. No mide deriva por comercio (muestras
muy chicas) ni de cada variable de entrada. No reentrena solo. La huella protege de cambios
accidentales o de un archivo sustituido, **no** si alguien controla el servidor entero (podría cambiar
también la base). Tampoco cambia el modelo en caliente: hay que reiniciar.

## Seguridad de la cuenta de administrador: segundo factor y claves inválidas

**Segundo factor (TOTP).** Con `MODELO_IA_EXIGIR_MFA=1`, las 4 acciones de administrador que
**cambian algo** (crear, suspender y reactivar comercios, registrar acuerdos) piden además el código
de 6 dígitos de una app de autenticación en la cabecera `X-TOTP`. Las lecturas del administrador
(panel, métricas, listar) siguen con la clave sola. Se activa desde la consola
(`admin activar-mfa`, `admin confirmar-mfa`, `admin desactivar-mfa`): el secreto se muestra **una
vez** en tu terminal y queda pendiente hasta confirmarlo con un primer código (así un error al
copiarlo no deja a nadie fuera).

- Es TOTP estándar (RFC 6238, HMAC-SHA1, 6 dígitos, pasos de 30 s) hecho con la biblioteca estándar;
  una prueba lo comprueba contra los **vectores oficiales** de la RFC.
- Se acepta el código del paso actual, uno antes y uno después (desfase de reloj de hasta ~30 s).
  **Un código no se puede usar dos veces** ni uno más viejo que el último aceptado: se guarda el
  último paso usado, y se decide dentro del bloqueo de escritura de la base, así dos peticiones con
  el mismo código a la vez no pasan las dos, tampoco desde dos procesos.
- **5 códigos malos seguidos bloquean esa clave 15 minutos** (`429` con `Retry-After`); un acierto
  pone la cuenta en cero. El estado va en la base, así vale entre procesos.
- Un comercio o un analista que llame a esas rutas recibe `403` sin ninguna pista del segundo factor.
- **Qué NO pide el segundo factor:** las lecturas del administrador y las acciones de **analista**,
  como resolver una revisión (`POST /v1/revisiones/{id}/resolver`): un administrador también puede
  hacerlas con la clave sola, porque son trabajo de analista y no de administración.
- Al **desactivar** o **rotar** una clave se borra su secreto TOTP (no queda uno huérfano en la base;
  los respaldos hechos **antes** de ese momento sí conservan el secreto viejo, que ya no sirve porque la
  clave está apagada).
- El secreto, los códigos y los fallos **nunca** salen en registros, métricas, errores ni en el
  detalle de la auditoría (sí queda constancia de `mfa_activado`, `mfa_fallido`, `mfa_bloqueada`…).
- Apagado por defecto (`MODELO_IA_EXIGIR_MFA`), como el acuerdo de datos; en producción el servicio
  avisa mientras esté apagado.

**Límite por IP de claves inventadas.** Pasadas `MODELO_IA_LIMITE_INVALIDAS` (20) claves que no
existen por minuto desde una IP, los siguientes intentos con claves inválidas reciben `429`. Las
claves **válidas nunca se frenan**, y no cuentan las peticiones sin clave ni las de un comercio
suspendido. Las claves son de 256 bits al azar: adivinarlas no es realista, así que esto sirve sobre
todo para frenar el ruido y dar una alerta temprana (`antifraude_claves_invalidas_total`).

- La IP real viene de `X-Forwarded-For` **solo si** la conexión llega de un proxy declarado en
  `MODELO_IA_PROXY_CONFIABLE`; se toma la primera dirección no confiable de derecha a izquierda, así
  lo que escriba un atacante a la izquierda no cuenta. Si no hay proxy declarado se usa la IP de la
  conexión. Una IPv6 se agrupa por su red /64. Un valor mal escrito impide arrancar.
- La IP solo vive en memoria del limitador (con tope de tamaño): no se guarda ni se escribe.

**Límites:** protege contra una **clave de API robada**, no contra un servidor comprometido (la
consola ya es de confianza). El secreto del TOTP está en la base sin cifrado adicional: cifrarlo con
una clave guardada en el mismo servidor ayudaría poco. Los contadores por IP son **por proceso**. Un
reintento del mismo código (p. ej. doble clic) cuenta como un fallo. Solo cubre al administrador. No
probado con un Caddy real, con una app de autenticación real ni en celular.

## Observabilidad: identificador de petición, registros y métricas

**Identificador de petición.** Cada respuesta lleva la cabecera `X-Request-ID`. Si el cliente manda
uno se respeta solo cuando es corto y de caracteres seguros (letras, números, `.`, `_`, `-`, de 8 a
64); cualquier otro valor se reemplaza por uno generado. Los errores (`application/problem+json`)
traen el mismo valor en el campo `request_id`: un comercio lo cita en soporte y tú encuentras su
petición en los registros.

**Registros.** Una línea por petición con hora, nivel, `request_id`, método, la **plantilla** de la
ruta (`/v1/evaluaciones/{id_evaluacion}`, nunca el id real), estado, duración en milisegundos, rol y,
si es un comercio, su número. `/v1/salud` no se registra (es ruido de monitoreo).
`MODELO_IA_LOG_FORMATO` elige el formato: `json` (una línea JSON por registro), `texto` (legible,
para desarrollo) o `auto` (por defecto: JSON en producción, texto en desarrollo).

**Qué nunca se escribe:** la clave de API, el cuerpo de la petición, el query string ni la IP. Un
filtro además tapa cualquier texto con forma de clave (`mia_<rol>_…`) que se cuele en un mensaje de
error. Una prueba envía valores «señuelo» (claves, ids de cliente, IP, ciudades) por todos esos
caminos y comprueba que ninguno aparece en el registro.

**Métricas** (`GET /v1/metricas`, solo con clave de **administrador**, formato Prometheus):

- `antifraude_http_peticiones_total{metodo,ruta,estado}`: peticiones atendidas.
- `antifraude_http_duracion_segundos{ruta}`: histograma de la duración.
- `antifraude_evaluaciones_total{decision,modo}`: pagos evaluados por primera vez (un reintento
  idempotente no cuenta).

No hay series por comercio a propósito: la actividad de un comercio no debe verse en una serie
compartida y una etiqueta por comercio o por id crece sin límite. Un verbo HTTP raro se cuenta como
`OTRO`. Con varios procesos (`--workers 2`, como en el contenedor) cada uno guarda sus contadores en
archivos de una carpeta común (`PROMETHEUS_MULTIPROC_DIR`, que `servidor.py` crea sola) y al leer se
suman, así la cifra es la del servicio completo.

**Dos límites a tener presentes.** (1) La traza de un error inesperado incluye el mensaje de la
excepción tal como lo escribe la librería (por ejemplo pandas o scikit-learn), que en un caso raro
podría traer un valor del pago: antes de compartir registros fuera del servidor, revísalos. (2)
`/v1/metricas` pasa por el proxy como el resto de `/v1/`: queda abierta a internet y protegida solo
por la clave de administrador. Son datos agregados (sin ids ni comercios); si prefieres cerrarla
hasta que exista un lector, bloquéala en el `Caddyfile` como `/demo`.

**Lo que NO hay:** alertas ni tableros (Grafana), almacenamiento central de registros (si el
contenedor se recrea se pierden, salvo que configures el controlador de logs de Docker) ni trazas
distribuidas. Los datos quedan listos para cuando se quieran.

## Despliegue en un servidor real (preparado, todavía no desplegado)

La carpeta `despliegue/` deja todo listo para llevar el servicio a un servidor, con su manual paso a
paso en `despliegue/LEEME.md`: imagen del servicio sin permisos de administrador y con el código en
solo lectura (`Dockerfile`), cuatro piezas con `docker-compose.yml` (la API, Caddy con HTTPS
automático, copias de seguridad y vigilancia), versiones exactas de las librerías
(`requirements-produccion.txt`) y un ejemplo de variables (`.env.ejemplo`).

- **Secretos.** En producción (`MODELO_IA_ENTORNO=produccion`) el servicio **se niega a arrancar**
  si el secreto es el de desarrollo o tiene menos de 32 caracteres, y puede leerlo de un archivo
  (`MODELO_IA_PEPPER_ARCHIVO`) para que no viaje en una variable de entorno. La API queda en una red
  **sin salida a internet**.
- **Copias de seguridad** (`python -m modelo_ia.despliegue.respaldo`): copia consistente aunque el
  servicio esté escribiendo, revisión de integridad antes de guardarla, cifrado AES-256 y
  autenticación con HMAC (un archivo alterado, truncado o con otra frase se rechaza antes de
  abrirlo), frase leída de un archivo privado y nunca de la línea de comandos, permisos 600,
  rotación (7 diarias, 4 semanales, 3 mensuales) y un gancho para copiarlas fuera del servidor.
- **Cada analista con su clave y su nombre** (`python -m modelo_ia.api.admin crear-clave --rol
  analista --nombre "Ana Pérez"`; también `listar-claves`, `rotar-clave` y `desactivar-clave`). El
  sistema no deja crear una clave de persona sin nombre ni repetir uno; anota cuándo se usó cada
  clave por última vez (como mucho una vez por hora) y registra quién decidió cada revisión. El
  personal ve el nombre en el panel de revisión, el dashboard (detalle, auditoría y «Equipo de
  revisión» del resumen) y `GET /v1/revisiones`; **un comercio nunca ve los nombres** (solo «un
  analista»). `GET /v1/yo` dice quién eres con tu clave. Límite: la clave es la única credencial,
  sin contraseña ni doble factor.
- **Vigilancia** (`python -m modelo_ia.despliegue.vigilancia`): servicio, tiempo de respuesta,
  certificado, disco, integridad de la base y, sobre todo, que las copias **sigan haciéndose**;
  avisa solo cuando el estado cambia.
- **Ensayo de desastre hecho** con el servicio real en modo producción (2 procesos): 25 pagos
  guardados, copia con el servicio funcionando, base destruida, restauración y arranque. Se
  recuperaron los 25 pagos con la misma decisión y el mismo identificador de seguimiento, y las
  claves siguieron sirviendo. Con un secreto distinto, las claves guardadas dejan de servir (por eso
  el secreto se guarda aparte).
- **Ensayo con Docker (en un Mac con Colima, dominio `localhost`, certificado local de prueba).** Se
  construyó la imagen (Python 3.12, 672 MB; sin datos, pruebas ni bases adentro), se validó el
  Caddyfile con la imagen oficial de Caddy y se levantaron las cuatro piezas con `docker compose`.
  Resultado: HTTPS con HSTS y redirección, lo de demostración cerrado (404) y tope de 64 KB (413);
  la API **no puede salir a internet**, no escribe en su código y corre sin privilegios; se
  **reinicia sola** si el proceso se cae; el modelo da **exactamente los mismos puntajes** con
  Python 3.12 (contenedor) y 3.9 (desarrollo); una copia hecha en el contenedor (OpenSSL 3.5) se
  abre en el Mac (LibreSSL 3.3); y tras **destruir el volumen de datos**, la restauración devolvió
  los pagos, las claves y los identificadores de seguimiento. El ensayo encontró un error del manual
  (restaurar sin `--no-deps` arranca una API con base vacía); ya está corregido y vigilado por una
  prueba.
- **Qué NO se probó todavía:** un certificado real con un dominio público, un servidor Linux de
  verdad (en Linux los secretos de 600 pueden necesitar `chmod 644`, ver el manual), la copia hacia
  otro lugar y la vigilancia mirando el certificado público desde afuera.
- **Límites:** un solo servidor (pérdida máxima = el intervalo entre copias), SQLite en vez de
  PostgreSQL (alcanza para un piloto: ~65 evaluaciones por segundo por proceso) y el límite de
  peticiones se cuenta por proceso.

## Adaptador de historial: el diagnóstico para un comercio (`python -m modelo_ia.historial`)

Sirve para el «Camino A» del plan de piloto: un comercio entrega un archivo con sus pagos pasados
(CSV o Excel, con los nombres de columna que él use) y se le devuelve cuánto fraude habría detectado
el sistema y cuánto dinero habría ahorrado, **sin tocar su operación ni tu base real**.

```bash
python -m modelo_ia.historial ejemplo   --salida carpeta            # archivo INVENTADO de una tienda imaginaria, para probar
python -m modelo_ia.historial sugerir   pagos.csv --salida mapeo.json   # propone qué columna es cada campo (revísalo)
python -m modelo_ia.historial verificar pagos.csv --mapeo mapeo.json    # cuántas filas sirven y qué falta (no evalúa)
python -m modelo_ia.historial evaluar   pagos.csv --mapeo mapeo.json --salida carpeta
```

**Cómo funciona.** (1) El *mapeo* (un JSON) dice qué columna del archivo es cada campo de la API,
cómo se traducen sus textos («Tarjeta crédito Visa» → `VISA`) y cuál es la columna de resultado
(contracargo o fraude). (2) Cada fila se convierte con las mismas reglas de la API, incluido el
rechazo de números de tarjeta. (3) Los pagos se reproducen en **orden de fecha** por el mismo
servicio de la API, sobre una base temporal que se borra al terminar, moviendo el reloj a la fecha
de cada pago: las señales de historial (compras en 24 h, cliente nuevo, viaje imposible) salen como
habrían salido ese día. (4) Se mide contra los resultados reales y se escribe `informe.md` (para
leer), `informe.json` y `resultados.csv` (decisión por pago, identificado por su fila en el
archivo).

**Elige solo el modelo.** Un modelo con más señales detecta más pero exige más datos; si faltan, el
servicio entra en modo degradado y manda todo a revisión (el primer intento con el archivo de
ejemplo dio 100 % degradado). El adaptador prueba, del más completo al menos, cuál tiene TODO lo que
pide con al menos 95 % de cobertura: `completo`, `perfil` (hora, historial y perfil de red) o
`historial`. El informe dice cuáles señales no se usaron y qué datos pedirle al comercio para
usarlas. Con el archivo de ejemplo se eligió `perfil`.

**Privacidad.** Los códigos de cliente, pedido y dispositivo se vuelven códigos irreversibles solo
de letras antes de evaluar (con dígitos, a veces parecían un número de tarjeta y la API los
rechazaba); las IP no se guardan; los motivos de rechazo hablan de campos, nunca de valores; la
consola solo imprime conteos. Con `--sin-seudonimos` se mantienen los originales (solo para
depurar).

**Límites que el informe repite.** (a) El proveedor de IP es simulado: con IP reales casi toda la
red queda «desconocida», salvo que el archivo traiga tipo de IP, país y ciudad. (b) Los pagos de los
últimos `--madurez-dias` (90) no se miden: sus contracargos todavía pueden llegar. (c) Con menos de
30 fraudes el margen de error es enorme y el informe lo dice. (d) Los modelos se entrenaron con
datos sintéticos y sus umbrales no están calibrados a la tasa de fraude de un comercio: en el
archivo de ejemplo el nivel `perfil` marcó como dudoso más del 40 % de los pagos. Esos umbrales hay
que recalibrarlos con los datos reales del comercio. (e) Un modelo con identidad parcial (solo edad
de la cuenta y verificación, sin doble factor) se probó y **se descartó porque no aporta** (ver «Qué
señales aportan de verdad»).

## Qué señales aportan de verdad (y una idea que se descartó)

Cuando un comercio no tiene todos los datos, el adaptador de historial usa un modelo más pequeño.
Para saber dónde conviene poner el esfuerzo, se midió cuánto aporta cada grupo de señales por
separado, sumado al historial. Gradient Boosting, sin el estado del dispositivo, historial por
comercio, conjunto de prueba sintético (datos inventados: sirve para comparar grupos entre sí, no
para prometer resultados reales):

| Señales que usa el modelo, además del historial | Calidad (AP) | Costo (millones de COP) |
|---|---|---|
| Solo historial | 0,811 | 343 |
| + identidad parcial (edad de la cuenta y verificación) | 0,817 | 346 |
| + identidad completa (con doble factor, intentos fallidos y cambio de contraseña) | 0,858 | 331 |
| + huella del dispositivo | 0,856 | 313 |
| + ubicación de la IP | 0,864 | 306 |
| + comportamiento en el formulario | 0,861 | 315 |
| Todas juntas | 0,902 | 266 |

- **La identidad parcial se probó y se descartó.** Se construyó un grupo con solo la edad de la
  cuenta y el nivel de verificación y dos modelos que lo usaban. No mejoró nada (0,811 a 0,817; el
  costo incluso subió) y con el archivo de historial inventado el recall bajó de 0,84 a 0,72 (con 32
  fraudes, los márgenes de error se solapan). El código y los modelos no se conservaron. La idea de
  partida, que era «lo que más ayudaría a un comercio típico», era una suposición sin medir.
- **El valor de la identidad está en el doble factor, los intentos fallidos y el cambio de
  contraseña**, las señales de toma de cuentas; la edad de la cuenta y la verificación casi no
  pesan.
- **Lo que más sube la calidad** son la ubicación de la IP, el comportamiento, la huella y la
  identidad completa; juntas llevan la calidad de 0,81 a 0,90. Por eso, para el piloto, las palancas
  son: un proveedor de IP real, instalar el SDK en la tienda del comercio (huella y comportamiento)
  y pedirle los datos de doble factor, intentos fallidos y cambios de contraseña.
- Son datos sintéticos: con datos reales de un comercio el orden podría cambiar, y eso es justo lo
  que el piloto debe medir.

## Prueba de la metodología con datos públicos y reales

`python -m modelo_ia.validacion_publica` repite la forma de trabajar (corte por tiempo, umbral
elegido por costo en validación, logística frente a Gradient Boosting) sobre un conjunto **público y
real**: 284.807 pagos con tarjeta de comerciantes europeos, 492 fraudes (copia de OpenML, id 1597).
Se descarga solo y no se guarda en el repositorio.

**Qué valida y qué no.** Valida la metodología sobre fraude real, no inventado por nosotros. **No**
valida nuestras 47 variables: este conjunto solo trae 28 columnas anónimas (V1…V28) y el monto, sin
dispositivo, IP ni comportamiento. Los costos están en euros y el costo de una falsa alarma no es un
dato real, por eso se prueban 1, 5 y 25 euros.

**Supuesto:** la copia no trae la hora, así que se usa el orden de las filas (60 % entrenar, 20 %
validar, 20 % probar). Se comprobó que el orden es cronológico: la variable V3 baja con el tiempo en
el archivo original, y aquí su correlación con el número de fila es −0,405 (con las filas mezcladas,
0,004).

| Corte por tiempo, prueba con 75 fraudes | Calidad (AP) | Costo con falsa alarma de 5 € | Sin modelo |
|---|---|---|---|
| Logística | 0,744 | 3.547 | 7.729 |
| Gradient Boosting | 0,758 | 3.927 | 7.729 |

- **El enfoque funciona con fraude real:** con cualquiera de los dos modelos el costo baja entre 47
  % y 66 % frente a no tener modelo, según el costo de la falsa alarma.
- **Gradient Boosting no es claramente mejor que la logística** (0,758 frente a 0,744, con solo 75
  fraudes en la prueba, dentro del ruido). La ventaja que vemos en los datos sintéticos no se
  confirma aquí. Lo razonable es seguir midiendo ambos con datos reales antes de decidir.
- **El umbral elegido con tan pocos fraudes es inestable:** con Gradient Boosting y una falsa alarma
  de 1 €, el umbral dio 908 falsas alarmas; con 5 €, solo 9. Hay que elegir el umbral con más
  fraudes (o con revisión de varios periodos).
- **El corte al azar no salió más «bonito»** de lo que esperaba (AP 0,68 frente a 0,74 por tiempo).
  No son comparables: la parte de prueba al azar tiene otros fraudes (13.578 € sin modelo frente a
  7.729 €), así que no se concluye nada de ahí.

## Hipótesis H1: modelo frente a reglas fijas (con datos sintéticos)

H1 dice que el modelo detecta más fraude (recall) que las reglas fijas **sin subir** los falsos
positivos. Se probó con `python -m modelo_ia.comparar_h1`, sobre la prueba (septiembre en adelante,
112.798 pagos y 4.575 fraudes). Las reglas se ajustaron con la validación, no con la prueba.

| Método | Recall | Precisión | Falsos positivos (tasa) | Falsas alarmas | Costo (millones COP) |
|---|---|---|---|---|---|
| Reglas ingenuas | 0,79 | 0,19 | 14,4 % | 15.542 | 634 |
| Reglas ajustadas | 0,81 | 0,14 | 20,3 % | 22.008 | 540 |
| Modelo (umbral de menor costo) | 0,93 | 0,32 | 8,3 % | 9.019 | 337 |
| Modelo igualando las falsas alarmas de las reglas ajustadas | 0,97 | 0,18 | 18,5 % | 19.991 | 384 |
| Modelo + red de seguridad | 0,93 | 0,30 | 9,1 % | 9.829 | 349 |

Con la misma tasa de falsos positivos que las reglas, el modelo detecta unos **16 puntos más de
fraude** (IC 95 %: de +14,5 a +16,7 frente a las ajustadas, y de +15,1 a +17,4 frente a las
ingenuas) y la tasa de falsos positivos no sube. En ambas comparaciones el resultado apoya H1.

**Cómo leerlo con cuidado:**
- **Son datos sintéticos.** El generador creó el fraude con señales parecidas a las de las reglas, y
  el modelo aun así les gana. Pero esto sirve para ensayar el método, no para afirmar H1 sobre
  fraude real. Falta repetirlo con datos reales (etapa 3).
- **No hay 3D Secure, AVS ni CVV** en los datos. Las reglas son de monto, velocidad, ubicación y
  dispositivo, no la validación completa de una pasarela.
- **La red de seguridad cuesta algo aquí:** sumó unas 810 falsas alarmas y 12 millones de costo sin
  detectar más fraude. Es esperable: es política para ataques que los datos no muestran. Sus
  umbrales conviene revisarlos con datos reales.
- Las reglas ajustadas apagaron la regla de «muchas compras en 24 horas», porque hay comercios
  legítimos con muchas ventas.

## Hoja de ruta: de modelo de clase a producto

- **1. Línea base** (hecha)
  - *Qué se hace:* Regresión logística, partición por fecha, pruebas, bitácora.
  - *Cómo se sabe que terminó:* Entrena, decide y se puede repetir el resultado.
- **2. Mejorar el modelo**
  - *Qué se hace:* Más variables (perfil habitual de IP y de hora), ajuste de parámetros (el motor
    de reglas ya está hecho), Gradient Boosting frente a logística, probabilidades calibradas,
    explicación para modelos de árboles, validación mes a mes dentro del código.
  - *Cómo se sabe que terminó:* La bitácora muestra menos costo y menos falsas alarmas, sin perder
    detección, y el resultado se sostiene mes a mes.
- **3. Datos reales**
  - *Qué se hace:* Validar con datos reales: una pyme piloto o un conjunto público (PaySim, ULB).
    Consentimiento según la Ley 1581. Captura de contracargos confirmados. Reentrenar y comparar
    contra reglas fijas simples.
  - *Cómo se sabe que terminó:* El modelo supera a las reglas fijas con datos reales (hipótesis H1
    del anteproyecto).
- **4. Servicio**
  - *Qué se hace:* API que puntúa un pago en milisegundos, historial del cliente en tiempo real,
    huella del dispositivo y geolocalización frente a la IP, cola y herramienta de revisión manual,
    registro de cada decisión, vigilancia de que el modelo no se deteriore y reentrenamiento
    programado con los casos confirmados.
  - *Cómo se sabe que terminó:* Responde rápido, no se cae y toda decisión se puede explicar y
    auditar.
- **5. Producto vendible**
  - *Qué se hace:* Varios clientes a la vez, validación de identidad (KYC, 2FA) con proveedores,
    seguridad (acceso, cifrado, alcance de PCI-DSS), controles alineados con ISO/IEC 27001, NIST y
    COSO, contrato y precios, soporte, y usar los contracargos reales para seguir aprendiendo.
  - *Cómo se sabe que terminó:* Una o más pymes pagan por usarlo en un piloto.

Del 3 en adelante el nivel de riesgo pasa a 🔴: hay dinero, datos personales y permisos de por medio.

## Brechas frente a un modelo antifraude completo

Un modelo antifraude de transacciones debería cubrir cinco capas: monitoreo en tiempo real,
aprendizaje automático, datos de contexto, ciclo de gestión y respuesta, y cumplimiento normativo.
Esta tabla compara esa lista con lo que existe hoy (versión 0.1.0) y dice en qué etapa se cierra
cada brecha.

Estados: **Sí** (hecho y probado), **Parcial** (existe una parte), **No** (no existe).

- **1. Tiempo real**
  - *Componente:* Análisis de cada pago antes de aprobarlo
  - *Estado:* Parcial
  - *Qué hay hoy:* API REST con OpenAPI/Swagger, probada **en local** con muchas peticiones a la
    vez: unos 65 pagos por segundo por proceso, mediana de 20 ms, sin errores. HTTPS local
    verificado. No está desplegada ni usa una base de datos de producción.
  - *Se cierra en:* Etapa 4 (despliegue)
- **1. Tiempo real**
  - *Componente:* Motor de reglas (monto, frecuencia, horario, cambio de ubicación)
  - *Estado:* Sí
  - *Qué hay hoy:* `reglas/`: reglas fijas de línea base (ingenuas y ajustadas) y una red de
    seguridad que manda sobre el modelo en `analizar_transaccion`. Las reglas de la red son política
    y no están ajustadas con datos.
  - *Se cierra en:* Hecho (etapa 2, parte 1)
- **2. Aprendizaje automático**
  - *Componente:* Modelo predictivo
  - *Estado:* Sí
  - *Qué hay hoy:* Regresión logística, evaluada por fecha. Se comparan además árbol, Random Forest
    y Gradient Boosting.
  - *Se cierra en:* Hecho (etapa 1)
- **2. Aprendizaje automático**
  - *Componente:* Modelo adaptativo (aprende de lo nuevo)
  - *Estado:* No
  - *Qué hay hoy:* Se puede reentrenar con las etiquetas ya confirmadas y hay una simulación
    mensual, pero en los datos sintéticos no mejoró y no hay reentrenamiento automático.
  - *Se cierra en:* Etapa 4
- **2. Aprendizaje automático**
  - *Componente:* Comparar con el perfil habitual del cliente
  - *Estado:* Parcial
  - *Qué hay hoy:* El modelo usa el historial del cliente, qué tan habitual es su IP y su franja
    horaria, y variables de comportamiento (tiempo en el formulario, teclado, mouse), todo con datos
    sintéticos. No hay captura real de la biometría de comportamiento.
  - *Se cierra en:* Captura real: etapa 5
- **3. Contexto**
  - *Componente:* Huella digital del dispositivo
  - *Estado:* Parcial
  - *Qué hay hoy:* Hay un SDK de navegador (demostración) que calcula la huella y las señales del
    equipo, y el modelo las usa (con datos sintéticos). Falta el SDK móvil y la integración real en
    una tienda.
  - *Se cierra en:* Etapa 4
- **3. Contexto**
  - *Componente:* Geolocalización frente a la IP
  - *Estado:* Parcial
  - *Qué hay hoy:* El comercio manda solo la IP y la API la ubica, calcula la distancia a la ciudad
    declarada y completa los datos de red; la IP no se guarda. El proveedor es una interfaz con uno
    **simulado**: falta contratar y conectar uno real.
  - *Se cierra en:* Etapa 4
- **3. Contexto**
  - *Componente:* Identidad (KYC, biometría facial, 2FA)
  - *Estado:* Parcial
  - *Qué hay hoy:* El modelo usa el nivel KYC, la antigüedad de la cuenta y el resultado del 2FA que
    informa el comercio. No hay integración con un proveedor de KYC, biometría facial ni 2FA.
  - *Se cierra en:* Etapa 5
- **4. Gestión y respuesta**
  - *Componente:* Puntaje de riesgo y decisión (aprobar, revisar, bloquear)
  - *Estado:* Sí
  - *Qué hay hoy:* Probabilidad, umbrales elegidos por costo en validación y motivos de cada
    decisión.
  - *Se cierra en:* Hecho (etapa 1)
- **4. Gestión y respuesta**
  - *Componente:* Equipo y herramienta de revisión manual
  - *Estado:* Parcial
  - *Qué hay hoy:* Hay una pantalla de analistas (`/panel`), probada en 6 perfiles de navegador, con
    cola, detalle, confirmación en dos pasos y control de conflictos. Faltan el equipo, el
    protocolo, los tiempos máximos y la identidad individual de cada analista.
  - *Se cierra en:* Etapa 4
- **4. Gestión y respuesta**
  - *Componente:* Mejora continua con casos confirmados
  - *Estado:* Parcial
  - *Qué hay hoy:* La API recibe el resultado de cada pago (fraude confirmado, contracargo,
    legítimo) y el entrenamiento respeta cuándo se conoce cada etiqueta. Falta el reentrenamiento
    automático.
  - *Se cierra en:* Etapa 4
- **5. Cumplimiento**
  - *Componente:* ISO/IEC 27001, marcos NIST, control interno COSO
  - *Estado:* No
  - *Qué hay hoy:* Hay controles técnicos sueltos (claves con hash, auditoría que no se edita,
    límite de peticiones, cabeceras de seguridad), pero ninguno de estos marcos está implementado ni
    se menciona Ley 1581 en el código.
  - *Se cierra en:* Ley 1581: etapa 3. Resto: etapa 5

**Resumen:** 3 componentes hechos, 7 parciales y 2 sin hacer (de 12).

## Lo que NO existe todavía

- **Datos reales.** Todo se entrenó y se midió con datos sintéticos cuyas reglas de fraude definimos
  nosotros: los números muestran qué tan bien aprende un modelo esas reglas, no cuánto fraude real
  detectaría. H1, el valor de las variables nuevas y la conclusión sobre el historial compartido
  **hay que repetirlos con datos reales** (etapa 3).
- **Despliegue real y producto**: ver la lista de arriba y las etapas 4 y 5 de la hoja de ruta.
- **Reentrenamiento automático** y un modelo realmente adaptativo: en los datos sintéticos
  reentrenar cada mes no mejoró nada.
- **ISO/IEC 27001, NIST y COSO**: ningún marco está implementado; hay controles técnicos sueltos.
- La carpeta no está bajo control de versiones (git). Conviene hacerlo antes de seguir.
