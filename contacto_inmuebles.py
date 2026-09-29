"""
Utilidad independiente para seleccionar inmuebles a contactar.

A partir de una lista de inmuebles (la misma estructura que produce el flujo
n8n), decide a cuáles corresponde enviar el siguiente intento de contacto según
las reglas de negocio de ACRECER.

Reglas:
  1. Solo inmuebles ACTIVOS (estado=True).
  2. Solo inmuebles cuya disponibilidad confirmada (lastAvailability) tenga
     MÁS de 20 días de antigüedad.
  3. Máximo 3 intentos de contacto.
  4. Al menos 1 día entre intentos (se usa lastMessageDate / el último intento
     guardado para calcular si ya corresponde el siguiente).

Memoria temporal:
  - Se guarda cada conversationId con la info del inmueble y el número de
    intentos, con un TTL de 24 horas.
  - NOTA sobre el TTL: 3 intentos espaciados 1 día ocupan ≥3 días, más que el
    TTL de 24h. Por eso el CONTEO DURABLE de intentos se toma, en este orden:
        a) del campo de entrada `intentos_realizados` / `intentos` (si viene
           de tu base de datos), o
        b) de la memoria temporal (válido dentro de las 24h), o
        c) 0 si es el primer contacto.
    La memoria de 24h sirve sobre todo para NO recontactar dos veces el mismo
    día si la utilidad se ejecuta varias veces. Para un conteo 100% durable a
    lo largo de los 3 días, persiste `intentos_realizados` en tu DB y pásalo en
    la entrada (la utilidad lo respeta).

Sin dependencias externas: solo biblioteca estándar. Usable desde Python o
desde un nodo "Code (Python)" en n8n.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger("contacto_inmuebles")

# --------------------------------------------------------------------------- #
# Parámetros de negocio (ajustables)
# --------------------------------------------------------------------------- #
MAX_INTENTOS = 3                 # máximo de intentos de contacto
DIAS_MIN_DISPONIBILIDAD = 20     # lastAvailability debe tener > 20 días
INTERVALO_MIN_DIAS = 1           # 1 día por medio entre intentos
TTL_SEGUNDOS = 24 * 60 * 60      # TTL de la memoria temporal (24 horas)


# --------------------------------------------------------------------------- #
# Memoria temporal con TTL (en proceso, sin dependencias)
# --------------------------------------------------------------------------- #
class MemoriaTemporal:
    """
    Almacén clave→valor con expiración por entrada (TTL). Thread-safe.

    Pensado para conservar, por conversationId, los datos del inmueble y los
    intentos de contacto realizados durante el TTL.
    """

    def __init__(self, ttl_segundos: int = TTL_SEGUNDOS):
        self.ttl = ttl_segundos
        self._datos: dict[str, dict] = {}
        self._expira_en: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def _ahora(self) -> datetime:
        return datetime.now(timezone.utc)

    def _purgar(self) -> None:
        """Elimina las entradas vencidas."""
        ahora = self._ahora()
        vencidas = [k for k, exp in self._expira_en.items() if exp <= ahora]
        for k in vencidas:
            self._datos.pop(k, None)
            self._expira_en.pop(k, None)

    def get(self, clave: str) -> Optional[dict]:
        with self._lock:
            self._purgar()
            return self._datos.get(clave)

    def set(self, clave: str, valor: dict) -> None:
        with self._lock:
            self._datos[clave] = valor
            self._expira_en[clave] = self._ahora() + timedelta(seconds=self.ttl)

    def todos(self) -> dict[str, dict]:
        with self._lock:
            self._purgar()
            return dict(self._datos)


# Instancia global reutilizable (sobrevive entre llamadas dentro del mismo
# proceso). En n8n cada ejecución suele ser un proceso nuevo: ver nota del TTL.
_memoria_global = MemoriaTemporal()


# --------------------------------------------------------------------------- #
# Utilidades de fecha
# --------------------------------------------------------------------------- #
def _parse_fecha(valor: Any) -> Optional[datetime]:
    """
    Convierte un valor a datetime con timezone (UTC si no trae offset).

    Acepta:
      - datetime (se normaliza a aware/UTC),
      - ISO 8601 con o sin 'Z' ('2026-05-10', '2026-05-10T12:00:00Z', ...).
    Devuelve None si no se puede interpretar o el valor es vacío.
    """
    if valor is None or valor == "":
        return None

    if isinstance(valor, datetime):
        dt = valor
    elif isinstance(valor, str):
        texto = valor.strip()
        # fromisoformat no acepta el sufijo 'Z' en versiones antiguas.
        if texto.endswith("Z"):
            texto = texto[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(texto)
        except ValueError:
            # Último intento: solo fecha en formato YYYY/MM/DD
            try:
                dt = datetime.strptime(texto[:10], "%Y-%m-%d")
            except ValueError:
                logger.warning("Fecha no interpretable: %r", valor)
                return None
    else:
        logger.warning("Tipo de fecha no soportado: %r", type(valor))
        return None

    # Normaliza a timezone-aware en UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _a_bool(valor: Any) -> bool:
    """Normaliza un valor 'boolean-like' (True/'true'/1/'si'...)."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return valor == 1
    if isinstance(valor, str):
        return valor.strip().lower() in ("true", "1", "yes", "si", "sí")
    return False


