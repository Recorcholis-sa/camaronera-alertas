import os, json, base64, urllib.request
from datetime import datetime
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
USUARIOS_JSON     = os.environ.get("USUARIOS_JSON", "[]")
POSTMARK_TOKEN    = os.environ.get("POSTMARK_TOKEN", "")
EMAIL_REMITENTE   = os.environ.get("EMAIL_REMITENTE", "biologo4@docapes.com")
DATABASE_URL      = os.environ.get("DATABASE_URL", "")
O2_CRITICO        = 2.9
O2_VIGILANCIA     = 3.5

CAMPOS = ["Rolesa 1","Rolesa 2","Pantrusko 1","Pantrusko 2",
          "Caesa 1","Caesa 2","Fimasa 1","Fimasa 2","Fimasa 3",
          "Recorcholis 1","Recorcholis 2"]

# ── Base de datos PostgreSQL ───────────────────────────────
import psycopg2
from psycopg2.extras import RealDictCursor

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    con = get_conn()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lecturas (
            id SERIAL PRIMARY KEY,
            fecha TEXT,
            sector TEXT,
            piscina TEXT,
            corrida INTEGER DEFAULT 1,
            oxigeno_am REAL,
            oxigeno_pm REAL,
            temp_am REAL,
            temp_pm REAL,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nombre TEXT,
            email TEXT UNIQUE,
            whatsapp TEXT,
            campos TEXT,
            rol TEXT,
            created_at TEXT
        )
    """)
    con.commit()
    cur.close()
    con.close()

try:
    init_db()
    print("DB inicializada OK")
except Exception as e:
    print(f"DB init error: {e}")

def get_corrida_actual(cur, sector, piscina):
    cur.execute("""
        SELECT corrida, created_at FROM lecturas
        WHERE sector=%s AND piscina=%s
        ORDER BY created_at DESC LIMIT 1
    """, (sector, piscina))
    row = cur.fetchone()
    if not row:
        return 1
    ultima_corrida = row[0]
    ultima_fecha   = datetime.fromisoformat(row[1])
    dias_sin_reporte = (datetime.utcnow() - ultima_fecha).days
    if dias_sin_reporte > 2:
        return ultima_corrida + 1
    return ultima_corrida

def guardar_lecturas(sector, fecha, piscinas):
    con = get_conn()
    cur = con.cursor()
    now = datetime.utcnow().isoformat()
    for p in piscinas:
        corrida = get_corrida_actual(cur, sector, p["ps"])
        cur.execute(
            "SELECT id FROM lecturas WHERE sector=%s AND fecha=%s AND piscina=%s AND corrida=%s",
            (sector, fecha, p["ps"], corrida)
        )
        if cur.fetchone():
            cur.execute("""
                UPDATE lecturas SET oxigeno_am=%s, oxigeno_pm=%s, temp_am=%s, temp_pm=%s, created_at=%s
                WHERE sector=%s AND fecha=%s AND piscina=%s AND corrida=%s
            """, (p.get("oxigeno_am"), p.get("oxigeno_pm"),
                  p.get("temp_am"), p.get("temp_pm"), now,
                  sector, fecha, p["ps"], corrida))
        else:
            cur.execute("""
                INSERT INTO lecturas (fecha, sector, piscina, corrida, oxigeno_am, oxigeno_pm, temp_am, temp_pm, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (fecha, sector, p["ps"], corrida,
                  p.get("oxigeno_am"), p.get("oxigeno_pm"),
                  p.get("temp_am"), p.get("temp_pm"), now))
    con.commit()
    cur.close()
    con.close()

# ── Usuarios ───────────────────────────────────────────────
def leer_usuarios():
    usuarios = []
    # Primero de PostgreSQL
    try:
        con = get_conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM usuarios")
        rows = cur.fetchall()
        cur.close()
        con.close()
        for u in rows:
            try:
                u["campos"] = json.loads(u["campos"])
            except:
                u["campos"] = []
            usuarios.append(dict(u))
    except Exception as e:
        print(f"Error leyendo usuarios DB: {e}")
    # Luego los de USUARIOS_JSON (sin duplicar)
    try:
        base = json.loads(USUARIOS_JSON)
        emails_existentes = {u["email"] for u in usuarios}
        for u in base:
            if u.get("email") not in emails_existentes:
                usuarios.append(u)
    except:
        pass
    return usuarios

def guardar_usuario_db(nombre, email, whatsapp, campos, rol):
    try:
        con = get_conn()
        cur = con.cursor()
        now = datetime.utcnow().isoformat()
        campos_json = json.dumps(campos)
        cur.execute("""
            INSERT INTO usuarios (nombre, email, whatsapp, campos, rol, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (email) DO UPDATE SET
                nombre=%s, whatsapp=%s, campos=%s, rol=%s, created_at=%s
        """, (nombre, email, whatsapp, campos_json, rol, now,
              nombre, whatsapp, campos_json, rol, now))
        con.commit()
        cur.close()
        con.close()
        return True
    except Exception as e:
        print(f"Error guardando usuario: {e}")
        return False

# ── Rutas ──────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/campos", methods=["GET"])
def get_campos():
    return jsonify({"campos": CAMPOS})

