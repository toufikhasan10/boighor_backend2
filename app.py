import os, uuid, urllib.parse, time
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, request, jsonify, redirect, Response, render_template_string
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ══════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════
FIREBASE_URL = os.environ.get("FIREBASE_URL", "").rstrip("/")
ADMIN_KEY    = os.environ.get("ADMIN_KEY", "boighor2024")
STORE_NAME   = os.environ.get("STORE_NAME", "বইঘর")
BACKEND_URL  = os.environ.get("BACKEND_URL", "")
GAS_URL      = os.environ.get("GAS_URL", "")

# ══════════════════════════════════════════════════════════════
# Firebase & Backend Helpers
# ══════════════════════════════════════════════════════════════

def fb_get(path, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(f"{FIREBASE_URL}/{path}.json", timeout=12)
            if r.ok: return r.json()
            return False
        except Exception as e:
            if attempt < max_retries: time.sleep(1.5)
    return False

def fb_set(path, data, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            r = requests.put(f"{FIREBASE_URL}/{path}.json", json=data, timeout=12)
            if r.ok: return True
        except Exception as e:
            if attempt < max_retries: time.sleep(1.5)
    return False

def get_google_drive_direct_link(url):
    if not url: return None
    try:
        if "drive.google.com" in url:
            if "/file/d/" in url:
                file_id = url.split("/file/d/")[1].split("/")[0]
                return f"https://drive.google.com/uc?export=download&id={file_id}"
    except: pass
    return url

def send_email_via_gas(to_email, buyer_name, book_title, download_url):
    if not GAS_URL: return False
    try:
        params = urllib.parse.urlencode({
            "action": "sendEmail", "to": to_email, "name": buyer_name,
            "book": book_title, "link": download_url, "store": STORE_NAME
        })
        r = requests.get(f"{GAS_URL}?{params}", timeout=20, allow_redirects=True)
        return r.status_code == 200 and "error" not in r.text.lower()
    except: return False

# ══════════════════════════════════════════════════════════════
# PWA Manifest
# ══════════════════════════════════════════════════════════════

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
            "type": "image/png"
        }]
    })

# ══════════════════════════════════════════════════════════════
# Core Routes
# ══════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    return jsonify({"status": "ok", "firebase": "✅" if fb_get("ping") is not False else "⚠️"})

