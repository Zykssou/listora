from flask import Flask, render_template, request, jsonify, session, redirect
import requests as http_requests
import os
import sqlite3
import secrets
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "listora-secret-2026")

DB_PATH = "/tmp/listora.db"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "listora-admin-2026")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
STRIPE_PACK_PRICE_ID = os.getenv("STRIPE_PACK_PRICE_ID", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
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
            created_at TEXT,
            week_uses INTEGER DEFAULT 0,
            week_key TEXT DEFAULT '',
            credits INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            verify_token TEXT DEFAULT ''
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
    for col, definition in [
        ("week_uses", "INTEGER DEFAULT 0"),
        ("week_key", "TEXT DEFAULT ''"),
        ("credits", "INTEGER DEFAULT 0"),
        ("verified", "INTEGER DEFAULT 0"),
        ("verify_token", "TEXT DEFAULT ''")
    ]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass
    conn.close()

init_db()

def send_verification_email(email, token, base_url):
    verify_url = base_url + "verify/" + token
    return http_requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": "Listora <noreply@listovai.com>",
            "to": [email],
            "subject": "Verify your email — Listora",
            "html": f"""
            <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:40px 20px;">
                <h1 style="color:#f97316;">Listora</h1>
                <p style="font-size:1.1rem;">Click the button below to verify your email and start generating listings.</p>
                <a href="{verify_url}" style="display:inline-block;background:#f97316;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:700;margin:20px 0;">Verify my email</a>
                <p style="color:#aaa;font-size:0.85rem;">If you didn't request this, ignore this email.</p>
            </div>
            """
        },
        timeout=10
    )

def get_week_key():
    now = datetime.now()
    return f"{now.year}-W{now.isocalendar()[1]}"

def get_or_create_user(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    week_key = get_week_key()
    c.execute("SELECT * FROM users WHERE email=?", (email,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (email, uses, paid, created_at, week_uses, week_key) VALUES (?,0,0,?,0,?)",
                  (email, datetime.now().isoformat(), week_key))
        conn.commit()
        c.execute("SELECT * FROM users WHERE email=?", (email,))
        user = c.fetchone()
    else:
        if user[6] != week_key:
            c.execute("UPDATE users SET week_uses=0, week_key=? WHERE email=?", (week_key, email))
            conn.commit()
            c.execute("SELECT * FROM users WHERE email=?", (email,))
            user = c.fetchone()
    conn.close()
    return user

def increment_uses(email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET uses = uses + 1, week_uses = week_uses + 1 WHERE email=?", (email,))
    conn.commit()
    conn.close()

def call_gemini(prompt):
    import json, time
    models_to_try = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-3.1-flash-lite"]
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            r = http_requests.post(url, json=body, timeout=90)
            if r.status_code == 503:
                time.sleep(2)
                continue
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return text, model_name
        except Exception:
            time.sleep(1)
            continue
    raise Exception("Tous les modèles sont indisponibles. Réessaie dans 1 minute.")

def generate_listing(product_name, category, features, target_audience, keywords):
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

    import json
    text, _ = call_gemini(prompt)
    start = text.find("{")
    end = text.rfind("}") + 1
    return json.loads(text[start:end])

@app.errorhandler(500)
def handle_500(e):
    import traceback
    return jsonify({"error": "Internal error", "detail": traceback.format_exc()}), 500

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    try:
        data = request.json or {}
        email = data.get("email", "").strip().lower()
        product_name = data.get("product_name", "")
        category = data.get("category", "")
        features = data.get("features", "")
        target_audience = data.get("target_audience", "")
        keywords = data.get("keywords", "")

        if not email or not product_name:
            return jsonify({"error": "Email and product name are required"}), 400

    except Exception as e:
        return jsonify({"error": "Bad request: " + str(e)}), 400

    try:
        user = get_or_create_user(email)
        uses = user[2]
        paid = user[3]

        week_uses = user[5] if len(user) > 5 else uses
        credits = user[7] if len(user) > 7 else 0
        verified = user[8] if len(user) > 8 else 0
        is_admin = ADMIN_EMAIL and email == ADMIN_EMAIL

        if not verified and not is_admin:
            token = secrets.token_urlsafe(32)
            conn2 = sqlite3.connect(DB_PATH)
            c2 = conn2.cursor()
            c2.execute("UPDATE users SET verify_token=? WHERE email=?", (token, email))
            conn2.commit()
            conn2.close()
            resend_error = None
            try:
                r = send_verification_email(email, token, request.host_url)
                if r and r.status_code != 200:
                    resend_error = r.text
            except Exception as ex:
                resend_error = str(ex)
            return jsonify({"error": "email_not_verified", "resend_debug": resend_error, "message": "Check your inbox to verify your email."}), 403

        if week_uses >= FREE_USES and not paid and not is_admin and credits <= 0:
            return jsonify({"error": "free_limit", "message": "You've used your 3 free listings. Upgrade to Pro for unlimited listings."}), 403

        result = generate_listing(product_name, category, features, target_audience, keywords)
        increment_uses(email)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        import json
        c.execute("INSERT INTO listings (email, product_name, result, created_at) VALUES (?,?,?,?)",
                  (email, product_name, json.dumps(result), datetime.now().isoformat()))
        conn.commit()
        conn.close()

        if not paid and not is_admin and week_uses >= FREE_USES and credits > 0:
            conn2 = sqlite3.connect(DB_PATH)
            c2 = conn2.cursor()
            c2.execute("UPDATE users SET credits = credits - 1 WHERE email=?", (email,))
            conn2.commit()
            conn2.close()
        remaining = max(0, FREE_USES - (week_uses + 1)) if not paid else 999
        return jsonify({"success": True, "listing": result, "remaining": remaining, "paid": paid})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "detail": traceback.format_exc()}), 500

@app.route("/test-resend")
def test_resend():
    test_email = request.args.get("email", "test@example.com")
    r = http_requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": "Listora <noreply@listovai.com>",
            "to": [test_email],
            "subject": "Test",
            "html": "<p>Test email</p>"
        },
        timeout=10
    )
    return jsonify({"status": r.status_code, "response": r.json()})

