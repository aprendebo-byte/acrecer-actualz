from datetime import timedelta

from ..models import HistorialIntervenciones, HistorialRespuestas, HistorialActualizacionesPrecios
from .Logging import log_evento
from django.conf import settings
from django.utils import timezone
import pandas as pd

# Textos canónicos de la respuesta del propietario.
RESPUESTA_DISPONIBLE = "Sí, continua disponible"
RESPUESTA_DESACTIVADO = "No, ha sido desactivado"

# Ventana para considerar que una desactivación sin motivo es "la misma" que la
# que ahora trae el motivo (el agente pregunta el motivo en el turno siguiente).
VENTANA_COMPLETAR_MOTIVO = timedelta(hours=24)


def valor_inmueble(payload: dict):
    """
    Valor del inmueble según el tipo de servicio.

    Antes se guardaba siempre `rentValue`, dejando los inmuebles en venta con
    valor nulo en el historial.
    """
    if payload.get("serviceType") == "forRent":
        return payload.get("rentValue")
    return payload.get("saleValue") or payload.get("rentValue")


def guardar_historial_inmueble(payload: dict, respuesta: str, razon_desactivacion=None):
    """
    Guarda o actualiza historial de un inmueble.

    - Si el inmueble está NO disponible pero NO trae razón aún -> crea registro.
    - Si posteriormente llega la razón -> completa ese registro de desactivación.

    El registro a completar debe ser una DESACTIVACIÓN reciente sin motivo. Sin
    filtrar por `respuesta` se sobrescribía una confirmación de disponibilidad
    (que legítimamente tiene `razon_desactivacion` nula) y se perdía el evento
    de desactivación.
    """

    codigo = payload.get("propertyCode")

    try:
        if respuesta == RESPUESTA_DESACTIVADO and razon_desactivacion:
            ultimo = (
                HistorialRespuestas.objects
                .filter(
                    codigo_inmueble=codigo,
                    respuesta=RESPUESTA_DESACTIVADO,
                    razon_desactivacion__isnull=True,
                    fecha_registro__gte=timezone.now() - VENTANA_COMPLETAR_MOTIVO,
                )
                .order_by("-fecha_registro")
                .first()
            )

            # Caso: había una desactivación reciente sin motivo → se completa.
            if ultimo:
                ultimo.razon_desactivacion = razon_desactivacion
                ultimo.save(update_fields=["razon_desactivacion"])
                return True

        # Si no aplica actualización, se crea un registro nuevo
        HistorialRespuestas.objects.create(
            codigo_inmueble=codigo,
            tipo_servicio=payload.get("serviceType"),
            tipo_inmueble=payload.get("propertyTypeName"),
            valor=valor_inmueble(payload),
            direccion=payload.get("address"),
            ciudad=payload.get("cityName"),
            conjunto=payload.get("condominiumName"),
            nombre_contacto=payload.get("contactName"),
            telefono_contacto=payload.get("contactPhone"),

            codigos_externos=payload.get("externalCodesJSON"),

            respuesta=respuesta,
            razon_desactivacion=razon_desactivacion
        )

        return True

    except Exception as e:
        log_evento("error", f"Error guardando historial de inmueble: {e}",
                   codigo=codigo, nivel="ERROR", exc_info=True)
        return False


def guardar_historial_actualizacion_precio(payload: dict, valor_anterior, valor_nuevo):
    try:
        HistorialActualizacionesPrecios.objects.create(
            codigo_inmueble = payload.get("propertyCode"),
            tipo_servicio = payload.get("serviceType"),
            tipo_inmueble = payload.get("propertyTypeName"),
            valor = valor_inmueble(payload),

            valor_anterior = valor_anterior,
            valor_nuevo = valor_nuevo,

            direccion = payload.get("address"),
            ciudad = payload.get("cityName"),
            conjunto = payload.get("condominiumName"),

            nombre_contacto = payload.get("contactName"),
            telefono_contacto = payload.get("contactPhone"),

        )

        return True

    except Exception as e:
        log_evento("error", f"Error guardando historial de precio: {e}",
                   nivel="ERROR", exc_info=True)
        return False

def guardar_historial_intervencion(payload: dict, tipo_intervencion: str):
    try:
        HistorialIntervenciones.objects.create(
            codigo_inmueble = payload.get("propertyCode"),
            tipo_servicio = payload.get("serviceType"),
            tipo_inmueble = payload.get("propertyTypeName"),
            direccion = payload.get("address"),
            captador = f"{payload.get('consigner') or ''} - {payload.get('branchName') or ''}".strip(" -"),
            nombre_contacto = payload.get("contactName"),
            telefono_contacto = payload.get("contactPhone"),

            tipo_intervencion = tipo_intervencion,
        )

        return True

    except Exception as e:
        log_evento("error", f"Error guardando historial de intervencion: {e}",
                   nivel="ERROR", exc_info=True)
        return False


def generar_xlsx_historial_general(dias=None):
    """
    Genera un xlsx con tres hojas (Respuestas, Precios, Intervenciones)
    a partir del historial almacenado. Devuelve la ruta del archivo o None.

    `dias` acota la ventana (por defecto `settings.INFORME_DIAS`); 0 o None
    explícito con INFORME_DIAS=0 incluye todo el histórico. La ventana evita que
    el informe cargue en memoria tablas que crecen sin límite.
    """
    if dias is None:
        dias = getattr(settings, "INFORME_DIAS", 0)

    try:
        respuestas = HistorialRespuestas.objects.all()
        precios = HistorialActualizacionesPrecios.objects.all()
        intervenciones = HistorialIntervenciones.objects.all()

        if dias and dias > 0:
            desde = timezone.now() - timedelta(days=dias)
            respuestas = respuestas.filter(fecha_registro__gte=desde)
            precios = precios.filter(fecha_registro__gte=desde)
            intervenciones = intervenciones.filter(fecha_registro__gte=desde)

        df_respuestas = pd.DataFrame.from_records(respuestas.values())
        df_precios = pd.DataFrame.from_records(precios.values())
        df_intervenciones = pd.DataFrame.from_records(intervenciones.values())

        media_dir = settings.MEDIA_DIR
        media_dir.mkdir(parents=True, exist_ok=True)
        nombre = f"Historial_inmuebles_{timezone.localtime():%Y%m%d_%H%M%S}.xlsx"
        archivo_path = str(media_dir / nombre)

        with pd.ExcelWriter(archivo_path, engine='openpyxl') as writer:
            # ExcelWriter exige al menos una hoja; un DataFrame vacío produce
            # una hoja con solo encabezados, que es justo lo que queremos.
            df_respuestas.to_excel(writer, sheet_name="Historial_Respuestas", index=False)
            df_precios.to_excel(writer, sheet_name="Historial_Precios", index=False)
            df_intervenciones.to_excel(writer, sheet_name="Historial_Intervenciones", index=False)

        log_evento("informe", "Informe de historial generado",
                   detalle={"archivo": archivo_path, "dias": dias or "todo"})
        return archivo_path

    except Exception as e:
        log_evento("error", f"Error generando historial general en XLSX: {e}",
                   nivel="ERROR", exc_info=True)
        return None
