# Llevar el servicio a un servidor real

Este manual deja el servicio listo para desplegar. **Nada de esto se ha desplegado todavía.** Está pensado para un piloto con 2 o 3 comercios; al final dice cuándo hay que cambiar de enfoque.

## Qué hay en esta carpeta

| Archivo | Para qué sirve |
|---|---|
| `Dockerfile` | Arma la imagen del servicio (sin permisos de administrador, código en solo lectura). |
| `docker-compose.yml` | Levanta 4 piezas: la API, el servidor web con HTTPS (Caddy), las copias de seguridad y la vigilancia. |
| `Caddyfile` | Configuración del servidor web: HTTPS automático, tamaño y tiempos acotados, y cierra lo que es solo de demostración. |
| `.env.ejemplo` | Las variables que tú cambias. Se copia como `.env`. |
| `requirements-produccion.txt` | Versiones exactas de las librerías (los modelos guardados dependen de ellas). |
| `respaldo.py` | Copias de seguridad cifradas: crear, verificar, restaurar y rotar. |
| `vigilancia.py` | Revisión de salud: servicio, certificado, disco, base y copias. |

## Qué necesitas conseguir (decisiones tuyas)

1. **Un servidor.** Una máquina virtual pequeña con Linux, de unos 2 núcleos y 2 a 4 GB de memoria, es suficiente para un piloto. Cuesta del orden de 5 a 20 dólares al mes, según el proveedor. Elige uno con centros de datos cercanos y revisa dónde quedan los datos: si el servidor está fuera de Colombia, eso puede importar para la ley de datos personales (es uno de los puntos para el abogado).
2. **Un dominio** (por ejemplo `antifraude.tudominio.com`) apuntando a la IP del servidor.
3. **Un lugar fuera del servidor para guardar copias** (otro servidor, o un almacenamiento en la nube). Una copia que queda en el mismo servidor no te sirve si el servidor se pierde.
4. **Un proveedor de IP real**, antes de evaluar pagos de verdad (ver «Antes de recibir datos reales»).

## Preparar el servidor (una sola vez)

1. Instala Docker y el complemento Compose siguiendo la guía oficial de Docker para tu sistema.
2. **Cierra lo que no uses:** deja abiertos solo los puertos 22 (SSH), 80 y 443. Entra por SSH solo con llave (sin contraseña) y activa las actualizaciones automáticas de seguridad del sistema.
3. Clona el repositorio en el servidor y entra a `despliegue/`.

## Poner en marcha, paso a paso

Todo se hace en la carpeta `despliegue/` del servidor. **Los secretos no se pegan en ningún chat, correo ni documento.**

**1. Variables.**
```
cp .env.ejemplo .env
nano .env        # cambia DOMINIO y CORREO_ACME; deja lo demás como está por ahora
```

**2. Secretos** (dos archivos que solo existen en el servidor):
```
mkdir -p secretos respaldos && chmod 700 secretos
openssl rand -base64 48 | tr -d '\n' > secretos/pepper
openssl rand -base64 48 | tr -d '\n' > secretos/respaldo_frase
chmod 600 secretos/*
sudo chown 10001:10001 respaldos        # el servicio de copias corre con este usuario
```
- **`pepper`** protege las claves de API. **Si lo pierdes o lo cambias, todas las claves dejan de funcionar** y hay que crearlas de nuevo. Guarda una copia en un lugar seguro y distinto (un gestor de contraseñas).
- **`respaldo_frase`** abre las copias de seguridad. **Si la pierdes, las copias no se pueden abrir.** Guárdala también fuera del servidor.
- El servicio se niega a arrancar si el secreto es el de desarrollo o tiene menos de 32 caracteres.
- **Solo en Linux:** si los registros de la API dicen «No se pudo leer el archivo del secreto», es que el contenedor (usuario 10001) no puede leer un archivo con permisos 600 de otro usuario. Arréglalo con `chmod 644 secretos/*` y deja la carpeta `secretos/` en 700: así los demás usuarios del servidor siguen sin poder entrar. (En el ensayo en Mac con Colima no hizo falta.)

