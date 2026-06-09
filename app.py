import os, json, base64, smtplib, urllib.request, urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, render_template
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
EMAIL_REMITENTE   = os.environ.get("EMAIL_REMITENTE", "")
EMAIL_PASSWORD    = os.environ.get("EMAIL_PASSWORD", "")
SHEET_ID          = os.environ.get("SHEET_ID", "")
O2_CRITICO        = 3.0
O2_VIGILANCIA     = 3.5

CAMPOS = ["Rolesa 1","Rolesa 2","Pantrusko 1","Pantrusko 2",
          "Caesa 1","Caesa 2","Fimasa 1","Fimasa 2","Fimasa 3",
          "Recorcholis 1","Recorcholis 2"]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "{}")

def get_sheets_service():
    creds_dict = json.loads(CREDS_JSON)
    creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds).spreadsheets()

def leer_usuarios():
    try:
        svc = get_sheets_service()
        result = svc.values().get(spreadsheetId=SHEET_ID, range="Sheet1!A2:E1000").execute()
        rows = result.get("values", [])
        db = {"gerencia": [], "biologos": [], "parametristas": []}
        for row in rows:
            if len(row) < 4: continue
            rol    = row[0] if len(row) > 0 else ""
            nombre = row[1] if len(row) > 1 else ""
            email  = row[2] if len(row) > 2 else ""
            wa     = row[3] if len(row) > 3 else ""
            campos = json.loads(row[4]) if len(row) > 4 and row[4] else []
            u = {"nombre": nombre, "email": email, "whatsapp": wa, "campos": campos}
            if rol == "gerencia":   db["gerencia"].append(u)
            elif rol == "biologo":  db["biologos"].append(u)
            elif rol == "parametrista": db["parametristas"].append(u)
        print(f"Usuarios leidos: gerencia={len(db['gerencia'])}, biologos={len(db['biologos'])}")
        return db
    except Exception as e:
        print(f"Error leyendo Sheets: {e}")
        return {"gerencia": [], "biologos": [], "parametristas": []}

def guardar_usuario(rol, nombre, email, wa, campos):
    try:
        svc = get_sheets_service()
        # Buscar si el email ya existe para actualizar
        result = svc.values().get(spreadsheetId=SHEET_ID, range="Sheet1!A2:E1000").execute()
        rows = result.get("values", [])
        row_num = None
        for i, row in enumerate(rows):
            if len(row) > 2 and row[2] == email:
                row_num = i + 2
                break
        campos_str = json.dumps(campos, ensure_ascii=False)
        values = [[rol, nombre, email, wa, campos_str]]
        if row_num:
            svc.values().update(
                spreadsheetId=SHEET_ID,
                range=f"Sheet1!A{row_num}:E{row_num}",
                valueInputOption="RAW",
                body={"values": values}
            ).execute()
        else:
            svc.values().append(
                spreadsheetId=SHEET_ID,
                range="Sheet1!A:E",
                valueInputOption="RAW",
                body={"values": values}
            ).execute()
        print(f"Usuario guardado: {nombre} ({rol})")
        return True
    except Exception as e:
        print(f"Error guardando en Sheets: {e}")
        return False

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
    if rol == "gerencia":
        campos = CAMPOS
    ok = guardar_usuario(rol, nombre, email, wa, campos)
    if ok:
        return jsonify({"ok": True, "mensaje": f"Registro guardado para {nombre}"})
    return jsonify({"error": "No se pudo guardar"}), 500

@app.route("/api/usuarios", methods=["GET"])
def usuarios():
    return jsonify(leer_usuarios())

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
        if campo:
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
    db = leer_usuarios()
    sector = datos.get("sector", campo_param)
    fecha  = datos.get("fecha", "")
    alertas = []
    for p in datos.get("piscinas", []):
        eam = estado_o2(p.get("oxigeno_am"))
        epm = estado_o2(p.get("oxigeno_pm"))
        if eam != "normal" or epm != "normal":
            alertas.append({**p, "estado_am": eam, "estado_pm": epm})
    if not alertas:
        print("No hay alertas que enviar")
        return 0
    print(f"Alertas encontradas: {len(alertas)}, buscando usuarios para sector: {sector}")
    destinatarios = []
    for u in db["gerencia"] + db["biologos"]:
        campos_u = [c.lower() for c in u.get("campos", [])]
        match = any(sector.lower() in c or c in sector.lower() for c in campos_u)
        print(f"  {u.get('nombre')}: match={match}")
        if match:
            destinatarios.append(u)
    enviados = 0
    vistos = set()
    for u in destinatarios:
        if u.get("email") in vistos: continue
        vistos.add(u.get("email"))
        criticos   = [a for a in alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]
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
