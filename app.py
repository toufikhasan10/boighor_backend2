import os, uuid, smtplib, requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, redirect, Response
from datetime import datetime

app = Flask(__name__)

FIREBASE_URL    = os.environ.get("FIREBASE_URL", "").rstrip("/")
FIREBASE_SECRET = os.environ.get("FIREBASE_SECRET", "")
GMAIL_USER      = os.environ.get("GMAIL_USER", "")
GMAIL_PASS      = os.environ.get("GMAIL_PASS", "")
BACKEND_URL     = os.environ.get("BACKEND_URL", "").rstrip("/")
ADMIN_KEY       = os.environ.get("ADMIN_KEY", "changeme")
STORE_NAME      = os.environ.get("STORE_NAME", "\u09ac\u0987\u0998\u09b0")

def fb_get(path):
    r = requests.get(f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}", timeout=10)
    r.raise_for_status(); return r.json()

def fb_set(path, data):
    r = requests.put(f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}", json=data, timeout=10)
    r.raise_for_status(); return r.json()

def fb_update(path, data):
    r = requests.patch(f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}", json=data, timeout=10)
    r.raise_for_status(); return r.json()

def send_email(to, name, title, url, oid):
    html = f"""<html><body style="font-family:Arial;background:#f5f0e8;padding:20px">
    <div style="max-width:500px;margin:auto;background:#fff;border-radius:12px;overflow:hidden">
    <div style="background:linear-gradient(135deg,#2c1a08,#8a5210);padding:24px;text-align:center;color:#fff">
    <h1 style="margin:0">\U0001f4da {STORE_NAME}</h1></div>
    <div style="padding:24px">
    <p>\u09aa\u09cd\u09b0\u09bf\u09af\u09bc <b>{name}</b>,</p>
    <div style="background:#fdf6ec;border:1.5px solid #e8dcc8;border-radius:10px;padding:16px;margin:16px 0;text-align:center">
    <div style="font-size:36px">\U0001f4d6</div>
    <b style="color:#8a5210;font-size:16px">{title}</b>
    <div style="font-size:11px;color:#9a8060;font-family:monospace">Order: {oid}</div></div>
    <a href="{url}" style="display:block;background:linear-gradient(135deg,#16a34a,#15803d);color:#fff;text-decoration:none;text-align:center;padding:14px;border-radius:10px;font-weight:700;font-size:15px">\U0001f4e5 \u0987\u09ac\u09c1\u0995 \u09a1\u09be\u0989\u09a8\u09b2\u09cb\u09a1 \u0995\u09b0\u09c1\u09a8</a>
    <div style="background:#fef9c3;border:1.5px solid #fde047;border-radius:8px;padding:12px;margin-top:14px;font-size:13px;color:#713f12">
    \u26a0\ufe0f \u098f\u0987 \u09b2\u09bf\u0982\u0995 <b>\u09ae\u09be\u09a4\u09cd\u09b0 \u09e7 \u09ac\u09be\u09b0</b> \u0995\u09be\u099c \u0995\u09b0\u09ac\u09c7\u0964</div>
    <p style="font-size:11px;color:#9a8060;margin-top:12px">\u09b2\u09bf\u0982\u0995 \u09b9\u09be\u09b0\u09be\u09b2\u09c7: <a href="{BACKEND_URL}/resend">{BACKEND_URL}/resend</a></p>
    </div>
    <div style="background:#2c1a08;color:#c4a77d;text-align:center;padding:14px;font-size:12px">
    \U0001f512 \u098f\u0987 \u09ac\u0987 \u09b6\u09c1\u09a7\u09c1 \u0986\u09aa\u09a8\u09be\u09b0 \u099c\u09a8\u09cd\u09af \u2014 \u09b6\u09c7\u09af\u09bc\u09be\u09b0 \u09a8\u09bf\u09b7\u09bf\u09a6\u09cd\u09a7</div>
    </div></body></html>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"\U0001f4da \u0986\u09aa\u09a8\u09be\u09b0 \u0987\u09ac\u09c1\u0995 \u09aa\u09cd\u09b0\u09b8\u09cd\u09a4\u09c1\u09a4 \u2014 {title}"
    msg["From"] = f"{STORE_NAME} <{GMAIL_USER}>"
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS); s.sendmail(GMAIL_USER, to, msg.as_string())

def html_page(title, body, resend=False, oid=""):
    form = ""
    if resend:
        form = f"""<div style="margin-top:16px;background:#fff;border-radius:10px;padding:16px;box-shadow:0 2px 8px rgba(0,0,0,.1)">
