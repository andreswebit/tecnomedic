from flask import (
    Flask, render_template, request,
    redirect, url_for, session, jsonify
)
from functools import wraps
import gspread
import json, time
from google.oauth2.service_account import Credentials
import requests as http_req
import os, re, logging
from datetime import datetime, timedelta, date
from dotenv import load_dotenv

# ── Scheduler ──────────────────────────────────────────────────────
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static",
            static_url_path="/static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# ── Credenciales desde variables de entorno ────────────────────────
ADMIN_USER     = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
GMAIL_USER    = os.environ.get("GMAIL_USER", "")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")


# URL pública para el ping keepalive (configurar en Render -> Environment)
PING_URL = os.environ.get("PING_URL", "https://tecnomedic.onrender.com")

HORARIOS        = ["08:30", "09:45", "11:00", "16:30", "17:45", "19:00"]
MAX_POR_HORARIO = 2

IDX = {
    "nombre":      0, "apellido":   1, "dni":        2,
    "obra_social": 3, "telefono":   4, "email":      5,
    "fecha":       6, "hora":       7, "estado":     8,
}
COL = {k: v + 1 for k, v in IDX.items()}
COLS_CANON = ['Nombre','Apellido','DNI','ObraSocial','Telefono','Email','Fecha','Hora','Estado']

# ── Google Sheets ──────────────────────────────────────────────────
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
_google_creds_raw = os.environ.get("GOOGLE_CREDS_JSON", "")
if not _google_creds_raw:
    raise RuntimeError(
        "Falta GOOGLE_CREDS_JSON en Render -> Environment. "
        "Pega el JSON de la Service Account completo."
    )
try:
    _creds_dict = json.loads(_google_creds_raw)
except json.JSONDecodeError as e:
    raise RuntimeError(f"GOOGLE_CREDS_JSON no es JSON valido: {e}")

creds   = Credentials.from_service_account_info(_creds_dict, scopes=scope)
gclient = gspread.authorize(creds)
sheet   = gclient.open("Turnos TECNOMEDIC").sheet1
log.info("Google Sheets conectado OK")


def sheets_update(rango, valores, reintentos=3):
    """Update con reintento automatico ante errores SSL transitorios de Python 3.14."""
    for intento in range(reintentos):
        try:
            sheet.update(rango, valores)
            return True
        except Exception as e:
            msg = str(e)
            es_ssl = any(k in msg for k in ["SSL", "ssl", "DECRYPTION", "bad record", "certificate"])
            if es_ssl and intento < reintentos - 1:
                log.warning(f"SSL error Sheets intento {intento+1}/{reintentos}, reintentando en 2s...")
                time.sleep(2)
            else:
                log.error(f"Error Sheets update [{type(e).__name__}]: {e}")
                raise


def sheets_get_all(reintentos=3):
    for intento in range(reintentos):
        try:
            return sheet.get_all_values()
        except Exception as e:
            msg = str(e)
            es_ssl = any(k in msg for k in ["SSL", "ssl", "DECRYPTION", "bad record", "certificate"])
            if es_ssl and intento < reintentos - 1:
                log.warning(f"SSL error get_all intento {intento+1}, reintentando...")
                time.sleep(2)
            else:
                log.error(f"Error Sheets get_all: {e}")
                raise
    return []


# ── Bot WhatsApp ───────────────────────────────────────────────────
try:
    from bot_wa import procesar as wa_procesar
    BOT_OK = True
    log.info("bot_wa importado OK")
except Exception as e:
    BOT_OK = False
    log.error(f"No se pudo importar bot_wa: {e}")


# ══════════════════════════════════════════════════════════════════
# EMAIL — Brevo API (HTTP, nunca bloqueado por Render)
# Registro gratis en brevo.com → 300 emails/dia sin costo
# ══════════════════════════════════════════════════════════════════

def enviar_email(destinatario: str, asunto: str, cuerpo_txt: str, cuerpo_html: str = "") -> bool:
    if not BREVO_API_KEY:
        log.warning("EMAIL NO CONFIGURADO: agregar BREVO_API_KEY en Render -> Environment")
        return False
    try:
        log.info(f"Enviando email via Brevo -> {destinatario}")
        payload = {
            "sender":      {"name": "TECNOMEDIC Turnos", "email": GMAIL_USER or "noreply@tecnomedic.com.ar"},
            "to":          [{"email": destinatario}],
            "subject":     asunto,
            "textContent": cuerpo_txt,
        }
        if cuerpo_html:
            payload["htmlContent"] = cuerpo_html
        r = http_req.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
            json=payload, timeout=15
        )
        if r.status_code in (200, 201):
            log.info(f"Email enviado OK a {destinatario}")
            return True
        log.error(f"Brevo error {r.status_code}: {r.text}")
        return False
    except Exception as e:
        log.error(f"Error email Brevo [{type(e).__name__}]: {e}")
        return False


