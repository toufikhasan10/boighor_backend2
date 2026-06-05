import os, uuid, urllib.parse, time
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


# ══════════════════════════════════════════════════════════════
# Firebase Helpers — retry logic সহ
# timeout=8 ছিল — Railway incident-এর সময়ে এটা যথেষ্ট নয়।
# এখন 12 সেকেন্ড + 2 বার retry করবে।
# ══════════════════════════════════════════════════════════════

def fb_get(path, max_retries=2):
    """
    Returns:
      - dict/value → সফলভাবে data পাওয়া গেছে
      - None        → Firebase চালু আছে কিন্তু path-এ কিছু নেই (JSON null)
      - False       → Firebase-এ connect করা যায়নি (timeout/error)
    """
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(f"{FIREBASE_URL}/{path}.json", timeout=12)
            if r.ok:
                return r.json()  # None হলে মানে path-এ data নেই
            return False
        except Exception as e:
            print(f"❌ fb_get attempt {attempt+1}/{max_retries+1} error ({path}): {e}")
            if attempt < max_retries:
                time.sleep(1.5)  # retry-র আগে একটু অপেক্ষা
    return False  # সব retry শেষ — Firebase পাওয়া যাচ্ছে না


def fb_set(path, data, max_retries=2):
    """
    Returns True on success, False on all retries exhausted.
    """
    for attempt in range(max_retries + 1):
        try:
            r = requests.put(f"{FIREBASE_URL}/{path}.json", json=data, timeout=12)
            if r.ok:
                return True
            print(f"❌ fb_set HTTP {r.status_code} on attempt {attempt+1} ({path})")
        except Exception as e:
            print(f"❌ fb_set attempt {attempt+1}/{max_retries+1} error ({path}): {e}")
        if attempt < max_retries:
            time.sleep(1.5)
    return False


# ══════════════════════════════════════════════════════════════
# Email — Google Apps Script দিয়ে (SMTP নয়)
# ══════════════════════════════════════════════════════════════

def send_email_via_gas(to_email, buyer_name, book_title, download_url):
    if not GAS_URL:
        print("❌ GAS_URL set নেই — email skip")
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
    # Firebase connection টেস্ট করো
    fb_ok = fb_get("ping") is not False
    return jsonify({
        "status":   "ok",
        "firebase": "✅" if fb_ok else "⚠️ timeout (try again)",
        "email":    "✅ GAS" if GAS_URL else "⚠️ GAS_URL not set"
    })


@app.route("/admin/issue-token", methods=["POST"])
def issue_token():
    data = request.get_json(force=True) or {}

    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    email      = (data.get("email")      or "").strip()
    buyer_name = (data.get("buyer_name") or "ক্রেতা").strip()
    order_id   = (data.get("order_id")   or "").strip()
    book_title = (data.get("book_title") or "").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_id    = (data.get("book_id")    or "").strip()

    # drive_link না থাকলে Firebase থেকে ebookLink আনো
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

    # ── CRITICAL: আগে Firebase-এ save করো ──────────────────────
    # save না হলে email পাঠানো হবে না।
    # কারণ: email গেলে ক্রেতা link-এ click করবে,
    # কিন্তু Firebase-এ token না থাকলে "বৈধ নয়" দেখাবে।
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

    if not saved:
        # Firebase-এ save হয়নি — email পাঠালে ক্রেতা ভাঙা লিংক পাবে।
        # Admin-কে জানাও যাতে সে আবার চেষ্টা করতে পারে।
        print("❌ Firebase save failed — email NOT sent to avoid broken link")
        return jsonify({
            "success": False,
            "error":   "Firebase সাময়িকভাবে অনুপলব্ধ। ৩০ সেকেন্ড পরে আবার 'ডেলিভার' চাপুন।",
            "retry":   True
        }), 503

    # Firebase-এ save হয়েছে — এখন email পাঠানো নিরাপদ
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
    Gmail scanner এখানে আসে এবং HTML page দেখে।
    Scanner বাটনে click করে না, তাই token expire হয় না।
    Real user বাটনে click করলে /confirm-এ যায়।
    """
    t = fb_get(f"tokens/{token}")

    # Firebase unavailable (timeout/error)
    if t is False:
        return """<!DOCTYPE html>