<p style="font-weight:700;margin-bottom:10px">\U0001f501 \u09a8\u09a4\u09c1\u09a8 \u09b2\u09bf\u0982\u0995:</p>
<input type="email" id="re_email" placeholder="\u0986\u09aa\u09a8\u09be\u09b0 \u0987\u09ae\u09c7\u0987\u09b2" style="width:100%;padding:9px;border:1.5px solid #e8dcc8;border-radius:8px;font-size:14px;margin-bottom:8px"/>
<input type="text" id="re_order" value="{oid}" placeholder="Order ID" style="width:100%;padding:9px;border:1.5px solid #e8dcc8;border-radius:8px;font-size:14px"/>
<button onclick="resendLink()" style="width:100%;margin-top:10px;padding:12px;background:linear-gradient(135deg,#b8741a,#8a5210);color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer">\U0001f4e8 \u09a8\u09a4\u09c1\u09a8 \u09b2\u09bf\u0982\u0995 \u09aa\u09be\u09a0\u09be\u09a8</button>
<div id="re_msg" style="margin-top:10px;font-size:13px"></div></div>
<script>async function resendLink(){{const e=document.getElementById("re_email").value.trim(),o=document.getElementById("re_order").value.trim(),m=document.getElementById("re_msg");if(!e||!o){{m.textContent="\u0987\u09ae\u09c7\u0987\u09b2 \u0993 \u0985\u09b0\u09cd\u09a1\u09be\u09b0 \u0986\u0987\u09a1\u09bf \u09a6\u09bf\u09a8";m.style.color="#dc2626";return;}}m.textContent="\u09aa\u09be\u09a0\u09be\u09a8\u09cb \u09b9\u099a\u09cd\u099b\u09c7...";m.style.color="#9a8060";try{{const r=await fetch("{BACKEND_URL}/resend-link",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{email:e,order_id:o}})}});const d=await r.json();if(d.success){{m.textContent="\u2705 \u09a8\u09a4\u09c1\u09a8 \u09b2\u09bf\u0982\u0995 \u09aa\u09be\u09a0\u09be\u09a8\u09cb \u09b9\u09af\u09bc\u09c7\u099b\u09c7!";m.style.color="#16a34a";}}else{{m.textContent="\u274c "+(d.error||"\u09b8\u09ae\u09b8\u09cd\u09af\u09be");m.style.color="#dc2626";}}}}catch(err){{m.textContent="\u274c \u09a8\u09c7\u099f\u0993\u09af\u09bc\u09be\u09b0\u09cd\u0995 \u09b8\u09ae\u09b8\u09cd\u09af\u09be";m.style.color="#dc2626";}}}}</script>"""
    return f"""<!DOCTYPE html><html lang="bn"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title><link href="https://fonts.googleapis.com/css2?family=Hind+Siliguri:wght@400;600;700&display=swap" rel="stylesheet"/>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:"Hind Siliguri",sans-serif;background:#f5f0e8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;color:#1a1208}}
