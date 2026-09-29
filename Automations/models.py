from django.db import models
from django.utils import timezone

# Create your models here.
class HistorialRespuestas(models.Model):
    # Indexados: se filtra por código y se ordena por fecha en cada desactivación.
    fecha_registro = models.DateTimeField(auto_now_add=True, db_index=True)

    # Identificación del inmueble
    codigo_inmueble = models.CharField(max_length=100, db_index=True)
    tipo_servicio = models.CharField(max_length=100, null=True, blank=True)  # serviceType
    tipo_inmueble = models.CharField(max_length=150, null=True, blank=True)  # propertyTypeName

    # Valores económicos
    valor = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # rentValue

    # Ubicación
    direccion = models.CharField(max_length=255, null=True, blank=True)
    ciudad = models.CharField(max_length=120, null=True, blank=True)  # cityName
    conjunto = models.CharField(max_length=120, null=True, blank=True)  # condominiumName

    # Contacto del inmueble
    nombre_contacto = models.CharField(max_length=150, null=True, blank=True)  # contactName
    telefono_contacto = models.CharField(max_length=20, null=True, blank=True)  # contactPhone

    # Información adicional externa
    codigos_externos = models.JSONField(null=True, blank=True)  # externalCodesJSON

    # Respuesta textual del sistema externo
    respuesta = models.TextField(null=True, blank=True)

    #motivo de desactivacion
    razon_desactivacion = models.TextField(null=True, blank=True)
    def __str__(self):
        return f"Historial {self.codigo_inmueble} – {self.fecha_registro.strftime('%Y-%m-%d')}"


class HistorialActualizacionesPrecios(models.Model):
    fecha_registro = models.DateTimeField(auto_now_add=True, db_index=True)

    # Identificación del inmueble
    codigo_inmueble = models.CharField(max_length=100, db_index=True)
    tipo_servicio = models.CharField(max_length=100, null=True, blank=True)  # serviceType
    tipo_inmueble = models.CharField(max_length=150, null=True, blank=True)  # propertyTypeName

    # Valores económicos
    valor = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # rentValue
    valor_anterior = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valor_nuevo = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Ubicación
    direccion = models.CharField(max_length=255, null=True, blank=True)
    ciudad = models.CharField(max_length=120, null=True, blank=True)  # cityName
    conjunto = models.CharField(max_length=120, null=True, blank=True)  # condominiumName

    # Contacto del inmueble
    nombre_contacto = models.CharField(max_length=150, null=True, blank=True)  # contactName
    telefono_contacto = models.CharField(max_length=20, null=True, blank=True)  # contactPhone

    def __str__(self):
        return f"Historial Precio {self.codigo_inmueble} – {self.fecha_registro.strftime('%Y-%m-%d')}"
    
class HistorialIntervenciones(models.Model):
    fecha_registro = models.DateTimeField(auto_now_add=True, db_index=True)

    # Identificación del inmueble
    codigo_inmueble = models.CharField(max_length=100, db_index=True)
    tipo_servicio = models.CharField(max_length=100, null=True, blank=True)  # serviceType
    tipo_inmueble = models.CharField(max_length=150, null=True, blank=True)  # propertyTypeName
    direccion = models.CharField(max_length=255, null=True, blank=True)
    
    nombre_contacto = models.CharField(max_length=150, null=True, blank=True)  # contactName
    telefono_contacto = models.CharField(max_length=20, null=True, blank=True)
    # Descripción de la intervención
    captador = models.CharField(max_length=255, null=True, blank=True)
    tipo_intervencion = models.TextField(null=True, blank=True)


    def __str__(self):
        return f"Intervención {self.codigo_inmueble} – {self.fecha_registro.strftime('%Y-%m-%d')}"
    
class MobiliaToken(models.Model):
    token = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_expired(self, ttl_minutes=50):
        return timezone.now() > self.created_at + timezone.timedelta(minutes=ttl_minutes)

    class Meta:
        db_table = "mobilia_token"


class EnvioPlantillaLog(models.Model):
    """
    Persistencia mínima de los envíos de plantilla de WhatsApp.

    El flujo n8n llama a POST /conversacion tras cada envío; aquí queda el
    registro de éxito/fallo, el conversationId y la fecha (req. de auditoría).
    """

    fecha_registro = models.DateTimeField(auto_now_add=True, db_index=True)
    conversation_id = models.CharField(max_length=100, db_index=True)
    codigo_inmueble = models.CharField(max_length=100, null=True, blank=True)
    exitoso = models.BooleanField(default=False)
    message_id = models.CharField(max_length=255, null=True, blank=True)
    template_id = models.CharField(max_length=255, null=True, blank=True)
    payload = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "envio_plantilla_log"
        ordering = ["-fecha_registro"]
        verbose_name = "Log de envío de plantilla"
        verbose_name_plural = "Logs de envío de plantilla"

    def __str__(self):
        estado = "OK" if self.exitoso else "FALLO"
        return f"[{estado}] {self.conversation_id} – {self.fecha_registro:%Y-%m-%d %H:%M}"


class RegistroLog(models.Model):
    """
    Persiste eventos de negocio y errores de la aplicación en PostgreSQL.

    Lo alimenta el handler de logging `PostgresLogHandler` y el helper
    `Automations.Services.Logging.log_evento`.
    """

    NIVELES = [
        ("DEBUG", "DEBUG"),
        ("INFO", "INFO"),
        ("WARNING", "WARNING"),
        ("ERROR", "ERROR"),
        ("CRITICAL", "CRITICAL"),
    ]

    fecha_registro = models.DateTimeField(auto_now_add=True, db_index=True)
    nivel = models.CharField(max_length=20, choices=NIVELES, default="INFO", db_index=True)
    # Categoría del evento de negocio: intervencion, desactivacion,
    # actualizacion_precio, mobilia, email, informe, error...
    evento = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    codigo_inmueble = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    mensaje = models.TextField()
    detalle = models.JSONField(null=True, blank=True)
    traceback = models.TextField(null=True, blank=True)
    origen = models.CharField(max_length=150, null=True, blank=True)  # logger name

    class Meta:
        db_table = "registro_log"
        ordering = ["-fecha_registro"]
        verbose_name = "Registro de log"
        verbose_name_plural = "Registros de log"

    def __str__(self):
        return f"[{self.nivel}] {self.evento or 's/categoria'} – {self.fecha_registro:%Y-%m-%d %H:%M}"