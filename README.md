# ACRECER · Actualización de Inventario API

API en Django 6 + DRF que conecta el agente de actualización de inventario con la
plataforma **Mobilia**. Registra el historial de novedades, persiste logs de
negocio en PostgreSQL y notifica por correo.

## Endpoints

Base: `/api/automations/`

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `update-status/` | Confirma disponibilidad o desactiva un inmueble. |
| POST | `update-price/` | Registra y notifica una solicitud de cambio de valor. |
| POST | `intervention/` | Registra una intervención sobre un inmueble. |

Si el inmueble no existe en Mobilia, los tres responden `404` y no producen
ningún efecto (ni historial, ni llamada a Mobilia, ni correo).

`update-price/` **no** escribe el valor en Mobilia: registra la solicitud y avisa
por correo. Su respuesta lo indica con `"actualizado_en_mobilia": false`.

`update-status/` acepta `deactivation_reason_code` opcional: Mobilia distingue el
código del catálogo del texto libre del motivo. Si se omite, se envía el texto en
ambos campos.

> ⚠️ Estos tres endpoints de escritura **no tienen autenticación**. No los expongas
> a Internet sin una capa de autorización delante. Los de consulta del historial
> (abajo) sí exigen token.

## Consulta del historial

Solo lectura, paginada y filtrable. Protegida con `API_INFORMES_TOKEN`, así que se
puede consultar desde cualquier cliente que tenga el token (Power BI, hoja de
cálculo, n8n, otro backend) sin abrir el resto de la API.

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `historial/` | Índice: rutas, filtros válidos y campos ordenables. |
| GET | `dashboard/` | Métricas agregadas ([sección propia](#dashboard-de-métricas)). |
| GET | `historial/respuestas/` | Disponibilidad confirmada y desactivaciones. |
| GET | `historial/precios/` | Solicitudes de cambio de valor (anterior y nuevo). |
| GET | `historial/intervenciones/` | Intervenciones registradas. |

El token se acepta de tres formas, por orden de preferencia:

```
Authorization: Bearer <API_INFORMES_TOKEN>
X-Api-Token: <API_INFORMES_TOKEN>
?token=<API_INFORMES_TOKEN>
```

El query param es el último recurso: queda registrado en los logs del proxy y en
el historial del navegador. Si `API_INFORMES_TOKEN` no está definido, los cuatro
endpoints responden `403` — **fallan cerrado**, para que un `.env` incompleto no
deje el historial abierto.

### Filtros

Comunes a los tres historiales:

| Parámetro | Efecto |
|-----------|--------|
| `codigo` (o `codigo_inmueble`) | Código exacto, sin distinguir mayúsculas. |
| `tipo_servicio` | `forRent` / `forSale`, exacto. |
| `tipo_inmueble`, `direccion`, `nombre_contacto`, `telefono_contacto` | Coincidencia parcial. |
| `desde`, `hasta` | `YYYY-MM-DD` o ISO 8601. `hasta` con fecha suelta incluye el día completo. |
| `dias` | Atajo: últimos N días. |
| `q` | Búsqueda libre sobre los campos de texto del historial. |
| `ordering` | `fecha_registro`, `codigo_inmueble`, `id`; prefijo `-` para descendente. |
| `page`, `page_size` | Paginación: 50 por página, máximo 500. |

Propios de cada uno:

- **respuestas**: `estado` (`disponible` / `desactivado`), `respuesta`,
  `razon_desactivacion`, `ciudad`, `conjunto`.
- **precios**: `ciudad`, `conjunto`.
- **intervenciones**: `captador`, `tipo_intervencion` (este modelo no guarda
  ciudad, conjunto ni valor).

Los filtros se combinan con AND. Un parámetro **desconocido devuelve 400** con la
lista de los válidos: un filtro mal escrito que se ignorara en silencio parecería
"no hay datos" y se leería como un problema de los datos.

```bash
# Desactivaciones de Bogotá del último mes, 100 por página
curl -H "Authorization: Bearer $API_INFORMES_TOKEN" \
  "http://localhost:8080/api/automations/historial/respuestas/?estado=desactivado&ciudad=Bogot%C3%A1&dias=30&page_size=100"

# Cambios de valor de un inmueble, del más antiguo al más reciente
curl -H "X-Api-Token: $API_INFORMES_TOKEN" \
  "http://localhost:8080/api/automations/historial/precios/?codigo=AC-1023&ordering=fecha_registro"
```

Respuesta paginada estándar de DRF:

```json
{"count": 240, "next": "...?page=2", "previous": null, "results": [ ... ]}
```

## Dashboard de métricas

```
GET /api/automations/dashboard/
```

Mismo token que el historial. Devuelve en un solo GET todo lo que se puede medir
con los datos que ya guarda la aplicación. Todo se calcula con agregados en
PostgreSQL: no se traen filas a memoria, así que el coste no crece con el
histórico (los rankings van acotados por `top`).

| Parámetro | Efecto |
|-----------|--------|
| `dias`, `desde`, `hasta` | Ventana temporal. Por defecto la de `INFORME_DIAS` (30 días); con `INFORME_DIAS=0` agrega todo el histórico. |
| `granularidad` | `dia` (por defecto), `semana` o `mes`, para la serie temporal. |
| `top` | Tamaño de los rankings: 10 por defecto, 50 máximo. |
| `secciones` | Lista separada por comas para pedir solo una parte (menos consultas SQL). |

### Secciones

| Sección | Métricas |
|---------|----------|
| `resumen` | Respuestas (disponibles / desactivados / inmuebles distintos y **tasa de desactivación**), solicitudes de precio, intervenciones, envíos de plantilla (tasa de éxito y de respuesta) y salud del log (errores y tasa de error). |
| `serie` | Evolución por día/semana/mes de disponibles, desactivados, solicitudes de precio, intervenciones y envíos (éxito/fallo). |
| `desactivaciones` | Ranking de **motivos** con su porcentaje, desactivaciones **sin motivo**, y desglose por ciudad y tipo de inmueble. |
| `precios` | Subidas / bajadas / sin cambio, variación promedio y total, variación **porcentual** promedio, valor anterior y nuevo promedios, mínimo y máximo, y ranking de mayores subidas y bajadas. |
| `intervenciones` | Total, inmuebles distintos, ranking por tipo y por captador. |
| `distribucion` | Inventario tocado por ciudad, tipo de inmueble, tipo de servicio y conjunto — cada uno con su tasa de desactivación y valor promedio (dónde se está cayendo el inventario). |
| `contacto` | Embudo: activos/inactivos, sin contactar, intentos agotados, promedio de intentos, distribución de intentos y **candidatos a contacto** según las reglas vigentes. |
| `actividad` | A qué **hora** (local) y qué **día de la semana** responden los propietarios: para elegir cuándo enviar las plantillas. |
| `logs` | Conteo por nivel y por evento, y los últimos errores (mensaje recortado a 300 caracteres). |

```bash
curl -H "X-Api-Token: $API_INFORMES_TOKEN" \
  "http://localhost:8080/api/automations/dashboard/?dias=90&granularidad=semana&top=5"

# Solo lo que necesita un tile de KPIs
curl -H "X-Api-Token: $API_INFORMES_TOKEN" \
  "http://localhost:8080/api/automations/dashboard/?secciones=resumen"
```

Tres detalles que conviene saber al leer los números:

- **`contacto` es una foto del momento**, no de la ventana: `estado` e
  `intentos_contacto` son campos mutables sin historia en la tabla `inmuebles`, así
  que no se pueden reconstruir para un rango pasado. Lo indica con
  `"tipo": "snapshot"`. La única métrica de esa sección que sí respeta la ventana es
  `desactivados_en_ventana`, porque `fecha_desactivacion` sí es un instante.
- **`tasa_respuesta_pct_aprox` es aproximada**: la respuesta del propietario puede
  llegar días después del envío, así que en ventanas cortas el numerador y el
  denominador no son la misma cohorte.
- Una tasa **sin base de cálculo devuelve `null`, no `0`**, para que un dashboard sin
  datos no muestre "0 % de desactivación" como si fuera una medición.

## Configuración

1. Copia `.env.example` a `.env` y completa los valores.
2. Instala dependencias: `pip install -r requirements.txt`
3. Migra y arranca:
   ```
   python manage.py migrate
   python manage.py runserver
   ```

`DEBUG` es `False` por defecto: si la variable falta, se asume producción. En ese
modo `SECRET_KEY` y `ALLOWED_HOSTS` son **obligatorias** y el arranque falla con un
mensaje explícito si no están definidas.

Verifica la configuración de despliegue con:

```
python manage.py check --deploy
```

## Logs en PostgreSQL

Los eventos de negocio (intervenciones, desactivaciones, cambios de precio,
envíos de correo, informes) y los errores se guardan en la tabla `registro_log`
y se pueden consultar desde el admin de Django. Configurado en `LOGGING`
(settings) mediante `Automations.logging_handlers.PostgresLogHandler`.

Para registrar un evento desde el código:

```python
from Automations.Services.Logging import log_evento
log_evento("intervencion", "Mensaje", codigo="A-1023", detalle={...})
```

## Informe del historial

Genera un `.xlsx` con tres hojas (respuestas, precios, intervenciones) y lo
envía por correo:

```
python manage.py enviar_informe               # ventana de INFORME_DIAS (30 por defecto)
python manage.py enviar_informe --to a@b.com  # destinatarios específicos
python manage.py enviar_informe --dias 90     # otra ventana
python manage.py enviar_informe --dias 0      # todo el histórico
python manage.py enviar_informe --conservar   # no borra el xlsx tras enviarlo
```

El xlsx se borra tras un envío exitoso para que `Media/` no acumule un archivo
por día; si el envío falla, se conserva para revisarlo.

## Reglas de contacto

[contacto_inmuebles.py](contacto_inmuebles.py) decide a qué inmuebles toca enviar
el siguiente intento de contacto. Es un módulo puro (sin Django ni red), con
`ahora` y `enviar_fn` inyectables:

- solo inmuebles activos,
- disponibilidad confirmada hace **más de 20 días**,
- máximo **3 intentos**,
- al menos **1 día** entre intentos.

El conteo de intentos es **durable**: vive en la tabla `inmuebles`
(`intentos_contacto`, `fecha_ultimo_mensaje`), no en la memoria de 24h — los 3
intentos se reparten en 3 días o más. `Automations.Services.Contacto` es el puente:
`enriquecer_con_estado_durable` inyecta el conteo antes de aplicar las reglas y
`registrar_intento_contacto` lo incrementa tras cada envío exitoso.

Enviar plantillas a los inmuebles que corresponda:

```
python manage.py enviar_plantillas --archivo inmuebles.json
python manage.py enviar_plantillas --archivo inmuebles.json --diagnostico
```

`--diagnostico` no envía nada: muestra por cada inmueble si se contactaría o el
motivo del descarte (útil para responder "¿por qué solo N?").

## Conversación viva (integración WhatsApp / n8n)

Memoria temporal (Redis, TTL 24h) que guarda el contexto del inmueble por
`conversationId` para que n8n lo recupere al recibir un mensaje entrante.

| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/conversacion` | Guarda el contexto del inmueble por `conversationId`. |
| GET | `/conversacion?conversationId=<id>` | Devuelve el contexto vivo. |
| GET | `/conversaciones` | Lista los `conversationId` activos (últimas 24h). |

Cada envío deja **un** registro en `envio_plantilla_log`, deduplicado por
`messageId`: el backend lo registra al enviar y n8n vuelve a notificarlo.

## Tests

```
python manage.py test
```

Cubren las reglas de contacto (sin base de datos), las tres vistas con Mobilia y
el correo simulados, el historial, los endpoints de consulta (token, paginación y
cada filtro), cada métrica del dashboard con números calculados a mano, el estado
durable de intentos y la renovación del token de Mobilia.

## Docker

Levanta PostgreSQL + Redis + API (puerto 8080→8000) + cron del informe diario (07:00):

```
docker compose up -d --build
```

- **web**: Gunicorn en el puerto 8000, como usuario sin privilegios (`app`).
  Publicado en `8080:8000`.
- **db**: PostgreSQL con volumen persistente `pgdata`.
- **redis**: memoria de la conversación viva (TTL 24h).
- **cron**: ejecuta `manage.py enviar_informe` cada día a las 07:00 hora de
  Colombia (`CRON_TZ` en [docker/crontab](docker/crontab)). Corre como root
  porque el demonio cron lo requiere, y guarda los informes en el volumen `media`.

El contenedor usa `TZ=America/Bogota` para que la hora del cron y la de Django
coincidan.
