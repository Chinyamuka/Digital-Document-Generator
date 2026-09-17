import io
import os
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path


from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_wtf import CSRFProtect
from flask_sqlalchemy import SQLAlchemy
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

# Always load the .env file located beside this app.py file.
ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=True)

BASE = Path(__file__).resolve().parent
ASSETS = BASE / "static"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("DATABASE_URL"):
    app.config["SESSION_COOKIE_SECURE"] = True
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Local fallback keeps the project portable in VS Code. In production, set DATABASE_URL
# to the PostgreSQL connection string supplied by your host/provider.
database_url = os.environ.get("DATABASE_URL", "sqlite:///shepmule.db")
if database_url.startswith("postgres://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgres://") :]
elif database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]
app.config["SQLALCHEMY_DATABASE_URI"] = database_url

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to use Shepmule Digital Books."

COMPANY = {
    "name": "SHEPMULE INVESTMENTS LIMITED",
    "address": "Plot No: 1226, Perazim Shopping Center, Shop No: 19, Off Kafue Rd, Post Office area, Chilanga, Lusaka.",
    "cell": "+260 966 369 913 / 976 573 715",
    "email": "Shepmuleinvestmentsltd@gmail.com",
    "tpin": "2377467683",
    "bank": "ZANACO",
    "branch": "Cairo Road Business Center",
    "account_name": "Shepmule Investments Limited",
    "account_number": "5753187500140",
    "swift": "ZNCOZMLU",
    "sort_code": "010040",
    "branch_code": "040",
}

ASSET_HEADER = ASSETS / "header_full.png"
ASSET_FOOTER = ASSETS / "footer.png"
ASSET_WATERMARK = ASSETS / "watermark.png"
ORANGE = colors.HexColor("#F6A313")
LIGHT_GRAY = colors.HexColor("#D2D2D2")
DARK = colors.HexColor("#111111")

DOC_LABELS = {
    "invoice": "INVOICE",
    "quotation": "QUOTATION",
    "receipt": "RECEIPT",
    "delivery_note": "DELIVERY NOTE",
    "cash_sale": "CASH SALE",
}


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    @property
    def is_active(self):
        return self.is_active_user

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Document(db.Model):
    __tablename__ = "documents"
    id = db.Column(db.Integer, primary_key=True)
    doc_type = db.Column(db.String(30), nullable=False, index=True)
    number = db.Column(db.String(80), nullable=False, index=True)
    customer = db.Column(db.String(200), default="")
    doc_date = db.Column(db.String(20), nullable=False)
    total = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    user = db.relationship("User", backref="documents")


def money(value):
    try:
        return Decimal(str(value or "0").replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def image_size(path):
    with Image.open(path) as im:
        return im.size


def asset_height(path, width):
    iw, ih = image_size(path)
    return width * ih / iw


def draw_asset(c, path, x, y, w, h):
    if path.exists():
        c.drawImage(ImageReader(str(path)), x, y, width=w, height=h, preserveAspectRatio=False, mask="auto")


def draw_header(c, x, top, w, title):
    h = asset_height(ASSET_HEADER, w)
    y = top - h
    draw_asset(c, ASSET_HEADER, x, y, w, h)
    # Supplied artwork contains QUOTATION. Replace only that title area for other documents.
    if title != "QUOTATION":
        cover_x, cover_y = x + w * 0.525, y + h * 0.28
        cover_w, cover_h = w * 0.46, h * 0.47
        c.setFillColor(LIGHT_GRAY)
        c.rect(cover_x, cover_y, cover_w, cover_h, fill=1, stroke=0)
        c.setFillColor(DARK)
        size = 23 if len(title) <= 7 else 17
        c.setFont("Helvetica-Bold", size)
        c.drawCentredString(cover_x + cover_w / 2, cover_y + cover_h / 2 - size / 3, title)
    return y, h


def draw_footer(c, x, bottom, w):
    h = asset_height(ASSET_FOOTER, w)
    draw_asset(c, ASSET_FOOTER, x, bottom, w, h)
    return h


def draw_watermark(c, x, y, w, h):
    if not ASSET_WATERMARK.exists():
        return
    iw, ih = image_size(ASSET_WATERMARK)
    scale = min((w * 0.78) / iw, (h * 0.68) / ih)
    ww, wh = iw * scale, ih * scale
    c.saveState()
    try:
        c.setFillAlpha(0.55)
    except Exception:
        pass
    c.drawImage(ImageReader(str(ASSET_WATERMARK)), x + (w - ww) / 2, y + (h - wh) / 2,
                width=ww, height=wh, preserveAspectRatio=True, mask="auto")
    c.restoreState()


def truncate(text, n):
    text = str(text or "")
    return text if len(text) <= n else text[: n - 3] + "..."


def draw_customer_box(c, x, top, w, data):
    box_w, box_h = w * 0.54, 54
    y = top - box_h
    c.setStrokeColor(ORANGE)
    c.setLineWidth(1)
    c.roundRect(x, y, box_w, box_h, 4, fill=0, stroke=1)
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 8, y + 37, "M/s:")
    c.setFont("Helvetica", 8)
    c.drawString(x + 34, y + 37, truncate(data["customer"], 30))
    c.setStrokeColor(ORANGE)
    c.line(x + 8, y + 25, x + box_w - 8, y + 25)
    c.line(x + 8, y + 13, x + box_w - 8, y + 13)
    rx = x + box_w + 46
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(rx, y + 34, "No.:")
    c.setFont("Helvetica", 8)
    c.drawString(rx + 28, y + 34, truncate(data["number"], 18))
    c.setFont("Helvetica-Bold", 8)
    c.drawString(rx, y + 14, "Date:")
    c.setFont("Helvetica", 8)
    c.drawString(rx + 28, y + 14, data["date"])
    return y


