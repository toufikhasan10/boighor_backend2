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
# Purchase / Restore Helpers  (NEW)
# ══════════════════════════════════════════════════════════════

def safe_email(email):
    """Convert email to a Firebase-safe path segment."""
    return (email.lower().strip()
            .replace("@", "_at_")
            .replace(".", "_dot_")
            .replace("+", "_plus_")
            .replace("-", "_dash_"))


def save_purchase_record(email, book_title, drive_link, buyer_name, book_id=""):
    """Write a purchase record to purchases/{safe_email}/{uuid12}."""
    se  = safe_email(email)
    key = uuid.uuid4().hex[:12]   # always unique — supports repeat purchases
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
    """Send OTP by reusing the existing GAS sendEmail action."""
    if not GAS_URL:
        return False
    try:
        params = urllib.parse.urlencode({
            "action": "sendEmail",
            "to":     to_email,
            "name":   "ক্রেতা",
            "book":   f"{STORE_NAME} — লাইব্রেরি Restore",
            "link":   f"আপনার কোড: {otp}  (মেয়াদ ১০ মিনিট)",
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
        "name":             STORE_NAME,
        "short_name":       STORE_NAME,
        "start_url":        "/library",
        "display":          "standalone",
        "background_color": "#1a1a1a",
        "theme_color":      "#b8741a",
        "icons": [{"src": "https://cdn-icons-png.flaticon.com/512/3389/3389037.png",
                   "sizes": "512x512", "type": "image/png"}],
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok",
                    "firebase": "✅" if fb_get("ping") is not False else "⚠️"})

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
        return jsonify({"success": False, "error": "Firebase Error", "retry": True}), 503

    # NEW: persist purchase so the buyer can restore later
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
# Restore OTP Routes  (NEW)
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

    fb_delete(f"otp/{se}")  # one-time use

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
# Library  (UPDATED — restore modal)
# ══════════════════════════════════════════════════════════════

