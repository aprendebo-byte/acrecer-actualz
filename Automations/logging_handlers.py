"""
Handler de logging que persiste registros en PostgreSQL (tabla `registro_log`).

Se conecta a los loggers `automations` (eventos de negocio, nivel INFO+) y
`django.request` (errores no controlados). Está pensado para guardar
únicamente eventos de negocio y errores, no el ruido de DEBUG.
"""

import logging
import threading

# Evita recursión infinita: si guardar el log dispara más logs (p.ej. SQL),
# no debemos volver a intentar persistirlos.
_reentrancy = threading.local()


class PostgresLogHandler(logging.Handler):
    def emit(self, record):
        if getattr(_reentrancy, "active", False):
            return

        _reentrancy.active = True
        try:
            # Import diferido: la app de Django debe estar lista.
            from .models import RegistroLog

            traceback_text = None
            if record.exc_info:
                traceback_text = self.format(record)

            RegistroLog.objects.create(
                nivel=record.levelname,
                evento=getattr(record, "evento", None),
                codigo_inmueble=getattr(record, "codigo_inmueble", None),
                mensaje=record.getMessage(),
                detalle=getattr(record, "detalle", None),
                traceback=traceback_text,
                origen=record.name,
            )
        except Exception:
            # Un fallo al escribir el log jamás debe tumbar la request.
            self.handleError(record)
        finally:
            _reentrancy.active = False
