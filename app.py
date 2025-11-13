# app.py (VERSIÓN CORREGIDA Y MEJORADA)
import os
import secrets
from datetime import datetime, timedelta
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, flash, request as flask_request
)

# Intentamos importar utilidades externas (si las tuvieras)
try:
    from utils_excel import (
        ensure_files, list_medicos, medicos_by_especialidad, get_medico_by_id,
        create_user, authenticate, safe_read, CIT_F, PATI_F, MED_F,
        list_citas_for_user, save_cita, generate_boleta_pdf as util_generate_boleta_pdf,
        marcar_cita_atendida, citas_por_medico
    )
except Exception:
    # si no existe utils_excel o faltan funciones, usaremos fallbacks internos
    ensure_files = None
    list_medicos = None
    medicos_by_especialidad = None
    get_medico_by_id = None
    create_user = None
    authenticate = None
    safe_read = None
    CIT_F = os.path.join("data", "citas.xlsx")
    PATI_F = os.path.join("data", "pacientes.xlsx")
    MED_F = os.path.join("data", "medicos.xlsx")
    list_citas_for_user = None
    save_cita = None
    util_generate_boleta_pdf = None
    marcar_cita_atendida = None
    citas_por_medico = None

# librerías opcionales
try:
    import pandas as pd
except Exception:
    pd = None

try:
    import qrcode
    QR_AVAILABLE = True
except Exception:
    QR_AVAILABLE = False

# reportlab para PDF decorado
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.units import cm
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

# RUTAS/ARCHIVOS
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
PDF_DIR = os.path.join(BASE_DIR, "static", "generated", "pdfs")
QR_DIR = os.path.join(BASE_DIR, "static", "generated", "qrs")
os.makedirs(PDF_DIR, exist_ok=True)
os.makedirs(QR_DIR, exist_ok=True)

# Rutas Excel por defecto si utils_excel no las exportó
CIT_F = locals().get("CIT_F", os.path.join(DATA_DIR, "citas.xlsx"))
PATI_F = locals().get("PATI_F", os.path.join(DATA_DIR, "pacientes.xlsx"))
MED_F = locals().get("MED_F", os.path.join(DATA_DIR, "medicos.xlsx"))

# APP
app = Flask(__name__)
app.secret_key = "cambia_por_una_clave_muy_segura"

# almacenamiento en memoria (fallback)
citas_pendientes_mem = []

# ----------------------------------------------------------------------
# Helpers Excel simples (fallbacks si no hay utils_excel)
# ----------------------------------------------------------------------
def _ensure_excel_file(path, columns):
    """Si no existe el archivo Excel, lo crea con columnas vacías (usar pandas)."""
    if pd is None:
        return False
    if not os.path.exists(path):
        df = pd.DataFrame(columns=columns)
        df.to_excel(path, index=False)
    return True

def excel_read(path, default_cols=None):
    """Leer Excel con pandas; si no existe crea vacío con columnas default_cols."""
    if pd is None:
        return None
    if default_cols is None:
        default_cols = []
    if not os.path.exists(path):
        _ensure_excel_file(path, default_cols)
    try:
        df = pd.read_excel(path)
        return df
    except Exception:
        # intentar crear con columnas
        _ensure_excel_file(path, default_cols)
        return pd.DataFrame(columns=default_cols)

def excel_append_row(path, row: dict, default_cols=None):
    """Agregar fila a archivo Excel (crea archivo si hace falta)."""
    if pd is None:
        raise RuntimeError("pandas requerido para persistencia Excel")
    df = excel_read(path, default_cols=default_cols)
    df2 = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df2.to_excel(path, index=False)
    return True

# aseguramos archivos básicos
if pd:
    _ensure_excel_file(PATI_F, ["username", "nombre", "email", "telefono", "rol", "password", "documento", "dni"])
    _ensure_excel_file(MED_F, ["id", "username", "nombre", "especialidad", "start_time", "end_time", "sede"])
    _ensure_excel_file(CIT_F, ["id", "usuario", "nombre_paciente", "especialidad", "medico_id", "medico_nombre", "fecha", "hora", "estado", "metodo_pago", "referencia"])

