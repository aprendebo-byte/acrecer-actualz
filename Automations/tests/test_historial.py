"""
Pruebas del historial y del informe.

El caso central es la regresión de `guardar_historial_inmueble`: al llegar el
motivo de una desactivación se completaba "el último registro sin motivo", que
podía ser una confirmación de disponibilidad. Eso corrompía ese registro y hacía
desaparecer el evento de desactivación.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from Automations.models import HistorialRespuestas
from Automations.Services.Utils import (
    RESPUESTA_DESACTIVADO,
    RESPUESTA_DISPONIBLE,
    guardar_historial_inmueble,
    valor_inmueble,
)

PAYLOAD = {
    "propertyCode": "AC-1000",
    "serviceType": "forRent",
    "propertyTypeName": "Apartamento",
    "rentValue": 2500000,
    "address": "CL 80 52 A 90",
    "contactName": "PROPIETARIO",
    "contactPhone": "573001112233",
}


class GuardarHistorialTests(TestCase):
    def test_crea_registro_de_disponibilidad(self):
        guardar_historial_inmueble(PAYLOAD, RESPUESTA_DISPONIBLE)

        registro = HistorialRespuestas.objects.get()
        self.assertEqual(registro.respuesta, RESPUESTA_DISPONIBLE)
        self.assertIsNone(registro.razon_desactivacion)

    def test_completa_una_desactivacion_pendiente_de_motivo(self):
        guardar_historial_inmueble(PAYLOAD, RESPUESTA_DESACTIVADO, razon_desactivacion=None)

        guardar_historial_inmueble(PAYLOAD, RESPUESTA_DESACTIVADO, razon_desactivacion="Lo vendió")

        # Se completó el registro existente, no se creó otro.
        self.assertEqual(HistorialRespuestas.objects.count(), 1)
        self.assertEqual(HistorialRespuestas.objects.get().razon_desactivacion, "Lo vendió")

    def test_no_sobrescribe_una_confirmacion_de_disponibilidad(self):
        """Regresión: la confirmación previa no debe convertirse en desactivación."""
        guardar_historial_inmueble(PAYLOAD, RESPUESTA_DISPONIBLE)

        guardar_historial_inmueble(PAYLOAD, RESPUESTA_DESACTIVADO, razon_desactivacion="Lo vendió")

        self.assertEqual(HistorialRespuestas.objects.count(), 2)

        disponible = HistorialRespuestas.objects.get(respuesta=RESPUESTA_DISPONIBLE)
        self.assertIsNone(disponible.razon_desactivacion)

        desactivado = HistorialRespuestas.objects.get(respuesta=RESPUESTA_DESACTIVADO)
        self.assertEqual(desactivado.razon_desactivacion, "Lo vendió")

    def test_no_completa_una_desactivacion_antigua(self):
        """Una desactivación de hace meses es otro evento: se crea uno nuevo."""
        antiguo = HistorialRespuestas.objects.create(
            codigo_inmueble="AC-1000",
            respuesta=RESPUESTA_DESACTIVADO,
            razon_desactivacion=None,
        )
        # fecha_registro es auto_now_add: se fuerza con update para simular el pasado.
        HistorialRespuestas.objects.filter(pk=antiguo.pk).update(
            fecha_registro=timezone.now() - timedelta(days=40)
        )

        guardar_historial_inmueble(PAYLOAD, RESPUESTA_DESACTIVADO, razon_desactivacion="Otro motivo")

        self.assertEqual(HistorialRespuestas.objects.count(), 2)
        antiguo.refresh_from_db()
        self.assertIsNone(antiguo.razon_desactivacion)

    def test_no_toca_registros_de_otro_inmueble(self):
        guardar_historial_inmueble(
            {**PAYLOAD, "propertyCode": "AC-2000"}, RESPUESTA_DESACTIVADO
        )

        guardar_historial_inmueble(PAYLOAD, RESPUESTA_DESACTIVADO, razon_desactivacion="Lo vendió")

        otro = HistorialRespuestas.objects.get(codigo_inmueble="AC-2000")
        self.assertIsNone(otro.razon_desactivacion)


class ValorInmuebleTests(TestCase):
    def test_arriendo_usa_el_canon(self):
        self.assertEqual(valor_inmueble({"serviceType": "forRent", "rentValue": 100}), 100)

    def test_venta_usa_el_precio_de_venta(self):
        """Antes se guardaba rentValue siempre y los inmuebles en venta quedaban nulos."""
        payload = {"serviceType": "forSale", "saleValue": 500000000, "rentValue": None}
        self.assertEqual(valor_inmueble(payload), 500000000)

    def test_sin_valor_de_venta_cae_al_canon(self):
        payload = {"serviceType": "forSale", "saleValue": None, "rentValue": 100}
        self.assertEqual(valor_inmueble(payload), 100)

    def test_historial_de_venta_guarda_el_precio_de_venta(self):
        guardar_historial_inmueble(
            {"propertyCode": "AC-3000", "serviceType": "forSale", "saleValue": 400000000},
            RESPUESTA_DISPONIBLE,
        )

        self.assertEqual(HistorialRespuestas.objects.get().valor, 400000000)
