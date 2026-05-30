import os
import uuid
import time
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, redirect, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

ADMIN_KEY    = os.environ.get("ADMIN_KEY", "boighor2024")
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").rstrip("/")
GMAIL_USER   = os.environ.get("GMAIL_USER", "")
GMAIL_PASS   = os.environ.get("GMAIL_PASS", "")
BACKEND_URL  = os.environ.get("BACKEND_URL", "").rstrip("/")
STORE_NAME   = os.environ.get("STORE_NAME", "boighor")


def fb_get(path):
    if not FIREBASE_URL:
        return None
    try:
        r = requests.get(f"{FIREBASE_URL}/{path}.json", timeout=10)
        return r.json()
    except Exception as e:
        print(f"fb_get error: {e}")
        return None


def fb_set(path, data):
    if not FIREBASE_URL:
        return None
    try:
        r = requests.put(f"{FIREBASE_URL}/{path}.json", json=data, timeout=10)
        return r.json()
    except Exception as e:
        print(f"fb_set error: {e}")
        return None


def fb_update(path, data):
    if not FIREBASE_URL:
        return None
    try:
        r = requests.patch(f"{FIREBASE_URL}/{path}.json", json=data, timeout=10)
        return r.json()
    except Exception as e:
        print(f"fb_update error: {e}")
        return None


def send_email(to_email, buyer_name, book_title, download_url, order_id=""):
    if not GMAIL_USER or not GMAIL_PASS:
        print("Gmail not configured")
        return False
    try:
        html = f"""
        <html><body style="font-family:Arial;background:#f5f0e8;padding:20px">
        <div style="max-width:520px;margin:auto;background:#fff;border-radius:12px;overflow:hidden">
          <div style="background:linear-gradient(135deg,#2c1a08,#8a5210);padding:28px;text-align:center;color:#fff">
            <h1 style="margin:0">Boighor</h1>
          </div>
          <div style="padding:28px;color:#1a1208">
            <p>Dear {buyer_name},</p>
            <div style="background:#fdf6ec;border:1.5px solid #e8dcc8;border-radius:10px;padding:16px;margin:16px 0;text-align:center">
              <div style="font-size:36px">book</div>
              <strong style="color:#8a5210">{book_title}</strong>
              <div style="font-size:11px;color:#9a8060;font-family:monospace">Order: {order_id}</div>
            </div>
            <div style="text-align:center;margin:20px 0">
              <a href="{download_url}" style="background:linear-gradient(135deg,#16a34a,#15803d);color:#fff;
                 padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:bold;font-size:16px">
                Download Ebook
              </a>
            </div>
            <div style="background:#fef9c3;border:1.5px solid #fde047;border-radius:8px;padding:12px;font-size:13px;color:#713f12">
              This link works ONLY ONCE. Download immediately after clicking.
            </div>
            <p style="font-size:12px;color:#9a8060;margin-top:12px">
              Lost the link? Visit: <a href="{BACKEND_URL}/resend">{BACKEND_URL}/resend</a>
            </p>
          </div>
          <div style="background:#2c1a08;color:#c4a77d;text-align:center;padding:14px;font-size:12px">
            This book is for your use only. Sharing is prohibited.
          </div>
        </div>
        </body></html>
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Your Ebook is Ready: {book_title}"
        msg["From"]    = f"Boighor <{GMAIL_USER}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
        print(f"Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


def html_page(title, body_html, show_resend=False, order_hint=""):
    resend_form = ""
    if show_resend:
        resend_form = f"""
        <div style="margin-top:20px;background:#fff;border-radius:10px;padding:18px;box-shadow:0 2px 8px rgba(0,0,0,.08)">
          <p style="font-weight:700;margin:0 0 10px">Get New Link:</p>
          <input type="email" id="re_email" placeholder="Your email"
            style="width:100%;padding:10px;border:1.5px solid #e8dcc8;border-radius:8px;font-size:14px;margin-bottom:8px;box-sizing:border-box"/>
          <input type="text" id="re_order" value="{order_hint}" placeholder="Order ID"
            style="width:100%;padding:10px;border:1.5px solid #e8dcc8;border-radius:8px;font-size:14px;box-sizing:border-box"/>
          <button onclick="resendLink()"
            style="width:100%;margin-top:12px;padding:13px;background:linear-gradient(135deg,#b8741a,#8a5210);
                   color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer">
            Send New Link
          </button>
          <div id="re_msg" style="margin-top:10px;font-size:13px"></div>
        </div>
        <script>
        async function resendLink() {{
          var e=document.getElementById("re_email").value.trim();
          var o=document.getElementById("re_order").value.trim();
          var m=document.getElementById("re_msg");
          if(!e||!o){{m.textContent="Enter email and order ID";m.style.color="#dc2626";return;}}
          m.textContent="Sending...";m.style.color="#9a8060";
          try{{
            var r=await fetch("{BACKEND_URL}/resend-link",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{email:e,order_id:o}})}});
            var d=await r.json();
            if(d.success){{m.textContent="New link sent! Check email.";m.style.color="#16a34a";}}
            else{{m.textContent="Error: "+(d.error||"Problem occurred");m.style.color="#dc2626";}}
          }}catch(err){{m.textContent="Network error";m.style.color="#dc2626";}}
        }}
        </script>
        """
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:Arial,sans-serif;background:#f5f0e8;
min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;color:#1a1208}}
.wrap{{max-width:440px;width:100%}}.top{{background:linear-gradient(135deg,#2c1a08,#8a5210);color:#fff;
padding:18px 22px;border-radius:14px 14px 0 0;text-align:center}}
.card{{background:#fff;border-radius:0 0 14px 14px;padding:24px;box-shadow:0 4px 20px rgba(0,0,0,.10)}}
.icon{{font-size:52px;text-align:center;margin-bottom:14px}}
h3{{font-size:18px;font-weight:700;text-align:center;margin-bottom:10px}}
p{{font-size:14px;color:#4a3520;line-height:1.7}}</style></head>
<body><div class="wrap"><div class="top"><h2>Boighor</h2></div>
<div class="card">{body_html}{resend_form}</div></div></body></html>"""