@app.route("/admin/issue-token", methods=["POST"])
def issue_token():
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
            drive_link = book_data.get("ebookLink", "").strip()
            if not book_title: book_title = book_data.get("title", "").strip()

    if not email or not drive_link:
        return jsonify({"success": False, "error": "Email or Book Link missing"}), 400

    token = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{BACKEND_URL}/download/{token}"

    saved = fb_set(f"tokens/{token}", {
        "email": email, "drive_link": drive_link, "book_title": book_title,
        "buyer_name": buyer_name, "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at, "used": False
    })

    if not saved:
        return jsonify({"success": False, "error": "Firebase Error", "retry": True}), 503

    email_sent = send_email_via_gas(email, buyer_name, book_title, download_url)
    return jsonify({"success": True, "token": token, "download_url": download_url, "email_sent": email_sent})

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

    <div id="bookshelf" class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        <!-- Books will be injected here -->
    </div>

    <div id="empty-state" class="hidden text-center py-20">
        <div class="text-5xl mb-4">📚</div>
        <p class="text-gray-500">আপনার লাইব্রেরিতে এখনো কোনো বই নেই।</p>
    </div>

    <script>
        const DB_NAME = 'BoighorDB';
        const STORE_NAME = 'books';

        async function openDB() {
            return new Promise((resolve, reject) => {
                const request = indexedDB.open(DB_NAME, 1);
                request.onupgradeneeded = (e) => {
                    e.target.result.createObjectStore(STORE_NAME, { keyPath: 'token' });
                };
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
        }

        async function loadLibrary() {
            const db = await openDB();
            const tx = db.transaction(STORE_NAME, 'readonly');
            const store = tx.objectStore(STORE_NAME);
            const books = await new Promise(r => {
                const req = store.getAll();
                req.onsuccess = () => r(req.result);
            });

            const bookshelf = document.getElementById('bookshelf');
            const emptyState = document.getElementById('empty-state');

            if (books.length === 0) {
                emptyState.classList.remove('hidden');
                return;
            }

            books.forEach(book => {
                const div = document.createElement('div');
                div.className = 'book-card bg-zinc-900 p-3 rounded-xl border border-zinc-800 text-center';
                div.innerHTML = `
                    <div class="aspect-[3/4] bg-zinc-800 rounded-lg mb-3 flex items-center justify-center text-3xl shadow-inner">📖</div>
                    <div class="text-sm font-medium truncate">${book.title}</div>
                `;
                div.onclick = () => {
                    window.location.href = `/reader?token=${book.token}`;
                };
                bookshelf.appendChild(div);
            });
        }
        loadLibrary();
    </script>
</body>
</html>
""", store=STORE_NAME)

@app.route("/download/<token>")
def download_landing(token):
    """
    Entry point for email links. Redirects users to the confirmation/import page.
    """
    return redirect(f"/download/{token}/confirm")

@app.route("/download/<token>/confirm")
def download_confirm(token):
    t = fb_get(f"tokens/{token}")
    if t is False or not isinstance(t, dict): return "❌ লিংকটি বৈধ নয়।", 404
    
    try:
        exp = datetime.fromisoformat(t.get("expires_at", ""))
        if datetime.now(timezone.utc) > exp: return "⌛ মেয়াদ শেষ।", 410
    except: pass

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
        <p class="text-zinc-400 mb-8">আপনার লাইব্রেরিতে বইটি সেভ করা হচ্ছে। অনুগ্রহ করে অপেক্ষা করুন...</p>
        
        <div class="w-full bg-zinc-800 h-2 rounded-full overflow-hidden mb-4">
            <div id="progress" class="bg-amber-500 h-full w-0 transition-all duration-300"></div>
        </div>
        <div id="status" class="text-xs text-zinc-500 uppercase tracking-widest animate-pulse-custom">Connecting to secure stream...</div>
    </div>

    <script>
        async function importBook() {
            const token = '{{ token }}';
            const title = '{{ title }}';
            const progress = document.getElementById('progress');
            const status = document.getElementById('status');

            try {
                status.innerText = "Downloading secure binary...";
                const response = await fetch(`/stream-pdf/${token}`);
                if (!response.ok) throw new Error("Stream failed");

                const reader = response.body.getReader();
                const contentLength = +response.headers.get('Content-Length');
                let receivedLength = 0;
                let chunks = [];

                while(true) {
                    const {done, value} = await reader.read();
                    if (done) break;
                    chunks.push(value);
                    receivedLength += value.length;
                    const pct = contentLength ? Math.round((receivedLength / contentLength) * 100) : 0;
                    progress.style.width = pct + '%';
                    status.innerText = `Downloading: ${pct}%`;
                }

                status.innerText = "Saving to encrypted library...";
                const blob = new Blob(chunks, {type: 'application/pdf'});
                
                const db = await new Promise((resolve) => {
                    const req = indexedDB.open('BoighorDB', 1);
                    req.onupgradeneeded = (e) => e.target.result.createObjectStore('books', { keyPath: 'token' });
                    req.onsuccess = () => resolve(req.result);
                });

                const tx = db.transaction('books', 'readwrite');
                tx.objectStore('books').put({
                    token: token,
                    title: title,
                    blob: blob,
                    added_at: new Date().toISOString()
                });

                tx.oncomplete = () => {
                    status.innerText = "Success! Redirecting...";
                    setTimeout(() => { window.location.href = '/library'; }, 1000);
                };

            } catch (e) {
                status.innerText = "Error occurred. Please try again.";
                status.classList.add('text-red-500');
                console.error(e);
            }
        }
        importBook();
    </script>
</body>
</html>
""", store=STORE_NAME, title=book_title, token=token)

@app.route("/reader")
def reader():
    return render_template_string("""
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
    <title>বই রিডার — {{ store }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
    <style>
        body { background-color: #1a1a1a; color: #eee; user-select: none; -webkit-user-select: none; }
        @media print { body { display: none !important; } }
        .page-placeholder { 
            background: #262626; 
            margin: 10px auto; 
            box-shadow: 0 4px 15px rgba(0,0,0,0.5);
            display: flex; align-items: center; justify-content: center;
            color: #555; font-size: 12px;
        }
        canvas { display: block; max-width: 100%; height: auto !important; margin: 10px auto; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
    </style>
</head>
<body oncontextmenu="return false;">
    <nav class="fixed top-0 w-full bg-zinc-900/90 backdrop-blur-md border-b border-zinc-800 z-50 p-4 flex items-center justify-between">
        <a href="/library" class="text-amber-500 text-sm font-bold flex items-center">
            <span class="mr-2">←</span> লাইব্রেরি
        </a>
        <span id="page-info" class="text-xs text-zinc-400">Loading...</span>
    </nav>

    <div id="viewer-container" class="pt-20 pb-10 px-2"></div>

    <script>
        const token = new URLSearchParams(window.location.search).get('token');
        const pdfjsLib = window['pdfjs-dist/build/pdf'];
        pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';

        let pdfDoc = null;
        const container = document.getElementById('viewer-container');
        const pageInfo = document.getElementById('page-info');
        const activePages = new Map();

        document.addEventListener('keydown', e => {
            if (e.ctrlKey && (e.key === 'p' || e.key === 's' || e.key === 'u')) e.preventDefault();
            if (e.key === 'F12') e.preventDefault();
            if (e.ctrlKey && e.shiftKey && (e.key === 'I' || e.key === 'J')) e.preventDefault();
        });

        async function initReader() {
            const db = await new Promise(r => {
                const req = indexedDB.open('BoighorDB', 1);
                req.onsuccess = () => r(req.result);
            });

            const tx = db.transaction('books', 'readonly');
            const book = await new Promise(r => {
                const req = tx.objectStore('books').get(token);
                req.onsuccess = () => r(req.result);
            });

            if (!book) { alert('বইটি পাওয়া যায়নি!'); window.location.href = '/library'; return; }

            const arrayBuffer = await book.blob.arrayBuffer();
            pdfDoc = await pdfjsLib.getDocument({data: arrayBuffer}).promise;
            pageInfo.innerText = `মোট পৃষ্ঠা: ${pdfDoc.numPages}`;

            for (let i = 1; i <= pdfDoc.numPages; i++) {
                const placeholder = document.createElement('div');
                placeholder.className = 'page-placeholder';
                placeholder.dataset.pageNumber = i;
                placeholder.innerText = `Page ${i}`;
                placeholder.style.height = '80vh'; 
                container.appendChild(placeholder);
            }
            setupIntersectionObserver();
        }

        function setupIntersectionObserver() {
            const options = { root: null, rootMargin: '200px', threshold: 0.1 };
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    const pageNum = parseInt(entry.target.dataset.pageNumber);
                    if (entry.isIntersecting) {
                        renderPage(entry.target, pageNum);
                    } else {
                        unloadPage(entry.target, pageNum);
                    }
                });
            }, options);

            document.querySelectorAll('.page-placeholder').forEach(el => observer.observe(el));
            const mutationObserver = new MutationObserver(() => {
                document.querySelectorAll('canvas').forEach(canvas => {
                    const pageNum = canvas.dataset.pageNumber;
                    if (pageNum) observer.observe(canvas);
                });
            });
            mutationObserver.observe(container, { childList: true });
        }

        async function renderPage(element, pageNum) {
            if (activePages.has(pageNum)) return;
            const page = await pdfDoc.getPage(pageNum);
            const viewport = page.getViewport({ scale: 1.5 });
            const screenWidth = window.innerWidth - 20;
            const scale = screenWidth / viewport.width;
            const scaledViewport = page.getViewport({ scale: scale });

            const canvas = document.createElement('canvas');
            canvas.dataset.pageNumber = pageNum;
            canvas.width = scaledViewport.width;
            canvas.height = scaledViewport.height;
            const context = canvas.getContext('2d');
            await page.render({ canvasContext: context, viewport: scaledViewport }).promise;
            element.replaceWith(canvas);
            activePages.set(pageNum, canvas);
        }

        function unloadPage(element, pageNum) {
            if (element.tagName === 'CANVAS') {
                const placeholder = document.createElement('div');
                placeholder.className = 'page-placeholder';
                placeholder.dataset.pageNumber = pageNum;
                placeholder.style.height = element.offsetHeight + 'px';
                placeholder.innerText = `Page ${pageNum}`;
                element.replaceWith(placeholder);
                activePages.delete(pageNum);
                const observer = new IntersectionObserver((entries) => {
                    entries.forEach(e => { if (e.isIntersecting) renderPage(e.target, pageNum); });
                }, { rootMargin: '200px' });
                observer.observe(placeholder);
            }
        }
        initReader();
    </script>
</body>
</html>
""", store=STORE_NAME)

@app.route("/stream-pdf/<token>")
def stream_pdf(token):
    t = fb_get(f"tokens/{token}")
    if t is False or not isinstance(t, dict): return "Unauthorized", 403
    drive_link = t.get("drive_link", "")
    direct_url = get_google_drive_direct_link(drive_link)
    if not direct_url: return "File not found", 404
    try:
        r = requests.get(direct_url, stream=True, timeout=20)
        def generate():
            for chunk in r.iter_content(chunk_size=8192): yield chunk
        return Response(generate(), content_type="application/pdf")
    except: return "Internal Server Error", 500

@app.route("/resend-link", methods=["POST"])
def resend_link():
    data = request.get_json(force=True) or {}
    if data.get("admin_key") != ADMIN_KEY: return jsonify({"success": False, "error": "Unauthorized"}), 401
    email = (data.get("email") or "").strip()
    drive_link = (data.get("drive_link") or "").strip()
    book_title = (data.get("book_title") or "").strip()
    if not email or not drive_link: return jsonify({"success": False, "error": "Missing data"}), 400
    token = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    download_url = f"{BACKEND_URL}/download/{token}"
    saved = fb_set(f"tokens/{token}", {
        "email": email, "drive_link": drive_link, "book_title": book_title,
        "created_at": datetime.now(timezone.utc).isoformat(), "expires_at": expires_at, "used": False
    })
    if not saved: return jsonify({"success": False, "error": "Firebase unavailable"}), 503
    send_email_via_gas(email, "ক্রেতা", book_title, download_url)
    return jsonify({"success": True, "token": token, "download_url": download_url})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))