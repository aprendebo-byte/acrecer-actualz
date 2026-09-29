"""
Helper para registrar eventos de negocio de forma consistente.

Uso:
    from .Logging import log_evento
    log_evento("intervencion", "Intervención registrada", codigo="A-1023",
               detalle={"tipo": "llamada"})

Los registros viajan por el logger `automations`, que está configurado en
settings para escribir tanto en consola como en la tabla `registro_log`.
"""

import logging

logger = logging.getLogger("automations")


def log_evento(evento, mensaje, codigo=None, nivel="INFO", detalle=None, exc_info=False):
    """
    Registra un evento de negocio o un error.

    evento   : categoría (intervencion, desactivacion, actualizacion_precio,
               mobilia, email, informe, error...).
    mensaje  : texto legible.
    codigo   : código del inmueble relacionado (opcional).
    nivel    : INFO | WARNING | ERROR | CRITICAL.
    detalle  : dict serializable con contexto extra (opcional).
    exc_info : True para adjuntar el traceback de la excepción en curso.
    """
    level = getattr(logging, str(nivel).upper(), logging.INFO)
    logger.log(
        level,
        mensaje,
        extra={
            "evento": evento,
            "codigo_inmueble": codigo,
            "detalle": detalle,
        },
        exc_info=exc_info,
    )