<html lang="bn">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>সাময়িক সমস্যা</title>
<style>body{font-family:Arial,sans-serif;text-align:center;padding:40px;background:#faf8f3}
.card{background:white;border-radius:16px;padding:40px;max-width:400px;margin:0 auto;box-shadow:0 4px 20px rgba(0,0,0,.1)}
h2{color:#b8741a}p{color:#4a3520;font-size:14px;line-height:1.7}
.btn{display:inline-block;background:#b8741a;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;margin-top:16px;font-weight:700}
</style></head>
<body><div class="card">
<h2>⌛ সাময়িক সমস্যা</h2>
<p>সার্ভার এই মুহূর্তে ব্যস্ত। আপনার লিংক সক্রিয় আছে।<br><br>পেইজটি <strong>Refresh</strong> করুন অথবা ১-২ মিনিট পরে আবার চেষ্টা করুন।</p>
<a href="" class="btn" onclick="location.reload();return false">🔄 Refresh করুন</a>
</div></body></html>""", 503

    # Token Firebase-এ নেই (None = JSON null)
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
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:Arial,sans-serif;background:#faf8f3;min-height:100vh;
         display:flex;justify-content:center;align-items:center;padding:20px}}
    .card{{background:white;border-radius:16px;padding:40px 32px;text-align:center;
           box-shadow:0 4px 24px rgba(0,0,0,.1);max-width:420px;width:100%}}
    .logo{{background:linear-gradient(135deg,#2c1a08,#8a5210);color:#e8a84c;
           font-size:22px;font-weight:700;padding:16px;border-radius:10px;
           margin-bottom:24px}}
    .icon{{font-size:52px;margin-bottom:14px}}
    h2{{color:#2c1a08;font-size:18px;margin-bottom:10px}}
    .greeting{{color:#4a3520;font-size:14px;margin-bottom:20px}}
    .book-title{{background:#f5f0e8;border:1px solid #e8dcc8;border-radius:8px;
                 padding:12px 16px;color:#2c1a08;font-size:15px;font-weight:600;
                 margin-bottom:28px}}
    .btn{{display:inline-block;background:#0f766e;color:white;padding:16px 36px;
          border-radius:10px;text-decoration:none;font-size:16px;font-weight:700;
          margin-bottom:20px}}
    .btn:hover{{background:#0d6460}}
    .warning{{color:#9a8060;font-size:12px;line-height:1.7}}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">{STORE_NAME}</div>
    <div class="icon">📚</div>
    <h2>আপনার ইবুক প্রস্তুত!</h2>
    <p class="greeting">{greeting}</p>
    <div class="book-title">📖 {book_title}</div>
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
    Real user বাটনে click করলে এখানে আসে।
    Gmail scanner এখানে কখনো আসে না।
    """
    t = fb_get(f"tokens/{token}")

    if t is False:
        return "⌛ সার্ভার সাময়িকভাবে ব্যস্ত। ব্রাউজারে Back চেপে আবার ডাউনলোড বাটনে ক্লিক করুন।", 503
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

    # Token expire করো এবং redirect করো
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

    email      = (data.get("email")      or "").strip()
    buyer_name = (data.get("buyer_name") or "ক্রেতা").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_title = (data.get("book_title") or "").strip()
    book_id    = (data.get("book_id")    or "").strip()
    order_id   = (data.get("order_id")   or "").strip()

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

    saved = fb_set(f"tokens/{token}", {
        "email": email, "drive_link": drive_link, "order_id": order_id,
        "book_title": book_title, "buyer_name": buyer_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at, "used": False
    })

    if not saved:
        return jsonify({
            "success": False,
            "error": "Firebase সাময়িকভাবে অনুপলব্ধ। একটু পরে আবার চেষ্টা করুন।",
            "retry": True
        }), 503

    email_sent = send_email_via_gas(email, buyer_name, book_title, download_url)
    return jsonify({
        "success": True, "token": token,
        "download_url": download_url, "email_sent": email_sent
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))