.wrap{{max-width:440px;width:100%}}.top{{background:linear-gradient(135deg,#2c1a08,#8a5210);color:#fff;padding:18px 22px;border-radius:14px 14px 0 0;text-align:center}}
.card{{background:#fff;border-radius:0 0 14px 14px;padding:22px;box-shadow:0 4px 20px rgba(0,0,0,.10)}}.icon{{font-size:48px;text-align:center;margin-bottom:12px}}
h3{{font-size:17px;font-weight:700;margin-bottom:8px;text-align:center}}p{{font-size:14px;color:#4a3520;line-height:1.7}}</style></head>
<body><div class="wrap"><div class="top"><h2>\U0001f4da {STORE_NAME}</h2></div>
<div class="card">{body}{form}</div></div></body></html>"""

@app.route("/health")
def health(): return jsonify({"status":"ok","store":STORE_NAME})

@app.route("/admin/issue-token", methods=["POST"])
def issue_token():
    d = request.get_json(force=True, silent=True) or {}
    if d.get("admin_key") != ADMIN_KEY: return jsonify({"success":False,"error":"Unauthorized"}), 401
    email = (d.get("email") or "").strip()
    name = (d.get("buyer_name") or "\u0995\u09cd\u09b0\u09c7\u09a4\u09be").strip()
    book_id = (d.get("book_id") or "").strip()
    order_id = (d.get("order_id") or "").strip()
    if not all([email, book_id, order_id]): return jsonify({"success":False,"error":"email, book_id, order_id required"}), 400
    try: book = fb_get(f"books/{book_id}")
    except Exception as e: return jsonify({"success":False,"error":str(e)}), 500
    if not book: return jsonify({"success":False,"error":"Book not found"}), 404
    link = book.get("ebookLink") or book.get("ebook_link") or ""
    if not link: return jsonify({"success":False,"error":"ebookLink not set"}), 400
    title = book.get("title") or book_id
    token = str(uuid.uuid4())
    dl_url = f"{BACKEND_URL}/download/{token}"
    try: fb_set(f"tokens/{token}", {"email":email,"buyer_name":name,"drive_link":link,"book_title":title,"book_id":book_id,"order_id":order_id,"used":False,"revoked":False,"created_at":datetime.utcnow().isoformat()})
    except Exception as e: return jsonify({"success":False,"error":str(e)}), 500
    sent = False; err = ""
    try: send_email(email, name, title, dl_url, order_id); sent = True
    except Exception as e: err = str(e)
    return jsonify({"success":True,"token":token,"download_url":dl_url,"email_sent":sent,"email_error":err})

@app.route("/download/<token>")
def download(token):
    try: t = fb_get(f"tokens/{token}")
    except: return Response(html_page("Error","<div style=\"text-align:center;padding:20px\">\u09b8\u09be\u09b0\u09cd\u09ad\u09be\u09b0 \u09b8\u09ae\u09b8\u09cd\u09af\u09be</div>"), content_type="text/html;charset=utf-8"), 500
    if not t: return Response(html_page("\u09b2\u09bf\u0982\u0995 \u09a8\u09c7\u0987","<div class=\"icon\">\U0001f50d</div><h3>\u09b2\u09bf\u0982\u0995\u099f\u09bf \u09aa\u09be\u0993\u09af\u09bc\u09be \u09af\u09be\u09af\u09bc\u09a8\u09bf</h3>",resend=True), content_type="text/html;charset=utf-8"), 404
    oid = t.get("order_id","")
    if t.get("used") or t.get("revoked"): return Response(html_page("\u09b2\u09bf\u0982\u0995 \u09b6\u09c7\u09b7","<div class=\"icon\">\u23f0</div><h3>\u09b2\u09bf\u0982\u0995\u099f\u09bf \u09b6\u09c7\u09b7 \u09b9\u09af\u09bc\u09c7 \u0997\u09c7\u099b\u09c7</h3><p>\u098f\u0987 \u09b2\u09bf\u0982\u0995 \u0986\u0997\u09c7 \u09ac\u09cd\u09af\u09ac\u09b9\u09be\u09b0 \u09b9\u09af\u09bc\u09c7\u099b\u09c7\u0964 \u09a8\u09a4\u09c1\u09a8 \u09b2\u09bf\u0982\u0995 \u09a8\u09bf\u09a8\u0964</p>",resend=True,oid=oid), content_type="text/html;charset=utf-8"), 410
    try: fb_update(f"tokens/{token}", {"used":True,"used_at":datetime.utcnow().isoformat()})
    except: pass
    return redirect(t.get("drive_link",""), code=302)

@app.route("/resend-link", methods=["POST"])
def resend_link():
    d = request.get_json(force=True, silent=True) or {}
    email = (d.get("email") or "").strip().lower()
    oid = (d.get("order_id") or "").strip()
    if not email or not oid: return jsonify({"success":False,"error":"email \u0993 order_id \u09a6\u09bf\u09a8"}), 400
    try: tokens = fb_get("tokens") or {}
    except Exception as e: return jsonify({"success":False,"error":str(e)}), 500
    fk = fd = None
    for k,v in tokens.items():
        if isinstance(v,dict) and v.get("order_id")==oid:
            if v.get("email","").lower()!=email: return jsonify({"success":False,"error":"\u0987\u09ae\u09c7\u0987\u09b2 \u09ac\u09be \u0985\u09b0\u09cd\u09a1\u09be\u09b0 \u0986\u0987\u09a1\u09bf \u09ae\u09bf\u09b2\u099b\u09c7 \u09a8\u09be"}), 403
            fk=k; fd=v; break
    if not fd: return jsonify({"success":False,"error":"\u0985\u09b0\u09cd\u09a1\u09be\u09b0 \u09aa\u09be\u0993\u09af\u09bc\u09be \u09af\u09be\u09af\u09bc\u09a8\u09bf"}), 404
    try: fb_update(f"tokens/{fk}", {"revoked":True})
    except: pass
    nt = str(uuid.uuid4())
    nu = f"{BACKEND_URL}/download/{nt}"
    try: fb_set(f"tokens/{nt}", {"email":email,"buyer_name":fd.get("buyer_name",""),"drive_link":fd.get("drive_link",""),"book_title":fd.get("book_title",""),"book_id":fd.get("book_id",""),"order_id":oid,"used":False,"revoked":False,"resent":True,"created_at":datetime.utcnow().isoformat()})
    except Exception as e: return jsonify({"success":False,"error":str(e)}), 500
    try: send_email(email, fd.get("buyer_name",""), fd.get("book_title",""), nu, oid)
    except Exception as e: return jsonify({"success":False,"error":str(e)}), 500
    return jsonify({"success":True})

@app.route("/resend")
def resend_page():
    b = "<div class=\"icon\">\U0001f501</div><h3>\u09a8\u09a4\u09c1\u09a8 \u09a1\u09be\u0989\u09a8\u09b2\u09cb\u09a1 \u09b2\u09bf\u0982\u0995</h3><p>\u0987\u09ae\u09c7\u0987\u09b2 \u0993 \u0985\u09b0\u09cd\u09a1\u09be\u09b0 \u0986\u0987\u09a1\u09bf \u09a6\u09bf\u09a8\u0964</p>"
    return Response(html_page("\u09a8\u09a4\u09c1\u09a8 \u09b2\u09bf\u0982\u0995", b, resend=True), content_type="text/html;charset=utf-8")

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
