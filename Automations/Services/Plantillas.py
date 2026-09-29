"""
Envío de plantillas de WhatsApp desde el backend (reemplaza el nodo
"Lambda - Enviar Plantilla" de n8n).

`procesar_envio_inmueble` se inyecta como `enviar_fn` en
`contacto_inmuebles.get_inmuebles_a_contactar`: esa función decide a quién
contactar y, por cada inmueble, llama aquí para:
  1. Enviar la plantilla a la API de WhatsApp por HTTP.
  2. Si el envío es exitoso, guardar la conversación viva (Redis, TTL 24h).
  3. Notificar al webhook de n8n para registrar el contacto en PostgreSQL.
  4. Registrar el envío (éxito/fallo) en `envio_plantilla_log` y en los logs.
"""

import requests
from django.conf import settings

from ..models import EnvioPlantillaLog
from .Contacto import registrar_intento_contacto
from .Memoria import construir_payload_inmueble, guardar_conversacion
from .Logging import log_evento

# Campos que viajan como `params` en la plantilla (igual que el flujo n8n).
PARAMS_PLANTILLA = ("nombre_propietario", "tipo_inmueble", "direccion_inmueble", "codigo", "canon")

HTTP_TIMEOUT = 20


def registrar_envio_plantilla(conversation_id, codigo_inmueble, exitoso,
                              message_id, template_id, payload):
    """
    Deja UN registro de auditoría por envío.

    El backend registra el envío al hacerlo y el flujo n8n vuelve a notificarlo
    por `POST /conversacion`; sin deduplicar, cada envío quedaba dos veces. El
    `messageId` identifica el envío, así que sirve de clave. Un envío fallido no
    tiene messageId y por tanto no se deduplica (cada fallo es un evento real).
    """
    if message_id:
        registro, _creado = EnvioPlantillaLog.objects.get_or_create(
            conversation_id=conversation_id,
            message_id=message_id,
            defaults={
                "codigo_inmueble": codigo_inmueble,
                "exitoso": exitoso,
                "template_id": template_id,
                "payload": payload,
            },
        )
        return registro

    return EnvioPlantillaLog.objects.create(
        conversation_id=conversation_id,
        codigo_inmueble=codigo_inmueble,
        exitoso=exitoso,
        message_id=None,
        template_id=template_id,
        payload=payload,
    )