@app.route("/health")
def health():
    return jsonify({
        "status":   "ok",
        "store":    STORE_NAME,
        "firebase": "connected" if FIREBASE_URL else "not set",
        "email":    "ready" if (GMAIL_USER and GMAIL_PASS) else "not set"
    })


@app.route("/admin/issue-token", methods=["POST"])
def issue_token():
    data = request.get_json(force=True, silent=True) or {}

    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    email      = (data.get("email") or "").strip()
    buyer_name = (data.get("buyer_name") or "Customer").strip()
    order_id   = (data.get("order_id") or "").strip()
    book_id    = (data.get("book_id") or "").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_title = (data.get("book_title") or "").strip()

    if book_id and (not drive_link or not book_title):
        book_data = fb_get(f"books/{book_id}")
        if book_data and isinstance(book_data, dict):
            if not drive_link:
                drive_link = (book_data.get("ebookLink") or "").strip()
            if not book_title:
                book_title = (book_data.get("title") or "Book").strip()

    if not book_title:
        book_title = "Book"

    if not drive_link:
        return jsonify({"success": False, "error": "ebookLink not found"}), 400

    token    = str(uuid.uuid4()).replace("-", "")
    base_url = BACKEND_URL or request.host_url.rstrip("/")
    dl_url   = f"{base_url}/download/{token}"

    fb_set(f"tokens/{token}", {
        "token":     token,
        "bookId":    book_id,
        "bookTitle": book_title,
        "driveLink": drive_link,
        "email":     email,
        "buyerName": buyer_name,
        "orderId":   order_id,
        "createdAt": int(time.time() * 1000),
        "used":      False,
        "expiresAt": int(time.time()) + 7 * 24 * 3600
    })

    email_sent = False
    if email:
        email_sent = send_email(email, buyer_name, book_title, dl_url, order_id)

    return jsonify({
        "success":      True,
        "token":        token,
        "download_url": dl_url,
        "email_sent":   email_sent
    })