def _dias_transcurridos(desde: Optional[datetime], ahora: datetime) -> Optional[float]:
    """Días (float) entre `desde` y `ahora`. None si `desde` es None."""
    if desde is None:
        return None
    return (ahora - desde).total_seconds() / 86400.0


# --------------------------------------------------------------------------- #
# Función principal
# --------------------------------------------------------------------------- #
def get_inmuebles_a_contactar(
    inmuebles_json: Any,
    ahora: Optional[datetime] = None,
    memoria: Optional[MemoriaTemporal] = None,
    registrar_intento: bool = True,
    enviar_fn=None,
) -> list[dict]:
    """
    Selecciona los inmuebles que cumplen los criterios de contacto y, si se le
    inyecta una utilidad de envío, ENVÍA la plantilla a cada uno.

    Parámetros
    ----------
    inmuebles_json : list[dict] | str
        Lista de inmuebles (o cadena JSON con la lista). Cada inmueble debe
        tener al menos: valor, direccion_inmueble, codigo, nombre_propietario,
        conversation_id, waid, tipo_inmueble, template_id, lastAvailability,
        lastMessageDate, estado. Opcional: intentos_realizados / intentos.
    ahora : datetime, opcional
        Momento de referencia (por defecto, ahora en UTC). Útil para pruebas.
    memoria : MemoriaTemporal, opcional
        Almacén con TTL a usar (por defecto, la instancia global).
    registrar_intento : bool
        Si True (por defecto), al contactar un inmueble se incrementa y guarda
        el intento en memoria. Si False, solo evalúa sin marcar.
    enviar_fn : callable, opcional
        Utilidad de envío. Si se pasa, por cada inmueble seleccionado se llama
        `enviar_fn(inmueble)` para enviar la plantilla. Debe devolver un dict
        con clave "exitoso" (o "success"). El intento solo se cuenta en memoria
        si el envío fue exitoso, de modo que un fallo se reintente luego.
        Se mantiene como parámetro (inyección) para que esta utilidad siga
        siendo independiente y sin dependencias de Django.

    Retorna
    -------
    list[dict]
        Inmuebles contactados con: conversation_id, nombre_propietario,
        direccion_inmueble, codigo, valor, intentos_realizados,
        fecha_ultimo_intento y, si hubo envío, `envio` (resultado de enviar_fn).
    """
    if ahora is None:
        ahora = datetime.now(timezone.utc)
    if memoria is None:
        memoria = _memoria_global

    # Aceptar tanto lista como cadena JSON.
    if isinstance(inmuebles_json, str):
        try:
            inmuebles_json = json.loads(inmuebles_json)
        except json.JSONDecodeError as e:
            logger.error("inmuebles_json no es JSON válido: %s", e)
            return []

    if not isinstance(inmuebles_json, list):
        logger.error("Se esperaba una lista de inmuebles, se recibió %s", type(inmuebles_json))
        return []

    seleccionados: list[dict] = []

    for idx, inmueble in enumerate(inmuebles_json):
        try:
            if not isinstance(inmueble, dict):
                logger.warning("Item %s ignorado (no es un objeto).", idx)
                continue

            conversation_id = str(
                inmueble.get("conversation_id")
                or inmueble.get("conversationId")
                or inmueble.get("waid")
                or ""
            ).strip()
            if not conversation_id:
                logger.warning("Item %s sin conversation_id/waid; se omite.", idx)
                continue

            # 1) Solo activos.
            if not _a_bool(inmueble.get("estado")):
                continue

            # 2) Disponibilidad confirmada con más de 20 días.
            last_availability = _parse_fecha(inmueble.get("lastAvailability"))
            dias_disp = _dias_transcurridos(last_availability, ahora)
            if dias_disp is not None and dias_disp <= DIAS_MIN_DISPONIBILIDAD:
                continue

            # Estado previo en memoria (si lo hay y sigue vivo).
            previo = memoria.get(conversation_id) or {}

            # 3) Intentos realizados: prioriza el dato de entrada (durable),
            #    luego la memoria, luego 0.
            intentos_entrada = inmueble.get("intentos_realizados", inmueble.get("intentos"))
            if intentos_entrada is not None:
                try:
                    intentos_previos = int(intentos_entrada)
                except (TypeError, ValueError):
                    intentos_previos = int(previo.get("intentos", 0))
            else:
                intentos_previos = int(previo.get("intentos", 0))

            if intentos_previos >= MAX_INTENTOS:
                continue

            # 4) Intervalo desde el último intento/mensaje (1 día por medio).
            #    Se usa la fecha más reciente entre el último intento guardado
            #    y lastMessageDate de la entrada.
            ultimo_intento = _parse_fecha(previo.get("fecha_ultimo_intento"))
            last_message = _parse_fecha(inmueble.get("lastMessageDate"))
            fechas_ref = [d for d in (ultimo_intento, last_message) if d is not None]
            referencia = max(fechas_ref) if fechas_ref else None

            if referencia is not None:
                dias_desde_ultimo = _dias_transcurridos(referencia, ahora)
                if dias_desde_ultimo is None or dias_desde_ultimo < INTERVALO_MIN_DIAS:
                    continue
            # Si no hay referencia, es el primer contacto → procede.

            # Cumple todos los criterios → se contacta.
            nuevos_intentos = intentos_previos + 1
            fecha_intento = ahora.isoformat()

            seleccionado = {
                "conversation_id": conversation_id,
                "nombre_propietario": inmueble.get("nombre_propietario"),
                "direccion_inmueble": inmueble.get("direccion_inmueble"),
                "codigo": inmueble.get("codigo"),
                "valor": inmueble.get("valor"),
                "intentos_realizados": nuevos_intentos,
                "fecha_ultimo_intento": fecha_intento,
            }

            # Envío de la plantilla usando la utilidad inyectada (si la hay).
            # Sin enviar_fn, la función solo selecciona (comportamiento previo).
            enviado_ok = True
            if enviar_fn is not None:
                try:
                    envio = enviar_fn(inmueble) or {}
                except Exception as e:  # un fallo de envío no aborta el lote
                    logger.exception("Error enviando plantilla a %s: %s", conversation_id, e)
                    envio = {"exitoso": False, "error": str(e)}
                seleccionado["envio"] = envio
                enviado_ok = bool(envio.get("exitoso", envio.get("success", False)))

            # El intento se cuenta solo si no hubo envío fallido (permite reintento).
            if registrar_intento and enviado_ok:
                memoria.set(
                    conversation_id,
                    {
                        "conversation_id": conversation_id,
                        "codigo": inmueble.get("codigo"),
                        "nombre_propietario": inmueble.get("nombre_propietario"),
                        "direccion_inmueble": inmueble.get("direccion_inmueble"),
                        "valor": inmueble.get("valor"),
                        "intentos": nuevos_intentos,
                        "fecha_ultimo_intento": fecha_intento,
                    },
                )

            seleccionados.append(seleccionado)

        except Exception as e:  # nunca abortar todo el lote por un item malo
            logger.exception("Error procesando el item %s: %s", idx, e)
            continue

    return seleccionados


