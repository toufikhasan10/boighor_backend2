from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import uuid, time, requests, os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
CORS(app)

# ── Environment Variables (Render-এ set করতে হবে) ──
ADMIN_KEY    = os.environ.get('ADMIN_KEY', 'boighor2024')
FIREBASE_URL = os.environ.get('FIREBASE_URL', '')   # https://xxx-rtdb.asia-southeast1.firebasedatabase.app
GMAIL_USER   = os.environ.get('GMAIL_USER', '')     # yourname@gmail.com
GMAIL_PASS   = os.environ.get('GMAIL_PASS', '')     # Gmail App Password (16 digit)
BACKEND_URL  = os.environ.get('BACKEND_URL', '')    # https://boighor-backend-xxx.onrender.com
STORE_NAME   = os.environ.get('STORE_NAME', 'বইঘর')

# ── Firebase REST helpers ──
def fb_set(path, data):
    if not FIREBASE_URL: return None
    try:
        r = requests.put(f"{FIREBASE_URL}/{path}.json", json=data, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Firebase set error: {e}"); return None

def fb_get(path):
    if not FIREBASE_URL: return None
    try:
        r = requests.get(f"{FIREBASE_URL}/{path}.json", timeout=10)
        return r.json()
    except Exception as e:
        print(f"Firebase get error: {e}"); return None

def fb_update(path, data):
    if not FIREBASE_URL: return None
    try:
        r = requests.patch(f"{FIREBASE_URL}/{path}.json", json=data, timeout=10)
        return r.json()
    except Exception as e:
        print(f"Firebase update error: {e}"); return None

# ── Email sender ──
def send_email(to_email, buyer_name, book_title, download_url):
    if not GMAIL_USER or not GMAIL_PASS:
        print("⚠️ Gmail সেট করা নেই — email পাঠানো হয়নি")
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'📚 আপনার ইবুক রেডি — {book_title}'
        msg['From']    = f'{STORE_NAME} <{GMAIL_USER}>'
        msg['To']      = to_email

        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;background:#fdf6ec;border-radius:12px">
          <h2 style="color:#b8741a;margin-bottom:4px">📚 {STORE_NAME}</h2>
          <p style="color:#4a3520">প্রিয় <strong>{buyer_name}</strong>,</p>
          <p style="color:#4a3520">আপনার ইবুক <strong>"{book_title}"</strong> প্রস্তুত!</p>
          <div style="margin:28px 0;text-align:center">
            <a href="{download_url}"
               style="background:#0f766e;color:#fff;padding:14px 32px;border-radius:10px;
                      text-decoration:none;font-weight:bold;font-size:16px;display:inline-block">
              📥 ইবুক ডাউনলোড করুন
            </a>
          </div>
          <p style="color:#dc2626;font-size:13px;font-weight:600">
            ⚠️ এই লিংকটি মাত্র একবার ব্যবহার করা যাবে।
          </p>
          <p style="color:#9a8060;font-size:12px">
            ৭ দিনের মধ্যে download করুন।<br>
            সমস্যা হলে reply করুন।
          </p>
          <hr style="border:none;border-top:1px solid #e8dcc8;margin:16px 0">
          <p style="color:#9a8060;font-size:11px">ধন্যবাদ {STORE_NAME} থেকে কেনার জন্য।</p>
        </div>
        """
        msg.attach(MIMEText(html, 'html', 'utf-8'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
        print(f"✅ Email পাঠানো হয়েছে → {to_email}")
        return True
    except Exception as e:
        print(f"❌ Email error: {e}")
        return False

# ════════════════════════════════════════════
# ROUTES
# ════════════════════════════════════════════

@app.route('/health')
def health():
    return jsonify({
        'status':   'ok',
        'message':  f'{STORE_NAME} Backend চালু আছে ✅',
        'firebase': '✅ Connected' if FIREBASE_URL else '❌ Not set',
        'email':    '✅ Ready'     if (GMAIL_USER and GMAIL_PASS) else '❌ Not set'
    })

# ── Admin: Token তৈরি করো ──
@app.route('/admin/issue-token', methods=['POST'])
def issue_token():
    data = request.json or {}

    if data.get('admin_key') != ADMIN_KEY:
        return jsonify({'error': 'Unauthorized — admin_key ভুল'}), 401

    email      = data.get('email', '').strip()
    buyer_name = data.get('buyer_name', 'ক্রেতা').strip()
    order_id   = data.get('order_id', '')
    book_id    = data.get('book_id', '')

    # Firebase থেকে book info নিয়ে আসো
    book_data  = fb_get(f'books/{book_id}') if book_id else None
    drive_link = ''
    book_title = 'বই'

    if book_data and isinstance(book_data, dict):
        drive_link = book_data.get('ebookLink', '').strip()
        book_title = book_data.get('title', 'বই')

    # Token তৈরি
    token      = str(uuid.uuid4()).replace('-', '')
    base_url   = BACKEND_URL or request.host_url.rstrip('/')
    dl_url     = f"{base_url}/download/{token}"

    # Firebase-এ সেভ করো
    fb_set(f'tokens/{token}', {
        'token':      token,
        'bookId':     book_id,
        'bookTitle':  book_title,
        'driveLink':  drive_link,
        'email':      email,
        'buyerName':  buyer_name,
        'orderId':    order_id,
        'createdAt':  int(time.time() * 1000),
        'used':       False,
        'expiresAt':  int(time.time()) + 7 * 24 * 3600  # ৭ দিন
    })

    # Email পাঠাও
    email_sent = send_email(email, buyer_name, book_title, dl_url) if email else False

    return jsonify({
        'success':      True,
        'token':        token,
        'download_url': dl_url,
        'email_sent':   email_sent
    })

# ── Download: Token চেক করে redirect ──
@app.route('/download/<token>')
def download_file(token):
    td = fb_get(f'tokens/{token}')

    if not td or not isinstance(td, dict):
        return _page('❌ অবৈধ লিংক', 'এই ডাউনলোড লিংকটি বৈধ নয়।', '#dc2626'), 404

    if td.get('used'):
        return _page('⚠️ লিংক ব্যবহৃত হয়েছে',
                     'এই লিংকটি আগেই একবার ব্যবহার করা হয়েছে।<br>নতুন লিংকের জন্য দোকানে যোগাযোগ করুন।',
                     '#f59e0b'), 410

    if td.get('expiresAt', 0) < int(time.time()):
        return _page('⏰ মেয়াদ শেষ',
                     'এই লিংকটির মেয়াদ (৭ দিন) শেষ হয়ে গেছে।',
                     '#9a8060'), 410

    drive_link = td.get('driveLink', '').strip()
    if not drive_link:
        return _page('❌ ফাইল পাওয়া যায়নি',
                     'ফাইল লিংক এখনও সেট করা হয়নি।', '#dc2626'), 404

    # Used mark করো redirect-এর আগে
    fb_update(f'tokens/{token}', {
        'used':   True,
        'usedAt': int(time.time() * 1000)
    })

    return redirect(drive_link)

# ── Admin: পুরনো link বাতিল করে নতুন পাঠাও ──
@app.route('/resend-link', methods=['POST'])
def resend_link():
    data     = request.json or {}
    email    = data.get('email', '').strip()
    order_id = data.get('order_id', '')

    # পুরনো token খোঁজো
    all_tokens = fb_get('tokens') or {}
    old_token  = None

    if isinstance(all_tokens, dict):
        for tk, tv in all_tokens.items():
            if isinstance(tv, dict) and tv.get('orderId') == order_id and not tv.get('used'):
                old_token = tk
                break

    # পুরনো token বাতিল
    old_data = {}
    if old_token and isinstance(all_tokens.get(old_token), dict):
        old_data = all_tokens[old_token]
        fb_update(f'tokens/{old_token}', {'used': True})

    book_title = old_data.get('bookTitle', 'বই')
    drive_link = old_data.get('driveLink', '')
    buyer_name = old_data.get('buyerName', 'ক্রেতা')

    # নতুন token তৈরি
    new_token = str(uuid.uuid4()).replace('-', '')
    base_url  = BACKEND_URL or request.host_url.rstrip('/')
    dl_url    = f"{base_url}/download/{new_token}"

    fb_set(f'tokens/{new_token}', {
        'token':      new_token,
        'bookTitle':  book_title,
        'driveLink':  drive_link,
        'email':      email,
        'buyerName':  buyer_name,
        'orderId':    order_id,
        'createdAt':  int(time.time() * 1000),
        'used':       False,
        'expiresAt':  int(time.time()) + 7 * 24 * 3600
    })

    email_sent = send_email(email, buyer_name, book_title, dl_url) if email else False

    return jsonify({
        'success':      True,
        'message':      f'নতুন লিংক {email}-এ পাঠানো হয়েছে' if email_sent else 'নতুন token তৈরি হয়েছে (email যায়নি)',
        'download_url': dl_url,
        'email_sent':   email_sent
    })

# ── Copyright verify page ──
@app.route('/verify-copyright')
def verify_copyright():
    return f"""
    <html><head><title>Copyright Verification</title></head>
    <body style="font-family:Arial;text-align:center;padding:60px;background:#fdf6ec">
      <h1 style="color:#b8741a">📚 {STORE_NAME}</h1>
      <h2 style="color:#16a34a">✅ Copyright Protected</h2>
      <p style="color:#4a3520">এই স্টোর থেকে বিক্রিত সকল বই কপিরাইট সুরক্ষিত।</p>
      <p style="color:#9a8060;font-size:13px">Unauthorized distribution is strictly prohibited.</p>
    </body></html>
    """

# ── Helper: সুন্দর error page ──
def _page(title, msg, color='#dc2626'):
    return f"""
    <html><head><title>{title}</title></head>
    <body style="font-family:Arial;text-align:center;padding:60px;background:#fdf6ec">
      <h2 style="color:{color}">{title}</h2>
      <p style="color:#4a3520">{msg}</p>
    </body></html>
    """

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
