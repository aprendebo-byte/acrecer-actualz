"""
Serializadores de solo lectura del historial.

Los valores económicos se emiten como número JSON (`coerce_to_string=False`) y no
como cadena: los consumidores del historial son hojas de cálculo y herramientas
de BI, que con `"2500000.00"` tendrían que castear en destino.
"""

from rest_framework import serializers

from .models import (
    HistorialActualizacionesPrecios,
    HistorialIntervenciones,
    HistorialRespuestas,
)


def _decimal():
    return serializers.DecimalField(
        max_digits=12, decimal_places=2, coerce_to_string=False, read_only=True
    )


class HistorialRespuestasSerializer(serializers.ModelSerializer):
    valor = _decimal()

    class Meta:
        model = HistorialRespuestas
        fields = "__all__"


class HistorialActualizacionesPreciosSerializer(serializers.ModelSerializer):
    valor = _decimal()
    valor_anterior = _decimal()
    valor_nuevo = _decimal()

    class Meta:
        model = HistorialActualizacionesPrecios
        fields = "__all__"


class HistorialIntervencionesSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistorialIntervenciones
        fields = "__all__"
