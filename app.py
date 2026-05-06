import os
from flask import Flask, render_template, request, redirect, url_for, session
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", "cle-secrete-temporaire")

RESTAURANT_NAME = "Les Délices de Diarra"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD","1234")
WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER","221776526751")
BASE_URL = os.getenv("BASE_URL", "https://les-delices-diarra.onrender.com")

menu = [
    {"category": "Box salé", "name": "Box 7000 FCFA", "price": 7000, "image": "box_7000.jpg", "active": True},
    {"category": "Box salé", "name": "Box 8000 FCFA", "price": 8000, "image": "box_8000.jpg", "active": True},
    {"category": "Box salé", "name": "Box 10000 FCFA", "price": 10000, "image": "box_10000.jpg", "active": True},
    {"category": "Box salé", "name": "Box 12500 FCFA", "price": 12500, "image": "box_12500.jpg", "active": True},
    {"category": "Box salé", "name": "Box 13000 FCFA", "price": 13000, "image": "box_13000.jpg", "active": True},

    {"category": "Box sucré", "name": "Box Beignets 16000 FCFA", "price": 16000, "image": "box_beignet_16000.jpg", "active": True},

    {"category": "Sandwich", "name": "Burger", "price": 3000, "image": "burger.jpg", "active": True},
    {"category": "Sandwich", "name": "Tacos", "price": 3500, "image": "tacos.jpg", "active": True},
    {"category": "Sandwich", "name": "Kebab", "price": 3000, "image": "kebab.jpg", "active": True},
]


def get_active_categories():
    categories = {}
    for item in menu:
        if item.get("active", True):
            categories.setdefault(item["category"], []).append(item)
    return categories


@app.route("/", methods=["GET", "POST"])
def home():
    categories = get_active_categories()
    whatsapp_link = None
    error = None
    total = 0
    order_summary = []

    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        telephone = request.form.get("telephone", "").strip()
        adresse = request.form.get("adresse", "").strip()
        note = request.form.get("note", "").strip()

        for item in menu:
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

📝 Note :
{note if note else "Aucune"}
"""

            whatsapp_link = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(message)}"

    return render_template(
        "index.html",
        restaurant_name=RESTAURANT_NAME,
        categories=categories,
        whatsapp_link=whatsapp_link,
        error=error
    )


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        password = request.form.get("password", "")

        if password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))

        return render_template("admin_login.html", error="Mot de passe incorrect")

    return render_template("admin_login.html")


@app.route("/admin/dashboard", methods=["GET", "POST"])
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin"))

    if request.method == "POST":
        product_name = request.form.get("product_name")

        for item in menu:
            if item["name"] == product_name:
                item["active"] = not item.get("active", True)
                break

        return redirect(url_for("admin_dashboard"))

    return render_template("admin_dashboard.html", menu=menu)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run()