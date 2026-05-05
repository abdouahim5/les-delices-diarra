from flask import Flask, render_template, request
from urllib.parse import quote

app = Flask(__name__)

# 👉 Mets TON numéro WhatsApp ici (sans +)
WHATSAPP_NUMBER = "221777849040"

RESTAURANT_NAME = "Les délices de Diarra"

# 👉 Produits pour composer la box
menu = [
    {"category": "Box salé", "name": "Mini Pizza", "price": 1.5, "image": "mini_pizza.jpg"},
    {"category": "Box salé", "name": "Tortilla", "price": 1.5, "image": "tortilla.jpg"},
    {"category": "Box salé", "name": "Rissole", "price": 1.2, "image": "rissole.jpg"},
    {"category": "Box salé", "name": "Neem", "price": 1.2, "image": "neem.jpg"},
    {"category": "Box salé", "name": "Brochette", "price": 2.0, "image": "brochette.jpg"},
    {"category": "Box salé", "name": "Fataya", "price": 1.2, "image": "fataya.jpg"},

    {"category": "Box sucré", "name": "Beignets nature", "price": 1.0, "image": "beignets.jpg"},
    {"category": "Box sucré", "name": "Beignets sucrés", "price": 1.2, "image": "beignets_sucres.jpg"},
    {"category": "Box sucré", "name": "Beignets mix", "price": 1.5, "image": "beignets_mix.jpg"},

    {"category": "Sandwich", "name": "Burger", "price": 8, "image": "burger.jpg"},
    {"category": "Sandwich", "name": "Tacos", "price": 9, "image": "tacos.jpg"},
    {"category": "Sandwich", "name": "Kebab", "price": 8, "image": "kebab.jpg"},
]


# 👉 Organiser les catégories
def build_categories():
    categories = {}
    for item in menu:
        categories.setdefault(item["category"], []).append(item)
    return categories


@app.route("/", methods=["GET", "POST"])
def home():
    categories = build_categories()
    whatsapp_link = None
    order_summary = []
    total = 0
    error = None

    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        telephone = request.form.get("telephone", "").strip()
        adresse = request.form.get("adresse", "").strip()
        note = request.form.get("note", "").strip()

        # 👉 Lire les quantités
        for item in menu:
            try:
                qty = int(request.form.get(item["name"], 0))
            except:
                qty = 0

            if qty > 0:
                subtotal = qty * item["price"]
                order_summary.append({
                    "name": item["name"],
                    "qty": qty,
                    "subtotal": subtotal
                })
                total += subtotal

        # 👉 Vérifications
        if not order_summary:
            error = "Veuillez choisir au moins un produit."
        elif not nom or not telephone or not adresse:
            error = "Veuillez remplir toutes les informations."
        else:
            # 👉 Message WhatsApp
            commande_text = "\n".join(
                f"🍴 {item['name']} x{item['qty']} = {item['subtotal']} €"
                for item in order_summary
            )

            message = f"""📦 Nouvelle commande - {RESTAURANT_NAME}

👤 Nom : {nom}
📞 Téléphone : {telephone}
📍 Adresse : {adresse}

🛒 Commande :
{commande_text}

💰 Total : {total} €

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


# 👉 IMPORTANT POUR RENDER
if __name__ == "__main__":
    app.run()