LIBRARY_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>আমার লাইব্রেরি — {{ store }}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="manifest" href="/manifest.json">
<style>
  body { background:#121212; color:#e0e0e0; font-family:'Segoe UI',sans-serif; }
  .book-card { transition:transform .2s; cursor:pointer; }
  .book-card:active { transform:scale(.95); }
</style>
</head>
<body class="p-4">

<header class="flex justify-between items-center mb-8 pt-4">
  <h1 class="text-2xl font-bold text-amber-500">{{ store }} লাইব্রেরি</h1>
  <div class="text-xs bg-amber-900/30 text-amber-200 px-3 py-1 rounded-full border border-amber-700">প্রিমিয়াম</div>
</header>

<div id="bookshelf" class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4"></div>

<div id="empty-state" class="hidden text-center py-16">
  <div class="text-5xl mb-4">📚</div>
  <p class="text-zinc-500 mb-6">আপনার লাইব্রেরিতে এখনো কোনো বই নেই।</p>
  <button onclick="openRestore()"
    class="px-6 py-3 bg-amber-600 hover:bg-amber-500 text-white rounded-xl font-semibold text-sm transition-colors">
    🔄 Restore করুন
  </button>
  <p class="text-xs text-zinc-600 mt-3">আগে কিনে থাকলে ইমেইল দিয়ে Restore করুন</p>
</div>

<!-- ══ Restore Modal ══════════════════════════════════════════ -->
<div id="rModal" class="hidden fixed inset-0 bg-black/80 z-50 flex items-end">
  <div class="bg-zinc-900 w-full rounded-t-2xl p-6 max-h-[90vh] overflow-y-auto">

    <div class="w-10 h-1 bg-zinc-700 rounded-full mx-auto mb-4"></div>
    <div class="flex justify-between items-center mb-5">
      <h2 class="text-lg font-bold text-white">লাইব্রেরি Restore করুন</h2>
      <button onclick="closeRestore()" class="text-zinc-400 text-2xl leading-none">✕</button>
    </div>

    <!-- Step 1 -->
    <div id="rs1">
      <p class="text-sm text-zinc-400 mb-4">কেনার সময় যে ইমেইল দিয়েছিলেন সেটি দিন। OTP পাঠানো হবে।</p>
      <input id="rEmail" type="email" placeholder="আপনার ইমেইল..."
        class="w-full bg-zinc-800 border border-zinc-700 rounded-xl px-4 py-3 text-white text-sm mb-4 outline-none focus:border-amber-500"
        onkeydown="if(event.key==='Enter')doOTP()">
      <button onclick="doOTP()"
        class="w-full py-3 bg-amber-600 hover:bg-amber-500 text-white rounded-xl font-semibold text-sm transition-colors">
        OTP পাঠান
      </button>
      <p id="rs1msg" class="text-sm mt-3 text-center hidden"></p>
    </div>

    <!-- Step 2 -->
    <div id="rs2" class="hidden">
      <p class="text-sm text-zinc-400 mb-1">ইমেইলে পাঠানো ৬ সংখ্যার কোডটি লিখুন।</p>
      <p id="rs2email" class="text-xs text-amber-400 mb-4"></p>
      <input id="rOTP" type="number" placeholder="123456"
        class="w-full bg-zinc-800 border border-zinc-700 rounded-xl px-4 py-3 text-white text-2xl tracking-widest text-center mb-4 outline-none focus:border-amber-500"
        oninput="if(this.value.length>6)this.value=this.value.slice(0,6)"
        onkeydown="if(event.key==='Enter')doVerify()">
      <button onclick="doVerify()"
        class="w-full py-3 bg-amber-600 hover:bg-amber-500 text-white rounded-xl font-semibold text-sm transition-colors">
        নিশ্চিত করুন
      </button>
      <button onclick="backStep1()" class="w-full py-2 text-zinc-500 text-sm mt-2">← ইমেইল পরিবর্তন</button>
      <p id="rs2msg" class="text-sm mt-3 text-center hidden"></p>
    </div>

    <!-- Step 3 -->
    <div id="rs3" class="hidden">
      <p class="text-sm text-zinc-400 mb-4">আপনার কেনা বইসমূহ নামানো হচ্ছে…</p>
      <div id="rList" class="space-y-2 mb-4"></div>
      <div id="rDone" class="hidden text-center py-4">
        <div class="text-4xl mb-2">✅</div>
        <p class="text-green-400 font-semibold">সফলভাবে Restore হয়েছে!</p>
      </div>
    </div>

  </div>
</div>

<script>
const DB   = 'BoighorDB';
const OSK  = 'books';
let rEmail = '';
let rBooks = [];

function esc(s) {
  return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

/* ─ IndexedDB ───────────────────────────────────────────── */
async function openDB() {
  return new Promise((res,rej)=>{
    const q=indexedDB.open(DB,1);
    q.onupgradeneeded=e=>{const d=e.target.result;if(!d.objectStoreNames.contains(OSK))d.createObjectStore(OSK,{keyPath:'token'});};
    q.onsuccess=()=>res(q.result); q.onerror=()=>rej(q.error);
  });
}

/* ─ Library loader ──────────────────────────────────────── */
async function loadLibrary() {
  try {
    const db=await openDB();
    const books=await new Promise((res,rej)=>{
      const tx=db.transaction(OSK,'readonly');
      const q=tx.objectStore(OSK).getAll();
      q.onsuccess=()=>res(q.result||[]); q.onerror=()=>rej(q.error);
    });
    const shelf=document.getElementById('bookshelf');
    const empty=document.getElementById('empty-state');
    shelf.innerHTML='';
    if(!books.length){empty.classList.remove('hidden');return;}
    empty.classList.add('hidden');
    books.forEach(b=>{
      const d=document.createElement('div');
      d.className='book-card bg-zinc-900 p-3 rounded-xl border border-zinc-800 text-center';
      d.innerHTML=`<div class="aspect-[3/4] bg-zinc-800 rounded-lg mb-3 flex items-center justify-center text-3xl shadow-inner">📘</div>
        <div class="text-sm font-medium truncate">${esc(b.title||'ইবুক')}</div>`;
      d.onclick=()=>{window.location.href=`/reader?token=${encodeURIComponent(b.token)}`;};
      shelf.appendChild(d);
    });
  } catch(e) {
    console.error(e);
    const empty=document.getElementById('empty-state');
    empty.classList.remove('hidden');
    empty.innerHTML='<p class="text-red-400">লাইব্রেরি লোড করা যায়নি।</p>';
  }
}

/* ─ Modal helpers ───────────────────────────────────────── */
function openRestore()  {document.getElementById('rModal').classList.remove('hidden');}
function closeRestore() {document.getElementById('rModal').classList.add('hidden');}
window.openRestore=openRestore; window.closeRestore=closeRestore;

function msg(id,text,err){
  const el=document.getElementById(id);
  el.innerText=text; el.className=`text-sm mt-3 text-center ${err?'text-red-400':'text-amber-400'}`;
  el.classList.remove('hidden');
}
function backStep1(){
  document.getElementById('rs2').classList.add('hidden');
  document.getElementById('rs1').classList.remove('hidden');
  document.getElementById('rs1msg').classList.add('hidden');
}
window.backStep1=backStep1;

/* ─ Step 1 : request OTP ────────────────────────────────── */
async function doOTP(){
  const email=(document.getElementById('rEmail').value||'').trim();
  if(!email)return;
  rEmail=email; msg('rs1msg','⏳ পাঠানো হচ্ছে…',false);
  try{
    const r=await fetch('/request-restore-otp',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email}),
    });
    const d=await r.json();
    if(d.success){
      document.getElementById('rs1').classList.add('hidden');
      document.getElementById('rs2email').innerText=`ইমেইল: ${rEmail}`;
      document.getElementById('rs2').classList.remove('hidden');
    } else { msg('rs1msg',d.error||'ত্রুটি হয়েছে।',true); }
  } catch(_){ msg('rs1msg','ইন্টারনেট সংযোগ পরীক্ষা করুন।',true); }
}
window.doOTP=doOTP;

