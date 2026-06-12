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

# ── Resumen por campo (últimos N días) ─────────────────────
def get_resumen_campo(sector, dias=3):
    """Devuelve resumen de piscinas con alertas y comparación de días."""
    try:
        con = get_conn()
        cur = con.cursor(cursor_factory=RealDictCursor)
        # Obtener corrida máxima por piscina y sus últimos N días
        cur.execute("""
            SELECT piscina, fecha, oxigeno_am, oxigeno_pm, temp_am, temp_pm
            FROM lecturas
            WHERE sector=%s AND corrida=(
                SELECT MAX(corrida) FROM lecturas l2 WHERE l2.sector=lecturas.sector AND l2.piscina=lecturas.piscina
            )
            ORDER BY piscina, created_at DESC
        """, (sector,))
        rows = cur.fetchall()
        cur.close(); con.close()

        # Agrupar por piscina
        piscinas = {}
        for r in rows:
            ps = r["piscina"]
            if ps not in piscinas:
                piscinas[ps] = []
            if len(piscinas[ps]) < dias:
                piscinas[ps].append(dict(r))

        # Ordenar numéricamente
        def sort_key(p):
            try: return (0, int(p))
            except: return (1, p)

        resultado = []
        for ps in sorted(piscinas.keys(), key=sort_key):
            historial = piscinas[ps]
            ultimo = historial[0] if historial else {}
            resultado.append({
                "piscina": ps,
                "ultimo": ultimo,
                "historial": list(reversed(historial))  # orden cronológico
            })
        return resultado
    except Exception as e:
        print(f"Error get_resumen_campo: {e}")
        return []

def get_resumen_todos_campos():
    """Resumen de todos los campos para dashboard gerencia."""
    resumen = {}
    for campo in CAMPOS:
        datos = get_resumen_campo(campo, dias=4)
        if datos:
            resumen[campo] = datos
    return resumen

# ── Email detallado gerencia ───────────────────────────────
def construir_email_gerencia_consolidado(fecha):
    """Email consolidado con TODOS los campos que tienen alertas."""
    campos_con_alertas = []
    total_criticos = 0
    total_vigilancia = 0

    for campo in CAMPOS:
        piscinas_data = get_resumen_campo(campo, dias=4)
        if not piscinas_data:
            continue
        alertas = [p for p in piscinas_data if
                   (p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_VIGILANCIA) or
                   (p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_VIGILANCIA)]
        criticos = [p for p in alertas if
                    (p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_CRITICO) or
                    (p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_CRITICO)]
        if alertas:
            campos_con_alertas.append({
                "campo": campo,
                "piscinas": piscinas_data,
                "alertas": alertas,
                "criticos": criticos
            })
            total_criticos += len(criticos)
            total_vigilancia += len(alertas) - len(criticos)

    if not campos_con_alertas:
        return None, None

    nivel = "🔴 ALERTA CRÍTICA" if total_criticos > 0 else "🟡 VIGILANCIA"
    asunto = f"{nivel} — Reporte Consolidado — {fecha}"

    lineas = [
        f"{nivel} — REPORTE CONSOLIDADO",
        f"Fecha: {fecha}",
        f"Campos con alertas: {len(campos_con_alertas)} | Piscinas críticas: {total_criticos} | En vigilancia: {total_vigilancia}",
        "=" * 60,
        ""
    ]

    for info in campos_con_alertas:
        campo = info["campo"]
        piscinas_data = info["piscinas"]

        # Promedios del campo
        o2_am_vals = [p["ultimo"]["oxigeno_am"] for p in piscinas_data if p["ultimo"].get("oxigeno_am") is not None]
        temp_am_vals = [p["ultimo"]["temp_am"] for p in piscinas_data if p["ultimo"].get("temp_am") is not None]
        prom_o2_am  = round(sum(o2_am_vals)/len(o2_am_vals), 2) if o2_am_vals else "—"
        prom_temp_am = round(sum(temp_am_vals)/len(temp_am_vals), 1) if temp_am_vals else "—"

        lineas.append(f"📍 CAMPO: {campo}")
        lineas.append(f"   Promedio O₂ AM: {prom_o2_am} mg/L | Promedio Temp AM: {prom_temp_am}°C")
        lineas.append(f"   Total piscinas: {len(piscinas_data)} | Alertas: {len(info['alertas'])} | Críticas: {len(info['criticos'])}")
        lineas.append("")

        # Piscinas en alerta primero
        lineas.append("   ⚠️  PISCINAS EN ALERTA:")
        for p in info["alertas"]:
            u = p["ultimo"]
            o2am = u.get("oxigeno_am", "—")
            o2pm = u.get("oxigeno_pm", "—")
            tam  = u.get("temp_am", "—")
            tpm  = u.get("temp_pm", "—")
            e = "🔴" if (isinstance(o2am,float) and o2am<O2_CRITICO) or (isinstance(o2pm,float) and o2pm<O2_CRITICO) else "🟡"
            lineas.append(f"   {e} Ps {p['piscina']:>3}: O₂AM={str(o2am):>5} mg/L | O₂PM={str(o2pm):>5} mg/L | TAM={str(tam):>5}°C | TPM={str(tpm):>5}°C")
            # Historial 3 días anteriores
            if len(p["historial"]) > 1:
                hist = " | ".join([f"{h['fecha']}: O₂AM={h.get('oxigeno_am','—')}" for h in p["historial"][:-1]])
                lineas.append(f"          Ant: {hist}")
        lineas.append("")

        # Todas las piscinas del campo
        lineas.append("   📋 TODAS LAS PISCINAS:")
        for p in piscinas_data:
            u = p["ultimo"]
            if not u: continue
            o2am = u.get("oxigeno_am", "—")
            o2pm = u.get("oxigeno_pm", "—")
            tam  = u.get("temp_am", "—")
            tpm  = u.get("temp_pm", "—")
            e = "🔴" if (isinstance(o2am,float) and o2am<O2_CRITICO) or (isinstance(o2pm,float) and o2pm<O2_CRITICO) else \
                "🟡" if (isinstance(o2am,float) and o2am<O2_VIGILANCIA) or (isinstance(o2pm,float) and o2pm<O2_VIGILANCIA) else "🟢"
            lineas.append(f"   {e} Ps {p['piscina']:>3}: O₂AM={str(o2am):>5} | O₂PM={str(o2pm):>5} | TAM={str(tam):>5}°C | TPM={str(tpm):>5}°C")
        lineas.append("")
        lineas.append("─" * 60)
        lineas.append("")

    cuerpo = "\n".join(lineas)
    return asunto, cuerpo

