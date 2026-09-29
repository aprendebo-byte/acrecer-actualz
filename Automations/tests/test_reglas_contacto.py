"""
Reglas de selección de inmuebles a contactar.

`contacto_inmuebles` es un módulo puro (sin Django ni red) y con `ahora`
inyectable, así que estas pruebas no necesitan base de datos.

Reglas cubiertas: solo activos · lastAvailability > 20 días · máximo 3 intentos ·
al menos 1 día entre intentos.
"""

from datetime import datetime, timezone

from django.test import SimpleTestCase

from contacto_inmuebles import (
    MemoriaTemporal,
    diagnosticar_inmuebles,
    get_inmuebles_a_contactar,
)

# Momento de referencia fijo para que las pruebas sean reproducibles.
AHORA = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def inmueble(**overrides):
    """Inmueble que cumple todas las reglas; los kwargs rompen una a una."""
    base = {
        "codigo": "AC-1000",
        "conversation_id": "573001112233",
        "waid": "573001112233",
        "estado": True,
        # 35 días antes de AHORA -> supera el mínimo de 20.
        "lastAvailability": "2026-04-29",
        "lastMessageDate": None,
        "nombre_propietario": "PROPIETARIO PRUEBA",
        "direccion_inmueble": "CL 80 52 A 90",
        "tipo_inmueble": "Apartamento",
        "valor": "2500000",
        "template_id": "T-1",
    }
    base.update(overrides)
    return base


class SeleccionInmueblesTests(SimpleTestCase):
    def seleccionar(self, inmuebles, **kwargs):
        # Memoria nueva por prueba: la instancia global del módulo es compartida
        # y contaminaría el resto de casos.
        kwargs.setdefault("memoria", MemoriaTemporal())
        kwargs.setdefault("ahora", AHORA)
        return get_inmuebles_a_contactar(inmuebles, **kwargs)

    def test_contacta_cuando_cumple_todas_las_reglas(self):
        resultado = self.seleccionar([inmueble()])

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["codigo"], "AC-1000")
        self.assertEqual(resultado[0]["intentos_realizados"], 1)

    def test_descarta_inmueble_inactivo(self):
        self.assertEqual(self.seleccionar([inmueble(estado=False)]), [])

    def test_descarta_disponibilidad_reciente(self):
        # 5 días: no alcanza el mínimo de 20.
        self.assertEqual(self.seleccionar([inmueble(lastAvailability="2026-05-29")]), [])

    def test_descarta_sin_fecha_de_disponibilidad(self):
        self.assertEqual(self.seleccionar([inmueble(lastAvailability=None)]), [])

    def test_descarta_al_alcanzar_el_maximo_de_intentos(self):
        self.assertEqual(self.seleccionar([inmueble(intentos_realizados=3)]), [])

    def test_contacta_en_el_tercer_intento(self):
        resultado = self.seleccionar([inmueble(intentos_realizados=2)])

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["intentos_realizados"], 3)

    def test_descarta_si_el_ultimo_mensaje_es_de_hoy(self):
        # 12 horas < el intervalo mínimo de 1 día.
        self.assertEqual(
            self.seleccionar([inmueble(lastMessageDate="2026-06-03", intentos_realizados=1)]),
            [],
        )

    def test_contacta_si_paso_mas_de_un_dia(self):
        resultado = self.seleccionar(
            [inmueble(lastMessageDate="2026-06-01", intentos_realizados=1)]
        )

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["intentos_realizados"], 2)

    def test_contacta_sin_fecha_de_ultimo_contacto(self):
        # Sin lastMessageDate (entrada) ni fecha_ultimo_intento (memoria): no
        # hay referencia de intervalo -> no debe descartarse por intervalo,
        # incluso si ya tuvo intentos previos.
        resultado = self.seleccionar(
            [inmueble(lastMessageDate=None, intentos_realizados=1)]
        )

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["intentos_realizados"], 2)

    def test_descarta_sin_conversation_id(self):
        sin_id = inmueble(conversation_id=None, waid=None)
        self.assertEqual(self.seleccionar([sin_id]), [])

    def test_acepta_alias_intentos(self):
        """El conteo también se lee de la clave `intentos`."""
        self.assertEqual(self.seleccionar([inmueble(intentos=3)]), [])

    def test_acepta_entrada_como_cadena_json(self):
        import json

        resultado = self.seleccionar(json.dumps([inmueble()]))
        self.assertEqual(len(resultado), 1)

    def test_entrada_invalida_devuelve_lista_vacia(self):
        self.assertEqual(self.seleccionar({"no": "es una lista"}), [])
        self.assertEqual(self.seleccionar("{no es json"), [])

    def test_un_item_invalido_no_aborta_el_lote(self):
        resultado = self.seleccionar(["no es un objeto", inmueble()])
        self.assertEqual(len(resultado), 1)

    def test_la_memoria_evita_recontactar_el_mismo_dia(self):
        memoria = MemoriaTemporal()
        datos = [inmueble()]

        primera = get_inmuebles_a_contactar(datos, ahora=AHORA, memoria=memoria)
        segunda = get_inmuebles_a_contactar(datos, ahora=AHORA, memoria=memoria)

        self.assertEqual(len(primera), 1)
        # El intento quedó registrado hace 0 días -> aún no corresponde otro.
        self.assertEqual(segunda, [])

    def test_registrar_intento_false_no_marca_la_memoria(self):
        memoria = MemoriaTemporal()
        datos = [inmueble()]

        get_inmuebles_a_contactar(datos, ahora=AHORA, memoria=memoria, registrar_intento=False)
        segunda = get_inmuebles_a_contactar(datos, ahora=AHORA, memoria=memoria)

        self.assertEqual(len(segunda), 1)


