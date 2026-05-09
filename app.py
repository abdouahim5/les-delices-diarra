import os
import json
import io
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from urllib.parse import quote
from pathlib import Path
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, text
from datetime import datetime
from textwrap import wrap
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.utils import ImageReader

load_dotenv(dotenv_path=Path(__file__).resolve().with_name(".env"), override=False)

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", "cle-secrete-temporaire")

# Bump this string when debugging deployments/restarts
APP_BUILD = "admin-crud-v1"

RESTAURANT_NAME = "Les Délices de Diarra"
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "").strip() or None
WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER")
BASE_URL = os.getenv("BASE_URL", "https://les-delices-diarra.onrender.com")
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip() or None

PRODUCTS_PATH = Path(__file__).resolve().with_name("products.json")
UPLOAD_DIR = Path(__file__).resolve().parent / "static" / "images" / "uploads"
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _normalize_database_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    # Render sometimes provides "postgres://", SQLAlchemy expects "postgresql://"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


_db_url = _normalize_database_url(DATABASE_URL)
# Flask-SQLAlchemy requires a URI at import time. If DATABASE_URL is not set
# (e.g. first Render deploy or local JSON mode), we use an in-memory sqlite.
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url or "sqlite:///:memory:"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

_invoice_serializer = URLSafeTimedSerializer(app.secret_key, salt="invoice")


def _wa_phone(phone: str) -> str:
    """
    wa.me expects digits only (no '+'). We keep digits and drop leading zeros only if user typed local;
    best practice is to store phones with country code.
    """
    p = (phone or "").strip()
    digits = "".join([ch for ch in p if ch.isdigit()])
    return digits


def _invoice_token(order_id: int) -> str:
    return _invoice_serializer.dumps({"oid": int(order_id)})


_STATUS_FR = {
    "new": "Nouvelle",
    "confirmed": "Confirmée",
    "preparing": "Préparation",
    "delivering": "Livraison",
    "delivered": "Livrée",
    "cancelled": "Annulée",
}