# ----------------------------------------------------------------------
# Utilities: horarios, QR, PDF
# ----------------------------------------------------------------------
def generate_slots(start_time_str, end_time_str, slot_minutes=30):
    fmt = "%H:%M"
    start = datetime.strptime(start_time_str, fmt)
    end = datetime.strptime(end_time_str, fmt)
    slots = []
    cur = start
    while cur + timedelta(minutes=int(slot_minutes)) <= end:
        slots.append(cur.strftime(fmt))
        cur += timedelta(minutes=int(slot_minutes))
    return slots

def get_horarios_disponibles(medico):
    try:
        inicio = medico.get("start_time", "09:00")
        fin = medico.get("end_time", "17:00")
        return generate_slots(inicio, fin, 30)
    except Exception:
        return ["09:00","09:30","10:00","10:30","11:00"]

def generate_qr_image(text, filename):
    path = os.path.join(QR_DIR, filename)
    if QR_AVAILABLE:
        q = qrcode.QRCode(box_size=6, border=2)
        q.add_data(text)
        q.make(fit=True)
        img = q.make_image(fill_color="black", back_color="white")
        img.save(path)
        return path
    else:
        # fallback: crear un archivo placeholder (puede ser reemplazado por imagen fija)
        placeholder = os.path.join("static","img","qr_placeholder.png")
        return placeholder if os.path.exists(placeholder) else None

def generate_boleta_pdf(cita: dict, paciente: dict, filename: str):
    """Genera PDF bonito con reportlab — si no está disponible, devuelve False."""
    if util_generate_boleta_pdf and callable(util_generate_boleta_pdf):
        # si tienes una función externa mejor usarla
        return util_generate_boleta_pdf(cita, paciente, filename)

    if not REPORTLAB_AVAILABLE:
        return False

    pdf_path = os.path.join(PDF_DIR, filename)
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("title", parent=styles["Title"], fontSize=18, textColor=colors.HexColor("#004aad"), alignment=1)
    subtitle_style = ParagraphStyle("subtitle", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#0077cc"))
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=10, leading=14)

    # logo
    logo_path = os.path.join(BASE_DIR, "static", "img", "logo_clinica.png")
    if os.path.exists(logo_path):
        story.append(Image(logo_path, width=120, height=60))
    story.append(Spacer(1,8))
    story.append(Paragraph("<b>BOLETA - Clínica LimaMedic</b>", title_style))
    story.append(Spacer(1,10))

    # paciente
    story.append(Paragraph("Datos del paciente", subtitle_style))
    data_paciente = [
        ["Nombre:", paciente.get("nombre", paciente.get("nombre_paciente", ""))],
        ["DNI:", paciente.get("documento", paciente.get("dni",""))],
        ["Correo:", paciente.get("email","")],
        ["Teléfono:", paciente.get("telefono","—")]
    ]
    t1 = Table(data_paciente, colWidths=[4*cm, 11*cm])
    t1.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),colors.HexColor("#f0f5ff")),("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#cfcfcf"))]))
    story.append(t1)
    story.append(Spacer(1,12))

    # cita
    story.append(Paragraph("Detalles de la cita", subtitle_style))
    data_cita = [
        ["Especialidad:", cita.get("especialidad","")],
        ["Médico:", cita.get("medico_nombre","")],
        ["Fecha:", cita.get("fecha","")],
        ["Hora:", cita.get("hora","")],
        ["Estado:", cita.get("estado","")],
        ["Código:", str(cita.get("id",""))]
    ]
    t2 = Table(data_cita, colWidths=[4*cm, 11*cm])
    t2.setStyle(TableStyle([("BACKGROUND",(0,0),(0,-1),colors.HexColor("#eaf2ff")),("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#cfcfcf"))]))
    story.append(t2)
    story.append(Spacer(1,12))

    # pago
    story.append(Paragraph("Pago", subtitle_style))
    pago = [["Método:", cita.get("metodo_pago", "—")], ["Referencia:", cita.get("referencia", "—")]]
    t3 = Table(pago, colWidths=[4*cm, 11*cm])
    t3.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#cfcfcf"))]))
    story.append(t3)
    story.append(Spacer(1,18))

    story.append(Paragraph("Gracias por confiar en Clínica LimaMedic.", normal))
    story.append(Spacer(1,10))
    story.append(Paragraph("<font size=9 color='#666666'>Av. Ejemplo 123, Lima · Tel: (01) 555-5555</font>", ParagraphStyle("footer", alignment=1)))

    doc.build(story)
    return True