class EnvioInyectadoTests(SimpleTestCase):
    def test_envio_exitoso_registra_el_intento(self):
        memoria = MemoriaTemporal()
        llamadas = []

        def enviar(item):
            llamadas.append(item["codigo"])
            return {"exitoso": True}

        resultado = get_inmuebles_a_contactar(
            [inmueble()], ahora=AHORA, memoria=memoria, enviar_fn=enviar
        )

        self.assertEqual(llamadas, ["AC-1000"])
        self.assertTrue(resultado[0]["envio"]["exitoso"])
        self.assertEqual(memoria.get("573001112233")["intentos"], 1)

    def test_envio_fallido_no_consume_el_intento(self):
        """Un fallo debe poder reintentarse: no se marca la memoria."""
        memoria = MemoriaTemporal()

        resultado = get_inmuebles_a_contactar(
            [inmueble()], ahora=AHORA, memoria=memoria,
            enviar_fn=lambda item: {"exitoso": False, "error": "timeout"},
        )

        self.assertFalse(resultado[0]["envio"]["exitoso"])
        self.assertIsNone(memoria.get("573001112233"))

    def test_excepcion_en_el_envio_no_aborta_el_lote(self):
        def enviar(item):
            raise RuntimeError("el API se cayó")

        resultado = get_inmuebles_a_contactar(
            [inmueble()], ahora=AHORA, memoria=MemoriaTemporal(), enviar_fn=enviar
        )

        self.assertEqual(len(resultado), 1)
        self.assertFalse(resultado[0]["envio"]["exitoso"])
        self.assertIn("el API se cayó", resultado[0]["envio"]["error"])


class DiagnosticoTests(SimpleTestCase):
    def test_resume_el_motivo_de_cada_descarte(self):
        datos = [
            inmueble(codigo="OK-1"),
            inmueble(codigo="INACTIVO", estado=False),
            inmueble(codigo="RECIENTE", lastAvailability="2026-05-29"),
            inmueble(codigo="MAX", intentos_realizados=3),
            inmueble(codigo="INTERVALO", lastMessageDate="2026-06-03"),
            inmueble(codigo="SIN-ID", conversation_id=None, waid=None),
        ]

        resultado = diagnosticar_inmuebles(datos, ahora=AHORA)
        motivos = {fila["codigo"]: fila["motivo"] for fila in resultado["detalle"]}

        self.assertEqual(motivos["OK-1"], "CONTACTAR")
        self.assertEqual(motivos["INACTIVO"], "inactivo")
        self.assertTrue(motivos["RECIENTE"].startswith("disponibilidad_<=20d"))
        self.assertTrue(motivos["MAX"].startswith("max_intentos"))
        self.assertTrue(motivos["INTERVALO"].startswith("intervalo_<1d"))
        self.assertEqual(motivos["SIN-ID"], "sin_conversation_id")
        self.assertEqual(resultado["resumen"]["CONTACTAR"], 1)

    def test_no_tiene_efectos_secundarios(self):
        """El diagnóstico no debe consumir intentos."""
        memoria = MemoriaTemporal()
        diagnosticar_inmuebles([inmueble()], ahora=AHORA)

        self.assertEqual(len(get_inmuebles_a_contactar([inmueble()], ahora=AHORA, memoria=memoria)), 1)
