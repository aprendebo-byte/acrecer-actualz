"""
Puente entre las reglas puras de contacto y el estado durable en PostgreSQL.

`contacto_inmuebles.get_inmuebles_a_contactar` es deliberadamente independiente
de Django: recibe los intentos ya realizados en la propia entrada. El problema es
que nadie los estaba aportando: la memoria en proceso se pierde al terminar el
comando y la de Redis expira en 24h, mientras que los 3 intentos permitidos se
reparten en 3 días o más. Resultado: el conteo arrancaba en 0 en cada ejecución y
se podía recontactar al mismo propietario más de 3 veces.

Aquí se resuelve sin ensuciar el módulo puro:
  - `enriquecer_con_estado_durable` inyecta intentos y fechas desde el modelo
    `Inmuebles` antes de aplicar las reglas.
  - `registrar_intento_contacto` incrementa el contador tras un envío exitoso.
"""

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from Inmuebles.models import Inmuebles

from .Logging import log_evento


def _codigo(item):
    return (item or {}).get("codigo") or (item or {}).get("propertyCode")


def enriquecer_con_estado_durable(inmuebles):
    """
    Devuelve la lista con `intentos_realizados`, `lastMessageDate` y
    `lastAvailability` completados desde el modelo `Inmuebles`.

    No sobrescribe lo que ya venga en la entrada: si el llamador aporta el dato
    (p. ej. desde otra base), ese valor manda. Se consulta en una sola query.
    """
    if not inmuebles:
        return []

    codigos = [c for c in (_codigo(i) for i in inmuebles if isinstance(i, dict)) if c]
    guardados = {r.codigo: r for r in Inmuebles.objects.filter(codigo__in=codigos)}

    enriquecidos = []
    for item in inmuebles:
        if not isinstance(item, dict):
            enriquecidos.append(item)
            continue

        item = dict(item)
        registro = guardados.get(_codigo(item))

        # Solo si la entrada no trae ya el conteo (en cualquiera de sus dos nombres).
        if "intentos_realizados" not in item and "intentos" not in item:
            item["intentos_realizados"] = registro.intentos_contacto if registro else 0

        if registro is not None:
            if not item.get("lastMessageDate") and registro.fecha_ultimo_mensaje:
                item["lastMessageDate"] = registro.fecha_ultimo_mensaje.isoformat()
            if not item.get("lastAvailability") and registro.fecha_disponibilidad_confirmada:
                item["lastAvailability"] = registro.fecha_disponibilidad_confirmada.isoformat()

        enriquecidos.append(item)

    return enriquecidos


def registrar_intento_contacto(codigo, cuando=None):
    """
    Incrementa el contador durable de intentos y sella la fecha del último
    mensaje. Se llama solo tras un envío exitoso, para que un fallo se reintente.

    Devuelve el registro actualizado, o None si no hay código.
    """
    if not codigo:
        return None

    cuando = cuando or timezone.now()

    try:
        with transaction.atomic():
            registro, _creado = Inmuebles.objects.get_or_create(codigo=codigo)
            # F() para que el incremento sea atómico en la base de datos.
            Inmuebles.objects.filter(pk=registro.pk).update(
                intentos_contacto=F("intentos_contacto") + 1,
                fecha_ultimo_mensaje=cuando,
            )
        registro.refresh_from_db(fields=["intentos_contacto", "fecha_ultimo_mensaje"])
        return registro
    except Exception as e:
        log_evento(
            "contacto",
            f"No se pudo registrar el intento de contacto: {e}",
            codigo=codigo,
            nivel="ERROR",
            exc_info=True,
        )
        return None


def registrar_disponibilidad_confirmada(codigo, cuando=None):
    """Sella la fecha de disponibilidad confirmada y reinicia los intentos."""
    if not codigo:
        return None

    cuando = cuando or timezone.now()
    try:
        registro, _creado = Inmuebles.objects.update_or_create(
            codigo=codigo,
            defaults={
                "estado": True,
                "fecha_disponibilidad_confirmada": cuando,
                # Confirmada la disponibilidad, el ciclo de contacto vuelve a cero.
                "intentos_contacto": 0,
            },
        )
        return registro
    except Exception as e:
        log_evento(
            "contacto",
            f"No se pudo registrar la disponibilidad confirmada: {e}",
            codigo=codigo,
            nivel="ERROR",
            exc_info=True,
        )
        return None


def registrar_desactivacion(codigo, cuando=None):
    """Marca el inmueble como inactivo para que deje de entrar en la selección."""
    if not codigo:
        return None

    cuando = cuando or timezone.now()
    try:
        registro, _creado = Inmuebles.objects.update_or_create(
            codigo=codigo,
            defaults={"estado": False, "fecha_desactivacion": cuando},
        )
        return registro
    except Exception as e:
        log_evento(
            "contacto",
            f"No se pudo registrar la desactivación: {e}",
            codigo=codigo,
            nivel="ERROR",
            exc_info=True,
        )
        return None