def _html_email(titulo: str, nombre: str, cuerpo: str, color_titulo: str = "#00b4d8") -> str:
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 20px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0"
        style="background:#ffffff;border-radius:16px;overflow:hidden;
                box-shadow:0 4px 24px rgba(0,0,0,.10);">

    <!-- Cabecera -->
    <tr><td style="background:linear-gradient(135deg,#0a2540,#023e6e);
                    padding:32px 40px;text-align:center;">
    <div style="color:#00b4d8;font-size:11px;font-weight:700;
                letter-spacing:3px;text-transform:uppercase;margin-bottom:8px;">
        TECNOMEDIC
    </div>
    <div style="color:#ffffff;font-size:22px;font-weight:700;">{titulo}</div>
    <div style="color:rgba(255,255,255,.5);font-size:12px;margin-top:6px;">
        Centro de Salud · Cámara Hiperbárica
    </div>
    </td></tr>

    <!-- Cuerpo -->
    <tr><td style="padding:36px 40px;">
    <p style="margin:0 0 20px;color:#1e3a5f;font-size:16px;">
        Hola <strong>{nombre}</strong>,
    </p>
    {cuerpo}
    <hr style="border:none;border-top:1px solid #e8edf2;margin:28px 0;">
    <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
        <td style="color:#64748b;font-size:12px;">
            <strong style="color:#0a2540;">TECNOMEDIC</strong><br>
            📍 C. Pellegrini 799, Corrientes<br>
            📞 (3794) 34-9278
        </td>
        <td align="right">
            <div style="width:40px;height:40px;background:linear-gradient(135deg,#0a2540,#023e6e);
                        border-radius:10px;display:inline-flex;align-items:center;
                        justify-content:center;font-size:20px;line-height:40px;text-align:center;">
            🏥
            </div>
        </td>
        </tr>
    </table>
    </td></tr>

    <!-- Pie -->
    <tr><td style="background:#f8fafc;padding:16px 40px;text-align:center;
                    color:#94a3b8;font-size:11px;">
    Este es un mensaje automático. No respondas este email.
    </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


def _bloque_turno(fecha: str, hora: str, extra: str = "") -> str:
    return f"""
    <div style="background:#f0f9ff;border-left:4px solid #00b4d8;
                border-radius:0 12px 12px 0;padding:20px 24px;margin:20px 0;">
        <div style="color:#0a2540;font-size:15px;margin-bottom:8px;">
        📅 <strong>Fecha:</strong> {fecha}
        </div>
        <div style="color:#0a2540;font-size:15px;">
        ⏰ <strong>Hora:</strong> {hora}hs
        </div>
        {('<div style="color:#0a2540;font-size:15px;margin-top:8px;">'+extra+'</div>') if extra else ''}
    </div>"""


def email_solicitud(data: dict):
    nombre = f"{data.get('nombre','').title()} {data.get('apellido','').title()}".strip()
    cuerpo_txt = (
        f"Hola {nombre},\n\n"
        f"Recibimos tu solicitud de turno para Camara Hiperbarica.\n\n"
        f"Fecha: {data['fecha']}\nHora: {data['hora']}hs\n\n"
        f"Te confirmaremos a la brevedad.\n\nTECNOMEDIC - (3794) 34-9278"
    )
    cuerpo_html = _html_email(
        "Solicitud de turno recibida", nombre,
        f"<p style='color:#475569;font-size:14px;line-height:1.7;'>"
        f"Recibimos tu solicitud de turno para <strong>Cámara Hiperbárica</strong>. "
        f"Te confirmaremos a la brevedad.</p>"
        + _bloque_turno(data['fecha'], data['hora'])
        + "<p style='color:#64748b;font-size:13px;margin-top:16px;'>"
        f"⏳ Estado actual: <strong>Pendiente de confirmación</strong></p>"
    )
    enviar_email(data["email"], "Solicitud de turno recibida – TECNOMEDIC", cuerpo_txt, cuerpo_html)


