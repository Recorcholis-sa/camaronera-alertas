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
CAMPOS_GRANDES = set(CAMPOS)  # Todos los campos usan Sonnet para mayor precision

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
    for col, tipo in [
        ("oxigeno_00","REAL"), ("temp_00","REAL"),
        ("oxigeno_02","REAL"), ("temp_02","REAL"),
        ("tipo","TEXT DEFAULT 'piscina'")
    ]:
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
        tipo = p.get("tipo", "piscina")
        import re as _re
        ps_limpio = _re.sub(r"(?i)^(pre|pc)\s*", "", str(p["ps"])).strip()
        p["ps"] = ps_limpio if ps_limpio else p["ps"]
        corrida = get_corrida_actual(cur, sector, p["ps"])
        cur.execute(
            "SELECT id FROM lecturas WHERE sector=%s AND fecha=%s AND piscina=%s AND corrida=%s AND COALESCE(tipo,'piscina')=%s",
            (sector, fecha, p["ps"], corrida, tipo)
        )
        if cur.fetchone():
            if es_fimasa3:
                cur.execute("""
                    UPDATE lecturas SET oxigeno_am=%s, oxigeno_pm=%s, temp_am=%s, temp_pm=%s,
                        oxigeno_00=%s, temp_00=%s, oxigeno_02=%s, temp_02=%s, tipo=%s, created_at=%s
                    WHERE sector=%s AND fecha=%s AND piscina=%s AND corrida=%s
                """, (p.get("oxigeno_am"), p.get("oxigeno_pm"),
                      p.get("temp_am"), p.get("temp_pm"),
                      p.get("oxigeno_00"), p.get("temp_00"),
                      p.get("oxigeno_02"), p.get("temp_02"), tipo, now,
                      sector, fecha, p["ps"], corrida))
            else:
                cur.execute("""
                    UPDATE lecturas SET oxigeno_am=%s, oxigeno_pm=%s, temp_am=%s, temp_pm=%s,
                        tipo=%s, created_at=%s
                    WHERE sector=%s AND fecha=%s AND piscina=%s AND corrida=%s
                """, (p.get("oxigeno_am"), p.get("oxigeno_pm"),
                      p.get("temp_am"), p.get("temp_pm"), tipo, now,
                      sector, fecha, p["ps"], corrida))
        else:
            if es_fimasa3:
                cur.execute("""
                    INSERT INTO lecturas (fecha, sector, piscina, corrida, oxigeno_am, oxigeno_pm,
                        temp_am, temp_pm, oxigeno_00, temp_00, oxigeno_02, temp_02, tipo, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (fecha, sector, p["ps"], corrida,
                      p.get("oxigeno_am"), p.get("oxigeno_pm"),
                      p.get("temp_am"), p.get("temp_pm"),
                      p.get("oxigeno_00"), p.get("temp_00"),
                      p.get("oxigeno_02"), p.get("temp_02"), tipo, now))
            else:
                cur.execute("""
                    INSERT INTO lecturas (fecha, sector, piscina, corrida,
                        oxigeno_am, oxigeno_pm, temp_am, temp_pm, tipo, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (fecha, sector, p["ps"], corrida,
                      p.get("oxigeno_am"), p.get("oxigeno_pm"),
                      p.get("temp_am"), p.get("temp_pm"), tipo, now))
    con.commit()
    cur.close()
    con.close()

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
                if u.get("rol") == "gerencia" or u.get("nombre","").lower() == "gerencia":
                    u["rol"] = "gerencia"
                    u["campos"] = CAMPOS
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

def get_resumen_campo(sector, dias=3):
    try:
        con = get_conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT piscina, fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm,
                   oxigeno_00, temp_00, oxigeno_02, temp_02,
                   COALESCE(tipo, 'piscina') as tipo
            FROM lecturas
            WHERE sector=%s AND corrida=(
                SELECT MAX(corrida) FROM lecturas l2
                WHERE l2.sector=lecturas.sector AND l2.piscina=lecturas.piscina
                AND COALESCE(l2.tipo,'piscina')=COALESCE(lecturas.tipo,'piscina')
            )
            ORDER BY piscina, COALESCE(tipo,'piscina'), created_at DESC
        """, (sector,))
        rows = cur.fetchall()
        cur.close(); con.close()

        piscinas_dict = {}
        for r in rows:
            ps = r["piscina"]
            tipo_r = r.get("tipo", "piscina") or "piscina"
            key = (ps, tipo_r)
            if key not in piscinas_dict:
                piscinas_dict[key] = []
            if len(piscinas_dict[key]) < dias:
                piscinas_dict[key].append(dict(r))

        def sort_key(k):
            ps, tipo_k = k
            tipo_ord = {"piscina": 0, "precria": 1, "reservorio": 2}.get(tipo_k, 3)
            try: return (tipo_ord, 0, int(ps))
            except: return (tipo_ord, 1, ps)

        resultado = []
        for key in sorted(piscinas_dict.keys(), key=sort_key):
            ps, tipo_k = key
            historial = piscinas_dict[key]
            ultimo = historial[0] if historial else {}
            resultado.append({
                "piscina": ps,
                "ultimo": ultimo,
                "historial": list(reversed(historial)),
                "tipo": tipo_k
            })
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

def separar_por_tipo(piscinas_data):
    piscinas   = [p for p in piscinas_data if p.get("tipo","piscina") == "piscina"]
    precrias   = [p for p in piscinas_data if p.get("tipo","piscina") == "precria"]
    reservorio = [p for p in piscinas_data if p.get("tipo","piscina") == "reservorio"]
    return piscinas, precrias, reservorio

def tiene_alerta_fimasa3(u):
    vals = [u.get("oxigeno_00"), u.get("oxigeno_02"), u.get("oxigeno_am"), u.get("oxigeno_pm")]
    return any(v is not None and v < O2_VIGILANCIA for v in vals)

def tiene_critico_fimasa3(u):
    vals = [u.get("oxigeno_00"), u.get("oxigeno_02"), u.get("oxigeno_am"), u.get("oxigeno_pm")]
    return any(v is not None and v < O2_CRITICO for v in vals)

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
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        import io
        img_bytes = archivo.read()
        img = Image.open(io.BytesIO(img_bytes))
        if img.mode in ('RGBA', 'P', 'L'):
            img = img.convert('RGB')
        max_size = 2000
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)
        img = ImageEnhance.Contrast(img).enhance(1.4)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        img = ImageEnhance.Brightness(img).enhance(1.1)
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=92, optimize=True)
        img_bytes = output.getvalue()
        imagen_b64 = base64.b64encode(img_bytes).decode()
        mime = "image/jpeg"
        print(f"Imagen procesada: {len(img_bytes)//1024}KB ({img.width}x{img.height}px)")
    except Exception as e:
        print(f"No se pudo procesar imagen: {e}, usando original")
        archivo.seek(0)
        imagen_b64 = base64.b64encode(archivo.read()).decode()
        mime = archivo.content_type or "image/jpeg"
    try:
        print("Llamando a IA...")
        datos = extraer_con_ia(imagen_b64, mime, campo)
        total = len(datos.get("piscinas", []))
        print(f"IA respondio: {total} registros")
        for p in datos.get("piscinas", [])[:5]:
            print(f"  -> ps={p.get('ps')} tipo={p.get('tipo','piscina')} o2am={p.get('oxigeno_am')} o2pm={p.get('oxigeno_pm')}")
        if campo:
            datos["sector"] = campo
        fecha_hoy = (datetime.utcnow() - timedelta(hours=5)).strftime("%d/%m/%Y")
        datos["fecha"] = fecha_hoy
        guardar_lecturas(datos.get("sector", campo), fecha_hoy, datos.get("piscinas", []))
        # enviados = evaluar_y_notificar(datos, campo)  # Temporalmente desactivado
        enviados = 0
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
                asunto, cuerpo, html_body = construir_resumen_gerencia_completo(fecha_hoy)
                if asunto:
                    enviar_email_postmark(u["email"], u.get("nombre",""), asunto, cuerpo, html=html_body)
                    enviados += 1
            else:
                for campo in u.get("campos", []):
                    piscinas_data = get_resumen_campo(campo, dias=4)
                    if not piscinas_data:
                        continue
                    todas = [{"ps": p["piscina"], "tipo": p.get("tipo","piscina"), **p["ultimo"]} for p in piscinas_data]

                    alertas = []
                    for p in piscinas_data:
                        if p.get("tipo","piscina") == "reservorio":
                            continue
                        u2 = p["ultimo"]
                        if campo == FIMASA3:
                            e00 = estado_o2(u2.get("oxigeno_00"))
                            e02 = estado_o2(u2.get("oxigeno_02"))
                            eam = estado_o2(u2.get("oxigeno_am"))
                            if e00 != "normal" or e02 != "normal" or eam != "normal":
                                alertas.append({"ps": p["piscina"], "tipo": p.get("tipo","piscina"),
                                    "estado_00": e00, "estado_02": e02, "estado_am": eam, "estado_pm": "normal", **u2})
                        else:
                            eam = estado_o2(u2.get("oxigeno_am"))
                            epm = estado_o2(u2.get("oxigeno_pm"))
                            if eam != "normal" or epm != "normal":
                                alertas.append({"ps": p["piscina"], "tipo": p.get("tipo","piscina"),
                                    "estado_am": eam, "estado_pm": epm, **u2})

                    if campo == FIMASA3:
                        criticos = [a for a in alertas if a["estado_00"]=="critico" or a["estado_02"]=="critico" or a["estado_am"]=="critico"]
                    else:
                        criticos = [a for a in alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]

                    if alertas:
                        nivel = "ALERTA CRITICA" if criticos else "VIGILANCIA"
                        asunto = f"{campo} — {'🔴 CRITICO' if criticos else '🟡 VIGILANCIA'} — {fecha_hoy}"
                        cuerpo = f"{nivel}\nSector: {campo} | Fecha: {fecha_hoy}"
                        if campo == FIMASA3:
                            html_body = construir_html_biologo_fimasa3(campo, alertas, todas, fecha_hoy)
                        else:
                            html_body = construir_html_biologo(campo, alertas, todas, fecha_hoy)
                    else:
                        asunto = f"{campo} — Todo Normal — {fecha_hoy}"
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
            ps_y_pr = [p for p in piscinas if p.get("tipo","piscina") != "reservorio"]
            if campo == FIMASA3:
                alertas  = sum(1 for p in ps_y_pr if tiene_alerta_fimasa3(p["ultimo"]))
                criticos = sum(1 for p in ps_y_pr if tiene_critico_fimasa3(p["ultimo"]))
            else:
                alertas = sum(1 for p in ps_y_pr if
                    p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_VIGILANCIA or
                    p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_VIGILANCIA)
                criticos = sum(1 for p in ps_y_pr if
                    p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_CRITICO or
                    p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_CRITICO)

            solo_piscinas = [p for p in piscinas if p.get("tipo","piscina") == "piscina"]
            o2_am_vals = [p["ultimo"]["oxigeno_am"] for p in solo_piscinas if p["ultimo"].get("oxigeno_am") is not None]
            o2_pm_vals = [p["ultimo"]["oxigeno_pm"] for p in solo_piscinas if p["ultimo"].get("oxigeno_pm") is not None]
            prom_o2_am = round(sum(o2_am_vals)/len(o2_am_vals), 2) if o2_am_vals else None
            prom_o2_pm = round(sum(o2_pm_vals)/len(o2_pm_vals), 2) if o2_pm_vals else None
            temp_am_vals = [p["ultimo"]["temp_am"] for p in solo_piscinas if p["ultimo"].get("temp_am") is not None]
            temp_pm_vals = [p["ultimo"]["temp_pm"] for p in solo_piscinas if p["ultimo"].get("temp_pm") is not None]
            prom_temp_am = round(sum(temp_am_vals)/len(temp_am_vals), 1) if temp_am_vals else None
            prom_temp_pm = round(sum(temp_pm_vals)/len(temp_pm_vals), 1) if temp_pm_vals else None

            resultado.append({
                "campo": campo, "total": len(ps_y_pr), "alertas": alertas, "criticos": criticos,
                "prom_o2_am": prom_o2_am, "prom_o2_pm": prom_o2_pm,
                "prom_temp_am": prom_temp_am, "prom_temp_pm": prom_temp_pm,
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
        tipo_filtro = request.args.get("tipo", None)
        if not sector or not piscina:
            return jsonify({"error": "sector y piscina son requeridos"}), 400
        con = get_conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        if corrida:
            cur.execute("""
                SELECT fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm, corrida,
                       oxigeno_00, temp_00, oxigeno_02, temp_02, COALESCE(tipo,'piscina') as tipo
                FROM lecturas WHERE sector=%s AND piscina=%s AND corrida=%s
                ORDER BY created_at ASC
            """, (sector, piscina, int(corrida)))
        else:
            tipo_where = "AND COALESCE(tipo,'piscina')=%s" if tipo_filtro else ""
            tipo_params = [tipo_filtro] if tipo_filtro else []
            cur.execute(f"SELECT MAX(corrida) as mc FROM lecturas WHERE sector=%s AND piscina=%s {tipo_where}", [sector, piscina] + tipo_params)
            row = cur.fetchone()
            max_corrida = row["mc"] if row and row["mc"] else 1
            tipo_cond = "AND COALESCE(tipo,'piscina')=%s" if tipo_filtro else ""
            tipo_val  = [tipo_filtro] if tipo_filtro else []
            if dias == 1:
                cur.execute(f"""
                    SELECT DISTINCT ON (fecha) fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm, corrida,
                           oxigeno_00, temp_00, oxigeno_02, temp_02, COALESCE(tipo,'piscina') as tipo
                    FROM lecturas WHERE sector=%s AND piscina=%s AND corrida=%s {tipo_cond}
                    ORDER BY fecha DESC, created_at DESC LIMIT 1
                """, [sector, piscina, max_corrida] + tipo_val)
            else:
                cur.execute(f"""
                    SELECT * FROM (
                        SELECT DISTINCT ON (fecha) fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm, corrida,
                               oxigeno_00, temp_00, oxigeno_02, temp_02, COALESCE(tipo,'piscina') as tipo,
                               created_at
                        FROM lecturas WHERE sector=%s AND piscina=%s AND corrida=%s {tipo_cond}
                        ORDER BY fecha DESC, created_at DESC LIMIT %s
                    ) sub ORDER BY created_at ASC
                """, [sector, piscina, max_corrida] + tipo_val + [dias])
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
        cur = con.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT DISTINCT piscina, COALESCE(tipo,'piscina') as tipo FROM lecturas WHERE sector=%s ORDER BY piscina", (sector,))
        rows = cur.fetchall()
        cur.close(); con.close()
        def sort_key(r):
            tipo_ord = {"piscina": 0, "precria": 1, "reservorio": 2}.get(r["tipo"], 3)
            try: return (tipo_ord, 0, int(r["piscina"]))
            except: return (tipo_ord, 1, r["piscina"])
        sorted_rows = sorted([dict(r) for r in rows], key=sort_key)
        return jsonify({"piscinas": [r["piscina"] for r in sorted_rows], "piscinas_tipo": sorted_rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── IA ─────────────────────────────────────────────────────
def extraer_con_ia(imagen_b64, mime, campo=""):
    if campo == FIMASA3:
        prompt = (
            "Eres un experto leyendo hojas de parametros de piscinas camaroneras de FIMASA SECTOR 3. "
            "Ignora completamente los encabezados del block. "
            "En cada fila, despues del numero de piscina (PS), hay exactamente 8 valores numericos en orden. "
            "Asigna esos 8 valores estrictamente por posicion: "
            "1er valor = oxigeno_00 (medicion 00:30). "
            "2do valor = temp_00 (medicion 00:30). "
            "3er valor = oxigeno_02 (medicion 02:30). "
            "4to valor = temp_02 (medicion 02:30). "
            "5to valor = oxigeno_am (medicion 05:00). "
            "6to valor = temp_am (medicion 05:00). "
            "7mo valor = oxigeno_pm (medicion 16:00). "
            "8vo valor = temp_pm (medicion 16:00). "
            "Los rangos te ayudan a distinguir oxigeno de temperatura: oxigeno entre 1.0 y 15.0 mg/L, temperatura entre 20.0 y 35.0 grados C. "
            "CRITICO: Si una posicion no tiene valor escrito o la celda esta en blanco, devuelve null para ese campo. NO inventes valores. "
            "NO copies ni repitas valores de otras posiciones para rellenar celdas vacias. "
            "Es completamente normal que oxigeno_pm y temp_pm (medicion 16:00) sean null si esa medicion aun no se ha registrado en el block. "
            "Si en alguna posicion el valor no existe o es ilegible usa null. "
            "El block puede tener 3 secciones separadas por palabras escritas: PRECRIAS y RESERVORIO. "
            "Asigna tipo segun la seccion: antes de PRECRIAS = tipo piscina, bajo PRECRIAS = tipo precria, bajo RESERVORIO = tipo reservorio. "
            "En la seccion PRECRIAS puede aparecer Pre, pre, Pc, pc u otras siglas antes del numero en columna PS. Ignoralas y usa solo el numero. "
            "Lee cada valor DOS VECES verificando digito por digito. "
            'Devuelve SOLO JSON valido sin texto extra ni markdown: {"sector":"Fimasa 3","piscinas":[{"ps":"1","tipo":"piscina","oxigeno_00":3.3,"temp_00":28.0,"oxigeno_02":2.8,"temp_02":28.1,"oxigeno_am":2.4,"temp_am":27.8,"oxigeno_pm":null,"temp_pm":null}]}'
        )
        max_tokens = 4000
    else:
        prompt = (
            "Eres un experto en acuicultura leyendo hojas de parametros de piscinas camaroneras. "
            "Lee cada valor DOS VECES antes de confirmar. "
            "El block tiene estas columnas en orden: PS | OXIGENO AM | OXIGENO PM | TEMPERATURA AM | TEMPERATURA PM | TB CM | COLOR | CALIBRACION ENT | CALIBRACION SALID | NIVEL cm | SAL %. "
            "IGNORA completamente: el membrete (nombre de empresa, sector, logo), los encabezados de columna (PS, OXIGENO, TEMPERATURA, TB CM, COLOR, CALIBRACION, NIVEL, SAL, AM, PM, ENT, SALID), la fecha, la hora, y cualquier texto impreso del block que no sea un dato de piscina. "
            "Solo lee las filas de datos donde la columna PS tiene un numero o prefijo de piscina. "
            "Los valores de CALIBRACION (numeros como 40, 47, 49, 51) y NIVEL (numeros como 200, 210, 220) NO son oxigeno ni temperatura — ignoralos totalmente. "
            "Rangos tipicos: oxigeno 1.0-15.0 mg/L, temperatura 20.0-35.0 grados C. "
            "Distingue con cuidado: 3 vs 8, 1 vs 7, 5 vs 6, 0 vs 9, punto decimal vs coma. "
            "CRITICO: Solo incluye en el JSON las filas donde puedas ver claramente un numero o codigo escrito en la columna PS. Si una fila tiene el PS en blanco, ilegible, o no estas seguro de lo que dice, NO la incluyas. Es mejor omitir una fila dudosa que inventar datos. "
            "NO copies ni repitas el valor AM en la columna PM si PM esta vacia. "
            "Es completamente normal recibir fotos solo con datos AM (manana) donde oxigeno_pm y temp_pm van null. "
            "Si un valor es ilegible usa null. "
            "El block tiene 3 secciones separadas por titulos escritos: piscinas normales, PRECRIAS y RESERVORIO. "
            "PASO 1 — Identifica las secciones: "
            "Busca activamente las palabras PRECRIAS (o variantes: PRECRIA, Precrias, PRECRIA) y RESERVORIO (o variantes: RESERUORIO, RECEBORIO, RESERV) escritas en cualquier parte del block como titulo o en la columna PS. "
            "Todo lo que esta ANTES de PRECRIAS = seccion piscinas. "
            "Todo lo que esta DESPUES de PRECRIAS y ANTES de RESERVORIO = seccion precrias. "
            "Todo lo que esta DESPUES de RESERVORIO = seccion reservorio. "
            "PASO 2 — Asigna tipo segun seccion Y prefijo combinados: "
            "1. Si la fila esta en seccion precrias O si el PS empieza con Pre/pre/PR = tipo precria. "
            "2. Si la fila esta en seccion reservorio O si el PS empieza con Est/EST/Rs/RS/Rc/RC (con o sin numero) = tipo reservorio. "
            "3. Si la fila esta en seccion piscinas y el PS es un numero sin prefijo = tipo piscina. "
            "IMPORTANTE: Los numeros de piscina en la seccion PRECRIAS pueden coincidir con numeros de piscinas normales — no importa, si estan en esa seccion son precrias. "
            "ESTANDAR DE PS: precrias sin prefijo conservan su numero (ej: 11, 16, 33). Si tienen prefijo normaliza a 'Pre 1', 'Pre 2'. Reservorio normaliza a 'Est 1', 'Est 2', 'Rs 1', 'Rs 2', 'Rc 1', 'Rc 2' o solo 'Est', 'Rs', 'Rc' si no tienen numero. "
            'Devuelve SOLO JSON valido sin texto extra ni explicaciones: {"sector":"nombre","piscinas":[{"ps":"1","tipo":"piscina","oxigeno_am":3.5,"oxigeno_pm":null,"temp_am":28.1,"temp_pm":null}]}'
        )
        max_tokens = 4096

    modelo = "claude-opus-4-8"  # Todos los campos usan Opus para mayor precision
    print(f"Usando modelo: {modelo} para campo: {campo}")
    payload = {
        "model": modelo,
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
    try:
        with urllib.request.urlopen(req, timeout=110) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        print(f"Claude API error {e.code}: {err_body[:300]}")
        raise Exception(f"HTTP Error {e.code}: {err_body[:200]}")

    text = "".join(b.get("text","") for b in resp["content"]).strip()
    if "```" in text:
        text = text.split("```")[1].replace("json","").strip()
        if "```" in text:
            text = text.split("```")[0].strip()
    start_idx = text.find("{")
    end_idx   = text.rfind("}") + 1
    if start_idx >= 0 and end_idx > start_idx:
        text = text[start_idx:end_idx]
    try:
        return json.loads(text)
    except Exception as e:
        print(f"JSON parse error: {e}, intentando reparar...")
        fixed = text.rstrip()
        if fixed.endswith(','):
            fixed = fixed[:-1]
        opens_brace   = fixed.count('{') - fixed.count('}')
        opens_bracket = fixed.count('[') - fixed.count(']')
        fixed += ']' * opens_bracket + '}' * opens_brace
        return json.loads(fixed)


def estado_o2(v):
    if v is None: return "normal"
    return "critico" if v < O2_CRITICO else "vigilancia" if v < O2_VIGILANCIA else "normal"

def evaluar_y_notificar(datos, campo_param):
    usuarios = leer_usuarios()
    sector = datos.get("sector", campo_param)
    fecha  = datos.get("fecha", "")
    alertas = []
    for p in datos.get("piscinas", []):
        if p.get("tipo","piscina") == "reservorio":
            continue
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
    print(f"Alertas: {len(alertas)} registros, sector: {sector}")
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
            asunto, cuerpo, html_body = construir_resumen_gerencia_completo(fecha)
            if asunto:
                enviar_email_postmark(u["email"], u.get("nombre",""), asunto, cuerpo, html=html_body)
                enviados += 1
        else:
            if sector == FIMASA3:
                criticos = [a for a in alertas if a["estado_00"]=="critico" or a["estado_02"]=="critico" or a["estado_am"]=="critico"]
            else:
                criticos = [a for a in alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]
            nivel = "ALERTA CRITICA" if criticos else "VIGILANCIA"
            asunto = f"{sector} — {'🔴 CRITICO' if criticos else '🟡 VIGILANCIA'} — {fecha}"
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

def sort_ps(lst, key="ps"):
    return sorted(lst, key=lambda x: (0,int(x[key])) if str(x[key]).isdigit() else (1,str(x[key])))

def bloque_promedios(piscinas, key_am="oxigeno_am", key_pm="oxigeno_pm", key_tam="temp_am"):
    o2am_v = [p.get(key_am) for p in piscinas if p.get(key_am) is not None]
    o2pm_v = [p.get(key_pm) for p in piscinas if p.get(key_pm) is not None]
    tam_v  = [p.get(key_tam) for p in piscinas if p.get(key_tam) is not None]
    prom_o2am = round(sum(o2am_v)/len(o2am_v),2) if o2am_v else "—"
    prom_o2pm = round(sum(o2pm_v)/len(o2pm_v),2) if o2pm_v else "—"
    prom_tam  = round(sum(tam_v)/len(tam_v),1) if tam_v else "—"
    return f"""<div style="background:#f9fafb;border-radius:8px;padding:14px;margin-top:4px;margin-bottom:16px">
      <div style="font-weight:700;color:#374151;margin-bottom:8px;font-size:13px">PROMEDIOS PISCINAS</div>
      <table style="width:100%;text-align:center">
        <tr>
          <td style="padding:4px"><div style="font-size:18px;font-weight:700;color:#1D9E75">{prom_o2am}</div><div style="font-size:11px;color:#6b7280">O2 AM mg/L</div></td>
          <td style="padding:4px"><div style="font-size:18px;font-weight:700;color:#0F6E56">{prom_o2pm}</div><div style="font-size:11px;color:#6b7280">O2 PM mg/L</div></td>
          <td style="padding:4px"><div style="font-size:18px;font-weight:700;color:#f59e0b">{prom_tam}</div><div style="font-size:11px;color:#6b7280">T AM C</div></td>
        </tr>
      </table>
    </div>"""

def tabla_simple(piscinas, titulo, bg):
    if not piscinas: return ""
    filas = ""
    for p in sort_ps(piscinas):
        filas += f"<tr><td style='padding:6px 10px;font-weight:700;text-align:center'>{p['ps']}</td>{celda_o2(p.get('oxigeno_am'))}{celda_o2(p.get('oxigeno_pm'))}{celda_temp(p.get('temp_am'))}{celda_temp(p.get('temp_pm'))}</tr>"
    return f"""<div style="margin-bottom:4px">
      <div style="background:{bg};color:white;padding:10px 14px;border-radius:8px 8px 0 0;font-weight:700;font-size:14px">{titulo}</div>
      <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">
        <thead><tr style="background:#f9fafb">
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">PS</th>
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2 AM</th>
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2 PM</th>
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">T AM</th>
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">T PM</th>
        </tr></thead><tbody>{filas}</tbody></table></div>"""

def seccion_reservorio(reservorio):
    if not reservorio: return ""
    filas = ""
    for p in sort_ps(reservorio):
        filas += f"<tr><td style='padding:6px 10px;font-weight:700;text-align:center'>{p['ps']}</td>{celda_o2(p.get('oxigeno_am'))}{celda_o2(p.get('oxigeno_pm'))}{celda_temp(p.get('temp_am'))}{celda_temp(p.get('temp_pm'))}</tr>"
    return f"""<div style="margin-bottom:16px">
      <div style="background:#0891b2;color:white;padding:10px 14px;border-radius:8px 8px 0 0;font-weight:700;font-size:14px">RESERVORIO</div>
      <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">
        <thead><tr style="background:#f9fafb">
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">ID</th>
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2 AM</th>
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2 PM</th>
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">T AM</th>
          <th style="padding:8px;text-align:center;font-size:12px;color:#6b7280;border-bottom:1px solid #e5e7eb">T PM</th>
        </tr></thead><tbody>{filas}</tbody></table></div>"""

def construir_resumen_gerencia_completo(fecha):
    campos_alerta = []
    campos_normal = []
    total_criticos = 0
    total_vigilancia = 0

    for campo in CAMPOS:
        piscinas_data = get_resumen_campo(campo, dias=4)
        if not piscinas_data:
            continue
        ps_y_pr = [p for p in piscinas_data if p.get("tipo","piscina") != "reservorio"]
        ps_data  = [p for p in piscinas_data if p.get("tipo","piscina") == "piscina"]
        pr_data  = [p for p in piscinas_data if p.get("tipo","piscina") == "precria"]

        if campo == FIMASA3:
            alertas  = [p for p in ps_y_pr if tiene_alerta_fimasa3(p["ultimo"])]
            criticos = [p for p in alertas if tiene_critico_fimasa3(p["ultimo"])]
        else:
            alertas  = [p for p in ps_y_pr if
                        (p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_VIGILANCIA) or
                        (p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_VIGILANCIA)]
            criticos = [p for p in alertas if
                        (p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_CRITICO) or
                        (p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_CRITICO)]

        o2am_v = [p["ultimo"]["oxigeno_am"] for p in ps_data if p["ultimo"].get("oxigeno_am") is not None]
        o2pm_v = [p["ultimo"]["oxigeno_pm"] for p in ps_data if p["ultimo"].get("oxigeno_pm") is not None]
        tam_v  = [p["ultimo"]["temp_am"] for p in ps_data if p["ultimo"].get("temp_am") is not None]
        prom_am  = round(sum(o2am_v)/len(o2am_v),2) if o2am_v else "—"
        prom_pm  = round(sum(o2pm_v)/len(o2pm_v),2) if o2pm_v else "—"
        prom_tam = round(sum(tam_v)/len(tam_v),1) if tam_v else "—"
        ultima_fecha = piscinas_data[0]["ultimo"].get("fecha","") if piscinas_data and piscinas_data[0]["ultimo"] else ""

        if alertas:
            total_criticos  += len(criticos)
            total_vigilancia += len(alertas) - len(criticos)
            campos_alerta.append({
                "campo": campo, "piscinas_data": piscinas_data,
                "ps_data": ps_data, "pr_data": pr_data,
                "alertas": alertas, "criticos": criticos,
                "prom_am": prom_am, "prom_pm": prom_pm, "prom_tam": prom_tam,
                "ultima_fecha": ultima_fecha
            })
        else:
            campos_normal.append({
                "campo": campo, "prom_am": prom_am, "prom_pm": prom_pm,
                "prom_tam": prom_tam, "ultima_fecha": ultima_fecha,
                "n_piscinas": len(ps_data)
            })

    if not campos_alerta and not campos_normal:
        return None, None, None

    hay_criticos = total_criticos > 0
    hay_alertas  = total_criticos > 0 or total_vigilancia > 0

    if hay_criticos:
        nivel_color = "#dc2626"
        nivel_texto = "ALERTA CRITICA"
        asunto = f"Resumen Alertas — {fecha} — CRITICO"
    elif hay_alertas:
        nivel_color = "#d97706"
        nivel_texto = "VIGILANCIA"
        asunto = f"Resumen Alertas — {fecha} — VIGILANCIA"
    else:
        nivel_color = "#16a34a"
        nivel_texto = "TODO NORMAL"
        asunto = f"Resumen Alertas — {fecha} — Normal"

    html_alertas = ""
    for info in campos_alerta:
        campo = info["campo"]
        campo_color = "#dc2626" if info["criticos"] else "#d97706"

        def filas_detalle(lst, key="piscina"):
            filas = ""
            for p in sort_ps(lst, key):
                u = p["ultimo"]
                if not u: continue
                hist = " -> ".join([f"{h['fecha']}: {h.get('oxigeno_am','—')}" for h in p["historial"][:-1]]) if len(p["historial"])>1 else "—"
                filas += f"<tr><td style='padding:6px 8px;font-weight:700;text-align:center'>{p[key]}</td>{celda_o2(u.get('oxigeno_am'))}{celda_o2(u.get('oxigeno_pm'))}{celda_temp(u.get('temp_am'))}{celda_temp(u.get('temp_pm'))}<td style='padding:6px 8px;font-size:11px;color:#6b7280'>{hist}</td></tr>"
            return filas

        cabecera = """<thead><tr style="background:#f9fafb">
          <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">PS</th>
          <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2 AM</th>
          <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2 PM</th>
          <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">T AM</th>
          <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">T PM</th>
          <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">Hist.</th>
        </tr></thead>"""

        filas_ps = filas_detalle(info["ps_data"])
        filas_pr = filas_detalle(info["pr_data"])
        bloque_ps = f'<table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">{cabecera}<tbody>{filas_ps}</tbody></table>' if filas_ps else ""
        bloque_pr = f'<div style="margin-top:6px"><div style="background:#6366f1;color:white;padding:5px 12px;font-weight:700;font-size:12px">PRECRIAS</div><table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-top:none">{cabecera}<tbody>{filas_pr}</tbody></table></div>' if filas_pr else ""

        html_alertas += f"""<div style="margin-bottom:20px;border-radius:10px;overflow:hidden;border:1px solid #e5e7eb">
          <div style="background:{campo_color};color:white;padding:10px 14px;font-weight:700;font-size:14px;display:flex;justify-content:space-between;align-items:center">
            <span>{campo} — {len(info['alertas'])} alertas | {len(info['criticos'])} criticas</span>
            <span style="font-size:12px;opacity:.85">O2 AM prom: {info['prom_am']} mg/L</span>
          </div>
          {bloque_ps}{bloque_pr}
        </div>"""

    html_normal = ""
    if campos_normal:
        filas_norm = ""
        for c in campos_normal:
            if c["ultima_fecha"]:
                filas_norm += f"""<tr>
                  <td style="padding:7px 10px;font-weight:600;color:#374151">{c['campo']}</td>
                  <td style="padding:7px 10px;text-align:center;background:#d1fae5;color:#065f46;font-weight:600">{c['prom_am']}</td>
                  <td style="padding:7px 10px;text-align:center;background:#d1fae5;color:#065f46;font-weight:600">{c['prom_pm']}</td>
                  <td style="padding:7px 10px;text-align:center;color:#374151">{c['prom_tam']}</td>
                  <td style="padding:7px 10px;text-align:center;font-size:11px;color:#9ca3af">{c['ultima_fecha']}</td>
                </tr>"""
        if filas_norm:
            html_normal = f"""<div style="margin-bottom:20px;border-radius:10px;overflow:hidden;border:1px solid #e5e7eb">
              <div style="background:#16a34a;color:white;padding:10px 14px;font-weight:700;font-size:14px">
                CAMPOS SIN NOVEDAD — Promedios O2
              </div>
              <table style="width:100%;border-collapse:collapse">
                <thead><tr style="background:#f9fafb">
                  <th style="padding:7px 10px;text-align:left;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">Campo</th>
                  <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2 AM</th>
                  <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2 PM</th>
                  <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">T AM</th>
                  <th style="padding:7px;text-align:center;font-size:11px;color:#6b7280;border-bottom:1px solid #e5e7eb">Fecha</th>
                </tr></thead>
                <tbody>{filas_norm}</tbody>
              </table>
            </div>"""

    html = f"""<div style="font-family:Arial,sans-serif;max-width:720px;margin:0 auto">
      <div style="background:{nivel_color};color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="margin:0;font-size:22px">{nivel_texto} — REPORTE CONSOLIDADO</h1>
        <p style="margin:6px 0 0;font-size:14px;opacity:.9">Fecha: {fecha}</p>
      </div>
      <div style="background:white;padding:20px;border:1px solid #e5e7eb;border-top:none">
        <div style="display:flex;gap:10px;margin-bottom:20px;text-align:center">
          <div style="flex:1;background:#fef2f2;border-radius:8px;padding:12px"><div style="font-size:26px;font-weight:700;color:#dc2626">{total_criticos}</div><div style="font-size:11px;color:#6b7280">Criticas</div></div>
          <div style="flex:1;background:#fffbeb;border-radius:8px;padding:12px"><div style="font-size:26px;font-weight:700;color:#d97706">{total_vigilancia}</div><div style="font-size:11px;color:#6b7280">Vigilancia</div></div>
          <div style="flex:1;background:#f0fdf4;border-radius:8px;padding:12px"><div style="font-size:26px;font-weight:700;color:#16a34a">{len(campos_alerta)}</div><div style="font-size:11px;color:#6b7280">Campos con alerta</div></div>
          <div style="flex:1;background:#f9fafb;border-radius:8px;padding:12px"><div style="font-size:26px;font-weight:700;color:#374151">{len(campos_normal)}</div><div style="font-size:11px;color:#6b7280">Sin novedad</div></div>
        </div>
        {html_alertas}
        {html_normal}
      </div>
      <div style="background:#f9fafb;padding:10px;text-align:center;font-size:11px;color:#9ca3af;border-radius:0 0 12px 12px;border:1px solid #e5e7eb;border-top:none">Sistema de Alertas Camaronera Recorcholis S.A.</div>
    </div>"""

    wa_campos = []
    for campo in CAMPOS:
        criticas_wa = []
        for info in campos_alerta:
            if info["campo"] == campo:
                for p in info["ps_data"] + info["pr_data"]:
                    u = p["ultimo"]
                    if not u: continue
                    o2am = u.get("oxigeno_am")
                    o2pm = u.get("oxigeno_pm")
                    if o2am is not None and o2am < O2_CRITICO:
                        criticas_wa.append({"piscina": p["piscina"], "o2": str(o2am)})
                    elif o2pm is not None and o2pm < O2_CRITICO:
                        criticas_wa.append({"piscina": p["piscina"], "o2": str(o2pm)})
        wa_campos.append({"nombre": campo, "criticas": criticas_wa})

    import json as _json
    wa_json = _json.dumps({"fecha": fecha, "campos": wa_campos}, ensure_ascii=False)
    wa_block = f'''<div style="display:none">[WA_ALERT]\n{wa_json}\n[/WA_ALERT]</div>'''

    html = wa_block + html

    cuerpo = f"{nivel_texto} — {fecha} | Criticas: {total_criticos} | Vigilancia: {total_vigilancia} | Sin novedad: {len(campos_normal)}"
    return asunto, cuerpo, html

def construir_html_biologo(sector, alertas_data, todas_piscinas, fecha):
    ps_todas    = [p for p in todas_piscinas if p.get("tipo","piscina") == "piscina"]
    pr_todas    = [p for p in todas_piscinas if p.get("tipo","piscina") == "precria"]
    res_todas   = [p for p in todas_piscinas if p.get("tipo","piscina") == "reservorio"]

    ps_alertas  = [a for a in alertas_data if a.get("tipo","piscina") == "piscina"]
    pr_alertas  = [a for a in alertas_data if a.get("tipo","piscina") == "precria"]

    criticos_ps = [a for a in ps_alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]
    criticos_pr = [a for a in pr_alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]
    criticos    = criticos_ps + criticos_pr
    vigilancia_ps = [a for a in ps_alertas if a not in criticos_ps]
    vigilancia_pr = [a for a in pr_alertas if a not in criticos_pr]

    ps_alerta_ids = {a["ps"] for a in alertas_data}
    normales_ps = [p for p in ps_todas if p["ps"] not in ps_alerta_ids]
    normales_pr = [p for p in pr_todas if p["ps"] not in ps_alerta_ids]

    nivel_color = "#dc2626" if criticos else "#d97706"
    nivel_texto = "ALERTA CRITICA" if criticos else "VIGILANCIA"

    html_piscinas = ""
    if criticos_ps:
        html_piscinas += tabla_simple(criticos_ps, "PISCINAS CRITICAS — O2 menor a 2.9 mg/L", "#dc2626")
    if vigilancia_ps:
        html_piscinas += tabla_simple(vigilancia_ps, "PISCINAS EN VIGILANCIA — O2 entre 2.9 y 3.5 mg/L", "#d97706")
    if normales_ps:
        html_piscinas += tabla_simple(normales_ps, "PISCINAS NORMALES", "#16a34a")
    html_piscinas += bloque_promedios(ps_todas)

    html_precrias = ""
    if pr_alertas or normales_pr:
        if criticos_pr:
            html_precrias += tabla_simple(criticos_pr, "PRECRIAS CRITICAS", "#dc2626")
        if vigilancia_pr:
            html_precrias += tabla_simple(vigilancia_pr, "PRECRIAS EN VIGILANCIA", "#d97706")
        if normales_pr:
            html_precrias += tabla_simple(normales_pr, "PRECRIAS NORMALES", "#16a34a")
        if html_precrias:
            html_precrias = f'<div style="border-top:2px solid #e5e7eb;padding-top:12px;margin-top:4px"><div style="font-weight:700;color:#6366f1;font-size:14px;margin-bottom:8px">PRECRIAS</div>{html_precrias}</div>'

    html_reservorio = ""
    if res_todas:
        html_reservorio = f'<div style="border-top:2px solid #e5e7eb;padding-top:12px;margin-top:4px">{seccion_reservorio(res_todas)}</div>'

    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
      <div style="background:{nivel_color};color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="margin:0;font-size:22px">{nivel_texto}</h1>
        <p style="margin:6px 0 0;font-size:14px;opacity:.9">{sector} — {fecha}</p>
      </div>
      <div style="background:white;padding:20px;border:1px solid #e5e7eb;border-top:none">
        <div style="display:flex;gap:10px;margin-bottom:20px;text-align:center">
          <div style="flex:1;background:#fef2f2;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#dc2626">{len(criticos)}</div><div style="font-size:11px;color:#6b7280">Criticas</div></div>
          <div style="flex:1;background:#fffbeb;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#d97706">{len(vigilancia_ps)+len(vigilancia_pr)}</div><div style="font-size:11px;color:#6b7280">Vigilancia</div></div>
          <div style="flex:1;background:#f0fdf4;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#16a34a">{len(normales_ps)+len(normales_pr)}</div><div style="font-size:11px;color:#6b7280">Normales</div></div>
        </div>
        {html_piscinas}
        {html_precrias}
        {html_reservorio}
      </div>
      <div style="background:#f9fafb;padding:10px;text-align:center;font-size:11px;color:#9ca3af;border-radius:0 0 12px 12px;border:1px solid #e5e7eb;border-top:none">Sistema de Alertas Camaronera Recorcholis S.A.</div>
    </div>"""
    return html

def construir_html_biologo_normal(sector, todas_piscinas, fecha):
    ps_todas  = [p for p in todas_piscinas if p.get("tipo","piscina") == "piscina"]
    pr_todas  = [p for p in todas_piscinas if p.get("tipo","piscina") == "precria"]
    res_todas = [p for p in todas_piscinas if p.get("tipo","piscina") == "reservorio"]

    html_ps  = tabla_simple(ps_todas, "PISCINAS", "#16a34a") + bloque_promedios(ps_todas) if ps_todas else ""
    html_pr  = f'<div style="border-top:2px solid #e5e7eb;padding-top:12px;margin-top:4px"><div style="font-weight:700;color:#6366f1;font-size:14px;margin-bottom:8px">PRECRIAS</div>{tabla_simple(pr_todas, "PRECRIAS NORMALES", "#16a34a")}</div>' if pr_todas else ""
    html_res = f'<div style="border-top:2px solid #e5e7eb;padding-top:12px;margin-top:4px">{seccion_reservorio(res_todas)}</div>' if res_todas else ""

    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
      <div style="background:#16a34a;color:white;padding:20px;border-radius:12px 12px 0 0;text-align:center">
        <h1 style="margin:0;font-size:22px">RESUMEN DIARIO — TODO NORMAL</h1>
        <p style="margin:6px 0 0;font-size:14px;opacity:.9">{sector} — {fecha}</p>
      </div>
      <div style="background:white;padding:20px;border:1px solid #e5e7eb;border-top:none">
        <p style="font-size:13px;color:#6b7280;margin-bottom:16px;text-align:center">Todas las piscinas en rango normal</p>
        {html_ps}{html_pr}{html_res}
      </div>
      <div style="background:#f9fafb;padding:10px;text-align:center;font-size:11px;color:#9ca3af;border-radius:0 0 12px 12px;border:1px solid #e5e7eb;border-top:none">Sistema de Alertas Camaronera Recorcholis S.A.</div>
    </div>"""
    return html

def construir_html_biologo_fimasa3(sector, alertas_data, todas_piscinas, fecha):
    ps_todas  = [p for p in todas_piscinas if p.get("tipo","piscina") == "piscina"]
    criticos  = [p for p in alertas_data if p.get("estado_00")=="critico" or p.get("estado_02")=="critico" or p.get("estado_am")=="critico"]
    vigilancia= [p for p in alertas_data if p not in criticos]
    ps_alerta_ids = {p["ps"] for p in alertas_data}
    normales  = [p for p in ps_todas if p["ps"] not in ps_alerta_ids]

    nivel_color = "#16a34a" if not alertas_data else ("#dc2626" if criticos else "#d97706")
    nivel_texto = "RESUMEN DIARIO" if not alertas_data else ("ALERTA CRITICA" if criticos else "VIGILANCIA")

    def tabla_f3(piscinas, titulo, bg):
        if not piscinas: return ""
        filas = ""
        for p in sort_ps(piscinas):
            filas += f"""<tr>
              <td style='padding:6px 8px;font-weight:700;text-align:center'>{p['ps']}</td>
              {celda_o2(p.get('oxigeno_00'))}{celda_temp(p.get('temp_00'))}
              {celda_o2(p.get('oxigeno_02'))}{celda_temp(p.get('temp_02'))}
              {celda_o2(p.get('oxigeno_am'))}{celda_temp(p.get('temp_am'))}
            </tr>"""
        return f"""<div style="margin-bottom:4px">
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
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2</th><th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">T</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2</th><th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">T</th>
                <th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">O2</th><th style="padding:5px;text-align:center;font-size:10px;color:#6b7280;border-bottom:1px solid #e5e7eb">T</th>
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
          <div style="flex:1;background:#fef2f2;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#dc2626">{len(criticos)}</div><div style="font-size:11px;color:#6b7280">Criticas</div></div>
          <div style="flex:1;background:#fffbeb;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#d97706">{len(vigilancia)}</div><div style="font-size:11px;color:#6b7280">Vigilancia</div></div>
          <div style="flex:1;background:#f0fdf4;border-radius:8px;padding:12px"><div style="font-size:24px;font-weight:700;color:#16a34a">{len(normales)}</div><div style="font-size:11px;color:#6b7280">Normales</div></div>
        </div>
        {tabla_f3(criticos, "CRITICAS — O2 menor a 2.9 mg/L", "#dc2626")}
        {tabla_f3(vigilancia, "VIGILANCIA — O2 entre 2.9 y 3.5 mg/L", "#d97706")}
        {tabla_f3(normales, "PISCINAS NORMALES", "#16a34a")}
        {bloque_promedios_fimasa3(ps_todas)}
      </div>
      <div style="background:#f9fafb;padding:10px;text-align:center;font-size:11px;color:#9ca3af;border-radius:0 0 12px 12px;border:1px solid #e5e7eb;border-top:none">Sistema de Alertas Camaronera Recorcholis S.A.</div>
    </div>"""
    return html

def bloque_promedios_fimasa3(piscinas):
    def prom(lst, key):
        vals = [p.get(key) for p in lst if p.get(key) is not None]
        return round(sum(vals)/len(vals), 2) if vals else "—"
    def promt(lst, key):
        vals = [p.get(key) for p in lst if p.get(key) is not None]
        return round(sum(vals)/len(vals), 1) if vals else "—"
    p00 = prom(piscinas, "oxigeno_00"); t00 = promt(piscinas, "temp_00")
    p02 = prom(piscinas, "oxigeno_02"); t02 = promt(piscinas, "temp_02")
    pam = prom(piscinas, "oxigeno_am"); tam = promt(piscinas, "temp_am")
    ppm = prom(piscinas, "oxigeno_pm"); tpm = promt(piscinas, "temp_pm")
    return f"""<div style="background:#f9fafb;border-radius:8px;padding:14px;margin-top:8px;margin-bottom:16px">
      <div style="font-weight:700;color:#374151;margin-bottom:10px;font-size:13px">PROMEDIOS PISCINAS</div>
      <table style="width:100%;text-align:center">
        <thead><tr>
          <th style="font-size:10px;color:#0369a1;padding:4px">00:30</th>
          <th style="font-size:10px;color:#6b7280;padding:4px"></th>
          <th style="font-size:10px;color:#0369a1;padding:4px">02:30</th>
          <th style="font-size:10px;color:#6b7280;padding:4px"></th>
          <th style="font-size:10px;color:#0369a1;padding:4px">05:00</th>
          <th style="font-size:10px;color:#6b7280;padding:4px"></th>
          <th style="font-size:10px;color:#7c3aed;padding:4px">16:00</th>
          <th style="font-size:10px;color:#6b7280;padding:4px"></th>
        </tr></thead>
        <tr>
          <td style="padding:4px"><div style="font-size:17px;font-weight:700;color:#1D9E75">{p00}</div><div style="font-size:10px;color:#6b7280">O2 mg/L</div></td>
          <td style="padding:4px"><div style="font-size:17px;font-weight:700;color:#f59e0b">{t00}</div><div style="font-size:10px;color:#6b7280">T C</div></td>
          <td style="padding:4px"><div style="font-size:17px;font-weight:700;color:#1D9E75">{p02}</div><div style="font-size:10px;color:#6b7280">O2 mg/L</div></td>
          <td style="padding:4px"><div style="font-size:17px;font-weight:700;color:#f59e0b">{t02}</div><div style="font-size:10px;color:#6b7280">T C</div></td>
          <td style="padding:4px"><div style="font-size:17px;font-weight:700;color:#1D9E75">{pam}</div><div style="font-size:10px;color:#6b7280">O2 mg/L</div></td>
          <td style="padding:4px"><div style="font-size:17px;font-weight:700;color:#f59e0b">{tam}</div><div style="font-size:10px;color:#6b7280">T C</div></td>
          <td style="padding:4px"><div style="font-size:17px;font-weight:700;color:#7c3aed">{ppm}</div><div style="font-size:10px;color:#6b7280">O2 mg/L</div></td>
          <td style="padding:4px"><div style="font-size:17px;font-weight:700;color:#ef4444">{tpm}</div><div style="font-size:10px;color:#6b7280">T C</div></td>
        </tr>
      </table>
    </div>"""

def enviar_email_postmark(dest_email, dest_nombre, asunto, cuerpo, html=None):
    try:
        print(f"Enviando Postmark a {dest_email}...")
        payload = {
            "From": f"Camaronera Alertas <{EMAIL_REMITENTE}>",
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
