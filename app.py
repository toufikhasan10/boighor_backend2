import os, uuid, json, requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

FIREBASE_URL   = os.environ.get("FIREBASE_URL", "").rstrip("/")
ADMIN_KEY      = os.environ.get("ADMIN_KEY", "boighor2024")
GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASS     = os.environ.get("GMAIL_PASS", "")
STORE_NAME     = os.environ.get("STORE_NAME", "বইঘর")
BACKEND_URL    = os.environ.get("BACKEND_URL", "")

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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

def send_email(to_email, subject, html_body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.sendmail(GMAIL_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

@app.route("/health")
def health():
    fb_ok = fb_get("ping") is not None or True
    return jsonify({
        "status": "ok",
        "firebase": "✅" if FIREBASE_URL else "❌ not set",
        "email":    "✅" if GMAIL_USER else "❌ not set"
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

    # drive_link না থাকলে book_id দিয়ে Firebase থেকে বের করো
    if not drive_link and book_id and FIREBASE_URL:
        book_data = fb_get(f"books/{book_id}")
        if isinstance(book_data, dict):
            drive_link = book_data.get("ebookLink", "").strip()
            if not book_title:
                book_title = book_data.get("title", "").strip()

    if not email:
        return jsonify({"success": False, "error": "email required"}), 400
    if not drive_link:
        return jsonify({"success": False, "error": "ebookLink not found — book_id টা সঠিক কিনা দেখুন, অথবা admin panel-এ বইয়ের ebookLink দিন"}), 400

    token        = str(uuid.uuid4())
    expires_at   = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{BACKEND_URL}/download/{token}"

    fb_set(f"tokens/{token}", {
        "email":       email,
        "drive_link":  drive_link,
        "order_id":    order_id,
        "book_title":  book_title,
        "buyer_name":  buyer_name,
        "created_at":  datetime.now(timezone.utc).isoformat(),
        "expires_at":  expires_at,
        "used":        False
    })

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#fffdf7;border:1px solid #e8dcc8;border-radius:12px;overflow:hidden">
      <div style="background:linear-gradient(135deg,#2c1a08,#8a5210);padding:24px;text-align:center">
        <h1 style="color:#e8a84c;margin:0;font-size:22px">{STORE_NAME}</h1>
        <p style="color:#f5e6cc;margin:6px 0 0;font-size:14px">আপনার ইবুক প্রস্তুত!</p>
      </div>
      <div style="padding:24px">
        <p style="font-size:15px;color:#1a1208">প্রিয় <strong>{buyer_name}</strong>,</p>
        <p style="color:#4a3520;font-size:14px">আপনার অর্ডার নিশ্চিত হয়েছে। নিচের বাটনে ক্লিক করে ইবুকটি ডাউনলোড করুন।</p>
        {f'<p style="color:#4a3520;font-size:13px">📖 বই: <strong>{book_title}</strong></p>' if book_title else ''}
        <div style="text-align:center;margin:24px 0">
          <a href="{download_url}" style="background:#0f766e;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-size:15px;font-weight:700;display:inline-block">📥 ইবুক ডাউনলোড করুন</a>
        </div>
        <p style="color:#9a8060;font-size:12px;text-align:center">⚠️ এই লিংক একবার মাত্র কাজ করবে এবং ৪৮ ঘণ্টা পরে মেয়াদ শেষ হবে।</p>
      </div>
      <div style="background:#f5f0e8;padding:12px;text-align:center;font-size:11px;color:#9a8060">
        {STORE_NAME} — ধন্যবাদ আপনার বিশ্বাসের জন্য
      </div>
    </div>
    """

    email_sent = send_email(email, f"📚 আপনার ইবুক ডাউনলোড লিংক — {book_title or STORE_NAME}", html)

    return jsonify({
        "success":      True,
        "token":        token,
        "download_url": download_url,
        "email_sent":   email_sent
    })

@app.route("/download/<token>")
def download(token):
    t = fb_get(f"tokens/{token}")
    if not isinstance(t, dict):
        return "❌ লিংকটি বৈধ নয়।", 404
    if t.get("used"):
        return "⚠️ এই লিংক আগেই ব্যবহার করা হয়েছে।", 410
    expires = t.get("expires_at", "")
    if expires:
        try:
            exp = datetime.fromisoformat(expires)
            if datetime.now(timezone.utc) > exp:
                return "⌛ এই লিংকের মেয়াদ শেষ হয়ে গেছে।", 410
        except:
            pass
    fb_set(f"tokens/{token}/used", True)
    fb_set(f"tokens/{token}/used_at", datetime.now(timezone.utc).isoformat())
    drive = t.get("drive_link", "")
    if not drive:
        return "❌ ফাইল লিংক পাওয়া যায়নি।", 404
    return redirect(drive)

@app.route("/resend-link", methods=["POST"])
def resend_link():
    data       = request.get_json(force=True) or {}
    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    order_id   = (data.get("order_id") or "").strip()
    email      = (data.get("email") or "").strip()
    buyer_name = (data.get("buyer_name") or "ক্রেতা").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_title = (data.get("book_title") or "").strip()
    book_id    = (data.get("book_id") or "").strip()

    if not drive_link and book_id and FIREBASE_URL:
        book_data = fb_get(f"books/{book_id}")
        if isinstance(book_data, dict):
            drive_link = book_data.get("ebookLink", "").strip()
            if not book_title:
                book_title = book_data.get("title", "").strip()

    if not email or not drive_link:
        return jsonify({"success": False, "error": "email বা drive_link নেই"}), 400

    token        = str(uuid.uuid4())
    expires_at   = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{BACKEND_URL}/download/{token}"

    fb_set(f"tokens/{token}", {
        "email": email, "drive_link": drive_link, "order_id": order_id,
        "book_title": book_title, "buyer_name": buyer_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at, "used": False
    })

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto">
      <h2 style="color:#0f766e">📚 নতুন ডাউনলোড লিংক</h2>
      <p>প্রিয় {buyer_name}, আপনার নতুন ডাউনলোড লিংক নিচে দেওয়া হলো:</p>
      {f'<p>📖 বই: <strong>{book_title}</strong></p>' if book_title else ''}
      <a href="{download_url}" style="background:#0f766e;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:700;display:inline-block;margin:16px 0">📥 ডাউনলোড করুন</a>
      <p style="color:#9a8060;font-size:12px">লিংকটি একবার মাত্র কাজ করবে এবং ৪৮ ঘণ্টায় মেয়াদ শেষ।</p>
    </div>
    """
    email_sent = send_email(email, f"📚 নতুন ডাউনলোড লিংক — {STORE_NAME}", html)
    return jsonify({"success": True, "token": token, "download_url": download_url, "email_sent": email_sent})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))