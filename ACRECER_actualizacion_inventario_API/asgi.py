"""
ASGI config for ACRECER_actualizacion_inventario_API project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ACRECER_actualizacion_inventario_API.settings')

application = get_asgi_application()