def draw_items_table(c, x, top, w, items, kind="invoice", rows=9):
    if kind == "delivery_note":
        headers, widths = ["Qty", "Item Description"], [0.14, 0.86]
    elif kind == "cash_sale":
        headers, widths = ["Qty", "Description", "Unit Price", "Amount"], [0.11, 0.47, 0.20, 0.22]
    else:
        headers, widths = ["No.", "Item Description", "Qty", "Unit Price", "Amount"], [0.075, 0.50, 0.095, 0.165, 0.165]
    header_h, row_h = 23, 22
    total_h = header_h + rows * row_h
    bottom = top - total_h
    draw_watermark(c, x, bottom, w, total_h)
    c.setFillColor(LIGHT_GRAY)
    c.rect(x, top - header_h, w, header_h, fill=1, stroke=0)
    c.setStrokeColor(ORANGE if kind != "cash_sale" else DARK)
    c.setLineWidth(0.8)
    c.rect(x, bottom, w, total_h, fill=0, stroke=1)
    for r in range(1, rows + 1):
        yy = top - header_h - r * row_h
        c.line(x, yy, x + w, yy)
    xx = x
    for cw in widths[:-1]:
        xx += w * cw
        c.line(xx, top, xx, bottom)
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 7.3)
    xx = x
    for htxt, cw in zip(headers, widths):
        c.drawCentredString(xx + w * cw / 2, top - 15, htxt)
        xx += w * cw
    c.setFont("Helvetica", 7.2)
    for idx, item in enumerate(items[:rows], 1):
        yy = top - header_h - (idx - 1) * row_h - 15
        qty, unit = money(item.get("qty")), money(item.get("unit"))
        amount = qty * unit
        if kind == "delivery_note":
            c.drawCentredString(x + w * widths[0] / 2, yy, f"{qty:g}")
            c.drawString(x + w * widths[0] + 6, yy, truncate(item.get("description"), 68))
        elif kind == "cash_sale":
            x0 = x
            c.drawCentredString(x0 + w * widths[0] / 2, yy, f"{qty:g}")
            c.drawString(x0 + w * widths[0] + 5, yy, truncate(item.get("description"), 36))
            c.drawRightString(x0 + w * sum(widths[:3]) - 5, yy, f"{unit:,.2f}")
            c.drawRightString(x + w - 5, yy, f"{amount:,.2f}")
        else:
            c.drawCentredString(x + w * widths[0] / 2, yy, str(idx))
            c.drawString(x + w * widths[0] + 5, yy, truncate(item.get("description"), 48))
            c.drawCentredString(x + w * (widths[0] + widths[1] + widths[2] / 2), yy, f"{qty:g}")
            c.drawRightString(x + w * sum(widths[:4]) - 5, yy, f"{unit:,.2f}")
            c.drawRightString(x + w - 5, yy, f"{amount:,.2f}")
    return bottom


