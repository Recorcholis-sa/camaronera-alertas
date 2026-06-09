# 🦐 Guía de publicación — 3 pasos

## Lo que necesitas antes de empezar
- Una cuenta Gmail (para enviar alertas)
- Tu API key de Anthropic (la consigues en platform.anthropic.com → API Keys)
- 15 minutos

---

## PASO 1 — Subir a GitHub (5 min)

1. Ve a github.com → crea cuenta gratis
2. Clic en "+" → "New repository"
   - Nombre: camaronera-alertas
   - Público
   - Clic "Create repository"
3. En la página del repo, clic "uploading an existing file"
4. Arrastra TODOS los archivos de esta carpeta (app.py, requirements.txt, Procfile, templates/)
5. Clic "Commit changes"

---

## PASO 2 — Publicar en Render (5 min)

1. Ve a render.com → "Get Started for Free"
2. Regístrate con tu cuenta de GitHub
3. "New +" → "Web Service"
4. Selecciona el repositorio camaronera-alertas
5. Render detecta todo automáticamente. Solo verifica:
   - Environment: Python
   - Start Command: gunicorn app:app
6. Baja hasta "Environment Variables" y agrega estas 4 claves:

```
ANTHROPIC_API_KEY  →  tu clave de platform.anthropic.com
EMAIL_REMITENTE    →  tucorreo@gmail.com
EMAIL_PASSWORD     →  contraseña de aplicación de Gmail*
CALLMEBOT_APIKEY   →  tu API key de CallMeBot**
```

7. Clic "Create Web Service"
8. Espera 2-3 minutos → Render te da tu link 🎉

---

## PASO 3 — Configurar CallMeBot (2 min)

Cada persona que recibirá alertas por WhatsApp debe hacer esto UNA sola vez:

1. Agrega el número +34 644 65 21 21 como contacto (nombre: CallMeBot)
2. Envíale este mensaje por WhatsApp:
   `I allow callmebot to send me messages`
3. Recibirá su API key en respuesta
4. Esa API key la pone al registrarse en tu app

---

## Contraseña de aplicación Gmail*

1. Entra a myaccount.google.com
2. Seguridad → Verificación en dos pasos (actívala)
3. "Contraseñas de aplicaciones"
4. Crea una nueva → escribe "camaronera"
5. Copia la clave de 16 caracteres → esa va en EMAIL_PASSWORD

---

## Tu link quedará así:
https://camaronera-alertas.onrender.com

Compártelo con todo el equipo. Cada quien entra, elige su rol y se registra solo.
El parametrista lo guarda en su pantalla de inicio del celular.

---

## ¿Algo no funciona?
Escríbeme a Claude y con gusto te ayudo a resolver cualquier paso.
