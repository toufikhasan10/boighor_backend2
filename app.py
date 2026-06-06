import os
import re
import html
import uuid
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
ADMIN_KEY = os.environ.get("ADMIN_KEY", "boighor2024")
STORE_NAME = os.environ.get("STORE_NAME", "বইঘর")
BACKEND_URL = os.environ.get("BACKEND_URL", "").rstrip("/")
GAS_URL = os.environ.get("GAS_URL", "")

# ══════════════════════════════════════════════════════════════
# Firebase & Backend Helpers
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


def extract_drive_file_id(url):
    """Supports https://drive.google.com/file/d/<id>/... and ?id=<id>."""
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
    """
    Returns a streaming requests.Response for a Google Drive EPUB/PDF/ebook file.
    Handles Google Drive's confirmation page for larger files.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    file_id = extract_drive_file_id(url)

    if not file_id:
        return requests.get(url, stream=True, timeout=30, headers=headers, allow_redirects=True)

    session = requests.Session()
    base = "https://drive.google.com/uc"
    params = {"export": "download", "id": file_id}

    # First request is not streamed so we can inspect Drive confirmation HTML.
    first = session.get(base, params=params, timeout=30, headers=headers, allow_redirects=True)

    confirm_token = None
    for key, value in first.cookies.items():
        if key.startswith("download_warning"):
            confirm_token = value
            break

    if confirm_token:
        return session.get(
            base,
            params={"export": "download", "id": file_id, "confirm": confirm_token},
            stream=True,
            timeout=30,
            headers=headers,
            allow_redirects=True,
        )

    # If Drive directly returned the file body, wrap it as a streaming-like response.
    content_type = (first.headers.get("Content-Type") or "").lower()
    first_bytes = first.content[:64]
    if first.ok and (
        first_bytes.startswith(b"PK") or
        first_bytes.startswith(b"%PDF") or
        "application/epub" in content_type or
        "application/octet-stream" in content_type or
        "application/pdf" in content_type
    ):
        class MemoryResponse:
            status_code = first.status_code
            ok = first.ok
            headers = first.headers
            content = first.content

            def iter_content(self, chunk_size=8192):
                for i in range(0, len(self.content), chunk_size):
                    yield self.content[i:i + chunk_size]

        return MemoryResponse()

    # Some Drive confirmation pages put the real URL in an href.
    text = first.text or ""
    m = re.search(r'href="([^"]*?/uc\?export=download[^"]+)"', text)
    if m:
        href = html.unescape(m.group(1))
        if href.startswith("/"):
            href = "https://drive.google.com" + href
        return session.get(href, stream=True, timeout=30, headers=headers, allow_redirects=True)

    return first


def send_email_via_gas(to_email, buyer_name, book_title, download_url):
    if not GAS_URL:
        return False
    try:
        params = urllib.parse.urlencode({
            "action": "sendEmail",
            "to": to_email,
            "name": buyer_name,
            "book": book_title,
            "link": download_url,
            "store": STORE_NAME,
        })
        r = requests.get(f"{GAS_URL}?{params}", timeout=20, allow_redirects=True)
        return r.status_code == 200 and "error" not in r.text.lower()
    except Exception:
        return False


def absolute_backend_url():
    if BACKEND_URL:
        return BACKEND_URL
    return request.url_root.rstrip("/")

# ══════════════════════════════════════════════════════════════
# Basic routes
# ══════════════════════════════════════════════════════════════

@app.route("/")
def home():
    return redirect("/library")


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": STORE_NAME,
        "short_name": STORE_NAME,
        "start_url": "/library",
        "display": "standalone",
        "background_color": "#1a1a1a",
        "theme_color": "#b8741a",
        "icons": [{
            "src": "https://cdn-icons-png.flaticon.com/512/3389/3389037.png",
            "sizes": "512x512",
            "type": "image/png",
        }],
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok", "firebase": "✅" if fb_get("ping") is not False else "⚠️"})

# ══════════════════════════════════════════════════════════════
# Admin/token routes
# ══════════════════════════════════════════════════════════════

@app.route("/admin/issue-token", methods=["POST", "OPTIONS"])
def issue_token():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(force=True) or {}
    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    email = (data.get("email") or "").strip()
    buyer_name = (data.get("buyer_name") or "ক্রেতা").strip()
    book_title = (data.get("book_title") or "").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_id = (data.get("book_id") or "").strip()

    if not drive_link and book_id:
        book_data = fb_get(f"books/{book_id}")
        if isinstance(book_data, dict):
            drive_link = (book_data.get("ebookLink") or "").strip()
            if not book_title:
                book_title = (book_data.get("title") or "").strip()

    if not email or not drive_link:
        return jsonify({"success": False, "error": "Email or Book Link missing"}), 400

    token = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{absolute_backend_url()}/download/{token}"

    saved = fb_set(f"tokens/{token}", {
        "email": email,
        "drive_link": drive_link,
        "book_title": book_title or "ইবুক",
        "buyer_name": buyer_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "used": False,
        "format": "epub",
    })

    if not saved:
        return jsonify({"success": False, "error": "Firebase Error", "retry": True}), 503

    email_sent = send_email_via_gas(email, buyer_name, book_title or "ইবুক", download_url)
    return jsonify({"success": True, "token": token, "download_url": download_url, "email_sent": email_sent})


@app.route("/resend-link", methods=["POST", "OPTIONS"])
def resend_link():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(force=True) or {}
    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    email = (data.get("email") or "").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_title = (data.get("book_title") or "ইবুক").strip()
    if not email or not drive_link:
        return jsonify({"success": False, "error": "Missing data"}), 400

    token = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{absolute_backend_url()}/download/{token}"
    saved = fb_set(f"tokens/{token}", {
        "email": email,
        "drive_link": drive_link,
        "book_title": book_title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "used": False,
        "format": "epub",
    })
    if not saved:
        return jsonify({"success": False, "error": "Firebase unavailable"}), 503

    send_email_via_gas(email, "ক্রেতা", book_title, download_url)
    return jsonify({"success": True, "token": token, "download_url": download_url})

# ══════════════════════════════════════════════════════════════
# Library
# ══════════════════════════════════════════════════════════════

@app.route("/library")
def library():
    return render_template_string("""
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>আমার লাইব্রেরি — {{ store }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="manifest" href="/manifest.json">
    <style>
        body { background-color: #121212; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; }
        .book-card { transition: transform 0.2s; cursor: pointer; }
        .book-card:active { transform: scale(0.95); }
    </style>
</head>
<body class="p-4">
    <header class="flex justify-between items-center mb-8 pt-4">
        <h1 class="text-2xl font-bold text-amber-500">{{ store }} লাইব্রেরি</h1>
        <div class="text-xs bg-amber-900/30 text-amber-200 px-3 py-1 rounded-full border border-amber-700">প্রিমিয়াম এক্সেস</div>
    </header>

    <div id="bookshelf" class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4"></div>

    <div id="empty-state" class="hidden text-center py-20">
        <div class="text-5xl mb-4">📚</div>
        <p class="text-gray-500">আপনার লাইব্রেরিতে এখনো কোনো বই নেই।</p>
    </div>

    <script>
        const DB_NAME = 'BoighorDB';
        const STORE_NAME = 'books';

        async function openDB() {
            return new Promise((resolve, reject) => {
                const req = indexedDB.open(DB_NAME, 1);
                req.onupgradeneeded = (e) => {
                    const db = e.target.result;
                    if (!db.objectStoreNames.contains(STORE_NAME)) db.createObjectStore(STORE_NAME, { keyPath: 'token' });
                };
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => reject(req.error);
            });
        }

        async function loadLibrary() {
            try {
                const db = await openDB();
                const books = await new Promise((resolve, reject) => {
                    const tx = db.transaction(STORE_NAME, 'readonly');
                    const req = tx.objectStore(STORE_NAME).getAll();
                    req.onsuccess = () => resolve(req.result || []);
                    req.onerror = () => reject(req.error);
                });

                const bookshelf = document.getElementById('bookshelf');
                const emptyState = document.getElementById('empty-state');

                if (!books.length) {
                    emptyState.classList.remove('hidden');
                    return;
                }

                books.forEach(book => {
                    const div = document.createElement('div');
                    div.className = 'book-card bg-zinc-900 p-3 rounded-xl border border-zinc-800 text-center';
                    div.innerHTML = `
                        <div class="aspect-[3/4] bg-zinc-800 rounded-lg mb-3 flex items-center justify-center text-3xl shadow-inner">📘</div>
                        <div class="text-sm font-medium truncate">${book.title || 'ইবুক'}</div>
                    `;
                    div.onclick = () => { window.location.href = `/reader?token=${encodeURIComponent(book.token)}`; };
                    bookshelf.appendChild(div);
                });
            } catch (e) {
                console.error(e);
                const emptyState = document.getElementById('empty-state');
                emptyState.classList.remove('hidden');
                emptyState.innerHTML = '<p class="text-red-400">লাইব্রেরি লোড করা যায়নি।</p>';
            }
        }
        loadLibrary();
    </script>
</body>
</html>
""", store=STORE_NAME)

# ══════════════════════════════════════════════════════════════
# Download/import EPUB
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
            return "⌛ মেয়াদ শেষ।", 410
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
        @keyframes pulse-custom { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .animate-pulse-custom { animation: pulse-custom 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
    </style>
</head>
<body class="bg-zinc-950 text-white flex items-center justify-center min-h-screen p-6">
    <div class="text-center max-w-sm">
        <div class="text-6xl mb-6 animate-bounce">📥</div>
        <h1 class="text-2xl font-bold mb-2">{{ title }}</h1>
        <p class="text-zinc-400 mb-8">আপনার লাইব্রেরিতে EPUB বইটি সেভ করা হচ্ছে। অনুগ্রহ করে অপেক্ষা করুন...</p>

        <div class="w-full bg-zinc-800 h-2 rounded-full overflow-hidden mb-4">
            <div id="progress" class="bg-amber-500 h-full w-0 transition-all duration-300"></div>
        </div>
        <div id="status" class="text-xs text-zinc-500 uppercase tracking-widest animate-pulse-custom">Connecting to secure stream...</div>
        <button id="retry" class="hidden mt-6 px-4 py-2 bg-amber-600 rounded-lg text-white" onclick="location.reload()">আবার চেষ্টা করুন</button>
    </div>

    <script>
        const TOKEN = {{ token|tojson }};
        const TITLE = {{ title|tojson }};

        function fail(msg) {
            const status = document.getElementById('status');
            status.innerText = msg;
            status.classList.add('text-red-500');
            document.getElementById('retry').classList.remove('hidden');
        }

        async function importBook() {
            const progress = document.getElementById('progress');
            const status = document.getElementById('status');

            try {
                status.innerText = "Downloading secure EPUB...";
                const response = await fetch(`/stream-ebook/${encodeURIComponent(TOKEN)}`, { cache: 'no-store' });
                if (!response.ok) {
                    const txt = await response.text().catch(() => '');
                    throw new Error(txt || `Stream failed (${response.status})`);
                }

                const reader = response.body.getReader();
                const contentLength = +(response.headers.get('Content-Length') || 0);
                let receivedLength = 0;
                let chunks = [];

                while (true) {
                    const {done, value} = await reader.read();
                    if (done) break;
                    chunks.push(value);
                    receivedLength += value.length;
                    if (contentLength) {
                        const pct = Math.min(100, Math.round((receivedLength / contentLength) * 100));
                        progress.style.width = pct + '%';
                        status.innerText = `Downloading: ${pct}%`;
                    } else {
                        progress.style.width = '60%';
                        status.innerText = `Downloading: ${Math.round(receivedLength / 1024)} KB`;
                    }
                }

                const blob = new Blob(chunks, { type: 'application/epub+zip' });
                const head = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
                if (!(head[0] === 0x50 && head[1] === 0x4B)) {
                    throw new Error('Downloaded file is not a valid EPUB. Google Drive link/share setting check করুন।');
                }

                progress.style.width = '90%';
                status.innerText = "Saving to library...";

                const db = await new Promise((resolve, reject) => {
                    const req = indexedDB.open('BoighorDB', 1);
                    req.onupgradeneeded = (e) => {
                        const db = e.target.result;
                        if (!db.objectStoreNames.contains('books')) db.createObjectStore('books', { keyPath: 'token' });
                    };
                    req.onsuccess = () => resolve(req.result);
                    req.onerror = () => reject(req.error);
                });

                await new Promise((resolve, reject) => {
                    const tx = db.transaction('books', 'readwrite');
                    tx.objectStore('books').put({
                        token: TOKEN,
                        title: TITLE,
                        format: 'epub',
                        blob: blob,
                        added_at: new Date().toISOString()
                    });
                    tx.oncomplete = resolve;
                    tx.onerror = () => reject(tx.error);
                    tx.onabort = () => reject(tx.error || new Error('IndexedDB save aborted'));
                });

                progress.style.width = '100%';
                status.innerText = "Success! Redirecting...";
                // Mark this token as used in Firebase so the link cannot be reused.
                // This only runs after the EPUB is confirmed saved to IndexedDB.
                // If this fetch fails, we still proceed to library — non-critical.
                try {
                    await fetch(`/mark-used/${encodeURIComponent(TOKEN)}`, { method: 'POST' });
                } catch (markErr) {
                    console.warn('Could not mark token as used:', markErr);
                }
                setTimeout(() => { window.location.href = '/library'; }, 700);
            } catch (e) {
                console.error(e);
                fail(e.message || 'Error occurred. Please try again.');
            }
        }
        importBook();
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
        r = open_drive_file_response(drive_link)
        status_code = getattr(r, "status_code", 500)
        if status_code < 200 or status_code >= 300:
            return f"Google Drive returned HTTP {status_code}", 502

        content_length = getattr(r, "headers", {}).get("Content-Length")
        iterator = r.iter_content(chunk_size=8192)
        first_chunk = next(iterator, b"")
        if not first_chunk:
            return "Empty ebook response", 502

        # EPUB is a ZIP file and starts with PK. This prevents saving Drive HTML pages as EPUB.
        if not first_chunk[:4].startswith(b"PK"):
            if first_chunk[:1024].lstrip().startswith(b"<"):
                return "Google Drive did not return the EPUB file. Set sharing to 'Anyone with the link' and use direct file link.", 502
            return "Downloaded file is not an EPUB/ZIP file.", 502

        def generate():
            yield first_chunk
            for chunk in iterator:
                if chunk:
                    yield chunk

        headers = {"Cache-Control": "no-store"}
        if content_length:
            headers["Content-Length"] = content_length
        return Response(stream_with_context(generate()), content_type="application/epub+zip", headers=headers)
    except Exception as e:
        print("stream_ebook error:", repr(e), flush=True)
        return "Internal Server Error while streaming ebook", 500

@app.route("/mark-used/<token>", methods=["POST"])
def mark_used(token):
    t = fb_get(f"tokens/{token}")
    if not isinstance(t, dict):
        return jsonify({"ok": False, "reason": "not found"}), 404
    if t.get("used"):
        # Already marked — idempotent, return success
        return jsonify({"ok": True, "reason": "already marked"})
    fb_set(f"tokens/{token}/used", True)
    fb_set(f"tokens/{token}/used_at", datetime.now(timezone.utc).isoformat())
    return jsonify({"ok": True})

# Backward compatibility if old pages call /stream-pdf
@app.route("/stream-pdf/<token>")
def stream_pdf_backward_compat(token):
    return stream_ebook(token)

# ══════════════════════════════════════════════════════════════
# EPUB Reader
# ══════════════════════════════════════════════════════════════

@app.route("/reader")
def reader():
    return render_template_string("""
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
    <title>ইবুক রিডার — {{ store }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/epubjs@0.3.93/dist/epub.min.js"></script>
    <style>
        html, body { margin: 0; padding: 0; height: 100%; background: #1a1a1a; color: #eee; overflow: hidden; }
        body { user-select: none; -webkit-user-select: none; font-family: 'Segoe UI', sans-serif; }
        @media print { body { display: none !important; } }
        #viewer { position: fixed; top: 64px; left: 0; right: 0; bottom: 54px; background: #1a1a1a; }
        .navbtn { width: 46px; height: 46px; border-radius: 999px; background: rgba(180,116,26,0.95); color: white; display: flex; align-items: center; justify-content: center; font-size: 22px; }
        .navbtn:disabled { opacity: .35; }
        #bottomBar { position: fixed; left: 0; right: 0; bottom: 0; height: 54px; background: rgba(24,24,27,.95); border-top: 1px solid #333; display: flex; align-items: center; justify-content: space-between; padding: 0 14px; }
    </style>
</head>
<body oncontextmenu="return false;">
    <nav class="fixed top-0 w-full h-16 bg-zinc-900/95 border-b border-zinc-800 z-50 px-4 flex items-center justify-between">
        <a href="/library" class="text-amber-500 text-sm font-bold flex items-center"><span class="mr-2">←</span> লাইব্রেরি</a>
        <span id="page-info" class="text-xs text-zinc-400">Loading...</span>
    </nav>

    <div id="viewer"></div>

    <div id="bottomBar">
        <button id="prev" class="navbtn">‹</button>
        <div id="book-title" class="text-xs text-zinc-400 truncate px-3">ইবুক</div>
        <button id="next" class="navbtn">›</button>
    </div>

    <script>
        const token = new URLSearchParams(window.location.search).get('token');
        const viewer = document.getElementById('viewer');
        const pageInfo = document.getElementById('page-info');
        const titleEl = document.getElementById('book-title');
        const prevBtn = document.getElementById('prev');
        const nextBtn = document.getElementById('next');
        let rendition = null;
        let epubBook = null;

        function escapeHtml(s) {
            return String(s || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
        }

        function showError(message) {
            pageInfo.innerText = 'Error';
            const isLibraryLoadError = /JSZip|EPUB reader library|CDN/i.test(message || '');
            viewer.innerHTML = `
                <div style="margin:70px 16px 0; padding:20px; border:1px solid #7f1d1d; background:rgba(127,29,29,.25); border-radius:16px; text-align:center;">
                    <div style="font-size:42px; margin-bottom:10px;">⚠️</div>
                    <h2 style="font-weight:700; color:#fca5a5; margin-bottom:8px;">বইটি খোলা যাচ্ছে না</h2>
                    <p style="font-size:14px; color:#d4d4d8; margin-bottom:16px;">${escapeHtml(message)}</p>
                    ${isLibraryLoadError ? '<button onclick="location.reload()" style="padding:10px 14px; background:#d97706; color:white; border:0; border-radius:10px;">আবার চেষ্টা করুন</button><p style="font-size:12px; color:#fca5a5; margin-top:12px;">এটা reader library loading error — বই ডিলিট করবেন না।</p>' : '<button onclick="deleteBadBook()" style="padding:10px 14px; background:#dc2626; color:white; border:0; border-radius:10px;">লাইব্রেরি থেকে মুছে আবার Import করুন</button>'}
                    <a href="/library" style="display:block; margin-top:16px; color:#f59e0b; font-size:14px;">লাইব্রেরিতে ফিরুন</a>
                </div>`;
        }

        async function openDB() {
            return new Promise((resolve, reject) => {
                const req = indexedDB.open('BoighorDB', 1);
                req.onupgradeneeded = (e) => {
                    const db = e.target.result;
                    if (!db.objectStoreNames.contains('books')) db.createObjectStore('books', { keyPath: 'token' });
                };
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => reject(req.error);
            });
        }

        async function deleteBadBook() {
            try {
                const db = await openDB();
                await new Promise((resolve, reject) => {
                    const tx = db.transaction('books', 'readwrite');
                    tx.objectStore('books').delete(token);
                    tx.oncomplete = resolve;
                    tx.onerror = () => reject(tx.error);
                });
            } catch (e) { console.error(e); }
            window.location.href = '/library';
        }
        window.deleteBadBook = deleteBadBook;

        document.addEventListener('keydown', e => {
            const k = (e.key || '').toLowerCase();
            if (e.ctrlKey && ['p', 's', 'u'].includes(k)) e.preventDefault();
            if (e.key === 'F12') e.preventDefault();
            if (e.ctrlKey && e.shiftKey && ['i', 'j'].includes(k)) e.preventDefault();
            if (e.key === 'ArrowLeft') rendition && rendition.prev();
            if (e.key === 'ArrowRight') rendition && rendition.next();
        });

        async function initReader() {
            try {
                if (!token) throw new Error('Token missing.');
                if (!window.JSZip) throw new Error('JSZip lib not loaded. Internet/CDN blocked হতে পারে।');
                if (!window.ePub) throw new Error('EPUB reader library did not load. Internet/CDN blocked হতে পারে।');

                const db = await openDB();
                const savedBook = await new Promise((resolve, reject) => {
                    const tx = db.transaction('books', 'readonly');
                    const req = tx.objectStore('books').get(token);
                    req.onsuccess = () => resolve(req.result);
                    req.onerror = () => reject(req.error);
                });

                if (!savedBook) throw new Error('বইটি এই ডিভাইসের লাইব্রেরিতে পাওয়া যায়নি।');
                if (!savedBook.blob || typeof savedBook.blob.arrayBuffer !== 'function') throw new Error('Saved book data corrupted.');

                titleEl.innerText = savedBook.title || 'ইবুক';

                const arrayBuffer = await savedBook.blob.arrayBuffer();
                if (!arrayBuffer || arrayBuffer.byteLength < 10) throw new Error('Saved EPUB is empty.');
                const head = new Uint8Array(arrayBuffer.slice(0, 4));
                if (!(head[0] === 0x50 && head[1] === 0x4B)) {
                    throw new Error('Saved file is not an EPUB. পুরোনো ভুল import মুছে আবার import করুন।');
                }

                epubBook = ePub(arrayBuffer);
                rendition = epubBook.renderTo('viewer', {
                    width: '100%',
                    height: '100%',
                    spread: 'none',
                    flow: 'paginated'
                });

                rendition.themes.default({
                    'body': {
                        'background': '#1a1a1a !important',
                        'color': '#e5e5e5 !important',
                        'font-size': '18px !important',
                        'line-height': '1.65 !important'
                    },
                    'p': { 'line-height': '1.65 !important' },
                    'a': { 'color': '#f59e0b !important' }
                });

                await rendition.display();
                pageInfo.innerText = 'Ready';

                rendition.on('relocated', (location) => {
                    if (location && location.start) {
                        pageInfo.innerText = location.start.percentage ? Math.round(location.start.percentage * 100) + '%' : 'Reading';
                    }
                });

                prevBtn.onclick = () => rendition.prev();
                nextBtn.onclick = () => rendition.next();

                // Tap left/right side for navigation
                viewer.addEventListener('click', (e) => {
                    if (!rendition) return;
                    if (e.clientX < window.innerWidth * 0.35) rendition.prev();
                    else if (e.clientX > window.innerWidth * 0.65) rendition.next();
                });
            } catch (e) {
                console.error(e);
                showError(e.message || 'Unknown EPUB reader error.');
            }
        }

        window.addEventListener('resize', () => {
            if (rendition) rendition.resize();
        });

        initReader();
    </script>
</body>
</html>
""", store=STORE_NAME)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
