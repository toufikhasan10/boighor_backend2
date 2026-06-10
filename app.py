import os
import re
import html
import uuid
import random
import urllib.parse
import time
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, request, jsonify, redirect, Response, render_template_string, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ══════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").rstrip("/")
ADMIN_KEY    = os.environ.get("ADMIN_KEY", "boighor2024")
STORE_NAME   = os.environ.get("STORE_NAME", "বইঘর")
BACKEND_URL  = os.environ.get("BACKEND_URL", "").rstrip("/")
GAS_URL      = os.environ.get("GAS_URL", "")

# ══════════════════════════════════════════════════════════════
# Firebase Helpers
# ══════════════════════════════════════════════════════════════

def fb_get(path, max_retries=2):
    if not FIREBASE_URL:
        return False
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(f"{FIREBASE_URL}/{path}.json", timeout=12)
            if r.ok:
                return r.json()
            return False
        except Exception:
            if attempt < max_retries:
                time.sleep(1.5)
    return False


def fb_set(path, data, max_retries=2):
    if not FIREBASE_URL:
        return False
    for attempt in range(max_retries + 1):
        try:
            r = requests.put(f"{FIREBASE_URL}/{path}.json", json=data, timeout=12)
            if r.ok:
                return True
        except Exception:
            if attempt < max_retries:
                time.sleep(1.5)
    return False


def fb_delete(path, max_retries=2):
    if not FIREBASE_URL:
        return False
    for attempt in range(max_retries + 1):
        try:
            r = requests.delete(f"{FIREBASE_URL}/{path}.json", timeout=12)
            if r.ok:
                return True
        except Exception:
            if attempt < max_retries:
                time.sleep(1.5)
    return False

# ══════════════════════════════════════════════════════════════
# Drive Helpers
# ══════════════════════════════════════════════════════════════

def extract_drive_file_id(url):
    if not url or "drive.google.com" not in url:
        return None
    try:
        m = re.search(r"/file/d/([^/]+)", url)
        if m:
            return m.group(1)
        parsed = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parsed.query)
        if q.get("id"):
            return q["id"][0]
    except Exception:
        pass
    return None


def open_drive_file_response(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    file_id = extract_drive_file_id(url)

    if not file_id:
        return requests.get(url, stream=True, timeout=30, headers=headers, allow_redirects=True)

    session = requests.Session()
    base    = "https://drive.google.com/uc"
    params  = {"export": "download", "id": file_id}
    first   = session.get(base, params=params, timeout=30, headers=headers, allow_redirects=True)

    confirm_token = None
    for key, value in first.cookies.items():
        if key.startswith("download_warning"):
            confirm_token = value
            break

    if confirm_token:
        return session.get(
            base,
            params={"export": "download", "id": file_id, "confirm": confirm_token},
            stream=True, timeout=30, headers=headers, allow_redirects=True,
        )

    content_type = (first.headers.get("Content-Type") or "").lower()
    first_bytes  = first.content[:64]
    if first.ok and (
        first_bytes.startswith(b"PK") or first_bytes.startswith(b"%PDF") or
        "application/epub" in content_type or
        "application/octet-stream" in content_type or
        "application/pdf" in content_type
    ):
        class MemoryResponse:
            status_code = first.status_code
            ok           = first.ok
            headers      = first.headers
            content      = first.content
            def iter_content(self, chunk_size=8192):
                for i in range(0, len(self.content), chunk_size):
                    yield self.content[i:i + chunk_size]
        return MemoryResponse()

    text = first.text or ""
    m = re.search(r'href="([^"]*?/uc\?export=download[^"]+)"', text)
    if m:
        href = html.unescape(m.group(1))
        if href.startswith("/"):
            href = "https://drive.google.com" + href
        return session.get(href, stream=True, timeout=30, headers=headers, allow_redirects=True)

    return first

# ══════════════════════════════════════════════════════════════
# Purchase / Restore Helpers
# ══════════════════════════════════════════════════════════════

def safe_email(email):
    return (email.lower().strip()
            .replace("@", "_at_")
            .replace(".", "_dot_")
            .replace("+", "_plus_")
            .replace("-", "_dash_"))


def save_purchase_record(email, book_title, drive_link, buyer_name, book_id=""):
    se  = safe_email(email)
    key = uuid.uuid4().hex[:12]
    fb_set(f"purchases/{se}/{key}", {
        "book_title":    book_title or "ইবুক",
        "drive_link":    drive_link,
        "buyer_name":    buyer_name or "ক্রেতা",
        "email":         email,
        "book_id":       book_id,
        "purchased_at":  datetime.now(timezone.utc).isoformat(),
        "restore_count": 0,
    })

# ══════════════════════════════════════════════════════════════
# Email Helpers
# ══════════════════════════════════════════════════════════════

def send_email_via_gas(to_email, buyer_name, book_title, download_url):
    if not GAS_URL:
        return False
    try:
        params = urllib.parse.urlencode({
            "action": "sendEmail",
            "to":     to_email,
            "name":   buyer_name,
            "book":   book_title,
            "link":   download_url,
            "store":  STORE_NAME,
        })
        r = requests.get(f"{GAS_URL}?{params}", timeout=20, allow_redirects=True)
        return r.status_code == 200 and "error" not in r.text.lower()
    except Exception:
        return False


def send_otp_via_gas(to_email, otp):
    if not GAS_URL:
        return False
    try:
        params = urllib.parse.urlencode({
            "action": "sendEmail",
            "to":     to_email,
            "name":   "ক্রেতা",
            "book":   f"{STORE_NAME} — Restore | কোড: {otp}",
            "book":   f"🔑 আপনার OTP কোড: {otp} (মেয়াদ ১০ মিনিট)",
            "link":   "#otp",
            "store":  STORE_NAME,
        })
        r = requests.get(f"{GAS_URL}?{params}", timeout=20, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def absolute_backend_url():
    if BACKEND_URL:
        return BACKEND_URL
    return request.url_root.rstrip("/")

# ══════════════════════════════════════════════════════════════
# Basic Routes
# ══════════════════════════════════════════════════════════════

@app.route("/")
def home():
    return redirect("/library")


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name":             STORE_NAME + " eBook Reader",
        "short_name":       STORE_NAME,
        "start_url":        "/library",
        "display":          "standalone",
        "background_color": "#0d0d0d",
        "theme_color":      "#b8741a",
        "icons": [{"src": "https://cdn-icons-png.flaticon.com/512/2702/2702134.png",
                   "sizes": "512x512", "type": "image/png"}],
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok",
                    "firebase": "✅" if fb_get("ping") is not False else "⚠️"})

# ══════════════════════════════════════════════════════════════
# Service Worker  (NEW — enables full offline support)
# ══════════════════════════════════════════════════════════════

SW_JS = r"""
const CACHE_V = 'boighor-v6';
const PRECACHE = [
  '/library',
  '/reader',
  '/manifest.json',
  'https://cdn.tailwindcss.com',
  'https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js',
  'https://cdn.jsdelivr.net/npm/epubjs@0.3.93/dist/epub.min.js',
];
const BYPASS = [
  '/stream-ebook/', '/admin/', '/request-restore',
  '/verify-restore', '/mark-used', '/health', '/download/',
];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE_V).then(cache =>
      Promise.allSettled(PRECACHE.map(url =>
        cache.add(url).catch(() => {/* ok to fail */})
      ))
    )
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE_V).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const OFFLINE_HTML = `<!DOCTYPE html><html lang="bn"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>অফলাইন — বইঘর</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#e2e2e2;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}
.box{text-align:center;max-width:320px}
.icon{font-size:64px;margin-bottom:20px;display:block}
h1{font-size:22px;color:#f59e0b;margin-bottom:10px}
p{color:#888;font-size:14px;line-height:1.6;margin-bottom:20px}
a{display:inline-block;padding:12px 24px;background:#f59e0b;color:#000;
border-radius:14px;text-decoration:none;font-weight:700;font-size:14px}
</style></head><body>
<div class="box">
<span class="icon">📚</span>
<h1>আপনি অফলাইনে আছেন</h1>
<p>ইন্টারনেট সংযোগ নেই। তবে আপনার ডাউনলোড করা বইগুলো পড়া যাবে।</p>
<a href="/library">লাইব্রেরিতে যান</a>
</div></body></html>`;

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);

  // Never intercept API / streaming / admin calls
  if (BYPASS.some(p => url.pathname.startsWith(p))) return;

  // CDN assets: cache-first
  if (url.origin !== self.location.origin) {
    e.respondWith(
      caches.match(e.request).then(hit => {
        if (hit) return hit;
        return fetch(e.request).then(res => {
          if (res.ok) caches.open(CACHE_V).then(c => c.put(e.request, res.clone()));
          return res;
        }).catch(() => hit || new Response('', { status: 503 }));
      })
    );
    return;
  }

  // /reader shell — cache without query string so it works offline
  if (url.pathname === '/reader') {
    const KEY = new Request('/reader');
    e.respondWith(
      fetch(e.request).then(res => {
        if (res.ok) caches.open(CACHE_V).then(c => c.put(KEY, res.clone()));
        return res;
      }).catch(() =>
        caches.match(KEY).then(hit =>
          hit || new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html;charset=utf-8' } })
        )
      )
    );
    return;
  }

  // /library and everything else: cache-first + background update
  e.respondWith(
    caches.match(e.request).then(cached => {
      const networkFetch = fetch(e.request).then(res => {
        if (res.ok && res.status < 300)
          caches.open(CACHE_V).then(c => c.put(e.request, res.clone()));
        return res;
      }).catch(() => cached || new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html;charset=utf-8' } }));
      return cached || networkFetch;
    })
  );
});
"""