def draw_terms_and_totals(c, x, y, w, data):
    left_w, line_h = w * 0.57, 14
    rows = [
        ("Prepared By:", data.get("prepared_by")),
        ("Signature:", data.get("prepared_signature")),
        ("Terms of Payment:", data.get("terms")),
        ("Validity:", data.get("validity")),
        ("Delivery Period:", data.get("delivery_period")),
        ("Received By:", data.get("received_by")),
        ("Received Signature:", data.get("received_signature")),
    ]
    c.setFillColor(DARK)
    for i, (lab, val) in enumerate(rows):
        yy = y - i * line_h
        c.setFont("Helvetica-Bold", 7.4)
        c.drawString(x, yy, lab)
        c.setFont("Helvetica", 7.4)
        c.drawString(x + 92, yy, truncate(val or "____________________________", 42))
    bx, bw = x + left_w + 5, w - left_w - 5
    for i, (lab, val) in enumerate([
        ("Sub Total", data["subtotal"]),
        (f"VAT @ {data['vat']}%", data["vat_amount"]),
        ("GRAND TOTAL", data["total"]),
    ]):
        yy = y + 5 - i * 20
        c.setFont("Helvetica-Bold" if i == 2 else "Helvetica", 8)
        c.drawString(bx, yy, lab)
        c.setStrokeColor(ORANGE)
        c.roundRect(bx + bw * 0.45, yy - 5, bw * 0.55, 17, 3, fill=0, stroke=1)
        c.setFillColor(DARK)
        c.drawRightString(bx + bw - 5, yy, f"{money(val):,.2f}")


def draw_bank_details(c, x, y, w):
    h = 83
    c.setStrokeColor(ORANGE)
    c.setLineWidth(1)
    c.roundRect(x, y, w, h, 4, fill=0, stroke=1)
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 7, y + h - 13, "BANK DETAILS")
    rows = [
        ("Bank Name", COMPANY["bank"]),
        ("Account Name", COMPANY["account_name"]),
        ("Account Number", COMPANY["account_number"]),
        ("Branch", COMPANY["branch"]),
        ("Sort Code", COMPANY["sort_code"]),
        ("Branch Code", COMPANY["branch_code"]),
        ("Swift Code", COMPANY["swift"]),
    ]
    c.setFont("Helvetica", 6.7)
    for i, (lab, val) in enumerate(rows):
        yy = y + h - 25 - i * 9
        c.drawString(x + 7, yy, f"{lab}:")
        c.drawString(x + 82, yy, truncate(val, 40))


def draw_receipt(c, data):
    W, H = A4
    m, w = 42, W - 84
    footer_h = draw_footer(c, m, 25, w)
    hb, _ = draw_header(c, m, H - 22, w, "RECEIPT")
    y = hb - 28
    draw_watermark(c, m, y - 300, w, 260)
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(m, y, "Received with thanks from:")
    c.setFont("Helvetica", 10)
    c.drawString(m + 145, y, truncate(data["received_from"], 55))
    c.line(m + 145, y - 2, m + w, y - 2)
    for off, label, val in [(31, "Towards:", data["towards"]), (62, "The Sum of:", data["amount_words"]), (93, "Amount (ZMW):", f"{money(data['total']):,.2f}")]:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(m, y - off, label)
        c.setFont("Helvetica", 10)
        c.drawString(m + 145, y - off, truncate(val, 55))
        c.line(m + 145, y - off - 2, m + w, y - off - 2)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(m, y - 124, "Payment Method:")
    c.setFont("Helvetica", 10)
    c.drawString(m + 145, y - 124, data["payment_method"])
    c.setFont("Helvetica-Bold", 10)
    c.drawString(m, y - 165, "No.:")
    c.setFont("Helvetica", 10)
    c.drawString(m + 145, y - 165, data["number"])
    c.drawString(m + 360, y - 165, "Date:")
    c.drawString(m + 390, y - 165, data["date"])
    c.setFont("Helvetica-Bold", 10)
    c.drawString(m, y - 205, "Signature:")
    c.setFont("Helvetica", 10)
    c.drawString(m + 70, y - 205, truncate(data.get("received_signature") or "____________________________", 45))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(m, max(25 + footer_h + 5, y - 260), "TPIN : " + COMPANY["tpin"])