/* ─ Step 2 : verify OTP ─────────────────────────────────── */
async function doVerify(){
  const otp=(document.getElementById('rOTP').value||'').trim();
  if(otp.length!==6){msg('rs2msg','৬ সংখ্যার কোড দিন।',true);return;}
  msg('rs2msg','⏳ যাচাই করা হচ্ছে…',false);
  try{
    const r=await fetch('/verify-restore-otp',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:rEmail,otp}),
    });
    const d=await r.json();
    if(d.success&&d.books&&d.books.length){
      rBooks=d.books;
      document.getElementById('rs2').classList.add('hidden');
      buildList(); document.getElementById('rs3').classList.remove('hidden');
      downloadAll();
    } else if(d.success){
      msg('rs2msg','কোনো বইয়ের তথ্য পাওয়া যায়নি।',true);
    } else { msg('rs2msg',d.error||'OTP ভুল বা মেয়াদ শেষ।',true); }
  } catch(_){ msg('rs2msg','ইন্টারনেট সংযোগ পরীক্ষা করুন।',true); }
}
window.doVerify=doVerify;

function buildList(){
  const list=document.getElementById('rList');
  list.innerHTML='';
  rBooks.forEach((b,i)=>{
    const d=document.createElement('div');
    d.className='flex justify-between items-center p-3 bg-zinc-800 rounded-xl';
    d.innerHTML=`<span class="text-sm text-zinc-200 truncate flex-1 pr-3">${esc(b.title||'ইবুক')}</span>
      <span id="rs_${i}" class="text-xs text-zinc-500 whitespace-nowrap">অপেক্ষা…</span>`;
    list.appendChild(d);
  });
}

