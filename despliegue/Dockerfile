# Imagen del servicio antifraude. Se construye desde la carpeta RAÍZ del repositorio:
#     docker build -f despliegue/Dockerfile -t antifraude:0.3.0 .
# (o con `docker compose up -d --build` desde la carpeta despliegue/, que ya lo hace).
# Mejora pendiente: fijar la imagen base por su «digest» (python:3.12-slim@sha256:...) una vez construida la primera vez en el servidor.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# openssl: lo usan las copias de seguridad cifradas.
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

# OPCIONAL (apagado por omisión): pg_dump y pg_restore para las copias de seguridad de PostgreSQL (respaldo_pg.py). La versión debe ser IGUAL O MÁS NUEVA que la del servidor
# de PostgreSQL. Se activa con:  docker build --build-arg INCLUIR_CLIENTE_POSTGRES=1 --build-arg VERSION_CLIENTE_POSTGRES=16 ...   (construida y probada el 15-nov-2026: pg_dump 16.16 y openssl presentes, curl y gnupg fuera)
ARG INCLUIR_CLIENTE_POSTGRES=0
ARG VERSION_CLIENTE_POSTGRES=16
RUN if [ "$INCLUIR_CLIENTE_POSTGRES" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
      && install -d /usr/share/postgresql-common/pgdg \
      && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      && test "$(gpg --show-keys --with-colons /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc | awk -F: '/^fpr:/{print $10; exit}')" = "B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8" \
      || { echo "ERROR: la huella de la clave del repositorio de PostgreSQL NO coincide con la esperada (¿cambió la clave oficial o alguien la alteró?). Verifícala en postgresql.org antes de actualizar la huella en este archivo." >&2; exit 1; } \
      && . /etc/os-release \
      && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
      && apt-get update && apt-get install -y --no-install-recommends "postgresql-client-${VERSION_CLIENTE_POSTGRES}" \
      && apt-get purge -y curl gnupg && apt-get autoremove -y \
      && rm -rf /var/lib/apt/lists/*; \
    fi

# El servicio NO corre como administrador.
RUN groupadd --system --gid 10001 antifraude \
    && useradd --system --uid 10001 --gid antifraude --no-create-home --shell /usr/sbin/nologin antifraude

WORKDIR /app
COPY despliegue/requirements-produccion.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# El código queda en solo lectura para el usuario del servicio (es de root); solo /datos y /respaldos son escribibles.
COPY . /app/modelo_ia
RUN mkdir -p /datos /respaldos && chown antifraude:antifraude /datos /respaldos

ENV PYTHONPATH=/app \
    MODELO_IA_ENTORNO=produccion \
    MODELO_IA_BD=/datos/api.sqlite3 \
    MODELO_IA_RESPALDOS=/respaldos \
    MODELO_IA_DURABILIDAD=FULL

USER antifraude
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/salud', timeout=4).status == 200 else 1)"

# 2 procesos: medimos unas 65 evaluaciones por segundo por proceso (con 1 solo, un pago lento frena a los demás).
CMD ["python", "-m", "modelo_ia.api.servidor", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