# ----------------------------------------------------------------------
# Rutas
# ----------------------------------------------------------------------
@app.route("/")
def index():
    # obtener medicos (prefer util, sino excel fallback)
    medicos = []
    try:
        if list_medicos and callable(list_medicos):
            medicos = list_medicos()
        elif pd:
            med_df = excel_read(MED_F, default_cols=["id","username","nombre","especialidad","start_time","end_time","sede"])
            medicos = med_df.to_dict(orient="records")
    except Exception as e:
        medicos = []
        print("Error list_medicos:", e)

    especialidades = sorted(list({m.get("especialidad") for m in medicos if m.get("especialidad")}))
    return render_template("index.html", medicos=medicos, especialidades=especialidades, user=session.get("user"))

# registro/login/logout (si tienes create_user/authenticate en utils_excel los usará)
@app.route("/registro", methods=["GET","POST"])
def registro():
    if request.method == "POST":
        username = request.form.get("username","").strip()
        nombre = request.form.get("nombre","").strip()
        email = request.form.get("email","").strip()
        telefono = request.form.get("telefono","").strip()
        rol = request.form.get("rol","paciente")
        password = request.form.get("password","")
        documento = request.form.get("documento","")
        # intentar usar create_user externo, sino persistir en pacientes.xlsx
        try:
            if create_user and callable(create_user):
                ok,res = create_user(username, nombre, email, telefono, rol, password, documento)
            else:
                # fallback: guardar a PATI_F
                row = {"username": username, "nombre": nombre, "email": email, "telefono": telefono, "rol": rol, "password": password, "documento": documento}
                excel_append_row(PATI_F, row, default_cols=["username","nombre","email","telefono","rol","password","documento"])
                ok,res = True, "Registrado OK (fallback)"
        except Exception as e:
            ok,res = False, f"Error al registrar: {e}"
        if not ok:
            flash(res, "error")
            return render_template("registro.html")
        flash("Registro exitoso. Por favor inicia sesión.", "success")
        return redirect(url_for("login"))
    return render_template("registro.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier","").strip()
        password = request.form.get("password","")
        rol = request.form.get("rol","paciente")
        try:
            if authenticate and callable(authenticate):
                ok,res = authenticate(identifier, password, rol)
            else:
                # fallback: buscar en PATI_F (por username o email)
                df = excel_read(PATI_F, default_cols=["username","nombre","email","telefono","rol","password","documento"])
                if identifier and password:
                    row = df[(df["username"]==identifier) | (df["email"]==identifier)]
                    if not row.empty and row.iloc[0]["password"]==password and row.iloc[0]["rol"]==rol:
                        ok = True
                        res = row.iloc[0].to_dict()
                    else:
                        ok,res = False,"Credenciales inválidas (fallback)"
                else:
                    ok,res = False,"Proporciona credenciales"
        except Exception as e:
            ok,res = False, f"Error autenticación: {e}"

        if not ok:
            flash(res, "error")
            return render_template("login.html")
        user = res
        session["user"] = {
            "username": user.get("username"),
            "nombre": user.get("nombre"),
            "rol": rol,
            "email": user.get("email")
        }
        # redirigir por rol
        if rol=="paciente": return redirect(url_for("dashboard_paciente"))
        if rol=="medico": return redirect(url_for("dashboard_medico"))
        if rol=="secretaria": return redirect(url_for("dashboard_secretaria"))
        if rol=="farmacia": return redirect(url_for("dashboard_farmacia"))
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Sesión cerrada", "success")
    return redirect(url_for("index"))