def _build_invoice_pdf(order: "Order", restaurant_name: str, whatsapp_number: str | None) -> bytes:
    """
    PDF invoice layout aligned with the web invoice: white rounded card, header + meta, client blocks,
    details table, peach total bar, footer.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    C_TEXT = HexColor("#0f172a")
    C_MUTED = HexColor("#64748b")
    C_BORDER = HexColor("#e5e7eb")
    C_CARD_BG = HexColor("#f9fafb")
    C_TOTAL_FILL = HexColor("#fff5f0")
    C_TOTAL_BORDER = HexColor("#fed7aa")
    C_BG_PAGE = HexColor("#f3f4f6")
    C_LINE = HexColor("#e5e7eb")

    margin = 14 * mm
    pad = 10 * mm
    radius_card = 14
    radius_box = 8

    card_w = width - 2 * margin
    card_h = height - 2 * margin
    card_x = margin
    card_y = margin
    ix = card_x + pad
    usable_w = card_w - 2 * pad
    iy_top = card_y + card_h - pad

    public_id = order.public_id or f"#{order.id}"
    dt_s = order.created_at.strftime("%Y-%m-%d %H:%M")
    stat_fr = _STATUS_FR.get(order.status or "", order.status or "—")
    items = list(order.items)

    row_h = 22
    thead_h = 18
    total_bar_h = 40
    footer_h = 52  # trois lignes centrées (nom, adresse, remerciement)

    def page_bg():
        c.setFillColor(C_BG_PAGE)
        c.rect(0, 0, width, height, fill=1, stroke=0)

    def dashed_h(x1, x2, y):
        c.saveState()
        c.setDash(3, 3)
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.5)
        c.line(x1, y, x2, y)
        c.restoreState()

    def draw_card_frame():
        c.setFillColor(HexColor("#ffffff"))
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.75)
        c.roundRect(card_x, card_y, card_w, card_h, radius_card, fill=1, stroke=1)

    def table_column_xs(inner_left: float, inner_w: float):
        x_prod = inner_left
        x_pu = inner_left + inner_w * 0.44
        x_qt = inner_left + inner_w * 0.66
        x_st = inner_left + inner_w - 2
        return x_prod, x_pu, x_qt, x_st

    def draw_table_head(y_baseline: float, inner_left: float, inner_w: float) -> float:
        """Returns y below header band (start of first row text)."""
        band_bottom = y_baseline - thead_h + 3
        c.setFillColor(HexColor("#f9fafb"))
        c.rect(inner_left - 2, band_bottom, inner_w + 4, thead_h, fill=1, stroke=0)
        x_prod, x_pu, x_qt, x_st = table_column_xs(inner_left, inner_w)
        c.setFont("Helvetica-Bold", 7.5)
        c.setFillColor(C_MUTED)
        c.drawString(x_prod, y_baseline - 11, "PRODUIT")
        c.drawRightString(x_pu, y_baseline - 11, "PU")
        c.drawRightString(x_qt, y_baseline - 11, "QTÉ")
        c.drawRightString(x_st, y_baseline - 11, "SOUS-TOTAL")
        c.setStrokeColor(C_LINE)
        c.setLineWidth(0.5)
        c.line(inner_left - 2, band_bottom, inner_left + inner_w + 2, band_bottom)
        return band_bottom - 8

    def draw_item_row(y_baseline: float, it, inner_left: float, inner_w: float) -> None:
        x_prod, x_pu, x_qt, x_st = table_column_xs(inner_left, inner_w)
        c.setStrokeColor(C_LINE)
        c.line(inner_left - 2, y_baseline + 16, inner_left + inner_w + 2, y_baseline + 16)
        name = (it.name or "")[:52]
        c.setFillColor(C_TEXT)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x_prod, y_baseline, name)
        c.setFont("Helvetica", 9)
        c.drawRightString(x_pu, y_baseline, f"{int(it.unit_price)} FCFA")
        c.drawRightString(x_qt, y_baseline, str(int(it.qty)))
        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(x_st, y_baseline, f"{int(it.subtotal)} FCFA")

    page_bg()
    draw_card_frame()

    # --- Header: logo + brand (left), meta block (right) ---
    header_band = max(42 * mm, 38 * mm)
    header_bottom_y = iy_top - header_band

    logo_size = 15 * mm
    logo_y = iy_top - logo_size - 4
    logo_x = ix
    logo_drawn = False
    logo_path = Path(__file__).resolve().parent / "static" / "images" / "logo2.jpg"
    try:
        if logo_path.exists():
            img = ImageReader(str(logo_path))
            c.setFillColor(HexColor("#ffffff"))
            c.setStrokeColor(C_BORDER)
            c.roundRect(logo_x, logo_y, logo_size, logo_size, 6, fill=1, stroke=1)
            c.drawImage(
                img, logo_x + 2, logo_y + 2, width=logo_size - 4, height=logo_size - 4,
                mask="auto", preserveAspectRatio=True,
            )
            logo_drawn = True
    except Exception:
        logo_drawn = False

    tx = ix + (logo_size + 9 if logo_drawn else 0)
    c.setFillColor(C_TEXT)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(tx, iy_top - 16, restaurant_name[:55])
    c.setFont("Helvetica", 8.5)
    c.setFillColor(C_MUTED)
    yb = iy_top - 30
    c.drawString(tx, yb, "Facture • Commande")
    yb -= 12
    c.setFont("Helvetica", 9)
    c.drawString(tx, yb, "Thies, quartier SOM, près de la mosquée Ndiakhaté")
    if whatsapp_number:
        yb -= 12
        c.drawString(tx, yb, f"Numéro WhatsApp : {whatsapp_number}")

    yy_m = iy_top - 14
    for label, val in (("N°", public_id), ("Date", dt_s), ("Statut", stat_fr)):
        c.setFont("Helvetica", 8.5)
        lw = stringWidth(label, "Helvetica", 8.5)
        c.setFont("Helvetica-Bold", 9)
        vw = stringWidth(val, "Helvetica-Bold", 9)
        gap = 8
        block = lw + gap + vw
        x_start = ix + usable_w - block - 4
        c.setFillColor(C_MUTED)
        c.setFont("Helvetica", 8.5)
        c.drawString(x_start, yy_m, label)
        c.setFillColor(C_TEXT)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x_start + lw + gap, yy_m, val)
        yy_m -= 16

    dashed_h(ix, ix + usable_w, header_bottom_y)

    # --- Client & livraison ---
    y = header_bottom_y - 18
    c.setFillColor(C_TEXT)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(ix, y, "Client & livraison")

    box_h = 34 * mm
    box_gap = 5 * mm
    box_w = (usable_w - box_gap) / 2
    box_top_baseline = y - 14
    box_ll_y = box_top_baseline - box_h

    addr = (order.customer_address or "").strip()[:400]
    c.setFillColor(C_CARD_BG)
    c.setStrokeColor(HexColor("#e2e8f0"))
    c.roundRect(ix, box_ll_y, box_w, box_h, radius_box, fill=1, stroke=1)
    c.roundRect(ix + box_w + box_gap, box_ll_y, box_w, box_h, radius_box, fill=1, stroke=1)

    # Text inside boxes (from top of box downward)
    inner_top = box_ll_y + box_h
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(C_MUTED)
    c.drawString(ix + 10, inner_top - 12, "Client")
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(C_TEXT)
    c.drawString(ix + 10, inner_top - 26, (order.customer_name or "")[:40])
    c.setFont("Helvetica", 9)
    c.setFillColor(C_MUTED)
    c.drawString(ix + 10, inner_top - 40, (order.customer_phone or "")[:42])

    rx = ix + box_w + box_gap
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(C_MUTED)
    c.drawString(rx + 10, inner_top - 12, "Adresse")
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(C_TEXT)
    adr_lines: list[str] = []
    if addr:
        for line in addr.splitlines():
            adr_lines.extend(wrap(line.strip() or "", width=34)[:5])
        if not adr_lines:
            adr_lines = ["—"]
    else:
        adr_lines = ["—"]
    ay = inner_top - 26
    for ln in adr_lines[:4]:
        c.drawString(rx + 10, ay, ln[:44])
        ay -= 12

    # --- Détails ---
    y_detail_title = box_ll_y - 20
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(C_TEXT)
    c.drawString(ix, y_detail_title, "Détails")

    inner_left = ix + 8
    inner_w = usable_w - 16
    ly = draw_table_head(y_detail_title - 14, inner_left, inner_w)

    reserve_bottom = card_y + footer_h + total_bar_h + 50
    item_idx = 0

    while item_idx < len(items):
        it = items[item_idx]
        if ly - row_h < reserve_bottom:
            c.showPage()
            page_bg()
            draw_card_frame()
            c.setFillColor(C_TEXT)
            c.setFont("Helvetica-Bold", 10)
            title = f"Détails (suite) — {public_id}"
            c.drawString(ix, iy_top - 20, title)
            ly = iy_top - 44
            ly = draw_table_head(ly, inner_left, inner_w)
            reserve_bottom = card_y + footer_h + total_bar_h + 50
            continue
        draw_item_row(ly, it, inner_left, inner_w)
        ly -= row_h
        item_idx += 1

    gap_above_total = 16
    bar_ll_y = ly - gap_above_total - total_bar_h

    c.setStrokeColor(C_TOTAL_BORDER)
    c.setFillColor(C_TOTAL_FILL)
    c.roundRect(ix - 6, bar_ll_y, usable_w + 12, total_bar_h, radius_box + 3, fill=1, stroke=1)
    c.setFillColor(C_TEXT)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(ix + 6, bar_ll_y + 13, "Total")
    c.setFont("Helvetica-Bold", 13.5)
    c.drawRightString(ix + usable_w + 4, bar_ll_y + 14, f"{int(order.total)} FCFA")

    cx = card_x + card_w / 2
    c.setFillColor(C_TEXT)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(cx, card_y + 36, restaurant_name)
    c.drawCentredString(
        cx,
        card_y + 24,
        "Adresse : Thies, quartier SOM, près de la mosquée Ndiakhaté",
    )
    c.drawCentredString(cx, card_y + 12, "Merci pour votre commande.")

    c.save()
    return buf.getvalue()


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.String(80), primary_key=True)
    category = db.Column(db.String(120), nullable=False)
    name = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    price = db.Column(db.Integer, nullable=False)
    image = db.Column(db.String(255), nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True)


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(180), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    address = db.Column(db.Text, nullable=False, default="")


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(32), unique=True, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(32), nullable=False, default="new")  # new, confirmed, preparing, delivering, delivered, cancelled

    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    client = db.relationship("Client", backref=db.backref("orders", lazy=True))

    customer_name = db.Column(db.String(180), nullable=False)
    customer_phone = db.Column(db.String(40), nullable=False)
    customer_address = db.Column(db.Text, nullable=False)
    note = db.Column(db.Text, nullable=False, default="")

    total = db.Column(db.Integer, nullable=False, default=0)


def _make_order_public_id(order_id: int) -> str:
    # Example: CMD-20260506-0001
    dt = datetime.utcnow()
    return f"CMD-{dt.strftime('%Y%m%d')}-{order_id:04d}"


def _ensure_db_schema() -> None:
    """
    Minimal migration helper for local/prod without Alembic.
    create_all() does NOT alter existing tables; so we add missing columns safely.
    """
    if not _db_enabled():
        return
    with app.app_context():
        db.create_all()
        try:
            # orders: new columns
            db.session.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS public_id VARCHAR(32) NOT NULL DEFAULT ''"))
            db.session.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS client_id INTEGER NULL"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            # If schema changes fail, we let the route error bubble up in dev
            raise


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    qty = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Integer, nullable=False, default=0)
    subtotal = db.Column(db.Integer, nullable=False, default=0)
    image = db.Column(db.String(255), nullable=False, default="")

    order = db.relationship("Order", backref=db.backref("items", lazy=True, cascade="all, delete-orphan"))


def _db_enabled() -> bool:
    # Only treat as "enabled" when a real DATABASE_URL is provided.
    return bool(DATABASE_URL)


def get_category_names() -> list[str]:
    if _db_enabled():
        with app.app_context():
            db.create_all()
            return [r[0] for r in db.session.query(Category.name).order_by(Category.name.asc()).all()]
    return sorted({p.get("category", "") for p in get_products() if p.get("category")})


def _seed_products_from_json_if_needed() -> None:
    """
    One-time bootstrap: when DB is configured and empty, import products.json.
    This keeps local/prod behavior consistent and avoids manual re-entry.
    """
    if not _db_enabled():
        return
    json_products = _read_products_file()
    if not json_products:
        return

    with app.app_context():
        db.create_all()
        existing_any = db.session.query(Product.id).limit(1).first()
        if existing_any:
            # Ensure categories table is populated at least once
            if db.session.query(Category.id).limit(1).first() is None:
                for (cat,) in db.session.query(Product.category).distinct().all():
                    if cat and not db.session.query(Category.id).filter(Category.name == cat).first():
                        db.session.add(Category(name=cat))
                db.session.commit()
            return

        for p in json_products:
            name = (p.get("name") or "").strip()
            category = (p.get("category") or "").strip()
            if not name or not category:
                continue

            product_id = (p.get("id") or "").strip() or _slugify_id(name)
            try:
                price = int(p.get("price") or 0)
            except (TypeError, ValueError):
                price = 0

            db.session.add(Product(
                id=product_id,
                category=category,
                name=name,
                description=(p.get("description") or "").strip(),
                price=price,
                image=(p.get("image") or "").strip(),
                active=bool(p.get("active", True)),
            ))
            if not db.session.query(Category.id).filter(Category.name == category).first():
                db.session.add(Category(name=category))
        db.session.commit()

def _read_products_file() -> list[dict]:
    if not PRODUCTS_PATH.exists():
        return []
    with PRODUCTS_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    products = data.get("products", [])
    if not isinstance(products, list):
        return []
    return products


def _write_products_file(products: list[dict]) -> None:
    PRODUCTS_PATH.write_text(
        json.dumps({"products": products}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _slugify_id(value: str) -> str:
    keep = []
    value = (value or "").strip().lower()
    for ch in value:
        if ch.isalnum():
            keep.append(ch)
        elif ch in {" ", "_", "-"}:
            keep.append("-")
    out = "".join(keep).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "produit"


# Seed after JSON helpers exist
_seed_products_from_json_if_needed()


def get_products() -> list[dict]:
    # Preferred: database (Render Postgres)
    if _db_enabled():
        with app.app_context():
            rows = Product.query.order_by(Product.category.asc(), Product.name.asc()).all()
            return [{
                "id": r.id,
                "category": r.category,
                "name": r.name,
                "description": r.description or "",
                "price": int(r.price),
                "image": r.image or "",
                "active": bool(r.active),
            } for r in rows]

    # Fallback: local JSON (dev)
    products = _read_products_file()
    for p in products:
        p.setdefault("description", "")
        p.setdefault("active", True)
        if "id" not in p:
            p["id"] = _slugify_id(p.get("name", "produit"))
    return products


def get_active_categories(products: list[dict]):
    categories = {}
    for item in products:
        if item.get("active", True):
            categories.setdefault(item.get("category", "Autres"), []).append(item)
    return categories


def _ensure_upload_dir():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@app.route("/", methods=["GET", "POST"])
def home():
    products = get_products()
    categories = get_active_categories(products)
    error = None
    total = 0
    order_summary = []

    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        telephone = request.form.get("telephone", "").strip()
        adresse = request.form.get("adresse", "").strip()

        for item in products:
            if not item.get("active", True):
                continue

            try:
                qty = int(request.form.get(item["name"], 0))
            except ValueError:
                qty = 0

            if qty > 0:
                subtotal = qty * item["price"]
                total += subtotal

                order_summary.append({
                    "name": item["name"],
                    "qty": qty,
                    "subtotal": subtotal,
                    "image": item["image"]
                })

        if not order_summary:
            error = "Veuillez sélectionner au moins un produit."
        elif not nom or not telephone or not adresse:
            error = "Veuillez remplir toutes les informations."
        else:
            commande_text = "\n\n".join([
                f"🍴 {x['name']} x{x['qty']} = {x['subtotal']} FCFA\n"
                f"🖼️ Image : {BASE_URL}/static/images/{x['image']}"
                for x in order_summary
            ])

            message = f"""📦 Nouvelle commande - {RESTAURANT_NAME}

