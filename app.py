import os, uuid, urllib.parse
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── Environment Variables ─────────────────────────────────────
# Railway-এর Variables tab-এ এগুলো set থাকতে হবে
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").rstrip("/")
ADMIN_KEY    = os.environ.get("ADMIN_KEY", "boighor2024")
STORE_NAME   = os.environ.get("STORE_NAME", "বইঘর")
BACKEND_URL  = os.environ.get("BACKEND_URL", "")   # e.g. https://web-production-890be.up.railway.app
GAS_URL      = os.environ.get("GAS_URL", "")       # Google Apps Script exec URL


# ══════════════════════════════════════════════════════════════
# Firebase Helper Functions
# ══════════════════════════════════════════════════════════════

def fb_get(path):
    """Firebase থেকে data read করো"""
    try:
        r = requests.get(f"{FIREBASE_URL}/{path}.json", timeout=8)
        return r.json() if r.ok else None
    except Exception as e:
        print(f"❌ fb_get error ({path}): {e}")
        return None

def fb_set(path, data):
    """Firebase-এ data write করো"""
    try:
        r = requests.put(f"{FIREBASE_URL}/{path}.json", json=data, timeout=8)
        return r.ok
    except Exception as e:
        print(f"❌ fb_set error ({path}): {e}")
        return False


# ══════════════════════════════════════════════════════════════
# Email — SMTP নয়, Google Apps Script দিয়ে পাঠানো হয়
# কারণ Railway Gmail SMTP port (465/587) block করে রাখে
# ══════════════════════════════════════════════════════════════