/* ─ Step 3 : download each book inline ──────────────────── */
async function downloadAll(){
  const db=await openDB();
  for(let i=0;i<rBooks.length;i++){
    const b=rBooks[i];
    const st=document.getElementById(`rs_${i}`);
    if(!st)continue;
    try{
      st.innerText='⬇️ শুরু…'; st.className='text-xs text-amber-400 whitespace-nowrap';
      const resp=await fetch(`/stream-ebook/${encodeURIComponent(b.token)}`,{cache:'no-store'});
      if(!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const rdr=resp.body.getReader();
      const cl=+(resp.headers.get('Content-Length')||0);
      let recv=0,chunks=[];
      while(true){
        const{done,value}=await rdr.read();
        if(done)break;
        chunks.push(value); recv+=value.length;
        st.innerText=cl?Math.round(recv/cl*100)+'%':Math.round(recv/1024)+' KB';
      }
      const blob=new Blob(chunks,{type:'application/epub+zip'});
      const hd=new Uint8Array(await blob.slice(0,4).arrayBuffer());
      if(!(hd[0]===0x50&&hd[1]===0x4B)) throw new Error('Invalid EPUB');
      st.innerText='💾 সেভ হচ্ছে…';
      await new Promise((res,rej)=>{
        const tx=db.transaction(OSK,'readwrite');
        tx.objectStore(OSK).put({token:b.token,title:b.title,format:'epub',blob:blob,added_at:new Date().toISOString()});
        tx.oncomplete=res; tx.onerror=()=>rej(tx.error);
      });
      try{await fetch(`/mark-used/${encodeURIComponent(b.token)}`,{method:'POST'});}catch(_){}
      st.innerText='✅ সেভ হয়েছে'; st.className='text-xs text-green-400 whitespace-nowrap';
    } catch(e){
      console.error(e);
      st.innerText='❌ ব্যর্থ'; st.className='text-xs text-red-400 whitespace-nowrap';
    }
  }
  document.getElementById('rDone').classList.remove('hidden');
  setTimeout(()=>{closeRestore();loadLibrary();},1500);
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
            return "⌛ মেয়াদ শেষ।", 410
    except Exception:
        pass

    book_title = t.get("book_title", "ইবুক")
    return render_template_string("""
<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>বইটি সংগ্রহ করা হচ্ছে — {{ store }}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
@keyframes pc{0%,100%{opacity:1}50%{opacity:.5}}
.apc{animation:pc 2s cubic-bezier(.4,0,.6,1) infinite}
</style>
</head>
<body class="bg-zinc-950 text-white flex items-center justify-center min-h-screen p-6">
<div class="text-center max-w-sm">
  <div class="text-6xl mb-6 animate-bounce">📥</div>
  <h1 class="text-2xl font-bold mb-2">{{ title }}</h1>
  <p class="text-zinc-400 mb-8">আপনার লাইব্রেরিতে EPUB বইটি সেভ করা হচ্ছে। অনুগ্রহ করে অপেক্ষা করুন…</p>
  <div class="w-full bg-zinc-800 h-2 rounded-full overflow-hidden mb-4">
    <div id="prog" class="bg-amber-500 h-full w-0 transition-all duration-300"></div>
  </div>
  <div id="stat" class="text-xs text-zinc-500 uppercase tracking-widest apc">Connecting to secure stream...</div>
  <button id="retry" class="hidden mt-6 px-4 py-2 bg-amber-600 rounded-lg text-white" onclick="location.reload()">আবার চেষ্টা করুন</button>
</div>
<script>
const TOKEN={{ token|tojson }};
const TITLE={{ title|tojson }};
function fail(m){const s=document.getElementById('stat');s.innerText=m;s.classList.add('text-red-500');document.getElementById('retry').classList.remove('hidden');}
async function run(){
  const prog=document.getElementById('prog'),stat=document.getElementById('stat');
  try{
    stat.innerText="Downloading secure EPUB...";
    const resp=await fetch(`/stream-ebook/${encodeURIComponent(TOKEN)}`,{cache:'no-store'});
    if(!resp.ok){const t=await resp.text().catch(()=>'');throw new Error(t||`Stream failed (${resp.status})`);}
    const rdr=resp.body.getReader();
    const cl=+(resp.headers.get('Content-Length')||0);
    let recv=0,chunks=[];
    while(true){
      const{done,value}=await rdr.read();
      if(done)break;
      chunks.push(value);recv+=value.length;
      if(cl){const p=Math.min(100,Math.round(recv/cl*100));prog.style.width=p+'%';stat.innerText=`Downloading: ${p}%`;}
      else{prog.style.width='60%';stat.innerText=`Downloading: ${Math.round(recv/1024)} KB`;}
    }
    const blob=new Blob(chunks,{type:'application/epub+zip'});
    const hd=new Uint8Array(await blob.slice(0,4).arrayBuffer());
    if(!(hd[0]===0x50&&hd[1]===0x4B)) throw new Error('Downloaded file is not a valid EPUB. Google Drive link/share setting check করুন।');
    prog.style.width='90%';stat.innerText="Saving to library...";
    const db=await new Promise((res,rej)=>{
      const q=indexedDB.open('BoighorDB',1);
      q.onupgradeneeded=e=>{const d=e.target.result;if(!d.objectStoreNames.contains('books'))d.createObjectStore('books',{keyPath:'token'});};
      q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
    });
    await new Promise((res,rej)=>{
      const tx=db.transaction('books','readwrite');
      tx.objectStore('books').put({token:TOKEN,title:TITLE,format:'epub',blob:blob,added_at:new Date().toISOString()});
      tx.oncomplete=res;tx.onerror=()=>rej(tx.error);tx.onabort=()=>rej(tx.error||new Error('IndexedDB save aborted'));
    });
    prog.style.width='100%';stat.innerText="Success! Redirecting...";
    try{await fetch(`/mark-used/${encodeURIComponent(TOKEN)}`,{method:'POST'});}catch(_){}
    setTimeout(()=>{window.location.href='/library';},700);
  }catch(e){console.error(e);fail(e.message||'Error occurred. Please try again.');}
}
run();
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
    if t.get("used"):
        return "এই লিংক আগেই ব্যবহার করা হয়েছে।", 403

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
    fb_set(f"tokens/{token}/used", True)
    fb_set(f"tokens/{token}/used_at", datetime.now(timezone.utc).isoformat())
    return jsonify({"ok": True})


@app.route("/stream-pdf/<token>")
def stream_pdf_backward_compat(token):
    return stream_ebook(token)

# ══════════════════════════════════════════════════════════════
# EPUB Reader  (UPDATED — Colibrio-like settings panel)
# ══════════════════════════════════════════════════════════════

READER_HTML = """
<!DOCTYPE html>
<html lang="bn" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>রিডার — {{ store }}</title>
<script src="https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/epubjs@0.3.93/dist/epub.min.js"></script>
<style>
/* ── Theme variables ───────────────────────────────────── */
:root {
  --bg:#1a1a1a; --bar:#18181b; --bdr:#27272a;
  --tx:#e5e5e5; --sub:#a1a1aa; --acc:#f59e0b; --ac2:rgba(245,158,11,.15);
}
[data-theme="light"] {
  --bg:#f0ece4; --bar:#e4e0d8; --bdr:#d4cfc7;
  --tx:#1c1c1e; --sub:#6b6b70; --acc:#d97706; --ac2:rgba(217,119,6,.1);
}
[data-theme="sepia"] {
  --bg:#f4e4c1; --bar:#e8d5a8; --bdr:#d4bf94;
  --tx:#3b2f1e; --sub:#7a6040; --acc:#8b4513; --ac2:rgba(139,69,19,.1);
}
*,*::before,*::after{box-sizing:border-box;}
html,body{margin:0;padding:0;height:100%;background:var(--bg);color:var(--tx);
  overflow:hidden;font-family:'Segoe UI',sans-serif;transition:background .25s,color .25s;}
body{user-select:none;-webkit-user-select:none;}
@media print{body{display:none !important;}}

/* top bar */
#tBar{position:fixed;top:0;left:0;right:0;height:54px;background:var(--bar);
  border-bottom:1px solid var(--bdr);display:flex;align-items:center;
  justify-content:space-between;padding:0 12px;z-index:50;transition:background .25s,border-color .25s;}
#tBack{color:var(--acc);font-size:13px;font-weight:700;text-decoration:none;
  display:flex;align-items:center;gap:4px;padding:8px 4px;white-space:nowrap;}
#tTitle{font-size:12px;color:var(--sub);flex:1;text-align:center;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 8px;}
#aaBtn{width:36px;height:36px;border-radius:50%;background:var(--ac2);
  border:1.5px solid var(--acc);color:var(--acc);font-size:13px;font-weight:700;
  display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;}
