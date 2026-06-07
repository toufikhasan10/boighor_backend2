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
# Service Worker  (NEW — fixes offline PWA)
# ══════════════════════════════════════════════════════════════

SW_JS = r"""
/* ═══════════════════════════════════════
   Boighor Service Worker v4
   ─ Cache shell on install
   ─ Serve from cache when offline
   ─ Update cache in background
═══════════════════════════════════════ */
const CACHE = 'boighor-v4';
const SHELL = ['/library', '/reader', '/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c =>
      Promise.allSettled(SHELL.map(u => c.add(u).catch(() => null)))
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(ks =>
      Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  const skip = ['/stream-','/download/','/admin/','/request-','/verify-','/mark-','/health','/sw.js'];
  if (skip.some(p => url.pathname.startsWith(p))) return;

  /* Reader page — ignore query params, serve cached shell */
  if (url.pathname === '/reader') {
    e.respondWith(
      caches.match('/reader').then(cached => {
        fetch('/reader').then(r => {
          if (r.ok) caches.open(CACHE).then(c => c.put('/reader', r));
        }).catch(() => {});
        return cached || fetch(e.request);
      })
    );
    return;
  }

  /* Everything else — cache-first, refresh in background */
  e.respondWith(
    caches.open(CACHE).then(cache =>
      cache.match(e.request).then(cached => {
        const net = fetch(e.request).then(res => {
          if (res.ok) cache.put(e.request, res.clone());
          return res;
        }).catch(() => null);
        return cached || net;
      })
    )
  );
});
"""

# ══════════════════════════════════════════════════════════════
# Library HTML  (REDESIGNED — last-read, gradients, skeleton)
# ══════════════════════════════════════════════════════════════

