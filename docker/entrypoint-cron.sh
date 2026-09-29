#!/bin/sh
set -e

# cron arranca un entorno limpio sin las variables del contenedor. Antes se
# volcaban a /etc/environment filtrando por prefijos (DB_|EMAIL_|MOBILIA_|...),
# lo que dejaba fuera en silencio cualquier variable nueva: REDIS_URL,
# WHATSAPP_*, N8N_*, NOTIFICACION_DESTINATARIOS...
#
# Ahora se vuelca TODO el entorno a un script que la tarea cron carga antes de
# ejecutar, sin lista de permitidos que mantener. Se usa shlex.quote para que
# valores con espacios, comillas o $ no rompan el script.
ARCHIVO_ENV=/tmp/cron-env.sh

python - > "$ARCHIVO_ENV" <<'PY'
import os
import shlex

# Variables propias del proceso/contenedor que no deben propagarse a la tarea.
OMITIR = {"PATH", "HOME", "PWD", "OLDPWD", "SHLVL", "TERM", "HOSTNAME", "_"}

for clave, valor in sorted(os.environ.items()):
    if clave in OMITIR or not clave.replace("_", "").isalnum():
        continue
    print(f"export {clave}={shlex.quote(valor)}")
PY

chmod 600 "$ARCHIVO_ENV"

# Instala la tabla cron y arranca el demonio en primer plano.
crontab /app/docker/crontab
touch /var/log/cron.log
echo "Cron iniciado (TZ=${TZ:-UTC}). Informe diario a las 07:00 hora local."
exec cron -f
