# TECNOMEDIC – Sistema de Turnos

Automatización de turnos para Cámara Hiperbárica. Este proyecto contiene el frontend web, el backend en Flask y la integración con Google Sheets, email Brevo y opcionalmente WhatsApp.

---

## Manual de uso del proyecto

Este README es una guía práctica para usar, ejecutar y desplegar el proyecto.

### Qué hace esta app

- muestra una página principal desde `templates/index.html`
- permite solicitar turnos en `/turnos`
- guarda los turnos en una planilla de Google Sheets
- notifica por email usando Brevo
- notifica por WhatsApp si está configurado
- ofrece panel de administración protegido en `/admin`
- permite confirmar, modificar y eliminar turnos
- tiene un endpoint público para consultar horarios libres

---

## Estructura actual del proyecto

```
tecnomedic/
├── app.py
├── bot_wa.py
├── credenciales.json
├── gunicorn.conf.py
├── Procfile
├── render.yaml
├── requirements.txt
├── runtime.txt
├── tecnomedic.conf
├── tecnomedic.service
├── update.sh
├── guia_dns_nicar.md
├── guia_donweb.md
├── guia_github_render.md
├── README.md
├── .gitignore
├── .python-version
├── static/
│   ├── all.min.css
│   ├── CLAUDE.md
│   ├── tecnomedic - copia.css
│   ├── tecnomedic.css
│   ├── img/
│   │   ├── dra repetto.JPG
│   │   ├── dra unger.jpg
│   │   ├── fondo1.JPG
│   │   ├── fondo2.jfif
│   │   ├── fondo3.webp
│   │   ├── tecno-logo.jpeg
│   │   ├── tecno_logo.png
│   │   ├── Tecnomedic- Logotipo.ps
│   │   └── notice/
│   └── video/
│       ├── deporte1.mp4
│       ├── deporte2.mp4
│       ├── Escuchar.mp4
│       ├── Heridas.mp4
│       ├── PieDiabetico.mp4
│       ├── sede.mp4
│       └── sesiones.mp4
├── templates/
│   ├── admin.html
│   ├── CLAUDE.md
│   ├── confirmacion.html
│   ├── form.html
│   ├── index.html
│   ├── login.html
│   ├── ooo index copia previa.html
│   └── tienda_section.html
└── n8n/
    ├── CLAUDE.md
    ├── confirmar_turno_Tecnomedic.json
    ├── cron.json
    └── solicitur_turno_tecnomedic.json
```

> Nota: la lista incluye los archivos que forman parte del proyecto actual. La carpeta `.venv/` local y `.env` no deben subirse al repositorio.

---

## Requisitos

- Python 3.10+ (o 3.11)
- Flask
- gspread
- google-auth
- requests
- python-dotenv
- apscheduler
- pytz
- Brevo API key
- Google Service Account JSON
- Twilio opcional para WhatsApp

Instala todo con:

```bash
pip install -r requirements.txt
```

---

## Variables de entorno necesarias

El proyecto carga configuraciones desde variables de entorno.

Agrega estas variables en tu `.env` o en el servicio donde despliegues:

- `SECRET_KEY` – clave secreta de Flask
- `ADMIN_USER` – usuario admin
- `ADMIN_PASSWORD` – contraseña admin
- `GMAIL_USER` – email remitente
- `BREVO_API_KEY` – clave Brevo para enviar emails
- `TWILIO_ACCOUNT_SID` – SID de Twilio (opcional)
- `TWILIO_AUTH_TOKEN` – token Twilio (opcional)
- `TWILIO_WHATSAPP_FROM` – número remitente WhatsApp, por ejemplo `whatsapp:+549XXXXXXXXXX`
- `GOOGLE_CREDS_JSON` – JSON completo de la Service Account de Google en una sola línea

> Importante: la app actual espera `GOOGLE_CREDS_JSON` en lugar de leer directamente `credenciales.json`.

### Keepalive / Ping automático

- `PING_URL` – (opcional) URL pública que la app usará para recibir un ping keepalive. Configura esta variable en tu servicio (por ejemplo Render → Environment) con la URL pública de tu app, por ejemplo `https://test.tecnomedic.com.ar` o la URL de Render `https://tecnomedic.ondender.com`.
- Comportamiento: la aplicación envía una petición HTTP `GET` a `PING_URL` cada 10 minutos para mantener la instancia activa. El job está activo 24/7 excepto entre las 02:00 y 03:59 (hora Argentina) para reducir uso; si no se configura `PING_URL` el job se salta automáticamente.
- Probar localmente: desde una terminal puedes ejecutar:

```bash
curl -I "https://test.tecnomedic.com.ar"
```

o probar desde Python:

```python
import requests
requests.get('https://test.tecnomedic.com.ar', timeout=10)
```

Coloca la URL que prefieras en `PING_URL` (dominio personalizado o la URL que te provea Render).

---

## Configuración local

1. Clona el repositorio.
2. Crea el entorno virtual:

```bash
python -m venv venv
```

3. Activa el entorno:

- Linux/Mac:
  ```bash
  source venv/bin/activate
  ```
- Windows:
  ```powershell
  .\venv\Scripts\activate
  ```

4. Instala dependencias:

```bash
pip install -r requirements.txt
```

5. Crea un archivo `.env` con las variables necesarias.
6. Ejecuta la app:

```bash
python app.py
```

7. Abre en el navegador:

- `http://localhost:5000`
- `http://localhost:5000/turnos`
- `http://localhost:5000/login`

---

## Cómo usar la app

### Páginas públicas

- `/` → Página principal del sitio (`templates/index.html`)
- `/turnos` → Formulario para solicitar turno (`templates/form.html`)
- `/tienda` → Sección de tienda (`templates/tienda_section.html`)

### Flujo de turnos

1. El paciente completa el formulario en `/turnos`.
2. El POST se envía a `/guardar`.
3. El turno se guarda en Google Sheets con estado `Pendiente`.
4. El paciente recibe un email de recepción.
5. Si está configurado, se intenta enviar un mensaje WhatsApp.

### Panel administrativo

- `/login` → acceder con `ADMIN_USER` y `ADMIN_PASSWORD`
- `/admin` → ver la lista de turnos, confirmar, modificar o eliminar
- `/actualizar` → cambiar estado de un turno
- `/modificar` → editar datos del turno
- `/eliminar` → borrar el turno

### API interna

- `/api/horarios?fecha=YYYY-MM-DD` → devuelve disponibilidad de horarios para la fecha seleccionada.

### Webhook WhatsApp

- `/whatsapp/bot` → recibe mensajes POST desde Twilio/WhatsApp si el bot está habilitado.

---

## Cómo funciona Google Sheets

La app usa Google Sheets como base de datos principal.

- la hoja se abre con `gspread`
- el nombre esperado es `Turnos TECNOMEDIC`
- las columnas son:
  - Nombre
  - Apellido
  - DNI
  - ObraSocial
  - Telefono
  - Email
  - Fecha
  - Hora
  - Estado

---

## Despliegue

### Render

Si usas Render, el archivo `render.yaml` ya está preparado.

- sube el repositorio a GitHub
- crea un Web Service en Render
- agrega las variables de entorno
- sube el `credenciales.json` o configura `GOOGLE_CREDS_JSON`
- despliega y usa un dominio personalizado

> Para más detalles, consulta `guia_github_render.md`.

### DonWeb

Si estás usando hosting compartido DonWeb, recuerda que ese plan no es ideal para ejecutar Flask directamente.

- puedes alojar el frontend estático en DonWeb
- la app Python/Flask debe ir a un servicio compatible
- puedes usar `turnos.tecnomedic.com.ar` como subdominio para el backend

> Para instrucciones específicas, consulta `guia_donweb.md`.

---

## Mantenimiento y actualización

### Actualizar código

- actualiza el repositorio
- reinicia el servicio o vuelve a desplegar

### Si usas Render

- haz push a GitHub
- Render deployará automáticamente

### Si usas VPS propio

- reinicia Gunicorn y Nginx
- revisa los logs si hay errores

---

## Consejos de seguridad

- nunca subas `.env` ni `credenciales.json` al repositorio público
- usa contraseñas seguras para `ADMIN_PASSWORD`
- no expongas la clave de Brevo ni Twilio en Git

---

## Qué archivos editar cuando querés cambiar el sitio

- `templates/index.html` → página principal
- `templates/form.html` → formulario de turnos
- `templates/admin.html` → panel de administración
- `static/tecnomedic.css` → estilos del sitio
- `static/js/` (si existe) → scripts del cliente

---

## Recursos adicionales

- `guia_donweb.md` → Guía para DonWeb
- `guia_github_render.md` → Guía para Render
- `guia_dns_nicar.md` → DNS en NIC.ar
