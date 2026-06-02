import os, uuid, urllib.parse
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

FIREBASE_URL = os.environ.get("FIREBASE_URL", "").rstrip("/")
ADMIN_KEY    = os.environ.get("ADMIN_KEY", "boighor2024")
STORE_NAME   = os.environ.get("STORE_NAME", "বইঘর")
BACKEND_URL  = os.environ.get("BACKEND_URL", "")
GAS_URL      = os.environ.get("GAS_URL", "")

def fb_get(path):
    try:
        r = requests.get(f"{FIREBASE_URL}/{path}.json", timeout=8)
        return r.json() if r.ok else None
    except:
        return None

def fb_set(path, data):
    try:
        r = requests.put(f"{FIREBASE_URL}/{path}.json", json=data, timeout=8)
        return r.ok
    except:
        return False

def send_email_via_gas(to_email, buyer_name, book_title, download_url):
    if not GAS_URL:
        print("❌ GAS_URL set নেই")
        return False
    try:
        # GET এর বদলে POST মেথডে JSON ডাটা পাঠানো হচ্ছে (সবচেয়ে নিরাপদ পদ্ধতি)
        payload = {
            "action": "sendEmail",
            "to":     to_email,
            "name":   buyer_name,
            "book":   book_title,
            "link":   download_url,
            "store":  STORE_NAME
        }
        # timeout 20 সেকেন্ড রাখা হলো যেন GAS রেসপন্স করতে সময় পায়
        r = requests.post(GAS_URL, json=payload, timeout=20, allow_redirects=True)
        print(f"✅ GAS response ({r.status_code}): {r.text[:200]}")
        return r.status_code == 200 and "error" not in r.text.lower()
    except Exception as e:
        print(f"❌ GAS email error: {e}")
        return False

@app.route("/health")
def health():
    return jsonify({
        "status":   "ok",
        "firebase": "✅" if FIREBASE_URL else "❌ not set",
        "email":    "✅ GAS POST" if GAS_URL else "⚠️ GAS_URL not set"
    })

@app.route("/admin/issue-token", methods=["POST"])
def issue_token():
    data = request.get_json(force=True) or {}
    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    email      = (data.get("email") or "").strip()
    buyer_name = (data.get("buyer_name") or "ক্রেতা").strip()
    order_id   = (data.get("order_id") or "").strip()
    book_title = (data.get("book_title") or "").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_id    = (data.get("book_id") or "").strip()

    if not drive_link and book_id and FIREBASE_URL:
        book_data = fb_get(f"books/{book_id}")
        if isinstance(book_data, dict):
            drive_link = book_data.get("ebookLink", "").strip()
            if not book_title:
                book_title = book_data.get("title", "").strip()
        print(f"Firebase lookup → book_id={book_id}, link={drive_link[:60] if drive_link else 'EMPTY'}")

    if not email:
        return jsonify({"success": False, "error": "email required"}), 400
    if not drive_link:
        return jsonify({"success": False, "error": "ebookLink not found — বইয়ের ebookLink field দিন"}), 400

    token        = str(uuid.uuid4())
    expires_at   = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{BACKEND_URL}/download/{token}"

    saved = fb_set(f"tokens/{token}", {
        "email": email, "drive_link": drive_link, "order_id": order_id,
        "book_title": book_title, "buyer_name": buyer_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at, "used": False
    })
    print(f"Token saved: {saved} | token={token[:8]}...")

    email_sent = send_email_via_gas(email, buyer_name, book_title, download_url)

    return jsonify({
        "success": True, "token": token,
        "download_url": download_url, "email_sent": email_sent
    })

@app.route("/download/<token>")
def download(token):
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
    fb_set(f"tokens/{token}/used", True)
    fb_set(f"tokens/{token}/used_at", datetime.now(timezone.utc).isoformat())
    drive = t.get("drive_link", "")
    if not drive:
        return "❌ ফাইল লিংক নেই।", 404
    return redirect(drive)

@app.route("/resend-link", methods=["POST"])
def resend_link():
    data = request.get_json(force=True) or {}
    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    email      = (data.get("email") or "").strip()
    buyer_name = (data.get("buyer_name") or "ক্রেতা").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_title = (data.get("book_title") or "").strip()
    book_id    = (data.get("book_id") or "").strip()
    order_id   = (data.get("order_id") or "").strip()

    if not drive_link and book_id and FIREBASE_URL:
        book_data = fb_get(f"books/{book_id}")
        if isinstance(book_data, dict):
            drive_link = book_data.get("ebookLink", "").strip()
            if not book_title:
                book_title = book_data.get("title", "").strip()

    if not email or not drive_link:
        return jsonify({"success": False, "error": "email বা link নেই"}), 400

    token        = str(uuid.uuid4())
    expires_at   = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{BACKEND_URL}/download/{token}"

    fb_set(f"tokens/{token}", {
        "email": email, "drive_link": drive_link, "order_id": order_id,
        "book_title": book_title, "buyer_name": buyer_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at, "used": False
    })
    email_sent = send_email_via_gas(email, buyer_name, book_title, download_url)
    return jsonify({"success": True, "token": token,
                    "download_url": download_url, "email_sent": email_sent})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