LIBRARY_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ store }} — আমার লাইব্রেরি</title>
<link rel="manifest" href="/manifest.json">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+Bengali:wght@700;900&display=swap" rel="stylesheet">
<style>
:root{--acc:#b8741a;--acc-l:#d4973b;--dark:#0c0c0c;--card:#161616;--card2:#1e1e1e;--bdr:#252525;--sub:#666;--tx:#e0e0e0;}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--dark);color:var(--tx);font-family:'Segoe UI',sans-serif;min-height:100vh}

/* ── HEADER ── */
.hdr{background:linear-gradient(160deg,#0c0c0c 0%,#1a1005 60%,#0c0c0c 100%);
  padding:env(safe-area-inset-top,0) 0 0;position:relative;overflow:hidden}
.hdr::after{content:'';position:absolute;inset:0;
  background:radial-gradient(ellipse 60% 80% at 30% 60%,rgba(184,116,26,.18) 0%,transparent 70%);
  pointer-events:none}
.hdr-inner{max-width:480px;margin:0 auto;padding:20px 16px 20px;position:relative;z-index:1}
.hdr-top{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:14px}
.logo{font-family:'Noto Serif Bengali',serif;font-size:28px;font-weight:900;color:var(--acc);line-height:1}
.logo-sub{font-size:11px;color:var(--sub);margin-top:2px;font-family:'Segoe UI',sans-serif}
.hdr-actions{display:flex;gap:8px}
.hdr-btn{padding:8px 13px;border-radius:20px;border:1px solid rgba(255,255,255,.1);
  background:rgba(255,255,255,.04);color:#a0a0a0;font-size:12px;font-weight:600;
  cursor:pointer;transition:all .2s;display:flex;align-items:center;gap:5px;white-space:nowrap}
.hdr-btn:active{border-color:var(--acc);color:var(--acc)}

/* ── LAST READ ── */
.lr-wrap{max-width:480px;margin:0 auto;padding:16px 14px 0}
.lr-card{background:var(--card);border:1px solid var(--bdr);border-radius:16px;
  padding:14px;display:flex;gap:14px;align-items:center;cursor:pointer;
  transition:transform .2s,box-shadow .2s;border-left:3px solid var(--acc);
  box-shadow:0 2px 16px rgba(184,116,26,.08)}
.lr-card:active{transform:scale(.98)}
.lr-cover{width:50px;height:68px;border-radius:8px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-size:22px;
  box-shadow:0 4px 14px rgba(0,0,0,.4)}
.lr-info{flex:1;min-width:0}
.lr-badge{font-size:9px;color:var(--acc);font-weight:800;text-transform:uppercase;
  letter-spacing:.06em;margin-bottom:4px}
.lr-title{font-size:14px;font-weight:700;color:var(--tx);margin-bottom:6px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lr-bar{height:3px;background:rgba(255,255,255,.1);border-radius:2px;margin-bottom:4px}
.lr-fill{height:100%;background:var(--acc);border-radius:2px;transition:width .5s}
.lr-pct{font-size:10px;color:var(--sub)}
.lr-arrow{color:var(--acc);font-size:20px;flex-shrink:0;opacity:.7}
.lr-section-title{font-size:11px;color:var(--sub);font-weight:600;
  text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px}

/* ── SECTION ── */
.sec{max-width:480px;margin:0 auto;padding:20px 14px 0}
.sec-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.sec-title{font-size:15px;font-weight:700;color:var(--tx)}
.sec-count{font-size:12px;color:var(--sub)}

/* ── GRID ── */
.bk-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}

/* ── BOOK CARD ── */
.bk{background:var(--card);border:1px solid var(--bdr);border-radius:14px;
  overflow:hidden;cursor:pointer;transition:transform .2s,box-shadow .2s}
.bk:active{transform:scale(.97)}
.bk-cov{position:relative;padding-bottom:148%;overflow:hidden}
.bk-cov-in{position:absolute;inset:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;padding:14px;text-align:center}
.bk-cov-emoji{font-size:34px;margin-bottom:8px;filter:drop-shadow(0 2px 6px rgba(0,0,0,.4))}
.bk-cov-title{font-size:11px;font-weight:700;color:rgba(255,255,255,.88);
  line-height:1.45;overflow:hidden;display:-webkit-box;
  -webkit-line-clamp:3;-webkit-box-orient:vertical}
.bk-prog{height:2px;background:rgba(255,255,255,.12)}
.bk-prog-fill{height:100%;background:rgba(255,255,255,.65);transition:width .4s}
.bk-foot{padding:10px 11px 12px}
.bk-name{font-size:12.5px;font-weight:700;color:var(--tx);overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;margin-bottom:2px}
.bk-meta{font-size:10px;color:var(--sub)}

/* ── SKELETON ── */
@keyframes sk{from{background-position:-300px 0}to{background-position:300px 0}}
.sk-card{border-radius:14px;height:210px;
  background:linear-gradient(90deg,var(--card) 0%,#242424 50%,var(--card) 100%);
  background-size:600px 100%;animation:sk 1.5s infinite}

/* ── EMPTY ── */
#emptyWrap{display:none;text-align:center;padding:72px 24px;max-width:300px;margin:0 auto}
.em-icon{font-size:64px;margin-bottom:18px}
.em-title{font-size:18px;font-weight:700;color:var(--tx);margin-bottom:8px}
.em-sub{font-size:13px;color:var(--sub);margin-bottom:28px;line-height:1.7}
.em-btn{display:inline-flex;align-items:center;gap:8px;padding:13px 26px;
  background:var(--acc);color:#fff;border:none;border-radius:12px;
  font-size:14px;font-weight:700;cursor:pointer;transition:background .2s;
  box-shadow:0 4px 16px rgba(184,116,26,.3)}
.em-btn:active{background:var(--acc-l)}

/* ── RESTORE MODAL ── */
#rModal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.82);z-index:50;
  align-items:flex-end}
#rModal.open{display:flex}
.r-sheet{background:#111;width:100%;border-radius:22px 22px 0 0;
  padding:0 0 env(safe-area-inset-bottom,0);max-height:88vh;overflow-y:auto}
.r-drag{width:36px;height:4px;background:var(--bdr);border-radius:2px;margin:12px auto 0}
.r-hd{display:flex;align-items:center;justify-content:space-between;padding:12px 18px 14px}
.r-hd-title{font-size:16px;font-weight:700;color:var(--tx)}
.r-x{width:30px;height:30px;border-radius:50%;background:rgba(255,255,255,.07);
  border:none;color:var(--sub);font-size:14px;cursor:pointer;
  display:flex;align-items:center;justify-content:center}
.r-body{padding:0 18px 24px}
.r-inp{width:100%;background:#1a1a1a;border:1.5px solid var(--bdr);border-radius:12px;
  padding:13px 15px;color:var(--tx);font-size:14px;margin-bottom:14px;
  outline:none;transition:border .2s}
.r-inp:focus{border-color:var(--acc)}
.r-btn{width:100%;padding:13px;background:var(--acc);color:#fff;border:none;
  border-radius:12px;font-size:14px;font-weight:700;cursor:pointer;transition:background .2s}
.r-btn:active{background:var(--acc-l)}
.r-msg{font-size:13px;margin-top:10px;text-align:center;display:none}
.r-back{width:100%;padding:9px;color:var(--sub);font-size:13px;
  background:none;border:none;cursor:pointer;margin-top:4px}
.r-book-row{display:flex;align-items:center;justify-content:space-between;
  padding:12px;background:#1a1a1a;border-radius:10px;margin-bottom:8px}

.bottom-spacer{height:24px}
</style>
</head>
<body>

<!-- ── HEADER ──────────────────────────────────── -->
<div class="hdr">
  <div class="hdr-inner">
    <div class="hdr-top">
      <div>
        <div class="logo">{{ store }}</div>
        <div class="logo-sub">আপনার প্রিমিয়াম লাইব্রেরি</div>
      </div>
      <div class="hdr-actions">
        <button class="hdr-btn" onclick="openRestore()">🔄 Restore</button>
      </div>
    </div>
  </div>
</div>

<!-- ── LAST READ ──────────────────────────────── -->
<div id="lastReadWrap" class="lr-wrap" style="display:none">
  <div class="lr-section-title">আগের পাঠ</div>
  <div class="lr-card" id="lastReadCard">
    <div class="lr-cover" id="lrCover">📖</div>
    <div class="lr-info">
      <div class="lr-badge">📌 সর্বশেষ পড়া</div>
      <div class="lr-title" id="lrTitle">—</div>
      <div class="lr-bar"><div class="lr-fill" id="lrFill" style="width:0%"></div></div>
      <div class="lr-pct" id="lrPct">0% পড়া হয়েছে</div>
    </div>
    <div class="lr-arrow">›</div>
  </div>
</div>

<!-- ── BOOK SHELF ─────────────────────────────── -->
<div class="sec" id="shelfSection" style="display:none">
  <div class="sec-hd">
    <span class="sec-title">আমার সংগ্রহ</span>
    <span class="sec-count" id="bookCount"></span>
  </div>
  <div class="bk-grid" id="shelf"></div>
</div>

<!-- ── SKELETON ──────────────────────────────── -->
<div class="sec" id="skelWrap">
  <div class="bk-grid">
    <div class="sk-card"></div><div class="sk-card"></div>
    <div class="sk-card"></div><div class="sk-card"></div>
  </div>
</div>

<!-- ── EMPTY STATE ────────────────────────────── -->
<div id="emptyWrap">
  <div class="em-icon">📚</div>
  <div class="em-title">লাইব্রেরি খালি</div>
  <div class="em-sub">আপনি এখনো কোনো বই ডাউনলোড করেননি।<br>আগে কিনে থাকলে Restore করুন।</div>
  <button class="em-btn" onclick="openRestore()">🔄 লাইব্রেরি Restore করুন</button>
</div>

<!-- ── RESTORE MODAL ──────────────────────────── -->
<div id="rModal">
  <div class="r-sheet">
    <div class="r-drag"></div>
    <div class="r-hd">
      <span class="r-hd-title">লাইব্রেরি Restore করুন</span>
      <button class="r-x" onclick="closeRestore()">✕</button>
    </div>
    <div class="r-body">
      <!-- Step 1 -->
      <div id="rs1">
        <p style="font-size:13px;color:var(--sub);margin-bottom:14px">কেনার সময়ের ইমেইল দিন। OTP পাঠানো হবে।</p>
        <input id="rEmail" class="r-inp" type="email" placeholder="আপনার ইমেইল..."
          onkeydown="if(event.key==='Enter')doOTP()">
        <button class="r-btn" onclick="doOTP()">OTP পাঠান</button>
        <p id="rs1msg" class="r-msg"></p>
      </div>
      <!-- Step 2 -->
      <div id="rs2" style="display:none">
        <p style="font-size:13px;color:var(--sub);margin-bottom:4px">ইমেইলে পাঠানো ৬ সংখ্যার কোড লিখুন।</p>
        <p id="rs2email" style="font-size:12px;color:var(--acc);margin-bottom:14px"></p>
        <input id="rOTP" class="r-inp" type="number" placeholder="123456"
          style="font-size:22px;letter-spacing:.2em;text-align:center"
          oninput="if(this.value.length>6)this.value=this.value.slice(0,6)"
          onkeydown="if(event.key==='Enter')doVerify()">
        <button class="r-btn" onclick="doVerify()">নিশ্চিত করুন</button>
        <button class="r-back" onclick="backStep1()">← ইমেইল পরিবর্তন</button>
        <p id="rs2msg" class="r-msg"></p>
      </div>
      <!-- Step 3 -->
      <div id="rs3" style="display:none">
        <p style="font-size:13px;color:var(--sub);margin-bottom:12px">বইসমূহ নামানো হচ্ছে…</p>
        <div id="rList"></div>
        <div id="rDone" style="display:none;text-align:center;padding:20px 0">
          <div style="font-size:40px;margin-bottom:8px">✅</div>
          <p style="color:#4ade80;font-weight:700">সফলভাবে Restore হয়েছে!</p>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="bottom-spacer"></div>

<script>
const DB='BoighorDB', OSK='books';
const LAST_KEY='boighor_lastread';
const POS_KEY=t=>`boighor_pos_${t}`;
let rEmail='', rBooks=[];

/* ── Cover gradient system ─────────────────── */
const COVER_G=[
  {g:'linear-gradient(145deg,#2c1a08,#8a5210)',e:'📖'},
  {g:'linear-gradient(145deg,#0d1f12,#2d6a4f)',e:'🌿'},
  {g:'linear-gradient(145deg,#0a1628,#1a3a5c)',e:'🌊'},
  {g:'linear-gradient(145deg,#1a0a2e,#4a1a7e)',e:'✨'},
  {g:'linear-gradient(145deg,#1a0a0a,#6b1a1a)',e:'🔥'},
  {g:'linear-gradient(145deg,#0a1a1a,#0a5c5c)',e:'💎'},
  {g:'linear-gradient(145deg,#1a1a0a,#4a4a12)',e:'🌙'},
  {g:'linear-gradient(145deg,#14091a,#5c1a4a)',e:'🌹'},
];
function coverFor(k){
  let h=0;for(const c of (k||''))h=((h<<5)-h+c.charCodeAt(0))|0;
  return COVER_G[Math.abs(h)%COVER_G.length];
}
function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

/* ── IndexedDB ──────────────────────────────── */
async function openDB(){
  return new Promise((res,rej)=>{
    const q=indexedDB.open(DB,1);
    q.onupgradeneeded=e=>{const d=e.target.result;if(!d.objectStoreNames.contains(OSK))d.createObjectStore(OSK,{keyPath:'token'});};
    q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
  });
}

/* ── Last Read ──────────────────────────────── */
function loadLastRead(books){
  try{
    const lr=JSON.parse(localStorage.getItem(LAST_KEY)||'null');
    if(!lr||!lr.token)return;
    const book=books.find(b=>b.token===lr.token);
    if(!book)return;
    // get saved position
    const pos=JSON.parse(localStorage.getItem(POS_KEY(lr.token))||'null');
    const pct=pos?pos.pct:(lr.pct||0);
    const cov=coverFor(lr.token);
    document.getElementById('lrCover').style.background=cov.g;
    document.getElementById('lrCover').textContent=cov.e;
    document.getElementById('lrTitle').textContent=lr.title||book.title||'ইবুক';
    document.getElementById('lrFill').style.width=pct+'%';
    document.getElementById('lrPct').textContent=pct+'% পড়া হয়েছে';
    document.getElementById('lastReadCard').onclick=()=>{
      window.location.href=`/reader?token=${encodeURIComponent(lr.token)}`;
    };
    document.getElementById('lastReadWrap').style.display='block';
  }catch(e){console.error(e);}
}

/* ── Render library ─────────────────────────── */
async function loadLibrary(){
  document.getElementById('skelWrap').style.display='block';
  try{
    const db=await openDB();
    const books=await new Promise((res,rej)=>{
      const tx=db.transaction(OSK,'readonly');
      const q=tx.objectStore(OSK).getAll();
      q.onsuccess=()=>res(q.result||[]);q.onerror=()=>rej(q.error);
    });

    document.getElementById('skelWrap').style.display='none';

    if(!books.length){
      document.getElementById('emptyWrap').style.display='block';
      return;
    }

    // Last read
    loadLastRead(books);

    // Shelf
    const shelf=document.getElementById('shelf');
    shelf.innerHTML='';
    books.forEach(b=>{
      const cov=coverFor(b.token);
      const pos=JSON.parse(localStorage.getItem(POS_KEY(b.token))||'null');
      const pct=pos?Math.min(100,pos.pct):0;
      const card=document.createElement('div');
      card.className='bk';
      card.innerHTML=`
        <div class="bk-cov">
          <div class="bk-cov-in" style="background:${cov.g}">
            <div class="bk-cov-emoji">${cov.e}</div>
            <div class="bk-cov-title">${esc(b.title||'ইবুক')}</div>
          </div>
        </div>
        <div class="bk-prog"><div class="bk-prog-fill" style="width:${pct}%"></div></div>
        <div class="bk-foot">
          <div class="bk-name">${esc(b.title||'ইবুক')}</div>
          <div class="bk-meta">${pct>0?pct+'% পড়া হয়েছে':'এখনো শুরু হয়নি'}</div>
        </div>`;
      card.onclick=()=>{window.location.href=`/reader?token=${encodeURIComponent(b.token)}`;};
      shelf.appendChild(card);
    });

    document.getElementById('bookCount').textContent=books.length+' টি বই';
    document.getElementById('shelfSection').style.display='block';
  }catch(e){
    console.error(e);
    document.getElementById('skelWrap').style.display='none';
    document.getElementById('emptyWrap').style.display='block';
  }
}

/* ── Restore modal ──────────────────────────── */
function openRestore(){document.getElementById('rModal').classList.add('open');}
function closeRestore(){document.getElementById('rModal').classList.remove('open');}
function msg(id,text,err){
  const el=document.getElementById(id);
  el.textContent=text;
  el.style.color=err?'#f87171':'#f59e0b';
  el.style.display='block';
}
function backStep1(){
  document.getElementById('rs2').style.display='none';
  document.getElementById('rs1').style.display='block';
  document.getElementById('rs1msg').style.display='none';
}
window.openRestore=openRestore;window.closeRestore=closeRestore;window.backStep1=backStep1;

async function doOTP(){
  const email=(document.getElementById('rEmail').value||'').trim();
  if(!email)return;
  rEmail=email;msg('rs1msg','⏳ পাঠানো হচ্ছে…',false);
  try{
    const r=await fetch('/request-restore-otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
    const d=await r.json();
    if(d.success){
      document.getElementById('rs1').style.display='none';
      document.getElementById('rs2email').textContent='ইমেইল: '+rEmail;
      document.getElementById('rs2').style.display='block';
    }else{msg('rs1msg',d.error||'ত্রুটি হয়েছে।',true);}
  }catch(_){msg('rs1msg','ইন্টারনেট সংযোগ পরীক্ষা করুন।',true);}
}
window.doOTP=doOTP;

async function doVerify(){
  const otp=(document.getElementById('rOTP').value||'').trim();
  if(otp.length!==6){msg('rs2msg','৬ সংখ্যার কোড দিন।',true);return;}
  msg('rs2msg','⏳ যাচাই করা হচ্ছে…',false);
  try{
    const r=await fetch('/verify-restore-otp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:rEmail,otp})});
    const d=await r.json();
    if(d.success&&d.books&&d.books.length){
      rBooks=d.books;
      document.getElementById('rs2').style.display='none';
      buildRestoreList();
      document.getElementById('rs3').style.display='block';
      downloadAll();
    }else if(d.success){msg('rs2msg','কোনো বইয়ের তথ্য পাওয়া যায়নি।',true);}
    else{msg('rs2msg',d.error||'OTP ভুল বা মেয়াদ শেষ।',true);}
  }catch(_){msg('rs2msg','ইন্টারনেট সংযোগ পরীক্ষা করুন।',true);}
}
window.doVerify=doVerify;

function buildRestoreList(){
  const list=document.getElementById('rList');
  list.innerHTML='';
  rBooks.forEach((b,i)=>{
    const row=document.createElement('div');
    row.className='r-book-row';
    row.innerHTML=`<span style="font-size:13px;color:var(--tx);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-right:8px">${esc(b.title||'ইবুক')}</span>
      <span id="rs_${i}" style="font-size:11px;color:var(--sub);white-space:nowrap">অপেক্ষা…</span>`;
    list.appendChild(row);
  });
}

async function downloadAll(){
  const db=await openDB();
  for(let i=0;i<rBooks.length;i++){
    const b=rBooks[i];
    const st=document.getElementById(`rs_${i}`);
    if(!st)continue;
    try{
      st.textContent='⬇️ শুরু…';st.style.color='#f59e0b';
      const resp=await fetch(`/stream-ebook/${encodeURIComponent(b.token)}`,{cache:'no-store'});
      if(!resp.ok)throw new Error(`HTTP ${resp.status}`);
      const rdr=resp.body.getReader();
      const cl=+(resp.headers.get('Content-Length')||0);
      let recv=0,chunks=[];
      while(true){
        const{done,value}=await rdr.read();
        if(done)break;
        chunks.push(value);recv+=value.length;
        st.textContent=cl?Math.round(recv/cl*100)+'%':Math.round(recv/1024)+' KB';
      }
      const blob=new Blob(chunks,{type:'application/epub+zip'});
      const hd=new Uint8Array(await blob.slice(0,4).arrayBuffer());
      if(!(hd[0]===0x50&&hd[1]===0x4B))throw new Error('Invalid EPUB');
      st.textContent='💾 সেভ হচ্ছে…';
      await new Promise((res,rej)=>{
        const tx=db.transaction(OSK,'readwrite');
        tx.objectStore(OSK).put({token:b.token,title:b.title,format:'epub',blob:blob,added_at:new Date().toISOString()});
        tx.oncomplete=res;tx.onerror=()=>rej(tx.error);
      });
      try{await fetch(`/mark-used/${encodeURIComponent(b.token)}`,{method:'POST'});}catch(_){}
      st.textContent='✅';st.style.color='#4ade80';
    }catch(e){
      console.error(e);
      st.textContent='❌ ব্যর্থ';st.style.color='#f87171';
    }
  }
  document.getElementById('rDone').style.display='block';
  setTimeout(()=>{closeRestore();loadLibrary();},1800);
}

/* ── SW registration ────────────────────────── */
if('serviceWorker' in navigator){
  window.addEventListener('load',()=>{
    navigator.serviceWorker.register('/sw.js').catch(()=>{});
  });
}

loadLibrary();
</script>
</body>
</html>
"""

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
        "background_color": "#0c0c0c",
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
# Library Route
# ══════════════════════════════════════════════════════════════

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
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0c0c0c;color:#e0e0e0;font-family:'Segoe UI',sans-serif;
  display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}
.card{text-align:center;max-width:340px;width:100%}
.icon{font-size:56px;margin-bottom:20px;animation:bounce 1.2s ease-in-out infinite}
@keyframes bounce{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}
h1{font-size:20px;font-weight:700;margin-bottom:6px;color:#f0f0f0}
p{font-size:13px;color:#777;margin-bottom:20px;line-height:1.6}
.bar-wrap{background:#1e1e1e;border-radius:8px;height:6px;overflow:hidden;margin-bottom:8px}
#prog{background:linear-gradient(90deg,#b8741a,#d4973b);height:100%;width:0;transition:width .3s;border-radius:8px}
#stat{font-size:12px;color:#666;font-weight:600;letter-spacing:.04em}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.pulsing{animation:pulse 1.8s ease-in-out infinite}
#retry{display:none;margin-top:20px;padding:11px 22px;background:#b8741a;color:#fff;
  border:none;border-radius:10px;font-size:14px;font-weight:700;cursor:pointer}
</style>
</head>
<body>
<div class="card">
  <div class="icon">📥</div>
  <h1>{{ title }}</h1>
  <p>বইটি আপনার ডিভাইসে সংরক্ষণ করা হচ্ছে।<br>একটু অপেক্ষা করুন…</p>
  <div class="bar-wrap"><div id="prog"></div></div>
  <div id="stat" class="pulsing">সংযোগ স্থাপন হচ্ছে...</div>
  <button id="retry" onclick="location.reload()">🔄 আবার চেষ্টা করুন</button>
</div>
<script>
const TOKEN={{ token|tojson }}, TITLE={{ title|tojson }};
const msgs=[
  'সংযোগ স্থাপন হচ্ছে...','বইটি নামানো শুরু হয়েছে...','একটু সময় লাগছে, ধৈর্য রাখুন...',
  'এই মুহূর্তে আপনার বইটি নামানো হচ্ছে...','প্রায় হয়ে গেল...',
];
let mi=0;
const stat=document.getElementById('stat');
const mt=setInterval(()=>{mi=(mi+1)%msgs.length;stat.textContent=msgs[mi];},2200);
function fail(m){clearInterval(mt);stat.textContent=m;stat.classList.remove('pulsing');stat.style.color='#f87171';document.getElementById('retry').style.display='inline-block';}
async function run(){
  const prog=document.getElementById('prog');
  try{
    stat.textContent='বইটি নামানো শুরু হয়েছে...';
    const resp=await fetch(`/stream-ebook/${encodeURIComponent(TOKEN)}`,{cache:'no-store'});
    if(!resp.ok){const t=await resp.text().catch(()=>'');throw new Error(t||`ত্রুটি (${resp.status})`);}
    const rdr=resp.body.getReader();
    const cl=+(resp.headers.get('Content-Length')||0);
    let recv=0,chunks=[];
    while(true){
      const{done,value}=await rdr.read();
      if(done)break;
      chunks.push(value);recv+=value.length;
      if(cl){const p=Math.min(100,Math.round(recv/cl*100));prog.style.width=p+'%';}
      else{prog.style.width='55%';}
    }
    const blob=new Blob(chunks,{type:'application/epub+zip'});
    const hd=new Uint8Array(await blob.slice(0,4).arrayBuffer());
    if(!(hd[0]===0x50&&hd[1]===0x4B))throw new Error('বইটি সঠিক ফরম্যাটে নেই। Drive শেয়ার সেটিং পরীক্ষা করুন।');
    prog.style.width='88%';stat.textContent='লাইব্রেরিতে সেভ হচ্ছে...';
    const db=await new Promise((res,rej)=>{
      const q=indexedDB.open('BoighorDB',1);
      q.onupgradeneeded=e=>{const d=e.target.result;if(!d.objectStoreNames.contains('books'))d.createObjectStore('books',{keyPath:'token'});};
      q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
    });
    await new Promise((res,rej)=>{
      const tx=db.transaction('books','readwrite');
      tx.objectStore('books').put({token:TOKEN,title:TITLE,format:'epub',blob:blob,added_at:new Date().toISOString()});
      tx.oncomplete=res;tx.onerror=()=>rej(tx.error||new Error('Save failed'));
    });
    prog.style.width='100%';clearInterval(mt);stat.textContent='✅ সফলভাবে সেভ হয়েছে!';stat.style.color='#4ade80';
    try{await fetch(`/mark-used/${encodeURIComponent(TOKEN)}`,{method:'POST'});}catch(_){}
    setTimeout(()=>{window.location.href='/library';},900);
  }catch(e){console.error(e);fail(e.message||'ত্রুটি হয়েছে। পুনরায় চেষ্টা করুন।');}
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
# EPUB Reader  ── COMPLETELY REDESIGNED
#   ✅ 1. Continuous full-book scroll (scrolled-doc)
#   ✅ 2. Default: scroll mode + narrow margin
#   ✅ 3. Tap to hide/show top & bottom bars
#   ✅ 4. 8 beautiful themes (dark as default)
#   ✅ 5. Polished Colibrio-style UI
#   ✅ 6. Last-read bookmark saved to localStorage
#   ✅ 7. Friendly rotating Bengali loading messages
#   ✅ 8. Full offline support via SW
#   ✅ 9. Position + settings auto-saved per book, restored on re-open
#   ✅ 10. TOC chapter-jump panel
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
/* ════════════════════════════════════════
   CSS CUSTOM PROPERTIES  (theme engine)
════════════════════════════════════════ */
:root{
  --bg:#1a1a1a; --cl:#e5e5e5; --ui:#111113; --acc:#f59e0b;
  --bdr:#282828; --sub:#666; --acc2:rgba(245,158,11,.12);
  --bar-t:54px; --bar-b:52px;
}
/* ── THEMES ── */
[data-theme="light"]  {--bg:#f5f1eb;--cl:#1c1c1e;--ui:#e9e5de;--acc:#c47c0d;--bdr:#d8d3cb;--sub:#888;--acc2:rgba(196,124,13,.1);}
[data-theme="sepia"]  {--bg:#f4e4c1;--cl:#3b2f1e;--ui:#e8d5a8;--acc:#8b4513;--bdr:#c8b08c;--sub:#7a6040;--acc2:rgba(139,69,19,.1);}
[data-theme="forest"] {--bg:#0d1f12;--cl:#c8e6c9;--ui:#091608;--acc:#66bb6a;--bdr:#1e3a21;--sub:#4a7a4d;--acc2:rgba(102,187,106,.1);}
[data-theme="ocean"]  {--bg:#0a1628;--cl:#b3d9ff;--ui:#060f1c;--acc:#29b6f6;--bdr:#152238;--sub:#3a6080;--acc2:rgba(41,182,246,.1);}
[data-theme="purple"] {--bg:#1a0a2e;--cl:#e0cfff;--ui:#12061f;--acc:#ba68c8;--bdr:#2e1a4a;--sub:#6a3a80;--acc2:rgba(186,104,200,.1);}
[data-theme="rose"]   {--bg:#fff0f2;--cl:#2d0f15;--ui:#ffe0e5;--acc:#e53935;--bdr:#f0c0c5;--sub:#a06070;--acc2:rgba(229,57,53,.08);}
[data-theme="slate"]  {--bg:#1e2937;--cl:#cfd8dc;--ui:#141e29;--acc:#0288d1;--bdr:#2a3a4a;--sub:#4a6070;--acc2:rgba(2,136,209,.1);}

/* ── BASE ── */
*,*::before,*::after{box-sizing:border-box;}
html,body{margin:0;padding:0;height:100%;background:var(--bg);color:var(--cl);
  overflow:hidden;font-family:'Segoe UI',sans-serif;
  transition:background .3s,color .3s;}
body{user-select:none;-webkit-user-select:none;}
@media print{body{display:none !important;}}

/* ── TOP BAR ── */
#tBar{
  position:fixed;top:0;left:0;right:0;height:var(--bar-t);
  background:var(--ui);border-bottom:1px solid var(--bdr);
  display:flex;align-items:center;justify-content:space-between;
  padding:0 10px 0 12px;z-index:50;
  transition:transform .35s cubic-bezier(.4,0,.2,1),background .3s,border-color .3s;
}
body.bars-hidden #tBar{transform:translateY(-110%);}

/* ── BOTTOM BAR ── */
#bBar{
  position:fixed;left:0;right:0;bottom:0;height:var(--bar-b);
  background:var(--ui);border-top:1px solid var(--bdr);
  display:flex;align-items:center;justify-content:space-between;
  padding:0 12px;z-index:50;
  transition:transform .35s cubic-bezier(.4,0,.2,1),background .3s,border-color .3s;
}
body.bars-hidden #bBar{transform:translateY(110%);}

/* ── PROGRESS BAR ── */
#progBar{
  position:fixed;top:var(--bar-t);left:0;right:0;height:3px;
  background:var(--bdr);z-index:49;
  transition:top .35s cubic-bezier(.4,0,.2,1);
}
body.bars-hidden #progBar{top:0;}
#progFill{height:100%;background:var(--acc);width:0%;transition:width .6s ease;}

/* ── VIEWER ── */
#viewer{
  position:fixed;
  top:calc(var(--bar-t) + 3px);
  left:0;right:0;
  bottom:var(--bar-b);
  background:var(--bg);
  transition:top .35s cubic-bezier(.4,0,.2,1),bottom .35s,background .3s;
}
body.bars-hidden #viewer{top:3px;bottom:0;}
#viewer.scroll-mode{overflow-y:auto !important;-webkit-overflow-scrolling:touch;}

/* ── LOADING OVERLAY ── */
#loadOverlay{
  position:fixed;inset:0;background:var(--bg);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  z-index:200;transition:opacity .4s ease;
}
#loadOverlay.fade-out{opacity:0;pointer-events:none;}
.ld-spinner{
  width:44px;height:44px;border-radius:50%;
  border:3px solid var(--bdr);border-top-color:var(--acc);
  animation:spin 1s linear infinite;margin-bottom:22px;
}
@keyframes spin{to{transform:rotate(360deg);}}
#ldTitle{font-size:15px;font-weight:700;color:var(--cl);margin-bottom:8px;text-align:center;padding:0 24px;}
#ldMsg{font-size:13px;color:var(--sub);text-align:center;transition:opacity .3s;padding:0 24px;}

/* ── TOP BAR ELEMENTS ── */
#tBack{
  color:var(--acc);font-size:13px;font-weight:700;text-decoration:none;
  padding:8px 6px;white-space:nowrap;flex-shrink:0;display:flex;align-items:center;gap:3px;
}
#tTitle{
  flex:1;font-size:12px;color:var(--sub);text-align:center;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 6px;
}
.tbtn{
  width:34px;height:34px;border-radius:50%;border:1.5px solid var(--bdr);
  background:var(--acc2);color:var(--acc);font-size:13px;font-weight:700;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;flex-shrink:0;margin-left:5px;transition:background .2s,border-color .2s;
}
.tbtn:active{background:rgba(245,158,11,.3);}

/* ── BOTTOM BAR ELEMENTS ── */
.nb{
  width:40px;height:40px;border-radius:50%;background:var(--acc);
  color:#fff;border:none;font-size:22px;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;flex-shrink:0;transition:opacity .2s,transform .1s;
}
.nb:active{transform:scale(.88);}
.nb:disabled{opacity:.3;}
#pgInfo{flex:1;text-align:center;font-size:12px;color:var(--sub);
  font-variant-numeric:tabular-nums;letter-spacing:.04em;}

/* ── BACKDROP ── */
#sBd,#tocBd{
  position:fixed;inset:0;background:rgba(0,0,0,0);
  z-index:98;pointer-events:none;transition:background .3s;
}
#sBd.on,#tocBd.on{pointer-events:auto;background:rgba(0,0,0,.7);}

/* ── SETTINGS PANEL ── */
#sPanel{
  position:fixed;bottom:0;left:0;right:0;
  background:var(--ui);border:1px solid var(--bdr);border-bottom:none;
  border-radius:22px 22px 0 0;
  transform:translateY(100%);transition:transform .32s cubic-bezier(.32,.72,0,1);
  z-index:99;max-height:88vh;overflow-y:auto;
}
#sPanel.on{transform:translateY(0);}
.sp-drag{width:36px;height:4px;background:var(--bdr);border-radius:2px;margin:12px auto 0;}
.sp-hd{display:flex;align-items:center;justify-content:space-between;padding:10px 16px 12px;}
.sp-title{font-size:15px;font-weight:700;color:var(--cl);}
.sp-x{width:28px;height:28px;border-radius:50%;border:none;background:rgba(128,128,128,.15);color:var(--sub);cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center;}
.sp-sec{padding:10px 16px 14px;border-top:1px solid var(--bdr);}
.sp-lbl{font-size:10px;color:var(--sub);text-transform:uppercase;letter-spacing:.1em;font-weight:700;margin-bottom:10px;}

/* Flow buttons */
.flow-row{display:flex;gap:8px;}
.flow-btn{flex:1;padding:10px 8px;border:1.5px solid var(--bdr);border-radius:10px;
  background:transparent;color:var(--sub);font-size:12px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;gap:5px;transition:all .2s;}
.flow-btn.on{border-color:var(--acc);color:var(--acc);background:var(--acc2);}

/* Theme grid */
.theme-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;}
.thb{border:2px solid var(--bdr);border-radius:12px;overflow:hidden;
  cursor:pointer;transition:border-color .2s;aspect-ratio:1;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:4px;padding:8px 4px;}
.thb.on{border-color:var(--acc);}
.thb-emoji{font-size:18px;}
.thb-name{font-size:9px;font-weight:700;text-align:center;line-height:1.2;}

/* Step controls */
.step-row{display:flex;align-items:center;gap:10px;}
.step-btn{width:38px;height:38px;border-radius:10px;border:1.5px solid var(--bdr);
  background:transparent;color:var(--cl);font-size:15px;font-weight:700;
  cursor:pointer;display:flex;align-items:center;justify-content:center;
  transition:all .15s;flex-shrink:0;}
.step-btn:active{background:var(--acc2);border-color:var(--acc);}
.step-val{flex:1;text-align:center;font-size:13px;font-weight:600;color:var(--cl);}
.sp-pad{height:14px;}

/* ── TOC PANEL ── */
#tocPanel{
  position:fixed;top:0;right:0;bottom:0;width:min(310px,88vw);
  background:var(--ui);border-left:1px solid var(--bdr);
  transform:translateX(110%);transition:transform .3s cubic-bezier(.32,.72,0,1);
  z-index:99;overflow-y:auto;
}
#tocPanel.on{transform:translateX(0);}
.toc-hd{
  display:flex;align-items:center;justify-content:space-between;
  padding:68px 16px 14px;border-bottom:1px solid var(--bdr);
  position:sticky;top:0;background:var(--ui);
  backdrop-filter:blur(12px);z-index:1;
}
.toc-hd-title{font-size:15px;font-weight:700;color:var(--cl);}
.toc-item{
  padding:13px 18px;border-bottom:1px solid rgba(255,255,255,.04);
  cursor:pointer;transition:background .15s;display:flex;align-items:center;gap:10px;
}
.toc-item:hover,.toc-item:active{background:var(--acc2);}
.toc-dot{width:6px;height:6px;border-radius:50%;background:var(--acc);flex-shrink:0;opacity:.6;}
.toc-label{font-size:13px;color:var(--cl);line-height:1.5;}
.toc-sub{padding-left:34px;}
.toc-empty{padding:28px 18px;font-size:13px;color:var(--sub);text-align:center;}

/* ── RESUME BANNER ── */
#resumeBanner{
  position:fixed;bottom:calc(var(--bar-b) + 12px);left:50%;
  transform:translateX(-50%);
  background:var(--acc);color:#fff;padding:9px 18px;
  border-radius:24px;font-size:12px;font-weight:700;
  z-index:45;display:flex;align-items:center;gap:8px;
  box-shadow:0 4px 20px rgba(0,0,0,.35);cursor:pointer;
  animation:slideUp .4s ease;white-space:nowrap;
}
@keyframes slideUp{from{opacity:0;transform:translateX(-50%) translateY(20px);}to{opacity:1;transform:translateX(-50%) translateY(0);}}
</style>
</head>
<body oncontextmenu="return false;">

<!-- ── LOADING OVERLAY ──────────────────────── -->
<div id="loadOverlay">
  <div class="ld-spinner"></div>
  <div id="ldTitle">বইঘর</div>
  <div id="ldMsg">📖 বইটির পাতা খোলা হচ্ছে...</div>
</div>

<!-- ── TOP BAR ──────────────────────────────── -->
<div id="tBar">
  <a href="/library" id="tBack">← লাইব্রেরি</a>
  <div id="tTitle">লোড হচ্ছে...</div>
  <button class="tbtn" onclick="openTOC()" title="অধ্যায়">☰</button>
  <button class="tbtn" onclick="openSettings()" title="সেটিং">Aa</button>
</div>

<!-- ── PROGRESS BAR ─────────────────────────── -->
<div id="progBar"><div id="progFill"></div></div>

<!-- ── VIEWER ────────────────────────────────── -->
<div id="viewer"></div>

<!-- ── BOTTOM BAR ────────────────────────────── -->
<div id="bBar">
  <button class="nb" id="prev">&#8249;</button>
  <span id="pgInfo">⋯</span>
  <button class="nb" id="next">&#8250;</button>
</div>

<!-- ── SETTINGS BACKDROP + PANEL ────────────── -->
<div id="sBd" onclick="closeSettings()"></div>
<div id="sPanel">
  <div class="sp-drag"></div>
  <div class="sp-hd">
    <span class="sp-title">পড়ার সেটিং</span>
    <button class="sp-x" onclick="closeSettings()">✕</button>
  </div>

  <!-- Flow Mode -->
  <div class="sp-sec">
    <div class="sp-lbl">পড়ার মোড</div>
    <div class="flow-row">
      <button class="flow-btn" id="fPg" onclick="setFlow('paginated')">📖 পাতায় পাতায়</button>
      <button class="flow-btn" id="fSc" onclick="setFlow('scroll')">📜 স্ক্রল মোড</button>
    </div>
  </div>

  <!-- Themes -->
  <div class="sp-sec">
    <div class="sp-lbl">থিম</div>
    <div class="theme-grid" id="themeGrid"></div>
  </div>

  <!-- Font Size -->
  <div class="sp-sec">
    <div class="sp-lbl">ফন্ট সাইজ</div>
    <div class="step-row">
      <button class="step-btn" onclick="stepFont(-1)" style="font-size:12px">A−</button>
      <div class="step-val" id="vF">100%</div>
      <button class="step-btn" onclick="stepFont(1)" style="font-size:18px">A+</button>
    </div>
  </div>

  <!-- Line Spacing -->
  <div class="sp-sec">
    <div class="sp-lbl">লাইন স্পেস</div>
    <div class="step-row">
      <button class="step-btn" onclick="stepLine(-1)">−</button>
      <div class="step-val" id="vL">স্বাভাবিক</div>
      <button class="step-btn" onclick="stepLine(1)">+</button>
    </div>
  </div>

  <!-- Margin -->
  <div class="sp-sec">
    <div class="sp-lbl">মার্জিন</div>
    <div class="step-row">
      <button class="step-btn" onclick="stepMargin(-1)">◀</button>
      <div class="step-val" id="vM">সরু</div>
      <button class="step-btn" onclick="stepMargin(1)">▶</button>
    </div>
  </div>

  <div class="sp-pad"></div>
</div>

<!-- ── TOC BACKDROP + PANEL ──────────────────── -->
<div id="tocBd" onclick="closeTOC()"></div>
<div id="tocPanel">
  <div class="toc-hd">
    <span class="toc-hd-title">অধ্যায়সমূহ</span>
    <button class="sp-x" onclick="closeTOC()">✕</button>
  </div>
  <div id="tocList"></div>
</div>

<script>
/* ═══════════════════════════════════════════
   CONFIGURATION
═══════════════════════════════════════════ */
const THEMES = {
  dark:  {bg:'#1a1a1a',cl:'#e5e5e5',lk:'#f59e0b',name:'রাত',emoji:'🌑'},
  light: {bg:'#f5f1eb',cl:'#1c1c1e',lk:'#0055cc',name:'দিন',emoji:'☀️'},
  sepia: {bg:'#f4e4c1',cl:'#3b2f1e',lk:'#8b4513',name:'সেপিয়া',emoji:'📜'},
  forest:{bg:'#0d1f12',cl:'#c8e6c9',lk:'#66bb6a',name:'বন',emoji:'🌿'},
  ocean: {bg:'#0a1628',cl:'#b3d9ff',lk:'#29b6f6',name:'সমুদ্র',emoji:'🌊'},
  purple:{bg:'#1a0a2e',cl:'#e0cfff',lk:'#ba68c8',name:'বেগুনি',emoji:'🔮'},
  rose:  {bg:'#fff0f2',cl:'#2d0f15',lk:'#e53935',name:'গোলাপ',emoji:'🌹'},
  slate: {bg:'#1e2937',cl:'#cfd8dc',lk:'#0288d1',name:'নীল',emoji:'💙'},
};

const FONTS   = [75, 85, 100, 115, 130, 150, 175];
const LINES   = [{l:'ঘন',v:1.35},{l:'স্বাভাবিক',v:1.65},{l:'প্রশস্ত',v:1.9},{l:'বেশি',v:2.2}];
const MARGINS = [{l:'সরু',v:6},{l:'মাঝারি',v:20},{l:'প্রশস্ত',v:40}];

const PKEY   = 'boighor_prefs';
const LRKEY  = 'boighor_lastread';
const posKey = t => `boighor_pos_${t}`;

/* ── Defaults: scroll mode + narrow margin ── */
let P = {theme:'dark', fi:2, li:1, mi:0, flow:'scroll'};

function loadP(){
  try{
    const s=JSON.parse(localStorage.getItem(PKEY)||'{}');
    if(s.theme&&THEMES[s.theme])P.theme=s.theme;
    if(s.fi!==undefined)P.fi=+s.fi;
    if(s.li!==undefined)P.li=+s.li;
    if(s.mi!==undefined)P.mi=+s.mi;
    if(s.flow)P.flow=s.flow;
  }catch(_){}
}
function saveP(){try{localStorage.setItem(PKEY,JSON.stringify(P));}catch(_){}}

function savePos(cfi,pct){
  try{localStorage.setItem(posKey(TOKEN),JSON.stringify({cfi,pct,time:Date.now()}));}catch(_){}
}
function loadPos(){
  try{return JSON.parse(localStorage.getItem(posKey(TOKEN))||'null');}catch(_){return null;}
}

/* ── Save last-read ── */
function saveLastRead(title,pct){
  try{localStorage.setItem(LRKEY,JSON.stringify({token:TOKEN,title,pct,time:Date.now()}));}catch(_){}
}

/* ═══════════════════════════════════════════
   LOADING MESSAGES
═══════════════════════════════════════════ */
const LOAD_MSGS=[
  '📖 বইটির পাতা খোলা হচ্ছে...',
  '✨ আপনার গল্পের দুনিয়া সাজানো হচ্ছে...',
  '🕐 একটু সময় নিচ্ছে, ধৈর্য রাখুন...',
  '📚 অধ্যায়গুলো সাজানো হচ্ছে...',
  '🌙 পড়ার পরিবেশ তৈরি হচ্ছে...',
  '🎭 গল্পের মঞ্চ প্রস্তুত হচ্ছে...',
  '✍️ প্রায় হয়ে গেল...',
];
let lmi=0, lTimer=null;
function startLoadMsgs(){
  const el=document.getElementById('ldMsg');
  lTimer=setInterval(()=>{
    lmi=(lmi+1)%LOAD_MSGS.length;
    el.style.opacity='0';
    setTimeout(()=>{el.textContent=LOAD_MSGS[lmi];el.style.opacity='1';},300);
  },2200);
}
function stopLoadMsgs(){if(lTimer){clearInterval(lTimer);lTimer=null;}}
function hideLoading(){
  stopLoadMsgs();
  const ov=document.getElementById('loadOverlay');
  ov.classList.add('fade-out');
  setTimeout(()=>{ov.style.display='none';},420);
}

/* ═══════════════════════════════════════════
   BARS TOGGLE  (tap content → show/hide)
═══════════════════════════════════════════ */
let barsOn=true;
function toggleBars(){
  barsOn=!barsOn;
  document.body.classList.toggle('bars-hidden',!barsOn);
}
function showBars(){
  if(!barsOn){barsOn=true;document.body.classList.remove('bars-hidden');}
}

/* ═══════════════════════════════════════════
   THEME APPLICATION
═══════════════════════════════════════════ */
function applyTheme(t){
  document.documentElement.dataset.theme=t;
  document.body.dataset.theme=t;
}
function applyEpubCSS(){
  if(!rend)return;
  const t=THEMES[P.theme]||THEMES.dark;
  const fs=FONTS[P.fi];
  const lh=LINES[P.li].v;
  const mg=MARGINS[P.mi].v;
  rend.themes.default({
    'html':{'background':t.bg+' !important'},
    'body':{
      'background':t.bg+' !important','color':t.cl+' !important',
      'font-size':fs+'% !important','line-height':lh+' !important',
      'padding':'8px '+mg+'px !important','max-width':'100% !important',
      'margin':'0 auto !important',
    },
    'p':{'line-height':lh+' !important','color':t.cl+' !important'},
    'h1,h2,h3,h4,h5,h6':{'color':t.cl+' !important'},
    'a':{'color':t.lk+' !important'},
    'img':{'max-width':'100% !important','height':'auto !important'},
  });
}

function setTheme(k){P.theme=k;saveP();applyTheme(k);applyEpubCSS();syncUI();}
function stepFont(d){P.fi=Math.max(0,Math.min(FONTS.length-1,P.fi+d));saveP();applyEpubCSS();syncUI();}
function stepLine(d){P.li=Math.max(0,Math.min(LINES.length-1,P.li+d));saveP();applyEpubCSS();syncUI();}
function stepMargin(d){P.mi=Math.max(0,Math.min(MARGINS.length-1,P.mi+d));saveP();applyEpubCSS();syncUI();}

window.setTheme=setTheme;window.stepFont=stepFont;window.stepLine=stepLine;window.stepMargin=stepMargin;

/* ═══════════════════════════════════════════
   BUILD THEME GRID
═══════════════════════════════════════════ */
function buildThemeGrid(){
  const grid=document.getElementById('themeGrid');
  Object.entries(THEMES).forEach(([k,v])=>{
    const btn=document.createElement('button');
    btn.className='thb';btn.id='th_'+k;
    btn.style.background=v.bg;
    btn.innerHTML=`<span class="thb-emoji">${v.emoji}</span><span class="thb-name" style="color:${v.cl}">${v.name}</span>`;
    btn.onclick=()=>setTheme(k);
    grid.appendChild(btn);
  });
}

/* ═══════════════════════════════════════════
   SYNC UI
═══════════════════════════════════════════ */
function syncUI(){
  document.getElementById('fPg').classList.toggle('on',P.flow==='paginated');
  document.getElementById('fSc').classList.toggle('on',P.flow==='scroll');
  document.querySelectorAll('.thb').forEach(e=>e.classList.remove('on'));
  const te=document.getElementById('th_'+P.theme);if(te)te.classList.add('on');
  document.getElementById('vF').textContent=FONTS[P.fi]+'%';
  document.getElementById('vL').textContent=LINES[P.li].l;
  document.getElementById('vM').textContent=MARGINS[P.mi].l;
}

/* ═══════════════════════════════════════════
   SETTINGS PANEL
═══════════════════════════════════════════ */
function openSettings(){
  document.getElementById('sPanel').classList.add('on');
  document.getElementById('sBd').classList.add('on');
}
function closeSettings(){
  document.getElementById('sPanel').classList.remove('on');
  document.getElementById('sBd').classList.remove('on');
}
window.openSettings=openSettings;window.closeSettings=closeSettings;

/* ═══════════════════════════════════════════
   FLOW SWITCH
═══════════════════════════════════════════ */
async function setFlow(nf){
  if(P.flow===nf||!book)return;
  let cfi=null;
  try{const loc=rend&&rend.currentLocation();if(loc&&loc.start)cfi=loc.start.cfi;}catch(_){}
  P.flow=nf;saveP();syncUI();
  document.getElementById('viewer').innerHTML='';
  if(rend){try{rend.destroy();}catch(_){}rend=null;}
  await buildRendition(cfi);
}
window.setFlow=setFlow;

/* ═══════════════════════════════════════════
   TOC PANEL
═══════════════════════════════════════════ */
function openTOC(){
  document.getElementById('tocPanel').classList.add('on');
  document.getElementById('tocBd').classList.add('on');
  showBars();
}
function closeTOC(){
  document.getElementById('tocPanel').classList.remove('on');
  document.getElementById('tocBd').classList.remove('on');
}
window.openTOC=openTOC;window.closeTOC=closeTOC;

function esc(s){return String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

async function buildTOC(){
  const list=document.getElementById('tocList');
  try{
    const nav=await book.loaded.navigation;
    const items=nav.toc||[];
    if(!items.length){list.innerHTML='<div class="toc-empty">সূচিপত্র পাওয়া যায়নি</div>';return;}
    renderTOCItems(items,list,0);
  }catch(e){
    list.innerHTML='<div class="toc-empty">সূচিপত্র লোড করা যায়নি</div>';
  }
}
function renderTOCItems(items,container,level){
  items.forEach(item=>{
    const el=document.createElement('div');
    el.className='toc-item'+(level>0?' toc-sub':'');
    el.style.paddingLeft=(18+level*14)+'px';
    el.innerHTML=`<span class="toc-dot"></span><span class="toc-label">${esc(item.label||'')}</span>`;
    el.onclick=()=>{if(rend)rend.display(item.href);closeTOC();};
    container.appendChild(el);
    if(item.subitems&&item.subitems.length)renderTOCItems(item.subitems,container,level+1);
  });
}

/* ═══════════════════════════════════════════
   PROGRESS / POSITION
═══════════════════════════════════════════ */
function onRelocated(loc){
  if(!loc||!loc.start)return;
  const pct=loc.start.percentage?Math.round(loc.start.percentage*100):0;
  document.getElementById('progFill').style.width=pct+'%';
  document.getElementById('pgInfo').textContent=pct+'%';
  // Save position + last read
  if(loc.start.cfi)savePos(loc.start.cfi,pct);
  try{saveLastRead(document.getElementById('tTitle').textContent,pct);}catch(_){}
}

/* ═══════════════════════════════════════════
   BUILD RENDITION
═══════════════════════════════════════════ */
let rend=null,book=null;
const TOKEN=new URLSearchParams(window.location.search).get('token');

async function buildRendition(startCfi){
  const viewer=document.getElementById('viewer');
  const isScroll=P.flow==='scroll';

  viewer.classList.toggle('scroll-mode',isScroll);

  /* ── Continuous scroll: scrolled-doc uses ContinuousViewManager ── */
  const opts={
    width:'100%',
    height:'100%',
    spread:'none',
    flow: isScroll ? 'scrolled-doc' : 'paginated',
  };

  rend=book.renderTo('viewer',opts);
  applyEpubCSS();

  await rend.display(startCfi||undefined);

  // Progress tracking
  rend.on('relocated', onRelocated);

  // Tap inside epub content → toggle bars
  rend.on('click', ()=>toggleBars());

  // Prev / Next
  document.getElementById('prev').onclick=()=>rend.prev();
  document.getElementById('next').onclick=()=>rend.next();

  // In scroll mode dim nav buttons (chapter-level only)
  const navOp=isScroll?'0.45':'1';
  document.getElementById('prev').style.opacity=navOp;
  document.getElementById('next').style.opacity=navOp;
}

/* ═══════════════════════════════════════════
   ERROR
═══════════════════════════════════════════ */
function showError(msg){
  hideLoading();
  document.getElementById('pgInfo').textContent='';
  const isCDN=/JSZip|EPUB|CDN/i.test(msg||'');
  document.getElementById('viewer').innerHTML=`
    <div style="margin:60px 16px 0;padding:24px;background:rgba(127,29,29,.2);
      border:1px solid #7f1d1d;border-radius:16px;text-align:center">
      <div style="font-size:48px;margin-bottom:12px">⚠️</div>
      <h2 style="color:#fca5a5;margin-bottom:8px;font-weight:700">বইটি খোলা যাচ্ছে না</h2>
      <p style="color:#d4d4d8;font-size:13px;margin-bottom:18px;line-height:1.65">${esc(msg)}</p>
      ${isCDN
        ?`<button onclick="location.reload()" style="padding:10px 22px;background:#d97706;color:#fff;border:0;border-radius:10px;font-size:14px;font-weight:700">🔄 আবার চেষ্টা করুন</button>
          <p style="font-size:11px;color:#fca5a5;margin-top:10px">বই ডিলিট করবেন না।</p>`
        :`<button onclick="delBook()" style="padding:10px 22px;background:#dc2626;color:#fff;border:0;border-radius:10px;font-size:14px;font-weight:700">🗑️ মুছে পুনরায় Import করুন</button>`}
      <br><a href="/library" style="display:inline-block;margin-top:14px;color:var(--acc);font-size:14px">← লাইব্রেরিতে ফিরুন</a>
    </div>`;
}

/* ═══════════════════════════════════════════
   INDEXEDDB
═══════════════════════════════════════════ */
async function openDB(){
  return new Promise((res,rej)=>{
    const q=indexedDB.open('BoighorDB',1);
    q.onupgradeneeded=e=>{const d=e.target.result;if(!d.objectStoreNames.contains('books'))d.createObjectStore('books',{keyPath:'token'});};
    q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
  });
}

async function delBook(){
  try{const db=await openDB();await new Promise((res,rej)=>{const tx=db.transaction('books','readwrite');tx.objectStore('books').delete(TOKEN);tx.oncomplete=res;tx.onerror=()=>rej(tx.error);});}catch(_){}
  window.location.href='/library';
}
window.delBook=delBook;

/* ═══════════════════════════════════════════
   KEYBOARD
═══════════════════════════════════════════ */
document.addEventListener('keydown',e=>{
  const k=(e.key||'').toLowerCase();
  if(e.ctrlKey&&['p','s','u'].includes(k))e.preventDefault();
  if(e.key==='F12')e.preventDefault();
  if(e.ctrlKey&&e.shiftKey&&['i','j'].includes(k))e.preventDefault();
  if(e.key==='Escape'){closeSettings();closeTOC();}
  if(e.key==='ArrowLeft'&&rend)rend.prev();
  if(e.key==='ArrowRight'&&rend)rend.next();
});

/* ═══════════════════════════════════════════
   MAIN INIT
═══════════════════════════════════════════ */
async function init(){
  try{
    if(!TOKEN)throw new Error('Token নেই। সঠিক লিংক ব্যবহার করুন।');
    if(!window.JSZip)throw new Error('JSZip লোড হয়নি। ইন্টারনেট/CDN সমস্যা।');
    if(!window.ePub)throw new Error('EPUB লাইব্রেরি লোড হয়নি। ইন্টারনেট/CDN সমস্যা।');

    loadP();
    applyTheme(P.theme);
    buildThemeGrid();
    syncUI();
    startLoadMsgs();

    // Load from IndexedDB
    const db=await openDB();
    const saved=await new Promise((res,rej)=>{
      const tx=db.transaction('books','readonly');
      const q=tx.objectStore('books').get(TOKEN);
      q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);
    });

    if(!saved)throw new Error('বইটি এই ডিভাইসে পাওয়া যায়নি। পুনরায় ডাউনলোড করুন।');
    if(!saved.blob||typeof saved.blob.arrayBuffer!=='function')throw new Error('বইয়ের ডেটা নষ্ট। মুছে পুনরায় Download করুন।');

    const title=saved.title||'ইবুক';
    document.getElementById('tTitle').textContent=title;
    document.getElementById('ldTitle').textContent=title;
    document.title=title+' — বইঘর';

    const ab=await saved.blob.arrayBuffer();
    if(!ab||ab.byteLength<10)throw new Error('বইটি খালি বা নষ্ট।');
    const hd=new Uint8Array(ab.slice(0,4));
    if(!(hd[0]===0x50&&hd[1]===0x4B))throw new Error('বইটি সঠিক EPUB ফরম্যাটে নেই।');

    book=ePub(ab);
    buildTOC();

    // Restore saved position
    const savedPos=loadPos();

    await buildRendition(savedPos&&savedPos.cfi?savedPos.cfi:null);

    hideLoading();

    // Show resume banner if meaningful progress exists
    if(savedPos&&savedPos.pct>2){
      showResumeBanner(savedPos.pct,savedPos.cfi);
    }

  }catch(e){
    console.error(e);
    showError(e.message||'অজানা ত্রুটি হয়েছে।');
  }
}

/* ── Resume banner ────────────────────────── */
function showResumeBanner(pct,cfi){
  const b=document.createElement('div');
  b.id='resumeBanner';
  b.innerHTML=`📌 আপনি ${pct}% পর্যন্ত পড়েছিলেন &nbsp;·&nbsp; ট্যাপ করুন`;
  b.onclick=()=>{if(rend&&cfi)rend.display(cfi);b.remove();};
  document.body.appendChild(b);
  setTimeout(()=>{if(b.parentNode)b.remove();},6000);
}

/* ── SW Registration ──────────────────────── */
if('serviceWorker' in navigator){
  window.addEventListener('load',()=>{
    navigator.serviceWorker.register('/sw.js').catch(()=>{});
  });
}

window.addEventListener('resize',()=>{if(rend)rend.resize();});
init();
</script>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════
# Reader Route
# ══════════════════════════════════════════════════════════════

@app.route("/reader")
def reader():
    return render_template_string(READER_HTML, store=STORE_NAME)

# ══════════════════════════════════════════════════════════════
# Service Worker Route  (NEW — enables offline PWA)
# ══════════════════════════════════════════════════════════════

@app.route("/sw.js")
def service_worker():
    return Response(SW_JS, mimetype="application/javascript",
                    headers={"Service-Worker-Allowed": "/"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