# ---------- Reserva flow -----------
@app.route("/reserva", methods=["GET","POST"])
def reserva():
    if "user" not in session or session["user"].get("rol") != "paciente":
        flash("Inicia sesión como paciente para reservar", "error")
        return redirect(url_for("login"))

    # especialidades
    medicos = []
    try:
        if list_medicos and callable(list_medicos):
            medicos = list_medicos()
        else:
            med_df = excel_read(MED_F, default_cols=["id","username","nombre","especialidad","start_time","end_time","sede"])
            medicos = med_df.to_dict(orient="records")
    except Exception:
        medicos = []
    especialidades = sorted(list({m.get("especialidad") for m in medicos if m.get("especialidad")}))

    if request.method == "POST":
        especialidad = request.form.get("especialidad")
        fecha = request.form.get("fecha")
        session["reserva_temp"] = {"especialidad": especialidad, "fecha": fecha}
        return redirect(url_for("seleccionar_medico"))

    return render_template("reserva.html", especialidades=especialidades, user=session.get("user"))

@app.route("/seleccionar_medico", methods=["GET","POST"])
def seleccionar_medico():
    if "user" not in session or "reserva_temp" not in session:
        flash("Selecciona especialidad primero", "error")
        return redirect(url_for("reserva"))

    temp = session["reserva_temp"]
    try:
        if medicos_by_especialidad and callable(medicos_by_especialidad):
            med_list = medicos_by_especialidad(temp.get("especialidad",""))
        else:
            med_df = excel_read(MED_F, default_cols=["id","username","nombre","especialidad","start_time","end_time","sede"])
            med_list = med_df[med_df["especialidad"]==temp.get("especialidad","")].to_dict(orient="records")
    except Exception:
        med_list = []

    if request.method == "POST":
        medico_id = request.form.get("medico_id")
        if not medico_id:
            flash("Selecciona un médico", "error")
            return redirect(url_for("seleccionar_medico"))
        temp["medico_id"] = int(medico_id)
        session["reserva_temp"] = temp
        session.modified = True
        return redirect(url_for("seleccionar_hora"))

    return render_template("seleccionar_medico.html", medicos=med_list, temp=temp, user=session.get("user"))

@app.route("/seleccionar_hora", methods=["GET","POST"])
def seleccionar_hora():
    temp = session.get("reserva_temp", {})
    if "medico_id" not in temp:
        flash("Selecciona un médico antes de elegir hora", "error")
        return redirect(url_for("seleccionar_medico"))

    try:
        if get_medico_by_id and callable(get_medico_by_id):
            medico = get_medico_by_id(temp["medico_id"])
        else:
            med_df = excel_read(MED_F)
            medico = med_df[med_df["id"]==temp["medico_id"]].iloc[0].to_dict() if not med_df.empty else {"id":temp["medico_id"], "nombre":"Medico"}
    except Exception:
        medico = {"id": temp.get("medico_id"), "nombre": "Médico"}

    horarios = get_horarios_disponibles(medico)

    if request.method == "POST":
        hora = request.form.get("hora")
        if not hora:
            flash("Selecciona una hora", "error")
            return redirect(url_for("seleccionar_hora"))
        temp["hora"] = hora
        session["reserva_temp"] = temp
        session.modified = True
        return redirect(url_for("confirmar_cita"))

    return render_template("seleccionar_hora.html", medico=medico, horarios=horarios, temp=temp, user=session.get("user"))

@app.route("/confirmar_cita", methods=["GET","POST"])
def confirmar_cita():
    temp = session.get("reserva_temp", {})
    if request.method == "POST":
        # aquí el usuario confirma y se va a pago
        return redirect(url_for("pago"))
    return render_template("confirmar_reserva.html", reserva=temp, user=session.get("user"))

