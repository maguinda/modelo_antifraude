# Imagen del servicio antifraude. Se construye desde la carpeta RAÍZ del repositorio:
#     docker build -f despliegue/Dockerfile -t antifraude:0.3.0 .
# (o con `docker compose up -d --build` desde la carpeta despliegue/, que ya lo hace).
# Mejora pendiente: fijar la imagen base por su «digest» (python:3.12-slim@sha256:...) una vez construida la primera vez en el servidor.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# openssl: lo usan las copias de seguridad cifradas.
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

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