@app.route("/sw.js")
def service_worker():
    return Response(
        SW_JS,
        mimetype="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )

# ══════════════════════════════════════════════════════════════
# Admin / Token Routes
# ══════════════════════════════════════════════════════════════

@app.route("/admin/issue-token", methods=["POST", "OPTIONS"])
def issue_token():
    if request.method == "OPTIONS":
        return ("", 204)

    data       = request.get_json(force=True) or {}
    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    email      = (data.get("email")      or "").strip()
    buyer_name = (data.get("buyer_name") or "ক্রেতা").strip()
    book_title = (data.get("book_title") or "").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_id    = (data.get("book_id")    or "").strip()

    if not drive_link and book_id:
        book_data = fb_get(f"books/{book_id}")
        if isinstance(book_data, dict):
            drive_link = (book_data.get("ebookLink") or "").strip()
            if not book_title:
                book_title = (book_data.get("title") or "").strip()

    if not email or not drive_link:
        return jsonify({"success": False, "error": "Email or Book Link missing"}), 400

    token      = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{absolute_backend_url()}/download/{token}"

    saved = fb_set(f"tokens/{token}", {
        "email":      email,
        "drive_link": drive_link,
        "book_title": book_title or "ইবুক",
        "buyer_name": buyer_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "used":       False,
        "format":     "epub",
    })

    if not saved:
        # Firebase সব retry-র পরেও fail করেছে।
        # Frontend retry করবে (retry:True দেখে), কিন্তু সেটাও fail করলে
        # customer যেন খালি হাতে না থাকে — fallback email পাঠাও drive link দিয়ে।
        print(f"⚠️ Firebase failed after all retries — sending fallback email to {email}", flush=True)
        # /view URL দিলে "No preview available" দেখাবে — direct download URL বানাও
        _fid = extract_drive_file_id(drive_link)
        fallback_url = (f"https://drive.google.com/uc?export=download&id={_fid}"
                        if _fid else drive_link)
        fallback_sent = send_email_via_gas(email, buyer_name, book_title or "ইবুক", fallback_url)
        print(f"📧 Fallback email sent: {fallback_sent}", flush=True)
        return jsonify({"success": False, "error": "Firebase Error", "retry": True,
                        "fallback_email_sent": fallback_sent}), 503

    save_purchase_record(email, book_title or "ইবুক", drive_link, buyer_name, book_id)
    email_sent = send_email_via_gas(email, buyer_name, book_title or "ইবুক", download_url)
    return jsonify({"success": True, "token": token,
                    "download_url": download_url, "email_sent": email_sent})


@app.route("/resend-link", methods=["POST", "OPTIONS"])
def resend_link():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(force=True) or {}
    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    email      = (data.get("email")      or "").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_title = (data.get("book_title") or "ইবুক").strip()
    if not email or not drive_link:
        return jsonify({"success": False, "error": "Missing data"}), 400

    token        = str(uuid.uuid4())
    expires_at   = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{absolute_backend_url()}/download/{token}"
    saved = fb_set(f"tokens/{token}", {
        "email": email, "drive_link": drive_link, "book_title": book_title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at, "used": False, "format": "epub",
    })
    if not saved:
        return jsonify({"success": False, "error": "Firebase unavailable"}), 503
    send_email_via_gas(email, "ক্রেতা", book_title, download_url)
    return jsonify({"success": True, "token": token, "download_url": download_url})

# ══════════════════════════════════════════════════════════════
# Restore OTP Routes
# ══════════════════════════════════════════════════════════════

@app.route("/request-restore-otp", methods=["POST", "OPTIONS"])
def request_restore_otp():
    if request.method == "OPTIONS":
        return ("", 204)
    data  = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"success": False, "error": "ইমেইল দিন।"}), 400

    se        = safe_email(email)
    purchases = fb_get(f"purchases/{se}")
    if not purchases or not isinstance(purchases, dict):
        return jsonify({"success": False,
                        "error": "এই ইমেইলে কোনো কেনার রেকর্ড নেই।"}), 404

    otp        = str(random.randint(100000, 999999))
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    fb_set(f"otp/{se}", {"code": otp, "expires_at": expires_at})
    send_otp_via_gas(email, otp)
    return jsonify({"success": True, "message": "OTP পাঠানো হয়েছে।"})