# ---------- Pago (graba la cita en Excel) ----------
@app.route("/pago", methods=["GET","POST"])
def pago():
    if "user" not in session or session["user"].get("rol")!="paciente":
        flash("Inicia sesión como paciente para pagar", "error")
        return redirect(url_for("login"))

    temp = session.get("reserva_temp", {})
    if not temp or "hora" not in temp:
        flash("Selecciona hora antes de pagar", "error")
        return redirect(url_for("seleccionar_hora"))

    if request.method == "POST":
        metodo = request.form.get("metodo","EFECTIVO")
        # crear registro de cita
        cita = {
            "id": int(datetime.utcnow().timestamp()),
            "usuario": session["user"]["username"],
            "nombre_paciente": session["user"]["nombre"],
            "especialidad": temp.get("especialidad"),
            "medico_id": temp.get("medico_id"),
            "medico_nombre": "",  # intentaremos recuperar
            "fecha": temp.get("fecha"),
            "hora": temp.get("hora"),
            "estado": "Pagado" if metodo=="QR" else "Pendiente",
            "metodo_pago": metodo,
            "referencia": ""
        }
        # obtener nombre de medico
        try:
            if get_medico_by_id and callable(get_medico_by_id):
                m = get_medico_by_id(cita["medico_id"])
                cita["medico_nombre"] = m.get("nombre", "")
            else:
                med_df = excel_read(MED_F)
                if not med_df.empty:
                    row = med_df[med_df["id"]==cita["medico_id"]]
                    if not row.empty:
                        cita["medico_nombre"] = row.iloc[0]["nombre"]
        except Exception:
            pass

        # generar referencia / QR si corresponde
        if metodo == "QR":
            ref = secrets.token_hex(6)
            cita["referencia"] = ref
            qr_text = f"LimaMedic|ref:{ref}|id:{cita['id']}"
            qr_fn = f"{ref}.png"
            qr_path = generate_qr_image(qr_text, qr_fn)
        else:
            # efectivo: código 6 digitos
            code = f"{secrets.randbelow(10**6):06d}"
            cita["referencia"] = code

        # persistir en Excel (prefer util, sino fallback)
        try:
            if save_cita and callable(save_cita):
                save_cita(cita)
            else:
                excel_append_row(CIT_F, cita, default_cols=["id","usuario","nombre_paciente","especialidad","medico_id","medico_nombre","fecha","hora","estado","metodo_pago","referencia"])
        except Exception as e:
            # fallback: memoria
            citas_pendientes_mem.append(cita)
            print("Error al persistir en Excel:", e)

        # limpiar reserva temporal
        session.pop("reserva_temp", None)
        flash("Cita registrada correctamente. Revisa tus citas pendientes.", "success")
        return redirect(url_for("citas_pendientes"))

    # GET -> mostrar opciones de pago con QR/efectivo
    return render_template("pago.html", temp=temp, user=session.get("user"))

# ---------- Citas pendientes (usuario) ----------
@app.route("/citas_pendientes")
def citas_pendientes():
    user = session.get("user")
    if not user:
        flash("Inicia sesión", "error")
        return redirect(url_for("login"))
    username = user.get("username")
    citas = []
    try:
        if list_citas_for_user and callable(list_citas_for_user):
            citas = list_citas_for_user(username)
        else:
            df = excel_read(CIT_F)
            if not df.empty:
                citas = df[df["usuario"]==username].to_dict(orient="records")
            else:
                # fallback en memoria
                citas = [c for c in citas_pendientes_mem if c.get("usuario")==username]
    except Exception as e:
        print("Error cargando citas:", e)
        citas = [c for c in citas_pendientes_mem if c.get("usuario")==username]
    if not citas:
        flash("No tienes citas pendientes.", "info")
    return render_template("citas_pendientes.html", citas=citas, user=user)

# ---------- Boleta (download/generación) ----------
@app.route("/boleta/<int:reserva_id>/download")
def boleta_download(reserva_id):
    try:
        pdf_name = f"boleta_{reserva_id}.pdf"
        path = os.path.join(PDF_DIR, pdf_name)
        if os.path.exists(path):
            return send_file(path, as_attachment=True, download_name=pdf_name)

        # buscar en Excel
        df_c = excel_read(CIT_F)
        if df_c is None or df_c.empty:
            flash("No hay datos de citas.", "error")
            return redirect(url_for("dashboard_paciente"))
        row = df_c[df_c["id"]==reserva_id]
        if row.empty:
            flash("Cita no encontrada.", "error")
            return redirect(url_for("dashboard_paciente"))
        cita_row = row.iloc[0].to_dict()

        # paciente
        df_p = excel_read(PATI_F)
        user_row = df_p[df_p["username"]==cita_row["usuario"]] if df_p is not None else None
        paciente = user_row.iloc[0].to_dict() if (user_row is not None and not user_row.empty) else {"nombre": cita_row.get("nombre_paciente","")}

        ok = generate_boleta_pdf(cita_row, paciente, pdf_name)
        if not ok:
            flash("No se pudo generar la boleta (falta reportlab o función externa).", "error")
            return redirect(url_for("dashboard_paciente"))

        return send_file(path, as_attachment=True, download_name=pdf_name)
    except Exception as e:
        flash(f"Error al generar boleta: {e}", "error")
        return redirect(url_for("dashboard_paciente"))

