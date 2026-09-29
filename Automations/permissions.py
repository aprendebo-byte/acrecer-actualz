"""
Autorización por token compartido para los endpoints de consulta (informes).

Es un token único de servicio (`API_INFORMES_TOKEN` en el entorno), no un usuario:
lo usan Power BI, hojas de cálculo, n8n o cualquier cliente que necesite leer el
historial desde fuera. Se acepta en tres formas para no obligar a cada cliente a
poder poner cabeceras:

    Authorization: Bearer <token>
    X-Api-Token: <token>
    ?token=<token>

El query param es el último recurso (queda en los logs del proxy y en el
historial del navegador); prefiere la cabecera cuando el cliente lo permita.
"""

from secrets import compare_digest

from django.conf import settings
from rest_framework.permissions import BasePermission


def _token_configurado():
    """Token esperado. Cadena vacía si no está definido en el entorno."""
    return (getattr(settings, "API_INFORMES_TOKEN", "") or "").strip()


def token_de_la_peticion(request):
    """Extrae el token de la cabecera Authorization, X-Api-Token o ?token=."""
    autorizacion = (request.META.get("HTTP_AUTHORIZATION") or "").strip()
    if autorizacion:
        partes = autorizacion.split()
        if len(partes) == 1:
            return partes[0]
        if len(partes) == 2 and partes[0].lower() in ("bearer", "token"):
            return partes[1]
        # Cualquier otro esquema (Basic, Digest...) no es el nuestro.
        return ""

    cabecera = (request.META.get("HTTP_X_API_TOKEN") or "").strip()
    if cabecera:
        return cabecera

    return (request.query_params.get("token") or "").strip()


class TokenInformes(BasePermission):
    """
    Permite el acceso solo si la petición trae el `API_INFORMES_TOKEN`.

    Falla cerrado: si la variable no está definida, nadie entra. Así un `.env`
    incompleto deja el historial inaccesible en lugar de abierto a Internet.
    """

    message = "Token de informes inválido o ausente."

    def has_permission(self, request, view):
        esperado = _token_configurado()
        if not esperado:
            self.message = (
                "Endpoint no configurado: falta API_INFORMES_TOKEN en el entorno."
            )
            return False

        recibido = token_de_la_peticion(request)
        if not recibido:
            return False

        # compare_digest evita filtrar la longitud/prefijo del token por tiempo.
        return compare_digest(recibido.encode("utf-8"), esperado.encode("utf-8"))
