"""
Cliente de Mobilia: renovación del token y cache del inventario.

Mobilia invalida la sesión anterior en cada login, así que dos renovaciones
simultáneas se pisan entre sí. Y como solo expone el inventario completo, cada
consulta por código descargaba todo.
"""

from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings

from Automations.models import MobiliaToken
from Automations.Services import Mobilia as mobilia_mod
from Automations.Services.Mobilia import CACHE_KEY_INMUEBLES, LOCK_KEY_TOKEN, MobiliaAPI

INVENTARIO = [
    {"propertyCode": "AC-1", "serviceType": "forRent"},
    {"propertyCode": "AC-2", "serviceType": "forSale"},
]


def api_de_prueba():
    api = MobiliaAPI()
    api.mobilia_url = "https://mobilia.example"
    api.username = "usuario"
    api.password = "clave"
    return api


class TokenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = api_de_prueba()

    def test_reutiliza_el_token_vigente(self):
        MobiliaToken.objects.create(token="TOKEN-VIGENTE")

        with patch.object(MobiliaAPI, "_login") as mock_login:
            self.assertEqual(self.api.obtener_token(), "TOKEN-VIGENTE")

        mock_login.assert_not_called()

    def test_renueva_cuando_no_hay_token(self):
        with patch.object(MobiliaAPI, "_login", return_value="NUEVO") as mock_login:
            self.assertEqual(self.api.obtener_token(), "NUEVO")

        mock_login.assert_called_once()
        self.assertEqual(MobiliaToken.objects.count(), 1)

    def test_dos_llamadas_seguidas_solo_hacen_un_login(self):
        with patch.object(MobiliaAPI, "_login", return_value="NUEVO") as mock_login:
            self.api.obtener_token()
            self.api.obtener_token()

        mock_login.assert_called_once()
        self.assertEqual(MobiliaToken.objects.count(), 1)

    def test_libera_el_lock_al_terminar(self):
        with patch.object(MobiliaAPI, "_login", return_value="NUEVO"):
            self.api.obtener_token()

        self.assertIsNone(cache.get(LOCK_KEY_TOKEN))

    def test_libera_el_lock_aunque_el_login_falle(self):
        with patch.object(MobiliaAPI, "_login", side_effect=RuntimeError("credenciales")):
            with self.assertRaises(RuntimeError):
                self.api.obtener_token()

        # Si el lock quedara puesto, ningún worker podría renovar hasta que expire.
        self.assertIsNone(cache.get(LOCK_KEY_TOKEN))

    def test_espera_el_token_del_worker_que_tiene_el_lock(self):
        """Con el lock ocupado no se hace un login paralelo: se usa su resultado."""
        cache.add(LOCK_KEY_TOKEN, "1", 30)

        def aparece_el_token(_segundos):
            # Simula que el otro worker terminó de renovar mientras esperábamos.
            MobiliaToken.objects.create(token="TOKEN-DEL-OTRO")

        with patch.object(mobilia_mod.time, "sleep", side_effect=aparece_el_token):
            with patch.object(MobiliaAPI, "_login") as mock_login:
                self.assertEqual(self.api.obtener_token(), "TOKEN-DEL-OTRO")

        mock_login.assert_not_called()


class LoginFueraDeTransaccionTests(TransactionTestCase):
    """
    `TransactionTestCase` en lugar de `TestCase`: este último envuelve cada
    prueba en una transacción, así que `in_atomic_block` sería siempre True y la
    comprobación no distinguiría nada.
    """

    def setUp(self):
        cache.clear()
        self.api = api_de_prueba()

    def test_el_login_ocurre_fuera_de_la_transaccion(self):
        """La llamada HTTP no debe mantener abierta una transacción de escritura."""
        estados = []

        def login_falso():
            estados.append(transaction.get_connection().in_atomic_block)
            return "NUEVO"

        with patch.object(MobiliaAPI, "_login", side_effect=login_falso):
            self.api.obtener_token()

        self.assertEqual(estados, [False])
        self.assertEqual(MobiliaToken.objects.count(), 1)


@override_settings(MOBILIA_CACHE_TTL=120)
class InventarioCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = api_de_prueba()
        MobiliaToken.objects.create(token="TOKEN")

    def _respuesta(self):
        respuesta = MagicMock()
        respuesta.json.return_value = {"results": INVENTARIO}
        respuesta.raise_for_status.return_value = None
        return respuesta

    def test_segunda_consulta_usa_la_cache(self):
        with patch.object(mobilia_mod.requests, "post", return_value=self._respuesta()) as mock_post:
            self.api.obtener_inmueble_por_codigo("AC-1")
            self.api.obtener_inmueble_por_codigo("AC-2")

        # Antes cada consulta por código descargaba el inventario completo.
        mock_post.assert_called_once()

    @override_settings(MOBILIA_CACHE_TTL=0)
    def test_ttl_cero_desactiva_la_cache(self):
        with patch.object(mobilia_mod.requests, "post", return_value=self._respuesta()) as mock_post:
            self.api.obtener_inmueble_por_codigo("AC-1")
            self.api.obtener_inmueble_por_codigo("AC-2")

        self.assertEqual(mock_post.call_count, 2)

    def test_codigo_inexistente_devuelve_marca_de_error(self):
        with patch.object(mobilia_mod.requests, "post", return_value=self._respuesta()):
            resultado = self.api.obtener_inmueble_por_codigo("NO-EXISTE")

        self.assertEqual(resultado["error"], "Inmueble no encontrado")

    def test_desactivar_invalida_la_cache(self):
        cache.set(CACHE_KEY_INMUEBLES, INVENTARIO, 120)

        with patch.object(mobilia_mod.requests, "get", return_value=self._respuesta()):
            self.api.desactivar_inmueble("AC-1", "SOLD", "Lo vendió")

        self.assertIsNone(cache.get(CACHE_KEY_INMUEBLES))

    def test_confirmar_disponibilidad_no_reconsulta_si_recibe_el_inmueble(self):
        with patch.object(mobilia_mod.requests, "get", return_value=self._respuesta()):
            with patch.object(mobilia_mod.requests, "post") as mock_post:
                self.api.confirmar_disponibilidad("AC-1", inmueble=INVENTARIO[0])

        mock_post.assert_not_called()
