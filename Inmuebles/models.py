from django.db import models

# Create your models here.

class Inmuebles(models.Model):
    """
    Estado durable de cada inmueble de cara al agente de contacto.

    Es la fuente de verdad de los intentos de contacto: la memoria en Redis tiene
    un TTL de 24h y los 3 intentos permitidos se reparten en 3 días o más, así que
    el conteo no puede vivir solo en cache. Ver
    `Automations.Services.Contacto.enriquecer_con_estado_durable`.
    """

    codigo = models.CharField(max_length=100, unique=True)
    estado = models.BooleanField(default=True)
    # Fecha del último mensaje enviado al propietario (último intento de contacto).
    fecha_ultimo_mensaje = models.DateTimeField(null=True, blank=True)
    fecha_disponibilidad_confirmada = models.DateTimeField(null=True, blank=True)
    fecha_desactivacion = models.DateTimeField(null=True, blank=True)
    intentos_contacto = models.IntegerField(default=0)
    portales = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"Inmueble {self.codigo} - {'Activo' if self.estado else 'Inactivo'}"