def construir_email_gerencia(sector, piscinas_data, fecha):
    """Construye email HTML detallado para gerencia."""
    alertas = [p for p in piscinas_data if
               (p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_VIGILANCIA) or
               (p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_VIGILANCIA)]

    if not alertas and fecha != "resumen_diario":
        return None, None

    # Calcular promedios
    o2_am_vals = [p["ultimo"]["oxigeno_am"] for p in piscinas_data if p["ultimo"].get("oxigeno_am") is not None]
    o2_pm_vals = [p["ultimo"]["oxigeno_pm"] for p in piscinas_data if p["ultimo"].get("oxigeno_pm") is not None]
    temp_am_vals = [p["ultimo"]["temp_am"] for p in piscinas_data if p["ultimo"].get("temp_am") is not None]
    temp_pm_vals = [p["ultimo"]["temp_pm"] for p in piscinas_data if p["ultimo"].get("temp_pm") is not None]

    prom_o2_am  = round(sum(o2_am_vals)/len(o2_am_vals), 2) if o2_am_vals else "—"
    prom_o2_pm  = round(sum(o2_pm_vals)/len(o2_pm_vals), 2) if o2_pm_vals else "—"
    prom_temp_am = round(sum(temp_am_vals)/len(temp_am_vals), 1) if temp_am_vals else "—"
    prom_temp_pm = round(sum(temp_pm_vals)/len(temp_pm_vals), 1) if temp_pm_vals else "—"

    criticos = [p for p in alertas if
                (p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_CRITICO) or
                (p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_CRITICO)]

    nivel = "🔴 ALERTA CRÍTICA" if criticos else ("🟡 VIGILANCIA" if alertas else "🟢 RESUMEN DIARIO")
    asunto = f"{nivel} — {sector} — {fecha}"

    # Construir texto
    lineas = [
        f"{nivel}",
        f"Campo: {sector} | Fecha: {fecha}",
        "=" * 50,
        "",
    ]

    if alertas:
        lineas.append("⚠️  PISCINAS CON OXÍGENO BAJO:")
        lineas.append("")
        for p in alertas:
            u = p["ultimo"]
            o2am = u.get("oxigeno_am", "—")
            o2pm = u.get("oxigeno_pm", "—")
            tam  = u.get("temp_am", "—")
            tpm  = u.get("temp_pm", "—")
            estado_am = "🔴" if isinstance(o2am, float) and o2am < O2_CRITICO else "🟡"
            estado_pm = "🔴" if isinstance(o2pm, float) and o2pm < O2_CRITICO else "🟡"

            lineas.append(f"  Piscina {p['piscina']}:")
            lineas.append(f"    O₂ AM: {o2am} mg/L {estado_am}  |  O₂ PM: {o2pm} mg/L {estado_pm}")
            lineas.append(f"    Temp AM: {tam}°C  |  Temp PM: {tpm}°C")

            # Historial 3 días anteriores
            if len(p["historial"]) > 1:
                lineas.append(f"    Últimos días:")
                for h in p["historial"][:-1]:  # excluir el más reciente
                    lineas.append(f"      {h['fecha']}: O₂ AM={h.get('oxigeno_am','—')} | O₂ PM={h.get('oxigeno_pm','—')}")
            lineas.append("")

    lineas.append("─" * 50)
    lineas.append("📊 RESUMEN DEL CAMPO:")
    lineas.append(f"  Total piscinas reportadas: {len(piscinas_data)}")
    lineas.append(f"  Piscinas en alerta: {len(alertas)}")
    lineas.append(f"  Piscinas críticas: {len(criticos)}")
    lineas.append("")
    lineas.append("📈 PROMEDIOS:")
    lineas.append(f"  O₂ AM promedio:   {prom_o2_am} mg/L")
    lineas.append(f"  O₂ PM promedio:   {prom_o2_pm} mg/L")
    lineas.append(f"  Temp AM promedio: {prom_temp_am}°C")
    lineas.append(f"  Temp PM promedio: {prom_temp_pm}°C")
    lineas.append("")

    # Todas las piscinas
    lineas.append("─" * 50)
    lineas.append("📋 DETALLE COMPLETO DE PISCINAS:")
    lineas.append("")
    for p in piscinas_data:
        u = p["ultimo"]
        if not u:
            continue
        o2am = u.get("oxigeno_am", "—")
        o2pm = u.get("oxigeno_pm", "—")
        tam  = u.get("temp_am", "—")
        tpm  = u.get("temp_pm", "—")
        e = "🔴" if (isinstance(o2am,float) and o2am<O2_CRITICO) or (isinstance(o2pm,float) and o2pm<O2_CRITICO) else \
            "🟡" if (isinstance(o2am,float) and o2am<O2_VIGILANCIA) or (isinstance(o2pm,float) and o2pm<O2_VIGILANCIA) else "🟢"
        lineas.append(f"  {e} Ps {p['piscina']:>3}: O₂AM={str(o2am):>5} O₂PM={str(o2pm):>5} | TAM={str(tam):>5}°C TPM={str(tpm):>5}°C")

    cuerpo = "\n".join(lineas)
    return asunto, cuerpo

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