@app.route("/verify-restore-otp", methods=["POST", "OPTIONS"])
def verify_restore_otp():
    if request.method == "OPTIONS":
        return ("", 204)
    data  = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    otp   = str(data.get("otp") or "").strip()
    if not email or not otp:
        return jsonify({"success": False, "error": "ইমেইল ও OTP দিন।"}), 400

    se      = safe_email(email)
    otp_rec = fb_get(f"otp/{se}")
    if not otp_rec or not isinstance(otp_rec, dict):
        return jsonify({"success": False, "error": "OTP পাওয়া যায়নি। নতুন OTP পাঠান।"}), 400

    if str(otp_rec.get("code", "")) != otp:
        return jsonify({"success": False, "error": "OTP ভুল।"}), 400

    try:
        exp = datetime.fromisoformat(otp_rec.get("expires_at", ""))
        if datetime.now(timezone.utc) > exp:
            fb_delete(f"otp/{se}")
            return jsonify({"success": False,
                            "error": "OTP মেয়াদ শেষ। নতুন OTP পাঠান।"}), 400
    except Exception:
        pass

    fb_delete(f"otp/{se}")

    purchases = fb_get(f"purchases/{se}")
    if not purchases or not isinstance(purchases, dict):
        return jsonify({"success": False, "error": "কেনার রেকর্ড পাওয়া যায়নি।"}), 404

    books_out = []
    for purchase_key, rec in purchases.items():
        if not isinstance(rec, dict):
            continue
        drive_link = (rec.get("drive_link") or "").strip()
        book_title = rec.get("book_title", "ইবুক")
        if not drive_link:
            continue

        new_token  = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        fb_set(f"tokens/{new_token}", {
            "email":      email,
            "drive_link": drive_link,
            "book_title": book_title,
            "buyer_name": rec.get("buyer_name", "ক্রেতা"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
            "used":       False,
            "format":     "epub",
            "is_restore": True,
        })
        old_count = rec.get("restore_count", 0)
        fb_set(f"purchases/{se}/{purchase_key}/restore_count", old_count + 1)
        books_out.append({"title": book_title, "token": new_token})

    return jsonify({"success": True, "books": books_out})

# ══════════════════════════════════════════════════════════════
# Library  (v2 — rewritten with Continue Reading + Progress)
# ══════════════════════════════════════════════════════════════

LIBRARY_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>আমার লাইব্রেরি — {{ store }}</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#1a0e02">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#e2e2e2;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh;padding-bottom:24px}
@media print{body{display:none!important}}

/* Header */
.lib-hdr{
  background:linear-gradient(165deg,#1a0e02 0%,#0d0d0d 100%);
  padding:20px 16px 16px;
  border-bottom:1px solid rgba(245,158,11,.12);
  position:sticky;top:0;z-index:30;
}
.lib-hdr-inner{display:flex;justify-content:space-between;align-items:center}
.lib-logo{font-size:22px;font-weight:900;color:#f59e0b;letter-spacing:-.3px}
.lib-sub{font-size:11px;color:#52525b;margin-top:2px}
.lib-badge{font-size:11px;background:rgba(245,158,11,.12);color:#f59e0b;
  padding:4px 10px;border-radius:20px;border:1px solid rgba(245,158,11,.22);font-weight:700}

/* Continue reading card */
.cont-card{
  background:linear-gradient(135deg,rgba(245,158,11,.1),rgba(245,158,11,.04));
  border:1px solid rgba(245,158,11,.22);
  border-radius:18px;padding:14px 16px;
  display:flex;align-items:center;gap:14px;cursor:pointer;
  transition:border-color .2s,transform .1s;-webkit-tap-highlight-color:transparent;
  margin-bottom:6px;
}
.cont-card:active{transform:scale(.97);border-color:rgba(245,158,11,.6)}

/* Section labels */
.sec-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:#52525b;
  font-weight:700;margin:20px 0 10px}

/* Book grid */
.bk-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}

/* Book card */
.bk-card{
  background:#111;border:1px solid #1e1e1e;border-radius:18px;
  overflow:hidden;cursor:pointer;position:relative;
  transition:transform .15s,border-color .15s;-webkit-tap-highlight-color:transparent;
}
.bk-card:active{transform:scale(.95);border-color:rgba(245,158,11,.35)}
.bk-cover{
  aspect-ratio:3/4;display:flex;align-items:center;
  justify-content:center;font-size:40px;position:relative;overflow:hidden;
}
.bk-cover::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(to bottom,transparent 50%,rgba(0,0,0,.7) 100%);
}
.bk-prog{position:absolute;bottom:0;left:0;right:0;height:3px;background:rgba(0,0,0,.5)}
.bk-prog-fill{height:100%;background:#f59e0b;border-radius:0 2px 2px 0;transition:width .5s}
.bk-info{padding:10px 12px 12px}
.bk-title{font-size:12.5px;font-weight:600;color:#e4e4e7;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:34px}
.bk-pct{font-size:10.5px;margin-top:4px}

/* Delete button on card */
.bk-del{position:absolute;top:6px;right:6px;z-index:5;
  width:28px;height:28px;border-radius:50%;background:rgba(0,0,0,.55);
  border:none;color:#f87171;font-size:13px;
  display:flex;align-items:center;justify-content:center;cursor:pointer;
  opacity:0;transition:opacity .2s;-webkit-tap-highlight-color:transparent}
.bk-card:hover .bk-del,.bk-del.vis{opacity:1}

/* Skeleton */
.skel{
  background:linear-gradient(90deg,#161616 25%,#222 50%,#161616 75%);
  background-size:300% 100%;animation:shimmer 1.6s infinite;border-radius:18px;
}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}

/* FAB */
#fab{
  position:fixed;bottom:24px;right:20px;width:52px;height:52px;border-radius:50%;
  background:#f59e0b;color:#000;font-size:20px;display:flex;
  align-items:center;justify-content:center;
  box-shadow:0 4px 20px rgba(245,158,11,.4);cursor:pointer;border:none;
  transition:transform .15s;z-index:40;-webkit-tap-highlight-color:transparent;
}
#fab:active{transform:scale(.9)}

/* Modal */
.modal-bg{
  position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:50;
  display:flex;align-items:flex-end;justify-content:center;
}
.modal-box{
  background:#111;border:1px solid #222;border-radius:24px 24px 0 0;
  width:100%;max-width:480px;padding:24px;max-height:85vh;overflow-y:auto;
}
.modal-handle{width:36px;height:4px;background:#333;border-radius:2px;margin:0 auto 20px}
input:focus{outline:none}

/* Confirm modal */
.confirm-modal{
  background:#111;border:1px solid #222;border-radius:20px;
  max-width:300px;width:90%;padding:24px;text-align:center;
  box-shadow:0 20px 60px rgba(0,0,0,.5);
}
</style>
</head>
<body>

<!-- Header -->
<div class="lib-hdr">
  <div class="lib-hdr-inner">
    <div>
      <div class="lib-logo">📚 {{ store }}</div>
      <div class="lib-sub" id="libSub">আপনার ডিজিটাল বুকশেলফ</div>
    </div>
    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
      <div class="lib-badge">✦ প্রিমিয়াম</div>
      <div id="offlineBadge" style="display:none;font-size:11px;color:#4ade80;display:flex;align-items:center;gap:5px">
        <span style="width:6px;height:6px;border-radius:50%;background:#4ade80;display:inline-block"></span>অফলাইনে পড়া যাবে
      </div>
    </div>
  </div>
</div>

<!-- Main -->
<div style="padding:16px">

  <!-- Skeleton -->
  <div id="loadSkel" class="bk-grid">
    <div class="skel" style="height:220px"></div>
    <div class="skel" style="height:220px"></div>
    <div class="skel" style="height:220px"></div>
    <div class="skel" style="height:220px"></div>
  </div>

  <!-- Continue Reading -->
  <div id="continueSection" style="display:none">
    <div class="sec-lbl" style="margin-top:0">সর্বশেষ পড়া</div>
    <div id="contCard" class="cont-card">
      <div id="contCover" style="width:60px;height:80px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:28px;flex-shrink:0;background:linear-gradient(135deg,#f59e0b,#b8741a)">📖</div>
      <div style="flex:1;min-width:0">
        <div id="contTitle" style="font-weight:600;color:#fff;font-size:13px;line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">বই শিরোনাম</div>
        <div id="contPct" style="font-size:11px;color:#f59e0b;margin-top:4px">শুরু করুন</div>
        <div style="margin-top:8px;height:3px;background:#1e1e1e;border-radius:2px;overflow:hidden">
          <div id="contBar" style="height:100%;background:#f59e0b;border-radius:2px;width:0%;transition:width .5s ease"></div>
        </div>
        <div style="font-size:10px;color:#52525b;margin-top:6px">▶ পড়া চালিয়ে যান</div>
      </div>
    </div>
  </div>

  <!-- Shelf -->
  <div id="shelfHdr" style="display:none;margin-bottom:10px">
    <div class="sec-lbl" style="margin:0;display:flex;justify-content:space-between;align-items:center">
      <span>আমার বই</span>
      <span id="bookCnt" style="font-size:11px;color:#52525b;text-transform:none;letter-spacing:0"></span>
    </div>
  </div>
  <div id="shelf" class="bk-grid" style="display:none"></div>

  <!-- Empty -->
  <div id="emptyState" style="display:none;text-align:center;padding:60px 0 40px">
    <div style="font-size:56px;margin-bottom:16px">📚</div>
    <h2 style="font-size:18px;font-weight:700;color:#d4d4d8;margin-bottom:8px">লাইব্রেরি খালি</h2>
    <p style="font-size:13px;color:#52525b;margin-bottom:24px;line-height:1.6">আগে কিনে থাকলে নিচের বোতাম দিয়ে বই ফিরিয়ে আনুন</p>
    <button onclick="openRestore()" style="padding:13px 28px;background:#f59e0b;color:#000;border:none;border-radius:16px;font-size:14px;font-weight:700;cursor:pointer">
      🔄 Restore করুন
    </button>
  </div>
</div>

<!-- FAB -->
<button id="fab" onclick="openRestore()" title="Restore করুন">🔄</button>

<!-- Restore Modal -->
<div id="rModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:50;display:none;align-items:flex-end;justify-content:center">
  <div class="modal-box">
    <div class="modal-handle"></div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
      <h3 style="font-size:16px;font-weight:700">🔄 লাইব্রেরি Restore</h3>
      <button onclick="closeRestore()" style="background:#1e1e1e;border:none;color:#888;width:28px;height:28px;border-radius:50%;font-size:14px;cursor:pointer">✕</button>
    </div>
    <div id="rs1">
      <p style="font-size:13px;color:#888;margin-bottom:16px">ক্রয়কালীন ইমেইল দিন — OTP পাঠানো হবে</p>
      <input id="rEmail" type="email" placeholder="email@example.com"
        style="width:100%;background:#1a1a1a;border:1.5px solid #2a2a2a;border-radius:14px;padding:14px;color:#fff;font-size:16px;margin-bottom:14px;display:block;transition:border-color .2s"
        onfocus="this.style.borderColor='#f59e0b'" onblur="this.style.borderColor='#2a2a2a'"
        onkeydown="if(event.key==='Enter')doOTP()">
      <button onclick="doOTP()" style="width:100%;padding:14px;background:#f59e0b;color:#000;border:none;border-radius:14px;font-size:14px;font-weight:700;cursor:pointer">
        OTP পাঠান 📧
      </button>
      <p id="rs1msg" style="font-size:13px;margin-top:12px;text-align:center;display:none"></p>
    </div>
    <div id="rs2" style="display:none">
      <p style="font-size:13px;color:#888;margin-bottom:6px">ইমেইলে পাঠানো ৬ সংখ্যার কোড দিন</p>
      <p id="rs2email" style="font-size:12px;color:#f59e0b;margin-bottom:18px"></p>
      <input id="rOTP" type="number" placeholder="123456"
        style="width:100%;background:#1a1a1a;border:1.5px solid #2a2a2a;border-radius:16px;padding:16px;color:#fff;font-size:28px;text-align:center;letter-spacing:12px;margin-bottom:14px;display:block;transition:border-color .2s"
        onfocus="this.style.borderColor='#f59e0b'" onblur="this.style.borderColor='#2a2a2a'"
        oninput="if(this.value.length>6)this.value=this.value.slice(0,6)"
        onkeydown="if(event.key==='Enter')doVerify()">
      <button onclick="doVerify()" style="width:100%;padding:14px;background:#f59e0b;color:#000;border:none;border-radius:16px;font-size:14px;font-weight:700;cursor:pointer">
        নিশ্চিত করুন ✓
      </button>
      <button onclick="backS1()" style="width:100%;padding:10px;background:transparent;border:none;color:#666;font-size:13px;cursor:pointer;margin-top:6px">
        ← ইমেইল পরিবর্তন
      </button>
      <p id="rs2msg" style="font-size:13px;margin-top:12px;text-align:center;display:none"></p>
    </div>
    <div id="rs3" style="display:none">
      <p style="font-size:13px;color:#888;margin-bottom:16px">✅ পরিচয় নিশ্চিত! বইগুলো ডাউনলোড হচ্ছে...</p>
      <div id="rList" style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px"></div>
      <div id="rDone" style="display:none;text-align:center;padding:24px 0">
        <div style="font-size:48px;margin-bottom:12px">🎉</div>
        <p style="color:#4ade80;font-size:16px;font-weight:700">Restore সফল হয়েছে!</p>
        <p style="color:#666;font-size:13px;margin-top:6px">বইগুলো আপনার লাইব্রেরিতে আছে।</p>
      </div>
    </div>
  </div>
</div>

<!-- Delete Confirm Modal -->
<div id="delModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:60;align-items:center;justify-content:center">
  <div class="confirm-modal">
    <div style="font-size:36px;margin-bottom:12px">🗑️</div>
    <h3 style="font-size:16px;font-weight:700;margin-bottom:8px">বই মুছবেন?</h3>
    <p id="delTitle" style="font-size:13px;color:#888;margin-bottom:20px;line-height:1.5">এই বইটি ডিভাইস থেকে মুছে যাবে। পরে Restore করতে পারবেন।</p>
    <div style="display:flex;gap:10px">
      <button onclick="closeDelModal()" style="flex:1;padding:12px;background:#1e1e1e;border:none;border-radius:12px;color:#aaa;font-size:13px;font-weight:700;cursor:pointer">বাতিল</button>
      <button id="delConfirmBtn" style="flex:1;padding:12px;background:#dc2626;border:none;border-radius:12px;color:#fff;font-size:13px;font-weight:700;cursor:pointer">মুছুন ✕</button>
    </div>
  </div>
</div>

<script>
/* ── Service Worker ── */
if('serviceWorker' in navigator){
  window.addEventListener('load',()=>{
    navigator.serviceWorker.register('/sw.js',{scope:'/'})
      .then(()=>{ if(!navigator.onLine) document.getElementById('offlineBadge').style.display='flex'; })
      .catch(e=>console.warn('[SW]',e));
  });
}
window.addEventListener('offline',()=>{ document.getElementById('offlineBadge').style.display='flex'; });

/* ── Helpers ── */
const DB_N='BoighorDB',STR='books';
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
const GRADS=[
  ['#f59e0b','#d97706'],['#667eea','#764ba2'],['#43e97b','#38f9d7'],
  ['#f093fb','#f5576c'],['#4facfe','#00f2fe'],['#fa709a','#fee140'],
  ['#a18cd1','#fbc2eb'],['#84fab0','#8fd3f4'],['#c471f5','#fa71cd'],
];
function grad(t){const i=(t&&t.charCodeAt?t.charCodeAt(0):0)%GRADS.length;return `linear-gradient(135deg,${GRADS[i][0]},${GRADS[i][1]}`;}
function getProgress(tok){try{return JSON.parse(localStorage.getItem('boighor_pos_'+tok)||'null');}catch(_){return null;}}

async function openDB(){
  return new Promise((res,rej)=>{
    const q=indexedDB.open(DB_N,1);
    q.onupgradeneeded=e=>{const d=e.target.result;if(!d.objectStoreNames.contains(STR))d.createObjectStore(STR,{keyPath:'token'});};
    q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
  });
}

/* ── Delete helpers ── */
let delToken=null;
function showDelModal(token,title){
  delToken=token;
  document.getElementById('delTitle').innerText='"'+title+'" ডিভাইস থেকে মুছে যাবে।';
  const m=document.getElementById('delModal');
  m.style.display='flex';
  document.getElementById('delConfirmBtn').onclick=()=>doDelete();
}
function closeDelModal(){
  document.getElementById('delModal').style.display='none';
  delToken=null;
}
async function doDelete(){
  if(!delToken)return;
  try{
    const db=await openDB();
    await new Promise((res,rej)=>{
      const tx=db.transaction(STR,'readwrite');
      tx.objectStore(STR).delete(delToken);
      tx.oncomplete=res;tx.onerror=()=>rej(tx.error);
    });
    closeDelModal();loadLibrary();
  }catch(e){alert('মুছতে সমস্যা: '+e.message);}
}
window.closeDelModal=closeDelModal;

/* ── Load library ── */
async function loadLibrary(){
  const skel=document.getElementById('loadSkel');
  const shelf=document.getElementById('shelf');
  const empty=document.getElementById('emptyState');
  const contSec=document.getElementById('continueSection');
  const hdr=document.getElementById('shelfHdr');
  skel.style.display='grid';
  shelf.style.display='none';
  empty.style.display='none';
  contSec.style.display='none';
  hdr.style.display='none';
  try{
    const db=await openDB();
    const books=await new Promise((res,rej)=>{
      const tx=db.transaction(STR,'readonly');
      const q=tx.objectStore(STR).getAll();
      q.onsuccess=()=>res(q.result||[]);q.onerror=()=>rej(q.error);
    });
    skel.style.display='none';
    if(!books.length){empty.style.display='block';return;}
    document.getElementById('offlineBadge').style.display='flex';
    document.getElementById('libSub').innerText=books.length+'টি বই • অফলাইনে পড়া যাবে';

    // Continue Reading
    const last=(()=>{try{return JSON.parse(localStorage.getItem('boighor_lastBook')||'null');}catch(_){return null;}})();
    if(last&&books.some(b=>b.token===last.token)){
      const p=getProgress(last.token);
      const pct=p?.percentage||0;
      document.getElementById('contTitle').innerText=last.title||'ইবুক';
      document.getElementById('contPct').innerText=pct?pct+'% পড়া হয়েছে':'শুরু করুন';
      document.getElementById('contBar').style.width=pct+'%';
      document.getElementById('contCover').style.background=grad(last.title||'')+')';
      document.getElementById('contCard').onclick=()=>{window.location.href='/reader?token='+encodeURIComponent(last.token);};
      contSec.style.display='block';
    }

    // Book grid
    shelf.innerHTML='';
    books.forEach(b=>{
      const p=getProgress(b.token);
      const pct=p?.percentage||0;
      const g=grad(b.title||'')+')';
      const card=document.createElement('div');
      card.className='bk-card';
      card.innerHTML=`
        <div class="bk-cover" style="background:${g}">
          <span style="filter:drop-shadow(0 2px 8px rgba(0,0,0,.6));position:relative;z-index:1">📖</span>
          <div class="bk-prog"><div class="bk-prog-fill" style="width:${pct}%"></div></div>
        </div>
        <button class="bk-del" onclick="event.stopPropagation();showDelModal('${esc(b.token)}','${esc(b.title||'ইবুক')}')">🗑</button>
        <div class="bk-info">
          <div class="bk-title">${esc(b.title||'ইবুক')}</div>
          <div class="bk-pct" style="color:${pct>0?'#f59e0b':'#52525b'}">${pct>0?pct+'% পড়া':'পড়া শুরু করুন'}</div>
        </div>`;
      card.onclick=()=>{window.location.href='/reader?token='+encodeURIComponent(b.token);};
      // Long press to show delete button
      let pressT;
      card.addEventListener('touchstart',()=>{pressT=setTimeout(()=>card.querySelector('.bk-del').classList.add('vis'),600);},{passive:true});
      card.addEventListener('touchend',()=>clearTimeout(pressT));
      shelf.appendChild(card);
    });
    shelf.style.display='grid';
    hdr.style.display='block';
    document.getElementById('bookCnt').innerText=books.length+'টি বই';
  }catch(e){
    console.error(e);
    skel.style.display='none';
    empty.style.display='block';
    empty.innerHTML='<div style="font-size:40px;margin-bottom:12px">⚠️</div><p style="color:#f87171;margin-bottom:16px">লাইব্রেরি লোড হয়নি।</p><button onclick="location.reload()" style="padding:10px 20px;background:#222;border:none;border-radius:12px;color:#e2e2e2;cursor:pointer;font-size:13px">আবার চেষ্টা করুন</button>';
  }
}

/* ── Restore modal ── */
let rEmail='',rBooks=[];
function openRestore(){const m=document.getElementById('rModal');m.style.display='flex';}
function closeRestore(){document.getElementById('rModal').style.display='none';}
window.openRestore=openRestore;window.closeRestore=closeRestore;

function setMsg(id,txt,isErr){
  const el=document.getElementById(id);
  el.innerText=txt;el.style.color=isErr?'#f87171':'#fbbf24';el.style.display='block';
}
function backS1(){
  document.getElementById('rs2').style.display='none';
  document.getElementById('rs1').style.display='block';
  document.getElementById('rs1msg').style.display='none';
}
window.backS1=backS1;

async function doOTP(){
  const email=(document.getElementById('rEmail').value||'').trim();
  if(!email)return;
  rEmail=email;
  setMsg('rs1msg','⏳ OTP পাঠানো হচ্ছে...',false);
  try{
    const r=await fetch('/request-restore-otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
    const d=await r.json();
    if(d.success){
      document.getElementById('rs1').style.display='none';
      document.getElementById('rs2email').innerText='📧 '+rEmail;
      document.getElementById('rs2').style.display='block';
    }else{setMsg('rs1msg',d.error||'ত্রুটি হয়েছে।',true);}
  }catch(_){setMsg('rs1msg','🔴 ইন্টারনেট সংযোগ পরীক্ষা করুন।',true);}
}
window.doOTP=doOTP;

async function doVerify(){
  const otp=(document.getElementById('rOTP').value||'').trim();
  if(!otp){setMsg('rs2msg','OTP দিন।',true);return;}
  setMsg('rs2msg','⏳ যাচাই হচ্ছে...',false);
  try{
    const r=await fetch('/verify-restore-otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:rEmail,otp})});
    const d=await r.json();
    if(d.success){
      rBooks=d.books||[];
      document.getElementById('rs2').style.display='none';
      document.getElementById('rs3').style.display='block';
      downloadAll();
    }else{setMsg('rs2msg',d.error||'OTP ভুল।',true);}
  }catch(_){setMsg('rs2msg','🔴 সংযোগ সমস্যা।',true);}
}
window.doVerify=doVerify;

async function downloadAll(){
  const db=await openDB();
  for(const b of rBooks){
    const row=document.createElement('div');
    row.style.cssText='background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:12px 14px;display:flex;align-items:center;justify-content:space-between;gap:8px';
    const st=document.createElement('span');
    st.style.cssText='font-size:12px;color:#888';st.innerText=b.title;
    const p=document.createElement('span');p.style.cssText='font-size:12px;color:#f59e0b;flex-shrink:0';p.innerText='⏳ ডাউনলোড...';
    row.appendChild(st);row.appendChild(p);
    document.getElementById('rList').appendChild(row);
    try{
      const resp=await fetch('/stream-ebook/'+encodeURIComponent(b.token),{cache:'no-store'});
      if(!resp.ok)throw new Error('HTTP '+resp.status);
      const rdr=resp.body.getReader();const cl=+(resp.headers.get('Content-Length')||0);
      let recv=0,chunks=[];
      while(true){const{done,value}=await rdr.read();if(done)break;chunks.push(value);recv+=value.length;p.innerText=cl?Math.round(recv/cl*100)+'%':Math.round(recv/1024)+' KB';}
      const blob=new Blob(chunks,{type:'application/epub+zip'});
      const hd=new Uint8Array(await blob.slice(0,4).arrayBuffer());
      if(!(hd[0]===0x50&&hd[1]===0x4B))throw new Error('Invalid EPUB');
      p.innerText='💾 সেভ...';
      await new Promise((res,rej)=>{
        const tx=db.transaction(STR,'readwrite');
        tx.objectStore(STR).put({token:b.token,title:b.title,format:'epub',blob,added_at:new Date().toISOString()});
        tx.oncomplete=res;tx.onerror=()=>rej(tx.error);
      });
      try{await fetch('/mark-used/'+encodeURIComponent(b.token),{method:'POST'});}catch(_){}
      p.innerText='✅ সম্পন্ন';p.style.color='#4ade80';
    }catch(e){console.error(e);p.innerText='❌ ব্যর্থ';p.style.color='#f87171';}
  }
  document.getElementById('rDone').style.display='block';
  setTimeout(()=>{closeRestore();loadLibrary();},2200);
}

loadLibrary();
</script>
</body>
</html>
"""

@app.route("/library")
def library():
    return render_template_string(LIBRARY_HTML, store=STORE_NAME)

# ══════════════════════════════════════════════════════════════
# Download / Import EPUB
# ══════════════════════════════════════════════════════════════

@app.route("/download/<token>")
def download_landing(token):
    return redirect(f"/download/{token}/confirm")


@app.route("/download/<token>/confirm")
def download_confirm(token):
    t = fb_get(f"tokens/{token}")
    if t is False or not isinstance(t, dict):
        return "❌ লিংকটি বৈধ নয়।", 404
    try:
        exp = datetime.fromisoformat(t.get("expires_at", ""))
        if datetime.now(timezone.utc) > exp:
            return "⌛ এই লিংকের মেয়াদ শেষ হয়ে গেছে।", 410
    except Exception:
        pass

    book_title = t.get("book_title", "ইবুক")
    return render_template_string("""
<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>বই সংগ্রহ — {{ store }}</title>
<link rel="manifest" href="/manifest.json">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#e2e2e2;font-family:'Segoe UI',system-ui,sans-serif;
min-height:100vh;padding:24px;display:flex;flex-direction:column;align-items:center;justify-content:center}
.box{text-align:center;max-width:360px;width:100%}
/* Install card */
.install-card{background:linear-gradient(135deg,rgba(245,158,11,.15),rgba(245,158,11,.05));
border:1.5px solid rgba(245,158,11,.35);border-radius:20px;padding:20px;margin-bottom:20px;text-align:center}
.app-icon{font-size:52px;display:block;margin-bottom:10px;animation:bob .8s ease-in-out infinite alternate}
@keyframes bob{0%{transform:translateY(0)}100%{transform:translateY(-6px)}}
.app-name{font-size:17px;font-weight:900;color:#f59e0b;margin-bottom:6px}
.app-desc{font-size:12px;color:#888;line-height:1.6;margin-bottom:14px}
.install-btn{width:100%;padding:14px;background:linear-gradient(135deg,#f59e0b,#d97706);
color:#000;border:none;border-radius:14px;font-size:14px;font-weight:800;cursor:pointer;
display:flex;align-items:center;justify-content:center;gap:8px}
.install-btn:active{opacity:.85}
.installed-note{font-size:11px;color:#52525b;margin-top:10px;line-height:1.5}
/* Download progress */
.dl-card{background:#111;border:1px solid #1e1e1e;border-radius:18px;padding:20px;margin-bottom:16px}
.dl-icon{font-size:52px;display:block;margin-bottom:14px;animation:bob .8s ease-in-out infinite alternate}
h1{font-size:18px;font-weight:700;color:#f0f0f0;margin-bottom:6px;line-height:1.3}
.sub{font-size:12px;color:#666;margin-bottom:18px;line-height:1.6}
.prog-wrap{background:#1a1a1a;border-radius:100px;height:5px;overflow:hidden;margin-bottom:10px}
.prog-bar{height:100%;background:linear-gradient(90deg,#f59e0b,#d97706);border-radius:100px;
transition:width .4s cubic-bezier(.4,0,.2,1);width:0%}
#stat{font-size:12px;color:#666;transition:color .3s}
#retryBtn{display:none;margin-top:16px;padding:12px 24px;background:#f59e0b;
color:#000;border:none;border-radius:14px;font-size:14px;font-weight:700;cursor:pointer}
.msg-good{color:#fbbf24!important}
.msg-err{color:#f87171!important}
.warn-note{font-size:11px;color:#52525b;margin-top:10px;line-height:1.5}
</style>
</head>
<body>
<div class="box">
  <!-- App install card -->
  <div class="install-card" id="installCard">
    <span class="app-icon">📚</span>
    <div class="app-name">বইঘর eBook Reader</div>
    <div class="app-desc">ইবুক খুলতে বইঘর Reader প্রয়োজন।<br>নিচের বোতামে চাপলে App ইনস্টল হবে এবং<br>বইটি স্বয়ংক্রিয়ভাবে লোড হবে।</div>
    <button class="install-btn" id="installBtn" onclick="doInstall()">
      📲 ইনস্টল ও বই ডাউনলোড করুন
    </button>
    <div class="installed-note">⚠️ এটাতে একটু সময় লাগতে পারে। রিফ্রেশ করবেন না।<br>ইনস্টল শেষে বইটি App-এ দেখতে পাবেন।</div>
  </div>

  <!-- Download progress -->
  <div class="dl-card">
    <span class="dl-icon">📥</span>
    <h1>{{ title }}</h1>
    <p class="sub">বইটি নিরাপদে ডাউনলোড হচ্ছে।<br>কোনো সমস্যা নেই।</p>
    <div class="prog-wrap"><div id="prog" class="prog-bar"></div></div>
    <div id="stat">🔒 নিরাপদ সংযোগ তৈরি হচ্ছে...</div>
    <button id="retryBtn" onclick="location.reload()">🔄 আবার চেষ্টা করুন</button>
    <div class="warn-note">✅ ডাউনলোড শেষে App-এ লাইব্রেরি খুলে যাবে।</div>
  </div>
</div>
<script>
const TOKEN = {{ token|tojson }};
const TITLE = {{ title|tojson }};
const prog = document.getElementById('prog');
const stat = document.getElementById('stat');

/* PWA Install prompt */
let deferredPrompt = null;
let downloadStarted = false;

function startDownload() {
  if(downloadStarted) return;
  downloadStarted = true;
  run();
}

window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  deferredPrompt = e;
  // auto-trigger install prompt immediately (page opened by user click)
  deferredPrompt.prompt();
  deferredPrompt.userChoice
    .then(() => { deferredPrompt = null; })
    .catch(() => { deferredPrompt = null; });
});

async function doInstall() {
  if(deferredPrompt) {
    deferredPrompt.prompt();
    deferredPrompt.userChoice.then(() => { deferredPrompt = null; }).catch(() => { deferredPrompt = null; });
  }
  document.getElementById('installCard').style.opacity = '.5';
  startDownload();
}

window.addEventListener('appinstalled', () => {
  const card = document.getElementById('installCard');
  if(card) { card.style.background = 'rgba(74,222,128,.1)'; card.style.borderColor = 'rgba(74,222,128,.3)'; }
  const btn = document.getElementById('installBtn');
  if(btn) btn.innerText = '✅ ইনস্টল সম্পন্ন';
});

if('serviceWorker' in navigator)
  navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(()=>{});

function setStatus(msg, cls) {
  stat.innerText = msg;
  stat.className = cls ? 'msg-' + cls : '';
}
function fail(msg) {
  setStatus('⚠️ ' + msg, 'err');
  document.getElementById('retryBtn').style.display = 'inline-block';
  prog.style.background = '#ef4444';
}

async function run() {
  try {
    setStatus('📡 সংযোগ স্থাপিত হচ্ছে...', 'good');
    const resp = await fetch('/stream-ebook/' + encodeURIComponent(TOKEN), { cache: 'no-store' });
    if (!resp.ok) { const t = await resp.text().catch(() => ''); throw new Error(t || 'ডাউনলোড ব্যর্থ হয়েছে (' + resp.status + ')'); }

    const rdr = resp.body.getReader();
    const cl  = +(resp.headers.get('Content-Length') || 0);
    let recv  = 0, chunks = [];

    setStatus('📥 বইটি ডাউনলোড হচ্ছে...', 'good');
    while (true) {
      const { done, value } = await rdr.read();
      if (done) break;
      chunks.push(value); recv += value.length;
      if (cl) {
        const p = Math.min(90, Math.round(recv / cl * 90));
        prog.style.width = p + '%';
        setStatus('📥 ডাউনলোড হচ্ছে: ' + Math.round(recv/cl*100) + '% সম্পন্ন', 'good');
      } else {
        prog.style.width = '60%';
        setStatus('📥 ডাউনলোড হচ্ছে: ' + Math.round(recv/1024) + ' KB...', 'good');
      }
    }

    const blob = new Blob(chunks, { type: 'application/epub+zip' });
    const hd   = new Uint8Array(await blob.slice(0,4).arrayBuffer());
    if (!(hd[0]===0x50 && hd[1]===0x4B)) throw new Error('ফাইলটি সঠিক EPUB নয়। Google Drive share setting পরীক্ষা করুন।');

    prog.style.width = '92%';
    setStatus('💾 লাইব্রেরিতে সংরক্ষণ করা হচ্ছে...', 'good');

    const db = await new Promise((res, rej) => {
      const q = indexedDB.open('BoighorDB', 1);
      q.onupgradeneeded = e => { const d=e.target.result; if(!d.objectStoreNames.contains('books')) d.createObjectStore('books',{keyPath:'token'}); };
      q.onsuccess = () => res(q.result);
      q.onerror   = () => rej(q.error);
    });
    await new Promise((res, rej) => {
      const tx = db.transaction('books', 'readwrite');
      tx.objectStore('books').put({ token:TOKEN, title:TITLE, format:'epub', blob, added_at:new Date().toISOString() });
      tx.oncomplete = res;
      tx.onerror = () => rej(tx.error);
      tx.onabort = () => rej(tx.error || new Error('IndexedDB aborted'));
    });

    prog.style.width = '100%';
    setStatus('🎉 সংগ্রহ সম্পন্ন! লাইব্রেরিতে নিয়ে যাচ্ছি...', 'good');
    try { await fetch('/mark-used/' + encodeURIComponent(TOKEN), { method:'POST' }); } catch(_) {}
    setTimeout(() => { window.location.href = '/library'; }, 900);

  } catch(e) { console.error(e); fail(e.message || 'অপ্রত্যাশিত ত্রুটি।'); }
}
startDownload();
</script>
</body>
</html>
""", store=STORE_NAME, title=book_title, token=token)


@app.route("/stream-ebook/<token>")
def stream_ebook(token):
    t = fb_get(f"tokens/{token}")
    if t is False or not isinstance(t, dict):
        return "Unauthorized or token not found", 403
    try:
        exp = datetime.fromisoformat(t.get("expires_at", ""))
        if datetime.now(timezone.utc) > exp:
            return "Token expired", 410
    except Exception:
        pass
    drive_link = (t.get("drive_link") or "").strip()
    if not drive_link:
        return "File link not found", 404

    try:
        r           = open_drive_file_response(drive_link)
        status_code = getattr(r, "status_code", 500)
        if status_code < 200 or status_code >= 300:
            return f"Google Drive returned HTTP {status_code}", 502
        content_length = getattr(r, "headers", {}).get("Content-Length")
        iterator       = r.iter_content(chunk_size=8192)
        first_chunk    = next(iterator, b"")
        if not first_chunk:
            return "Empty ebook response", 502
        if not first_chunk[:4].startswith(b"PK"):
            if first_chunk[:1024].lstrip().startswith(b"<"):
                return "Google Drive did not return the EPUB file. Set sharing to 'Anyone with the link'.", 502
            return "Downloaded file is not an EPUB/ZIP file.", 502

        def generate():
            yield first_chunk
            for chunk in iterator:
                if chunk:
                    yield chunk

        headers = {"Cache-Control": "no-store"}
        if content_length:
            headers["Content-Length"] = content_length
        return Response(stream_with_context(generate()),
                        content_type="application/epub+zip", headers=headers)
    except Exception as e:
        print("stream_ebook error:", repr(e), flush=True)
        return "Internal Server Error while streaming ebook", 500


@app.route("/mark-used/<token>", methods=["POST"])
def mark_used(token):
    t = fb_get(f"tokens/{token}")
    if not isinstance(t, dict):
        return jsonify({"ok": False, "reason": "not found"}), 404
    if t.get("used"):
        return jsonify({"ok": True, "reason": "already marked"})
    count = (t.get("download_count") or 0) + 1
    fb_set(f"tokens/{token}/download_count", count)
    fb_set(f"tokens/{token}/last_used_at", datetime.now(timezone.utc).isoformat())
    return jsonify({"ok": True})


@app.route("/stream-pdf/<token>")
def stream_pdf_backward_compat(token):
    return stream_ebook(token)



# ══════════════════════════════════════════════════════════════
# EPUB Reader v4 — Colibrio-style, full features
# ══════════════════════════════════════════════════════════════

READER_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" id="themeColorMeta" content="#f2f2f2">
<title>{{ store }}</title>
<script src="https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/epubjs@0.3.93/dist/epub.min.js"></script>
<style>
:root{--bg:#f8f8f8;--bar:#f2f2f2;--bdr:#ddd;--tx:#1a1a1a;--sub:#666;--acc:#1a3a5c;--ac2:rgba(26,58,92,.1)}
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;height:100%;background:var(--bg);color:var(--tx);overflow:hidden;font-family:"Segoe UI",system-ui,sans-serif;transition:background .3s,color .3s}
body,*{user-select:none!important;-webkit-user-select:none!important;-webkit-tap-highlight-color:transparent}
@media print{body{display:none!important}}

/* TOP BAR */
#tBar{position:fixed;top:0;left:0;right:0;height:52px;background:var(--bar);border-bottom:1px solid var(--bdr);display:flex;align-items:center;justify-content:space-between;padding:0 10px;z-index:60;transition:transform .32s cubic-bezier(.4,0,.2,1),background .3s,border-color .3s;will-change:transform}
#tBar.bar-hidden{transform:translateY(-100%);pointer-events:none}
#progStrip{position:absolute;bottom:0;left:0;right:0;height:2px;background:rgba(0,0,0,.08);overflow:hidden}
#progFill{height:100%;width:0%;background:var(--acc);transition:width .8s ease;border-radius:0 2px 2px 0}
#tBack{color:var(--acc);font-size:13px;font-weight:700;text-decoration:none;display:flex;align-items:center;padding:8px 4px;white-space:nowrap;flex-shrink:0}
#tMid{flex:1;text-align:center;overflow:hidden;padding:0 2px;cursor:pointer;min-width:0}
#tTitle{font-size:11px;color:var(--sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.2}
#tChInfo{font-size:10px;color:var(--acc);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;line-height:1.2;margin-top:1px}
.hd-btn{width:32px;height:32px;border-radius:50%;background:var(--ac2);border:1.5px solid var(--acc);color:var(--acc);font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;transition:background .15s;margin-left:5px}
.hd-btn:active{background:var(--acc);color:#fff}
#tocBtn{font-size:16px;font-weight:400}
#qtBtn{font-size:15px}

/* VIEWER */
#viewer{position:fixed;top:52px;left:0;right:0;bottom:0;background:var(--bg);transition:background .3s}

/* bottom bar removed — prev/next via tap, bookmark in top bar */

/* PANELS */
.pbd{position:fixed;inset:0;background:rgba(0,0,0,0);z-index:80;pointer-events:none;transition:background .3s}
.pbd.on{pointer-events:auto;background:rgba(0,0,0,.6);backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px)}
.sp{position:fixed;bottom:0;left:0;right:0;background:var(--bar);border:1px solid var(--bdr);border-bottom:none;border-radius:24px 24px 0 0;transform:translateY(100%);transition:transform .34s cubic-bezier(.32,.72,0,1),background .3s,border-color .3s;z-index:90;max-height:88vh;overflow-y:auto;will-change:transform}
.sp.on{transform:translateY(0)}
.ph{width:40px;height:4px;border-radius:2px;background:var(--bdr);margin:12px auto 4px}
.phdr{display:flex;align-items:center;justify-content:space-between;padding:4px 16px 10px}
.pttl{font-size:15px;font-weight:700;color:var(--tx)}
.px{width:28px;height:28px;border-radius:50%;background:rgba(128,128,128,.2);border:none;color:var(--sub);font-size:13px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.px:active{background:rgba(255,255,255,.1)}

/* SETTINGS SECTIONS */
.ss{padding:10px 16px 14px;border-top:1px solid var(--bdr)}
.sl{font-size:9px;color:var(--sub);text-transform:uppercase;letter-spacing:.12em;margin-bottom:10px;font-weight:700}

/* THEME GRID */
.tgd{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}
.tsw{border:2px solid transparent;border-radius:14px;height:54px;cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;transition:border-color .2s,transform .1s}
.tsw:active{transform:scale(.9)}
.tsw.on{border-color:var(--acc)}
.tsw .tk{font-size:14px;opacity:0;color:#fff;font-weight:900;text-shadow:0 1px 4px rgba(0,0,0,.7)}
.tsw.on .tk{opacity:1}
.tsw .tl{font-size:9px;color:rgba(255,255,255,.92);font-weight:600;text-shadow:0 1px 3px rgba(0,0,0,.6);text-align:center}

/* STEP CONTROLS */
.stc{display:flex;align-items:center;gap:8px}
.stb{width:38px;height:38px;border-radius:10px;border:1.5px solid var(--bdr);background:transparent;color:var(--tx);font-size:15px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s}
.stb:active{background:var(--ac2);border-color:var(--acc)}
.stv{flex:1;text-align:center;font-size:13px;font-weight:600;color:var(--tx)}

/* TOGGLE GROUP */
.tgg{display:flex;gap:8px}
.tgb{flex:1;padding:9px 8px;border-radius:12px;border:1.5px solid var(--bdr);background:transparent;color:var(--sub);font-size:11px;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:5px;transition:all .2s}
.tgb.on{border-color:var(--acc);color:var(--acc);background:var(--ac2)}

/* FONT FAMILY */
.ffg{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
.ffb{padding:10px 4px;border-radius:12px;border:1.5px solid var(--bdr);background:transparent;color:var(--sub);font-size:10px;cursor:pointer;text-align:center;transition:all .2s;line-height:1.3}
.ffb.on{border-color:var(--acc);color:var(--acc);background:var(--ac2)}

/* TOC & BOOKMARKS */
#tocList,#bmList{padding:0 8px 28px}
.ti{width:100%;padding:12px 14px;text-align:left;border:none;background:transparent;color:var(--tx);font-size:14px;cursor:pointer;border-radius:12px;display:flex;align-items:center;gap:10px;transition:background .15s;line-height:1.4}
.ti:active{background:var(--ac2)}
.ti.cur{color:var(--acc);font-weight:700}
.tn{font-size:11px;color:var(--sub);min-width:24px;text-align:right;flex-shrink:0}
.tsub{padding-left:22px;font-size:13px;color:var(--sub)}

/* BOOKMARK ITEMS */
.bmi{display:flex;align-items:center;gap:10px;padding:12px 14px;border-radius:12px;border:1px solid var(--bdr);margin-bottom:8px;cursor:pointer;transition:border-color .15s,background .15s}
.bmi:active{background:var(--ac2);border-color:var(--acc)}
.bminfo{flex:1;min-width:0}
.bmlbl{font-size:13px;font-weight:600;color:var(--tx);margin-bottom:2px}
.bmtime{font-size:11px;color:var(--sub)}
.bmdel{width:26px;height:26px;border-radius:50%;border:none;background:transparent;color:var(--sub);font-size:13px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.bmdel:active{background:rgba(239,68,68,.15);color:#ef4444}

/* ERROR */
.eb{margin:60px 16px 0;padding:24px 20px;border:1px solid #7f1d1d;background:rgba(127,29,29,.18);border-radius:20px;text-align:center}

/* TOAST */
#toast{position:fixed;bottom:72px;left:50%;transform:translateX(-50%) translateY(20px);background:rgba(10,10,10,.92);color:#f0f0f0;font-size:13px;padding:10px 20px;border-radius:20px;white-space:nowrap;z-index:200;opacity:0;transition:opacity .25s,transform .25s;pointer-events:none;backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.08)}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.spad{height:20px}
</style>
</head>
<body oncontextmenu="return false;">

<!-- TOP BAR -->
<div id="tBar">
  <a href="/library" id="tBack">&#8592; লাইব্রেরি</a>
  <div id="tMid" onclick="openTOC()" title="অধ্যায়ে যান">
    <div id="tTitle">ইবুক</div>
    <div id="tChInfo" style="display:none"></div>
  </div>
  <div style="display:flex;align-items:center;flex-shrink:0">
    <div id="bmBtn2" class="hd-btn" onclick="saveBookmark()" title="বুকমার্ক">&#128278;</div>
    <div id="qtBtn"  class="hd-btn" onclick="quickToggle()" title="থিম toggle">&#9790;</div>
    <div id="tocBtn" class="hd-btn" onclick="openTOC()"     title="সূচিপত্র">&#9776;</div>
    <div id="aaBtn"  class="hd-btn" onclick="openS()"       title="সেটিং">Aa</div>
  </div>
  <div id="progStrip"><div id="progFill"></div></div>
</div>

<!-- VIEWER -->
<div id="viewer"></div>

<!-- prev/next via tap zones, bookmark in top bar -->

<!-- TOAST -->
<div id="toast"></div>

<!-- BACKDROPS -->
<div id="sBd"   class="pbd" onclick="closeS()"></div>
<div id="tocBd" class="pbd" onclick="closeTOC()"></div>
<div id="bmBd"  class="pbd" onclick="closeBM()"></div>

<!-- SETTINGS PANEL -->
<div id="sP" class="sp">
  <div class="ph"></div>
  <div class="phdr"><span class="pttl">পড়ার সেটিং</span><button class="px" onclick="closeS()">&#10005;</button></div>

  <div class="ss">
    <div class="sl">থিম ও রঙ</div>
    <div class="tgd">
      <button class="tsw" id="th_native"   style="background:linear-gradient(135deg,#ede9e0,#f8f4ef)"  onclick="setTheme('native')">  <span class="tk" style="color:#8b5e20">&#10003;</span><span class="tl" style="color:rgba(100,70,20,.9)">মূল epub</span></button>
      <button class="tsw" id="th_dark"     style="background:linear-gradient(135deg,#1a0800,#050505)"  onclick="setTheme('dark')">    <span class="tk">&#10003;</span><span class="tl">ডার্ক</span></button>
      <button class="tsw" id="th_light"    style="background:linear-gradient(135deg,#f8f8f8,#e8e8e8)"  onclick="setTheme('light')">   <span class="tk" style="color:#333">&#10003;</span><span class="tl" style="color:rgba(0,0,0,.7)">লাইট</span></button>
      <button class="tsw" id="th_sepia"    style="background:linear-gradient(135deg,#f4e4c1,#d4b896)"  onclick="setTheme('sepia')">   <span class="tk" style="color:#3b2f1e">&#10003;</span><span class="tl" style="color:rgba(59,47,30,.8)">সেপিয়া</span></button>
      <button class="tsw" id="th_midnight" style="background:linear-gradient(135deg,#0d1117,#1a2540)"  onclick="setTheme('midnight')"><span class="tk">&#10003;</span><span class="tl">মিডনাইট</span></button>
      <button class="tsw" id="th_forest"   style="background:linear-gradient(135deg,#0a1a0a,#152a15)"  onclick="setTheme('forest')">  <span class="tk">&#10003;</span><span class="tl">ফরেস্ট</span></button>
      <button class="tsw" id="th_sunset"   style="background:linear-gradient(135deg,#3a1400,#1e0800)"  onclick="setTheme('sunset')">  <span class="tk">&#10003;</span><span class="tl">সানসেট</span></button>
      <button class="tsw" id="th_paper"    style="background:linear-gradient(135deg,#fafafa,#eeeeee)"   onclick="setTheme('paper')">   <span class="tk" style="color:#222">&#10003;</span><span class="tl" style="color:rgba(0,0,0,.7)">পেপার</span></button>
    </div>
  </div>

  <div class="ss">
    <div class="sl">ফন্ট সাইজ</div>
    <div class="stc">
      <button class="stb" style="font-size:12px" onclick="stepF(-1)">A&#8722;</button>
      <div class="stv" id="vF">100%</div>
      <button class="stb" style="font-size:18px" onclick="stepF(1)">A+</button>
    </div>
  </div>

  <div class="ss">
    <div class="sl">লাইন স্পেস</div>
    <div class="stc">
      <button class="stb" onclick="stepL(-1)">&#8722;</button>
      <div class="stv" id="vL">স্বাভাবিক</div>
      <button class="stb" onclick="stepL(1)">+</button>
    </div>
  </div>

  <div class="ss">
    <div class="sl">ফন্ট পরিবার — Publisher Default = epub এর নিজস্ব ফন্ট</div>
    <div class="ffg">
      <button class="ffb" id="ff_0" onclick="setFF(0)">Publisher<br>Default</button>
      <button class="ffb" id="ff_1" onclick="setFF(1)">Classic<br>Serif</button>
      <button class="ffb" id="ff_2" onclick="setFF(2)">Modern<br>Sans</button>
    </div>
  </div>

  <div class="ss">
    <div class="sl">পড়ার মোড</div>
    <div class="tgg">
      <button class="tgb" id="fPg" onclick="setFlow('paginated')">&#128196; পাতায় পাতায়</button>
      <button class="tgb" id="fSc" onclick="setFlow('scroll')">&#128220; ক্রমাগত স্ক্রল</button>
    </div>
  </div>

  <div class="ss">
    <div class="sl">মার্জিন (শূন্য = প্রান্ত পর্যন্ত)</div>
    <div class="stc">
      <button class="stb" onclick="stepM(-1)">&#9664;</button>
      <div class="stv" id="vM">সরু</div>
      <button class="stb" onclick="stepM(1)">&#9654;</button>
    </div>
  </div>

  <div class="ss">
    <div class="sl">অতিরিক্ত সেটিং</div>
    <div class="tgg">
      <button class="tgb" id="ahBtn" onclick="toggleAutoHide()">&#128065; অটো বার লুকান</button>
      <button class="tgb" onclick="openBMFromS()">&#128278; বুকমার্ক তালিকা</button>
    </div>
  </div>

  <div class="spad"></div>
</div>

<!-- TOC PANEL -->
<div id="tocP" class="sp">
  <div class="ph"></div>
  <div class="phdr"><span class="pttl">&#128203; সূচিপত্র</span><button class="px" onclick="closeTOC()">&#10005;</button></div>
  <div id="tocList"><p style="padding:20px;text-align:center;color:var(--sub);font-size:13px">&#9200; লোড হচ্ছে...</p></div>
</div>

<!-- BOOKMARK PANEL -->
<div id="bmP" class="sp">
  <div class="ph"></div>
  <div class="phdr"><span class="pttl">&#128278; বুকমার্ক</span><button class="px" onclick="closeBM()">&#10005;</button></div>
  <div style="padding:0 16px 10px">
    <button onclick="saveBookmark()" style="width:100%;padding:12px;background:var(--acc);color:#fff;border:none;border-radius:14px;font-size:14px;font-weight:700;cursor:pointer">
      + এখানে বুকমার্ক দিন
    </button>
  </div>
  <div id="bmList"></div>
</div>

<script>
/* ═══ বইঘর Reader v4 — Complete ══════════════════════════ */

const PKEY = 'boighor_prefs_v4';

/* Preference tables */
const FSIZES  = [70, 80, 90, 100, 112, 125, 145, 165];
const LHEIGHTS = [
  {l:'টাইট',v:1.3},{l:'স্বাভাবিক',v:1.65},{l:'প্রশস্ত',v:1.9},{l:'বেশি',v:2.2}
];
/* Margins now include 0px = edge-to-edge reading */
const MARGINS = [
  {l:'শূন্য',v:0},{l:'সরু',v:5},{l:'স্বাভাবিক',v:16},{l:'প্রশস্ত',v:30}
];
/* Colibrio-style: Publisher Default uses epub's embedded font */
const FFONTS = [
  {l:'Publisher Default',v:'inherit'},
  {l:'Classic Serif',v:"Georgia,'Times New Roman',serif"},
  {l:'Modern Sans',v:"'Helvetica Neue',Arial,sans-serif"}
];

/* epub content colors for themed modes */
const EPUB_C = {
  dark:     {bg:'#0d0d0d',cl:'#e0e0e0',lk:'#f59e0b'},
  light:    {bg:'#f8f8f8',cl:'#1a1a1a',lk:'#1a3a5c'},
  sepia:    {bg:'#f4e4c1',cl:'#3b2f1e',lk:'#8b4513'},
  midnight: {bg:'#0d1117',cl:'#c9d6e3',lk:'#58a6ff'},
  forest:   {bg:'#0e1a0e',cl:'#c8d8b8',lk:'#90c97e'},
  sunset:   {bg:'#1f0d04',cl:'#f0c8a0',lk:'#ff8c42'},
  paper:    {bg:'#fafafa',cl:'#1a1a1a',lk:'#c0392b'}
};

/* UI chrome variables — native theme mirrors epub's warm cream */
const UI_T = {
  native:   {bg:'#f8f4ef',bar:'#ede9e0',bdr:'#d8d4cc',tx:'#1c1c1e',sub:'#888',acc:'#b8741a',ac2:'rgba(184,116,26,.12)'},
  dark:     {bg:'#0d0d0d',bar:'#080808',bdr:'#1e1e1e',tx:'#e2e2e2',sub:'#666',acc:'#f59e0b',ac2:'rgba(245,158,11,.12)'},
  light:    {bg:'#f8f8f8',bar:'#f2f2f2',bdr:'#ddd',   tx:'#1a1a1a',sub:'#666',acc:'#1a3a5c',ac2:'rgba(26,58,92,.1)'},
  sepia:    {bg:'#f4e4c1',bar:'#e8d5a8',bdr:'#cdb896',tx:'#3b2f1e',sub:'#8a6340',acc:'#8b4513',ac2:'rgba(139,69,19,.1)'},
  midnight: {bg:'#0d1117',bar:'#010409',bdr:'#161b22',tx:'#c9d6e3',sub:'#666',acc:'#58a6ff',ac2:'rgba(88,166,255,.1)'},
  forest:   {bg:'#0d1a0d',bar:'#070f07',bdr:'#142014',tx:'#c8d8b8',sub:'#6a9460',acc:'#90c97e',ac2:'rgba(144,201,126,.1)'},
  sunset:   {bg:'#1f0d04',bar:'#130800',bdr:'#2a1200',tx:'#f0c8a0',sub:'#a07040',acc:'#ff8c42',ac2:'rgba(255,140,66,.12)'},
  paper:    {bg:'#fafafa',bar:'#f0f0ef',bdr:'#e0e0de',tx:'#1a1a1a',sub:'#888',acc:'#c0392b',ac2:'rgba(192,57,43,.1)'}
};

/* State */
let prefs = {
  theme:'light',   /* light theme = default, clean look */
  fi:3, li:1, mi:0,
  flow:'scroll',   /* continuous scroll default */
  ff:0,            /* Publisher Default font */
  autoHide:false
};
let book=null, rend=null, barsVisible=true;
let tocDone=false, autoHideTimer=null;
let tocItems=[], curChIdx=-1; // chapter tracker
let pgTimer=null, progTimer=null, curPct=0;

const token    = new URLSearchParams(location.search).get('token');
const viewerEl = document.getElementById('viewer');
const titleEl  = document.getElementById('tTitle');
const pgEl     = null; // progress shown in top bar
const tBar     = document.getElementById('tBar');
const bBar     = null; // removed
const progFill = document.getElementById('progFill');

/* Preferences */
function lpref(){
  try{
    const s=JSON.parse(localStorage.getItem(PKEY)||'{}');
    if(s.theme) prefs.theme=s.theme;
    if(s.fi!==undefined) prefs.fi=s.fi;
    if(s.li!==undefined) prefs.li=s.li;
    if(s.mi!==undefined) prefs.mi=s.mi;
    if(s.flow) prefs.flow=s.flow;
    if(s.ff!==undefined) prefs.ff=s.ff;
    if(s.autoHide!==undefined) prefs.autoHide=s.autoHide;
  }catch(_){}
}
function spref(){try{localStorage.setItem(PKEY,JSON.stringify(prefs));}catch(_){}}

/* Toast */
let toastT=null;
function showToast(msg){
  const t=document.getElementById('toast');
  t.innerHTML=msg; t.classList.add('show');
  clearTimeout(toastT);
  toastT=setTimeout(()=>t.classList.remove('show'),2400);
}

/* Progress */
const PROG_KEY='boighor_pos_'+token;
function saveProgress(loc){
  clearTimeout(progTimer);
  progTimer=setTimeout(()=>{
    if(!loc||!loc.start)return;
    const pct=loc.start.percentage?Math.round(loc.start.percentage*100):0;
    curPct=pct;
    try{
      localStorage.setItem(PROG_KEY,JSON.stringify({cfi:loc.start.cfi,percentage:pct,ts:Date.now()}));
      localStorage.setItem('boighor_lastBook',JSON.stringify({token,title:titleEl.innerText,percentage:pct,ts:Date.now()}));
    }catch(_){}
  },800);
}

/* Chapter display in top bar */
function updateChapterBar(href){
  if(!tocItems.length||!href)return;
  const h=(href||'').split('#')[0].split('/').pop().toLowerCase();
  let best=-1,bestScore=0;
  tocItems.forEach((t,i)=>{
    const th=(t.href||'').split('#')[0].split('/').pop().toLowerCase();
    let score=0;
    if(th===h)score=3;
    else if(th&&h&&(th.includes(h)||h.includes(th)))score=2;
    else if(t.href&&t.href.toLowerCase().includes(h))score=1;
    if(score>bestScore){bestScore=score;best=i;}
  });
  if(best>=0&&best!==curChIdx){
    curChIdx=best;
    const ch=tocItems[best];
    const chEl=document.getElementById('tChInfo');
    const ttEl=document.getElementById('tTitle');
    if(chEl&&ch.label){
      const lbl=ch.label.trim().substring(0,24);
      chEl.innerText='📖 '+lbl+(tocItems.length>1?' ('+( best+1)+'/'+tocItems.length+')':'');
      chEl.style.display='block';
      if(ttEl)ttEl.style.fontSize='10px';
    }
  }
}
function updatePg(loc){
  clearTimeout(pgTimer);
  pgTimer=setTimeout(()=>{
    if(!loc||!loc.start)return;
    const pct=loc.start.percentage?Math.round(loc.start.percentage*100):0;
    curPct=pct;
    if(progFill)progFill.style.width=pct+'%';
    /* Update % in top bar chapter info */
    const chEl=document.getElementById('tChInfo');
    if(chEl&&chEl.style.display!=='none'&&pct>0){
      const txt=chEl.innerText.replace(/ [·]? *[0-9]+%.*$/,'');
      chEl.innerText=txt+(pct>0?' · '+pct+'%':'');
    }
  },250);
}
function getSavedCfi(){
  try{return JSON.parse(localStorage.getItem(PROG_KEY)||'null')?.cfi||null;}
  catch(_){return null;}
}

/* Apply UI chrome variables */
function applyVars(){
  const u=UI_T[prefs.theme]||UI_T.light;
  const r=document.documentElement;
  r.style.setProperty('--bg',u.bg);r.style.setProperty('--bar',u.bar);
  r.style.setProperty('--bdr',u.bdr);r.style.setProperty('--tx',u.tx);
  r.style.setProperty('--sub',u.sub);r.style.setProperty('--acc',u.acc);
  r.style.setProperty('--ac2',u.ac2);
  /* Update Android status bar color */
  const tcm=document.getElementById('themeColorMeta');
  if(tcm)tcm.content=u.bar;
  const qt=document.getElementById('qtBtn');
  if(qt)qt.innerHTML=['dark','midnight','forest','sunset'].includes(prefs.theme)?'&#9728;':'&#9790;';
  /* Sync bookmark button color */
  const bm2=document.getElementById('bmBtn2');
}

/* Apply styles to epub iframe content.
   CRITICAL: in native mode, inject ZERO colors — epub's CSS has full priority.
   Only inject padding/size. For other themes, also inject colors. */
function applyEpub(){
  if(!rend)return;
  const mg=MARGINS[prefs.mi].v, lh=LHEIGHTS[prefs.li].v;
  const fs=FSIZES[prefs.fi], ff=FFONTS[prefs.ff].v;
  const css={
    body:{
      'padding':mg+'px !important',
      'max-width':'100% !important',
      'font-size':fs+'% !important',
      'line-height':lh+' !important'
    },
    p:{'line-height':lh+' !important'},
    img:{'max-width':'100%!important','height':'auto!important'}
  };
  if(ff!=='inherit'){css.body['font-family']=ff+' !important';css.p['font-family']=ff+' !important';}
  if(prefs.theme!=='native'){
    const t=EPUB_C[prefs.theme];
    if(t){
      css.body['background']=t.bg+' !important';
      css.body['color']=t.cl+' !important';
      css.p['color']=t.cl+' !important';
      css['h1']={color:t.cl+' !important'};css['h2']={color:t.cl+' !important'};
      css['h3']={color:t.cl+' !important'};css['h4']={color:t.cl+' !important'};
      css['a']={color:t.lk+' !important'};
    }
  }
  rend.themes.default(css);
}

/* Theme */
function setTheme(t){prefs.theme=t;spref();applyVars();applyEpub();syncUI();}
window.setTheme=setTheme;

/* Quick toggle: light ↔ dark */
let _pdark='dark';
function quickToggle(){
  if(['dark','midnight','forest','sunset'].includes(prefs.theme)){
    _pdark=prefs.theme;setTheme('light');showToast('&#9728; লাইট মোড');
  }else{setTheme(_pdark);showToast('&#9790; ডার্ক মোড');}
  // Update toggle icon
  const qt=document.getElementById('qtBtn');
  if(qt)qt.innerHTML=['dark','midnight','forest','sunset'].includes(prefs.theme)?'&#9728;':'&#9790;';
  /* Sync bookmark button color */
  const bm2=document.getElementById('bmBtn2');
}
window.quickToggle=quickToggle;

/* Step controls */
function stepF(d){prefs.fi=Math.max(0,Math.min(FSIZES.length-1,prefs.fi+d));spref();syncUI();applyEpub();}
function stepL(d){prefs.li=Math.max(0,Math.min(LHEIGHTS.length-1,prefs.li+d));spref();syncUI();applyEpub();}
function stepM(d){prefs.mi=Math.max(0,Math.min(MARGINS.length-1,prefs.mi+d));spref();syncUI();applyEpub();}
window.stepF=stepF;window.stepL=stepL;window.stepM=stepM;

/* Font family */
function setFF(i){prefs.ff=i;spref();syncUI();applyEpub();showToast('ফন্ট: '+FFONTS[i].l);}
window.setFF=setFF;

/* Auto-hide bars */
function toggleAutoHide(){
  prefs.autoHide=!prefs.autoHide;spref();syncUI();
  showToast(prefs.autoHide?'&#128065; অটো-হাইড চালু':'&#128065; অটো-হাইড বন্ধ');
  if(!prefs.autoHide){clearTimeout(autoHideTimer);if(!barsVisible)toggleBars();}
  else scheduleAH();
}
function scheduleAH(){
  clearTimeout(autoHideTimer);
  if(prefs.autoHide&&barsVisible)autoHideTimer=setTimeout(()=>{if(barsVisible)toggleBars();},4000);
}
function resetAH(){if(!barsVisible&&prefs.autoHide)toggleBars();scheduleAH();}
window.toggleAutoHide=toggleAutoHide;

/* Flow switch — rebuilds renderer with continuous manager for full-book scroll */
async function setFlow(nf){
  if(prefs.flow===nf||!book)return;
  prefs.flow=nf;spref();syncUI();
  let cfi=null;
  try{const l=rend&&rend.currentLocation();if(l?.start)cfi=l.start.cfi;}catch(_){}
  viewerEl.innerHTML='';
  if(rend){try{rend.destroy();}catch(_){}rend=null;}
  rend=book.renderTo('viewer',buildOpts(nf==='scroll'));
  applyEpub();
  await rend.display(cfi||undefined);
  bindEv();
}
window.setFlow=setFlow;

function buildOpts(isScroll){
  /* continuous manager = entire book is one scrollable document (fixes per-chapter scroll) */
  const o={width:'100%',height:'100%',spread:'none',flow:isScroll?'scrolled':'paginated'};
  if(isScroll)o.manager='continuous';
  return o;
}

/* Bar toggle — only top bar (no jump since viewer bottom stays at 0) */
function toggleBars(){
  barsVisible=!barsVisible;
  tBar.classList.toggle('bar-hidden',!barsVisible);
  /* Delay resize by animation duration to avoid page jump */
  if(rend){const loc=rend.currentLocation&&rend.currentLocation();setTimeout(()=>{try{rend.resize();if(loc?.start?.cfi)setTimeout(()=>rend.display(loc.start.cfi).catch(()=>{}),80);}catch(_){}},340);}
  if(barsVisible&&prefs.autoHide)scheduleAH();
}

/* Settings panel */
function openS() {document.getElementById('sP').classList.add('on'); document.getElementById('sBd').classList.add('on'); clearTimeout(autoHideTimer);}
function closeS(){document.getElementById('sP').classList.remove('on');document.getElementById('sBd').classList.remove('on');scheduleAH();}
window.openS=openS;window.closeS=closeS;

/* TOC panel */
function openTOC() {document.getElementById('tocP').classList.add('on'); document.getElementById('tocBd').classList.add('on'); clearTimeout(autoHideTimer);if(!tocDone&&book)buildTOC();}
function closeTOC(){document.getElementById('tocP').classList.remove('on');document.getElementById('tocBd').classList.remove('on');scheduleAH();}
window.openTOC=openTOC;window.closeTOC=closeTOC;

async function buildTOC(){
  tocDone=true;
  const list=document.getElementById('tocList');
  try{
    await book.ready;
    const toc=book.navigation?.toc||[];
    list.innerHTML='';
    if(!toc.length){list.innerHTML='<p style="padding:20px;text-align:center;color:var(--sub);font-size:13px">সূচিপত্র পাওয়া যায়নি।</p>';return;}
    let idx=0;
    function addItem(item,indent){
      idx++;const n=idx;
      const btn=document.createElement('button');
      btn.className='ti'+(indent?' tsub':'');
      btn.innerHTML=(indent?'':'<span class="tn">'+n+'.</span>')+'<span style="flex:1">'+(item.label||'').trim()+'</span>';
      btn.onclick=()=>{if(rend&&item.href){rend.display(item.href).catch(()=>{});closeTOC();}};
      list.appendChild(btn);
      (item.subitems||[]).forEach(s=>addItem(s,true));
    }
    toc.forEach(item=>addItem(item,false));
    /* Populate flat TOC for chapter bar */
    tocItems=[];
    function flatTOC(items){items.forEach(it=>{tocItems.push({label:it.label||'',href:it.href||''});if(it.subitems)flatTOC(it.subitems);});}
    flatTOC(toc);
  }catch(e){list.innerHTML='<p style="padding:16px;color:var(--sub);font-size:12px">লোড হয়নি।</p>';}
}

/* Bookmark panel */
const BM_KEY='boighor_bm_'+token;
function getBMs(){try{return JSON.parse(localStorage.getItem(BM_KEY)||'[]');}catch(_){return[];}}

function saveBookmark(){
  try{
    const loc=rend&&rend.currentLocation();
    if(!loc||!loc.start){showToast('&#9888; অবস্থান পাওয়া যায়নি।');return;}
    const bms=getBMs();
    const now=new Date();
    const ds=now.toLocaleString('bn-BD',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
    bms.unshift({cfi:loc.start.cfi,label:(curPct>0?curPct+'% — ':'')+ds,pct:curPct,ts:Date.now()});
    if(bms.length>15)bms.splice(15);
    localStorage.setItem(BM_KEY,JSON.stringify(bms));
    const _bm=document.getElementById('bmBtn2');
    if(_bm){_bm.style.color='var(--acc)';_bm.style.background='var(--ac2)';setTimeout(()=>{_bm.style.color='';_bm.style.background='';},2000);}
    showToast('&#128278; বুকমার্ক সংরক্ষিত!');
    renderBMs();
  }catch(e){showToast('&#9888; সংরক্ষণ হয়নি।');}
}

function renderBMs(){
  const list=document.getElementById('bmList');
  const bms=getBMs();
  if(!bms.length){list.innerHTML='<p style="padding:16px;text-align:center;color:var(--sub);font-size:13px">কোনো বুকমার্ক নেই।<br>&#128278; বোতাম দিয়ে বুকমার্ক দিন।</p>';return;}
  list.innerHTML='';
  bms.forEach((bm,i)=>{
    const row=document.createElement('div');row.className='bmi';
    row.innerHTML='<div class="bminfo"><div class="bmlbl">&#128278; '+(bm.label||'বুকমার্ক '+(i+1))+'</div><div class="bmtime">'+(bm.pct||0)+'% অবস্থান</div></div><button class="bmdel" onclick="delBM('+i+')" title="মুছুন">&#128465;</button>';
    row.querySelector('.bminfo').onclick=()=>{if(rend&&bm.cfi){rend.display(bm.cfi).catch(()=>{});closeBM();}};
    list.appendChild(row);
  });
}

function delBM(i){const bms=getBMs();bms.splice(i,1);localStorage.setItem(BM_KEY,JSON.stringify(bms));renderBMs();showToast('বুকমার্ক মুছা হয়েছে।');}
window.delBM=delBM;

function openBM() {document.getElementById('bmP').classList.add('on'); document.getElementById('bmBd').classList.add('on'); clearTimeout(autoHideTimer);renderBMs();}
function closeBM(){document.getElementById('bmP').classList.remove('on');document.getElementById('bmBd').classList.remove('on');scheduleAH();}
function openBMFromS(){closeS();setTimeout(openBM,350);}
window.openBM=openBM;window.closeBM=closeBM;window.openBMFromS=openBMFromS;

/* Sync all UI to prefs */
function syncUI(){
  ['native','dark','light','sepia','midnight','forest','sunset','paper'].forEach(t=>{
    const el=document.getElementById('th_'+t);if(el)el.classList.toggle('on',prefs.theme===t);
  });
  document.getElementById('fPg').classList.toggle('on',prefs.flow==='paginated');
  document.getElementById('fSc').classList.toggle('on',prefs.flow==='scroll');
  document.getElementById('vF').innerText=FSIZES[prefs.fi]+'%';
  document.getElementById('vL').innerText=LHEIGHTS[prefs.li].l;
  document.getElementById('vM').innerText=MARGINS[prefs.mi].l;
  [0,1,2].forEach(i=>{const el=document.getElementById('ff_'+i);if(el)el.classList.toggle('on',prefs.ff===i);});
  const ah=document.getElementById('ahBtn');if(ah)ah.classList.toggle('on',prefs.autoHide);
}

/* Error UI */
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function showErr(msg){
  titleEl.innerText='ত্রুটি';
  const isCDN=/JSZip|EPUB reader|CDN/i.test(msg||'');
  viewerEl.innerHTML='<div class="eb"><div style="font-size:48px;margin-bottom:14px">&#9888;</div><h2 style="font-weight:700;color:#fca5a5;margin:0 0 10px">বইটি খোলা যাচ্ছে না</h2><p style="font-size:13px;color:#a1a1aa;margin:0 0 20px;line-height:1.6">'+esc(msg)+'</p>'+(isCDN?'<button onclick="location.reload()" style="padding:11px 20px;background:#d97706;color:#fff;border:0;border-radius:12px;font-size:14px;cursor:pointer">&#128260; আবার চেষ্টা করুন</button>':'<button onclick="delBad()" style="padding:11px 20px;background:#dc2626;color:#fff;border:0;border-radius:12px;font-size:14px;cursor:pointer">&#128465; মুছে আবার Import</button>')+'<br><a href="/library" style="display:inline-block;margin-top:18px;color:var(--acc);font-size:13px">&#8592; লাইব্রেরিতে ফিরুন</a></div>';
}

/* IndexedDB */
async function openDB(){
  return new Promise((res,rej)=>{
    const q=indexedDB.open('BoighorDB',1);
    q.onupgradeneeded=e=>{const d=e.target.result;if(!d.objectStoreNames.contains('books'))d.createObjectStore('books',{keyPath:'token'});};
    q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
  });
}
async function delBad(){
  try{const db=await openDB();await new Promise((res,rej)=>{const tx=db.transaction('books','readwrite');tx.objectStore('books').delete(token);tx.oncomplete=res;tx.onerror=()=>rej(tx.error);});}catch(e){}
  location.href='/library';
}
window.delBad=delBad;

/* Keyboard */
document.addEventListener('keydown',e=>{
  const k=(e.key||'').toLowerCase();
  if(e.ctrlKey&&['p','s','u','c','a'].includes(k))e.preventDefault();
  if(e.key==='F12')e.preventDefault();
  if(e.ctrlKey&&e.shiftKey&&['i','j','c','s'].includes(k))e.preventDefault();
  if(e.key==='PrintScreen')e.preventDefault();
  if(e.key==='Escape'){closeS();closeTOC();closeBM();}
  if(!rend)return;
  if(e.key==='ArrowLeft')rend.prev();
  if(e.key==='ArrowRight')rend.next();
  if(e.key==='b'||e.key==='B')saveBookmark();
});
/* Block copy/cut/selection globally */
document.addEventListener('copy',e=>e.preventDefault());
document.addEventListener('cut',e=>e.preventDefault());
document.addEventListener('selectstart',e=>e.preventDefault());
/* Inject anti-copy into epub iframes after render */
function injectAntiCopy(){
  try{
    const frames=document.querySelectorAll('#viewer iframe');
    frames.forEach(fr=>{
      try{
        const d=fr.contentDocument;
        if(!d)return;
        let s=d.getElementById('_bc_');
        if(!s){s=d.createElement('style');s.id='_bc_';d.head&&d.head.appendChild(s);}
        s.textContent='*{user-select:none!important;-webkit-user-select:none!important}';
        d.addEventListener('copy',e=>e.preventDefault(),true);
        d.addEventListener('cut',e=>e.preventDefault(),true);
        d.addEventListener('contextmenu',e=>e.preventDefault(),true);
        d.addEventListener('selectstart',e=>e.preventDefault(),true);
      }catch(_){}
    });
  }catch(_){}
}
setInterval(injectAntiCopy,1200);

/* Bind rendition events */
function bindEv(){
  rend.on('relocated',loc=>{
    updatePg(loc);saveProgress(loc);
    if(prefs.autoHide)scheduleAH();
    if(loc&&loc.start&&loc.start.href)updateChapterBar(loc.start.href);
  });
  /* Tap zones: paginated=left/center/right, scroll=anywhere=toggle */
  rend.on('click',e=>{
    const x=(e&&e.clientX!=null)?e.clientX:innerWidth/2;
    const w=innerWidth;
    if(prefs.flow==='paginated'){
      if(x<w*.28){rend.prev();return;}
      if(x>w*.72){rend.next();return;}
    }
    resetAH();toggleBars();
  });
  /* Swipe for paginated mode */
  let _tx=0,_ty=0;
  viewerEl.addEventListener('touchstart',e=>{_tx=e.touches[0].clientX;_ty=e.touches[0].clientY;},{passive:true});
  viewerEl.addEventListener('touchend',e=>{
    const dx=e.changedTouches[0].clientX-_tx;
    const dy=Math.abs(e.changedTouches[0].clientY-_ty);
    if(dy>60)return; // vertical scroll, ignore
    if(prefs.flow!=='paginated')return;
    if(Math.abs(dx)>55){if(dx<0)rend.next();else rend.prev();}
  },{passive:true});
}

/* Service Worker */
if('serviceWorker' in navigator){
  addEventListener('load',()=>{navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(()=>{});});
}

/* Init */
async function init(){
  try{
    if(!token)throw new Error('Token missing.');
    if(!window.JSZip)throw new Error('JSZip লোড হয়নি। ইন্টারনেট সংযোগ পরীক্ষা করুন।');
    if(!window.ePub) throw new Error('EPUB library লোড হয়নি। ইন্টারনেট সংযোগ পরীক্ষা করুন।');
    lpref();applyVars();syncUI();
    titleEl.innerText='⏳';
    const db=await openDB();
    const saved=await new Promise((res,rej)=>{
      const tx=db.transaction('books','readonly');
      const q=tx.objectStore('books').get(token);
      q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
    });
    if(!saved)throw new Error('বইটি এই ডিভাইসে নেই। লাইব্রেরি থেকে Import করুন।');
    if(!saved.blob||typeof saved.blob.arrayBuffer!=='function')throw new Error('বইয়ের ডেটা নষ্ট। মুছে আবার Import করুন।');
    titleEl.innerText=saved.title||'ইবুক';
    titleEl.innerText='⚙️';
    const ab=await saved.blob.arrayBuffer();
    if(!ab||ab.byteLength<10)throw new Error('ফাইলটি খালি বা ক্ষতিগ্রস্ত।');
    const hd=new Uint8Array(ab.slice(0,4));
    if(!(hd[0]===0x50&&hd[1]===0x4B))throw new Error('সঠিক EPUB নয়। মুছে আবার Import করুন।');
    book=ePub(ab);
    const isScroll=prefs.flow==='scroll';
    rend=book.renderTo('viewer',buildOpts(isScroll));
    /* epub's own CSS has full priority in native mode */
    applyEpub();
    const savedCfi=getSavedCfi();
    titleEl.innerText=saved.title||'ইবুক';
    await rend.display(savedCfi||undefined);
    // reading started;
    bindEv();
    try{localStorage.setItem('boighor_lastBook',JSON.stringify({token,title:saved.title||'ইবুক',percentage:0,ts:Date.now()}));}catch(_){}
    if(prefs.autoHide)scheduleAH();
    book.ready.catch(()=>{});
  }catch(e){
    console.error(e);showErr(e.message||'অজানা ত্রুটি।');
  }
}
addEventListener('resize',()=>{if(rend)rend.resize();});
init();
</script>
</body>
</html>
"""


@app.route("/reader")
def reader():
    return render_template_string(READER_HTML, store=STORE_NAME)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
