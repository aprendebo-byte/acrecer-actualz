"""
Dashboard de métricas del inventario.

    GET /api/automations/dashboard/

Un solo endpoint que agrega todo lo que se puede medir con los datos que ya
guarda la aplicación. Mismo token que el historial (`API_INFORMES_TOKEN`).

Parámetros:
    dias | desde | hasta -> ventana temporal. Por defecto, `INFORME_DIAS` (30).
    granularidad         -> dia (por defecto) | semana | mes, para la serie.
    top                  -> tamaño de los rankings (10 por defecto, 50 máx.).
    secciones            -> lista separada por comas para pedir solo una parte.

Secciones:
    resumen         contadores y tasas de la ventana
    serie           evolución temporal de cada tipo de novedad
    desactivaciones motivos y ciudades donde se cae el inventario
    precios         subidas/bajadas y magnitud de la variación
    intervenciones  por tipo y por captador
    distribucion    inventario tocado por ciudad, tipo de inmueble y servicio
    contacto        embudo de contacto (ESTADO ACTUAL, no depende de la ventana)
    actividad       a qué hora y qué día responden los propietarios
    logs            salud operativa: niveles, eventos y últimos errores

Todo se calcula con agregados en la base de datos: no se traen filas a memoria,
así que el coste no crece con el histórico (salvo los rankings, acotados por `top`).
"""

from collections import OrderedDict
from decimal import Decimal

from django.conf import settings
from django.db.models import Avg, Count, DecimalField, ExpressionWrapper, F, Max, Min, Q, Sum
from django.db.models.functions import (
    ExtractHour,
    ExtractIsoWeekDay,
    TruncDay,
    TruncMonth,
    TruncWeek,
)
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from contacto_inmuebles import DIAS_MIN_DISPONIBILIDAD, MAX_INTENTOS
from Inmuebles.models import Inmuebles

from .consultas import (
    acotar,
    parsear_entero_positivo,
    porcentaje,
    rango_de_fechas,
)
from .models import (
    EnvioPlantillaLog,
    HistorialActualizacionesPrecios,
    HistorialIntervenciones,
    HistorialRespuestas,
    RegistroLog,
)
from .permissions import TokenInformes
from .Services.Utils import RESPUESTA_DESACTIVADO, RESPUESTA_DISPONIBLE

GRANULARIDADES = {"dia": TruncDay, "semana": TruncWeek, "mes": TruncMonth}

TOP_POR_DEFECTO = 10
TOP_MAXIMO = 50

DIAS_SEMANA = {
    1: "lunes",
    2: "martes",
    3: "miércoles",
    4: "jueves",
    5: "viernes",
    6: "sábado",
    7: "domingo",
}

NIVELES_ERROR = ("ERROR", "CRITICAL")