#aaBtn:active{background:rgba(245,158,11,.35);}

/* viewer */
#viewer{position:fixed;top:54px;left:0;right:0;bottom:52px;background:var(--bg);}

/* bottom bar */
#bBar{position:fixed;left:0;right:0;bottom:0;height:52px;background:var(--bar);
  border-top:1px solid var(--bdr);display:flex;align-items:center;
  justify-content:space-between;padding:0 12px;transition:background .25s,border-color .25s;}
.nb{width:40px;height:40px;border-radius:50%;background:var(--acc);color:#fff;
  display:flex;align-items:center;justify-content:center;font-size:20px;
  cursor:pointer;border:none;transition:opacity .2s,transform .1s;flex-shrink:0;}
.nb:active{transform:scale(.88);} .nb:disabled{opacity:.3;}
#pgInfo{font-size:11px;color:var(--sub);font-variant-numeric:tabular-nums;
  letter-spacing:.06em;text-align:center;flex:1;}

/* backdrop */
#sBd{position:fixed;inset:0;background:rgba(0,0,0,0);z-index:98;
  pointer-events:none;transition:background .3s;}
#sBd.on{pointer-events:auto;background:rgba(0,0,0,.65);}

/* settings panel */
#sP{position:fixed;bottom:0;left:0;right:0;background:var(--bar);
  border:1px solid var(--bdr);border-bottom:none;border-radius:20px 20px 0 0;
  transform:translateY(100%);transition:transform .32s cubic-bezier(.32,.72,0,1);
  z-index:99;max-height:82vh;overflow-y:auto;}
#sP.on{transform:translateY(0);}

.sh{width:36px;height:4px;border-radius:2px;background:var(--bdr);margin:12px auto 2px;}
.shd{display:flex;align-items:center;justify-content:space-between;padding:6px 16px 10px;}
.sttl{font-size:14px;font-weight:700;color:var(--tx);}
.sclx{width:28px;height:28px;border-radius:50%;background:rgba(128,128,128,.2);
  border:none;color:var(--sub);font-size:13px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;}

.ssec{padding:10px 16px 12px;border-top:1px solid var(--bdr);}
.slbl{font-size:10px;color:var(--sub);text-transform:uppercase;
  letter-spacing:.1em;margin-bottom:8px;font-weight:700;}

/* toggle group */
.tg{display:flex;gap:8px;}
.tgb{flex:1;padding:9px 10px;border-radius:10px;border:1.5px solid var(--bdr);
  background:transparent;color:var(--sub);font-size:12px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;gap:5px;transition:all .2s;}
.tgb.on{border-color:var(--acc);color:var(--acc);background:var(--ac2);}

/* theme buttons */
.thg{display:flex;gap:10px;}
.thb{flex:1;padding:10px 6px;border-radius:12px;border:2px solid var(--bdr);
  cursor:pointer;display:flex;flex-direction:column;align-items:center;
  gap:4px;transition:border-color .2s;}