def email_confirmacion(nombre: str, email: str, fecha: str, hora: str):
    cuerpo_txt = (
        f"Hola {nombre},\n\nTu turno fue CONFIRMADO.\n\n"
        f"Fecha: {fecha}\nHora: {hora}hs\n\n"
        f"C. Pellegrini 799, Corrientes\nTel: (3794) 34-9278\n\nTe esperamos!"
    )
    cuerpo_html = _html_email(
        "✔️ Turno Confirmado", nombre,
        "<p style='color:#475569;font-size:14px;line-height:1.7;'>"
        "Tu turno fue <strong style='color:#16a34a;'>CONFIRMADO</strong>. "
        "¡Te esperamos!</p>"
        + _bloque_turno(fecha, hora)
        + "<p style='color:#64748b;font-size:13px;margin-top:16px;'>"
        "📍 C. Pellegrini 799, Corrientes &nbsp;·&nbsp; 📞 (3794) 34-9278</p>"
    )
    enviar_email(email, "✔️ Turno confirmado – TECNOMEDIC", cuerpo_txt, cuerpo_html)


def email_modificacion(nombre: str, email: str, fecha: str, hora: str):
    cuerpo_txt = (
        f"Hola {nombre},\n\nTu turno fue MODIFICADO.\n\n"
        f"Nueva fecha: {fecha}\nNueva hora: {hora}hs\n\n"
        f"Consultas: (3794) 34-9278\n\nTECNOMEDIC"
    )
    cuerpo_html = _html_email(
        "✏️ Turno Modificado", nombre,
        "<p style='color:#475569;font-size:14px;line-height:1.7;'>"
        "Tu turno fue <strong>modificado</strong>. "
        "Los nuevos datos son:</p>"
        + _bloque_turno(fecha, hora)
        + "<p style='color:#64748b;font-size:13px;margin-top:16px;'>"
        "Ante cualquier consulta llamanos al 📞 (3794) 34-9278</p>"
    )
    enviar_email(email, "✏️ Turno modificado – TECNOMEDIC", cuerpo_txt, cuerpo_html)





# ══════════════════════════════════════════════════════════════════
# WHATSAPP — Twilio
# ══════════════════════════════════════════════════════════════════

def formatear_wa(telefono: str) -> str:
    d = re.sub(r"\D", "", telefono)
    if d.startswith("54"):
        if not d.startswith("549"): d = "549" + d[2:]
    elif d.startswith("0"):
        d = "549" + d[1:]
    else:
        d = "549" + d
    return f"whatsapp:+{d}"


def enviar_whatsapp(telefono: str, mensaje: str) -> bool:
    if not TWILIO_SID or not TWILIO_TOKEN:
        log.warning("WHATSAPP NO CONFIGURADO: verificar TWILIO_* en Render")
        return False
    try:
        wa_to = formatear_wa(telefono)
        log.info(f"Enviando WA -> {wa_to}")
        r = http_req.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"From": TWILIO_WA_FROM, "To": wa_to, "Body": mensaje},
            auth=(TWILIO_SID, TWILIO_TOKEN), timeout=10
        )
        if r.status_code == 201:
            log.info(f"WA enviado OK a {wa_to}")
            return True
        log.error(f"Twilio error {r.status_code}: {r.text}")
        return False
    except Exception as e:
        log.error(f"Error WA [{type(e).__name__}]: {e}")
        return False


# ══════════════════════════════════════════════════════════════════
# DISPONIBILIDAD
# ══════════════════════════════════════════════════════════════════

def get_ocupados(fecha_hoja: str) -> dict:
    conteo = {h: 0 for h in HORARIOS}
    try:
        rows = sheets_get_all()
        if len(rows) < 2: return conteo
        i_f = IDX["fecha"]; i_h = IDX["hora"]; i_e = IDX["estado"]
        for row in rows[1:]:
            if len(row) <= i_e: continue
            if row[i_f].strip() != fecha_hoja: continue
            if row[i_e].strip().lower() == "cancelado": continue
            hora = row[i_h].strip()
            if hora in conteo: conteo[hora] += 1
    except Exception as e:
        log.error(f"Error get_ocupados: {e}")
    return conteo


# ══════════════════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("usuario", "").strip()
        p = request.form.get("password", "").strip()
        if u == ADMIN_USER and p == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("admin"))
        error = "Usuario o contrasena incorrectos."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ══════════════════════════════════════════════════════════════════
