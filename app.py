import os, json, base64, smtplib, urllib.request, urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
EMAIL_REMITENTE   = os.environ.get("EMAIL_REMITENTE", "")
EMAIL_PASSWORD    = os.environ.get("EMAIL_PASSWORD", "")
CALLMEBOT_APIKEY  = os.environ.get("CALLMEBOT_APIKEY", "")
O2_CRITICO    = 3.0
O2_VIGILANCIA = 3.5

CAMPOS = ["Rolesa 1","Rolesa 2","Pantrusko 1","Pantrusko 2",
          "Caesa 1","Caesa 2","Fimasa 1","Fimasa 2","Fimasa 3",
          "Recorcholis 1","Recorcholis 2"]

DB_PATH = os.path.join(os.path.dirname(__file__), "usuarios.json")

def leer_db():
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"gerencia": [], "biologos": [], "parametristas": []}

def guardar_db(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/registrar", methods=["POST"])
def registrar():
    data   = request.json
    rol    = data.get("rol")
    nombre = data.get("nombre", "").strip()
    email  = data.get("email", "").strip()
    wa     = data.get("whatsapp", "").strip()
    campos = data.get("campos", [])
    if not rol or not nombre or not email:
        return jsonify({"error": "Faltan datos"}), 400
    db = leer_db()
    if rol == "gerencia":
        db["gerencia"] = [u for u in db["gerencia"] if u["email"] != email]
        db["gerencia"].append({"nombre": nombre, "email": email, "whatsapp": wa, "campos": CAMPOS})
    elif rol == "biologo":
        db["biologos"] = [u for u in db["biologos"] if u["email"] != email]
        db["biologos"].append({"nombre": nombre, "email": email, "whatsapp": wa, "campos": campos})
    elif rol == "parametrista":
        db["parametristas"] = [u for u in db["parametristas"] if u["email"] != email]
        db["parametristas"].append({"nombre": nombre, "email": email, "whatsapp": wa, "campos": campos})
    else:
        return jsonify({"error": "Rol inválido"}), 400
    guardar_db(db)
    return jsonify({"ok": True, "mensaje": f"Registro guardado para {nombre}"})

@app.route("/api/usuarios", methods=["GET"])
def usuarios():
    return jsonify(leer_db())

@app.route("/api/procesar", methods=["POST"])
def procesar():
    if "foto" not in request.files:
        return jsonify({"error": "No se recibió foto"}), 400
    archivo = request.files["foto"]
    campo   = request.form.get("campo", "")
    imagen_b64 = base64.b64encode(archivo.read()).decode()
    mime = archivo.content_type or "image/jpeg"
    try:
        print("Llamando a IA...")
        datos = extraer_con_ia(imagen_b64, mime)
        print(f"IA respondio: {len(datos.get('piscinas',[]))} piscinas")
        if campo and not datos.get("sector"):
            datos["sector"] = campo
        alertas = evaluar_y_notificar(datos, campo)
        return jsonify({
            "ok": True,
            "fecha": datos.get("fecha"),
            "sector": datos.get("sector"),
            "piscinas": datos.get("piscinas", []),
            "alertas_enviadas": alertas
        })
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500

def extraer_con_ia(imagen_b64, mime):
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": imagen_b64}},
            {"type": "text", "text": 'Extrae los datos de esta hoja de parametros de piscinas. Devuelve SOLO JSON: {"fecha":"DD/MM/YYYY","sector":"nombre","piscinas":[{"ps":"codigo","oxigeno_am":num_o_null,"oxigeno_pm":num_o_null,"temp_am":num_o_null,"temp_pm":num_o_null}]}'}
        ]}]
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01"}
    )
    with urllib.request.urlopen(req, timeout=55) as r:
        resp = json.loads(r.read())
    text = "".join(b.get("text","") for b in resp["content"]).strip()
    if "```" in text:
        text = text.split("```")[1].replace("json","").strip()
        if "```" in text:
            text = text.split("```")[0].strip()
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    return json.loads(text)

def estado_o2(v):
    if v is None: return "normal"
    return "critico" if v < O2_CRITICO else "vigilancia" if v < O2_VIGILANCIA else "normal"

def evaluar_y_notificar(datos, campo_param):
    db = leer_db()
    sector = datos.get("sector", campo_param)
    fecha  = datos.get("fecha", "")
    alertas = []
    for p in datos.get("piscinas", []):
        eam = estado_o2(p.get("oxigeno_am"))
        epm = estado_o2(p.get("oxigeno_pm"))
        if eam != "normal" or epm != "normal":
            alertas.append({**p, "estado_am": eam, "estado_pm": epm})
    if not alertas:
        return 0
    destinatarios = []
    for u in db["gerencia"] + db["biologos"]:
        campos_u = [c.lower() for c in u.get("campos", [])]
        if any(sector.lower() in c or c in sector.lower() for c in campos_u):
            destinatarios.append(u)
    enviados = 0
    vistos = set()
    for u in destinatarios:
        if u.get("email") in vistos: continue
        vistos.add(u.get("email"))
        criticos   = [a for a in alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]
        vigilancia = [a for a in alertas if a not in criticos]
        nivel = "ALERTA CRITICA" if criticos else "VIGILANCIA"
        asunto = f"{nivel} - {sector} - {fecha}"
        cuerpo = f"{nivel}\nSector: {sector} | Fecha: {fecha}\n{'='*40}\n\n"
        for a in alertas:
            cuerpo += f"Piscina {a['ps']} - O2 AM: {a.get('oxigeno_am','--')} | O2 PM: {a.get('oxigeno_pm','--')} mg/L\n"
        if u.get("email") and EMAIL_REMITENTE:
            enviar_email(u["email"], asunto, cuerpo)
        enviados += 1
    return enviados

def enviar_email(dest, asunto, cuerpo):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_REMITENTE
        msg["To"]   = dest
        msg["Subject"] = asunto
        msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL_REMITENTE, EMAIL_PASSWORD)
            s.sendmail(EMAIL_REMITENTE, dest, msg.as_string())
        print(f"Email enviado a {dest}")
    except Exception as e:
        print(f"Email error: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
