import os
import time

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from dotenv import load_dotenv

from Inmuebles.models import Inmuebles
from ..models import MobiliaToken
from .Logging import log_evento

# Cargar las variables de entorno
load_dotenv()

# Timeout por defecto (segundos) para todas las llamadas a Mobilia.
HTTP_TIMEOUT = 20

# Clave de cache del listado de inmuebles activos (ver MOBILIA_CACHE_TTL).
CACHE_KEY_INMUEBLES = "mobilia:inmuebles_activos"

# Lock de renovación del token y cuánto espera un worker a que otro termine.
LOCK_KEY_TOKEN = "mobilia:token:lock"
LOCK_TTL_SEGUNDOS = 30
ESPERA_LOCK_INTENTOS = 10
ESPERA_LOCK_PAUSA = 0.5


class MobiliaAPI:
    TOKEN_TTL_MINUTES = 50  # ajusta según Mobilia

    def __init__(self):
        self.username = os.getenv("MOBILIA_USER")
        self.password = os.getenv("MOBILIA_PASSWORD")
        self.mobilia_url = os.getenv("MOBILIA_URL")

    def _login(self):
        # Falla temprano y claro si faltan credenciales en el entorno.
        if not self.mobilia_url or not self.username or not self.password:
            log_evento(
                "mobilia",
                "Credenciales de Mobilia incompletas (MOBILIA_URL/MOBILIA_USER/MOBILIA_PASSWORD).",
                nivel="ERROR",
            )
            raise RuntimeError(
                "Credenciales de Mobilia incompletas: revisa MOBILIA_URL, "
                "MOBILIA_USER y MOBILIA_PASSWORD en el entorno."
            )

        params = {
            "operation": "doLogin",
            "username": self.username,
            "password": self.password,
        }
        response = requests.get(f"{self.mobilia_url}/ws/Auth", params=params, timeout=15)
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            data = {}

        # Mobilia no siempre devuelve loginResults (credenciales inválidas,
        # respuesta de error, etc.). Lo manejamos en vez de reventar con KeyError.
        key = (data.get("loginResults") or {}).get("key")
        if not key:
            log_evento(
                "mobilia",
                "Login a Mobilia falló: la respuesta no trae loginResults.key.",
                nivel="ERROR",
                detalle={"respuesta": data},
            )
            raise RuntimeError(f"Login a Mobilia falló. Respuesta: {data}")

        return key

    def _token_vigente(self):
        """Token guardado si sigue dentro del TTL; None si no hay o venció."""
        token_obj = MobiliaToken.objects.first()
        if token_obj and not token_obj.is_expired(self.TOKEN_TTL_MINUTES):
            return token_obj.token
        return None

    def obtener_token(self):
        """
        Devuelve un token activo, renovándolo solo si no existe o está vencido.

        Mobilia invalida la sesión anterior en cada login, así que la renovación
        se serializa con un lock en cache: si varios workers de Gunicorn detectan
        el token vencido a la vez, solo uno hace login y el resto reutiliza el
        token resultante. La llamada HTTP se hace FUERA de la transacción para no
        mantenerla abierta durante segundos.

        Nota: con el backend locmem (desarrollo sin REDIS_URL) el lock es por
        proceso y no coordina entre workers. En producción se usa Redis.
        """
        token = self._token_vigente()
        if token:
            return token

        adquirido = cache.add(LOCK_KEY_TOKEN, "1", LOCK_TTL_SEGUNDOS)
        if not adquirido:
            # Otro worker está renovando: esperamos su token en lugar de hacer
            # un login paralelo que invalidaría el suyo.
            for _ in range(ESPERA_LOCK_INTENTOS):
                time.sleep(ESPERA_LOCK_PAUSA)
                token = self._token_vigente()
                if token:
                    return token
            log_evento(
                "mobilia",
                "Espera del lock de token agotada; se renueva de todas formas.",
                nivel="WARNING",
            )

        try:
            # Doble comprobación: pudo renovarse mientras adquiríamos el lock.
            token = self._token_vigente()
            if token:
                return token

            nuevo_token = self._login()

            # Escritura corta y atómica, ya sin la llamada HTTP dentro.
            with transaction.atomic():
                MobiliaToken.objects.all().delete()
                MobiliaToken.objects.create(token=nuevo_token)

            return nuevo_token
        finally:
            if adquirido:
                cache.delete(LOCK_KEY_TOKEN)

    def obtener_todos_los_inmuebles(self, usar_cache=True):
        """
        Listado de inmuebles activos de Mobilia.

        Mobilia solo expone el inventario completo, así que el resultado se
        cachea unos segundos (MOBILIA_CACHE_TTL) para no repetir la descarga
        varias veces dentro de la misma operación.
        """
        ttl = getattr(settings, "MOBILIA_CACHE_TTL", 0)

        if usar_cache and ttl > 0:
            cacheados = cache.get(CACHE_KEY_INMUEBLES)
            if cacheados is not None:
                return cacheados

        key = self.obtener_token()
        url = f"{self.mobilia_url}/ws/Extra?operation=getActivePropertiesInfo&key={key}"

        response = requests.post(url, timeout=HTTP_TIMEOUT)
        response.raise_for_status()

        inmuebles = response.json().get("results", [])

        if usar_cache and ttl > 0:
            cache.set(CACHE_KEY_INMUEBLES, inmuebles, ttl)

        return inmuebles

    def obtener_inmueble_por_codigo(self, codigo):
        inmuebles = self.obtener_todos_los_inmuebles()
        for inmueble in inmuebles:
            if inmueble.get("propertyCode") == codigo:
                return inmueble
        log_evento(
            "mobilia",
            "Inmueble no encontrado al consultar Mobilia",
            codigo=codigo,
            nivel="WARNING",
        )
        return {"propertyCode": codigo, "error": "Inmueble no encontrado"}

    def registrar_inmuebles(self):
        inmuebles = self.obtener_todos_los_inmuebles()
        for inmueble in inmuebles:
            Inmuebles.objects.update_or_create(
                codigo=inmueble.get("propertyCode"),
                defaults={
                    "estado": True,
                    "portales": inmueble.get("externalCodesJSON", [])
                }
            )

    def desactivar_inmueble(self, codigo, reason_code, reason_text):
        key = self.obtener_token()

        # Los valores van como query params para que requests los URL-encode
        # (espacios, tildes y & en el motivo no rompen la URL).
        params = {
            "operation": "updatePropertyByRentOrSale",
            "key": key,
            "username": self.username,
            "serviceType": "isWithdrawn",
            "deactivationReasonCode": reason_code,
            "deactivationReason": reason_text,
            "propertyCode": codigo,
        }

        response = requests.get(f"{self.mobilia_url}/ws/Extra", params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()

        # El inventario cacheado quedó obsoleto tras el cambio en Mobilia.
        cache.delete(CACHE_KEY_INMUEBLES)
        return response.json()

    def confirmar_disponibilidad(self, codigo, inmueble=None):
        """
        Confirma la disponibilidad del inmueble en Mobilia.

        `inmueble` permite reutilizar el registro que ya consultó quien llama y
        evitar así una segunda descarga del inventario completo.
        """
        if inmueble is None:
            inmueble = self.obtener_inmueble_por_codigo(codigo)

        key = self.obtener_token()
        service_type = inmueble.get("serviceType")

        params = {
            "operation": "updatePropertyByRentOrSale",
            "username": self.username,
            "key": key,
            "serviceType": service_type,
            "propertyCode": codigo,
        }
        response = requests.get(f"{self.mobilia_url}/ws/Extra", params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()

        cache.delete(CACHE_KEY_INMUEBLES)
        return response.json()