# Variación porcentual del valor solicitado. En Decimal y no en float: mezclar
# numeric con double en Postgres obliga a un cast implícito y el resultado
# arrastra error de redondeo en cifras de nueve dígitos.
VARIACION = ExpressionWrapper(
    F("valor_nuevo") - F("valor_anterior"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)
VARIACION_PCT = ExpressionWrapper(
    (F("valor_nuevo") - F("valor_anterior")) * Decimal("100") / F("valor_anterior"),
    output_field=DecimalField(max_digits=16, decimal_places=4),
)


def _redondear(valor, decimales=2):
    """Redondea Decimal/float dejando pasar los None (métrica sin datos)."""
    if valor is None:
        return None
    return round(float(valor), decimales)


def _conteos(qs, campo, top=None, etiqueta="valor"):
    """Ranking `campo` -> total, descendente, ignorando nulos y cadenas vacías."""
    filas = (
        qs.exclude(**{f"{campo}__isnull": True})
        .exclude(**{campo: ""})
        .values(campo)
        .annotate(total=Count("id"))
        .order_by("-total", campo)
    )
    if top:
        filas = filas[:top]
    return [{etiqueta: fila[campo], "total": fila["total"]} for fila in filas]


class DashboardView(APIView):
    """Métricas agregadas del inventario, en un solo GET."""

    permission_classes = [TokenInformes]

    PARAMS_VALIDOS = frozenset(
        {"dias", "desde", "hasta", "granularidad", "top", "secciones", "token", "format"}
    )

    def get(self, request):
        params = request.query_params
        self._validar_params(params)

        dias_defecto = getattr(settings, "INFORME_DIAS", 30)
        desde, hasta = rango_de_fechas(params, dias_por_defecto=dias_defecto)
        top = self._top(params)
        granularidad = self._granularidad(params)

        secciones = OrderedDict(
            (
                ("resumen", self._resumen),
                ("serie", self._serie),
                ("desactivaciones", self._desactivaciones),
                ("precios", self._precios),
                ("intervenciones", self._intervenciones),
                ("distribucion", self._distribucion),
                ("contacto", self._contacto),
                ("actividad", self._actividad),
                ("logs", self._logs),
            )
        )
        pedidas = self._secciones_pedidas(params, secciones)

        contexto = {
            "desde": desde,
            "hasta": hasta,
            "top": top,
            "granularidad": granularidad,
        }

        cuerpo = {
            "ventana": {
                "desde": desde,
                "hasta": hasta or timezone.now(),
                # Explícito: sin `dias`/`desde`/`hasta` la ventana la fija
                # INFORME_DIAS, y con INFORME_DIAS=0 se agrega todo el histórico.
                "acotada": desde is not None,
            },
            "granularidad": granularidad,
            "top": top,
            "generado_en": timezone.now(),
        }
        for nombre in pedidas:
            cuerpo[nombre] = secciones[nombre](**contexto)

        return Response(cuerpo)

    # ------------------------------------------------------------------
    # Parámetros
    # ------------------------------------------------------------------
    def _validar_params(self, params):
        desconocidos = sorted(set(params) - self.PARAMS_VALIDOS)
        if desconocidos:
            raise ValidationError(
                {
                    "parametros_desconocidos": desconocidos,
                    "parametros_validos": sorted(self.PARAMS_VALIDOS),
                }
            )

    def _top(self, params):
        crudo = params.get("top")
        if not crudo:
            return TOP_POR_DEFECTO
        return parsear_entero_positivo(crudo, "top", maximo=TOP_MAXIMO)

    def _granularidad(self, params):
        valor = (params.get("granularidad") or "dia").strip().lower()
        if valor not in GRANULARIDADES:
            raise ValidationError(
                {
                    "granularidad": f"Valor no reconocido: '{valor}'.",
                    "valores": sorted(GRANULARIDADES),
                }
            )
        return valor

    def _secciones_pedidas(self, params, disponibles):
        crudo = (params.get("secciones") or "").strip()
        if not crudo:
            return list(disponibles)

        pedidas = [parte.strip().lower() for parte in crudo.split(",") if parte.strip()]
        invalidas = sorted(set(pedidas) - set(disponibles))
        if invalidas:
            raise ValidationError(
                {
                    "secciones_desconocidas": invalidas,
                    "secciones_validas": sorted(disponibles),
                }
            )
        # Se respeta el orden canónico, no el que llegue en la URL.
        return [nombre for nombre in disponibles if nombre in pedidas]

    # ------------------------------------------------------------------
    # Secciones
    # ------------------------------------------------------------------
    def _resumen(self, *, desde, hasta, **_):
        respuestas = acotar(HistorialRespuestas.objects.all(), desde, hasta).aggregate(
            total=Count("id"),
            disponibles=Count("id", filter=Q(respuesta=RESPUESTA_DISPONIBLE)),
            desactivados=Count("id", filter=Q(respuesta=RESPUESTA_DESACTIVADO)),
            inmuebles=Count("codigo_inmueble", distinct=True),
        )
        precios = acotar(
            HistorialActualizacionesPrecios.objects.all(), desde, hasta
        ).aggregate(
            total=Count("id"), inmuebles=Count("codigo_inmueble", distinct=True)
        )
        intervenciones = acotar(
            HistorialIntervenciones.objects.all(), desde, hasta
        ).aggregate(
            total=Count("id"), inmuebles=Count("codigo_inmueble", distinct=True)
        )
        envios = acotar(EnvioPlantillaLog.objects.all(), desde, hasta).aggregate(
            total=Count("id"),
            exitosos=Count("id", filter=Q(exitoso=True)),
            fallidos=Count("id", filter=Q(exitoso=False)),
            conversaciones=Count("conversation_id", distinct=True),
        )
        logs = acotar(RegistroLog.objects.all(), desde, hasta).aggregate(
            total=Count("id"),
            errores=Count("id", filter=Q(nivel__in=NIVELES_ERROR)),
        )

        # Denominador: solo las respuestas con una de las dos respuestas canónicas.
        # Incluir otras en el total inflaría la base y bajaría la tasa artificialmente.
        decididas = respuestas["disponibles"] + respuestas["desactivados"]

        return {
            "respuestas": {
                "total": respuestas["total"],
                "disponibles": respuestas["disponibles"],
                "desactivados": respuestas["desactivados"],
                "inmuebles_distintos": respuestas["inmuebles"],
                "tasa_desactivacion_pct": porcentaje(
                    respuestas["desactivados"], decididas
                ),
            },
            "precios": {
                "solicitudes": precios["total"],
                "inmuebles_distintos": precios["inmuebles"],
            },
            "intervenciones": {
                "total": intervenciones["total"],
                "inmuebles_distintos": intervenciones["inmuebles"],
            },
            "envios_plantilla": {
                "total": envios["total"],
                "exitosos": envios["exitosos"],
                "fallidos": envios["fallidos"],
                "conversaciones_distintas": envios["conversaciones"],
                "tasa_exito_pct": porcentaje(envios["exitosos"], envios["total"]),
                # Aproximada: la respuesta puede llegar días después del envío, así
                # que en ventanas cortas el numerador y el denominador no son la
                # misma cohorte de inmuebles.
                "tasa_respuesta_pct_aprox": porcentaje(
                    respuestas["total"], envios["exitosos"]
                ),
            },
            "logs": {
                "total": logs["total"],
                "errores": logs["errores"],
                "tasa_error_pct": porcentaje(logs["errores"], logs["total"]),
            },
        }

    def _serie(self, *, desde, hasta, granularidad, **_):
        trunc = GRANULARIDADES[granularidad]
        filas = {}

        def punto(fecha):
            clave = timezone.localtime(fecha).date().isoformat()
            return filas.setdefault(
                clave,
                {
                    "periodo": clave,
                    "disponibles": 0,
                    "desactivados": 0,
                    "solicitudes_precio": 0,
                    "intervenciones": 0,
                    "envios_exitosos": 0,
                    "envios_fallidos": 0,
                },
            )

        respuestas = (
            acotar(HistorialRespuestas.objects.all(), desde, hasta)
            .annotate(periodo=trunc("fecha_registro"))
            .values("periodo")
            .annotate(
                disponibles=Count("id", filter=Q(respuesta=RESPUESTA_DISPONIBLE)),
                desactivados=Count("id", filter=Q(respuesta=RESPUESTA_DESACTIVADO)),
            )
        )
        for fila in respuestas:
            destino = punto(fila["periodo"])
            destino["disponibles"] = fila["disponibles"]
            destino["desactivados"] = fila["desactivados"]

        for modelo, campo in (
            (HistorialActualizacionesPrecios, "solicitudes_precio"),
            (HistorialIntervenciones, "intervenciones"),
        ):
            filas_modelo = (
                acotar(modelo.objects.all(), desde, hasta)
                .annotate(periodo=trunc("fecha_registro"))
                .values("periodo")
                .annotate(total=Count("id"))
            )
            for fila in filas_modelo:
                punto(fila["periodo"])[campo] = fila["total"]

        envios = (
            acotar(EnvioPlantillaLog.objects.all(), desde, hasta)
            .annotate(periodo=trunc("fecha_registro"))
            .values("periodo")
            .annotate(
                exitosos=Count("id", filter=Q(exitoso=True)),
                fallidos=Count("id", filter=Q(exitoso=False)),
            )
        )
        for fila in envios:
            destino = punto(fila["periodo"])
            destino["envios_exitosos"] = fila["exitosos"]
            destino["envios_fallidos"] = fila["fallidos"]

        # Solo periodos con actividad: rellenar los huecos es trabajo del cliente,
        # que sabe si quiere una línea continua o barras salteadas.
        return [filas[clave] for clave in sorted(filas)]

    def _desactivaciones(self, *, desde, hasta, top, **_):
        qs = acotar(
            HistorialRespuestas.objects.filter(respuesta=RESPUESTA_DESACTIVADO),
            desde,
            hasta,
        )
        total = qs.count()
        con_motivo = qs.exclude(razon_desactivacion__isnull=True).exclude(
            razon_desactivacion=""
        )
        motivos = _conteos(con_motivo, "razon_desactivacion", top, etiqueta="motivo")

        for motivo in motivos:
            motivo["porcentaje"] = porcentaje(motivo["total"], total)

        return {
            "total": total,
            # Se pregunta el motivo en el turno siguiente de la conversación: un
            # número alto aquí significa conversaciones que se cortaron a medias.
            "sin_motivo": total - con_motivo.count(),
            "motivos": motivos,
            "por_ciudad": _conteos(qs, "ciudad", top, etiqueta="ciudad"),
            "por_tipo_inmueble": _conteos(qs, "tipo_inmueble", top, etiqueta="tipo"),
        }

    def _precios(self, *, desde, hasta, top, **_):
        qs = acotar(HistorialActualizacionesPrecios.objects.all(), desde, hasta)
        comparables = qs.filter(valor_anterior__isnull=False, valor_nuevo__isnull=False)

        agregados = comparables.aggregate(
            subidas=Count("id", filter=Q(valor_nuevo__gt=F("valor_anterior"))),
            bajadas=Count("id", filter=Q(valor_nuevo__lt=F("valor_anterior"))),
            sin_cambio=Count("id", filter=Q(valor_nuevo=F("valor_anterior"))),
            variacion_promedio=Avg(VARIACION),
            variacion_total=Sum(VARIACION),
            valor_anterior_promedio=Avg("valor_anterior"),
            valor_nuevo_promedio=Avg("valor_nuevo"),
            valor_nuevo_minimo=Min("valor_nuevo"),
            valor_nuevo_maximo=Max("valor_nuevo"),
        )
        # La variación porcentual solo tiene sentido con base positiva; un
        # valor_anterior en 0 haría que Postgres dividiera por cero.
        con_base = comparables.filter(valor_anterior__gt=0)
        pct = con_base.aggregate(promedio=Avg(VARIACION_PCT))

        ranking = (
            con_base.annotate(variacion=VARIACION, variacion_pct=VARIACION_PCT)
            .values(
                "codigo_inmueble",
                "fecha_registro",
                "ciudad",
                "valor_anterior",
                "valor_nuevo",
                "variacion",
                "variacion_pct",
            )
        )

        def formatear(filas):
            return [
                {
                    **fila,
                    "valor_anterior": _redondear(fila["valor_anterior"]),
                    "valor_nuevo": _redondear(fila["valor_nuevo"]),
                    "variacion": _redondear(fila["variacion"]),
                    "variacion_pct": _redondear(fila["variacion_pct"]),
                }
                for fila in filas
            ]

        return {
            "solicitudes": qs.count(),
            "comparables": comparables.count(),
            "subidas": agregados["subidas"],
            "bajadas": agregados["bajadas"],
            "sin_cambio": agregados["sin_cambio"],
            "variacion_promedio": _redondear(agregados["variacion_promedio"]),
            "variacion_total": _redondear(agregados["variacion_total"]),
            "variacion_pct_promedio": _redondear(pct["promedio"]),
            "valor_anterior_promedio": _redondear(agregados["valor_anterior_promedio"]),
            "valor_nuevo_promedio": _redondear(agregados["valor_nuevo_promedio"]),
            "valor_nuevo_minimo": _redondear(agregados["valor_nuevo_minimo"]),
            "valor_nuevo_maximo": _redondear(agregados["valor_nuevo_maximo"]),
            # Cada ranking se restringe a su signo: sin el filtro, con menos de
            # `top` subidas la lista se rellenaba con bajadas bajo el rótulo
            # "mayores_subidas".
            "mayores_subidas": formatear(
                ranking.filter(valor_nuevo__gt=F("valor_anterior")).order_by(
                    "-variacion_pct"
                )[:top]
            ),
            "mayores_bajadas": formatear(
                ranking.filter(valor_nuevo__lt=F("valor_anterior")).order_by(
                    "variacion_pct"
                )[:top]
            ),
        }

    def _intervenciones(self, *, desde, hasta, top, **_):
        qs = acotar(HistorialIntervenciones.objects.all(), desde, hasta)

        return {
            "total": qs.count(),
            "inmuebles_distintos": qs.values("codigo_inmueble").distinct().count(),
            "por_tipo": _conteos(qs, "tipo_intervencion", top, etiqueta="tipo"),
            "por_captador": _conteos(qs, "captador", top, etiqueta="captador"),
        }

    def _distribucion(self, *, desde, hasta, top, **_):
        qs = acotar(HistorialRespuestas.objects.all(), desde, hasta)

        def desglose(campo, etiqueta, limite=None):
            filas = (
                qs.exclude(**{f"{campo}__isnull": True})
                .exclude(**{campo: ""})
                .values(campo)
                .annotate(
                    total=Count("id"),
                    disponibles=Count("id", filter=Q(respuesta=RESPUESTA_DISPONIBLE)),
                    desactivados=Count(
                        "id", filter=Q(respuesta=RESPUESTA_DESACTIVADO)
                    ),
                    valor_promedio=Avg("valor"),
                )
                .order_by("-total", campo)
            )
            if limite:
                filas = filas[:limite]
            return [
                {
                    etiqueta: fila[campo],
                    "total": fila["total"],
                    "disponibles": fila["disponibles"],
                    "desactivados": fila["desactivados"],
                    "tasa_desactivacion_pct": porcentaje(
                        fila["desactivados"],
                        fila["disponibles"] + fila["desactivados"],
                    ),
                    "valor_promedio": _redondear(fila["valor_promedio"]),
                }
                for fila in filas
            ]

        return {
            "por_ciudad": desglose("ciudad", "ciudad", top),
            "por_tipo_inmueble": desglose("tipo_inmueble", "tipo", top),
            # Solo hay dos tipos de servicio (forRent/forSale): no se acota.
            "por_tipo_servicio": desglose("tipo_servicio", "tipo_servicio"),
            "por_conjunto": desglose("conjunto", "conjunto", top),
        }

    def _contacto(self, *, desde, hasta, **_):
        """
        Embudo de contacto. Es una FOTO DEL ESTADO ACTUAL de la tabla `inmuebles`:
        `estado` e `intentos_contacto` son campos mutables sin historia, así que no
        se pueden reconstruir para una ventana pasada. Solo `desactivados_en_ventana`
        respeta el rango, porque `fecha_desactivacion` sí es un instante.
        """
        ahora = timezone.now()
        limite_disponibilidad = ahora - timezone.timedelta(
            days=DIAS_MIN_DISPONIBILIDAD
        )

        agregados = Inmuebles.objects.aggregate(
            total=Count("id"),
            activos=Count("id", filter=Q(estado=True)),
            inactivos=Count("id", filter=Q(estado=False)),
            sin_contactar=Count("id", filter=Q(estado=True, intentos_contacto=0)),
            agotados=Count(
                "id", filter=Q(estado=True, intentos_contacto__gte=MAX_INTENTOS)
            ),
            sin_disponibilidad_confirmada=Count(
                "id", filter=Q(estado=True, fecha_disponibilidad_confirmada__isnull=True)
            ),
            intentos_promedio=Avg("intentos_contacto", filter=Q(estado=True)),
        )

        # Mismas condiciones que aplica el agente en contacto_inmuebles.py.
        candidatos = Inmuebles.objects.filter(
            estado=True,
            intentos_contacto__lt=MAX_INTENTOS,
            fecha_disponibilidad_confirmada__lte=limite_disponibilidad,
        ).count()

        distribucion = (
            Inmuebles.objects.filter(estado=True)
            .values("intentos_contacto")
            .annotate(total=Count("id"))
            .order_by("intentos_contacto")
        )

        return {
            "tipo": "snapshot",
            "total": agregados["total"],
            "activos": agregados["activos"],
            "inactivos": agregados["inactivos"],
            "sin_contactar": agregados["sin_contactar"],
            "intentos_agotados": agregados["agotados"],
            "sin_disponibilidad_confirmada": agregados[
                "sin_disponibilidad_confirmada"
            ],
            "intentos_promedio": _redondear(agregados["intentos_promedio"]),
            "candidatos_a_contacto": candidatos,
            "reglas": {
                "max_intentos": MAX_INTENTOS,
                "dias_min_disponibilidad": DIAS_MIN_DISPONIBILIDAD,
            },
            "distribucion_intentos": [
                {"intentos": fila["intentos_contacto"], "total": fila["total"]}
                for fila in distribucion
            ],
            "desactivados_en_ventana": acotar(
                Inmuebles.objects.filter(fecha_desactivacion__isnull=False),
                desde,
                hasta,
                campo="fecha_desactivacion",
            ).count(),
        }

    def _actividad(self, *, desde, hasta, **_):
        """Cuándo responden los propietarios: útil para elegir la hora de envío."""
        qs = acotar(HistorialRespuestas.objects.all(), desde, hasta)

        por_hora = (
            qs.annotate(hora=ExtractHour("fecha_registro"))
            .values("hora")
            .annotate(total=Count("id"))
            .order_by("hora")
        )
        por_dia = (
            qs.annotate(dia=ExtractIsoWeekDay("fecha_registro"))
            .values("dia")
            .annotate(total=Count("id"))
            .order_by("dia")
        )

        return {
            # Hora local del negocio: ExtractHour aplica TIME_ZONE con USE_TZ activo.
            "por_hora": [
                {"hora": fila["hora"], "total": fila["total"]} for fila in por_hora
            ],
            "por_dia_semana": [
                {
                    "dia": fila["dia"],
                    "nombre": DIAS_SEMANA.get(fila["dia"]),
                    "total": fila["total"],
                }
                for fila in por_dia
            ],
        }

    def _logs(self, *, desde, hasta, top, **_):
        qs = acotar(RegistroLog.objects.all(), desde, hasta)

        ultimos_errores = (
            qs.filter(nivel__in=NIVELES_ERROR)
            .order_by("-fecha_registro")
            .values("fecha_registro", "nivel", "evento", "codigo_inmueble", "mensaje")[
                :top
            ]
        )

        return {
            "total": qs.count(),
            "por_nivel": _conteos(qs, "nivel", etiqueta="nivel"),
            "por_evento": _conteos(qs, "evento", top, etiqueta="evento"),
            "ultimos_errores": [
                # El mensaje se recorta: un traceback completo por fila convertiría
                # el dashboard en una descarga de megas.
                {**fila, "mensaje": (fila["mensaje"] or "")[:300]}
                for fila in ultimos_errores
            ],
        }
