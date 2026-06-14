import os, json, base64, urllib.request
from datetime import datetime, timedelta
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

FIMASA3 = "Fimasa 3"

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
    for col, tipo in [("oxigeno_00","REAL"),("temp_00","REAL"),("oxigeno_02","REAL"),("temp_02","REAL")]:
        try:
            cur.execute(f"ALTER TABLE lecturas ADD COLUMN IF NOT EXISTS {col} {tipo}")
        except Exception as e:
            print(f"Columna {col}: {e}")
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
    es_fimasa3 = sector == FIMASA3
    for p in piscinas:
        corrida = get_corrida_actual(cur, sector, p["ps"])
        cur.execute(
            "SELECT id FROM lecturas WHERE sector=%s AND fecha=%s AND piscina=%s AND corrida=%s",
            (sector, fecha, p["ps"], corrida)
        )
        if cur.fetchone():
            if es_fimasa3:
                cur.execute("""
                    UPDATE lecturas SET oxigeno_am=%s, oxigeno_pm=%s, temp_am=%s, temp_pm=%s,
                        oxigeno_00=%s, temp_00=%s, oxigeno_02=%s, temp_02=%s, created_at=%s
                    WHERE sector=%s AND fecha=%s AND piscina=%s AND corrida=%s
                """, (p.get("oxigeno_am"), p.get("oxigeno_pm"),
                      p.get("temp_am"), p.get("temp_pm"),
                      p.get("oxigeno_00"), p.get("temp_00"),
                      p.get("oxigeno_02"), p.get("temp_02"), now,
                      sector, fecha, p["ps"], corrida))
            else:
                cur.execute("""
                    UPDATE lecturas SET oxigeno_am=%s, oxigeno_pm=%s, temp_am=%s, temp_pm=%s, created_at=%s
                    WHERE sector=%s AND fecha=%s AND piscina=%s AND corrida=%s
                """, (p.get("oxigeno_am"), p.get("oxigeno_pm"),
                      p.get("temp_am"), p.get("temp_pm"), now,
                      sector, fecha, p["ps"], corrida))
        else:
            if es_fimasa3:
                cur.execute("""
                    INSERT INTO lecturas (fecha, sector, piscina, corrida, oxigeno_am, oxigeno_pm,
                        temp_am, temp_pm, oxigeno_00, temp_00, oxigeno_02, temp_02, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (fecha, sector, p["ps"], corrida,
                      p.get("oxigeno_am"), p.get("oxigeno_pm"),
                      p.get("temp_am"), p.get("temp_pm"),
                      p.get("oxigeno_00"), p.get("temp_00"),
                      p.get("oxigeno_02"), p.get("temp_02"), now))
            else:
                cur.execute("""
                    INSERT INTO lecturas (fecha, sector, piscina, corrida, oxigeno_am, oxigeno_pm, temp_am, temp_pm, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (fecha, sector, p["ps"], corrida,
                      p.get("oxigeno_am"), p.get("oxigeno_pm"),
                      p.get("temp_am"), p.get("temp_pm"), now))
    con.commit()
    cur.close()
    con.close()

# ── Usuarios ───────────────────────────────────────────────
def leer_usuarios():
    usuarios = []
    try:
        con = get_conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM usuarios")
        rows = cur.fetchall()
        cur.close(); con.close()
        for u in rows:
            try: u["campos"] = json.loads(u["campos"])
            except: u["campos"] = []
            usuarios.append(dict(u))
    except Exception as e:
        print(f"Error leyendo usuarios DB: {e}")
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
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (email) DO UPDATE SET
                nombre=%s, whatsapp=%s, campos=%s, rol=%s, created_at=%s
        """, (nombre, email, whatsapp, campos_json, rol, now,
              nombre, whatsapp, campos_json, rol, now))
        con.commit()
        cur.close(); con.close()
        return True
    except Exception as e:
        print(f"Error guardando usuario: {e}")
        return False

# ── Resumen por campo ──────────────────────────────────────
def get_resumen_campo(sector, dias=3):
    try:
        con = get_conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT piscina, fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm,
                   oxigeno_00, temp_00, oxigeno_02, temp_02
            FROM lecturas
            WHERE sector=%s AND corrida=(
                SELECT MAX(corrida) FROM lecturas l2
                WHERE l2.sector=lecturas.sector AND l2.piscina=lecturas.piscina
            )
            ORDER BY piscina, created_at DESC
        """, (sector,))
        rows = cur.fetchall()
        cur.close(); con.close()
        piscinas = {}
        for r in rows:
            ps = r["piscina"]
            if ps not in piscinas:
                piscinas[ps] = []
            if len(piscinas[ps]) < dias:
                piscinas[ps].append(dict(r))
        def sort_key(p):
            try: return (0, int(p))
            except: return (1, p)
        resultado = []
        for ps in sorted(piscinas.keys(), key=sort_key):
            historial = piscinas[ps]
            ultimo = historial[0] if historial else {}
            resultado.append({"piscina": ps, "ultimo": ultimo, "historial": list(reversed(historial))})
        return resultado
    except Exception as e:
        print(f"Error get_resumen_campo: {e}")
        return []

def get_resumen_todos_campos():
    resumen = {}
    for campo in CAMPOS:
        datos = get_resumen_campo(campo, dias=4)
        if datos:
            resumen[campo] = datos
    return resumen

# ── Helpers Fimasa 3 ───────────────────────────────────────
def tiene_alerta_fimasa3(u):
    vals = [u.get("oxigeno_00"), u.get("oxigeno_02"), u.get("oxigeno_am"), u.get("oxigeno_pm")]
    return any(v is not None and v < O2_VIGILANCIA for v in vals)

