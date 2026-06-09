import os, json, base64, smtplib, urllib.request, urllib.parse, threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
EMAIL_REMITENTE   = os.environ.get("EMAIL_REMITENTE", "")
EMAIL_PASSWORD    = os.environ.get("EMAIL_PASSWORD", "")
CALLMEBOT_APIKEY  = os.environ.get("CALLMEBOT_APIKEY", "")
O2_CRITICO        = 3.0
O2_VIGILANCIA     = 3.5

CAMPOS = [
    "Rolesa 1","Rolesa 2","Pantrusko 1","Pantrusko 2",
    "Caesa 1","Caesa 2","Fimasa 1","Fimasa 2","Fimasa 3",
    "Recorcholis 1","Recorcholis 2"
]

DB_PATH = os.path.join(os.path.dirname(__file__), "usuarios.json")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

def guardar_resultado(job_id, data):
    path = os.path.join(RESULTS_DIR, f"{job_id}.json")
    with open(path, "w") as f:
        json.dump(data, f)

def leer_resultado(job_id):
    path = os.path.join(RESULTS_DIR, f"{job_id}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None

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
    data = request.json
    rol    = data.get("rol")
    nombre = data.get("nombre", "").strip()
    email  = data.get("email", "").strip()
    wa     = data.get("whatsapp", "").strip()
    campos = data.get("campos", [])
    if not rol or not nombre or not email:
        return jsonify({"error": "Faltan datos obligatorios"}), 400
    db = leer_db()
    if rol == "gerencia":
        entrada = {"nombre": nombre, "email": email, "whatsapp": wa, "campos": CAMPOS}
        db["gerencia"] = [u for u in db["gerencia"] if u["email"] != email]
        db["gerencia"].append(entrada)
    elif rol == "biologo":
        entrada = {"nombre": nombre, "email": email, "whatsapp": wa, "campos": campos}
        db["biologos"] = [u for u in db["biologos"] if u["email"] != email]
        db["biologos"].append(entrada)
    elif rol == "parametrista":
        entrada = {"nombre": nombre, "email": email, "whatsapp": wa, "campos": campos}
        db["parametristas"] = [u for u in db["parametristas"] if u["email"] != email]
        db["parametristas"].append(entrada)
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
    campo_parametrista = request.form.get("campo", "")
    imagen_b64 = base64.b64encode(archivo.read()).decode()
    mime = archivo.content_type or "image/jpeg"
    # Generar job_id único
    import time
    job_id = str(int(time.time() * 1000))
    guardar_resultado(job_id, {"estado": "procesando"})
    # Procesar en hilo separado
    t = threading.Thread(target=procesar_async, args=(job_id, imagen_b64, mime, campo_parametrista))
    t.daemon = True
    t.start()
    return jsonify({"ok": True, "job_id": job_id})

def procesar_async(job_id, imagen_b64, mime, campo_parametrista):
    try:
        print(f"[JOB {job_id}] Iniciando extraccion IA...")
        datos = extraer_con_ia(imagen_b64, mime)
        print(f"[JOB {job_id}] IA respondio: {len(datos.get('piscinas',[]))} piscinas")
        if campo_parametrista and not datos.get("sector"):
            datos["sector"] = campo_parametrista
        alertas = evaluar_y_notificar(datos, campo_parametrista)
        guardar_resultado(job_id, {
            "estado": "listo",
            "fecha": datos.get("fecha"),
            "sector": datos.get("sector"),
            "piscinas": datos.get("piscinas", []),
            "alertas_enviadas": alertas
        })
        print(f"[JOB {job_id}] Resultado guardado OK")
    except Exception as e:
        print(f"[JOB {job_id}] ERROR: {str(e)}")
        guardar_resultado(job_id, {"estado": "error", "error": str(e)})

@app.route("/api/resultado/<job_id>", methods=["GET"])
def resultado(job_id):
    res = leer_resultado(job_id)
    if res is None:
        return jsonify({"estado": "procesando"})
    return jsonify(res)

def extraer_con_ia(imagen_b64, mime):
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": imagen_b64}},
            {"type": "text", "text": 'Extrae TODOS los datos de esta hoja de parámetros de piscinas camaroneras. Devuelve SOLO JSON sin texto extra ni backticks: {"fecha":"DD/MM/YYYY","sector":"nombre","piscinas":[{"ps":"codigo","oxigeno_am":num_o_null,"oxigeno_pm":num_o_null,"temp_am":num_o_null,"temp_pm":num_o_null,"tb_cm":num_o_null,"color":num_o_null,"cal_ent":num_o_null,"cal_salid":num_o_null,"nivel_am":num_o_null,"nivel_cm":num_o_null,"nivel_pm":num_o_null,"sal_pct":num_o_null}]}'}
        ]}]
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    text = "".join(b.get("text","") for b in resp["content"]).strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    return json.loads(text)

