from flask import Flask, render_template, request, jsonify, session
from google import genai
import os
import sqlite3
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "listora-secret-2026")

DB_PATH = "listora.db"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FREE_USES = 3

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            uses INTEGER DEFAULT 0,
            paid INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            product_name TEXT,
            result TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_or_create_user(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email=?", (email,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (email, uses, paid, created_at) VALUES (?,0,0,?)",
                  (email, datetime.now().isoformat()))
        conn.commit()
        c.execute("SELECT * FROM users WHERE email=?", (email,))
        user = c.fetchone()
    conn.close()
    return user

def increment_uses(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET uses = uses + 1 WHERE email=?", (email,))
    conn.commit()
    conn.close()

def generate_listing(product_name, category, features, target_audience, keywords):
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""You are an expert Amazon listing copywriter. Generate a complete, optimized Amazon product listing for the following product.

Product Name: {product_name}
Category: {category}
Key Features: {features}
Target Audience: {target_audience}
Main Keywords: {keywords}

Generate the following in JSON format only, no extra text:
{{
  "title": "Optimized Amazon title (max 200 characters, include main keywords)",
  "bullet_points": [
    "Bullet point 1 (start with a benefit, include keyword)",
    "Bullet point 2",
    "Bullet point 3",
    "Bullet point 4",
    "Bullet point 5"
  ],
  "description": "Full product description (300-500 words, SEO optimized, engaging)",
  "backend_keywords": "backend search terms separated by spaces (max 250 bytes)"
}}

Rules:
- Title: Include primary keyword naturally, highlight main benefit
- Bullets: Start with CAPS benefit word, focus on what customer gains
- Description: Tell a story, include social proof language, end with CTA
- Keywords: Include variations, synonyms, related terms"""

    response = client.models.generate_content(model="gemini-3.8-flash", contents=prompt)
    import json
    text = response.text
    start = text.find("{")
    end = text.rfind("}") + 1
    return json.loads(text[start:end])

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    data = request.json
    email = data.get("email", "").strip().lower()
    product_name = data.get("product_name", "")
    category = data.get("category", "")
    features = data.get("features", "")
    target_audience = data.get("target_audience", "")
    keywords = data.get("keywords", "")

    if not email or not product_name:
        return jsonify({"error": "Email and product name are required"}), 400

    user = get_or_create_user(email)
    uses = user[2]
    paid = user[3]

    if uses >= FREE_USES and not paid:
        return jsonify({"error": "free_limit", "message": "You've used your 3 free listings. Upgrade to Pro for unlimited listings."}), 403

    try:
        result = generate_listing(product_name, category, features, target_audience, keywords)
        increment_uses(email)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        import json
        c.execute("INSERT INTO listings (email, product_name, result, created_at) VALUES (?,?,?,?)",
                  (email, product_name, json.dumps(result), datetime.now().isoformat()))
        conn.commit()
        conn.close()

        remaining = max(0, FREE_USES - (uses + 1)) if not paid else 999
        return jsonify({"success": True, "listing": result, "remaining": remaining, "paid": paid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/test-api")
def test_api():
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not set"}), 500
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model="gemini-3.8-flash", contents="Say hello in one word")
        return jsonify({"ok": True, "response": response.text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/stats")
def stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings")
    listings = c.fetchone()[0]
    conn.close()
    return jsonify({"users": users, "listings": listings})

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