def tiene_critico_fimasa3(u):
    vals = [u.get("oxigeno_00"), u.get("oxigeno_02"), u.get("oxigeno_am"), u.get("oxigeno_pm")]
    return any(v is not None and v < O2_CRITICO for v in vals)

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
        datos = extraer_con_ia(imagen_b64, mime, campo)
        print(f"IA respondio: {len(datos.get('piscinas',[]))} piscinas")
        if campo:
            datos["sector"] = campo
        # Usar fecha del servidor (hora Ecuador UTC-5), no la del block
        fecha_hoy = (datetime.utcnow() - timedelta(hours=5)).strftime("%d/%m/%Y")
        datos["fecha"] = fecha_hoy
        guardar_lecturas(datos.get("sector", campo), fecha_hoy, datos.get("piscinas", []))
        enviados = evaluar_y_notificar(datos, campo)
        return jsonify({
            "ok": True,
            "fecha": fecha_hoy,
            "sector": datos.get("sector"),
            "piscinas": datos.get("piscinas", []),
            "alertas_enviadas": enviados
        })
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/resumen-diario", methods=["GET", "POST"])
def resumen_diario():
    """Endpoint para el cron job de resumen diario a las 6 AM Ecuador.
       Siempre envía aunque todas las piscinas estén en rango normal."""
    try:
        print("Enviando resumen diario...")
        fecha_hoy = (datetime.utcnow() - timedelta(hours=5)).strftime("%d/%m/%Y")
        usuarios = leer_usuarios()
        enviados = 0

        for u in usuarios:
            if not u.get("email"):
                continue
            rol = u.get("rol", "biologo")

            if rol == "gerencia":
                # Gerencia siempre recibe consolidado
                asunto, cuerpo, html_body = construir_html_gerencia_consolidado(fecha_hoy)
                if not asunto:
                    # Si no hay alertas, construir resumen normal de todos los campos
                    asunto, cuerpo, html_body = construir_html_gerencia_todo(fecha_hoy)
                if asunto:
                    enviar_email_postmark(u["email"], u.get("nombre",""), asunto, cuerpo, html=html_body)
                    enviados += 1
            else:
                # Biólogos siempre reciben resumen de sus campos
                for campo in u.get("campos", []):
                    piscinas_data = get_resumen_campo(campo, dias=4)
                    if not piscinas_data:
                        continue
                    todas = [{"ps": p["piscina"], **p["ultimo"]} for p in piscinas_data]
                    alertas = []
                    for p in piscinas_data:
                        u2 = p["ultimo"]
                        if campo == FIMASA3:
                            e00 = estado_o2(u2.get("oxigeno_00"))
                            e02 = estado_o2(u2.get("oxigeno_02"))
                            eam = estado_o2(u2.get("oxigeno_am"))
                            if e00 != "normal" or e02 != "normal" or eam != "normal":
                                alertas.append({"ps": p["piscina"], "estado_00": e00, "estado_02": e02, "estado_am": eam, "estado_pm": "normal", **u2})
                        else:
                            eam = estado_o2(u2.get("oxigeno_am"))
                            epm = estado_o2(u2.get("oxigeno_pm"))
                            if eam != "normal" or epm != "normal":
                                alertas.append({"ps": p["piscina"], "estado_am": eam, "estado_pm": epm, **u2})

                    if campo == FIMASA3:
                        criticos = [a for a in alertas if a["estado_00"]=="critico" or a["estado_02"]=="critico" or a["estado_am"]=="critico"]
                    else:
                        criticos = [a for a in alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]

                    if alertas:
                        nivel = "ALERTA CRITICA" if criticos else "VIGILANCIA"
                        asunto = f"{'🔴' if criticos else '🟡'} {nivel} - {campo} - {fecha_hoy}"
                        cuerpo = f"{nivel}\nSector: {campo} | Fecha: {fecha_hoy}"
                        if campo == FIMASA3:
                            html_body = construir_html_biologo_fimasa3(campo, alertas, todas, fecha_hoy)
                        else:
                            html_body = construir_html_biologo(campo, alertas, todas, fecha_hoy)
                    else:
                        # Todo normal — enviar resumen verde
                        asunto = f"🟢 TODO NORMAL - {campo} - {fecha_hoy}"
                        cuerpo = f"RESUMEN DIARIO\nSector: {campo} | Fecha: {fecha_hoy}\nTodas las piscinas en rango normal."
                        if campo == FIMASA3:
                            html_body = construir_html_biologo_fimasa3(campo, [], todas, fecha_hoy)
                        else:
                            html_body = construir_html_biologo_normal(campo, todas, fecha_hoy)

                    enviar_email_postmark(u["email"], u.get("nombre",""), asunto, cuerpo, html=html_body)
                    enviados += 1

        print(f"Resumen diario enviado: {enviados} emails")
        return jsonify({"ok": True, "enviados": enviados, "fecha": fecha_hoy})
    except Exception as e:
        print(f"Error resumen diario: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/dashboard-gerencia", methods=["GET"])
