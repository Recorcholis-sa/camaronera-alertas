import os, json, base64, urllib.request, urllib.parse
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
USUARIOS_JSON     = os.environ.get("USUARIOS_JSON", "[]")
EMAILJS_SERVICE   = os.environ.get("EMAILJS_SERVICE", "")
EMAILJS_TEMPLATE  = os.environ.get("EMAILJS_TEMPLATE", "")
EMAILJS_PUBLIC    = os.environ.get("EMAILJS_PUBLIC", "")
O2_CRITICO        = 3.0
O2_VIGILANCIA     = 3.5

CAMPOS = ["Rolesa 1","Rolesa 2","Pantrusko 1","Pantrusko 2",
          "Caesa 1","Caesa 2","Fimasa 1","Fimasa 2","Fimasa 3",
          "Recorcholis 1","Recorcholis 2"]

def leer_usuarios():
    try:
        return json.loads(USUARIOS_JSON)
    except:
        return []

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/campos", methods=["GET"])
def get_campos():
    return jsonify({"campos": CAMPOS})

@app.route("/api/procesar", methods=["POST"])
def procesar():
    if "foto" not in request.files:
        return jsonify({"error": "No se recibio foto"}), 400
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
        import threading
        t = threading.Thread(target=evaluar_y_notificar, args=(datos, campo))
        t.daemon = True
        t.start()
        return jsonify({
            "ok": True,
            "fecha": datos.get("fecha"),
            "sector": datos.get("sector"),
            "piscinas": datos.get("piscinas", []),
            "alertas_enviadas": "enviando..."
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
            {"type": "text", "text": 'Extrae los datos de esta hoja de parametros de piscinas. Devuelve SOLO JSON sin texto extra: {"fecha":"DD/MM/YYYY","sector":"nombre","piscinas":[{"ps":"codigo","oxigeno_am":num_o_null,"oxigeno_pm":num_o_null,"temp_am":num_o_null,"temp_pm":num_o_null}]}'}
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
    usuarios = leer_usuarios()
    sector = datos.get("sector", campo_param)
    fecha  = datos.get("fecha", "")
    alertas = []
    for p in datos.get("piscinas", []):
        eam = estado_o2(p.get("oxigeno_am"))
        epm = estado_o2(p.get("oxigeno_pm"))
        if eam != "normal" or epm != "normal":
            alertas.append({**p, "estado_am": eam, "estado_pm": epm})
    if not alertas:
        print("Todas las piscinas en rango normal")
        return 0
    print(f"Alertas: {len(alertas)} piscinas, sector: {sector}")
    enviados = 0
    vistos = set()
    for u in usuarios:
        if u.get("email") in vistos:
            continue
        campos_u = [c.lower() for c in u.get("campos", [])]
        if not any(sector.lower() in c or c in sector.lower() for c in campos_u):
            continue
        vistos.add(u.get("email"))
        criticos = [a for a in alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]
        nivel = "ALERTA CRITICA" if criticos else "VIGILANCIA"
        asunto = f"{nivel} - {sector} - {fecha}"
        cuerpo = f"{nivel}\nSector: {sector} | Fecha: {fecha}\n{'='*40}\n\n"
        for a in alertas:
            cuerpo += f"Piscina {a['ps']} - O2 AM: {a.get('oxigeno_am','--')} | O2 PM: {a.get('oxigeno_pm','--')} mg/L\n"
        enviar_email_emailjs(u.get("email"), u.get("nombre",""), asunto, cuerpo)
        enviados += 1
    print(f"Emails enviados: {enviados}")
    return enviados

def enviar_email_emailjs(dest_email, dest_nombre, asunto, cuerpo):
    try:
        payload = {
            "service_id":  EMAILJS_SERVICE,
            "template_id": EMAILJS_TEMPLATE,
            "user_id":     EMAILJS_PUBLIC,
            "template_params": {
                "to_email": dest_email,
                "name":     dest_nombre or "Equipo Camaronera",
                "subject":  asunto,
                "message":  cuerpo,
                "email":    dest_email
            }
        }
        req = urllib.request.Request(
            "https://api.emailjs.com/api/v1.0/email/send",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "origin": "http://localhost"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            status = r.status
            body   = r.read().decode()
        print(f"EmailJS -> {dest_email} | status: {status} | resp: {body}")
    except Exception as e:
        print(f"EmailJS error ({dest_email}): {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