def draw_delivery_note(c, data):
    W, H = A4
    m, w = 42, W - 84
    footer_h = draw_footer(c, m, 25, w)
    hb, _ = draw_header(c, m, H - 22, w, "DELIVERY NOTE")
    meta_bottom = draw_customer_box(c, m, hb - 15, w, data)
    top = meta_bottom - 18
    bottom = draw_items_table(c, m, top, w, data["items"], "delivery_note", rows=11)
    y = bottom - 25
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m, y, "Delivered By:")
    c.setFont("Helvetica", 8)
    c.drawString(m + 70, y, truncate(data.get("delivered_by") or "________________________", 30))
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m + w * 0.53, y, "Signature By:")
    c.setFont("Helvetica", 8)
    c.drawString(m + w * 0.53 + 70, y, truncate(data.get("delivered_signature") or "________________________", 25))
    y -= 28
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m, y, "Received By:")
    c.setFont("Helvetica", 8)
    c.drawString(m + 70, y, truncate(data.get("received_by") or "________________________", 30))
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m + w * 0.53, y, "Signature By:")
    c.setFont("Helvetica", 8)
    c.drawString(m + w * 0.53 + 70, y, truncate(data.get("received_signature") or "________________________", 25))
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(m + w, max(25 + footer_h + 5, y - 25), "TPIN : " + COMPANY["tpin"])


def draw_cash_sale(c, data):
    W, H = A4
    m, w = 42, W - 84
    footer_h = draw_footer(c, m, 25, w)
    hb, _ = draw_header(c, m, H - 22, w, "CASH SALE")
    meta_bottom = draw_customer_box(c, m, hb - 15, w, data)
    top = meta_bottom - 18
    bottom = draw_items_table(c, m, top, w, data["items"], "cash_sale", rows=8)
    y = bottom - 25
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m, y, "Received by:")
    c.setFont("Helvetica", 8)
    c.drawString(m + 65, y, truncate(data.get("received_by") or "____________________", 30))
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m, y - 17, "Sign:")
    c.setFont("Helvetica", 8)
    c.drawString(m + 65, y - 17, truncate(data.get("received_signature") or "____________________", 30))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(m + w * 0.58, y, "TOTAL K")
    c.setStrokeColor(DARK)
    c.rect(m + w * 0.78, y - 10, w * 0.22, 22, fill=0, stroke=1)
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(m + w - 6, y - 2, f"{money(data['total']):,.2f}")
    c.drawRightString(m + w, max(25 + footer_h + 5, y - 38), "TPIN : " + COMPANY["tpin"])


def draw_standard(c, data, title):
    W, H = A4
    m, w = 34, W - 68
    footer_h = draw_footer(c, m, 24, w)
    hb, _ = draw_header(c, m, H - 22, w, title)
    meta_bottom = draw_customer_box(c, m, hb - 12, w, data)
    top = meta_bottom - 17
    bottom = draw_items_table(c, m, top, w, data["items"], data["doc_type"], rows=9)
    terms_y = bottom - 17
    draw_terms_and_totals(c, m, terms_y, w, data)
    if data["doc_type"] in ("invoice", "quotation"):
        bank_y = 24 + footer_h + 5
        draw_bank_details(c, m, bank_y, w * 0.55)
        c.setFillColor(DARK)
        c.setFont("Helvetica-Bold", 8)
        c.drawRightString(m + w, bank_y + 8, "TPIN : " + COMPANY["tpin"])
    else:
        c.setFillColor(DARK)
        c.setFont("Helvetica-Bold", 8)
        c.drawRightString(m + w, 24 + footer_h + 10, "TPIN : " + COMPANY["tpin"])