# RUTAS PUBLICAS
# ══════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/turnos")
def turnos():
    return render_template("form.html")


@app.route("/api/horarios")
def api_horarios():
    from datetime import datetime as dt
    fecha_raw = request.args.get("fecha", "")
    if not fecha_raw:
        return jsonify({"error": "fecha requerida"}), 400
    try:
        fecha_hoja = dt.strptime(fecha_raw, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return jsonify({"error": "formato invalido, usar YYYY-MM-DD"}), 400
    ocupados = get_ocupados(fecha_hoja)
    slots = []
    for h in HORARIOS:
        c = ocupados.get(h, 0)
        slots.append({
            "hora": h, "ocupados": c, "max": MAX_POR_HORARIO,
            "disponible": c < MAX_POR_HORARIO, "libres": MAX_POR_HORARIO - c
        })
    return jsonify({"fecha": fecha_hoja, "slots": slots})


@app.route("/guardar", methods=["POST"])
def guardar():
    data = {
        "nombre":      request.form.get("nombre", "").strip().title(),
        "apellido":    request.form.get("apellido", "").strip().title(),
        "dni":         request.form.get("dni", "").strip(),
        "obra_social": request.form.get("obra_social", "").strip(),
        "telefono":    request.form.get("telefono", "").strip(),
        "email":       request.form.get("email", "").strip(),
        "fecha":       request.form.get("fecha", "").strip(),
        "hora":        request.form.get("hora", "").strip(),
    }
    if not all(data[k] for k in ["nombre","apellido","telefono","email","fecha","hora"]):
        return render_template("form.html", error="Por favor completa todos los campos obligatorios.")

    ocupados = get_ocupados(data["fecha"])
    if ocupados.get(data["hora"], 0) >= MAX_POR_HORARIO:
        return render_template("form.html", error="Ese horario ya no tiene lugares. Por favor elegi otro.")

    sheet.append_row([
        data["nombre"], data["apellido"], data["dni"], data["obra_social"],
        data["telefono"], data["email"], data["fecha"], data["hora"], "Pendiente"
    ])
    log.info(f"Turno guardado: {data['nombre']} {data['apellido']} | {data['fecha']} {data['hora']}")

    try: email_solicitud(data)
    except Exception as e: log.error(f"Error email solicitud: {e}")

    try:
        enviar_whatsapp(data["telefono"],
            f"TECNOMEDIC - Turno recibido\n\n"
            f"Hola {data['nombre']}!\n"
            f"Fecha: {data['fecha']}  Hora: {data['hora']}hs\n\n"
            f"Te confirmaremos a la brevedad. Gracias!"
        )
    except Exception as e: log.error(f"Error WA solicitud: {e}")

    return render_template("confirmacion.html", turno=data)


# ══════════════════════════════════════════════════════════════════
# ADMIN
# ══════════════════════════════════════════════════════════════════

@app.route("/admin")
@login_required
def admin():
    try:
        rows = sheets_get_all()
        if not rows:
            return render_template("admin.html", turnos=[], total=0, confirmados=0, pendientes=0)
        headers = rows[0]
        turnos  = []
        for i, row in enumerate(rows[1:]):
            row_ext = list(row) + [""] * max(0, len(COLS_CANON) - len(row))
            t = dict(zip(headers, row_ext))
            for idx, key in enumerate(COLS_CANON):
                if key not in t:
                    t[key] = row_ext[idx] if idx < len(row_ext) else ""
            t["row"] = i + 2
            turnos.append(t)
        total       = len(turnos)
        confirmados = sum(1 for t in turnos if t.get("Estado") == "Confirmado")
        pendientes  = sum(1 for t in turnos if t.get("Estado") == "Pendiente")
        return render_template("admin.html", turnos=turnos,
                                total=total, confirmados=confirmados, pendientes=pendientes)
    except Exception as e:
        log.error(f"Error admin: {e}")
        return f"Error al leer la hoja: {e}", 500


@app.route("/actualizar", methods=["POST"])
@login_required
def actualizar():
    row    = int(request.form["row"])
    estado = request.form["estado"]
    sheet.update_cell(row, COL["estado"], estado)
    log.info(f"Estado fila {row} -> {estado}")
    if estado == "Confirmado":
        try:
            fila   = sheets_get_all()[row - 1]
            nombre = f"{fila[IDX['nombre']]} {fila[IDX['apellido']]}".strip()
            tel    = fila[IDX["telefono"]]
            email  = fila[IDX["email"]]
            fecha  = fila[IDX["fecha"]]
            hora   = fila[IDX["hora"]]
            email_confirmacion(nombre, email, fecha, hora)
            enviar_whatsapp(tel,
                f"TECNOMEDIC - Turno CONFIRMADO\n\n"
                f"Hola {fila[IDX['nombre']]}! Tu turno fue CONFIRMADO.\n\n"
                f"Fecha: {fecha}  Hora: {hora}hs\n"
                f"Direccion: C. Pellegrini 799, Corrientes\n"
                f"Tel: (3794) 34-9278\n\nTe esperamos!"
            )
        except Exception as e:
            log.error(f"Error notificando confirmacion: {e}")
    return redirect(url_for("admin") + "?guardado=1")


@app.route("/modificar", methods=["POST"])
@login_required
def modificar():
    row          = int(request.form["row"])
    nuevo_nombre = request.form.get("nombre", "").strip()
    apellido     = request.form.get("apellido", "").strip()
    dni          = request.form.get("dni", "").strip()
    obra_social  = request.form.get("obra_social", "").strip()
    telefono     = request.form.get("telefono", "").strip()
    email        = request.form.get("email", "").strip()
    fecha_raw    = request.form.get("fecha", "").strip()
    hora         = request.form.get("hora", "").strip()
    estado       = request.form.get("estado", "").strip()

    # Convertir fecha de YYYY-MM-DD (HTML) a DD/MM/YYYY (Sheets)
    try:
        from datetime import datetime as _dt
        if "-" in fecha_raw:
            fecha = _dt.strptime(fecha_raw, "%Y-%m-%d").strftime("%d/%m/%Y")
        else:
            fecha = fecha_raw  # ya viene en DD/MM/YYYY
    except ValueError:
        fecha = fecha_raw

    try:
        sheets_update(
            f'A{row}:I{row}',
            [[nuevo_nombre, apellido, dni, obra_social, telefono, email, fecha, hora, estado]]
        )
        log.info(f"Turno fila {row} modificado: {nuevo_nombre} {apellido} | {fecha} {hora} | {estado}")
    except Exception as e:
        log.error(f"Error actualizando Sheets en /modificar: {e}")

    nombre_completo = f"{nuevo_nombre} {apellido}".strip()

    if estado == "Confirmado":
        try: email_confirmacion(nombre_completo, email, fecha, hora)
        except Exception as e: log.error(f"Error email confirmacion: {e}")
        try:
            enviar_whatsapp(telefono,
                f"TECNOMEDIC - Turno CONFIRMADO\n\n"
                f"Hola {nuevo_nombre}! Tu turno fue CONFIRMADO.\n\n"
                f"Fecha: {fecha}  Hora: {hora}hs\n"
                f"C. Pellegrini 799, Corrientes - Tel: (3794) 34-9278\n\nTe esperamos!"
            )
        except Exception as e: log.error(f"Error WA confirmacion: {e}")

    elif estado == "Pendiente":
        try: email_modificacion(nombre_completo, email, fecha, hora)
        except Exception as e: log.error(f"Error email modificacion: {e}")
        try:
            enviar_whatsapp(telefono,
                f"TECNOMEDIC - Turno modificado\n\n"
                f"Hola {nuevo_nombre}! Tu turno fue reprogramado.\n\n"
                f"Nueva fecha: {fecha}  Nueva hora: {hora}hs\n\n"
                f"Te confirmaremos a la brevedad. Tel: (3794) 34-9278"
            )
        except Exception as e: log.error(f"Error WA modificacion: {e}")

    elif estado == "Cancelado":
        try:
            enviar_whatsapp(telefono,
                f"TECNOMEDIC - Turno cancelado\n\n"
                f"Hola {nuevo_nombre}, tu turno del {fecha} a las {hora}hs fue cancelado.\n\n"
                f"Para sacar otro turno escribinos o llama al (3794) 34-9278."
            )
        except Exception as e: log.error(f"Error WA cancelacion: {e}")

    return redirect(url_for("admin") + "?guardado=1")


@app.route("/eliminar", methods=["POST"])
@login_required
def eliminar():
    row = int(request.form["row"])
    sheet.delete_rows(row)
    log.info(f"Turno fila {row} eliminado")
    return redirect(url_for("admin") + "?guardado=1")

# --- TU RUTA DE PING  ---
@app.route('/ping', methods=['GET'])
def ping():
    # Devuelve un texto plano y un código 200 OK sin cargar HTML pesado
    return "OK", 200
# ══════════════════════════════════════════════════════════════════
# BOT WHATSAPP
# ══════════════════════════════════════════════════════════════════

@app.route("/whatsapp/bot", methods=["POST"])
def whatsapp_bot():
    phone = request.form.get("From", "").strip()
    msg   = request.form.get("Body", "").strip()
    if not phone or not msg:
        return '<Response></Response>', 200, {'Content-Type': 'text/xml'}
    log.info(f"WA recibido de {phone}: {msg[:60]}")
    if BOT_OK:
        try:
            wa_procesar(phone, msg, sheet)
        except Exception as e:
            log.error(f"Error en bot WA: {e}")
    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}

