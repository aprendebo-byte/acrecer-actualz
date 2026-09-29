"""
Utilidades compartidas por los endpoints de consulta (historial y dashboard).

Centraliza el parseo de la ventana temporal: es la parte con más aristas
(fecha suelta vs ISO 8601, fin de día, fechas imposibles) y tenerla dos veces
garantizaba que las dos divergieran.
"""

from datetime import datetime, time, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.exceptions import ValidationError


def aware(valor):
    """Convierte a datetime con zona horaria si USE_TZ está activo."""
    if timezone.is_naive(valor):
        return timezone.make_aware(valor, timezone.get_current_timezone())
    return valor


def parsear_instante(crudo, nombre, *, fin_de_dia=False):
    """
    Interpreta una fecha de filtro: ISO 8601 completo o solo fecha (YYYY-MM-DD).

    Con solo fecha, `fin_de_dia` la lleva a las 23:59:59.999999: `hasta=2026-07-28`
    debe incluir lo registrado ese día, no cortar a las 00:00.

    Se prueba `parse_date` ANTES que `parse_datetime` a propósito: `parse_datetime`
    también acepta una fecha suelta (la devuelve a medianoche), y entonces
    `fin_de_dia` nunca se aplicaría. Ambas propagan ValueError con fechas
    imposibles (2026-13-01), que aquí se traduce a 400.
    """
    crudo = crudo.strip()
    invalida = ValidationError(
        {nombre: f"Fecha inválida: '{crudo}'. Usa YYYY-MM-DD o ISO 8601."}
    )

    try:
        fecha = parse_date(crudo)
        if fecha is not None:
            hora = time.max if fin_de_dia else time.min
            return aware(datetime.combine(fecha, hora))

        momento = parse_datetime(crudo)
    except ValueError:
        raise invalida

    if momento is None:
        raise invalida

    return aware(momento)


def parsear_entero_positivo(crudo, nombre, *, maximo=None):
    try:
        numero = int(crudo)
    except (TypeError, ValueError):
        raise ValidationError({nombre: f"Debe ser un entero, no '{crudo}'."})
    if numero <= 0:
        raise ValidationError({nombre: "Debe ser mayor que cero."})
    if maximo is not None and numero > maximo:
        raise ValidationError({nombre: f"El máximo es {maximo}."})
    return numero


def rango_de_fechas(params, *, dias_por_defecto=None):
    """
    Ventana temporal pedida en `dias`, `desde` y `hasta`. Devuelve (desde, hasta).

    Los tres se combinan de forma restrictiva (AND): con `dias=7&desde=<antigua>`
    manda la más reciente de las dos, que es lo que hacía el historial cuando cada
    filtro se aplicaba por separado.

    `dias_por_defecto` acota la ventana cuando el cliente no pide ninguna, para que
    el dashboard no agregue sobre tablas que crecen sin límite.
    """
    desde = hasta = None

    dias = params.get("dias")
    if dias:
        desde = timezone.now() - timedelta(
            days=parsear_entero_positivo(dias, "dias")
        )

    crudo_desde = params.get("desde")
    if crudo_desde:
        explicito = parsear_instante(crudo_desde, "desde")
        desde = max(desde, explicito) if desde else explicito

    crudo_hasta = params.get("hasta")
    if crudo_hasta:
        hasta = parsear_instante(crudo_hasta, "hasta", fin_de_dia=True)

    if desde is None and hasta is None and dias_por_defecto:
        desde = timezone.now() - timedelta(days=dias_por_defecto)

    if desde and hasta and desde > hasta:
        raise ValidationError(
            {"desde": "El inicio de la ventana es posterior a 'hasta'."}
        )

    return desde, hasta


def acotar(qs, desde, hasta, campo="fecha_registro"):
    """Aplica la ventana temporal a un queryset."""
    if desde:
        qs = qs.filter(**{f"{campo}__gte": desde})
    if hasta:
        qs = qs.filter(**{f"{campo}__lte": hasta})
    return qs


def porcentaje(parte, total, decimales=2):
    """
    Porcentaje de `parte` sobre `total`. None si no hay base para calcularlo.

    Devolver None y no 0 evita que un dashboard sin datos muestre "0 % de
    desactivación" como si fuera un resultado medido.
    """
    if not total:
        return None
    return round(parte * 100 / total, decimales)