# ---------- Dashboards ----------
@app.route("/dashboard/paciente")
def dashboard_paciente():
    if "user" not in session or session["user"].get("rol")!="paciente":
        flash("Inicia sesión como paciente", "error")
        return redirect(url_for("login"))
    user = session["user"]
    try:
        if list_citas_for_user and callable(list_citas_for_user):
            citas = list_citas_for_user(user["username"])
        else:
            df = excel_read(CIT_F)
            citas = df[df["usuario"]==user["username"]].to_dict(orient="records") if df is not None else []
    except Exception:
        citas = [c for c in citas_pendientes_mem if c.get("usuario")==user["username"]]
    return render_template("dashboard_paciente.html", user=user, citas=citas)

@app.route("/dashboard/medico")
def dashboard_medico():
    if "user" not in session or session["user"].get("rol")!="medico":
        flash("Inicia sesión como médico", "error")
        return redirect(url_for("login"))
    user = session["user"]
    try:
        med_df = excel_read(MED_F)
        med = med_df[med_df["username"]==user["username"]] if med_df is not None else None
        medico_id = med.iloc[0]["id"] if (med is not None and not med.empty) else None
    except Exception:
        medico_id = None
    try:
        citas = citas_por_medico(medico_id) if (citas_por_medico and medico_id) else []
    except Exception:
        citas = []
    return render_template("dashboard_medico.html", user=user, citas=citas)

@app.route("/dashboard/secretaria")
def dashboard_secretaria():
    if "user" not in session or session["user"].get("rol")!="secretaria":
        flash("Inicia sesión como secretaria", "error")
        return redirect(url_for("login"))
    try:
        df = excel_read(CIT_F)
        citas = df.to_dict(orient="records") if (df is not None) else []
    except Exception:
        citas = citas_pendientes_mem
    try:
        med = excel_read(MED_F)
        medicos = med.to_dict(orient="records") if med is not None else []
    except Exception:
        medicos = []
    return render_template("dashboard_secretaria.html", user=session["user"], citas=citas, medicos=medicos)

@app.route("/dashboard/farmacia")
def dashboard_farmacia():
    if "user" not in session or session["user"].get("rol")!="farmacia":
        flash("Inicia sesión como farmacia", "error")
        return redirect(url_for("login"))
    # inventario & proveedores: tu implementación (aquí placeholders)
    inventario = []
    proveedores = []
    return render_template("dashboard_farmacia.html", user=session["user"], inventario=inventario, proveedores=proveedores)

# ---------- marcar atendido ----------
@app.route("/marcar_atendido/<int:cita_id>")
def marcar_atendido(cita_id):
    ok = False
    try:
        if marcar_cita_atendida and callable(marcar_cita_atendida):
            ok = marcar_cita_atendida(cita_id)
        else:
            # fallback: actualizar Excel/Csv si existe
            df = excel_read(CIT_F)
            if df is not None and not df.empty:
                df.loc[df["id"]==cita_id, "estado"] = "Atendido"
                df.to_excel(CIT_F, index=False)
                ok = True
    except Exception:
        ok = False
    flash("Cita marcada como atendida" if ok else "No se pudo actualizar", "success" if ok else "error")
    return redirect(flask_request.referrer or url_for("index"))

# historial
@app.route("/historial")
def historial():
    user = session.get("user")
    if not user:
        return redirect(url_for("login"))
    try:
        df = excel_read(CIT_F)
        historial = df[df["usuario"]==user["username"]].to_dict(orient="records") if df is not None else []
    except Exception:
        historial = []
    return render_template("historial.html", user=user, historial=historial)

# run
if __name__ == "__main__":
    app.run(debug=True)