@app.route("/api/registrar", methods=["POST"])
def registrar():
    try:
        data     = request.get_json()
        rol      = data.get("rol", "")
        nombre   = data.get("nombre", "").strip()
        email    = data.get("email", "").strip().lower()
        whatsapp = data.get("whatsapp", "").strip()
        campos   = data.get("campos", [])
        if not nombre or not email:
            return jsonify({"ok": False, "error": "Nombre y email son requeridos"}), 400
        if rol == "gerencia":
            campos = CAMPOS
        ok = guardar_usuario_db(nombre, email, whatsapp, campos, rol)
        if ok:
            return jsonify({"ok": True, "mensaje": f"Registro exitoso. Bienvenido {nombre}!"})
        else:
            return jsonify({"ok": False, "error": "Error al guardar"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

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
        guardar_lecturas(datos.get("sector", campo), datos.get("fecha", ""), datos.get("piscinas", []))
        enviados = evaluar_y_notificar(datos, campo)
        return jsonify({
            "ok": True,
            "fecha": datos.get("fecha"),
            "sector": datos.get("sector"),
            "piscinas": datos.get("piscinas", []),
            "alertas_enviadas": enviados
        })
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/corridas", methods=["GET"])
def get_corridas():
    try:
        sector  = request.args.get("sector", "")
        piscina = request.args.get("piscina", "")
        if not sector or not piscina:
            return jsonify({"error": "sector y piscina requeridos"}), 400
        con = get_conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT corrida, MIN(fecha) as inicio, MAX(fecha) as fin, COUNT(*) as dias
            FROM lecturas WHERE sector=%s AND piscina=%s
            GROUP BY corrida ORDER BY corrida DESC
        """, (sector, piscina))
        corridas = [dict(r) for r in cur.fetchall()]
        cur.close(); con.close()
        return jsonify({"corridas": corridas})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/historico", methods=["GET"])
def historico():
    try:
        sector  = request.args.get("sector", "")
        piscina = request.args.get("piscina", "")
        dias    = int(request.args.get("dias", 99999))
        corrida = request.args.get("corrida", None)
        if not sector or not piscina:
            return jsonify({"error": "sector y piscina son requeridos"}), 400
        con = get_conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        if corrida:
            cur.execute("""
                SELECT fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm, corrida
                FROM lecturas WHERE sector=%s AND piscina=%s AND corrida=%s
                ORDER BY created_at ASC
            """, (sector, piscina, int(corrida)))
        else:
            cur.execute("SELECT MAX(corrida) as mc FROM lecturas WHERE sector=%s AND piscina=%s", (sector, piscina))
            row = cur.fetchone()
            max_corrida = row["mc"] if row and row["mc"] else 1
            cur.execute("""
                SELECT fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm, corrida
                FROM lecturas WHERE sector=%s AND piscina=%s AND corrida=%s
                ORDER BY created_at ASC LIMIT %s
            """, (sector, piscina, max_corrida, dias))
        rows = cur.fetchall()
        cur.close(); con.close()
        datos = [dict(r) for r in rows]
        corrida_num = datos[0]["corrida"] if datos else 1
        return jsonify({"ok": True, "datos": datos, "sector": sector, "piscina": piscina, "corrida": corrida_num})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/piscinas", methods=["GET"])
def get_piscinas():
    try:
        sector = request.args.get("sector", "")
        con = get_conn()
        cur = con.cursor()
        cur.execute("SELECT DISTINCT piscina FROM lecturas WHERE sector=%s ORDER BY piscina", (sector,))
        piscinas_raw = [r[0] for r in cur.fetchall()]
        cur.close(); con.close()
        # Ordenar numéricamente
        def sort_key(p):
            try: return (0, int(p))
            except: return (1, p)
        piscinas = sorted(piscinas_raw, key=sort_key)
        return jsonify({"piscinas": piscinas})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── IA ─────────────────────────────────────────────────────
def extraer_con_ia(imagen_b64, mime):
    payload = {
        "model": "claude-opus-4-6",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": imagen_b64}},
            {"type": "text", "text": 'Eres un experto en acuicultura leyendo hojas de parámetros de piscinas camaroneras. Debes leer cada valor DOS VECES antes de confirmar. Proceso: 1) Lee todos los valores, 2) Vuelve a verificar cada valor leyendo dígito por dígito. Rangos típicos: oxígeno 1.0-15.0 mg/L (valores fuera de este rango son errores de lectura), temperatura 20.0-35.0 °C. Distingue con cuidado: 3 vs 8, 1 vs 7, 5 vs 6, 0 vs 9, punto decimal vs coma. Si un valor es ilegible usa null. Devuelve SOLO JSON sin texto extra ni explicaciones: {"fecha":"DD/MM/YYYY","sector":"nombre","piscinas":[{"ps":"codigo","oxigeno_am":num_o_null,"oxigeno_pm":num_o_null,"temp_am":num_o_null,"temp_pm":num_o_null}]}'}
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

# ── Alertas ────────────────────────────────────────────────
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
        enviar_email_postmark(u.get("email"), u.get("nombre",""), asunto, cuerpo)
        enviados += 1
    print(f"Emails enviados: {enviados}")
    return enviados

def enviar_email_postmark(dest_email, dest_nombre, asunto, cuerpo):
    try:
        print(f"Enviando Postmark a {dest_email}...")
        payload = {
            "From": EMAIL_REMITENTE,
            "To": dest_email,
            "Subject": asunto,
            "TextBody": cuerpo,
            "MessageStream": "outbound"
        }
        req = urllib.request.Request(
            "https://api.postmarkapp.com/email",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Postmark-Server-Token": POSTMARK_TOKEN
            }
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            status = r.status
            body   = r.read().decode()
        print(f"Postmark OK -> {dest_email} | status: {status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Postmark error ({dest_email}): {e.code} | {body}")
    except Exception as e:
        print(f"Postmark error ({dest_email}): {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