def dashboard_gerencia():
    try:
        resumen = get_resumen_todos_campos()
        fecha_hoy = (datetime.utcnow() - timedelta(hours=5)).strftime("%d/%m/%Y")
        resultado = []
        for campo, piscinas in resumen.items():
            if campo == FIMASA3:
                alertas = sum(1 for p in piscinas if tiene_alerta_fimasa3(p["ultimo"]))
                criticos = sum(1 for p in piscinas if tiene_critico_fimasa3(p["ultimo"]))
            else:
                alertas = sum(1 for p in piscinas if
                    p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_VIGILANCIA or
                    p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_VIGILANCIA)
                criticos = sum(1 for p in piscinas if
                    p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_CRITICO or
                    p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_CRITICO)
            o2_am_vals = [p["ultimo"]["oxigeno_am"] for p in piscinas if p["ultimo"].get("oxigeno_am") is not None]
            prom_o2_am = round(sum(o2_am_vals)/len(o2_am_vals), 2) if o2_am_vals else None
            resultado.append({
                "campo": campo, "total": len(piscinas), "alertas": alertas, "criticos": criticos,
                "prom_o2_am": prom_o2_am,
                "ultima_fecha": piscinas[0]["ultimo"].get("fecha") if piscinas and piscinas[0]["ultimo"] else None,
                "piscinas": piscinas, "es_fimasa3": campo == FIMASA3
            })
        return jsonify({"ok": True, "resumen": resultado, "fecha": fecha_hoy})
    except Exception as e:
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
                SELECT fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm, corrida,
                       oxigeno_00, temp_00, oxigeno_02, temp_02
                FROM lecturas WHERE sector=%s AND piscina=%s AND corrida=%s
                ORDER BY created_at ASC
            """, (sector, piscina, int(corrida)))
        else:
            cur.execute("SELECT MAX(corrida) as mc FROM lecturas WHERE sector=%s AND piscina=%s", (sector, piscina))
            row = cur.fetchone()
            max_corrida = row["mc"] if row and row["mc"] else 1
            cur.execute("""
                SELECT fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm, corrida,
                       oxigeno_00, temp_00, oxigeno_02, temp_02
                FROM lecturas WHERE sector=%s AND piscina=%s AND corrida=%s
                ORDER BY created_at ASC LIMIT %s
            """, (sector, piscina, max_corrida, dias))
        rows = cur.fetchall()
        cur.close(); con.close()
        datos = [dict(r) for r in rows]
        corrida_num = datos[0]["corrida"] if datos else 1
        return jsonify({"ok": True, "datos": datos, "sector": sector, "piscina": piscina,
                        "corrida": corrida_num, "es_fimasa3": sector == FIMASA3})
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
        def sort_key(p):
            try: return (0, int(p))
            except: return (1, p)
        piscinas = sorted(piscinas_raw, key=sort_key)
        return jsonify({"piscinas": piscinas})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── IA ─────────────────────────────────────────────────────
def extraer_con_ia(imagen_b64, mime, campo=""):
    if campo == FIMASA3:
        prompt = (
            "Eres un experto leyendo hojas de parametros de piscinas camaroneras de FIMASA SECTOR 3. "
            "Este block tiene columnas que registran 4 mediciones por piscina usando nombres de columna enganosos. "
            "Las 4 mediciones y como leerlas son: "
            "MEDICION 00:30 (oxigeno_00 = columna OXIGENO AM, temp_00 = columna TEMPERATURA AM). "
            "MEDICION 02:30 (oxigeno_02 = columna OXIGENO PM, temp_02 = columna TEMPERATURA PM). "
            "MEDICION 05:00 (oxigeno_am = columna TB CM, temp_am = columna COLOR). "
            "MEDICION 16:00 (oxigeno_pm = columna ENT bajo CALIBRACION, temp_pm = columna SALID bajo CALIBRACION). "
            "El orden de columnas de izquierda a derecha es: PS, OXIGENO AM, OXIGENO PM, TEMPERATURA AM, TEMPERATURA PM, TB CM, COLOR, ENT, SALID. "
            "Lee cada valor DOS VECES verificando digito por digito. "
            "Rangos tipicos: oxigeno 1.0-15.0 mg/L, temperatura 20.0-35.0 grados C. "
            "Si un valor es ilegible usa null. "
            "Devuelve SOLO JSON valido sin texto extra ni markdown. "
            "Estructura exacta requerida: "
            '{"sector":"Fimasa 3","piscinas":[{"ps":"1","oxigeno_00":3.3,"temp_00":28.0,"oxigeno_02":2.8,"temp_02":28.1,"oxigeno_am":2.4,"temp_am":27.8,"oxigeno_pm":12.5,"temp_pm":32.0}]}'
        )
        max_tokens = 4000
    else:
        prompt = (
            "Eres un experto en acuicultura leyendo hojas de parametros de piscinas camaroneras. "
            "Lee cada valor DOS VECES antes de confirmar. "
            "Rangos tipicos: oxigeno 1.0-15.0 mg/L, temperatura 20.0-35.0 grados C. "
            "Distingue con cuidado: 3 vs 8, 1 vs 7, 5 vs 6, 0 vs 9, punto decimal vs coma. "
            "Si un valor es ilegible usa null. "
            "Devuelve SOLO JSON valido sin texto extra ni explicaciones: "
            '{"sector":"nombre","piscinas":[{"ps":"codigo","oxigeno_am":3.5,"oxigeno_pm":3.2,"temp_am":28.1,"temp_pm":27.8}]}'
        )
        max_tokens = 2000

    payload = {
        "model": "claude-opus-4-6",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": imagen_b64}},
            {"type": "text", "text": prompt}
        ]}]
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_API_KEY,"anthropic-version":"2023-06-01"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
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
        if sector == FIMASA3:
            e00 = estado_o2(p.get("oxigeno_00"))
            e02 = estado_o2(p.get("oxigeno_02"))
            eam = estado_o2(p.get("oxigeno_am"))
            if e00 != "normal" or e02 != "normal" or eam != "normal":
                alertas.append({**p, "estado_00": e00, "estado_02": e02, "estado_am": eam, "estado_pm": "normal"})
        else:
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
        rol = u.get("rol", "biologo")
        if rol == "gerencia":
            asunto, cuerpo, html_body = construir_html_gerencia_consolidado(fecha)
            if asunto:
                enviar_email_postmark(u["email"], u.get("nombre",""), asunto, cuerpo, html=html_body)
                enviados += 1
        else:
            if sector == FIMASA3:
                criticos = [a for a in alertas if a["estado_00"]=="critico" or a["estado_02"]=="critico" or a["estado_am"]=="critico"]
            else:
                criticos = [a for a in alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]
            nivel = "ALERTA CRITICA" if criticos else "VIGILANCIA"
            asunto = f"{'🔴' if criticos else '🟡'} {nivel} - {sector} - {fecha}"
            cuerpo = f"{nivel}\nSector: {sector} | Fecha: {fecha}\n"
            if sector == FIMASA3:
                html_body = construir_html_biologo_fimasa3(sector, alertas, datos.get("piscinas",[]), fecha)
            else:
                html_body = construir_html_biologo(sector, alertas, datos.get("piscinas",[]), fecha)
            enviar_email_postmark(u["email"], u.get("nombre",""), asunto, cuerpo, html=html_body)
            enviados += 1
    print(f"Emails enviados: {enviados}")
    return enviados

def celda_o2(val):
    if val is None:
        return '<td style="padding:6px 10px;text-align:center;color:#9ca3af">—</td>'
    if val < O2_CRITICO:
        return f'<td style="padding:6px 10px;text-align:center;background:#fee2e2;color:#991b1b;font-weight:700;font-size:15px">{val}</td>'
    if val < O2_VIGILANCIA:
        return f'<td style="padding:6px 10px;text-align:center;background:#fef3c7;color:#92400e;font-weight:700;font-size:15px">{val}</td>'
    return f'<td style="padding:6px 10px;text-align:center;background:#d1fae5;color:#065f46;font-weight:600">{val}</td>'

def celda_temp(val):
    if val is None:
        return '<td style="padding:6px 10px;text-align:center;color:#9ca3af">—</td>'
    return f'<td style="padding:6px 10px;text-align:center;color:#374151">{val}°C</td>'

def construir_html_biologo(sector, alertas_data, todas_piscinas, fecha):
    criticos  = [p for p in alertas_data if p["estado_am"]=="critico" or p["estado_pm"]=="critico"]
    vigilancia= [p for p in alertas_data if p not in criticos]
    ps_alerta = {p["ps"] for p in alertas_data}
    normales  = [p for p in todas_piscinas if p["ps"] not in ps_alerta]
    nivel_color = "#dc2626" if criticos else "#d97706"
    nivel_texto = "🔴 ALERTA CRÍTICA" if criticos else "🟡 VIGILANCIA"

    def sort_ps(lst):
        return sorted(lst, key=lambda x: (0,int(x["ps"])) if str(x["ps"]).isdigit() else (1,x["ps"]))

    def tabla(piscinas, titulo, bg):
        if not piscinas: return ""
        filas = ""
        for p in sort_ps(piscinas):
            filas += f"<tr><td style='padding:6px 10px;font-weight:700;text-align:center'>{p['ps']}</td>{celda_o2(p.get('oxigeno_am'))}{celda_o2(p.get('oxigeno_pm'))}{celda_temp(p.get('temp_am'))}{celda_temp(p.get('temp_pm'))}</tr>"
        return f"""<div style="margin-bottom:16px">
          <div style="background:{bg};color:white;padding:10px 14px;border-radius:8px 8px 0 0;font-weight:700;font-size:14px">{titulo}</div>
          <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">
            <thead><tr style="background:#f9fafb">
              <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">PISCINA</th>
              <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂ AM</th>
              <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂ PM</th>
              <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">T° AM</th>
              <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">T° PM</th>
            </tr></thead><tbody>{filas}</tbody></table></div>"""

    o2am_v = [p.get("oxigeno_am") for p in todas_piscinas if p.get("oxigeno_am") is not None]
    o2pm_v = [p.get("oxigeno_pm") for p in todas_piscinas if p.get("oxigeno_pm") is not None]
    tam_v  = [p.get("temp_am") for p in todas_piscinas if p.get("temp_am") is not None]
    prom_o2am = round(sum(o2am_v)/len(o2am_v),2) if o2am_v else "—"
    prom_o2pm = round(sum(o2pm_v)/len(o2pm_v),2) if o2pm_v else "—"
    prom_tam  = round(sum(tam_v)/len(tam_v),1) if tam_v else "—"

    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
      <div style="background:{nivel_color};color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="margin:0;font-size:22px">{nivel_texto}</h1>
        <p style="margin:6px 0 0;font-size:14px;opacity:.9">{sector} — {fecha}</p>
      </div>
      <div style="background:white;padding:20px;border:1px solid #e5e7eb;border-top:none">
        <div style="display:flex;gap:10px;margin-bottom:20px;text-align:center">
          <div style="flex:1;background:#fef2f2;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#dc2626">{len(criticos)}</div><div style="font-size:11px;color:#6b7280">Críticas</div></div>
          <div style="flex:1;background:#fffbeb;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#d97706">{len(vigilancia)}</div><div style="font-size:11px;color:#6b7280">Vigilancia</div></div>
          <div style="flex:1;background:#f0fdf4;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#16a34a">{len(normales)}</div><div style="font-size:11px;color:#6b7280">Normales</div></div>
        </div>
        {tabla(criticos, "🔴 PISCINAS CRÍTICAS — O₂ menor a 2.9 mg/L", "#dc2626")}
        {tabla(vigilancia, "🟡 PISCINAS EN VIGILANCIA — O₂ entre 2.9 y 3.5 mg/L", "#d97706")}
        {tabla(normales, "🟢 PISCINAS NORMALES", "#16a34a")}
        <div style="background:#f9fafb;border-radius:8px;padding:14px;margin-top:10px">
          <div style="font-weight:700;color:#374151;margin-bottom:8px">📊 PROMEDIOS DEL CAMPO</div>
          <table style="width:100%;text-align:center">
            <tr>
              <td style="padding:4px"><div style="font-size:18px;font-weight:700;color:#1D9E75">{prom_o2am}</div><div style="font-size:11px;color:#6b7280">O₂ AM mg/L</div></td>
              <td style="padding:4px"><div style="font-size:18px;font-weight:700;color:#0F6E56">{prom_o2pm}</div><div style="font-size:11px;color:#6b7280">O₂ PM mg/L</div></td>
              <td style="padding:4px"><div style="font-size:18px;font-weight:700;color:#f59e0b">{prom_tam}</div><div style="font-size:11px;color:#6b7280">T° AM °C</div></td>
            </tr>
          </table>
        </div>
      </div>
      <div style="background:#f9fafb;padding:10px;text-align:center;font-size:11px;color:#9ca3af;border-radius:0 0 12px 12px;border:1px solid #e5e7eb;border-top:none">Sistema de Alertas Camaronera Recorcholis S.A.</div>
    </div>"""
    return html