@app.route("/test-api")
def test_api():
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not set"}), 500
    try:
        text, model_name = call_gemini("Say hello in one word")
        return jsonify({"ok": True, "model": model_name, "response": text})
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

@app.route("/upgrade")
def upgrade():
    email = request.args.get("email", "").strip().lower()
    if not email or not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return redirect("/")
    import stripe as stripe_lib
    stripe_lib.api_key = STRIPE_SECRET_KEY
    try:
        session = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            customer_email=email,
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=request.host_url + "upgrade/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.host_url,
            metadata={"email": email}
        )
        return redirect(session.url)
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route("/buy-pack")
def buy_pack():
    email = request.args.get("email", "").strip().lower()
    if not email or not STRIPE_SECRET_KEY or not STRIPE_PACK_PRICE_ID:
        return redirect("/")
    import stripe as stripe_lib
    stripe_lib.api_key = STRIPE_SECRET_KEY
    try:
        session = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            customer_email=email,
            line_items=[{"price": STRIPE_PACK_PRICE_ID, "quantity": 1}],
            success_url=request.host_url + "pack/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.host_url,
            metadata={"email": email}
        )
        return redirect(session.url)
    except Exception as e:
        return f"Error: {str(e)}", 500

@app.route("/pack/success")
def pack_success():
    session_id = request.args.get("session_id", "")
    if session_id and STRIPE_SECRET_KEY:
        import stripe as stripe_lib
        stripe_lib.api_key = STRIPE_SECRET_KEY
        try:
            session = stripe_lib.checkout.Session.retrieve(session_id)
            if session.payment_status == "paid":
                email = (session.metadata.get("email") or session.customer_email or "").lower()
                if email:
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("SELECT * FROM users WHERE email=?", (email,))
                    if not c.fetchone():
                        c.execute("INSERT INTO users (email, uses, paid, created_at, week_uses, week_key, credits) VALUES (?,0,0,?,0,?,10)",
                                  (email, datetime.now().isoformat(), get_week_key()))
                    else:
                        c.execute("UPDATE users SET credits = credits + 10 WHERE email=?", (email,))
                    conn.commit()
                    conn.close()
        except:
            pass
    return render_template("pack_success.html")

@app.route("/upgrade/success")
def upgrade_success():
    session_id = request.args.get("session_id", "")
    if session_id and STRIPE_SECRET_KEY:
        import stripe as stripe_lib
        stripe_lib.api_key = STRIPE_SECRET_KEY
        try:
            session = stripe_lib.checkout.Session.retrieve(session_id)
            if session.payment_status == "paid":
                email = (session.metadata.get("email") or session.customer_email or "").lower()
                if email:
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("SELECT * FROM users WHERE email=?", (email,))
                    if not c.fetchone():
                        c.execute("INSERT INTO users (email, uses, paid, created_at) VALUES (?,0,1,?)", (email, datetime.now().isoformat()))
                    else:
                        c.execute("UPDATE users SET paid=1 WHERE email=?", (email,))
                    conn.commit()
                    conn.close()
        except:
            pass
    return render_template("upgrade_success.html")

@app.route("/verify/<token>")
def verify_email(token):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT email FROM users WHERE verify_token=?", (token,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE users SET verified=1, verify_token='' WHERE verify_token=?", (token,))
        conn.commit()
    conn.close()
    return render_template("verified.html", success=bool(row))

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/admin")
def admin():
    pwd = request.args.get("pwd", "")
    if pwd != ADMIN_PASSWORD:
        return render_template("admin_login.html")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings")
    total_listings = c.fetchone()[0]
    c.execute("SELECT email, uses, paid, created_at FROM users ORDER BY created_at DESC LIMIT 50")
    users = c.fetchall()
    c.execute("SELECT email, product_name, created_at FROM listings ORDER BY created_at DESC LIMIT 20")
    recent = c.fetchall()
    conn.close()
    return render_template("admin.html", total_users=total_users, total_listings=total_listings, users=users, recent=recent)

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