def enviar_plantilla(phone_number, template_id, params, timeout=HTTP_TIMEOUT):
    """
    Hace el POST a la API de WhatsApp. `phone_number` es el número destino.

    Devuelve un dict normalizado: {success, conversationId, messageId,
    status_code, raw, error}. `conversationId` es el que DEVUELVE el API
    (el identificador real de la conversación); si no viene, cae al número.
    """
    # La URL ya no tiene default en settings: si falta, se falla explícitamente
    # en lugar de hacer un POST a None.
    if not settings.WHATSAPP_API_URL:
        log_evento(
            "envio_plantilla",
            "WHATSAPP_API_URL no está configurada; no se puede enviar la plantilla.",
            nivel="ERROR",
        )
        return {
            "success": False,
            "conversationId": phone_number,
            "messageId": None,
            "status_code": None,
            "raw": None,
            "error": "WHATSAPP_API_URL no configurada",
        }

    if not template_id:
        log_evento(
            "envio_plantilla",
            "Sin template_id: revisa WHATSAPP_DEFAULT_TEMPLATE_ID_ARR en el entorno.",
            nivel="ERROR",
        )
        return {
            "success": False,
            "conversationId": phone_number,
            "messageId": None,
            "status_code": None,
            "raw": None,
            "error": "template_id no configurado",
        }

    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "target": "phone",
        "phoneNumber": phone_number,
        "templateId": template_id,
        "params": params,
    }

    try:
        resp = requests.post(settings.WHATSAPP_API_URL, json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            data = {}

        return {
            "success": bool(data.get("success", True)),
            "conversationId": data.get("conversationId") or phone_number,
            "messageId": data.get("messageId"),
            "status_code": resp.status_code,
            "raw": data,
            "error": None,
        }
    except requests.RequestException as e:
        return {
            "success": False,
            "conversationId": phone_number,
            "messageId": None,
            "status_code": getattr(e.response, "status_code", None),
            "raw": None,
            "error": str(e),
        }


def notificar_webhook_pg(conversation_id, template_id, message_id, payload, timeout=HTTP_TIMEOUT):
    """
    Notifica al webhook de n8n para que registre el contacto en PostgreSQL.

    Envía el body que espera el flujo "Guardar Contacto en PG". Un fallo aquí
    NO interrumpe el envío de la plantilla (solo se loggea). Devuelve bool.
    """
    url = getattr(settings, "N8N_GUARDAR_PG_WEBHOOK", "")
    if not url:
        return False

    body = {
        "conversationId": conversation_id,
        "nombre_propietario": payload.get("nombre_propietario"),
        "direccion_inmueble": payload.get("direccion_inmueble"),
        "tipo_inmueble": payload.get("tipo_inmueble"),
        "codigo": payload.get("codigo"),
        "valor": payload.get("canon"),
        "template_id": template_id,
        "message_id": message_id,
    }

    try:
        resp = requests.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log_evento(
            "webhook_pg",
            f"No se pudo notificar al webhook de PG: {e}",
            codigo=payload.get("codigo"),
            nivel="ERROR",
            detalle={"conversationId": conversation_id, "url": url},
        )
        return False


def procesar_envio_inmueble(inmueble):
    """
    Envía la plantilla de un inmueble y registra el resultado.

    Devuelve {conversation_id, exitoso, messageId, error}.
    """
    # Número destino al que se envía la plantilla (phoneNumber / waid).
    numero = str(
        inmueble.get("conversation_id") or inmueble.get("conversationId") or inmueble.get("waid") or ""
    ).strip()
    template_id = inmueble.get("template_id") or settings.WHATSAPP_DEFAULT_TEMPLATE_ID_ARR

    # Construye el payload normalizado y de ahí extrae los params de la plantilla.
    payload = construir_payload_inmueble(inmueble)
    params = {campo: payload.get(campo, "") for campo in PARAMS_PLANTILLA}

    res = enviar_plantilla(numero, template_id, params)
    exitoso = res["success"]

    # El conversationId que guardamos/propagamos es el que DEVUELVE el API de
    # envío (identificador real de la conversación); si no vino, cae al número.
    conversation_id = res["conversationId"]

    if exitoso:
        # Conversación viva: guarda el contexto del inmueble por 24h.
        guardar_conversacion(
            conversation_id,
            payload,
            meta={"messageId": res["messageId"], "templateId": template_id, "numero": numero},
        )
        # Conteo durable de intentos: la memoria de 24h no cubre los 3 días que
        # abarcan los 3 intentos, así que el contador vive en la tabla inmuebles.
        registrar_intento_contacto(payload.get("codigo"))
        # Notifica a n8n para registrar el contacto en PostgreSQL.
        notificar_webhook_pg(conversation_id, template_id, res["messageId"], payload)

    # Persistencia mínima del envío (deduplicada por messageId).
    registrar_envio_plantilla(
        conversation_id=conversation_id,
        codigo_inmueble=payload.get("codigo"),
        exitoso=exitoso,
        message_id=res["messageId"],
        template_id=template_id,
        payload=payload,
    )

    log_evento(
        "envio_plantilla",
        "Plantilla enviada" if exitoso else "Fallo al enviar plantilla",
        codigo=payload.get("codigo"),
        nivel="INFO" if exitoso else "ERROR",
        detalle={"conversationId": conversation_id, "numero": numero, "error": res["error"]},
    )

    return {
        "conversation_id": conversation_id,
        "numero": numero,
        "exitoso": exitoso,
        "messageId": res["messageId"],
        "error": res["error"],
    }
