"""
Consulta del historial (respuestas, precios e intervenciones).

Solo lectura, paginado y filtrable. Protegido con `API_INFORMES_TOKEN`, así que se
puede consultar desde cualquier cliente que tenga el token (BI, hoja de cálculo,
n8n) sin abrir el resto de la API.

    GET /api/automations/historial/                 -> índice de rutas y filtros
    GET /api/automations/historial/respuestas/      -> disponibilidad / desactivaciones
    GET /api/automations/historial/precios/         -> solicitudes de cambio de valor
    GET /api/automations/historial/intervenciones/  -> intervenciones registradas

Filtros comunes:
    codigo (o codigo_inmueble), tipo_servicio  -> coincidencia exacta (sin distinguir
                                                  mayúsculas)
    tipo_inmueble, direccion, nombre_contacto,
    telefono_contacto                          -> coincidencia parcial
    desde, hasta                               -> fecha (YYYY-MM-DD) o ISO 8601
    dias                                       -> atajo: últimos N días
    q                                          -> búsqueda libre en los campos de texto
    ordering                                   -> p. ej. `fecha_registro` o `-codigo_inmueble`
    page, page_size                            -> paginación (50 por defecto, 500 máx.)

Un parámetro desconocido devuelve 400: un filtro mal escrito que se ignora en
silencio parece "no hay datos" y se interpreta como un problema de los datos.
"""

from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from .consultas import acotar, rango_de_fechas
from .models import (
    HistorialActualizacionesPrecios,
    HistorialIntervenciones,
    HistorialRespuestas,
)
from .permissions import TokenInformes
from .serializers import (
    HistorialActualizacionesPreciosSerializer,
    HistorialIntervencionesSerializer,
    HistorialRespuestasSerializer,
)
from .Services.Utils import RESPUESTA_DESACTIVADO, RESPUESTA_DISPONIBLE

# Parámetros que no son filtros de campo pero sí válidos en cualquier consulta.
# `token` porque la autorización lo admite por query string y `format` porque lo
# usa la negociación de contenido de DRF.
PARAMS_GENERALES = frozenset(
    {"desde", "hasta", "dias", "q", "ordering", "page", "page_size", "token", "format"}
)

# Alias en español de los textos canónicos de `respuesta`, para no obligar al
# cliente a escribir la frase completa con tilde.
ESTADOS_RESPUESTA = {
    "disponible": RESPUESTA_DISPONIBLE,
    "disponibles": RESPUESTA_DISPONIBLE,
    "desactivado": RESPUESTA_DESACTIVADO,
    "desactivados": RESPUESTA_DESACTIVADO,
}


class PaginacionHistorial(PageNumberPagination):
    """50 registros por página; el cliente puede pedir hasta 500."""

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


class _HistorialBaseView(ListAPIView):
    """
    Base de las tres vistas de historial: token, paginación y filtros comunes.

    Las subclases declaran `queryset`, `serializer_class` y sus filtros propios.
    """

    permission_classes = [TokenInformes]
    pagination_class = PaginacionHistorial

    # param de la URL -> campo del modelo
    filtros_exactos = {
        "codigo": "codigo_inmueble",
        "codigo_inmueble": "codigo_inmueble",
        "tipo_servicio": "tipo_servicio",
    }
    filtros_parciales = {
        "tipo_inmueble": "tipo_inmueble",
        "direccion": "direccion",
        "nombre_contacto": "nombre_contacto",
        "telefono_contacto": "telefono_contacto",
    }
    campos_busqueda = (
        "codigo_inmueble",
        "direccion",
        "nombre_contacto",
        "telefono_contacto",
    )
    ordenables = ("fecha_registro", "codigo_inmueble", "id")
    orden_por_defecto = ("-fecha_registro", "-id")

    def get_queryset(self):
        params = self.request.query_params
        self._validar_params(params)

        qs = super().get_queryset()
        qs = self._filtrar_por_fecha(qs, params)
        qs = self._filtrar_por_campos(qs, params)
        qs = self._buscar(qs, params)
        return qs.order_by(*self._orden(params))

    # ------------------------------------------------------------------
    # Filtrado
    # ------------------------------------------------------------------
    def _params_permitidos(self):
        return (
            PARAMS_GENERALES
            | set(self.filtros_exactos)
            | set(self.filtros_parciales)
        )

    def _validar_params(self, params):
        desconocidos = sorted(set(params) - self._params_permitidos())
        if desconocidos:
            raise ValidationError(
                {
                    "parametros_desconocidos": desconocidos,
                    "parametros_validos": sorted(self._params_permitidos()),
                }
            )

    def _filtrar_por_fecha(self, qs, params):
        # Sin ventana por defecto: el historial completo es un caso de uso válido
        # (una exportación inicial), y la paginación ya acota lo que se sirve.
        return acotar(qs, *rango_de_fechas(params))

    def _filtrar_por_campos(self, qs, params):
        for param, campo in self.filtros_exactos.items():
            valor = (params.get(param) or "").strip()
            if valor:
                qs = qs.filter(**{f"{campo}__iexact": valor})

        for param, campo in self.filtros_parciales.items():
            valor = (params.get(param) or "").strip()
            if valor:
                qs = qs.filter(**{f"{campo}__icontains": valor})

        return qs

    def _buscar(self, qs, params):
        termino = (params.get("q") or "").strip()
        if not termino or not self.campos_busqueda:
            return qs

        condicion = Q()
        for campo in self.campos_busqueda:
            condicion |= Q(**{f"{campo}__icontains": termino})
        return qs.filter(condicion)

    def _orden(self, params):
        crudo = (params.get("ordering") or "").strip()
        if not crudo:
            return self.orden_por_defecto

        orden = []
        for parte in crudo.split(","):
            campo = parte.strip()
            if not campo:
                continue
            desnudo = campo.lstrip("-")
            if desnudo not in self.ordenables:
                raise ValidationError(
                    {
                        "ordering": f"Campo no ordenable: '{desnudo}'.",
                        "ordenables": sorted(self.ordenables),
                    }
                )
            orden.append(campo)

        if not orden:
            return self.orden_por_defecto

        # `id` desempata: dos registros del mismo segundo tendrían orden
        # indefinido entre páginas y algún registro podría repetirse o perderse.
        return tuple(orden) + ("-id",)


