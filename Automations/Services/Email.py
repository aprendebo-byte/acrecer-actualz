from email.mime.text import MIMEText
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import os
from django.conf import settings
from dotenv import load_dotenv
from .Logging import log_evento

load_dotenv()

LOGO_URL = "https://www-img-cc.s3.amazonaws.com/usuarios/images/6989/6989160708417765_plana.jpeg"


def destinatarios_por_defecto():
    """
    Destinatarios de las notificaciones de negocio.

    Configurables por entorno (`NOTIFICACION_DESTINATARIOS`); antes la lista
    estaba repetida en cada función de este módulo.
    """
    return list(getattr(settings, "NOTIFICACION_DESTINATARIOS", []))


def _envoltorio_html(titulo, cuerpo):
    """Marco común de todos los correos: cabecera con logo, cuerpo y pie."""
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 20px;">
        <div style="max-width: 600px; margin: auto; background: white; border-radius: 10px;
                    overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

            <div style="background-color: #383736; padding: 20px; text-align: center;">
                <img src="{LOGO_URL}" alt="Logo" style="width: 160px; border-radius: 6px;">
            </div>

            <div style="padding: 30px; color: #333;">
                <h2 style="color: #383736; text-align: center;">{titulo}</h2>

                <p>Estimado(a),</p>

                {cuerpo}

                <br>
                <p style="font-size: 13px; color: #777;">
                    Este correo fue generado automáticamente por el sistema de
                    <b>Automatización de Inventario</b>.
                    No es necesario responder.
                </p>
            </div>
        </div>
    </body>
    </html>
    """


def _fila_datos(etiqueta, valor):
    """
    Una línea de la caja de datos.

    `etiqueta is None` inserta un separador; `etiqueta == ""` muestra solo el
    valor (texto libre, como el resumen de una intervención).
    """
    if etiqueta is None:
        return "<br>"
    if etiqueta == "":
        return f'<p style="margin: 5px 0;">{valor}</p>'
    return f'<p style="margin: 5px 0;"><b>{etiqueta}:</b> {valor}</p>'


def _caja_datos(filas, fondo="#f8f8f8", borde="#e5e5e5"):
    """Bloque gris con pares etiqueta/valor. `filas` es una lista de tuplas."""
    contenido = "".join(_fila_datos(etiqueta, valor) for etiqueta, valor in filas)
    return (
        f'<div style="background:{fondo}; padding:15px; border-radius:8px; '
        f'margin: 20px 0; border:1px solid {borde};">{contenido}</div>'
    )


def enviar_email_html(subject, destinatarios, html_body, adjuntos=None):
    from_addr = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASSWORD")

    if not from_addr or not password:
        log_evento(
            "email",
            "EMAIL_USER o EMAIL_PASSWORD no están configurados",
            nivel="ERROR",
        )
        return False

    if not destinatarios:
        log_evento(
            "email",
            "Sin destinatarios: revisa NOTIFICACION_DESTINATARIOS en el entorno.",
            nivel="ERROR",
            detalle={"asunto": subject},
        )
        return False

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = subject

    msg.attach(MIMEText(html_body, "html"))

    # Adjuntar archivos si existen
    if adjuntos:
        for archivo in adjuntos:
            if os.path.exists(archivo):
                with open(archivo, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f'attachment; filename="{os.path.basename(archivo)}"'
                    )
                    msg.attach(part)
            else:
                log_evento(
                    "email",
                    f"Adjunto no encontrado: {archivo}",
                    nivel="WARNING",
                )

    server = None
    try:
        server = smtplib.SMTP(
            getattr(settings, "EMAIL_SMTP_HOST", "smtp.gmail.com"),
            getattr(settings, "EMAIL_SMTP_PORT", 587),
            timeout=30,
        )
        server.starttls()
        server.login(from_addr, password)
        server.sendmail(from_addr, destinatarios, msg.as_string())
        log_evento(
            "email",
            f"Correo enviado: {subject}",
            detalle={"destinatarios": destinatarios},
        )
        return True
    except smtplib.SMTPAuthenticationError as e:
        detalle_smtp = e.smtp_error.decode(errors="ignore") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        # Auth SMTP rechazada (p. ej. app password de Gmail revocada/expirada).
        # Se registra como CRITICAL con evento propio para que NO pase inadvertido
        # como un ERROR más y pueda alertarse (antes falló en silencio 16 días).
        log_evento(
            "email_auth",
            "Fallo de autenticacion SMTP: credenciales rechazadas "
            "(revisa/rota EMAIL_PASSWORD - app password de Gmail). "
            f"Codigo {e.smtp_code}: {detalle_smtp}",
            nivel="CRITICAL",
            detalle={"asunto": subject, "destinatarios": destinatarios, "smtp_code": e.smtp_code},
            exc_info=True,
        )
        return False
    except Exception as e:
        log_evento(
            "email",
            f"Error enviando correo: {e}",
            nivel="ERROR",
            detalle={"asunto": subject, "destinatarios": destinatarios},
            exc_info=True,
        )
        return False
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass


def enviar_informe_historial(archivo_path, destinatarios=None):
    """Envía el informe xlsx del historial como adjunto."""
    destinos = destinatarios or destinatarios_por_defecto()

    cuerpo = """
                <p>
                    Adjunto encontrará el informe del historial de inventario generado
                    automáticamente por el <b>Agente de Actualización de Inventario</b>.
                    Incluye respuestas, actualizaciones de precio e intervenciones registradas.
                </p>
    """

    return enviar_email_html(
        subject="Informe de Inventario – Historial",
        destinatarios=destinos,
        html_body=_envoltorio_html("Informe de Inventario", cuerpo),
        adjuntos=[archivo_path] if archivo_path else None,
    )


def notificacion_inmueble_desactivado(codigo, direccion, propietario, destinatarios=None):
    destinos = destinatarios or destinatarios_por_defecto()

    cuerpo = f"""
                <p>
                    Un propietario ha reportado una novedad a través del
                    <b>Agente de Actualización de Inventario</b>.
                    Como resultado, el siguiente inmueble ha sido marcado como <b>Retirado</b>:
                </p>

                {_caja_datos([
                    ("Código del inmueble", codigo),
                    ("Dirección", direccion),
                    ("Propietario", propietario),
                ])}
    """

    return enviar_email_html(
        subject=f"Inmueble retirado – Código {codigo}",
        destinatarios=destinos,
        html_body=_envoltorio_html("Actualización de Inventario", cuerpo),
    )


def notificacion_actualizacion_valor(codigo, direccion, propietario, valor_actual,
                                     valor_nuevo, captador, destinatarios=None):
    destinos = destinatarios or destinatarios_por_defecto()

    cuerpo = f"""
                <p>
                    Un propietario ha solicitado actualizar el valor del inmueble a través del
                    <b>Agente de Actualización de Inventario</b>.
                    A continuación, se detalla la información del caso:
                </p>

                {_caja_datos([
                    ("Código del inmueble", codigo),
                    ("Dirección", direccion),
                    ("Propietario", propietario),
                    (None, None),
                    ("Valor actual", valor_actual),
                    ("Nuevo valor solicitado", valor_nuevo),
                    ("Captador", captador),
                ])}
    """

    return enviar_email_html(
        subject=f"Actualización de valor – Inmueble {codigo}",
        destinatarios=destinos,
        html_body=_envoltorio_html("Solicitud de Actualización de Valor", cuerpo),
    )


def notificar_intervencion_realizada(
    codigo,
    direccion,
    propietario,
    numero,
    captador,
    tipo_intervencion,
    resumen,
    sucursal,
    destinatarios=None,
):
    destinos = destinatarios or destinatarios_por_defecto()

    cuerpo = f"""
                <p>
                    Se informa que se ha detectado una <b>intervención sobre un inmueble</b>
                    a través del <b>Agente de Actualización de Inventario</b>.
                    A continuación, se detalla la información del caso:
                </p>

                {_caja_datos([
                    ("Código del inmueble", codigo),
                    ("Dirección", direccion),
                    ("Propietario", propietario),
                    ("Número de contacto", numero),
                    ("Número de captador", captador),
                    ("Sucursal", sucursal),
                    (None, None),
                    ("Tipo de intervención", tipo_intervencion),
                ])}

                <p>Resumen de la intervención:</p>
                {_caja_datos([("", resumen)], fondo="#e8e8e8", borde="#d5d5d5")}

                <p>
                    La intervención fue detectada; por favor ingrese al sistema Gamma
                    y revise el chat del propietario.
                </p>
    """

    return enviar_email_html(
        subject=f"Intervención detectada – Inmueble {codigo}",
        destinatarios=destinos,
        html_body=_envoltorio_html("Intervención Detectada sobre Inmueble", cuerpo),
    )