def construir_html_biologo_normal(sector, todas_piscinas, fecha):
    """Email resumen cuando todo está normal — sin alertas."""
    def sort_ps(lst):
        return sorted(lst, key=lambda x: (0,int(x["ps"])) if str(x["ps"]).isdigit() else (1,x["ps"]))

    filas = ""
    for p in sort_ps(todas_piscinas):
        filas += f"<tr><td style='padding:6px 10px;font-weight:700;text-align:center'>{p['ps']}</td>{celda_o2(p.get('oxigeno_am'))}{celda_o2(p.get('oxigeno_pm'))}{celda_temp(p.get('temp_am'))}{celda_temp(p.get('temp_pm'))}</tr>"

    o2am_v = [p.get("oxigeno_am") for p in todas_piscinas if p.get("oxigeno_am") is not None]
    o2pm_v = [p.get("oxigeno_pm") for p in todas_piscinas if p.get("oxigeno_pm") is not None]
    tam_v  = [p.get("temp_am") for p in todas_piscinas if p.get("temp_am") is not None]
    prom_o2am = round(sum(o2am_v)/len(o2am_v),2) if o2am_v else "—"
    prom_o2pm = round(sum(o2pm_v)/len(o2pm_v),2) if o2pm_v else "—"
    prom_tam  = round(sum(tam_v)/len(tam_v),1) if tam_v else "—"

    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
      <div style="background:#16a34a;color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="margin:0;font-size:22px">🟢 RESUMEN DIARIO — TODO NORMAL</h1>
        <p style="margin:6px 0 0;font-size:14px;opacity:.9">{sector} — {fecha}</p>
      </div>
      <div style="background:white;padding:20px;border:1px solid #e5e7eb;border-top:none">
        <p style="font-size:13px;color:#6b7280;margin-bottom:16px;text-align:center">✅ Todas las piscinas en rango normal</p>
        <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb">
          <thead><tr style="background:#f9fafb">
            <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">PISCINA</th>
            <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂ AM</th>
            <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂ PM</th>
            <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">T° AM</th>
            <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">T° PM</th>
          </tr></thead><tbody>{filas}</tbody></table>
        <div style="background:#f9fafb;border-radius:8px;padding:14px;margin-top:16px">
          <div style="font-weight:700;color:#374151;margin-bottom:8px">📊 PROMEDIOS DEL CAMPO</div>
          <table style="width:100%;text-align:center">
            <tr>
              <td style="padding:4px"><div style="font-size:18px;font-weight:700;color:#1D9E75">{prom_o2am}</div><div style="font-size:11px;color:#6b7280">O₂ AM mg/L</div></td>
              <td style="padding:4px"><div style="font-size:18px;font-weight:700;color:#0F6E56">{prom_o2pm}</div><div style="font-size:11px;color:#6b7280">O₂ PM mg/L</div></td>
              <td style="padding:4px"><div style="font-size:18px;font-weight:700;color:#f59e0b">{prom_tam}</div><div style="font-size:11px;color:#6b7280">T° AM °C</div></td>
            </tr>
          </table>
        </div>
      </div>
      <div style="background:#f9fafb;padding:10px;text-align:center;font-size:11px;color:#9ca3af;border-radius:0 0 12px 12px;border:1px solid #e5e7eb;border-top:none">Sistema de Alertas Camaronera Recorcholis S.A.</div>
    </div>"""
    return html

def construir_html_biologo_fimasa3(sector, alertas_data, todas_piscinas, fecha):
    criticos  = [p for p in alertas_data if p.get("estado_00")=="critico" or p.get("estado_02")=="critico" or p.get("estado_am")=="critico"]
    vigilancia= [p for p in alertas_data if p not in criticos]
    ps_alerta = {p["ps"] for p in alertas_data}
    normales  = [p for p in todas_piscinas if p["ps"] not in ps_alerta]
    nivel_color = "#16a34a" if not alertas_data else ("#dc2626" if criticos else "#d97706")
    nivel_texto = "🟢 RESUMEN DIARIO" if not alertas_data else ("🔴 ALERTA CRÍTICA" if criticos else "🟡 VIGILANCIA")

    def sort_ps(lst):
        return sorted(lst, key=lambda x: (0,int(x["ps"])) if str(x["ps"]).isdigit() else (1,x["ps"]))

    def tabla_fimasa(piscinas, titulo, bg):
        if not piscinas: return ""
        filas = ""
        for p in sort_ps(piscinas):
            filas += f"""<tr>
              <td style='padding:6px 8px;font-weight:700;text-align:center'>{p['ps']}</td>
              {celda_o2(p.get('oxigeno_00'))}{celda_temp(p.get('temp_00'))}
              {celda_o2(p.get('oxigeno_02'))}{celda_temp(p.get('temp_02'))}
              {celda_o2(p.get('oxigeno_am'))}{celda_temp(p.get('temp_am'))}
            </tr>"""
        return f"""<div style="margin-bottom:16px">
          <div style="background:{bg};color:white;padding:10px 14px;border-radius:8px 8px 0 0;font-weight:700;font-size:14px">{titulo}</div>
          <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">
            <thead>
              <tr style="background:#e0f2fe">
                <th rowspan="2" style="padding:6px;text-align:center;font-size:11px;color:#374151;border-bottom:1px solid #e5e7eb">PS</th>
                <th colspan="2" style="padding:6px;text-align:center;font-size:11px;color:#0369a1;border-bottom:1px solid #bae6fd">00:30</th>
                <th colspan="2" style="padding:6px;text-align:center;font-size:11px;color:#0369a1;border-bottom:1px solid #bae6fd">02:30</th>
                <th colspan="2" style="padding:6px;text-align:center;font-size:11px;color:#0369a1;border-bottom:1px solid #bae6fd">05:00</th>
              </tr>
              <tr style="background:#f0f9ff">
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
              </tr>
            </thead><tbody>{filas}</tbody></table></div>"""

    def tabla_normales_fimasa(piscinas):
        if not piscinas: return ""
        filas = ""
        for p in sort_ps(piscinas):
            filas += f"""<tr>
              <td style='padding:6px 8px;font-weight:700;text-align:center'>{p['ps']}</td>
              {celda_o2(p.get('oxigeno_00'))}{celda_temp(p.get('temp_00'))}
              {celda_o2(p.get('oxigeno_02'))}{celda_temp(p.get('temp_02'))}
              {celda_o2(p.get('oxigeno_am'))}{celda_temp(p.get('temp_am'))}
            </tr>"""
        return f"""<div style="margin-bottom:16px">
          <div style="background:#16a34a;color:white;padding:10px 14px;border-radius:8px 8px 0 0;font-weight:700;font-size:14px">🟢 PISCINAS NORMALES</div>
          <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">
            <thead>
              <tr style="background:#e0f2fe">
                <th rowspan="2" style="padding:6px;text-align:center;font-size:11px;color:#374151;border-bottom:1px solid #e5e7eb">PS</th>
                <th colspan="2" style="padding:6px;text-align:center;font-size:11px;color:#0369a1;border-bottom:1px solid #bae6fd">00:30</th>
                <th colspan="2" style="padding:6px;text-align:center;font-size:11px;color:#0369a1;border-bottom:1px solid #bae6fd">02:30</th>
                <th colspan="2" style="padding:6px;text-align:center;font-size:11px;color:#0369a1;border-bottom:1px solid #bae6fd">05:00</th>
              </tr>
              <tr style="background:#f0f9ff">
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
              </tr>
            </thead><tbody>{filas}</tbody></table></div>"""

    html = f"""<div style="font-family:Arial,sans-serif;max-width:650px;margin:0 auto">
      <div style="background:{nivel_color};color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="margin:0;font-size:22px">{nivel_texto}</h1>
        <p style="margin:6px 0 0;font-size:14px;opacity:.9">{sector} — {fecha}</p>
        <p style="margin:4px 0 0;font-size:12px;opacity:.8">Mediciones madrugada: 00:30 | 02:30 | 05:00</p>
      </div>
      <div style="background:white;padding:20px;border:1px solid #e5e7eb;border-top:none">
        <div style="display:flex;gap:10px;margin-bottom:20px;text-align:center">
          <div style="flex:1;background:#fef2f2;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#dc2626">{len(criticos)}</div><div style="font-size:11px;color:#6b7280">Críticas</div></div>
          <div style="flex:1;background:#fffbeb;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#d97706">{len(vigilancia)}</div><div style="font-size:11px;color:#6b7280">Vigilancia</div></div>
          <div style="flex:1;background:#f0fdf4;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#16a34a">{len(normales)}</div><div style="font-size:11px;color:#6b7280">Normales</div></div>
        </div>
        {tabla_fimasa(criticos, "🔴 PISCINAS CRÍTICAS — O₂ menor a 2.9 mg/L", "#dc2626") if criticos else ""}
        {tabla_fimasa(vigilancia, "🟡 PISCINAS EN VIGILANCIA — O₂ entre 2.9 y 3.5 mg/L", "#d97706") if vigilancia else ""}
        {tabla_normales_fimasa(normales) if normales else ""}
      </div>
      <div style="background:#f9fafb;padding:10px;text-align:center;font-size:11px;color:#9ca3af;border-radius:0 0 12px 12px;border:1px solid #e5e7eb;border-top:none">Sistema de Alertas Camaronera Recorcholis S.A.</div>
    </div>"""
    return html

def construir_html_gerencia_todo(fecha):
    """Email resumen para gerencia cuando no hay alertas — muestra todos los campos."""
    secciones = ""
    for campo in CAMPOS:
        piscinas_data = get_resumen_campo(campo, dias=1)
        if not piscinas_data: continue

        def sort_ps(lst):
            return sorted(lst, key=lambda x: (0,int(x["piscina"])) if str(x["piscina"]).isdigit() else (1,x["piscina"]))

        filas = ""
        for p in sort_ps(piscinas_data):
            u = p["ultimo"]
            if not u: continue
            filas += f"<tr><td style='padding:6px 10px;font-weight:700;text-align:center'>{p['piscina']}</td>{celda_o2(u.get('oxigeno_am'))}{celda_o2(u.get('oxigeno_pm'))}{celda_temp(u.get('temp_am'))}{celda_temp(u.get('temp_pm'))}</tr>"

        if filas:
            secciones += f"""<div style="margin-bottom:20px">
              <div style="background:#16a34a;color:white;padding:8px 14px;border-radius:8px 8px 0 0;font-weight:700;font-size:13px">📍 {campo}</div>
              <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">
                <thead><tr style="background:#f9fafb">
                  <th style="padding:6px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">PS</th>
                  <th style="padding:6px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂ AM</th>
                  <th style="padding:6px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂ PM</th>
                  <th style="padding:6px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">T° AM</th>
                  <th style="padding:6px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">T° PM</th>
                </tr></thead><tbody>{filas}</tbody></table></div>"""

    if not secciones:
        return None, None, None

    html = f"""<div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
      <div style="background:#16a34a;color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="margin:0;font-size:22px">🟢 RESUMEN DIARIO — TODO NORMAL</h1>
        <p style="margin:6px 0 0;font-size:14px;opacity:.9">Fecha: {fecha}</p>
      </div>
      <div style="background:white;padding:20px;border:1px solid #e5e7eb;border-top:none">
        <p style="font-size:13px;color:#6b7280;margin-bottom:20px;text-align:center">✅ Todas las piscinas reportadas en rango normal</p>
        {secciones}
      </div>
      <div style="background:#f9fafb;padding:10px;text-align:center;font-size:11px;color:#9ca3af;border-radius:0 0 12px 12px;border:1px solid #e5e7eb;border-top:none">Sistema de Alertas Camaronera Recorcholis S.A.</div>
    </div>"""
    asunto = f"🟢 RESUMEN DIARIO — Todo Normal — {fecha}"
    cuerpo = f"RESUMEN DIARIO\nFecha: {fecha}\nTodas las piscinas en rango normal."
    return asunto, cuerpo, html

def construir_html_gerencia_consolidado(fecha):
    campos_info = []
    total_criticos = 0
    total_vigilancia = 0
    for campo in CAMPOS:
        piscinas_data = get_resumen_campo(campo, dias=4)
        if not piscinas_data: continue
        if campo == FIMASA3:
            alertas  = [p for p in piscinas_data if tiene_alerta_fimasa3(p["ultimo"])]
            criticos = [p for p in alertas if tiene_critico_fimasa3(p["ultimo"])]
        else:
            alertas  = [p for p in piscinas_data if
                        (p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_VIGILANCIA) or
                        (p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_VIGILANCIA)]
            criticos = [p for p in alertas if
                        (p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_CRITICO) or
                        (p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_CRITICO)]
        if alertas:
            campos_info.append({"campo":campo,"piscinas":piscinas_data,"alertas":alertas,"criticos":criticos})
            total_criticos  += len(criticos)
            total_vigilancia+= len(alertas)-len(criticos)
    if not campos_info: return None, None, None
    nivel_color = "#dc2626" if total_criticos > 0 else "#d97706"
    nivel_texto = "🔴 ALERTA CRÍTICA" if total_criticos > 0 else "🟡 VIGILANCIA"
    asunto = f"{nivel_texto} — Reporte Consolidado — {fecha}"
    secciones = ""
    for info in campos_info:
        o2am_v = [p["ultimo"]["oxigeno_am"] for p in info["piscinas"] if p["ultimo"].get("oxigeno_am") is not None]
        prom   = round(sum(o2am_v)/len(o2am_v),2) if o2am_v else "—"
        campo_color = "#dc2626" if info["criticos"] else "#d97706"
        campo = info["campo"]

        def sort_ps(lst):
            return sorted(lst, key=lambda x: (0,int(x["piscina"])) if str(x["piscina"]).isdigit() else (1,x["piscina"]))

        filas = ""
        if campo == FIMASA3:
            for p in sort_ps(info["piscinas"]):
                u = p["ultimo"]
                if not u: continue
                hist = " → ".join([f"{h['fecha']}: {h.get('oxigeno_am','—')}" for h in p["historial"][:-1]]) if len(p["historial"])>1 else "—"
                filas += f"""<tr>
                  <td style='padding:6px 8px;font-weight:700;text-align:center'>{p['piscina']}</td>
                  {celda_o2(u.get('oxigeno_00'))}{celda_temp(u.get('temp_00'))}
                  {celda_o2(u.get('oxigeno_02'))}{celda_temp(u.get('temp_02'))}
                  {celda_o2(u.get('oxigeno_am'))}{celda_temp(u.get('temp_am'))}
                  {celda_o2(u.get('oxigeno_pm'))}{celda_temp(u.get('temp_pm'))}
                  <td style='padding:6px 8px;font-size:11px;color:#6b7280'>{hist}</td>
                </tr>"""
            secciones += f"""<div style="margin-bottom:24px">
              <div style="background:{campo_color};color:white;padding:10px 14px;border-radius:8px 8px 0 0;font-weight:700">
                📍 {campo} — {len(info['alertas'])} alertas | {len(info['criticos'])} críticas | O₂ 05:00 prom: {prom} mg/L
              </div>
              <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">
                <thead>
                  <tr style="background:#e0f2fe">
                    <th rowspan="2" style="padding:6px;text-align:center;font-size:10px;color:#374151;border-bottom:1px solid #e5e7eb">PS</th>
                    <th colspan="2" style="padding:5px;text-align:center;font-size:10px;color:#0369a1;border-bottom:1px solid #bae6fd">00:30</th>
                    <th colspan="2" style="padding:5px;text-align:center;font-size:10px;color:#0369a1;border-bottom:1px solid #bae6fd">02:30</th>
                    <th colspan="2" style="padding:5px;text-align:center;font-size:10px;color:#0369a1;border-bottom:1px solid #bae6fd">05:00</th>
                    <th colspan="2" style="padding:5px;text-align:center;font-size:10px;color:#7c3aed;border-bottom:1px solid #ede9fe">16:00</th>
                    <th rowspan="2" style="padding:6px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">Hist.</th>
                  </tr>
                  <tr style="background:#f0f9ff">
                    <th style="padding:4px;text-align:center;font-size:9px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th><th style="padding:4px;text-align:center;font-size:9px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
                    <th style="padding:4px;text-align:center;font-size:9px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th><th style="padding:4px;text-align:center;font-size:9px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
                    <th style="padding:4px;text-align:center;font-size:9px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th><th style="padding:4px;text-align:center;font-size:9px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
                    <th style="padding:4px;text-align:center;font-size:9px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂</th><th style="padding:4px;text-align:center;font-size:9px;color:#6b7280;border-bottom:1px solid #e5e7eb">T°</th>
                  </tr>
                </thead><tbody>{filas}</tbody></table></div>"""
        else:
            for p in sort_ps(info["piscinas"]):
                u = p["ultimo"]
                if not u: continue
                hist = " → ".join([f"{h['fecha']}: {h.get('oxigeno_am','—')}" for h in p["historial"][:-1]]) if len(p["historial"])>1 else "—"
                filas += f"<tr><td style='padding:6px 10px;font-weight:700;text-align:center'>{p['piscina']}</td>{celda_o2(u.get('oxigeno_am'))}{celda_o2(u.get('oxigeno_pm'))}{celda_temp(u.get('temp_am'))}{celda_temp(u.get('temp_pm'))}<td style='padding:6px 8px;font-size:11px;color:#6b7280'>{hist}</td></tr>"
            secciones += f"""<div style="margin-bottom:24px">
              <div style="background:{campo_color};color:white;padding:10px 14px;border-radius:8px 8px 0 0;font-weight:700">
                📍 {campo} — {len(info['alertas'])} alertas | {len(info['criticos'])} críticas | O₂ AM prom: {prom} mg/L
              </div>
              <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">
                <thead><tr style="background:#f9fafb">
                  <th style="padding:8px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">PS</th>
                  <th style="padding:8px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂ AM</th>
                  <th style="padding:8px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">O₂ PM</th>
                  <th style="padding:8px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">T° AM</th>
                  <th style="padding:8px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">T° PM</th>
                  <th style="padding:8px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">Últimos 3 días O₂ AM</th>
                </tr></thead><tbody>{filas}</tbody></table></div>"""

    html = f"""<div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
      <div style="background:{nivel_color};color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="margin:0;font-size:22px">{nivel_texto} — REPORTE CONSOLIDADO</h1>
        <p style="margin:6px 0 0;font-size:14px;opacity:.9">Fecha: {fecha}</p>
      </div>
      <div style="background:white;padding:20px;border:1px solid #e5e7eb;border-top:none">
        <div style="display:flex;gap:10px;margin-bottom:24px;text-align:center">
          <div style="flex:1;background:#fef2f2;border-radius:8px;padding:12px"><div style="font-size:28px;font-weight:700;color:#dc2626">{total_criticos}</div><div style="font-size:11px;color:#6b7280">Piscinas Críticas</div></div>
          <div style="flex:1;background:#fffbeb;border-radius:8px;padding:12px"><div style="font-size:28px;font-weight:700;color:#d97706">{total_vigilancia}</div><div style="font-size:11px;color:#6b7280">En Vigilancia</div></div>
          <div style="flex:1;background:#eff6ff;border-radius:8px;padding:12px"><div style="font-size:28px;font-weight:700;color:#2563eb">{len(campos_info)}</div><div style="font-size:11px;color:#6b7280">Campos Afectados</div></div>
        </div>
        {secciones}
      </div>
      <div style="background:#f9fafb;padding:10px;text-align:center;font-size:11px;color:#9ca3af;border-radius:0 0 12px 12px;border:1px solid #e5e7eb;border-top:none">Sistema de Alertas Camaronera Recorcholis S.A.</div>
    </div>"""
    cuerpo = f"{nivel_texto} — {fecha}\nCampos con alertas: {len(campos_info)} | Críticas: {total_criticos} | Vigilancia: {total_vigilancia}"
    return asunto, cuerpo, html

def enviar_email_postmark(dest_email, dest_nombre, asunto, cuerpo, html=None):
    try:
        print(f"Enviando Postmark a {dest_email}...")
        payload = {
            "From": EMAIL_REMITENTE,
            "To": dest_email,
            "Subject": asunto,
            "TextBody": cuerpo,
            "MessageStream": "outbound"
        }
        if html:
            payload["HtmlBody"] = html
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
