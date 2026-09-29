FROM python:3.12-slim

# La zona horaria del contenedor debe coincidir con TIME_ZONE de Django para que
# el informe "de las 07:00" salga a las 07:00 de Colombia y no a las 02:00.
ENV TZ=America/Bogota

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=ACRECER_actualizacion_inventario_API.settings

WORKDIR /app

# cron para los informes programados; libpq5 para psycopg2; tzdata para la
# zona horaria local (python:slim no la trae).
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron libpq5 tzdata \
    && ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/docker/entrypoint.sh /app/docker/entrypoint-cron.sh

# Usuario sin privilegios para el servicio web. Los directorios que la app
# escribe en tiempo de ejecución se crean aquí para que el volumen montado
# herede la propiedad correcta.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/Media /app/staticfiles \
    && chown -R app:app /app

# Puerto no privilegiado: ya no hace falta ser root para escuchar.
EXPOSE 8000

USER app

ENTRYPOINT ["/app/docker/entrypoint.sh"]
