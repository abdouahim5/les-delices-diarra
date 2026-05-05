from flask import Flask, render_template, request
from urllib.parse import quote

app = Flask(__name__)

# 👉 TON NUMERO WHATSAPP (sans +)
WHATSAPP_NUMBER = "221776526751"

menu = [
    # BOX SALEES
    {"category": "Box salé", "name": "Box 7000", "price": 7000, "image": "box_7000.jpg"},
    {"category": "Box salé", "name": "Box 8000", "price": 8000, "image": "box_8000.jpg"},
    {"category": "Box salé", "name": "Box 10000", "price": 10000, "image": "box_10000.jpg"},
    {"category": "Box salé", "name": "Box 13000", "price": 13000, "image": "box_13000.jpg"},

    # BOX SUCREES
    {"category": "Box sucré", "name": "Beignets 5000", "price": 5000, "image": "box_beignet_16000.jpg"},
    {"category": "Box sucré", "name": "Donuts 7000", "price": 7000, "image": "box_12500.jpg"},

    # SANDWICH
    {"category": "Sandwich", "name": "Burger", "price": 3000, "image": "burger.jpg"},
    {"category": "Sandwich", "name": "Tacos", "price": 3500, "image": "tacos.jpg"},
    {"category": "Sandwich", "name": "Kebab", "price": 3000, "image": "kebab.jpg"},
]


@app.route("/", methods=["GET", "POST"])
def home():
    categories = {}
    for item in menu:
        categories.setdefault(item["category"], []).append(item)

    whatsapp_link = None
    order_summary = []
    total = 0

    if request.method == "POST":
        nom = request.form.get("nom")
        telephone = request.form.get("telephone")
        adresse = request.form.get("adresse")

        for item in menu:
            qty = int(request.form.get(item["name"], 0))
            if qty > 0:
                subtotal = qty * item["price"]
                total += subtotal

                order_summary.append({
                    "name": item["name"],
                    "qty": qty,
                    "subtotal": subtotal,
                    "image": item["image"]
                })

        commande = "\n".join([
            f"{x['name']} x{x['qty']} = {x['subtotal']} FCFA"
            for x in order_summary
        ])

        images = "\n".join([
            f"https://les-delices-diarra.onrender.com/static/images/{x['image']}"
            for x in order_summary
        ])

        message = f"""
Nouvelle commande

Nom: {nom}
Téléphone: {telephone}
Adresse: {adresse}

Commande:
{commande}

Total: {total} FCFA

Images:
{images}
"""

        whatsapp_link = f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(message)}"

    return render_template("index.html",
                           categories=categories,
                           whatsapp_link=whatsapp_link)


if __name__ == "__main__":
    app.run(debug=True)