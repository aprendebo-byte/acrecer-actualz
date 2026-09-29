#!/bin/sh
set -e

echo "Aplicando migraciones..."
python manage.py migrate --noinput

echo "Recolectando estáticos..."
python manage.py collectstatic --noinput || true

# Puerto no privilegiado: el contenedor corre como el usuario "app", no root.
PUERTO="${PORT:-8000}"

echo "Iniciando Gunicorn en el puerto ${PUERTO}..."
exec gunicorn ACRECER_actualizacion_inventario_API.wsgi:application \
    --bind "0.0.0.0:${PUERTO}" \
    --workers 3 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
