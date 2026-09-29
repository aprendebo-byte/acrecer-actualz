from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .Services.Mobilia import MobiliaAPI
from .Services.Email import notificacion_inmueble_desactivado, notificacion_actualizacion_valor, notificar_intervencion_realizada
from .Services.Utils import guardar_historial_inmueble, guardar_historial_actualizacion_precio, guardar_historial_intervencion
from .Services.Contacto import registrar_disponibilidad_confirmada, registrar_desactivacion
from .Services.Logging import log_evento


def _inmueble_no_encontrado(inmueble):
    """True si Mobilia no devolvió el inmueble buscado."""
    return not inmueble or inmueble.get("error")


def _respuesta_no_encontrado(evento, codigo):
    """404 uniforme cuando Mobilia no tiene el inmueble, dejando rastro en el log."""
    log_evento(
        evento,
        "Inmueble no encontrado en Mobilia",
        codigo=codigo,
        nivel="WARNING",
    )
    return Response(
        {"error": f"Inmueble {codigo} no encontrado."},
        status=status.HTTP_404_NOT_FOUND,
    )


def _a_bool(valor):
    """Normaliza un valor 'boolean-like'. Devuelve None si no es interpretable."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, int):
        return valor == 1
    if isinstance(valor, str):
        return valor.strip().lower() in ("true", "1", "yes", "si", "sí")
    return None


class UpdateStatusView(APIView):
    """
    Actualiza el estado de disponibilidad de un inmueble.

    Body:
        inmueble_id (str)  : código del inmueble en Mobilia.
        status (bool-like) : True si sigue disponible, False si se retira.
        deactivation_reason (str)      : obligatorio solo si status es False.
        deactivation_reason_code (str) : opcional; código del catálogo de Mobilia.
                                         Si se omite se reutiliza el texto del motivo.
    """

    def post(self, request):
        data = request.data
        # `deactivation_reason` solo se exige en la rama de desactivación: al
        # confirmar disponibilidad no hay motivo que informar.
        for field in ('inmueble_id', 'status'):
            if field not in data:
                return Response(
                    {"error": f"Missing required field: {field}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        status_value = _a_bool(data['status'])
        if status_value is None:
            return Response(
                {"error": "Invalid status value. Must be boolean-like (true/false)."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if status_value is False and not data.get('deactivation_reason'):
            return Response(
                {"error": "Missing required field: deactivation_reason"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # -----------------------------
        # Obtención del inmueble
        # -----------------------------
        mobilia_api = MobiliaAPI()
        inmueble = mobilia_api.obtener_inmueble_por_codigo(data['inmueble_id'])
        if _inmueble_no_encontrado(inmueble):
            # Sin esta guarda se guardaba historial con campos nulos, se llamaba
            # a Mobilia con un código inexistente y se notificaba por correo.
            return _respuesta_no_encontrado("desactivacion", data['inmueble_id'])

        # -----------------------------
        # Procesa según estado
        # -----------------------------
        if status_value is True:
            # Inmueble sigue disponible
            guardar_historial_inmueble(
                payload=inmueble,
                respuesta="Sí, continua disponible",
                razon_desactivacion=None
            )
            # Se reenvía el inmueble ya consultado para no volver a descargar
            # el inventario completo de Mobilia.
            mobilia_api.confirmar_disponibilidad(data['inmueble_id'], inmueble=inmueble)
            # Sella la fecha de disponibilidad y reinicia el ciclo de contacto:
            # es lo que luego lee la regla de "más de 20 días" del agente.
            registrar_disponibilidad_confirmada(data['inmueble_id'])
            log_evento(
                "disponibilidad",
                "Disponibilidad confirmada",
                codigo=data['inmueble_id'],
            )

        else:
            # Inmueble desactivado
            reason_text = data['deactivation_reason']
            # Mobilia distingue código y texto libre del motivo. Si el cliente no
            # envía el código, se reutiliza el texto (comportamiento previo).
            reason_code = data.get('deactivation_reason_code') or reason_text

            historial_ok = guardar_historial_inmueble(
                payload=inmueble,
                respuesta="No, ha sido desactivado",
                razon_desactivacion=reason_text
            )
            # No perder el rastro de auditoría en silencio: si el historial no se
            # pudo guardar, se deja constancia explícita antes de desactivar en
            # Mobilia (el motivo real puede quedar sin registrar).
            if not historial_ok:
                log_evento(
                    "desactivacion",
                    "Historial de inmueble NO guardado; se desactiva en Mobilia sin registro de auditoría",
                    codigo=data['inmueble_id'],
                    nivel="ERROR",
                    detalle={"razon": reason_text},
                )

            mobilia_api.desactivar_inmueble(
                codigo=data['inmueble_id'],
                reason_code=reason_code,
                reason_text=reason_text
            )
            # Marca el inmueble como inactivo para que salga de la selección de contacto.
            registrar_desactivacion(data['inmueble_id'])
            # Notifica por correo la desactivación del inmueble.
            notificacion_inmueble_desactivado(
                codigo=data['inmueble_id'],
                direccion=inmueble.get("address"),
                propietario=inmueble.get("contactName"),
            )
            log_evento(
                "desactivacion",
                "Inmueble desactivado",
                codigo=data['inmueble_id'],
                detalle={"razon": reason_text, "codigo_razon": reason_code},
            )
        return Response(
            {"message": "Inmueble status updated successfully."},
            status=status.HTTP_200_OK
        )


class UpdatePriceView(APIView):
    """
    Actualiza el precio de un inmueble y envía notificación.

    Descripción:
        Este endpoint permite que un propietario o agente solicite la actualización del
        precio de un inmueble (arriendo o venta). El sistema:

            • Obtiene el inmueble desde Mobilia.
            • Calcula el precio actual dependiendo del tipo de operación.
            • Envía una notificación por correo con el valor anterior y el nuevo valor.
            • (Opcional) Registra historial de precios si usas guardar_historial_actualizacion_precio()

        *No implementa aún la actualización real del precio en Mobilia.*
        Solo prepara y envía la notificación. La respuesta lo refleja: informa
        que la SOLICITUD quedó registrada, no que el valor ya esté actualizado.

    Request Body (JSON):
        {
            "inmueble_id": "A-1023",
            "new_price": 1650000
        }

    Campos:
        inmueble_id (str): Código del inmueble en Mobilia.
        new_price (float): Nuevo valor propuesto.

    Response:
        200 OK -> {"message": "Solicitud de actualización de valor registrada...",
                   "actualizado_en_mobilia": false}
        400 Bad Request -> {"error": "Missing required field: X"}
        404 Not Found -> {"error": "Inmueble X no encontrado."}

    Ejemplo de solicitud:
        {
            "inmueble_id": "B-2045",
            "new_price": 320000000
        }

    Ejemplo de notificación enviada:
        Asunto:
            "Solicitud de actualización de valor – Inmueble B-2045"

        Cuerpo:
            Valor actual: 300,000,000
            Nuevo valor solicitado: 320,000,000
    """
    def post(self, request):
        data = request.data
        required_fields = ['inmueble_id', 'new_price']
        for field in required_fields:
            if field not in data:
                return Response(
                    {"error": f"Missing required field: {field}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        mobilia_api = MobiliaAPI()
        inmueble = mobilia_api.obtener_inmueble_por_codigo(data['inmueble_id'])
        if _inmueble_no_encontrado(inmueble):
            return _respuesta_no_encontrado("actualizacion_precio", data['inmueble_id'])

        valor_actual = inmueble.get("rentValue") if inmueble.get("serviceType") == "forRent" else inmueble.get("saleValue")
        notificacion_actualizacion_valor(
            codigo=data['inmueble_id'], direccion=inmueble.get("address"), propietario=inmueble.get("contactName"), valor_actual=valor_actual, valor_nuevo=data['new_price'], captador=inmueble.get("consigner"))
        guardar_historial_actualizacion_precio(
            payload=inmueble,
            valor_anterior=valor_actual,
            valor_nuevo=data['new_price']
        )
        log_evento(
            "actualizacion_precio",
            "Solicitud de actualización de valor registrada",
            codigo=data['inmueble_id'],
            detalle={"valor_anterior": valor_actual, "valor_nuevo": data['new_price']},
        )
        # Aquí se implementaría la lógica para actualizar el precio en Mobilia.
        # Hasta entonces la respuesta no debe afirmar que el valor ya cambió.
        return Response(
            {
                "message": (
                    "Solicitud de actualización de valor registrada y notificada. "
                    "El valor no se ha modificado en Mobilia."
                ),
                "actualizado_en_mobilia": False,
            },
            status=status.HTTP_200_OK
        )


class InterventionView(APIView):
    """
    Marca una intervención realizada en un inmueble.

    """

    def post(self, request):
        mobilia_api = MobiliaAPI()
        data = request.data
        required_fields = ['inmueble_id', 'intervention_type']
        for field in required_fields:
            if field not in data:
                return Response(
                    {"error": f"Missing required field: {field}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        inmueble = mobilia_api.obtener_inmueble_por_codigo(data['inmueble_id'])
        if _inmueble_no_encontrado(inmueble):
            return _respuesta_no_encontrado("intervencion", data['inmueble_id'])

        guardar_historial_intervencion(
            payload=inmueble,
            tipo_intervencion=data['intervention_type'],
        )
        captador = f"{inmueble.get('consigner') or ''} - {inmueble.get('branchName') or ''}".strip(" -")
        notificar_intervencion_realizada(
            codigo=data['inmueble_id'],
            direccion=inmueble.get("address"),
            propietario=inmueble.get("contactName"),
            numero=inmueble.get("contactPhone"),
            tipo_intervencion=data['intervention_type'],
            captador=captador,
            resumen=data.get('resumen'),
            sucursal=inmueble.get("branchName")

        )
        log_evento(
            "intervencion",
            "Intervención registrada",
            codigo=data['inmueble_id'],
            detalle={"tipo": data['intervention_type']},
        )
        return Response(
            {"message": "Intervention recorded successfully."},
            status=status.HTTP_200_OK
        )