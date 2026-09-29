from django.contrib import admin

from .models import Inmuebles


@admin.register(Inmuebles)
class InmueblesAdmin(admin.ModelAdmin):
    """Estado de contacto por inmueble: permite auditar por qué se contactó o no."""

    list_display = (
        "codigo",
        "estado",
        "intentos_contacto",
        "fecha_ultimo_mensaje",
        "fecha_disponibilidad_confirmada",
        "fecha_desactivacion",
    )
    list_filter = ("estado", "intentos_contacto")
    search_fields = ("codigo",)
