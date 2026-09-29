"""
Estado durable de contacto y deduplicación del log de envíos.

Cubre la razón por la que se podía superar el máximo de 3 intentos: el conteo
vivía solo en memoria (in-process o Redis con TTL de 24h) y arrancaba en 0 en
cada ejecución.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from Automations.models import EnvioPlantillaLog
from Automations.Services.Contacto import (
    enriquecer_con_estado_durable,
    registrar_desactivacion,
    registrar_disponibilidad_confirmada,
    registrar_intento_contacto,
)
from Automations.Services.Plantillas import registrar_envio_plantilla
from Inmuebles.models import Inmuebles
from contacto_inmuebles import get_inmuebles_a_contactar, MemoriaTemporal


class EstadoDurableTests(TestCase):
    def test_inyecta_los_intentos_guardados(self):
        Inmuebles.objects.create(codigo="AC-1", intentos_contacto=2)

        [item] = enriquecer_con_estado_durable([{"codigo": "AC-1"}])

        self.assertEqual(item["intentos_realizados"], 2)

    def test_inmueble_sin_registro_arranca_en_cero(self):
        [item] = enriquecer_con_estado_durable([{"codigo": "NUEVO"}])

        self.assertEqual(item["intentos_realizados"], 0)

    def test_respeta_el_valor_que_trae_la_entrada(self):
        Inmuebles.objects.create(codigo="AC-1", intentos_contacto=2)

        [item] = enriquecer_con_estado_durable([{"codigo": "AC-1", "intentos_realizados": 1}])

        self.assertEqual(item["intentos_realizados"], 1)

    def test_respeta_el_alias_intentos(self):
        Inmuebles.objects.create(codigo="AC-1", intentos_contacto=2)

        [item] = enriquecer_con_estado_durable([{"codigo": "AC-1", "intentos": 0}])

        self.assertNotIn("intentos_realizados", item)
        self.assertEqual(item["intentos"], 0)

    def test_completa_las_fechas_desde_la_base(self):
        registro = Inmuebles.objects.create(codigo="AC-1")
        registrar_disponibilidad_confirmada("AC-1")
        registrar_intento_contacto("AC-1")
        registro.refresh_from_db()

        [item] = enriquecer_con_estado_durable([{"codigo": "AC-1"}])

        self.assertIn("lastMessageDate", item)
        self.assertIn("lastAvailability", item)

    def test_no_sobrescribe_las_fechas_de_la_entrada(self):
        registrar_intento_contacto("AC-1")

        [item] = enriquecer_con_estado_durable(
            [{"codigo": "AC-1", "lastMessageDate": "2026-01-01"}]
        )

        self.assertEqual(item["lastMessageDate"], "2026-01-01")

    def test_una_sola_consulta_para_todo_el_lote(self):
        Inmuebles.objects.bulk_create(
            [Inmuebles(codigo=f"AC-{i}", intentos_contacto=i) for i in range(5)]
        )

        with self.assertNumQueries(1):
            enriquecer_con_estado_durable([{"codigo": f"AC-{i}"} for i in range(5)])

    def test_ignora_items_que_no_son_objetos(self):
        resultado = enriquecer_con_estado_durable(["basura", {"codigo": "AC-1"}])

        self.assertEqual(resultado[0], "basura")
        self.assertEqual(resultado[1]["intentos_realizados"], 0)


class RegistrarIntentoTests(TestCase):
    def test_incrementa_y_sella_la_fecha(self):
        registro = registrar_intento_contacto("AC-1")

        self.assertEqual(registro.intentos_contacto, 1)
        self.assertIsNotNone(registro.fecha_ultimo_mensaje)

    def test_acumula_entre_ejecuciones(self):
        registrar_intento_contacto("AC-1")
        registrar_intento_contacto("AC-1")
        registro = registrar_intento_contacto("AC-1")

        self.assertEqual(registro.intentos_contacto, 3)

    def test_sin_codigo_no_hace_nada(self):
        self.assertIsNone(registrar_intento_contacto(None))
        self.assertEqual(Inmuebles.objects.count(), 0)

    def test_confirmar_disponibilidad_reinicia_el_ciclo(self):
        registrar_intento_contacto("AC-1")
        registrar_intento_contacto("AC-1")

        registro = registrar_disponibilidad_confirmada("AC-1")

        self.assertEqual(registro.intentos_contacto, 0)
        self.assertTrue(registro.estado)

    def test_desactivacion_marca_inactivo(self):
        registro = registrar_desactivacion("AC-1")

        self.assertFalse(registro.estado)
        self.assertIsNotNone(registro.fecha_desactivacion)


class TopeDeIntentosTests(TestCase):
    """El conteo durable debe cortar al cuarto intento aunque la memoria se pierda."""

    def _inmueble(self):
        return {
            "codigo": "AC-1",
            "conversation_id": "573001112233",
            "estado": True,
            "lastAvailability": "2020-01-01",
            "template_id": "T-1",
        }

    def _ronda(self, momento, envios):
        """Una ejecución completa del comando, con memoria en proceso nueva."""

        def enviar(item):
            envios.append(item["codigo"])
            registrar_intento_contacto(item["codigo"], cuando=momento)
            return {"exitoso": True}

        # Memoria nueva en cada ronda: simula procesos distintos, que es lo que
        # ocurre con el comando de management y con n8n.
        datos = enriquecer_con_estado_durable([self._inmueble()])
        get_inmuebles_a_contactar(
            datos, ahora=momento, memoria=MemoriaTemporal(), enviar_fn=enviar
        )

    def test_cinco_dias_seguidos_solo_producen_tres_envios(self):
        envios = []
        inicio = timezone.now() - timedelta(days=10)

        # Un día por ronda: se respeta el intervalo mínimo, así que lo único que
        # puede frenar el envío es el tope de 3 intentos.
        for dia in range(5):
            self._ronda(inicio + timedelta(days=dia), envios)

        self.assertEqual(len(envios), 3)
        self.assertEqual(Inmuebles.objects.get(codigo="AC-1").intentos_contacto, 3)

    def test_dos_ejecuciones_el_mismo_dia_solo_envian_una_vez(self):
        """El intervalo de 1 día se respeta aunque la memoria de proceso se pierda."""
        envios = []
        momento = timezone.now() - timedelta(days=10)

        self._ronda(momento, envios)
        self._ronda(momento, envios)

        self.assertEqual(len(envios), 1)


class DedupEnvioPlantillaTests(TestCase):
    def test_mismo_message_id_no_duplica(self):
        """El backend y el webhook de n8n registran el mismo envío."""
        for _ in range(2):
            registrar_envio_plantilla(
                conversation_id="conv-1",
                codigo_inmueble="AC-1",
                exitoso=True,
                message_id="msg-1",
                template_id="T-1",
                payload={"codigo": "AC-1"},
            )

        self.assertEqual(EnvioPlantillaLog.objects.count(), 1)

    def test_message_ids_distintos_son_envios_distintos(self):
        for message_id in ("msg-1", "msg-2"):
            registrar_envio_plantilla(
                conversation_id="conv-1",
                codigo_inmueble="AC-1",
                exitoso=True,
                message_id=message_id,
                template_id="T-1",
                payload={},
            )

        self.assertEqual(EnvioPlantillaLog.objects.count(), 2)

    def test_fallos_sin_message_id_se_registran_siempre(self):
        """Cada fallo es un evento real y no hay clave con la que deduplicar."""
        for _ in range(2):
            registrar_envio_plantilla(
                conversation_id="conv-1",
                codigo_inmueble="AC-1",
                exitoso=False,
                message_id=None,
                template_id="T-1",
                payload={},
            )

        self.assertEqual(EnvioPlantillaLog.objects.count(), 2)
        self.assertEqual(EnvioPlantillaLog.objects.filter(exitoso=False).count(), 2)