def build_data(form):
    items = []
    for desc, qty, unit in zip(form.getlist("description[]"), form.getlist("qty[]"), form.getlist("unit[]")):
        if desc.strip() or qty.strip() or unit.strip():
            items.append({"description": desc.strip(), "qty": qty or "0", "unit": unit or "0"})
    subtotal = sum((money(i["qty"]) * money(i["unit"]) for i in items), Decimal("0"))
    vat = money(form.get("vat", "16"))
    vat_amount = subtotal * vat / Decimal("100")
    total = subtotal + vat_amount
    kind = form.get("doc_type", "invoice")
    if kind == "cash_sale":
        total, vat, vat_amount = subtotal, Decimal("0"), Decimal("0")
    if kind == "receipt":
        total = money(form.get("receipt_total", "0"))
        subtotal, vat, vat_amount = total, Decimal("0"), Decimal("0")
    return {
        "doc_type": kind,
        "number": form.get("number", "").strip() or f"{kind.upper()}-{uuid.uuid4().hex[:6].upper()}",
        "date": form.get("date") or date.today().isoformat(),
        "customer": form.get("customer", "").strip(),
        "items": items,
        "vat": vat,
        "vat_amount": vat_amount,
        "subtotal": subtotal,
        "total": total,
        "prepared_by": form.get("prepared_by", "").strip(),
        "prepared_signature": form.get("prepared_signature", "").strip(),
        "terms": form.get("terms", "").strip(),
        "validity": form.get("validity", "").strip(),
        "delivery_period": form.get("delivery_period", "").strip(),
        "received_by": form.get("received_by", "").strip(),
        "received_signature": form.get("received_signature", "").strip(),
        "delivered_by": form.get("delivered_by", "").strip(),
        "delivered_signature": form.get("delivered_signature", "").strip(),
        "received_from": form.get("received_from", "").strip(),
        "towards": form.get("towards", "").strip(),
        "amount_words": form.get("amount_words", "").strip(),
        "payment_method": form.get("payment_method", "Cash"),
    }


def create_pdf(data):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    title = DOC_LABELS[data["doc_type"]]
    if data["doc_type"] == "receipt":
        draw_receipt(c, data)
    elif data["doc_type"] == "delivery_note":
        draw_delivery_note(c, data)
    elif data["doc_type"] == "cash_sale":
        draw_cash_sale(c, data)
    else:
        draw_standard(c, data, title)
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapped


@app.get("/health")
def health():
    return {"status": "ok"}


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user)
            return redirect(url_for("index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html", company=COMPANY)


@app.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        data = build_data(request.form)
        payload = {**data, "vat": str(data["vat"]), "vat_amount": str(data["vat_amount"]),
                   "subtotal": str(data["subtotal"]), "total": str(data["total"])}
        document = Document(
            doc_type=data["doc_type"], number=data["number"], customer=data["customer"],
            doc_date=data["date"], total=data["total"], payload=payload, created_by=current_user.id,
        )
        db.session.add(document)
        db.session.commit()
        pdf = create_pdf(data)
        filename = f"{data['doc_type']}_{data['number'].replace('/', '-')}.pdf"
        return send_file(pdf, as_attachment=True, download_name=filename, mimetype="application/pdf")

    docs = Document.query.order_by(Document.id.desc()).limit(20).all()
    return render_template("index.html", company=COMPANY, today=date.today().isoformat(), docs=docs)


@app.get("/documents/<int:document_id>/pdf")
@login_required
def regenerate_pdf(document_id):
    document = db.session.get(Document, document_id)
    if not document:
        abort(404)
    data = document.payload.copy()
    for key in ("vat", "vat_amount", "subtotal", "total"):
        data[key] = money(data.get(key))
    pdf = create_pdf(data)
    filename = f"{document.doc_type}_{document.number.replace('/', '-')}.pdf"
    return send_file(pdf, as_attachment=True, download_name=filename, mimetype="application/pdf")


def initialize():
    with app.app_context():
        db.create_all()
        username = os.environ.get("ADMIN_USERNAME", "admin").strip()
        password = os.environ.get("ADMIN_PASSWORD", "").strip()

        if not password:
            if os.environ.get("DATABASE_URL"):
                raise RuntimeError("ADMIN_PASSWORD must be set in production.")
            password = "ChangeMe123!"

        user = User.query.filter_by(username=username).first()

        if not user:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
        elif os.environ.get("RESET_ADMIN_PASSWORD", "").lower() in {"1", "true", "yes", "on"}:
            # Deliberate password reset for local setup or recovery.
            # This is NOT enabled unless RESET_ADMIN_PASSWORD is explicitly true.
            user.set_password(password)
            db.session.commit()


initialize()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=True)