@app.route('/tienda')
def tienda():
    return render_template('tienda_section.html')


# ══════════════════════════════════════════════════════════════════
# CRON — Recordatorio automático día anterior
# Corre todos los días a las 09:00 hora Argentina (UTC-3)
# Busca turnos de mañana con estado Confirmado o Pendiente
# y envía email + WhatsApp a cada paciente
# ══════════════════════════════════════════════════════════════════

def email_recordatorio(nombre: str, email: str, fecha: str, hora: str):
    """Email de recordatorio 24hs antes del turno."""
    cuerpo_txt = (
        f"Hola {nombre},\n\n"
        f"Te recordamos que mañana tenés turno en TECNOMEDIC.\n\n"
        f"Fecha: {fecha}\nHora: {hora}hs\n\n"
        f"Dirección: C. Pellegrini 799, Corrientes\n"
        f"Tel: (3794) 34-9278\n\n"
        f"Si necesitás cancelar o reprogramar, avisanos con anticipación.\n\n"
        f"¡Te esperamos! — TECNOMEDIC"
    )
    cuerpo_html = _html_email(
        "🔔 Recordatorio de turno", nombre,
        "<p style='color:#475569;font-size:14px;line-height:1.7;'>"
        "Te recordamos que <strong>mañana tenés turno</strong> "
        "en TECNOMEDIC Cámara Hiperbárica.</p>"
        + _bloque_turno(fecha, hora)
        + "<p style='color:#64748b;font-size:13px;margin-top:16px;'>"
        "📍 C. Pellegrini 799, Corrientes &nbsp;·&nbsp; 📞 (3794) 34-9278</p>"
        + "<p style='color:#94a3b8;font-size:12px;margin-top:12px;'>"
        "Si necesitás cancelar o reprogramar, contactanos con anticipación.</p>",
        color_titulo="#1aa5a5"
    )
    return enviar_email(
        email,
        "🔔 Recordatorio: tu turno en TECNOMEDIC es mañana",
        cuerpo_txt,
        cuerpo_html
    )