**3. Levantar.**
```
docker compose up -d --build
docker compose ps          # los cuatro deben estar «Up», y la API «healthy»
```
Caddy pide solo el certificado HTTPS la primera vez (tarda un minuto). Prueba: `https://TU-DOMINIO/v1/salud` debe responder `ok`.

**4. Crear la primera clave de administrador** (se muestra una sola vez, en tu terminal):
```
docker compose exec api python -m modelo_ia.api.admin crear-clave --rol admin
docker compose exec api python -m modelo_ia.api.admin crear-clave --rol analista
docker compose exec api python -m modelo_ia.api.admin crear-comercio --nombre "Comercio piloto 1"
docker compose exec api python -m modelo_ia.api.admin crear-clave --rol comercio --comercio-id 2
```
Guarda cada clave en un gestor de contraseñas. No se pueden volver a ver.

## Pruebas obligatorias antes de usarlo con datos de nadie

Estas dos pruebas son lo que separa «lo instalé» de «lo puedo operar».

**A. Copia de seguridad y restauración de verdad.**
```
docker compose exec respaldo python -m modelo_ia.despliegue.respaldo crear
docker compose exec respaldo python -m modelo_ia.despliegue.respaldo verificar /respaldos/antifraude_XXXXXXXX_XXXXXX.afbk
```
`verificar` descifra, revisa la integridad y cuenta las filas. Después **simula el desastre** en un servidor de pruebas: borra la base, restaura con
el comando de restauración de abajo (ver «Se perdió el servidor») y comprueba que el servicio arranca y los datos están: reenviar un pago que ya existía debe responder «repetida». Una copia que nunca se restauró no es una copia.

**B. La vigilancia debe quejarse.** Detén el servicio de copias (`docker compose stop respaldo`), espera más de 26 horas (o baja `MODELO_IA_MAX_HORAS_RESPALDO` para la prueba) y comprueba que
`docker compose logs vigilancia` muestra `[CRÍTICO]` por las copias. Si la vigilancia no avisa cuando algo falla, no sirve.

## Copia fuera del servidor

Las copias quedan en `respaldos/` (cifradas). Hay que sacarlas del servidor. La opción más simple: desde **otro computador o servidor tuyo**, programa una copia periódica, por ejemplo con `rsync`:
```
rsync -a --include='*.afbk' --exclude='*' usuario@servidor:/ruta/a/despliegue/respaldos/ /carpeta/segura/
```
Como los archivos están cifrados, el lugar donde queden guardados no ve su contenido. **La frase de las copias NO debe estar junto a ellas.**

## Qué vigilar y cómo enterarte

- `docker compose logs -f vigilancia` muestra el estado cada 5 minutos.
- Si defines `MODELO_IA_ALERTA_URL` (una dirección https que reciba JSON, por ejemplo un canal de mensajería), recibirás un aviso **solo cuando el estado cambie**.
- Además, usa un servicio externo de «ping» gratuito que consulte `https://TU-DOMINIO/v1/salud` cada pocos minutos: la vigilancia interna no te avisa si el servidor entero se apaga.

## Endurecer con el tiempo

- **Política de contenido (`MODELO_IA_CSP`):** déjala en `report-only` unos días, revisa que las pantallas y `/docs` funcionen y los avisos del navegador estén vacíos, y entonces pásala a `enforce` (cambia `.env` y `docker compose up -d`).
- **HSTS (`HSTS_SEGUNDOS`):** súbelo por escalones (300 → 86400 → 2592000 → 31536000) a medida que confirmas que el HTTPS funciona bien. Un valor alto es difícil de deshacer.
- **Imagen base:** la primera vez que construyas la imagen, fija `python:3.12-slim` por su «digest» en el `Dockerfile`, y revisa las actualizaciones de seguridad cada mes.

## Si algo sale mal

**El servicio no arranca.** `docker compose logs api`. Si dice «El servicio NO arranca en producción», falta el secreto o es muy corto.

