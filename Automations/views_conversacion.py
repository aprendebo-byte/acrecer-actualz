"""
Endpoints de la "conversación viva".

- POST /conversacion        -> guarda los datos del inmueble por conversationId
                               (lo llama n8n tras enviar la plantilla).
- GET  /conversacion?conversationId=<id>  -> devuelve los datos vivos.
- GET  /conversaciones      -> lista los conversationId activos (últimas 24h).
"""

from datetime import timedelta

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

from .models import EnvioPlantillaLog
from .Services.Memoria import (
    construir_payload_inmueble,
    guardar_conversacion,
    obtener_conversacion,
    existe_conversacion,
    TTL_SEGUNDOS,
)
from .Services.Plantillas import registrar_envio_plantilla
from .Services.Logging import log_evento


def _a_bool(valor, por_defecto=True):
    """Normaliza un valor 'boolean-like' proveniente de JSON/n8n."""
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return por_defecto
    if isinstance(valor, (int, float)):
        return valor == 1
    return str(valor).strip().lower() in ("true", "1", "yes", "si", "sí")


class ConversacionView(APIView):
    """Guarda y consulta los datos de la conversación viva."""

    def get(self, request):
        conversation_id = request.query_params.get("conversationId")
        if not conversation_id:
            return Response(
                {"error": "Missing required query param: conversationId"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = obtener_conversacion(conversation_id)
        if data is None:
            return Response(
                {"error": "conversationId no encontrado o expirado.",
                 "conversationId": conversation_id},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({"found": True, "conversationId": conversation_id, "data": data})

    def post(self, request):
        body = request.data or {}
        conversation_id = body.get("conversationId") or body.get("conversation_id")
        if not conversation_id:
            return Response(
                {"error": "Missing required field: conversationId"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # El inmueble puede venir anidado en "inmueble" o como campos planos.
        inmueble = body.get("inmueble") or body
        payload = construir_payload_inmueble(inmueble)

        exitoso = _a_bool(body.get("envio_exitoso", body.get("success", True)))
        message_id = body.get("messageId") or body.get("message_id")
        template_id = body.get("templateId") or body.get("template_id")

        # Solo guardamos en memoria viva los envíos exitosos.
        registro = None
        if exitoso:
            registro = guardar_conversacion(
                conversation_id,
                payload,
                meta={"messageId": message_id, "templateId": template_id},
            )

        # Persistencia mínima: éxito/fallo, conversationId, fecha. Deduplicada por
        # messageId, porque el backend pudo registrar ya este mismo envío.
        registrar_envio_plantilla(
            conversation_id=conversation_id,
            codigo_inmueble=payload.get("codigo"),
            exitoso=exitoso,
            message_id=message_id,
            template_id=template_id,
            payload=payload,
        )

        log_evento(
            "envio_plantilla",
            "Envío de plantilla registrado",
            codigo=payload.get("codigo"),
            nivel="INFO" if exitoso else "WARNING",
            detalle={"conversationId": conversation_id, "exitoso": exitoso},
        )

        return Response(
            {
                "stored": exitoso,
                "conversationId": conversation_id,
                "ttl_segundos": TTL_SEGUNDOS,
                "data": registro or payload,
                # Igual que el nodo "Retornar conversation_ids", pero por item.
                "conversation_ids": [conversation_id] if exitoso else [],
            },
            status=status.HTTP_200_OK,
        )


class ConversacionesView(APIView):
    """Lista los conversationId activos en memoria (envíos exitosos < 24h)."""

    def get(self, request):
        desde = timezone.now() - timedelta(seconds=TTL_SEGUNDOS)
        candidatos = (
            EnvioPlantillaLog.objects
            .filter(exitoso=True, fecha_registro__gte=desde)
            .order_by("-fecha_registro")
            .values_list("conversation_id", flat=True)
        )

        # Únicos preservando orden y que sigan vivos en cache.
        vistos = set()
        activos = []
        for cid in candidatos:
            if cid in vistos:
                continue
            vistos.add(cid)
            if existe_conversacion(cid):
                activos.append(cid)

        return Response({"total": len(activos), "conversation_ids": activos})