.thb span{font-size:10px;font-weight:600;}
.thb.on{border-color:var(--acc);}
.th-dk{background:#1a1a1a;color:#e5e5e5;}
.th-lt{background:#f0ece4;color:#1c1c1e;}
.th-sp{background:#f4e4c1;color:#3b2f1e;}

/* step controls */
.stc{display:flex;align-items:center;gap:10px;}
.stb{width:38px;height:38px;border-radius:10px;border:1.5px solid var(--bdr);
  background:transparent;color:var(--tx);font-size:16px;font-weight:700;
  cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;}
.stb:active{background:var(--ac2);border-color:var(--acc);}
.stv{flex:1;text-align:center;font-size:13px;font-weight:600;color:var(--tx);}
.spad{height:14px;}
</style>
</head>
<body data-theme="dark" oncontextmenu="return false;">

<div id="tBar">
  <a href="/library" id="tBack">← লাইব্রেরি</a>
  <div id="tTitle">ইবুক</div>
  <div id="aaBtn" onclick="openS()">Aa</div>
</div>

<div id="viewer"></div>

<div id="bBar">
  <button id="prev" class="nb">&#8249;</button>
  <span id="pgInfo">Loading…</span>
  <button id="next" class="nb">&#8250;</button>
</div>

<div id="sBd" onclick="closeS()"></div>

<div id="sP">
  <div class="sh"></div>
  <div class="shd">
    <span class="sttl">পড়ার সেটিং</span>
    <button class="sclx" onclick="closeS()">&#10005;</button>
  </div>

  <div class="ssec">
    <div class="slbl">পড়ার মোড</div>
    <div class="tg">
      <button class="tgb" id="fPg" onclick="setFlow('paginated')">&#128214; পাতায় পাতায়</button>
      <button class="tgb" id="fSc" onclick="setFlow('scroll')">&#128220; স্ক্রল করে</button>
    </div>
  </div>

  <div class="ssec">
    <div class="slbl">থিম</div>
    <div class="thg">
      <button class="thb th-dk" id="thDk" onclick="setTheme('dark')">&#127761;<span>ডার্ক</span></button>
      <button class="thb th-lt" id="thLt" onclick="setTheme('light')">&#9728;&#65039;<span>লাইট</span></button>
      <button class="thb th-sp" id="thSp" onclick="setTheme('sepia')">&#127807;<span>সেপিয়া</span></button>
    </div>
  </div>

  <div class="ssec">
    <div class="slbl">ফন্ট সাইজ</div>
    <div class="stc">
      <button class="stb" style="font-size:13px" onclick="stepF(-1)">A&#8722;</button>
      <div class="stv" id="vF">100%</div>
      <button class="stb" style="font-size:19px" onclick="stepF(1)">A+</button>
    </div>
  </div>

  <div class="ssec">
    <div class="slbl">লাইন স্পেস</div>
    <div class="stc">
      <button class="stb" onclick="stepL(-1)">&#8722;</button>
      <div class="stv" id="vL">স্বাভাবিক</div>
      <button class="stb" onclick="stepL(1)">+</button>
    </div>
  </div>

  <div class="ssec">
    <div class="slbl">মার্জিন</div>
    <div class="stc">
      <button class="stb" onclick="stepM(-1)">&#9664;</button>
      <div class="stv" id="vM">স্বাভাবিক</div>
      <button class="stb" onclick="stepM(1)">&#9654;</button>
    </div>
  </div>

  <div class="spad"></div>
</div>

<script>
/* ── Preference tables ──────────────────────────────────── */
const PKEY='boighor_prefs';
const FONTS  =[80,90,100,110,120,140,160];
const LINES  =[{l:'টাইট',v:1.35},{l:'স্বাভাবিক',v:1.65},{l:'প্রশস্ত',v:1.9},{l:'বেশি',v:2.2}];
const MARGINS=[{l:'সরু',v:8},{l:'স্বাভাবিক',v:20},{l:'প্রশস্ত',v:38}];
const ECSS={
  dark: {bg:'#1a1a1a',cl:'#e5e5e5',lk:'#f59e0b'},
  light:{bg:'#f0ece4',cl:'#1c1c1e',lk:'#0055cc'},
  sepia:{bg:'#f4e4c1',cl:'#3b2f1e',lk:'#8b4513'},
};

let prefs={theme:'dark',fi:2,li:1,mi:1,flow:'paginated'};

function lpref(){
  try{
    const s=JSON.parse(localStorage.getItem(PKEY)||'{}');
    if(s.theme)prefs.theme=s.theme;
    if(s.fi!==undefined)prefs.fi=s.fi;
    if(s.li!==undefined)prefs.li=s.li;
    if(s.mi!==undefined)prefs.mi=s.mi;
    if(s.flow)prefs.flow=s.flow;
  }catch(_){}
}
function spref(){try{localStorage.setItem(PKEY,JSON.stringify(prefs));}catch(_){}}

/* ── DOM refs ───────────────────────────────────────────── */
const token   =new URLSearchParams(window.location.search).get('token');
const viewerEl=document.getElementById('viewer');
const titleEl =document.getElementById('tTitle');
const pgEl    =document.getElementById('pgInfo');
const prevBtn =document.getElementById('prev');
const nextBtn =document.getElementById('next');
let rend=null, book=null;

/* ── Apply CSS to epub iframe ───────────────────────────── */
function applyEpub(){
  if(!rend)return;
  const t=ECSS[prefs.theme]||ECSS.dark;
  const fs=FONTS[prefs.fi];
  const lh=LINES[prefs.li].v;
  const mg=MARGINS[prefs.mi].v;
  rend.themes.default({
    body:{'background':t.bg+' !important','color':t.cl+' !important',
      'font-size':fs+'% !important','line-height':lh+' !important',
      'padding':'6px '+mg+'px !important','max-width':'100% !important'},
    p:{'line-height':lh+' !important','color':t.cl+' !important'},
    h1:{'color':t.cl+' !important'},h2:{'color':t.cl+' !important'},
    h3:{'color':t.cl+' !important'},a:{'color':t.lk+' !important'},
    img:{'max-width':'100% !important','height':'auto !important'},
  });
}

/* ── Theme ──────────────────────────────────────────────── */
function setTheme(t){
  prefs.theme=t; spref();
  document.documentElement.dataset.theme=t;
  document.body.dataset.theme=t;
  syncUI(); applyEpub();
}

/* ── Steps ──────────────────────────────────────────────── */
function stepF(d){prefs.fi=Math.max(0,Math.min(FONTS.length-1,prefs.fi+d));spref();syncUI();applyEpub();}
function stepL(d){prefs.li=Math.max(0,Math.min(LINES.length-1,prefs.li+d));spref();syncUI();applyEpub();}
function stepM(d){prefs.mi=Math.max(0,Math.min(MARGINS.length-1,prefs.mi+d));spref();syncUI();applyEpub();}
window.stepF=stepF;window.stepL=stepL;window.stepM=stepM;

/* ── Flow switch ────────────────────────────────────────── */
async function setFlow(nf){
  if(prefs.flow===nf||!book)return;
  prefs.flow=nf; spref(); syncUI();
  let cfi=null;
  try{const loc=rend&&rend.currentLocation();if(loc&&loc.start)cfi=loc.start.cfi;}catch(_){}
  viewerEl.innerHTML='';
  if(rend){try{rend.destroy();}catch(_){} rend=null;}
  const fs=nf==='scroll'?'scrolled-doc':'paginated';
  rend=book.renderTo('viewer',{width:'100%',height:'100%',spread:'none',flow:fs});
  applyEpub();
  await rend.display(cfi||undefined);
  bindEv();
}
window.setFlow=setFlow;

/* ── Settings open/close ────────────────────────────────── */
function openS(){document.getElementById('sP').classList.add('on');document.getElementById('sBd').classList.add('on');}
function closeS(){document.getElementById('sP').classList.remove('on');document.getElementById('sBd').classList.remove('on');}
window.openS=openS;window.closeS=closeS;

/* ── Sync controls to prefs ─────────────────────────────── */
function syncUI(){
  document.getElementById('fPg').classList.toggle('on',prefs.flow==='paginated');
  document.getElementById('fSc').classList.toggle('on',prefs.flow==='scroll');
  ['Dk','Lt','Sp'].forEach(n=>document.getElementById('th'+n).classList.remove('on'));
  const m={dark:'Dk',light:'Lt',sepia:'Sp'};
  const el=document.getElementById('th'+(m[prefs.theme]||'Dk'));
  if(el)el.classList.add('on');
  document.getElementById('vF').innerText=FONTS[prefs.fi]+'%';
  document.getElementById('vL').innerText=LINES[prefs.li].l;
  document.getElementById('vM').innerText=MARGINS[prefs.mi].l;
}
window.setTheme=setTheme;

/* ── Error UI ───────────────────────────────────────────── */
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function showErr(msg){
  pgEl.innerText='Error';
  const cdn=/JSZip|EPUB reader|CDN/i.test(msg||'');
  viewerEl.innerHTML=`
    <div style="margin:60px 16px 0;padding:20px;border:1px solid #7f1d1d;background:rgba(127,29,29,.25);border-radius:16px;text-align:center">
      <div style="font-size:40px;margin-bottom:10px">&#9888;&#65039;</div>
      <h2 style="font-weight:700;color:#fca5a5;margin-bottom:8px">বইটি খোলা যাচ্ছে না</h2>
      <p style="font-size:14px;color:#d4d4d8;margin-bottom:16px">${esc(msg)}</p>
      ${cdn
        ?`<button onclick="location.reload()" style="padding:10px 14px;background:#d97706;color:#fff;border:0;border-radius:10px">আবার চেষ্টা করুন</button>
          <p style="font-size:12px;color:#fca5a5;margin-top:12px">CDN loading error — বই ডিলিট করবেন না।</p>`
        :`<button onclick="delBad()" style="padding:10px 14px;background:#dc2626;color:#fff;border:0;border-radius:10px">মুছে আবার Import করুন</button>`}
      <a href="/library" style="display:block;margin-top:16px;color:#f59e0b;font-size:14px">লাইব্রেরিতে ফিরুন</a>
    </div>`;
}

/* ── IndexedDB ──────────────────────────────────────────── */
async function openDB(){
  return new Promise((res,rej)=>{
    const q=indexedDB.open('BoighorDB',1);
    q.onupgradeneeded=e=>{const d=e.target.result;if(!d.objectStoreNames.contains('books'))d.createObjectStore('books',{keyPath:'token'});};
    q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
  });
}
async function delBad(){
  try{
    const db=await openDB();
    await new Promise((res,rej)=>{const tx=db.transaction('books','readwrite');tx.objectStore('books').delete(token);tx.oncomplete=res;tx.onerror=()=>rej(tx.error);});
  }catch(e){console.error(e);}
  window.location.href='/library';
}
window.delBad=delBad;

/* ── Keyboard ───────────────────────────────────────────── */
document.addEventListener('keydown',e=>{
  const k=(e.key||'').toLowerCase();
  if(e.ctrlKey&&['p','s','u'].includes(k))e.preventDefault();
  if(e.key==='F12')e.preventDefault();
  if(e.ctrlKey&&e.shiftKey&&['i','j'].includes(k))e.preventDefault();
  if(e.key==='Escape')closeS();
  if(e.key==='ArrowLeft' &&rend)rend.prev();
  if(e.key==='ArrowRight'&&rend)rend.next();
});

/* ── Bind rendition events ──────────────────────────────── */
function bindEv(){
  rend.on('relocated',loc=>{
    if(loc&&loc.start)
      pgEl.innerText=loc.start.percentage?Math.round(loc.start.percentage*100)+'%':'Reading';
  });
  prevBtn.onclick=()=>rend.prev();
  nextBtn.onclick=()=>rend.next();
  viewerEl.onclick=e=>{
    if(!rend||prefs.flow==='scroll')return;
    if(e.clientX<window.innerWidth*.3)rend.prev();
    else if(e.clientX>window.innerWidth*.7)rend.next();
  };
}

/* ── Init ───────────────────────────────────────────────── */
async function init(){
  try{
    if(!token)      throw new Error('Token missing.');
    if(!window.JSZip)throw new Error('JSZip lib not loaded. Internet/CDN blocked হতে পারে।');
    if(!window.ePub) throw new Error('EPUB reader library did not load. Internet/CDN blocked হতে পারে।');

    lpref();
    document.documentElement.dataset.theme=prefs.theme;
    document.body.dataset.theme=prefs.theme;
    syncUI();

    const db=await openDB();
    const saved=await new Promise((res,rej)=>{
      const tx=db.transaction('books','readonly');
      const q=tx.objectStore('books').get(token);
      q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
    });

    if(!saved)throw new Error('বইটি এই ডিভাইসের লাইব্রেরিতে পাওয়া যায়নি।');
    if(!saved.blob||typeof saved.blob.arrayBuffer!=='function')throw new Error('Saved book data corrupted.');

    titleEl.innerText=saved.title||'ইবুক';
    const ab=await saved.blob.arrayBuffer();
    if(!ab||ab.byteLength<10)throw new Error('Saved EPUB is empty.');
    const hd=new Uint8Array(ab.slice(0,4));
    if(!(hd[0]===0x50&&hd[1]===0x4B))
      throw new Error('Saved file is not an EPUB. পুরোনো ভুল import মুছে আবার import করুন।');

    book=ePub(ab);
    const fls=prefs.flow==='scroll'?'scrolled-doc':'paginated';
    rend=book.renderTo('viewer',{width:'100%',height:'100%',spread:'none',flow:fls});
    applyEpub();
    await rend.display();
    pgEl.innerText='Ready';
    bindEv();

  }catch(e){console.error(e);showErr(e.message||'Unknown EPUB reader error.');}
}

window.addEventListener('resize',()=>{if(rend)rend.resize();});
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
