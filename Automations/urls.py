from .views import UpdateStatusView, UpdatePriceView, InterventionView
from .views_dashboard import DashboardView
from .views_historial import (
    HistorialIndexView,
    HistorialIntervencionesListView,
    HistorialPreciosListView,
    HistorialRespuestasListView,
)
from django.urls import path

urlpatterns = [
    path('update-status/', UpdateStatusView.as_view(), name='update-status'),
    path('update-price/', UpdatePriceView.as_view(), name='update-price'),
    path('intervention/', InterventionView.as_view(), name='record-intervention'),

    # Consulta del historial (solo lectura, requiere API_INFORMES_TOKEN).
    path('historial/', HistorialIndexView.as_view(), name='historial-index'),
    path('historial/respuestas/', HistorialRespuestasListView.as_view(),
         name='historial-respuestas'),
    path('historial/precios/', HistorialPreciosListView.as_view(),
         name='historial-precios'),
    path('historial/intervenciones/', HistorialIntervencionesListView.as_view(),
         name='historial-intervenciones'),

    # Métricas agregadas (mismo token que el historial).
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
]