# --------------------------------------------------------------------------- #
# Diagnóstico: por qué cada inmueble se contacta o se descarta
# --------------------------------------------------------------------------- #
def diagnosticar_inmuebles(inmuebles_json, ahora=None):
    """
    Igual que `get_inmuebles_a_contactar` pero NO envía ni registra nada:
    devuelve, por cada inmueble, si se contactaría y el motivo de descarte.

    Útil para entender "por qué solo N". Devuelve:
        {"resumen": {motivo: cantidad, ...}, "detalle": [ {...}, ... ]}
    """
    if ahora is None:
        ahora = datetime.now(timezone.utc)
    if isinstance(inmuebles_json, str):
        inmuebles_json = json.loads(inmuebles_json)

    detalle = []
    resumen = {}

    for idx, inmueble in enumerate(inmuebles_json or []):
        codigo = (inmueble or {}).get("codigo") if isinstance(inmueble, dict) else None
        cid = ""
        motivo = "CONTACTAR"

        if not isinstance(inmueble, dict):
            motivo = "no_es_objeto"
        else:
            cid = str(inmueble.get("conversation_id") or inmueble.get("conversationId")
                      or inmueble.get("waid") or "").strip()
            dias_disp = _dias_transcurridos(_parse_fecha(inmueble.get("lastAvailability")), ahora)
            intentos = inmueble.get("intentos_realizados", inmueble.get("intentos")) or 0
            try:
                intentos = int(intentos)
            except (TypeError, ValueError):
                intentos = 0
            ref = _parse_fecha(inmueble.get("lastMessageDate"))
            dias_msg = _dias_transcurridos(ref, ahora)

            if not cid:
                motivo = "sin_conversation_id"
            elif not _a_bool(inmueble.get("estado")):
                motivo = "inactivo"
            elif dias_disp is not None and dias_disp <= DIAS_MIN_DISPONIBILIDAD:
                motivo = f"disponibilidad_<={DIAS_MIN_DISPONIBILIDAD}d ({dias_disp:.1f}d)"
            elif intentos >= MAX_INTENTOS:
                motivo = f"max_intentos ({intentos})"
            elif dias_msg is not None and dias_msg < INTERVALO_MIN_DIAS:
                motivo = f"intervalo_<{INTERVALO_MIN_DIAS}d ({dias_msg:.1f}d)"

        resumen[motivo] = resumen.get(motivo, 0) + 1
        detalle.append({"i": idx, "codigo": codigo, "conversation_id": cid, "motivo": motivo})

    return {"resumen": resumen, "detalle": detalle}