**La API se cayó.** Si el proceso se cae solo, Docker lo levanta de nuevo (comprobado). Si lo detuvo una persona con `docker stop` o `docker kill`, Docker lo trata como una parada voluntaria y NO lo reinicia: súbelo con `docker compose up -d`.

**Se perdió el servidor.** Tiempo estimado: de 30 a 60 minutos si tienes las copias y los secretos.
1. Prepara un servidor nuevo y sigue «Preparar el servidor» y los pasos 1 a 3 **usando los mismos archivos `pepper` y `respaldo_frase`**.
2. Copia la última `.afbk` a `respaldos/`.
3. Restaura **antes de levantar la API**, y con `--no-deps` (sin esa opción Compose arranca primero la API, la API crea una base vacía y la restauración se niega a pisarla):
   ```
   docker compose run --rm --no-deps respaldo python -m modelo_ia.despliegue.respaldo restaurar /respaldos/ARCHIVO --destino /datos/api.sqlite3
   ```
4. `docker compose up -d` y comprueba `/v1/salud` y que un pago de antes responde «repetida».
5. Cambia el dominio al servidor nuevo. Se pierde, como máximo, lo guardado desde la última copia (por defecto, hasta 6 horas).

**Una clave de API se filtró.** Desactívala: `docker compose exec api python -m modelo_ia.api.admin desactivar-clave --id N` y crea otra.

## Qué garantiza esta preparación y qué no

**Probado en un ensayo con Docker (en un Mac con Colima, dominio `localhost` y certificado local de prueba):** la imagen se construye (672 MB) y los cuatro servicios arrancan; el HTTPS responde con HSTS, sin cabecera `Server`, con redirección de HTTP, lo de demostración cerrado y un tope de 64 KB por petición; la API no puede salir a internet ni escribir en su propio código; se reinicia sola si el proceso se cae; el modelo da exactamente los mismos puntajes con Python 3.12 (contenedor) y 3.9 (desarrollo); una copia hecha en el contenedor se abre en el Mac (otra versión de OpenSSL); se destruyó el volumen de datos y se restauró desde la copia con los pagos, las claves y los identificadores de seguimiento intactos.

**Todavía no probado:** la emisión de un certificado REAL con un dominio público (el de prueba usó la autoridad local de Caddy), el comportamiento en un servidor Linux de verdad, la copia hacia otro lugar (`rsync`) y la vigilancia mirando el certificado público desde afuera.

**Sí:** HTTPS con renovación automática; el servicio sin permisos de administrador, con el código en solo lectura y sin salida a internet; el secreto fuera de las variables de entorno; copias consistentes, cifradas y autenticadas (un archivo alterado se rechaza antes de abrirlo); vigilancia que avisa si las copias dejan de hacerse.

**No (y hay que saberlo):**
- **Un solo servidor.** Si se cae, el servicio está caído hasta que lo recuperes. Pérdida máxima de datos: el intervalo entre copias. Tiempo de recuperación: el de montar un servidor y restaurar.
- **SQLite, no PostgreSQL.** Alcanza para un piloto: se midieron unas 65 evaluaciones por segundo por proceso, sin errores. Conviene pasar a PostgreSQL cuando haya más de un servidor, un uso sostenido cercano al límite, o el comercio pida alta disponibilidad.
- **El límite de peticiones se cuenta por proceso**, y hay 2 procesos: el límite real puede ser hasta el doble del configurado.
- **No hay usuarios individuales para los analistas** (comparten una clave): no se sabe quién decidió cada revisión.
- **El proveedor de IP sigue simulado** mientras no configures uno real.
- **No hay una prueba de seguridad hecha por un tercero.**

## Antes de recibir datos reales de un comercio

1. HTTPS funcionando, y las pruebas A y B de arriba hechas.
2. Copia fuera del servidor funcionando y la frase y el secreto guardados aparte.
3. Un proveedor de IP real configurado.
4. El acuerdo de datos revisado por un abogado y firmado, y el aviso a los clientes del comercio publicado.
5. Una decisión sobre los analistas: usuario propio para cada uno.
6. Una prueba de seguridad externa, o al menos una revisión cuidadosa de los permisos del servidor.
