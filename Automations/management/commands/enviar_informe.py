"""
Genera el informe xlsx del historial y lo envía por correo.

Pensado para ejecutarse de forma programada (crontab, diario). Uso manual:

    python manage.py enviar_informe
    python manage.py enviar_informe --to alguien@correo.com otro@correo.com
    python manage.py enviar_informe --dias 90     # ventana distinta a la de settings
    python manage.py enviar_informe --dias 0      # todo el histórico
"""

import os

from django.core.management.base import BaseCommand

from Automations.Services.Utils import generar_xlsx_historial_general
from Automations.Services.Email import enviar_informe_historial
from Automations.Services.Logging import log_evento


class Command(BaseCommand):
    help = "Genera y envía por correo el informe del historial de inventario."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            nargs="+",
            default=None,
            help="Destinatarios. Si se omite, usa los configurados por defecto.",
        )
        parser.add_argument(
            "--dias",
            type=int,
            default=None,
            help="Ventana en días del historial. 0 = todo. Por defecto, INFORME_DIAS.",
        )
        parser.add_argument(
            "--conservar",
            action="store_true",
            help="No borra el xlsx tras enviarlo (por defecto se borra).",
        )

    def handle(self, *args, **options):
        self.stdout.write("Generando informe de historial...")
        archivo = generar_xlsx_historial_general(dias=options.get("dias"))

        if not archivo:
            log_evento("informe", "No se pudo generar el informe", nivel="ERROR")
            self.stderr.write(self.style.ERROR("No se pudo generar el informe."))
            return

        enviado = enviar_informe_historial(archivo, destinatarios=options.get("to"))

        if enviado:
            self.stdout.write(self.style.SUCCESS(f"Informe enviado: {archivo}"))
            if not options["conservar"]:
                # Ya se entregó por correo: no dejamos que Media/ crezca un xlsx
                # por día. Si el envío falla, el archivo se conserva para revisarlo.
                try:
                    os.remove(archivo)
                except OSError as e:
                    log_evento(
                        "informe",
                        f"No se pudo borrar el informe ya enviado: {e}",
                        nivel="WARNING",
                        detalle={"archivo": archivo},
                    )
        else:
            self.stderr.write(self.style.ERROR("El informe se generó pero no pudo enviarse."))
            self.stderr.write(f"El archivo se conserva en: {archivo}")
