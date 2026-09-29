"""
Endpoints de consulta del historial.

Cubre lo que rompe en producción si se descuida: el token (incluido el caso de
`API_INFORMES_TOKEN` sin definir, que debe cerrar y no abrir), la paginación y
que cada filtro acote de verdad en lugar de ignorarse en silencio.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from Automations.models import (
    HistorialActualizacionesPrecios,
    HistorialIntervenciones,
    HistorialRespuestas,
)
from Automations.Services.Utils import RESPUESTA_DESACTIVADO, RESPUESTA_DISPONIBLE

TOKEN = "token-de-prueba"


def _fechar(modelo, pk, cuando):
    """`fecha_registro` es auto_now_add: solo se puede mover con update()."""
    modelo.objects.filter(pk=pk).update(fecha_registro=cuando)


class TokenInformesTests(TestCase):
    def setUp(self):
        self.url = reverse("historial-respuestas")

    def test_sin_token_no_hay_acceso(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_token_incorrecto_no_hay_acceso(self):
        with self.settings(API_INFORMES_TOKEN=TOKEN):
            respuesta = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer otro")
        self.assertEqual(respuesta.status_code, 403)

    def test_token_en_cabecera_bearer(self):
        with self.settings(API_INFORMES_TOKEN=TOKEN):
            respuesta = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertEqual(respuesta.status_code, 200)

    def test_token_en_cabecera_x_api_token(self):
        with self.settings(API_INFORMES_TOKEN=TOKEN):
            respuesta = self.client.get(self.url, HTTP_X_API_TOKEN=TOKEN)
        self.assertEqual(respuesta.status_code, 200)

    def test_token_en_query_param(self):
        with self.settings(API_INFORMES_TOKEN=TOKEN):
            respuesta = self.client.get(self.url, {"token": TOKEN})
        self.assertEqual(respuesta.status_code, 200)

    def test_sin_token_configurado_falla_cerrado(self):
        """Un .env incompleto no debe dejar el historial abierto."""
        with self.settings(API_INFORMES_TOKEN=""):
            respuesta = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer cualquiera")
        self.assertEqual(respuesta.status_code, 403)

    def test_el_indice_tambien_exige_token(self):
        self.assertEqual(self.client.get(reverse("historial-index")).status_code, 403)

    def test_el_indice_lista_los_tres_historiales(self):
        with self.settings(API_INFORMES_TOKEN=TOKEN):
            respuesta = self.client.get(reverse("historial-index"), {"token": TOKEN})

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            sorted(respuesta.json()["historiales"]),
            ["intervenciones", "precios", "respuestas"],
        )


class _HistorialApiTestCase(TestCase):
    """Cliente autenticado con el token de informes."""

    def get(self, nombre_ruta, params=None):
        with self.settings(API_INFORMES_TOKEN=TOKEN):
            return self.client.get(
                reverse(nombre_ruta), params or {}, HTTP_X_API_TOKEN=TOKEN
            )

    def resultados(self, nombre_ruta, params=None):
        respuesta = self.get(nombre_ruta, params)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()["results"]


class HistorialRespuestasViewTests(_HistorialApiTestCase):
    def setUp(self):
        self.disponible = HistorialRespuestas.objects.create(
            codigo_inmueble="AC-1000",
            respuesta=RESPUESTA_DISPONIBLE,
            ciudad="Bogotá",
            tipo_servicio="forRent",
            valor=2500000,
            direccion="CL 80 52 A 90",
            nombre_contacto="ANA PROPIETARIA",
        )
        self.desactivado = HistorialRespuestas.objects.create(
            codigo_inmueble="AC-2000",
            respuesta=RESPUESTA_DESACTIVADO,
            razon_desactivacion="Lo vendió por su cuenta",
            ciudad="Medellín",
            tipo_servicio="forSale",
        )

    def test_lista_todo_ordenado_por_fecha_descendente(self):
        resultados = self.resultados("historial-respuestas")

        self.assertEqual(
            [r["codigo_inmueble"] for r in resultados], ["AC-2000", "AC-1000"]
        )

    def test_el_valor_se_serializa_como_numero(self):
        [registro] = self.resultados("historial-respuestas", {"codigo": "AC-1000"})

        self.assertEqual(registro["valor"], 2500000.0)

    def test_filtra_por_codigo_sin_distinguir_mayusculas(self):
        resultados = self.resultados("historial-respuestas", {"codigo": "ac-1000"})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["AC-1000"])

    def test_filtra_por_estado(self):
        resultados = self.resultados("historial-respuestas", {"estado": "desactivado"})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["AC-2000"])

    def test_estado_invalido_es_400(self):
        respuesta = self.get("historial-respuestas", {"estado": "vendido"})

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("estado", respuesta.json())

    def test_filtra_por_ciudad_parcialmente(self):
        resultados = self.resultados("historial-respuestas", {"ciudad": "medel"})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["AC-2000"])

    def test_busqueda_libre_alcanza_la_razon_de_desactivacion(self):
        resultados = self.resultados("historial-respuestas", {"q": "vendió"})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["AC-2000"])

    def test_filtros_se_combinan_con_and(self):
        resultados = self.resultados(
            "historial-respuestas", {"estado": "desactivado", "ciudad": "Bogotá"}
        )

        self.assertEqual(resultados, [])

    def test_parametro_desconocido_es_400(self):
        """Un filtro mal escrito no debe pasar por 'no hay datos'."""
        respuesta = self.get("historial-respuestas", {"ciduad": "Bogotá"})

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["parametros_desconocidos"], ["ciduad"])


class FiltrosDeFechaTests(_HistorialApiTestCase):
    def setUp(self):
        self.viejo = HistorialRespuestas.objects.create(
            codigo_inmueble="VIEJO", respuesta=RESPUESTA_DISPONIBLE
        )
        _fechar(HistorialRespuestas, self.viejo.pk, timezone.now() - timedelta(days=40))

        self.reciente = HistorialRespuestas.objects.create(
            codigo_inmueble="RECIENTE", respuesta=RESPUESTA_DISPONIBLE
        )

    def test_dias_acota_la_ventana(self):
        resultados = self.resultados("historial-respuestas", {"dias": "7"})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["RECIENTE"])

    def test_desde_con_fecha_simple(self):
        ayer = (timezone.localtime() - timedelta(days=1)).date().isoformat()

        resultados = self.resultados("historial-respuestas", {"desde": ayer})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["RECIENTE"])

    def test_hasta_incluye_el_dia_completo(self):
        """`hasta=hoy` debe incluir lo registrado hoy, no cortar a las 00:00."""
        hoy = timezone.localtime().date().isoformat()

        resultados = self.resultados("historial-respuestas", {"hasta": hoy})

        self.assertIn("RECIENTE", [r["codigo_inmueble"] for r in resultados])

    def test_rango_desde_hasta(self):
        hace_dos_meses = (timezone.localtime() - timedelta(days=60)).date().isoformat()
        hace_un_mes = (timezone.localtime() - timedelta(days=30)).date().isoformat()

        resultados = self.resultados(
            "historial-respuestas", {"desde": hace_dos_meses, "hasta": hace_un_mes}
        )

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["VIEJO"])

    def test_acepta_iso_8601_con_hora(self):
        hace_una_hora = (timezone.now() - timedelta(hours=1)).isoformat()

        resultados = self.resultados("historial-respuestas", {"desde": hace_una_hora})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["RECIENTE"])

    def test_fecha_invalida_es_400(self):
        respuesta = self.get("historial-respuestas", {"desde": "ayer"})

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("desde", respuesta.json())

    def test_fecha_imposible_es_400(self):
        """`date.fromisoformat` lanza ValueError con 2026-13-01: no debe ser un 500."""
        respuesta = self.get("historial-respuestas", {"hasta": "2026-13-01"})

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("hasta", respuesta.json())

    def test_dias_no_numerico_es_400(self):
        self.assertEqual(
            self.get("historial-respuestas", {"dias": "muchos"}).status_code, 400
        )

    def test_dias_cero_es_400(self):
        self.assertEqual(
            self.get("historial-respuestas", {"dias": "0"}).status_code, 400
        )


class PaginacionYOrdenTests(_HistorialApiTestCase):
    def setUp(self):
        for i in range(7):
            HistorialRespuestas.objects.create(
                codigo_inmueble=f"AC-{i:03d}", respuesta=RESPUESTA_DISPONIBLE
            )

    def test_pagina_con_page_size(self):
        respuesta = self.get("historial-respuestas", {"page_size": "3"})
        cuerpo = respuesta.json()

        self.assertEqual(cuerpo["count"], 7)
        self.assertEqual(len(cuerpo["results"]), 3)
        self.assertIsNotNone(cuerpo["next"])
        self.assertIsNone(cuerpo["previous"])

    def test_segunda_pagina(self):
        cuerpo = self.get("historial-respuestas", {"page_size": "3", "page": "3"}).json()

        self.assertEqual(len(cuerpo["results"]), 1)
        self.assertIsNone(cuerpo["next"])

    def test_pagina_inexistente_es_404(self):
        self.assertEqual(
            self.get("historial-respuestas", {"page": "99"}).status_code, 404
        )

    def test_page_size_por_encima_del_maximo_se_recorta(self):
        cuerpo = self.get("historial-respuestas", {"page_size": "5000"}).json()

        # No revienta ni sirve 5000 filas: cae al máximo permitido.
        self.assertEqual(cuerpo["count"], 7)
        self.assertEqual(len(cuerpo["results"]), 7)

    def test_ordering_ascendente_por_codigo(self):
        resultados = self.resultados(
            "historial-respuestas", {"ordering": "codigo_inmueble"}
        )

        codigos = [r["codigo_inmueble"] for r in resultados]
        self.assertEqual(codigos, sorted(codigos))

    def test_ordering_por_campo_no_permitido_es_400(self):
        respuesta = self.get("historial-respuestas", {"ordering": "telefono_contacto"})

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("ordering", respuesta.json())


class HistorialPreciosViewTests(_HistorialApiTestCase):
    def setUp(self):
        HistorialActualizacionesPrecios.objects.create(
            codigo_inmueble="AC-1000",
            valor=2500000,
            valor_anterior=2500000,
            valor_nuevo=2700000,
            ciudad="Bogotá",
        )
        HistorialActualizacionesPrecios.objects.create(
            codigo_inmueble="AC-2000", valor_anterior=1000000, valor_nuevo=1100000
        )

    def test_devuelve_valores_anterior_y_nuevo_como_numeros(self):
        [registro] = self.resultados("historial-precios", {"codigo": "AC-1000"})

        self.assertEqual(registro["valor_anterior"], 2500000.0)
        self.assertEqual(registro["valor_nuevo"], 2700000.0)

    def test_filtra_por_ciudad(self):
        resultados = self.resultados("historial-precios", {"ciudad": "bogot"})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["AC-1000"])

    def test_no_acepta_filtros_de_otro_historial(self):
        """`captador` solo existe en intervenciones: aquí es un error, no un no-op."""
        respuesta = self.get("historial-precios", {"captador": "Pedro"})

        self.assertEqual(respuesta.status_code, 400)


class HistorialIntervencionesViewTests(_HistorialApiTestCase):
    def setUp(self):
        HistorialIntervenciones.objects.create(
            codigo_inmueble="AC-1000",
            captador="PEDRO - Sucursal Norte",
            tipo_intervencion="Cambio de fotos",
        )
        HistorialIntervenciones.objects.create(
            codigo_inmueble="AC-2000",
            captador="MARIA - Sucursal Sur",
            tipo_intervencion="Visita al inmueble",
        )

    def test_filtra_por_captador(self):
        resultados = self.resultados("historial-intervenciones", {"captador": "maria"})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["AC-2000"])

    def test_filtra_por_tipo_de_intervencion(self):
        resultados = self.resultados(
            "historial-intervenciones", {"tipo_intervencion": "fotos"}
        )

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["AC-1000"])

    def test_busqueda_libre_alcanza_el_captador(self):
        resultados = self.resultados("historial-intervenciones", {"q": "Norte"})

        self.assertEqual([r["codigo_inmueble"] for r in resultados], ["AC-1000"])

    def test_no_acepta_filtro_de_ciudad(self):
        """El modelo no guarda ciudad; filtrar por ella debe avisar."""
        respuesta = self.get("historial-intervenciones", {"ciudad": "Bogotá"})

        self.assertEqual(respuesta.status_code, 400)