👤 Nom : {nom}
📞 Téléphone : {telephone}
📍 Adresse : {adresse}

🛒 Commande :
{commande_text}

💰 Total : {total} FCFA
"""

            whatsapp_link = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(message)}"

            # Persist order for admin tracking (DB only)
            if _db_enabled():
                with app.app_context():
                    _ensure_db_schema()

                    def _normalize_phone(p: str) -> str:
                        p = (p or "").strip()
                        if not p:
                            return ""
                        keep = []
                        for ch in p:
                            if ch.isdigit() or ch == "+":
                                keep.append(ch)
                        out = "".join(keep)
                        # keep only leading '+'
                        if out.count("+") > 1:
                            out = ("+" if out.startswith("+") else "") + out.replace("+", "")
                        return out

                    phone_key = _normalize_phone(telephone)
                    existing = None
                    if phone_key:
                        existing = Client.query.filter(Client.phone == phone_key).first()

                    if existing:
                        # Keep client up to date with latest info
                        existing.full_name = nom or existing.full_name
                        existing.address = adresse or existing.address
                        c = existing
                    else:
                        c = Client(
                            full_name=nom,
                            phone=phone_key or telephone,
                            address=adresse,
                        )
                        db.session.add(c)
                        db.session.flush()

                    o = Order(
                        status="new",
                        public_id="",
                        client_id=c.id if c else None,
                        customer_name=nom,
                        customer_phone=telephone,
                        customer_address=adresse,
                        note="",
                        total=total,
                    )
                    db.session.add(o)
                    db.session.flush()  # get o.id
                    o.public_id = _make_order_public_id(o.id)
                    for it in order_summary:
                        db.session.add(OrderItem(
                            order_id=o.id,
                            name=it["name"],
                            qty=int(it["qty"]),
                            unit_price=int(it["subtotal"] / max(int(it["qty"]), 1)),
                            subtotal=int(it["subtotal"]),
                            image=it.get("image", ""),
                        ))
                    db.session.commit()

            return redirect(whatsapp_link)

    return render_template(
        "index.html",
        restaurant_name=RESTAURANT_NAME,
        categories=categories,
        error=error
    )

@app.context_processor
def inject_build():
    return {"app_build": APP_BUILD}


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        password = (request.form.get("password") or "").strip()

        if not ADMIN_PASSWORD:
            return render_template("admin_login.html", error="ADMIN_PASSWORD non configuré sur le serveur (Render → Environment).")

        if password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))

        return render_template("admin_login.html", error="Mot de passe incorrect")

    return render_template("admin_login.html")


@app.route("/admin/dashboard", methods=["GET", "POST"])
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin"))

    # Modern dashboard
    stats = {
        "products_total": 0,
        "products_active": 0,
        "products_inactive": 0,
        "categories_total": 0,
        "clients_total": 0,
        "orders_total": 0,
        "orders_new": 0,
        "orders_confirmed": 0,
        "orders_preparing": 0,
        "orders_delivering": 0,
        "orders_delivered": 0,
        "orders_cancelled": 0,
        "revenue_delivered": 0,
    }
    recent_orders = []

    if _db_enabled():
        _ensure_db_schema()
        # Products / categories / clients
        stats["products_total"] = int(Product.query.count())
        stats["products_active"] = int(Product.query.filter(Product.active.is_(True)).count())
        stats["products_inactive"] = int(Product.query.filter(Product.active.is_(False)).count())
        stats["categories_total"] = int(Category.query.count())
        stats["clients_total"] = int(Client.query.count())

        # Orders
        stats["orders_total"] = int(Order.query.count())
        for s in ("new", "confirmed", "preparing", "delivering", "delivered", "cancelled"):
            stats[f"orders_{s}"] = int(Order.query.filter(Order.status == s).count())

        rev = db.session.query(db.func.coalesce(db.func.sum(Order.total), 0)).filter(Order.status == "delivered").scalar()
        stats["revenue_delivered"] = int(rev or 0)

        recent_orders = (
            Order.query.order_by(Order.created_at.desc(), Order.id.desc()).limit(6).all()
        )
    else:
        products = get_products()
        stats["products_total"] = len(products)
        stats["products_active"] = len([p for p in products if p.get("active", True)])
        stats["products_inactive"] = len([p for p in products if not p.get("active", True)])
        stats["categories_total"] = len({p.get("category") for p in products if p.get("category")})

    return render_template(
        "admin_dashboard.html",
        title="Dashboard",
        restaurant_name=RESTAURANT_NAME,
        active_page="dashboard",
        kicker="Admin",
        heading="Dashboard",
        stats=stats,
        recent_orders=recent_orders,
    )


def _unique_product_id(base: str) -> str:
    base = base.strip()
    if not base:
        base = "produit"

    if not _db_enabled():
        products = get_products()
        existing_ids = {p.get("id") for p in products}
    else:
        existing_ids = {r[0] for r in db.session.query(Product.id).all()}

    if base not in existing_ids:
        return base

    i = 2
    while f"{base}-{i}" in existing_ids:
        i += 1
    return f"{base}-{i}"


@app.route("/admin/products", methods=["GET"])
def admin_products():
    if not session.get("admin"):
        return redirect(url_for("admin"))

    q = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()

    # Default behavior: show active only.
    # If the user explicitly selects "Tous" (empty string) we must respect it.
    raw_status = request.args.get("status")
    status = "active" if raw_status is None else raw_status.strip()
    auto_show_all = False
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    page = max(page, 1)
    try:
        per_page = int(request.args.get("per_page", "10"))
    except ValueError:
        per_page = 10
    per_page = min(max(per_page, 5), 50)

    def _apply_filters_db(status_value: str):
        query = Product.query
        if q:
            like = f"%{q}%"
            query = query.filter(
                or_(
                    Product.name.ilike(like),
                    Product.category.ilike(like),
                    Product.description.ilike(like),
                )
            )
        if category:
            query = query.filter(Product.category == category)
        if status_value == "active":
            query = query.filter(Product.active.is_(True))
        elif status_value == "inactive":
            query = query.filter(Product.active.is_(False))

        total = query.count()
        rows = (
            query.order_by(Product.category.asc(), Product.name.asc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        out_products = [{
            "id": r.id,
            "category": r.category,
            "name": r.name,
            "description": r.description or "",
            "price": int(r.price),
            "image": r.image or "",
            "active": bool(r.active),
        } for r in rows]
        out_categories = [
            r[0] for r in db.session.query(Product.category).distinct().order_by(Product.category.asc()).all()
        ]
        return out_products, out_categories, total

    def _apply_filters_json(status_value: str):
        out_products = get_products()
        if q:
            ql = q.lower()
            out_products = [
                p for p in out_products
                if ql in (p.get("name", "").lower() + " " + p.get("category", "").lower() + " " + p.get("description", "").lower())
            ]
        if category:
            out_products = [p for p in out_products if p.get("category") == category]
        if status_value == "active":
            out_products = [p for p in out_products if p.get("active", True)]
        elif status_value == "inactive":
            out_products = [p for p in out_products if not p.get("active", True)]
        out_categories = sorted({p.get("category", "") for p in get_products() if p.get("category")})
        total = len(out_products)
        start = (page - 1) * per_page
        out_products = out_products[start:start + per_page]
        return out_products, out_categories, total

    if _db_enabled():
        products, categories, total = _apply_filters_db(status)
        if raw_status is None and status == "active" and total == 0:
            # If there are no active products, don’t show an empty admin.
            auto_show_all = True
            status = ""
            products, categories, total = _apply_filters_db(status)
    else:
        products, categories, total = _apply_filters_json(status)
        if raw_status is None and status == "active" and total == 0:
            auto_show_all = True
            status = ""
            products, categories, total = _apply_filters_json(status)

    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    return render_template(
        "admin_products_list.html",
        title="Produits",
        restaurant_name=RESTAURANT_NAME,
        active_page="products",
        kicker="Catalogue",
        heading="Produits",
        products=products,
        categories=categories,
        q=q,
        selected_category=category,
        selected_status=status,
        auto_show_all=auto_show_all,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


@app.route("/admin/products/new", methods=["GET", "POST"])
def admin_product_new():
    if not session.get("admin"):
        return redirect(url_for("admin"))

    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "").strip()
        description = (request.form.get("description") or "").strip()
        try:
            price = int(request.form.get("price", "0"))
        except ValueError:
            price = 0
        active = (request.form.get("active") == "on")

        image = request.files.get("image")
        if not name or not category or price <= 0:
            error = "Nom, catégorie et prix sont obligatoires."
        elif not image or not image.filename:
            error = "Veuillez ajouter une image."
        else:
            ext = Path(image.filename).suffix.lower()
            if ext not in ALLOWED_IMAGE_EXTS:
                error = "Format image non supporté (jpg, png, webp)."

        if not error:
            _ensure_upload_dir()
            product_id = _unique_product_id(_slugify_id(name))
            filename = secure_filename(f"{product_id}{ext}")
            image.save(str(UPLOAD_DIR / filename))
            image_path = f"uploads/{filename}"

            if _db_enabled():
                db.session.add(Product(
                    id=product_id,
                    category=category,
                    name=name,
                    description=description,
                    price=price,
                    image=image_path,
                    active=active,
                ))
                db.session.commit()
            else:
                products = get_products()
                products.append({
                    "id": product_id,
                    "category": category,
                    "name": name,
                    "description": description,
                    "price": price,
                    "image": image_path,
                    "active": active,
                })
                _write_products_file(products)

            return redirect(url_for("admin_products"))

    template = "admin_product_form_partial.html" if request.headers.get("X-Admin-Modal") == "1" else "admin_product_form.html"
    return render_template(
        template,
        title="Nouveau produit",
        restaurant_name=RESTAURANT_NAME,
        active_page="products",
        kicker="Catalogue",
        heading="Nouveau produit",
        error=error,
        product=None,
        categories=get_category_names(),
    )


@app.route("/admin/products/<product_id>/edit", methods=["GET", "POST"])
def admin_product_edit(product_id: str):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    if _db_enabled():
        p = Product.query.get(product_id)
        if not p:
            return redirect(url_for("admin_products"))
        product = {
            "id": p.id,
            "category": p.category,
            "name": p.name,
            "description": p.description or "",
            "price": int(p.price),
            "image": p.image or "",
            "active": bool(p.active),
        }
    else:
        products = get_products()
        product = next((x for x in products if x.get("id") == product_id), None)
        if not product:
            return redirect(url_for("admin_products"))

    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "").strip()
        description = (request.form.get("description") or "").strip()
        try:
            price = int(request.form.get("price", "0"))
        except ValueError:
            price = 0
        active = (request.form.get("active") == "on")

        image = request.files.get("image")
        image_path = product.get("image", "")
        if not name or not category or price <= 0:
            error = "Nom, catégorie et prix sont obligatoires."
        else:
            if image and image.filename:
                ext = Path(image.filename).suffix.lower()
                if ext not in ALLOWED_IMAGE_EXTS:
                    error = "Format image non supporté (jpg, png, webp)."
                else:
                    _ensure_upload_dir()
                    filename = secure_filename(f"{product_id}{ext}")
                    image.save(str(UPLOAD_DIR / filename))
                    image_path = f"uploads/{filename}"

        if not error:
            if _db_enabled():
                p = Product.query.get(product_id)
                if p:
                    p.name = name
                    p.category = category
                    p.description = description
                    p.price = price
                    p.active = active
                    p.image = image_path
                    db.session.commit()
            else:
                products = get_products()
                for item in products:
                    if item.get("id") == product_id:
                        item.update({
                            "name": name,
                            "category": category,
                            "description": description,
                            "price": price,
                            "active": active,
                            "image": image_path,
                        })
                        break
                _write_products_file(products)
            return redirect(url_for("admin_products"))

        product = {**product, "name": name, "category": category, "description": description, "price": price, "active": active, "image": image_path}

    template = "admin_product_form_partial.html" if request.headers.get("X-Admin-Modal") == "1" else "admin_product_form.html"
    return render_template(
        template,
        title="Modifier produit",
        restaurant_name=RESTAURANT_NAME,
        active_page="products",
        kicker="Catalogue",
        heading="Modifier produit",
        error=error,
        product=product,
        categories=get_category_names(),
    )


@app.route("/admin/products/<product_id>", methods=["GET"])
def admin_product_view(product_id: str):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    if _db_enabled():
        p = Product.query.get(product_id)
        if not p:
            return redirect(url_for("admin_products"))
        product = {
            "id": p.id,
            "category": p.category,
            "name": p.name,
            "description": p.description or "",
            "price": int(p.price),
            "image": p.image or "",
            "active": bool(p.active),
        }
    else:
        products = get_products()
        product = next((x for x in products if x.get("id") == product_id), None)
        if not product:
            return redirect(url_for("admin_products"))

    template = "admin_product_view_partial.html" if request.headers.get("X-Admin-Modal") == "1" else "admin_product_view.html"
    return render_template(
        template,
        title="Produit",
        restaurant_name=RESTAURANT_NAME,
        active_page="products",
        kicker="Catalogue",
        heading="Produit",
        product=product,
    )


@app.route("/admin/products/<product_id>/delete", methods=["POST"])
def admin_product_delete(product_id: str):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    if _db_enabled():
        p = Product.query.get(product_id)
        if p:
            db.session.delete(p)
            db.session.commit()
    else:
        products = get_products()
        products = [p for p in products if p.get("id") != product_id]
        _write_products_file(products)

    return redirect(url_for("admin_products"))


@app.route("/admin/products/<product_id>/toggle", methods=["POST"])
def admin_product_toggle(product_id: str):
    if not session.get("admin"):
        return redirect(url_for("admin"))

    if _db_enabled():
        p = Product.query.get(product_id)
        if p:
            p.active = not bool(p.active)
            db.session.commit()
    else:
        products = get_products()
        for item in products:
            if item.get("id") == product_id:
                item["active"] = not item.get("active", True)
                break
        _write_products_file(products)

    # Stay on the same admin page (preserve filters/search)
    return redirect(request.referrer or url_for("admin_products"))

    if request.method == "POST":
        action = request.form.get("action", "toggle")
        if action == "toggle":
            product_id = request.form.get("product_id", "")
            if _db_enabled():
                p = Product.query.get(product_id)
                if p:
                    p.active = not bool(p.active)
                    db.session.commit()
            else:
                for item in products:
                    if item.get("id") == product_id:
                        item["active"] = not item.get("active", True)
                        break
                _write_products_file(products)
        elif action == "add":
            name = request.form.get("name", "").strip()
            category = request.form.get("category", "").strip()
            description = request.form.get("description", "").strip()
            try:
                price = int(request.form.get("price", "0"))
            except ValueError:
                price = 0

            image = request.files.get("image")
            if not name or not category or price <= 0:
                return render_template("admin_dashboard.html", menu=products, error="Nom, catégorie et prix sont obligatoires.")
            if not image or not image.filename:
                return render_template("admin_dashboard.html", menu=products, error="Veuillez ajouter une image.")

            ext = Path(image.filename).suffix.lower()
            if ext not in ALLOWED_IMAGE_EXTS:
                return render_template("admin_dashboard.html", menu=products, error="Format image non supporté (jpg, png, webp).")

            _ensure_upload_dir()
            product_id = _slugify_id(name)
            # ensure unique id
            existing_ids = {p.get("id") for p in products}
            if product_id in existing_ids:
                i = 2
                while f"{product_id}-{i}" in existing_ids:
                    i += 1
                product_id = f"{product_id}-{i}"

            filename = secure_filename(f"{product_id}{ext}")
            image.save(str(UPLOAD_DIR / filename))

            image_path = f"uploads/{filename}"

            if _db_enabled():
                db.session.add(Product(
                    id=product_id,
                    category=category,
                    name=name,
                    description=description,
                    price=price,
                    image=image_path,
                    active=True,
                ))
                db.session.commit()
            else:
                products.append({
                    "id": product_id,
                    "category": category,
                    "name": name,
                    "description": description,
                    "price": price,
                    "image": image_path,
                    "active": True,
                })
                _write_products_file(products)

        return redirect(url_for("admin_dashboard"))

    return render_template(
        "admin_dashboard.html",
        title="Produits",
        restaurant_name=RESTAURANT_NAME,
        active_page="products",
        kicker="Catalogue",
        heading="Produits",
        menu=products,
        error=None,
    )


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("home"))


@app.route("/admin/products.json", methods=["GET"])
def admin_products_export():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    return jsonify({"products": get_products(), "source": "db" if _db_enabled() else "json"})


@app.route("/admin/clients", methods=["GET"])
def admin_clients():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return render_template(
            "admin_placeholder.html",
            title="Clients",
            restaurant_name=RESTAURANT_NAME,
            active_page="clients",
            kicker="CRM",
            heading="Clients",
            description="Active DATABASE_URL pour gérer les clients en base.",
        )

    db.create_all()
    q = (request.args.get("q") or "").strip()
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    page = max(page, 1)
    try:
        per_page = int(request.args.get("per_page", "10"))
    except ValueError:
        per_page = 10
    per_page = min(max(per_page, 5), 50)

    query = Client.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Client.full_name.ilike(like), Client.phone.ilike(like), Client.address.ilike(like)))
    total = query.count()
    clients = (
        query.order_by(Client.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    return render_template(
        "admin_clients_list.html",
        title="Clients",
        restaurant_name=RESTAURANT_NAME,
        active_page="clients",
        kicker="CRM",
        heading="Clients",
        clients=clients,
        q=q,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


@app.route("/admin/clients/new", methods=["GET", "POST"])
def admin_client_new():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_clients"))

    error = None
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        address = (request.form.get("address") or "").strip()
        if not full_name or not phone:
            error = "Nom et téléphone sont obligatoires."
        else:
            db.create_all()
            db.session.add(Client(full_name=full_name, phone=phone, address=address))
            db.session.commit()
            return redirect(url_for("admin_clients"))

    template = "admin_client_form_partial.html" if request.headers.get("X-Admin-Modal") == "1" else "admin_client_form.html"
    return render_template(
        template,
        title="Nouveau client",
        restaurant_name=RESTAURANT_NAME,
        active_page="clients",
        kicker="CRM",
        heading="Nouveau client",
        error=error,
        client=None,
    )


@app.route("/admin/clients/<int:client_id>/edit", methods=["GET", "POST"])
def admin_client_edit(client_id: int):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_clients"))

    db.create_all()
    c = db.session.get(Client, client_id)
    if not c:
        return redirect(url_for("admin_clients"))

    client = {"id": c.id, "full_name": c.full_name, "phone": c.phone, "address": c.address or ""}
    error = None
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        address = (request.form.get("address") or "").strip()
        if not full_name or not phone:
            error = "Nom et téléphone sont obligatoires."
        else:
            c.full_name = full_name
            c.phone = phone
            c.address = address
            db.session.commit()
            return redirect(url_for("admin_clients"))
        client = {"id": c.id, "full_name": full_name, "phone": phone, "address": address}

    template = "admin_client_form_partial.html" if request.headers.get("X-Admin-Modal") == "1" else "admin_client_form.html"
    return render_template(
        template,
        title="Modifier client",
        restaurant_name=RESTAURANT_NAME,
        active_page="clients",
        kicker="CRM",
        heading="Modifier client",
        error=error,
        client=client,
    )


@app.route("/admin/clients/<int:client_id>", methods=["GET"])
def admin_client_view(client_id: int):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_clients"))

    db.create_all()
    c = db.session.get(Client, client_id)
    if not c:
        return redirect(url_for("admin_clients"))

    orders_count = int(Order.query.filter(Order.client_id == c.id).count())
    orders = (
        Order.query.filter(Order.client_id == c.id)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(20)
        .all()
    )

    template = "admin_client_view_partial.html" if request.headers.get("X-Admin-Modal") == "1" else "admin_client_view.html"
    return render_template(
        template,
        title="Client",
        restaurant_name=RESTAURANT_NAME,
        active_page="clients",
        kicker="CRM",
        heading="Client",
        client=c,
        orders_count=orders_count,
        orders=orders,
    )


@app.route("/admin/clients/<int:client_id>/delete", methods=["POST"])
def admin_client_delete(client_id: int):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_clients"))

    c = db.session.get(Client, client_id)
    if c:
        db.session.delete(c)
        db.session.commit()
    return redirect(request.referrer or url_for("admin_clients"))


@app.route("/admin/categories", methods=["GET", "POST"])
def admin_categories():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return render_template(
            "admin_placeholder.html",
            title="Catégories",
            restaurant_name=RESTAURANT_NAME,
            active_page="categories",
            kicker="Organisation",
            heading="Catégories",
            description="Active DATABASE_URL pour gérer les catégories en base.",
        )

    error = None
    name = ""
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            error = "Nom de catégorie requis."
        elif db.session.query(Category.id).filter(Category.name == name).first():
            error = "Cette catégorie existe déjà."
        else:
            db.session.add(Category(name=name))
            db.session.commit()
            return redirect(url_for("admin_categories"))

    cats = db.session.query(Category).order_by(Category.name.asc()).all()
    return render_template(
        "admin_categories_list.html",
        title="Catégories",
        restaurant_name=RESTAURANT_NAME,
        active_page="categories",
        kicker="Organisation",
        heading="Catégories",
        categories=cats,
        error=error,
        name=name,
    )


@app.route("/admin/categories/<int:category_id>/delete", methods=["POST"])
def admin_category_delete(category_id: int):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_categories"))

    c = db.session.get(Category, category_id)
    if c:
        db.session.delete(c)
        db.session.commit()
    return redirect(url_for("admin_categories"))


@app.route("/admin/orders", methods=["GET"])
def admin_orders():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return render_template(
            "admin_placeholder.html",
            title="Commandes",
            restaurant_name=RESTAURANT_NAME,
            active_page="orders",
            kicker="Ventes",
            heading="Commandes",
            description="Active DATABASE_URL pour suivre les commandes en base.",
        )

    q = (request.args.get("q") or "").strip()
    raw_status = request.args.get("status")
    status = "" if raw_status is None else raw_status.strip()
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    page = max(page, 1)
    try:
        per_page = int(request.args.get("per_page", "10"))
    except ValueError:
        per_page = 10
    per_page = min(max(per_page, 5), 50)

    _ensure_db_schema()
    query = Order.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Order.customer_name.ilike(like), Order.customer_phone.ilike(like), Order.customer_address.ilike(like)))
    if status:
        query = query.filter(Order.status == status)

    total = query.count()
    rows = (
        query.order_by(Order.created_at.desc(), Order.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    return render_template(
        "admin_orders_list.html",
        title="Commandes",
        restaurant_name=RESTAURANT_NAME,
        active_page="orders",
        kicker="Ventes",
        heading="Commandes",
        orders=rows,
        q=q,
        selected_status=status,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


@app.route("/admin/orders/<int:order_id>", methods=["GET"])
def admin_order_view(order_id: int):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_orders"))

    _ensure_db_schema()
    o = db.session.get(Order, order_id)
    if not o:
        return redirect(url_for("admin_orders"))

    template = "admin_order_view_partial.html" if request.headers.get("X-Admin-Modal") == "1" else "admin_order_view.html"
    return render_template(
        template,
        title=f"Commande #{o.id}",
        restaurant_name=RESTAURANT_NAME,
        active_page="orders",
        kicker="Ventes",
        heading="Commande",
        order=o,
    )


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
def admin_order_update_status(order_id: int):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_orders"))

    new_status = (request.form.get("status") or "").strip()
    allowed = {"new", "confirmed", "preparing", "delivering", "delivered", "cancelled"}
    if new_status not in allowed:
        return redirect(request.referrer or url_for("admin_orders"))

    _ensure_db_schema()
    o = db.session.get(Order, order_id)
    if o:
        o.status = new_status
        db.session.commit()
    return redirect(request.referrer or url_for("admin_orders"))


@app.route("/admin/orders/<int:order_id>/receipt", methods=["GET"])
def admin_order_receipt(order_id: int):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_orders"))

    _ensure_db_schema()
    o = db.session.get(Order, order_id)
    if not o:
        return redirect(url_for("admin_orders"))

    token = _invoice_token(o.id)
    public_url = f"{BASE_URL}{url_for('public_invoice', order_id=o.id)}?token={token}"
    public_pdf_url = f"{BASE_URL}{url_for('public_invoice_pdf', order_id=o.id)}?token={token}"
    phone_digits = _wa_phone(o.customer_phone)
    wa_invoice_link = None
    if phone_digits:
        msg = f"Bonjour {o.customer_name}, voici votre facture (PDF) : {public_pdf_url}"
        wa_invoice_link = f"https://wa.me/{phone_digits}?text={quote(msg)}"

    return render_template(
        "admin_order_receipt.html",
        title=f"Reçu {o.public_id if o.public_id else ('#' + str(o.id))}",
        restaurant_name=RESTAURANT_NAME,
        whatsapp_number=WHATSAPP_NUMBER,
        public_invoice_url=public_url,
        public_invoice_pdf_url=public_pdf_url,
        wa_invoice_link=wa_invoice_link,
        active_page="orders",
        kicker="Ventes",
        heading="Reçu",
        order=o,
    )


@app.route("/admin/orders/<int:order_id>/receipt.pdf", methods=["GET"])
def admin_order_receipt_pdf(order_id: int):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_orders"))

    _ensure_db_schema()
    o = db.session.get(Order, order_id)
    if not o:
        return redirect(url_for("admin_orders"))

    pdf_bytes = _build_invoice_pdf(o, RESTAURANT_NAME, WHATSAPP_NUMBER)
    filename = f"facture-{(o.public_id or str(o.id)).replace('/', '-')}.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/invoice/<int:order_id>", methods=["GET"])
def public_invoice(order_id: int):
    """
    Public invoice view for customers (token-protected).
    """
    token = (request.args.get("token") or "").strip()
    if not token:
        return "Not found", 404
    try:
        data = _invoice_serializer.loads(token, max_age=60 * 60 * 24 * 30)  # 30 days
    except SignatureExpired:
        return "Lien expiré", 410
    except BadSignature:
        return "Not found", 404

    if not isinstance(data, dict) or int(data.get("oid") or 0) != int(order_id):
        return "Not found", 404

    if not _db_enabled():
        return "Not found", 404

    _ensure_db_schema()
    o = db.session.get(Order, order_id)
    if not o:
        return "Not found", 404

    return render_template(
        "invoice_public.html",
        restaurant_name=RESTAURANT_NAME,
        whatsapp_number=WHATSAPP_NUMBER,
        order=o,
    )


@app.route("/invoice/<int:order_id>/pdf", methods=["GET"])
def public_invoice_pdf(order_id: int):
    token = (request.args.get("token") or "").strip()
    if not token:
        return "Not found", 404
    try:
        data = _invoice_serializer.loads(token, max_age=60 * 60 * 24 * 30)  # 30 days
    except SignatureExpired:
        return "Lien expiré", 410
    except BadSignature:
        return "Not found", 404

    if not isinstance(data, dict) or int(data.get("oid") or 0) != int(order_id):
        return "Not found", 404
    if not _db_enabled():
        return "Not found", 404

    _ensure_db_schema()
    o = db.session.get(Order, order_id)
    if not o:
        return "Not found", 404

    pdf_bytes = _build_invoice_pdf(o, RESTAURANT_NAME, WHATSAPP_NUMBER)
    filename = f"facture-{(o.public_id or str(o.id)).replace('/', '-')}.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/admin/orders/<int:order_id>/delete", methods=["POST"])
def admin_order_delete(order_id: int):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_orders"))

    _ensure_db_schema()
    o = db.session.get(Order, order_id)
    if not o:
        return redirect(request.referrer or url_for("admin_orders"))

    # Business rule: allow delete only for new/cancelled
    if o.status not in {"new", "cancelled"}:
        return redirect(request.referrer or url_for("admin_orders"))

    db.session.delete(o)
    db.session.commit()
    return redirect(request.referrer or url_for("admin_orders"))


@app.route("/admin/orders/new", methods=["GET", "POST"])
def admin_order_new():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    if not _db_enabled():
        return redirect(url_for("admin_orders"))

    _ensure_db_schema()
    error = None
    clients = Client.query.order_by(Client.full_name.asc()).all()
    products = get_products()
    if request.method == "POST":
        client_id_raw = (request.form.get("client_id") or "").strip()
        note = (request.form.get("note") or "").strip()
        adresse = (request.form.get("adresse") or "").strip()
        status = (request.form.get("status") or "new").strip()
        if status not in {"new", "confirmed", "preparing", "delivering", "delivered", "cancelled"}:
            status = "new"

        # Client selection only
        client_id = None
        try:
            client_id = int(client_id_raw) if client_id_raw else None
        except ValueError:
            client_id = None
        c = db.session.get(Client, client_id) if client_id else None

        # Parse dynamic items
        products_by_id = {p.get("id"): p for p in products if p.get("id")}
        item_ids = request.form.getlist("item_product_id")
        item_qtys = request.form.getlist("item_qty")

        items = []
        total = 0
        for pid, qty_raw in zip(item_ids, item_qtys):
            pid = (pid or "").strip()
            if not pid or pid not in products_by_id:
                continue
            try:
                qty = int(qty_raw or "0")
            except ValueError:
                qty = 0
            if qty <= 0:
                continue

            p = products_by_id[pid]
            unit_price = int(p.get("price") or 0)
            subtotal = qty * unit_price
            total += subtotal
            items.append({
                "name": p.get("name", ""),
                "qty": qty,
                "unit_price": unit_price,
                "subtotal": subtotal,
                "image": p.get("image", ""),
            })

        if not c:
            error = "Sélectionne un client."
        elif not adresse:
            error = "Adresse de livraison obligatoire."
        elif not items:
            error = "Sélectionne au moins un produit (quantité > 0)."
        else:
            _ensure_db_schema()
            o = Order(
                status=status,
                public_id="",
                client_id=c.id,
                customer_name=c.full_name,
                customer_phone=c.phone,
                customer_address=adresse,
                note=note,
                total=total,
            )
            db.session.add(o)
            db.session.flush()
            o.public_id = _make_order_public_id(o.id)
            for it in items:
                db.session.add(OrderItem(
                    order_id=o.id,
                    name=it["name"],
                    qty=int(it["qty"]),
                    unit_price=int(it["unit_price"]),
                    subtotal=int(it["subtotal"]),
                    image=it.get("image", ""),
                ))
            db.session.commit()
            return redirect(url_for("admin_orders"))

    template = "admin_order_form_partial.html" if request.headers.get("X-Admin-Modal") == "1" else "admin_order_form.html"
    return render_template(
        template,
        title="Nouvelle commande",
        restaurant_name=RESTAURANT_NAME,
        active_page="orders",
        kicker="Ventes",
        heading="Nouvelle commande",
        error=error,
        clients=clients,
        products=products,
    )


if __name__ == "__main__":
    app.run()