def recordatorio_manana():
    """
    Tarea programada: envía recordatorios a todos los pacientes
    con turno mañana (Confirmado o Pendiente).
    Se ejecuta diariamente a las 09:00 hora Argentina.
    """
    tz_arg  = pytz.timezone("America/Argentina/Buenos_Aires")
    hoy     = datetime.now(tz_arg).date()
    manana  = hoy + timedelta(days=1)
    # Formato que usa Google Sheets en este proyecto: DD/MM/YYYY
    fecha_str = manana.strftime("%d/%m/%Y")

    log.info(f"[CRON] Buscando turnos para mañana: {fecha_str}")

    try:
        rows = sheets_get_all()
    except Exception as e:
        log.error(f"[CRON] Error leyendo Sheets: {e}")
        return

    if len(rows) < 2:
        log.info("[CRON] Sin filas en la hoja, nada que hacer.")
        return

    enviados = 0
    errores  = 0

    for row in rows[1:]:
        # Rellenar columnas faltantes para no romper con índices
        r = list(row) + [""] * max(0, len(COLS_CANON) - len(row))

        fecha_turno = r[IDX["fecha"]].strip()
        estado      = r[IDX["estado"]].strip().lower()

        # Solo turnos de mañana, no cancelados
        if fecha_turno != fecha_str:
            continue
        if estado == "cancelado":
            continue

        nombre   = f"{r[IDX['nombre']].strip()} {r[IDX['apellido']].strip()}".strip()
        telefono = r[IDX["telefono"]].strip()
        email    = r[IDX["email"]].strip()
        hora     = r[IDX["hora"]].strip()

        log.info(f"[CRON] Enviando recordatorio a {nombre} | {fecha_str} {hora}hs")

        # ── Email ──────────────────────────────────────────────────
        if email:
            try:
                ok = email_recordatorio(nombre, email, fecha_str, hora)
                if ok:
                    log.info(f"[CRON] Email OK → {email}")
                else:
                    log.warning(f"[CRON] Email falló → {email}")
            except Exception as e:
                log.error(f"[CRON] Error email {email}: {e}")
                errores += 1

        # ── WhatsApp ───────────────────────────────────────────────
        if telefono:
            try:
                msg = (
                    f"🔔 *TECNOMEDIC - Recordatorio de turno*\n\n"
                    f"Hola {r[IDX['nombre']].strip()}! 👋\n"
                    f"Te recordamos que *mañana* tenés turno.\n\n"
                    f"📅 Fecha: {fecha_str}\n"
                    f"⏰ Hora: {hora}hs\n\n"
                    f"📍 C. Pellegrini 799, Corrientes\n"
                    f"📞 (3794) 34-9278\n\n"
                    f"Si necesitás cancelar, avisanos con anticipación. ¡Te esperamos!"
                )
                ok = enviar_whatsapp(telefono, msg)
                if ok:
                    log.info(f"[CRON] WA OK → {telefono}")
                else:
                    log.warning(f"[CRON] WA falló → {telefono}")
            except Exception as e:
                log.error(f"[CRON] Error WA {telefono}: {e}")
                errores += 1

        enviados += 1

    log.info(
        f"[CRON] Recordatorios finalizados: "
        f"{enviados} paciente(s) notificados, {errores} error(es)."
    )