@app.route("/api/resumen-diario", methods=["GET", "POST"])
def resumen_diario():
    """Endpoint para el cron job de resumen diario a las 6 AM Ecuador."""
    try:
        print("Enviando resumen diario...")
        fecha_hoy = (datetime.utcnow() - timedelta(hours=5)).strftime("%d/%m/%Y")
        usuarios = leer_usuarios()
        gerentes = [u for u in usuarios if u.get("rol") == "gerencia" or
                    set(u.get("campos", [])) >= set(CAMPOS)]

        enviados = 0
        for campo in CAMPOS:
            piscinas_data = get_resumen_campo(campo, dias=4)
            if not piscinas_data:
                continue
            asunto, cuerpo = construir_email_gerencia(campo, piscinas_data, fecha_hoy)
            if not asunto:
                continue
            for g in gerentes:
                if g.get("email"):
                    enviar_email_postmark(g["email"], g.get("nombre",""), asunto, cuerpo)
                    enviados += 1

        print(f"Resumen diario enviado: {enviados} emails")
        return jsonify({"ok": True, "enviados": enviados, "fecha": fecha_hoy})
    except Exception as e:
        print(f"Error resumen diario: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/dashboard-gerencia", methods=["GET"])
def dashboard_gerencia():
    """Dashboard resumen para gerencia."""
    try:
        resumen = get_resumen_todos_campos()
        fecha_hoy = (datetime.utcnow() - timedelta(hours=5)).strftime("%d/%m/%Y")
        resultado = []
        for campo, piscinas in resumen.items():
            alertas = sum(1 for p in piscinas if
                p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_VIGILANCIA or
                p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_VIGILANCIA)
            criticos = sum(1 for p in piscinas if
                p["ultimo"].get("oxigeno_am") is not None and p["ultimo"]["oxigeno_am"] < O2_CRITICO or
                p["ultimo"].get("oxigeno_pm") is not None and p["ultimo"]["oxigeno_pm"] < O2_CRITICO)
            o2_am_vals = [p["ultimo"]["oxigeno_am"] for p in piscinas if p["ultimo"].get("oxigeno_am") is not None]
            prom_o2_am = round(sum(o2_am_vals)/len(o2_am_vals), 2) if o2_am_vals else None
            resultado.append({
                "campo": campo,
                "total": len(piscinas),
                "alertas": alertas,
                "criticos": criticos,
                "prom_o2_am": prom_o2_am,
                "ultima_fecha": piscinas[0]["ultimo"].get("fecha") if piscinas and piscinas[0]["ultimo"] else None,
                "piscinas": piscinas
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

        rol = u.get("rol", "biologo")

        if rol == "gerencia":
            # Email consolidado HTML para gerencia
            asunto, cuerpo, html_body = construir_html_gerencia_consolidado(fecha)
            if asunto:
                enviar_email_postmark(u["email"], u.get("nombre",""), asunto, cuerpo, html=html_body)
                enviados += 1
        else:
            # Email HTML para biólogos
            criticos = [a for a in alertas if a["estado_am"]=="critico" or a["estado_pm"]=="critico"]
            nivel = "ALERTA CRITICA" if criticos else "VIGILANCIA"
            asunto = f"{'🔴' if criticos else '🟡'} {nivel} - {sector} - {fecha}"
            cuerpo = f"{nivel}\nSector: {sector} | Fecha: {fecha}\n"
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
    tam_v  = [p.get("temp_am")    for p in todas_piscinas if p.get("temp_am")    is not None]
    prom_o2am = round(sum(o2am_v)/len(o2am_v),2) if o2am_v else "—"
    prom_o2pm = round(sum(o2pm_v)/len(o2pm_v),2) if o2pm_v else "—"
    prom_tam  = round(sum(tam_v)/len(tam_v),1)   if tam_v  else "—"

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

def construir_html_gerencia_consolidado(fecha):
    campos_info = []
    total_criticos = 0
    total_vigilancia = 0

    for campo in CAMPOS:
        piscinas_data = get_resumen_campo(campo, dias=4)
        if not piscinas_data: continue
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

        def sort_ps(lst):
            return sorted(lst, key=lambda x: (0,int(x["piscina"])) if str(x["piscina"]).isdigit() else (1,x["piscina"]))

        filas = ""
        for p in sort_ps(info["piscinas"]):
            u = p["ultimo"]
            if not u: continue
            hist = " → ".join([f"{h['fecha']}: {h.get('oxigeno_am','—')}" for h in p["historial"][:-1]]) if len(p["historial"])>1 else "—"
            filas += f"<tr><td style='padding:6px 10px;font-weight:700;text-align:center'>{p['piscina']}</td>{celda_o2(u.get('oxigeno_am'))}{celda_o2(u.get('oxigeno_pm'))}{celda_temp(u.get('temp_am'))}{celda_temp(u.get('temp_pm'))}<td style='padding:6px 8px;font-size:11px;color:#6b7280'>{hist}</td></tr>"

        secciones += f"""<div style="margin-bottom:24px">
          <div style="background:{campo_color};color:white;padding:10px 14px;border-radius:8px 8px 0 0;font-weight:700">
            📍 {info['campo']} — {len(info['alertas'])} alertas | {len(info['criticos'])} críticas | O₂ AM prom: {prom} mg/L
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
