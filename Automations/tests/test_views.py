"""
Pruebas de las tres vistas de `Automations`.

Mobilia y el envío de correo se sustituyen por dobles: las pruebas no deben
salir a la red ni mandar correos reales.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from Automations.models import HistorialRespuestas, HistorialIntervenciones
from Inmuebles.models import Inmuebles

INMUEBLE_MOBILIA = {
    "propertyCode": "AC-75360",
    "serviceType": "forRent",
    "propertyTypeName": "Apartamento",
    "rentValue": 2500000,
    "address": "CL 80 52 A 90 LC 101",
    "cityName": "Bogotá",
    "condominiumName": "Conjunto Alto",
    "contactName": "PROPIETARIO PRUEBA",
    "contactPhone": "573001112233",
    "consigner": "Carlos Captador",
    "branchName": "Chapinero",
    "externalCodesJSON": [],
}

NO_ENCONTRADO = {"propertyCode": "NO-EXISTE", "error": "Inmueble no encontrado"}


class UpdateStatusTests(TestCase):
    url = None

    def setUp(self):
        self.url = reverse("update-status")

    @patch("Automations.views.notificacion_inmueble_desactivado")
    @patch("Automations.views.MobiliaAPI")
    def test_confirma_disponibilidad(self, mock_mobilia, mock_correo):
        api = mock_mobilia.return_value
        api.obtener_inmueble_por_codigo.return_value = INMUEBLE_MOBILIA

        respuesta = self.client.post(
            self.url, {"inmueble_id": "AC-75360", "status": True}, format="json"
        )

        self.assertEqual(respuesta.status_code, 200)
        historial = HistorialRespuestas.objects.get()
        self.assertEqual(historial.respuesta, "Sí, continua disponible")
        self.assertIsNone(historial.razon_desactivacion)
        # No es una desactivación: no se notifica por correo.
        mock_correo.assert_not_called()

    @patch("Automations.views.MobiliaAPI")
    def test_confirmar_disponibilidad_reutiliza_el_inmueble_consultado(self, mock_mobilia):
        """No debe volver a descargar el inventario completo de Mobilia."""
        api = mock_mobilia.return_value
        api.obtener_inmueble_por_codigo.return_value = INMUEBLE_MOBILIA

        self.client.post(self.url, {"inmueble_id": "AC-75360", "status": True}, format="json")

        api.obtener_inmueble_por_codigo.assert_called_once_with("AC-75360")
        api.confirmar_disponibilidad.assert_called_once_with(
            "AC-75360", inmueble=INMUEBLE_MOBILIA
        )

    @patch("Automations.views.MobiliaAPI")
    def test_confirmar_disponibilidad_sella_el_estado_durable(self, mock_mobilia):
        mock_mobilia.return_value.obtener_inmueble_por_codigo.return_value = INMUEBLE_MOBILIA
        Inmuebles.objects.create(codigo="AC-75360", intentos_contacto=2)

        self.client.post(self.url, {"inmueble_id": "AC-75360", "status": True}, format="json")

        registro = Inmuebles.objects.get(codigo="AC-75360")
        self.assertTrue(registro.estado)
        self.assertIsNotNone(registro.fecha_disponibilidad_confirmada)
        # Confirmada la disponibilidad, el ciclo de contacto vuelve a empezar.
        self.assertEqual(registro.intentos_contacto, 0)

    @patch("Automations.views.notificacion_inmueble_desactivado")
    @patch("Automations.views.MobiliaAPI")
    def test_desactiva_con_motivo(self, mock_mobilia, mock_correo):
        api = mock_mobilia.return_value
        api.obtener_inmueble_por_codigo.return_value = INMUEBLE_MOBILIA

        respuesta = self.client.post(
            self.url,
            {"inmueble_id": "AC-75360", "status": False, "deactivation_reason": "Lo vendió"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 200)
        historial = HistorialRespuestas.objects.get()
        self.assertEqual(historial.respuesta, "No, ha sido desactivado")
        self.assertEqual(historial.razon_desactivacion, "Lo vendió")
        api.desactivar_inmueble.assert_called_once()
        mock_correo.assert_called_once()
        self.assertFalse(Inmuebles.objects.get(codigo="AC-75360").estado)

    @patch("Automations.views.notificacion_inmueble_desactivado")
    @patch("Automations.views.MobiliaAPI")
    def test_codigo_de_motivo_separado_del_texto(self, mock_mobilia, _mock_correo):
        api = mock_mobilia.return_value
        api.obtener_inmueble_por_codigo.return_value = INMUEBLE_MOBILIA

        self.client.post(
            self.url,
            {
                "inmueble_id": "AC-75360",
                "status": False,
                "deactivation_reason": "El propietario lo vendió",
                "deactivation_reason_code": "SOLD",
            },
            format="json",
        )

        _args, kwargs = api.desactivar_inmueble.call_args
        self.assertEqual(kwargs["reason_code"], "SOLD")
        self.assertEqual(kwargs["reason_text"], "El propietario lo vendió")

    @patch("Automations.views.notificacion_inmueble_desactivado")
    @patch("Automations.views.MobiliaAPI")
    def test_inmueble_inexistente_devuelve_404_sin_efectos(self, mock_mobilia, mock_correo):
        """Antes respondía 200, guardaba historial nulo y enviaba el correo."""
        api = mock_mobilia.return_value
        api.obtener_inmueble_por_codigo.return_value = NO_ENCONTRADO

        respuesta = self.client.post(
            self.url,
            {"inmueble_id": "NO-EXISTE", "status": False, "deactivation_reason": "X"},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(HistorialRespuestas.objects.count(), 0)
        api.desactivar_inmueble.assert_not_called()
        api.confirmar_disponibilidad.assert_not_called()
        mock_correo.assert_not_called()

    @patch("Automations.views.MobiliaAPI")
    def test_no_exige_motivo_al_confirmar_disponibilidad(self, mock_mobilia):
        mock_mobilia.return_value.obtener_inmueble_por_codigo.return_value = INMUEBLE_MOBILIA

        respuesta = self.client.post(
            self.url, {"inmueble_id": "AC-75360", "status": True}, format="json"
        )

        self.assertEqual(respuesta.status_code, 200)

    @patch("Automations.views.MobiliaAPI")
    def test_exige_motivo_al_desactivar(self, mock_mobilia):
        mock_mobilia.return_value.obtener_inmueble_por_codigo.return_value = INMUEBLE_MOBILIA

        respuesta = self.client.post(
            self.url, {"inmueble_id": "AC-75360", "status": False}, format="json"
        )

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("deactivation_reason", respuesta.json()["error"])

    def test_campos_obligatorios(self):
        respuesta = self.client.post(self.url, {"status": True}, format="json")

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("inmueble_id", respuesta.json()["error"])

    @patch("Automations.views.MobiliaAPI")
    def test_status_no_interpretable(self, mock_mobilia):
        respuesta = self.client.post(
            self.url,
            {"inmueble_id": "AC-75360", "status": {"raro": True}},
            format="json",
        )

        self.assertEqual(respuesta.status_code, 400)
        mock_mobilia.return_value.obtener_inmueble_por_codigo.assert_not_called()


class UpdatePriceTests(TestCase):
    def setUp(self):
        self.url = reverse("update-price")

    @patch("Automations.views.notificacion_actualizacion_valor")
    @patch("Automations.views.MobiliaAPI")
    def test_registra_la_solicitud_sin_afirmar_que_actualizo(self, mock_mobilia, mock_correo):
        mock_mobilia.return_value.obtener_inmueble_por_codigo.return_value = INMUEBLE_MOBILIA

        respuesta = self.client.post(
            self.url, {"inmueble_id": "AC-75360", "new_price": 2700000}, format="json"
        )

        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.json()
        # La respuesta ya no dice "price updated successfully": no se escribe en Mobilia.
        self.assertFalse(cuerpo["actualizado_en_mobilia"])
        mock_correo.assert_called_once()

    @patch("Automations.views.notificacion_actualizacion_valor")
    @patch("Automations.views.MobiliaAPI")
    def test_inmueble_inexistente_devuelve_404(self, mock_mobilia, mock_correo):
        mock_mobilia.return_value.obtener_inmueble_por_codigo.return_value = NO_ENCONTRADO

        respuesta = self.client.post(
            self.url, {"inmueble_id": "NO-EXISTE", "new_price": 100}, format="json"
        )

        self.assertEqual(respuesta.status_code, 404)
        mock_correo.assert_not_called()

    def test_campos_obligatorios(self):
        respuesta = self.client.post(self.url, {"inmueble_id": "AC-1"}, format="json")

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("new_price", respuesta.json()["error"])


class InterventionTests(TestCase):
    def setUp(self):
        self.url = reverse("record-intervention")

    @patch("Automations.views.notificar_intervencion_realizada")
    @patch("Automations.views.MobiliaAPI")
    def test_registra_la_intervencion(self, mock_mobilia, mock_correo):
        mock_mobilia.return_value.obtener_inmueble_por_codigo.return_value = INMUEBLE_MOBILIA

        respuesta = self.client.post(
            self.url,
            {
                "inmueble_id": "AC-75360",
                "intervention_type": "Llamada del propietario",
                "resumen": "Pidió revisar el canon",
            },
            format="json",
        )

        self.assertEqual(respuesta.status_code, 200)
        intervencion = HistorialIntervenciones.objects.get()
        self.assertEqual(intervencion.tipo_intervencion, "Llamada del propietario")
        self.assertEqual(intervencion.captador, "Carlos Captador - Chapinero")

        _args, kwargs = mock_correo.call_args
        self.assertEqual(kwargs["sucursal"], "Chapinero")
        self.assertEqual(kwargs["resumen"], "Pidió revisar el canon")

    @patch("Automations.views.notificar_intervencion_realizada")
    @patch("Automations.views.MobiliaAPI")
    def test_inmueble_inexistente_devuelve_404(self, mock_mobilia, mock_correo):
        mock_mobilia.return_value.obtener_inmueble_por_codigo.return_value = NO_ENCONTRADO

        respuesta = self.client.post(
            self.url, {"inmueble_id": "NO-EXISTE", "intervention_type": "X"}, format="json"
        )

        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(HistorialIntervenciones.objects.count(), 0)
        mock_correo.assert_not_called()