class HistorialRespuestasListView(_HistorialBaseView):
    """Respuestas del propietario: sigue disponible o fue desactivado."""

    queryset = HistorialRespuestas.objects.all()
    serializer_class = HistorialRespuestasSerializer

    filtros_exactos = {
        **_HistorialBaseView.filtros_exactos,
        "respuesta": "respuesta",
    }
    filtros_parciales = {
        **_HistorialBaseView.filtros_parciales,
        "ciudad": "ciudad",
        "conjunto": "conjunto",
        "razon_desactivacion": "razon_desactivacion",
    }
    campos_busqueda = _HistorialBaseView.campos_busqueda + (
        "ciudad",
        "conjunto",
        "razon_desactivacion",
    )

    def _params_permitidos(self):
        return super()._params_permitidos() | {"estado"}

    def _filtrar_por_campos(self, qs, params):
        qs = super()._filtrar_por_campos(qs, params)

        estado = (params.get("estado") or "").strip().lower()
        if estado:
            respuesta = ESTADOS_RESPUESTA.get(estado)
            if respuesta is None:
                raise ValidationError(
                    {
                        "estado": f"Valor no reconocido: '{estado}'.",
                        "valores": ["disponible", "desactivado"],
                    }
                )
            qs = qs.filter(respuesta=respuesta)

        return qs


class HistorialPreciosListView(_HistorialBaseView):
    """Solicitudes de actualización de valor, con el valor anterior y el nuevo."""

    queryset = HistorialActualizacionesPrecios.objects.all()
    serializer_class = HistorialActualizacionesPreciosSerializer

    filtros_parciales = {
        **_HistorialBaseView.filtros_parciales,
        "ciudad": "ciudad",
        "conjunto": "conjunto",
    }
    campos_busqueda = _HistorialBaseView.campos_busqueda + ("ciudad", "conjunto")


class HistorialIntervencionesListView(_HistorialBaseView):
    """
    Intervenciones registradas sobre cada inmueble.

    Este modelo no guarda ciudad, conjunto ni valor: sus filtros propios son el
    captador y el tipo de intervención.
    """

    queryset = HistorialIntervenciones.objects.all()
    serializer_class = HistorialIntervencionesSerializer

    filtros_parciales = {
        **_HistorialBaseView.filtros_parciales,
        "captador": "captador",
        "tipo_intervencion": "tipo_intervencion",
    }
    campos_busqueda = _HistorialBaseView.campos_busqueda + (
        "captador",
        "tipo_intervencion",
    )


class HistorialIndexView(APIView):
    """Índice de los historiales disponibles y de los filtros de cada uno."""

    permission_classes = [TokenInformes]

    def get(self, request):
        historiales = {
            "respuestas": ("historial-respuestas", HistorialRespuestasListView),
            "precios": ("historial-precios", HistorialPreciosListView),
            "intervenciones": ("historial-intervenciones", HistorialIntervencionesListView),
        }

        return Response(
            {
                "historiales": {
                    nombre: {
                        "url": reverse(ruta, request=request),
                        # Se instancia solo para leer los filtros declarados;
                        # `_params_permitidos` no toca la petición.
                        "filtros": sorted(vista()._params_permitidos()),
                        "ordenables": sorted(vista.ordenables),
                    }
                    for nombre, (ruta, vista) in historiales.items()
                },
                "dashboard": reverse("dashboard", request=request),
                "paginacion": {
                    "page_size_por_defecto": PaginacionHistorial.page_size,
                    "page_size_maximo": PaginacionHistorial.max_page_size,
                },
            }
        )