@app.route("/download/<token>")
def download_file(token):
    td = fb_get(f"tokens/{token}")

    if not td or not isinstance(td, dict):
        body = '<div class="icon">not found</div><h3>Link Not Found</h3><p>This download link is not valid.</p>'
        return Response(html_page("Not Found", body, show_resend=True),
                        content_type="text/html; charset=utf-8"), 404

    if td.get("used"):
        oid  = td.get("orderId", "")
        body = '<div class="icon">expired</div><h3>Link Already Used</h3><p>This link was used once. Request a new one.</p>'
        return Response(html_page("Used", body, show_resend=True, order_hint=oid),
                        content_type="text/html; charset=utf-8"), 410

    if td.get("expiresAt", 0) < int(time.time()):
        oid  = td.get("orderId", "")
        body = '<div class="icon">expired</div><h3>Link Expired</h3><p>This link expired after 7 days.</p>'
        return Response(html_page("Expired", body, show_resend=True, order_hint=oid),
                        content_type="text/html; charset=utf-8"), 410

    drive_link = (td.get("driveLink") or "").strip()
    if not drive_link:
        body = '<div class="icon">error</div><h3>File Not Found</h3><p>Please contact support.</p>'
        return Response(html_page("Error", body),
                        content_type="text/html; charset=utf-8"), 404

    fb_update(f"tokens/{token}", {
        "used":   True,
        "usedAt": int(time.time() * 1000)
    })
    return redirect(drive_link, code=302)


@app.route("/resend-link", methods=["POST"])
def resend_link():
    data     = request.get_json(force=True, silent=True) or {}
    email    = (data.get("email") or "").strip().lower()
    order_id = (data.get("order_id") or "").strip()

    if not email or not order_id:
        return jsonify({"success": False, "error": "email and order_id required"}), 400

    all_tokens = fb_get("tokens") or {}
    found_key  = None
    found_data = None

    if isinstance(all_tokens, dict):
        for tk, tv in all_tokens.items():
            if isinstance(tv, dict) and tv.get("orderId") == order_id:
                if tv.get("email", "").lower() != email:
                    return jsonify({"success": False, "error": "Email or Order ID mismatch"}), 403
                found_key  = tk
                found_data = tv
                break

    if not found_data:
        return jsonify({"success": False, "error": "Order not found"}), 404

    if found_key:
        fb_update(f"tokens/{found_key}", {"used": True})

    new_token = str(uuid.uuid4()).replace("-", "")
    base_url  = BACKEND_URL or ""
    dl_url    = f"{base_url}/download/{new_token}"

    fb_set(f"tokens/{new_token}", {
        "token":     new_token,
        "bookId":    found_data.get("bookId", ""),
        "bookTitle": found_data.get("bookTitle", "Book"),
        "driveLink": found_data.get("driveLink", ""),
        "email":     email,
        "buyerName": found_data.get("buyerName", "Customer"),
        "orderId":   order_id,
        "createdAt": int(time.time() * 1000),
        "used":      False,
        "expiresAt": int(time.time()) + 7 * 24 * 3600,
        "resent":    True
    })

    email_sent = send_email(
        email,
        found_data.get("buyerName", "Customer"),
        found_data.get("bookTitle", "Book"),
        dl_url, order_id
    )
    if not email_sent:
        return jsonify({"success": False, "error": "Could not send email"}), 500

    return jsonify({"success": True, "message": "New link sent"})


@app.route("/resend")
def resend_page():
    body = '<div class="icon">refresh</div><h3>Get New Download Link</h3><p>Enter your email and order ID to get a new link.</p>'
    return Response(
        html_page("New Link", body, show_resend=True),
        content_type="text/html; charset=utf-8"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