# ── Ruta manual para disparar el cron desde el admin (opcional) ───

@app.route("/admin/recordatorio-manana", methods=["POST"])
@login_required
def disparar_recordatorio():
    """Permite ejecutar el recordatorio manualmente desde el panel admin."""
    try:
        recordatorio_manana()
        return redirect(url_for("admin") + "?recordatorio=1")
    except Exception as e:
        log.error(f"Error disparando recordatorio manual: {e}")
        return redirect(url_for("admin") + "?recordatorio_error=1")


# ── Iniciar el scheduler ──────────────────────────────────────────

def iniciar_scheduler():
    tz_arg    = pytz.timezone("America/Argentina/Buenos_Aires")
    scheduler = BackgroundScheduler(timezone=tz_arg)
    scheduler.add_job(
        func    = recordatorio_manana,
        trigger = CronTrigger(hour=9, minute=0, timezone=tz_arg),
        id      = "recordatorio_diario",
        name    = "Recordatorio turnos día siguiente",
        replace_existing = True,
        misfire_grace_time = 3600   # si Render tarda en despertar, tolera 1h de desfase
    )
    # Job keepalive: ping cada 10 minutos excepto entre las 02:00 y 03:59
    def ping_keepalive():
        if not PING_URL:
            log.warning("PING_URL no configurada; saltando ping keepalive")
            return False
        try:
            log.info(f"[PING] Enviando keepalive a {PING_URL}")
            r = http_req.get(PING_URL, timeout=10)
            if r.status_code < 400:
                log.info(f"[PING] OK {r.status_code}")
                return True
            log.warning(f"[PING] Respuesta no OK: {r.status_code}")
            return False
        except Exception as e:
            log.error(f"[PING] Error ping_keepalive: {e}")
            return False

    scheduler.add_job(
        func = ping_keepalive,
        trigger = CronTrigger(minute="*/10", hour="0-1,4-23", timezone=tz_arg),
        id = "keepalive_ping",
        name = "Keepalive ping cada 10 minutos (excluye 02:00-04:00)",
        replace_existing = True,
        misfire_grace_time = 120
    )
    scheduler.start()
    log.info("[CRON] Scheduler iniciado — recordatorio diario a las 09:00 ARG")
    return scheduler

# Arrancar el scheduler al cargar el módulo
# (gunicorn lo importa una sola vez por worker, no hay duplicados)
_scheduler = iniciar_scheduler()


if __name__ == "__main__":
    app.run(debug=True)