# --------------------------------------------------------------------------- #
# Ejemplo de uso
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # Fecha de referencia fija para que el ejemplo sea reproducible.
    ahora = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)

    # NOTA: el único número de PRUEBA es 3122458770. Es el único inmueble que
    # resulta "contactable"; el resto queda filtrado por las reglas, así que
    # aunque se inyecte un sender real, SOLO ese número podría recibir mensaje.
    NUMERO_PRUEBA = "3122458770"

    inmuebles_demo = [
        {  # ✅ Activo, 35 días de disponibilidad, sin contacto previo → se contacta
            "valor": "2500000", "direccion_inmueble": "CL 80 52 A 90 LC 101",
            "codigo": "AC-75360", "nombre_propietario": "PROPIETARIO PRUEBA",
            "conversation_id": NUMERO_PRUEBA, "waid": NUMERO_PRUEBA,
            "tipo_inmueble": "", "template_id": "01KRKJXQ0NWVVKEQSDVH9VRP76",
            "lastAvailability": "2026-04-29", "lastMessageDate": None, "estado": True,
        },
        {  # ❌ Inactivo → se omite (nunca se contacta)
            "valor": "2200000", "direccion_inmueble": "KR 50 A 39 11",
            "codigo": "AC-75396", "nombre_propietario": "DEMO INACTIVO",
            "conversation_id": "DEMO-INACTIVO", "waid": "DEMO-INACTIVO",
            "tipo_inmueble": "", "template_id": "01KRKJXQ0NWVVKEQSDVH9VRP76",
            "lastAvailability": "2026-03-01", "lastMessageDate": None, "estado": False,
        },
        {  # ❌ Disponibilidad de solo 5 días (<20) → se omite
            "valor": "1800000", "direccion_inmueble": "CL 19 26 50 AP 207",
            "codigo": "AC-73340", "nombre_propietario": "DEMO RECIENTE",
            "conversation_id": "DEMO-RECIENTE", "waid": "DEMO-RECIENTE",
            "tipo_inmueble": "", "template_id": "01KRKJXQ0NWVVKEQSDVH9VRP76",
            "lastAvailability": "2026-05-29", "lastMessageDate": None, "estado": True,
        },
        {  # ❌ Ya tiene 3 intentos → se omite
            "valor": "9000000", "direccion_inmueble": "DG 74 B 32 91 LC 9",
            "codigo": "AC-75283", "nombre_propietario": "DEMO MAX INTENTOS",
            "conversation_id": "DEMO-MAXINTENTOS", "waid": "DEMO-MAXINTENTOS",
            "tipo_inmueble": "", "template_id": "01KRKJXQ0NWVVKEQSDVH9VRP76",
            "lastAvailability": "2026-03-15", "lastMessageDate": "2026-06-02",
            "estado": True, "intentos_realizados": 3,
        },
        {  # ❌ Último mensaje hoy (<1 día) → aún no corresponde
            "valor": "5800000", "direccion_inmueble": "CL 50 SUR 48 60 AP 2501",
            "codigo": "AC-75227", "nombre_propietario": "DEMO INTERVALO",
            "conversation_id": "DEMO-INTERVALO", "waid": "DEMO-INTERVALO",
            "tipo_inmueble": "", "template_id": "01KRKJXQ0NWVVKEQSDVH9VRP76",
            "lastAvailability": "2026-03-15", "lastMessageDate": "2026-06-03",
            "estado": True, "intentos_realizados": 1,
        },
    ]

    # Solo SELECCIÓN (sin enviar_fn) → NO envía nada, solo muestra a quién tocaría.
    resultado = get_inmuebles_a_contactar(inmuebles_demo, ahora=ahora)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


# =========================================================================== #
# Integración con n8n (nodo "Code" en modo Python)
# =========================================================================== #
#
# n8n permite un nodo Code con "Language: Python (Beta)". Pega el contenido de
# este archivo (sin el bloque `if __name__ == "__main__"`) en el nodo y al
# final agrega:
#
#     items_in = [i["json"] for i in items]          # inmuebles desde el nodo previo
#     a_contactar = get_inmuebles_a_contactar(items_in)
#     return [{"json": x} for x in a_contactar]       # salida para el siguiente nodo
#
# Consideraciones:
#   - El nodo Python de n8n se ejecuta aislado: la memoria en proceso NO
#     persiste entre ejecuciones. Para el conteo durable de intentos, incluye
#     `intentos_realizados` en cada inmueble (consultándolo de tu DB) y/o usa
#     el endpoint /conversacion de este proyecto como memoria compartida.
#   - Si tu n8n no tiene el runtime de Python, replica esta lógica en un nodo
#     "Code" JavaScript, o llama a esta utilidad vía un pequeño endpoint HTTP.
