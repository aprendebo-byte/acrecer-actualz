"""
Revisa qué inmuebles deben contactarse y les envía la plantilla de WhatsApp.

Es un disparador delgado: lee el JSON de inmuebles, completa los intentos ya
realizados desde la base de datos y llama a
`get_inmuebles_a_contactar(..., enviar_fn=procesar_envio_inmueble)`, que aplica
las reglas de contacto y envía la plantilla a cada seleccionado.

Uso:
    python manage.py enviar_plantillas --archivo inmuebles.json
    python manage.py enviar_plantillas --archivo inmuebles.json --diagnostico
"""

import json

from django.core.management.base import BaseCommand, CommandError

from contacto_inmuebles import diagnosticar_inmuebles, get_inmuebles_a_contactar
from Automations.Services.Contacto import enriquecer_con_estado_durable
from Automations.Services.Plantillas import procesar_envio_inmueble


class Command(BaseCommand):
    help = "Revisa los inmuebles a contactar y les envía la plantilla de WhatsApp."

    def add_arguments(self, parser):
        parser.add_argument("--archivo", required=True, help="Ruta a un JSON con la lista de inmuebles.")
        parser.add_argument(
            "--diagnostico",
            action="store_true",
            help="No envía nada: muestra por qué cada inmueble se contactaría o se descarta.",
        )

    def handle(self, *args, **options):
        ruta = options["archivo"]
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                inmuebles = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise CommandError(f"No se pudo leer el archivo {ruta}: {e}")

        if not isinstance(inmuebles, list):
            raise CommandError("El archivo debe contener una lista JSON de inmuebles.")

        # Completa intentos y fechas desde la tabla `inmuebles`: sin esto el
        # conteo arrancaría en 0 en cada ejecución y se podría superar el máximo.
        inmuebles = enriquecer_con_estado_durable(inmuebles)

        if options["diagnostico"]:
            resultado = diagnosticar_inmuebles(inmuebles)
            self.stdout.write(json.dumps(resultado["resumen"], ensure_ascii=False, indent=2))
            for fila in resultado["detalle"]:
                self.stdout.write(f"  {fila['codigo']}: {fila['motivo']}")
            return

        # La función de selección envía la plantilla a cada inmueble elegible.
        contactados = get_inmuebles_a_contactar(inmuebles, enviar_fn=procesar_envio_inmueble)

        enviados = [c for c in contactados if c.get("envio", {}).get("exitoso")]
        cids = [c["conversation_id"] for c in enviados]

        self.stdout.write(self.style.SUCCESS(
            f"Seleccionados: {len(contactados)} | Enviados OK: {len(enviados)} "
            f"| Fallidos: {len(contactados) - len(enviados)}"
        ))
        self.stdout.write(f"conversation_ids enviados: {cids}")
