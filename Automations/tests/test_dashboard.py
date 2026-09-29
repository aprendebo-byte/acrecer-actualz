"""
Endpoint de métricas (`/api/automations/dashboard/`).

Cada aserción comprueba un número calculado a mano sobre datos fijos: un agregado
mal escrito devuelve otro número, no un error, así que el único test útil es el
que conoce la respuesta de antemano.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from Automations.models import (
    EnvioPlantillaLog,
    HistorialActualizacionesPrecios,
    HistorialIntervenciones,
    HistorialRespuestas,
    RegistroLog,
)
from Automations.Services.Utils import RESPUESTA_DESACTIVADO, RESPUESTA_DISPONIBLE
from contacto_inmuebles import DIAS_MIN_DISPONIBILIDAD, MAX_INTENTOS
from Inmuebles.models import Inmuebles

TOKEN = "token-de-prueba"


def _fechar(modelo, pk, cuando):
    """`fecha_registro` es auto_now_add: solo se puede mover con update()."""
    modelo.objects.filter(pk=pk).update(fecha_registro=cuando)


class _DashboardTestCase(TestCase):
    def pedir(self, params=None):
        with self.settings(API_INFORMES_TOKEN=TOKEN):
            return self.client.get(
                reverse("dashboard"), params or {}, HTTP_X_API_TOKEN=TOKEN
            )

    def cuerpo(self, params=None):
        respuesta = self.pedir(params)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()


class DashboardAccesoTests(_DashboardTestCase):
    def test_exige_token(self):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)

    def test_sin_datos_responde_todas_las_secciones(self):
        """Un dashboard vacío no debe reventar por divisiones entre cero."""
        cuerpo = self.cuerpo()

        for seccion in (
            "resumen",
            "serie",
            "desactivaciones",
            "precios",
            "intervenciones",
            "distribucion",
            "contacto",
            "actividad",
            "logs",
        ):
            self.assertIn(seccion, cuerpo)

        self.assertEqual(cuerpo["serie"], [])
        # Sin base de cálculo la tasa es None, no 0: no se ha medido nada.
        self.assertIsNone(cuerpo["resumen"]["respuestas"]["tasa_desactivacion_pct"])

    def test_seccion_unica(self):
        cuerpo = self.cuerpo({"secciones": "resumen"})

        self.assertIn("resumen", cuerpo)
        self.assertNotIn("precios", cuerpo)

    def test_varias_secciones(self):
        cuerpo = self.cuerpo({"secciones": "precios,resumen"})

        self.assertIn("resumen", cuerpo)
        self.assertIn("precios", cuerpo)
        self.assertNotIn("logs", cuerpo)

    def test_seccion_desconocida_es_400(self):
        respuesta = self.pedir({"secciones": "ventas"})

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["secciones_desconocidas"], ["ventas"])

    def test_parametro_desconocido_es_400(self):
        self.assertEqual(self.pedir({"ciudad": "Bogotá"}).status_code, 400)

    def test_granularidad_invalida_es_400(self):
        respuesta = self.pedir({"granularidad": "trimestre"})

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("granularidad", respuesta.json())

    def test_top_por_encima_del_maximo_es_400(self):
        respuesta = self.pedir({"top": "500"})

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("top", respuesta.json())

    def test_rango_invertido_es_400(self):
        respuesta = self.pedir({"desde": "2026-06-01", "hasta": "2026-05-01"})

        self.assertEqual(respuesta.status_code, 400)

    def test_la_ventana_por_defecto_es_informe_dias(self):
        with self.settings(API_INFORMES_TOKEN=TOKEN, INFORME_DIAS=30):
            cuerpo = self.client.get(
                reverse("dashboard"), HTTP_X_API_TOKEN=TOKEN
            ).json()

        self.assertTrue(cuerpo["ventana"]["acotada"])

    def test_informe_dias_cero_agrega_todo_el_historico(self):
        with self.settings(API_INFORMES_TOKEN=TOKEN, INFORME_DIAS=0):
            cuerpo = self.client.get(
                reverse("dashboard"), HTTP_X_API_TOKEN=TOKEN
            ).json()

        self.assertFalse(cuerpo["ventana"]["acotada"])
        self.assertIsNone(cuerpo["ventana"]["desde"])


class ResumenTests(_DashboardTestCase):
    def setUp(self):
        for codigo in ("AC-1", "AC-2", "AC-3"):
            HistorialRespuestas.objects.create(
                codigo_inmueble=codigo, respuesta=RESPUESTA_DISPONIBLE
            )
        HistorialRespuestas.objects.create(
            codigo_inmueble="AC-4", respuesta=RESPUESTA_DESACTIVADO
        )
        HistorialActualizacionesPrecios.objects.create(
            codigo_inmueble="AC-1", valor_anterior=1000, valor_nuevo=1100
        )
        HistorialIntervenciones.objects.create(
            codigo_inmueble="AC-1", tipo_intervencion="Fotos"
        )
        EnvioPlantillaLog.objects.create(conversation_id="c1", exitoso=True)
        EnvioPlantillaLog.objects.create(conversation_id="c2", exitoso=True)
        EnvioPlantillaLog.objects.create(conversation_id="c3", exitoso=False)
        RegistroLog.objects.create(nivel="INFO", mensaje="ok")
        RegistroLog.objects.create(nivel="ERROR", mensaje="falló")

    def test_contadores_de_respuestas(self):
        resumen = self.cuerpo({"secciones": "resumen"})["resumen"]["respuestas"]

        self.assertEqual(resumen["total"], 4)
        self.assertEqual(resumen["disponibles"], 3)
        self.assertEqual(resumen["desactivados"], 1)
        self.assertEqual(resumen["inmuebles_distintos"], 4)
        self.assertEqual(resumen["tasa_desactivacion_pct"], 25.0)

    def test_tasa_de_exito_de_envios(self):
        envios = self.cuerpo({"secciones": "resumen"})["resumen"]["envios_plantilla"]

        self.assertEqual(envios["total"], 3)
        self.assertEqual(envios["exitosos"], 2)
        self.assertEqual(envios["fallidos"], 1)
        self.assertEqual(envios["tasa_exito_pct"], 66.67)

    def test_contadores_de_logs(self):
        logs = self.cuerpo({"secciones": "resumen"})["resumen"]["logs"]

        self.assertEqual(logs["total"], 2)
        self.assertEqual(logs["errores"], 1)
        self.assertEqual(logs["tasa_error_pct"], 50.0)

    def test_la_ventana_excluye_lo_antiguo(self):
        viejo = HistorialRespuestas.objects.create(
            codigo_inmueble="ANTIGUO", respuesta=RESPUESTA_DISPONIBLE
        )
        _fechar(HistorialRespuestas, viejo.pk, timezone.now() - timedelta(days=90))

        resumen = self.cuerpo({"secciones": "resumen", "dias": "7"})["resumen"]

        self.assertEqual(resumen["respuestas"]["total"], 4)


class SerieTests(_DashboardTestCase):
    def setUp(self):
        hoy = HistorialRespuestas.objects.create(
            codigo_inmueble="HOY", respuesta=RESPUESTA_DISPONIBLE
        )
        self.hoy = timezone.localtime(hoy.fecha_registro).date().isoformat()

        antes = HistorialRespuestas.objects.create(
            codigo_inmueble="ANTES", respuesta=RESPUESTA_DESACTIVADO
        )
        hace_cinco = timezone.now() - timedelta(days=5)
        _fechar(HistorialRespuestas, antes.pk, hace_cinco)
        self.hace_cinco = timezone.localtime(hace_cinco).date().isoformat()

    def test_un_punto_por_dia_con_actividad(self):
        serie = self.cuerpo({"secciones": "serie"})["serie"]

        self.assertEqual([p["periodo"] for p in serie], [self.hace_cinco, self.hoy])
        self.assertEqual(serie[0]["desactivados"], 1)
        self.assertEqual(serie[0]["disponibles"], 0)
        self.assertEqual(serie[1]["disponibles"], 1)

    def test_granularidad_mes_agrupa_los_dos_registros(self):
        """Ambos caen en el mismo mes salvo que el test corra a inicio de mes."""
        serie = self.cuerpo({"secciones": "serie", "granularidad": "mes"})["serie"]

        self.assertEqual(sum(p["disponibles"] + p["desactivados"] for p in serie), 2)
        self.assertLessEqual(len(serie), 2)

    def test_la_serie_incluye_todos_los_tipos_de_novedad(self):
        HistorialActualizacionesPrecios.objects.create(codigo_inmueble="HOY")
        HistorialIntervenciones.objects.create(codigo_inmueble="HOY")
        EnvioPlantillaLog.objects.create(conversation_id="c1", exitoso=True)

        [punto] = [
            p for p in self.cuerpo({"secciones": "serie"})["serie"]
            if p["periodo"] == self.hoy
        ]

        self.assertEqual(punto["solicitudes_precio"], 1)
        self.assertEqual(punto["intervenciones"], 1)
        self.assertEqual(punto["envios_exitosos"], 1)


class DesactivacionesTests(_DashboardTestCase):
    def setUp(self):
        for _ in range(3):
            HistorialRespuestas.objects.create(
                codigo_inmueble="AC-1",
                respuesta=RESPUESTA_DESACTIVADO,
                razon_desactivacion="Lo vendió",
                ciudad="Bogotá",
                tipo_inmueble="Apartamento",
            )
        HistorialRespuestas.objects.create(
            codigo_inmueble="AC-2",
            respuesta=RESPUESTA_DESACTIVADO,
            razon_desactivacion="Lo arrendó solo",
            ciudad="Medellín",
        )
        # Desactivación cuya conversación se cortó antes de preguntar el motivo.
        HistorialRespuestas.objects.create(
            codigo_inmueble="AC-3", respuesta=RESPUESTA_DESACTIVADO
        )
        # No debe contarse: sigue disponible.
        HistorialRespuestas.objects.create(
            codigo_inmueble="AC-4", respuesta=RESPUESTA_DISPONIBLE
        )

    def test_ranking_de_motivos_con_porcentaje(self):
        seccion = self.cuerpo({"secciones": "desactivaciones"})["desactivaciones"]

        self.assertEqual(seccion["total"], 5)
        self.assertEqual(seccion["sin_motivo"], 1)
        self.assertEqual(
            seccion["motivos"][0], {"motivo": "Lo vendió", "total": 3, "porcentaje": 60.0}
        )

    def test_ranking_por_ciudad(self):
        seccion = self.cuerpo({"secciones": "desactivaciones"})["desactivaciones"]

        self.assertEqual(
            seccion["por_ciudad"][0], {"ciudad": "Bogotá", "total": 3}
        )

    def test_top_recorta_el_ranking(self):
        seccion = self.cuerpo({"secciones": "desactivaciones", "top": "1"})[
            "desactivaciones"
        ]

        self.assertEqual(len(seccion["motivos"]), 1)


class PreciosTests(_DashboardTestCase):
    def setUp(self):
        HistorialActualizacionesPrecios.objects.create(
            codigo_inmueble="SUBE", valor_anterior=1000000, valor_nuevo=1200000
        )
        HistorialActualizacionesPrecios.objects.create(
            codigo_inmueble="BAJA", valor_anterior=2000000, valor_nuevo=1800000
        )
        HistorialActualizacionesPrecios.objects.create(
            codigo_inmueble="IGUAL", valor_anterior=1000000, valor_nuevo=1000000
        )
        # Sin valor anterior: no es comparable y no debe entrar en los promedios.
        HistorialActualizacionesPrecios.objects.create(
            codigo_inmueble="SIN-BASE", valor_nuevo=900000
        )

    def test_clasifica_subidas_bajadas_y_sin_cambio(self):
        seccion = self.cuerpo({"secciones": "precios"})["precios"]

        self.assertEqual(seccion["solicitudes"], 4)
        self.assertEqual(seccion["comparables"], 3)
        self.assertEqual(seccion["subidas"], 1)
        self.assertEqual(seccion["bajadas"], 1)
        self.assertEqual(seccion["sin_cambio"], 1)

    def test_magnitud_de_la_variacion(self):
        seccion = self.cuerpo({"secciones": "precios"})["precios"]

        # (+200000 - 200000 + 0) / 3 = 0
        self.assertEqual(seccion["variacion_promedio"], 0.0)
        self.assertEqual(seccion["variacion_total"], 0.0)
        # (+20 % - 10 % + 0 %) / 3 = 3.33 %
        self.assertEqual(seccion["variacion_pct_promedio"], 3.33)

    def test_mayores_subidas_y_bajadas(self):
        seccion = self.cuerpo({"secciones": "precios"})["precios"]

        self.assertEqual(seccion["mayores_subidas"][0]["codigo_inmueble"], "SUBE")
        self.assertEqual(seccion["mayores_subidas"][0]["variacion"], 200000.0)
        self.assertEqual(seccion["mayores_subidas"][0]["variacion_pct"], 20.0)
        self.assertEqual(seccion["mayores_bajadas"][0]["codigo_inmueble"], "BAJA")
        self.assertEqual(seccion["mayores_bajadas"][0]["variacion_pct"], -10.0)

    def test_cada_ranking_solo_lleva_su_signo(self):
        """Con menos de `top` subidas, el hueco no se rellena con bajadas."""
        seccion = self.cuerpo({"secciones": "precios"})["precios"]

        self.assertEqual(
            [f["codigo_inmueble"] for f in seccion["mayores_subidas"]], ["SUBE"]
        )
        self.assertEqual(
            [f["codigo_inmueble"] for f in seccion["mayores_bajadas"]], ["BAJA"]
        )

    def test_valor_anterior_en_cero_no_divide_por_cero(self):
        """Postgres lanzaría division_by_zero y la vista devolvería 500."""
        HistorialActualizacionesPrecios.objects.create(
            codigo_inmueble="CERO", valor_anterior=0, valor_nuevo=500000
        )

        seccion = self.cuerpo({"secciones": "precios"})["precios"]

        self.assertEqual(seccion["comparables"], 4)
        # Entra en los conteos pero no en la variación porcentual.
        self.assertEqual(seccion["subidas"], 2)
        self.assertEqual(seccion["variacion_pct_promedio"], 3.33)


class IntervencionesTests(_DashboardTestCase):
    def setUp(self):
        HistorialIntervenciones.objects.create(
            codigo_inmueble="AC-1",
            tipo_intervencion="Cambio de fotos",
            captador="PEDRO - Norte",
        )
        HistorialIntervenciones.objects.create(
            codigo_inmueble="AC-2",
            tipo_intervencion="Cambio de fotos",
            captador="PEDRO - Norte",
        )
        HistorialIntervenciones.objects.create(
            codigo_inmueble="AC-3", tipo_intervencion="Visita", captador="MARIA - Sur"
        )

    def test_ranking_por_tipo_y_captador(self):
        seccion = self.cuerpo({"secciones": "intervenciones"})["intervenciones"]

        self.assertEqual(seccion["total"], 3)
        self.assertEqual(seccion["inmuebles_distintos"], 3)
        self.assertEqual(
            seccion["por_tipo"][0], {"tipo": "Cambio de fotos", "total": 2}
        )
        self.assertEqual(
            seccion["por_captador"][0], {"captador": "PEDRO - Norte", "total": 2}
        )


class DistribucionTests(_DashboardTestCase):
    def setUp(self):
        HistorialRespuestas.objects.create(
            codigo_inmueble="AC-1",
            respuesta=RESPUESTA_DISPONIBLE,
            ciudad="Bogotá",
            tipo_inmueble="Apartamento",
            tipo_servicio="forRent",
            valor=2000000,
        )
        HistorialRespuestas.objects.create(
            codigo_inmueble="AC-2",
            respuesta=RESPUESTA_DESACTIVADO,
            ciudad="Bogotá",
            tipo_inmueble="Apartamento",
            tipo_servicio="forRent",
            valor=3000000,
        )
        HistorialRespuestas.objects.create(
            codigo_inmueble="AC-3",
            respuesta=RESPUESTA_DISPONIBLE,
            ciudad="Medellín",
            tipo_inmueble="Casa",
            tipo_servicio="forSale",
            valor=500000000,
        )

    def test_desglose_por_ciudad_con_tasa_y_valor_promedio(self):
        [bogota, medellin] = self.cuerpo({"secciones": "distribucion"})[
            "distribucion"
        ]["por_ciudad"]

        self.assertEqual(bogota["ciudad"], "Bogotá")
        self.assertEqual(bogota["total"], 2)
        self.assertEqual(bogota["desactivados"], 1)
        self.assertEqual(bogota["tasa_desactivacion_pct"], 50.0)
        self.assertEqual(bogota["valor_promedio"], 2500000.0)
        self.assertEqual(medellin["tasa_desactivacion_pct"], 0.0)

    def test_desglose_por_tipo_de_servicio(self):
        servicios = self.cuerpo({"secciones": "distribucion"})["distribucion"][
            "por_tipo_servicio"
        ]

        self.assertEqual(
            {s["tipo_servicio"]: s["total"] for s in servicios},
            {"forRent": 2, "forSale": 1},
        )


class ContactoTests(_DashboardTestCase):
    def setUp(self):
        ahora = timezone.now()
        # Elegible: activo, sin agotar intentos y disponibilidad vencida.
        Inmuebles.objects.create(
            codigo="ELEGIBLE",
            intentos_contacto=1,
            fecha_disponibilidad_confirmada=ahora
            - timedelta(days=DIAS_MIN_DISPONIBILIDAD + 5),
        )
        # Confirmado hace poco: todavía no toca.
        Inmuebles.objects.create(
            codigo="RECIENTE",
            intentos_contacto=0,
            fecha_disponibilidad_confirmada=ahora - timedelta(days=2),
        )
        # Intentos agotados.
        Inmuebles.objects.create(
            codigo="AGOTADO",
            intentos_contacto=MAX_INTENTOS,
            fecha_disponibilidad_confirmada=ahora - timedelta(days=60),
        )
        # Inactivo: fuera del embudo.
        Inmuebles.objects.create(
            codigo="INACTIVO",
            estado=False,
            fecha_desactivacion=ahora - timedelta(days=1),
        )

    def test_embudo(self):
        seccion = self.cuerpo({"secciones": "contacto"})["contacto"]

        self.assertEqual(seccion["tipo"], "snapshot")
        self.assertEqual(seccion["total"], 4)
        self.assertEqual(seccion["activos"], 3)
        self.assertEqual(seccion["inactivos"], 1)
        self.assertEqual(seccion["sin_contactar"], 1)
        self.assertEqual(seccion["intentos_agotados"], 1)
        self.assertEqual(seccion["candidatos_a_contacto"], 1)

    def test_publica_las_reglas_que_aplica(self):
        reglas = self.cuerpo({"secciones": "contacto"})["contacto"]["reglas"]

        self.assertEqual(reglas["max_intentos"], MAX_INTENTOS)
        self.assertEqual(reglas["dias_min_disponibilidad"], DIAS_MIN_DISPONIBILIDAD)

    def test_distribucion_de_intentos(self):
        seccion = self.cuerpo({"secciones": "contacto"})["contacto"]

        self.assertEqual(
            seccion["distribucion_intentos"],
            [
                {"intentos": 0, "total": 1},
                {"intentos": 1, "total": 1},
                {"intentos": MAX_INTENTOS, "total": 1},
            ],
        )

    def test_desactivados_en_la_ventana(self):
        seccion = self.cuerpo({"secciones": "contacto", "dias": "7"})["contacto"]

        self.assertEqual(seccion["desactivados_en_ventana"], 1)

    def test_desactivacion_antigua_queda_fuera_de_la_ventana(self):
        Inmuebles.objects.filter(codigo="INACTIVO").update(
            fecha_desactivacion=timezone.now() - timedelta(days=90)
        )

        seccion = self.cuerpo({"secciones": "contacto", "dias": "7"})["contacto"]

        self.assertEqual(seccion["desactivados_en_ventana"], 0)
        # El snapshot no depende de la ventana: sigue inactivo.
        self.assertEqual(seccion["inactivos"], 1)


class ActividadTests(_DashboardTestCase):
    def setUp(self):
        registro = HistorialRespuestas.objects.create(
            codigo_inmueble="AC-1", respuesta=RESPUESTA_DISPONIBLE
        )
        # 10:30 hora local, para comprobar que se agrupa en hora local y no UTC.
        self.momento = timezone.localtime().replace(
            hour=10, minute=30, second=0, microsecond=0
        )
        _fechar(HistorialRespuestas, registro.pk, self.momento)

    def test_agrupa_por_hora_local(self):
        seccion = self.cuerpo({"secciones": "actividad", "dias": "2"})["actividad"]

        self.assertEqual(seccion["por_hora"], [{"hora": 10, "total": 1}])

    def test_agrupa_por_dia_de_la_semana_con_nombre(self):
        [dia] = self.cuerpo({"secciones": "actividad", "dias": "2"})["actividad"][
            "por_dia_semana"
        ]

        self.assertEqual(dia["dia"], self.momento.isoweekday())
        self.assertEqual(dia["total"], 1)
        self.assertIsNotNone(dia["nombre"])


class LogsTests(_DashboardTestCase):
    def setUp(self):
        RegistroLog.objects.create(nivel="INFO", evento="intervencion", mensaje="ok")
        RegistroLog.objects.create(nivel="INFO", evento="intervencion", mensaje="ok")
        RegistroLog.objects.create(
            nivel="ERROR",
            evento="error",
            codigo_inmueble="AC-9",
            mensaje="x" * 500,
        )

    def test_conteos_por_nivel_y_evento(self):
        seccion = self.cuerpo({"secciones": "logs"})["logs"]

        self.assertEqual(seccion["total"], 3)
        self.assertEqual(seccion["por_nivel"][0], {"nivel": "INFO", "total": 2})
        self.assertEqual(
            seccion["por_evento"][0], {"evento": "intervencion", "total": 2}
        )

    def test_ultimos_errores_con_el_mensaje_recortado(self):
        [error] = self.cuerpo({"secciones": "logs"})["logs"]["ultimos_errores"]

        self.assertEqual(error["codigo_inmueble"], "AC-9")
        self.assertEqual(len(error["mensaje"]), 300)