def send_email_via_gas(to_email, buyer_name, book_title, download_url):
    """GAS-কে HTTP GET করলে GAS নিজে MailApp.sendEmail() দিয়ে email পাঠায়"""
    if not GAS_URL:
        print("❌ GAS_URL set নেই — email skip করা হলো")
        return False
    try:
        params = urllib.parse.urlencode({
            "action": "sendEmail",
            "to":     to_email,
            "name":   buyer_name,
            "book":   book_title,
            "link":   download_url,
            "store":  STORE_NAME
        })
        r = requests.get(f"{GAS_URL}?{params}", timeout=20, allow_redirects=True)
        print(f"✅ GAS response ({r.status_code}): {r.text[:200]}")
        return r.status_code == 200 and "error" not in r.text.lower()
    except Exception as e:
        print(f"❌ GAS email error: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    """সব কিছু ঠিক আছে কিনা দ্রুত চেক করার জন্য"""
    return jsonify({
        "status":   "ok",
        "firebase": "✅" if FIREBASE_URL else "❌ FIREBASE_URL set নেই",
        "email":    "✅ GAS" if GAS_URL else "⚠️ GAS_URL set নেই"
    })


@app.route("/admin/issue-token", methods=["POST"])
def issue_token():
    """
    Admin panel বা GAS থেকে call হয়।
    Token তৈরি করে Firebase-এ save করে, তারপর GAS দিয়ে email পাঠায়।
    """
    data = request.get_json(force=True) or {}

    # Admin key যাচাই
    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    email      = (data.get("email")      or "").strip()
    buyer_name = (data.get("buyer_name") or "ক্রেতা").strip()
    order_id   = (data.get("order_id")   or "").strip()
    book_title = (data.get("book_title") or "").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_id    = (data.get("book_id")    or "").strip()

    # drive_link না থাকলে book_id দিয়ে Firebase থেকে ebookLink খোঁজো
    if not drive_link and book_id and FIREBASE_URL:
        book_data = fb_get(f"books/{book_id}")
        if isinstance(book_data, dict):
            drive_link = book_data.get("ebookLink", "").strip()
            if not book_title:
                book_title = book_data.get("title", "").strip()
        print(f"Firebase lookup → book_id={book_id}, link={drive_link[:60] if drive_link else 'EMPTY'}")

    # Validation
    if not email:
        return jsonify({"success": False, "error": "email required"}), 400
    if not drive_link:
        return jsonify({"success": False, "error": "ebookLink not found — বইয়ের ebookLink field দিন"}), 400

    # Token তৈরি করো — 48 ঘণ্টার জন্য valid
    token      = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()

    # Email-এ যাওয়া link হবে intermediate page (/download/TOKEN)
    # সরাসরি /confirm নয়, কারণ Gmail scanner সেটাও hit করে ফেলতে পারে
    download_url = f"{BACKEND_URL}/download/{token}"

    # Firebase-এ save করো
    saved = fb_set(f"tokens/{token}", {
        "email":      email,
        "drive_link": drive_link,
        "order_id":   order_id,
        "book_title": book_title,
        "buyer_name": buyer_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "used":       False
    })
    print(f"Token saved to Firebase: {saved} | token={token[:8]}...")

    # GAS দিয়ে email পাঠাও
    email_sent = send_email_via_gas(email, buyer_name, book_title, download_url)

    return jsonify({
        "success":      True,
        "token":        token,
        "download_url": download_url,
        "email_sent":   email_sent
    })


@app.route("/download/<token>")
def download(token):
    """
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Gmail Scanner Problem এবং সমাধান:

    Gmail, Outlook সহ সব email client নিরাপত্তার
    জন্য email-এর প্রতিটা link নিজে থেকে visit করে
    (malware/phishing check)। এই visit-এ যদি token
    "used" mark হয়ে যায়, তাহলে আসল user click
    করলে "already used" error দেখে।

    সমাধান: এই route-এ token কখনো mark করা হবে না।
    শুধু একটা HTML page দেখানো হবে। Gmail scanner
    এই page দেখবে কিন্তু বাটনে click করবে না।
    User বাটনে click করলে /confirm route-এ যাবে,
    সেখানে token mark করা হবে এবং Drive redirect হবে।
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """
    t = fb_get(f"tokens/{token}")

    if not isinstance(t, dict):
        return "❌ লিংকটি বৈধ নয়।", 404
    if t.get("used"):
        return "⚠️ এই লিংক আগেই ব্যবহার করা হয়েছে।", 410
    try:
        exp = datetime.fromisoformat(t.get("expires_at", ""))
        if datetime.now(timezone.utc) > exp:
            return "⌛ এই লিংকের মেয়াদ শেষ।", 410
    except:
        pass

    # Token valid — intermediate HTML page দেখাও
    book_title = t.get("book_title", "আপনার ইবুক")
    buyer_name = t.get("buyer_name", "")
    greeting   = f"প্রিয় {buyer_name}," if buyer_name else "আপনার অর্ডার নিশ্চিত হয়েছে।"

    html = f"""<!DOCTYPE html>
<html lang="bn">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ইবুক ডাউনলোড — {STORE_NAME}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: Arial, sans-serif;
      background: #faf8f3;
      min-height: 100vh;
      display: flex;
      justify-content: center;
      align-items: center;
      padding: 20px;
    }}
    .card {{
      background: white;
      border-radius: 16px;
      padding: 40px 32px;
      text-align: center;
      box-shadow: 0 4px 24px rgba(0,0,0,0.1);
      max-width: 420px;
      width: 100%;
    }}
    .logo {{
      background: linear-gradient(135deg, #2c1a08, #8a5210);
      color: #e8a84c;
      font-size: 22px;
      font-weight: 700;
      padding: 16px;
      border-radius: 10px;
      margin-bottom: 24px;
      letter-spacing: 1px;
    }}
    .icon {{ font-size: 52px; margin-bottom: 14px; }}
    h2 {{ color: #2c1a08; font-size: 18px; margin-bottom: 10px; }}
    .greeting {{ color: #4a3520; font-size: 14px; margin-bottom: 20px; }}
    .book-title {{
      background: #f5f0e8;
      border: 1px solid #e8dcc8;
      border-radius: 8px;
      padding: 12px 16px;
      color: #2c1a08;
      font-size: 15px;
      font-weight: 600;
      margin-bottom: 28px;
    }}
    .btn {{
      display: inline-block;
      background: #0f766e;
      color: white;
      padding: 16px 36px;
      border-radius: 10px;
      text-decoration: none;
      font-size: 16px;
      font-weight: 700;
      margin-bottom: 20px;
      transition: background 0.2s;
    }}
    .btn:hover {{ background: #0d6460; }}
    .warning {{
      color: #9a8060;
      font-size: 12px;
      line-height: 1.7;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">{STORE_NAME}</div>
    <div class="icon">📚</div>
    <h2>আপনার ইবুক প্রস্তুত!</h2>
    <p class="greeting">{greeting}</p>
    <div class="book-title">📖 {book_title}</div>

    <!-- এই বাটনে click করলে /confirm route-এ যাবে এবং তখনই token "used" mark হবে -->
    <a href="/download/{token}/confirm" class="btn">📥 ইবুক ডাউনলোড করুন</a>

    <p class="warning">
      ⚠️ এই লিংক <strong>একবার মাত্র</strong> কাজ করবে।<br>
      ডাউনলোড শুরু না হলে বাটনে আবার ক্লিক করুন।
    </p>
  </div>
</body>
</html>"""
    return html, 200


@app.route("/download/<token>/confirm")
def download_confirm(token):
    """
    User নিজে বাটনে click করলে এই route-এ আসে।
    Gmail scanner কখনো এখানে আসে না কারণ
    HTML page-এর বাটন scanner click করে না।

    এখানে এসে মানেই real user — তাই এখন
    token "used" mark করো এবং Drive-এ redirect করো।
    """
    t = fb_get(f"tokens/{token}")

    if not isinstance(t, dict):
        return "❌ লিংকটি বৈধ নয়।", 404
    if t.get("used"):
        return "⚠️ এই লিংক আগেই ব্যবহার করা হয়েছে।", 410
    try:
        exp = datetime.fromisoformat(t.get("expires_at", ""))
        if datetime.now(timezone.utc) > exp:
            return "⌛ এই লিংকের মেয়াদ শেষ।", 410
    except:
        pass

    # এখন safely token expire করো
    fb_set(f"tokens/{token}/used", True)
    fb_set(f"tokens/{token}/used_at", datetime.now(timezone.utc).isoformat())

    drive = t.get("drive_link", "")
    if not drive:
        return "❌ ফাইল লিংক নেই।", 404

    # Google Drive download page-এ redirect করো
    return redirect(drive)


@app.route("/resend-link", methods=["POST"])
def resend_link():
    """Admin panel থেকে manually link resend করার জন্য"""
    data = request.get_json(force=True) or {}

    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    email      = (data.get("email")      or "").strip()
    buyer_name = (data.get("buyer_name") or "ক্রেতা").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_title = (data.get("book_title") or "").strip()
    book_id    = (data.get("book_id")    or "").strip()
    order_id   = (data.get("order_id")   or "").strip()

    # ebookLink না থাকলে Firebase থেকে আনার চেষ্টা করো
    if not drive_link and book_id and FIREBASE_URL:
        book_data = fb_get(f"books/{book_id}")
        if isinstance(book_data, dict):
            drive_link = book_data.get("ebookLink", "").strip()
            if not book_title:
                book_title = book_data.get("title", "").strip()

    if not email or not drive_link:
        return jsonify({"success": False, "error": "email বা link নেই"}), 400

    # নতুন token তৈরি করো (আগের টা expire হয়ে গেছে)
    token        = str(uuid.uuid4())
    expires_at   = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{BACKEND_URL}/download/{token}"

    fb_set(f"tokens/{token}", {
        "email":      email,
        "drive_link": drive_link,
        "order_id":   order_id,
        "book_title": book_title,
        "buyer_name": buyer_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "used":       False
    })

    email_sent = send_email_via_gas(email, buyer_name, book_title, download_url)

    return jsonify({
        "success":      True,
        "token":        token,
        "download_url": download_url,
        "email_sent":   email_sent
    })


# ── Local development-এ চালানোর জন্য ──────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))