from django.contrib import admin
from .models import (
    HistorialRespuestas,
    HistorialActualizacionesPrecios,
    HistorialIntervenciones,
    MobiliaToken,
    RegistroLog,
    EnvioPlantillaLog,
)


@admin.register(EnvioPlantillaLog)
class EnvioPlantillaLogAdmin(admin.ModelAdmin):
    list_display = ("fecha_registro", "conversation_id", "codigo_inmueble", "exitoso")
    list_filter = ("exitoso", "fecha_registro")
    search_fields = ("conversation_id", "codigo_inmueble", "message_id")
    date_hierarchy = "fecha_registro"


@admin.register(RegistroLog)
class RegistroLogAdmin(admin.ModelAdmin):
    list_display = ("fecha_registro", "nivel", "evento", "codigo_inmueble", "mensaje")
    list_filter = ("nivel", "evento", "fecha_registro")
    search_fields = ("codigo_inmueble", "mensaje", "evento")
    readonly_fields = [f.name for f in RegistroLog._meta.fields]
    date_hierarchy = "fecha_registro"


@admin.register(HistorialRespuestas)
class HistorialRespuestasAdmin(admin.ModelAdmin):
    list_display = ("fecha_registro", "codigo_inmueble", "respuesta", "razon_desactivacion")
    list_filter = ("fecha_registro",)
    search_fields = ("codigo_inmueble",)


@admin.register(HistorialActualizacionesPrecios)
class HistorialActualizacionesPreciosAdmin(admin.ModelAdmin):
    list_display = ("fecha_registro", "codigo_inmueble", "valor_anterior", "valor_nuevo")
    list_filter = ("fecha_registro",)
    search_fields = ("codigo_inmueble",)


@admin.register(HistorialIntervenciones)
class HistorialIntervencionesAdmin(admin.ModelAdmin):
    list_display = ("fecha_registro", "codigo_inmueble", "tipo_intervencion", "captador")
    list_filter = ("fecha_registro",)
    search_fields = ("codigo_inmueble", "captador")


admin.site.register(MobiliaToken)