def estado_o2(v):
    if v is None: return "normal"
    return "critico" if v < O2_CRITICO else "vigilancia" if v < O2_VIGILANCIA else "normal"

def evaluar_y_notificar(datos, campo_parametrista):
    db = leer_db()
    sector = datos.get("sector", campo_parametrista)
    fecha  = datos.get("fecha", "")
    piscinas_alerta = []
    for p in datos.get("piscinas", []):
        est_am = estado_o2(p.get("oxigeno_am"))
        est_pm = estado_o2(p.get("oxigeno_pm"))
        if est_am != "normal" or est_pm != "normal":
            piscinas_alerta.append({**p, "estado_am": est_am, "estado_pm": est_pm})
    if not piscinas_alerta:
        return 0
    destinatarios = []
    todos_usuarios = db["gerencia"] + db["biologos"]
    for u in todos_usuarios:
        campos_u = [c.lower() for c in u.get("campos", [])]
        sector_l = sector.lower()
        if any(sector_l in c or c in sector_l for c in campos_u):
            destinatarios.append(u)
    enviados = 0
    vistos = set()
    for u in destinatarios:
        uid = u.get("email")
        if uid in vistos: continue
        vistos.add(uid)
        msg_wa, asunto, msg_email = construir_mensaje(u, piscinas_alerta, sector, fecha)
        if u.get("whatsapp") and CALLMEBOT_APIKEY:
            enviar_wa(u["whatsapp"], msg_wa)
        if u.get("email") and EMAIL_REMITENTE:
            enviar_email(u["email"], asunto, msg_email)
        enviados += 1
    return enviados

def construir_mensaje(u, alertas, sector, fecha):
    criticos   = [a for a in alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]
    vigilancia = [a for a in alertas if a not in criticos]
    nivel = "ALERTA CRITICA" if criticos else "VIGILANCIA"
    wa = f"*{nivel}*\n{sector} - {fecha}\n\n"
    if criticos:
        wa += "*Critico (O2 menor 3 mg/L)*\n"
        for a in criticos:
            wa += f"PS {a['ps']}: AM {a.get('oxigeno_am','--')} | PM {a.get('oxigeno_pm','--')} mg/L\n"
    if vigilancia:
        wa += "*Vigilancia (3-3.5 mg/L)*\n"
        for a in vigilancia:
            wa += f"PS {a['ps']}: AM {a.get('oxigeno_am','--')} | PM {a.get('oxigeno_pm','--')} mg/L\n"
    asunto = f"{nivel} - {sector} - {fecha}"
    email_body = f"{nivel}\nSector: {sector} | Fecha: {fecha}\n{'='*40}\n\n"
    for a in alertas:
        email_body += f"Piscina {a['ps']}\n"
        email_body += f"  O2 AM: {a.get('oxigeno_am','--')} mg/L  |  O2 PM: {a.get('oxigeno_pm','--')} mg/L\n"
        email_body += f"  Temp AM: {a.get('temp_am','--')} C  |  Temp PM: {a.get('temp_pm','--')} C\n\n"
    return wa, asunto, email_body

def enviar_wa(telefono, mensaje):
    try:
        url = (f"https://api.callmebot.com/whatsapp.php?phone={telefono}"
               f"&apikey={CALLMEBOT_APIKEY}&text={urllib.parse.quote(mensaje)}")
        urllib.request.urlopen(url, timeout=10)
    except Exception as e:
        print(f"WA error: {e}")

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
    except Exception as e:
        print(f"Email error: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
