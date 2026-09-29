"""
Memoria temporal de "conversaciones vivas".

Cada conversationId devuelto al enviar la plantilla de WhatsApp se guarda aquí
junto con los datos del inmueble, con un TTL de 24 horas. Permite que, cuando
llega un mensaje entrante, se consulte rápidamente el contexto del inmueble por
conversationId.

Backend: Django cache (Redis en producción; ver CACHES en settings).
"""

from django.core.cache import cache
from django.utils import timezone

# 24 horas
TTL_SEGUNDOS = 24 * 60 * 60

# Prefijo de las claves en cache.
_PREFIX = "conv:"


def _key(conversation_id):
    return f"{_PREFIX}{conversation_id}"


def construir_payload_inmueble(row):
    """
    Construye el objeto JSON del inmueble para la plantilla a partir de una
    fila de Mobilia (o de los campos planos del flujo n8n).

    Regla de negocio: `condominiumName` solo se incluye si existe junto a
    `address` (dirección). Cuando aplica, también se añade a la dirección
    mostrada.

    Acepta tanto claves de Mobilia (address, rentValue, contactName,
    propertyCode, condominiumName, contactPhone) como las del flujo n8n
    (direccion_inmueble, valor, nombre_propietario, codigo, ...).
    """
    row = row or {}

    address = (row.get("address") or row.get("direccion_inmueble") or "").strip()
    condominio = (row.get("condominiumName") or "").strip()
    nombre = row.get("contactName") or row.get("nombre_propietario") or ""
    codigo = row.get("propertyCode") or row.get("codigo") or ""
    canon = row.get("rentValue") or row.get("valor") or ""
    tipo = row.get("propertyTypeName") or row.get("tipo_inmueble") or ""
    telefono = row.get("contactPhone") or row.get("waid") or ""

    # Dirección a mostrar: si hay conjunto y dirección, se concatenan.
    direccion_inmueble = address
    if condominio and address:
        direccion_inmueble = f"{address}, {condominio}"

    payload = {
        "nombre_propietario": nombre,
        "tipo_inmueble": tipo,
        "direccion_inmueble": direccion_inmueble,
        "codigo": codigo,
        "canon": str(canon),
        "contactPhone": telefono,
    }

    # condominiumName solo viaja si existe junto a address.
    if condominio and address:
        payload["condominiumName"] = condominio

    return payload


def guardar_conversacion(conversation_id, payload, meta=None):
    """
    Guarda los datos de una conversación viva con TTL de 24h.

    Devuelve el registro almacenado (payload + metadatos).
    """
    registro = {
        "conversationId": conversation_id,
        **payload,
        "guardado_en": timezone.now().isoformat(),
    }
    if meta:
        registro.update(meta)

    cache.set(_key(conversation_id), registro, TTL_SEGUNDOS)
    return registro


def obtener_conversacion(conversation_id):
    """Devuelve los datos guardados o None si no existe / expiró."""
    if not conversation_id:
        return None
    return cache.get(_key(conversation_id))


def existe_conversacion(conversation_id):
    return obtener_conversacion(conversation_id) is not None
