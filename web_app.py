import os
import io
import functools
import requests
import smtplib
import jwt
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import chess
import chess.engine
import numpy as np
import cv2
from PIL import Image
from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
from google import genai
from google.genai import types

import veritabani as db

BOT_TOKEN = "8931626734:AAG_d0YmV8dtVpFqMAntFmodchqv25ZNxyk"
ADMIN_CHAT_ID = "1061051813"

# Gmail ayarları — buraya kendi bilgilerini gir
GMAIL_ADRES = "kemaluslu810@gmail.com"
GMAIL_UYGULAMA_SIFRESI = "qouc nsxq xisq cdoq"


def mail_gonder(alici_email, konu, icerik_html):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = konu
        msg["From"] = GMAIL_ADRES
        msg["To"] = alici_email
        msg.attach(MIMEText(icerik_html, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADRES, GMAIL_UYGULAMA_SIFRESI)
            server.sendmail(GMAIL_ADRES, alici_email, msg.as_string())
        print(f"Mail gönderildi: {alici_email}")
    except Exception as e:
        print(f"Mail hatası: {e}")


def telegram_gonder(chat_id, mesaj):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": mesaj, "parse_mode": "Markdown"},
            timeout=5
        )
    except Exception as e:
        print(f"Telegram bildirim hatası: {e}")

app = Flask(__name__)
CORS(app, origins="*")
app.secret_key = "satranc-kocu-gizli-anahtar-2026"
JWT_SECRET = "satranc-jwt-secret-2026"
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024


def token_uret(kullanici_id):
    return jwt.encode(
        {"kullanici_id": kullanici_id, "exp": datetime.utcnow() + timedelta(days=30)},
        JWT_SECRET, algorithm="HS256"
    )


def token_coz(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])["kullanici_id"]
    except Exception:
        return None


def giris_gerekli(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # Mobil: JWT token
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            uid = token_coz(auth[7:])
            if uid:
                session['kullanici_id'] = uid
                return f(*args, **kwargs)
        # Web: session
        if 'kullanici_id' in session:
            return f(*args, **kwargs)
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"hata": "Giriş gerekli"}), 401
        return redirect(url_for('giris'))
    return decorated

GEMINI_API_KEY = "AIzaSyBxoN2uWYEbbzbFQENoHurAn_0Ht8pOjuo"
ai_client = genai.Client(api_key=GEMINI_API_KEY)

su_anki_klasor = os.path.dirname(os.path.abspath(__file__))

# Windows: .exe dosyasını bul | Linux (Railway): sistem stockfish
import shutil, platform
if platform.system() == "Windows":
    stockfish_dosyalari = [f for f in os.listdir(su_anki_klasor) if f.endswith('.exe')]
    STOCKFISH_PATH = os.path.join(su_anki_klasor, stockfish_dosyalari[0])
else:
    STOCKFISH_PATH = shutil.which("stockfish") or "/usr/bin/stockfish"


def sadece_tahtayi_kirp(pil_image):
    try:
        open_cv_image = np.array(pil_image)
        open_cv_image = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2BGR)
        h, w = open_cv_image.shape[:2]

        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 30, 120)
        kernel = np.ones((7, 7), np.uint8)
        dilated = cv2.dilate(edges, kernel, iterations=3)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best, best_score = None, 0
        for cnt in contours:
            alan = cv2.contourArea(cnt)
            if alan < w * h * 0.15:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            karelik = min(bw, bh) / max(bw, bh) if max(bw, bh) > 0 else 0
            if karelik < 0.70:
                continue
            skor = alan * (karelik ** 2)
            if skor > best_score:
                best_score, best = skor, (x, y, bw, bh)

        if best:
            x, y, bw, bh = best
            pad = 8
            x1, y1 = max(0, x - pad), max(0, y - pad)
            x2, y2 = min(w, x + bw + pad), min(h, y + bh + pad)
            kirpilmis = open_cv_image[y1:y2, x1:x2]
            return Image.fromarray(cv2.cvtColor(kirpilmis, cv2.COLOR_BGR2RGB))

        if h > w * 1.1:
            fark = (h - w) // 2
            kirpilmis = open_cv_image[fark:fark + w, 0:w]
            return Image.fromarray(cv2.cvtColor(kirpilmis, cv2.COLOR_BGR2RGB))

        return pil_image
    except Exception as e:
        print(f"Kırpma hatası: {e}")
        return pil_image


def resimden_fen_uret(pil_image, hamle_sirasi):
    sira_harfi = "w" if hamle_sirasi == "beyaz" else "b"
    prompt = f"""Bu bir satranç tahtası fotoğrafıdır. Tahtada uygulama arayüzü, oklar veya butonlar olabilir, onları yoksay.

Tahta beyazın veya siyahın perspektifinden gösterilebilir. Koordinat harflerine (a-h) ve sayılara (1-8) bakarak yönü anla.

Tahtadaki tüm taşların tam yerini tespit et ve standart FEN notasyonuyla tek satır olarak döndür.

ZORUNLU KURALLAR:
1. Yalnızca FEN kodunu yaz, başka hiçbir şey yazma.
2. Markdown, tırnak, kod bloğu kullanma.
3. FEN'in sonundaki hamle sırası '{sira_harfi}' olsun.
4. FEN tam olarak 6 bölümden oluşsun.

Örnek: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR {sira_harfi} KQkq - 0 1"""

    # PIL image'ı JPEG bytes'a çevir — Gemini bu formatı kesin kabul eder
    buf = io.BytesIO()
    pil_image.save(buf, format='JPEG', quality=95)
    img_part = types.Part.from_bytes(data=buf.getvalue(), mime_type='image/jpeg')

    modeller = ['models/gemini-flash-lite-latest', 'models/gemini-2.5-flash', 'models/gemini-2.0-flash']
    for model in modeller:
        for deneme in range(2):
            try:
                response = ai_client.models.generate_content(
                    model=model,
                    contents=[img_part, prompt]
                )
                fen = response.text.strip().split('\n')[0]
                fen = fen.replace("`", "").replace("text", "").replace("fen", "").strip()
                print(f"[{model}] Ham FEN: {fen}")
                chess.Board(fen)
                print(f"[{model}] Geçerli FEN: {fen}")
                return fen
            except ValueError as e:
                print(f"[{model}] Deneme {deneme+1}: Geçersiz FEN — {fen} | Hata: {e}")
            except Exception as e:
                hata_str = str(e)
                print(f"[{model}] Deneme {deneme+1}: API hatası — {hata_str[:200]}")
                if '429' in hata_str or 'RESOURCE_EXHAUSTED' in hata_str:
                    raise RuntimeError("KOTA_BITTI")
                break
    return None


def yapay_zeka_yorumu_al(fen, hamle, skor):
    prompt = f"""Satranç koçusun. FEN: {fen} | En iyi hamle: {hamle} | Skor: {skor}.
Espritüel bir Türk koçu gibi, bu hamlenin neden en iyi olduğunu maksimum 2 kısa cümleyle açıkla."""
    try:
        response = ai_client.models.generate_content(
            model='models/gemini-flash-lite-latest',
            contents=[{"role": "user", "parts": [{"text": prompt}]}]
        )
        return response.text
    except Exception:
        return None


# ─── MOBİL API ────────────────────────────────────────────────
@app.route('/api/kayit', methods=['POST'])
def api_kayit():
    data = request.get_json()
    email = data.get('email', '').strip()
    sifre = data.get('sifre', '')
    chat_id = data.get('chat_id', None)
    if not email or len(sifre) < 6:
        return jsonify({"hata": "Geçersiz bilgiler"}), 400
    uid = db.kayit_ol(email, sifre, bildirim_chat_id=chat_id)
    if uid is None:
        return jsonify({"hata": "Bu email zaten kayıtlı"}), 409
    return jsonify({"token": token_uret(uid), "kullanici_id": uid}), 201


@app.route('/api/giris', methods=['POST'])
def api_giris():
    data = request.get_json()
    sonuc = db.giris_yap(data.get('email', ''), data.get('sifre', ''))
    if sonuc is None:
        return jsonify({"hata": "Email veya şifre hatalı"}), 401
    uid, kalan_hak, is_premium = sonuc
    return jsonify({"token": token_uret(uid), "kullanici_id": uid,
                    "kalan_hak": kalan_hak, "is_premium": is_premium})


@app.route('/api/google-giris', methods=['POST'])
def api_google_giris():
    """Google OAuth access token ile giriş / otomatik kayıt."""
    data = request.get_json()
    access_token = data.get('access_token', '')
    if not access_token:
        return jsonify({"hata": "Access token eksik"}), 400

    # Google'dan kullanıcı bilgilerini al
    try:
        google_res = requests.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )
        if google_res.status_code != 200:
            return jsonify({"hata": "Google doğrulama başarısız"}), 401
        user_info = google_res.json()
    except Exception:
        return jsonify({"hata": "Google sunucusuna ulaşılamadı"}), 503

    email = user_info.get('email', '').lower().strip()
    google_id = user_info.get('sub', '')

    if not email or not google_id:
        return jsonify({"hata": "Google hesap bilgileri alınamadı"}), 400

    uid = db.sosyal_giris_veya_kayit(email=email, google_id=google_id)
    if uid is None:
        return jsonify({"hata": "Kullanıcı oluşturulamadı"}), 500

    return jsonify({"token": token_uret(uid), "kullanici_id": uid, "mesaj": "Google ile giriş başarılı"})


@app.route('/api/apple-giris', methods=['POST'])
def api_apple_giris():
    """Apple identity token ile giriş / otomatik kayıt."""
    import base64, json as json_mod
    data = request.get_json()
    identity_token = data.get('identity_token', '')
    email_gelen = data.get('email', '')
    full_name = data.get('full_name', '')

    if not identity_token:
        return jsonify({"hata": "Identity token eksik"}), 400

    # JWT'nin payload kısmını decode et (imza doğrulaması yapmadan temel bilgileri al)
    try:
        parts = identity_token.split('.')
        if len(parts) != 3:
            raise ValueError("Geçersiz JWT")
        payload_b64 = parts[1] + '=='  # padding ekle
        payload = json_mod.loads(base64.urlsafe_b64decode(payload_b64))
        apple_id = payload.get('sub', '')
        email = payload.get('email', email_gelen or '').lower().strip()
    except Exception:
        return jsonify({"hata": "Apple token çözümlenemedi"}), 400

    if not apple_id:
        return jsonify({"hata": "Apple kullanıcı ID alınamadı"}), 400

    # Email yoksa apple_id'den üret
    if not email:
        email = f"apple_{apple_id}@privaterelay.appleid.com"

    uid = db.sosyal_giris_veya_kayit(email=email, apple_id=apple_id)
    if uid is None:
        return jsonify({"hata": "Kullanıcı oluşturulamadı"}), 500

    return jsonify({"token": token_uret(uid), "kullanici_id": uid, "mesaj": "Apple ile giriş başarılı"})


@app.route('/api/profil')
@giris_gerekli
def api_profil():
    uid = session['kullanici_id']
    _, kalan_hak, is_premium = db.kullanici_getir_id(uid)
    istat = db.istatistik(uid)
    return jsonify({"kalan_hak": kalan_hak, "is_premium": is_premium,
                    "toplam_analiz": istat['toplam'], "bugun": istat['bugun']})


@app.route('/api/gecmis')
@giris_gerekli
def api_gecmis():
    uid = session['kullanici_id']
    analizler = db.analiz_gecmisi(uid, limit=20)
    return jsonify({"analizler": [
        [a[0], a[1], a[2], a[3], a[4]] for a in analizler
    ]})


@app.route('/api/odeme-bildir', methods=['POST'])
@giris_gerekli
def api_odeme_bildir():
    uid = session['kullanici_id']
    not_bilgisi = request.json.get('not', 'Mobil uygulama üzerinden ödeme bildirimi') if request.is_json else 'Mobil ödeme'
    db.odeme_talebi_olustur(uid, not_bilgisi)
    # Admin'e Telegram bildirimi
    import sqlite3
    conn = sqlite3.connect(db.DB_DOSYASI)
    row = conn.execute("SELECT email FROM kullanicilar WHERE id=?", (uid,)).fetchone()
    conn.close()
    email = row[0] if row else "bilinmiyor"
    telegram_gonder(ADMIN_CHAT_ID,
        f"💰 *Yeni Ödeme Talebi!*\n\n"
        f"📧 Email: `{email}`\n"
        f"📱 Kanal: Mobil Uygulama\n\n"
        f"Admin panelinden onayla: http://localhost:5000/admin"
    )
    return jsonify({"mesaj": "Ödeme bildirimi alındı. Admin onayından sonra aktif edilecek."})
@app.route('/api/premium-ver', methods=['POST'])
@giris_gerekli
def api_premium_ver():
    """Google Play / RevenueCat satın alma sonrası premium aktif et."""
    uid = session['kullanici_id']
    bitis = db.premium_ver(uid, ay=1)
    return jsonify({"mesaj": "Premium aktif edildi", "bitis": str(bitis)})

# ──────────────────────────────────────────────────────────────


@app.route('/kayit', methods=['GET', 'POST'])
def kayit():
    if 'kullanici_id' in session:
        return redirect(url_for('index'))
    hata = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        sifre = request.form.get('sifre', '')
        sifre2 = request.form.get('sifre2', '')
        if not email or not sifre:
            hata = "Email ve şifre zorunlu."
        elif len(sifre) < 6:
            hata = "Şifre en az 6 karakter olmalı."
        elif sifre != sifre2:
            hata = "Şifreler eşleşmiyor."
        else:
            chat_id = request.form.get('chat_id', '').strip() or None
            kullanici_id = db.kayit_ol(email, sifre, bildirim_chat_id=chat_id)
            if kullanici_id is None:
                hata = "Bu email zaten kayıtlı."
            else:
                session['kullanici_id'] = kullanici_id
                return redirect(url_for('index'))
    return render_template('kayit.html', hata=hata)


@app.route('/giris', methods=['GET', 'POST'])
def giris():
    if 'kullanici_id' in session:
        return redirect(url_for('index'))
    hata = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        sifre = request.form.get('sifre', '')
        sonuc = db.giris_yap(email, sifre)
        if sonuc is None:
            hata = "Email veya şifre hatalı."
        else:
            kullanici_id, _, _ = sonuc
            session['kullanici_id'] = kullanici_id
            return redirect(url_for('index'))
    return render_template('giris.html', hata=hata)


@app.route('/cikis')
def cikis():
    session.clear()
    return redirect(url_for('giris'))


@app.route('/')
@giris_gerekli
def index():
    kullanici_id, kalan_hak, is_premium = db.kullanici_getir_id(session['kullanici_id'])
    istat = db.istatistik(kullanici_id)
    return render_template('index.html',
                           kalan_hak=kalan_hak,
                           is_premium=is_premium,
                           toplam_analiz=istat['toplam'])


@app.route('/analiz', methods=['POST'])
@giris_gerekli
def analiz():
    kullanici_id, kalan_hak, is_premium = db.kullanici_getir_id(session['kullanici_id'])

    if kalan_hak <= 0 and not is_premium:
        return jsonify({'hata': '❌ Günlük analiz hakkın doldu. Yarın yenilenir veya premium al.'}), 403

    if 'fotograf' not in request.files:
        return jsonify({'hata': 'Fotoğraf gönderilmedi'}), 400

    dosya = request.files['fotograf']
    hamle_sirasi = request.form.get('hamle_sirasi', 'beyaz')

    try:
        resim = Image.open(dosya.stream).convert('RGB')
        temiz_resim = sadece_tahtayi_kirp(resim)

        try:
            fen = resimden_fen_uret(temiz_resim, hamle_sirasi)
        except RuntimeError:
            return jsonify({'hata': '⏳ Günlük AI kotası doldu. Yarın tekrar deneyin.'}), 429

        if not fen:
            return jsonify({'hata': 'Tahta okunamadı. Daha net bir fotoğraf deneyin.'}), 422

        board = chess.Board(fen)

        if board.is_game_over():
            kazanan = "Siyah" if board.turn == chess.WHITE else "Beyaz"
            sonuc = f'Şah Mat! {kazanan} kazandı.' if board.is_checkmate() else 'Oyun beraberlikle bitti.'
            return jsonify({'sonuc': sonuc, 'fen': fen})

        sure = 1.5 if is_premium else 0.5
        multipv = 3 if is_premium else 1

        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            analiz_sonucu = engine.analyse(board, chess.engine.Limit(time=sure), multipv=multipv)

        # analyse() her zaman liste döndürür (multipv>=1)
        if not isinstance(analiz_sonucu, list):
            analiz_sonucu = [analiz_sonucu]

        hamleler = [a["pv"][0].uci() for a in analiz_sonucu if a.get("pv")]
        en_iyi_hamle = hamleler[0] if hamleler else "Bulunamadı"
        alternatifler = hamleler[1:]
        skor_raw = analiz_sonucu[0]["score"].white().score(mate_score=10000)

        skor_durumu = f"{skor_raw / 100:+.2f}" if skor_raw is not None else "Mat Yakın!"
        sira = "Beyaz" if board.turn == chess.WHITE else "Siyah"
        yorum = yapay_zeka_yorumu_al(fen, en_iyi_hamle, skor_durumu)

        db.hak_dusur(kullanici_id)
        db.analiz_kaydet(kullanici_id, "web", fen, en_iyi_hamle, skor_durumu)
        _, yeni_hak, _ = db.kullanici_getir_id(session['kullanici_id'])

        return jsonify({
            'fen': fen,
            'sira': sira,
            'en_iyi_hamle': en_iyi_hamle,
            'alternatifler': alternatifler,
            'skor': skor_durumu,
            'yorum': yorum,
            'kalan_hak': yeni_hak,
            'is_premium': is_premium
        })

    except Exception as e:
        print(f"Analiz hatası: {e}")
        return jsonify({'hata': 'Sunucu hatası oluştu.'}), 500


IBAN = "TR00 0000 0000 0000 0000 0000 00"   # Kendi IBAN'ını buraya yaz
ADMIN_SIFRE = "admin123"                    # Admin şifreni değiştir


@app.route('/premium', methods=['GET', 'POST'])
@giris_gerekli
def premium():
    kullanici_id = session['kullanici_id']
    mesaj = None
    if request.method == 'POST':
        not_bilgisi = request.form.get('not', '').strip()
        db.odeme_talebi_olustur(kullanici_id, not_bilgisi)
        mesaj = "✅ Talebiniz alındı! En geç 24 saat içinde premium aktif edilir."
        # Admin'e bildirim gönder
        conn = db.baglanti()
        email = conn.execute("SELECT email FROM kullanicilar WHERE id = ?", (kullanici_id,)).fetchone()[0]
        conn.close()
        telegram_gonder(ADMIN_CHAT_ID,
            f"💰 *Yeni Ödeme Talebi!*\n\n"
            f"👤 Email: `{email}`\n"
            f"📝 Not: {not_bilgisi or '—'}\n\n"
            f"Onaylamak için: localhost:5000/admin"
        )
    return render_template('premium.html', iban=IBAN, mesaj=mesaj)


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if session.get('admin') != True:
        if request.method == 'POST' and request.form.get('sifre') == ADMIN_SIFRE:
            session['admin'] = True
        else:
            return render_template('admin_giris.html', hata=request.method == 'POST')
    odemeler = db.bekleyen_odemeler()
    return render_template('admin.html', odemeler=odemeler)


@app.route('/admin/onayla/<int:odeme_id>', methods=['POST'])
def admin_onayla(odeme_id):
    if not session.get('admin'):
        return redirect(url_for('admin'))

    # Onaylamadan önce kullanıcı bilgisini al
    conn = db.baglanti()
    row = conn.execute(
        "SELECT o.kullanici_id, k.email, k.bildirim_chat_id FROM odemeler o JOIN kullanicilar k ON k.id = o.kullanici_id WHERE o.id = ?",
        (odeme_id,)
    ).fetchone()
    conn.close()

    db.odeme_onayla(odeme_id)

    if row:
        kullanici_id, email, bildirim_chat_id = row
        # Kullanıcıya Telegram bildirimi (bağladıysa)
        if bildirim_chat_id:
            telegram_gonder(bildirim_chat_id,
                "🎉 *Premium Aktif Oldu!*\n\n"
                "Artık sınırsız analiz yapabilirsin. ♟️"
            )
        # Kullanıcıya email gönder
        mail_gonder(email, "♟️ Premium Üyeliğin Aktif Oldu!", f"""
        <div style="font-family:sans-serif;max-width:480px;margin:auto;background:#1a1a2e;color:#eee;border-radius:12px;padding:32px;">
            <h2 style="background:linear-gradient(135deg,#667eea,#764ba2);-webkit-background-clip:text;-webkit-text-fill-color:transparent;">
                ♟️ Satranç Koçu
            </h2>
            <h3 style="margin-top:16px;">🎉 Premium Üyeliğin Aktif!</h3>
            <p style="color:#aaa;margin-top:8px;line-height:1.6;">
                Ödemen onaylandı. Artık <strong style="color:#eee;">sınırsız analiz</strong> yapabilirsin.
            </p>
            <ul style="color:#aaa;margin-top:16px;line-height:2;">
                <li>✅ Sınırsız günlük analiz</li>
                <li>✅ Top 3 alternatif hamle</li>
                <li>✅ Derin Stockfish analizi</li>
            </ul>
            <a href="http://localhost:5000" style="display:inline-block;margin-top:24px;padding:12px 24px;background:linear-gradient(135deg,#667eea,#764ba2);color:white;border-radius:8px;text-decoration:none;font-weight:600;">
                Hemen Kullan →
            </a>
        </div>
        """)
        # Admin'e onay bildirimi
        telegram_gonder(ADMIN_CHAT_ID, f"✅ `{email}` için premium aktif edildi ve mail gönderildi.")

    return redirect(url_for('admin'))


@app.route('/sifre-unut', methods=['GET', 'POST'])
def sifre_unut():
    mesaj = None
    hata = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        token = db.sifre_sifirlama_token_olustur(email)
        if token:
            link = f"http://localhost:5000/sifre-yenile/{token}"
            mail_gonder(email, "♟️ Şifre Sıfırlama", f"""
            <div style="font-family:sans-serif;max-width:480px;margin:auto;background:#1a1a2e;color:#eee;border-radius:12px;padding:32px;">
                <h2 style="color:#667eea;">♟️ Şifre Sıfırlama</h2>
                <p style="color:#aaa;margin-top:12px;">Aşağıdaki butona tıklayarak şifreni sıfırlayabilirsin. Link 1 saat geçerli.</p>
                <a href="{link}" style="display:inline-block;margin-top:20px;padding:12px 24px;background:linear-gradient(135deg,#667eea,#764ba2);color:white;border-radius:8px;text-decoration:none;font-weight:600;">
                    Şifremi Sıfırla →
                </a>
                <p style="color:#666;margin-top:16px;font-size:0.8rem;">Bu maili sen istemediysen dikkate alma.</p>
            </div>
            """)
        mesaj = "Eğer bu email kayıtlıysa sıfırlama linki gönderildi."
    return render_template('sifre_unut.html', mesaj=mesaj, hata=hata)


@app.route('/sifre-yenile/<token>', methods=['GET', 'POST'])
def sifre_yenile(token):
    kullanici_id = db.sifre_sifirlama_token_dogrula(token)
    if not kullanici_id:
        return render_template('sifre_unut.html', hata="Link geçersiz veya süresi dolmuş.")
    hata = None
    if request.method == 'POST':
        yeni = request.form.get('sifre', '')
        tekrar = request.form.get('sifre2', '')
        if len(yeni) < 6:
            hata = "Şifre en az 6 karakter olmalı."
        elif yeni != tekrar:
            hata = "Şifreler eşleşmiyor."
        else:
            db.sifre_guncelle(kullanici_id, yeni)
            return redirect(url_for('giris'))
    return render_template('sifre_yenile.html', token=token, hata=hata)


@app.route('/gecmis')
@giris_gerekli
def gecmis():
    kullanici_id = session['kullanici_id']
    analizler = db.analiz_gecmisi(kullanici_id, limit=20)
    return render_template('gecmis.html', analizler=analizler)


@app.route('/durum')
@giris_gerekli
def durum():
    kullanici_id, kalan_hak, is_premium = db.kullanici_getir_id(session['kullanici_id'])
    istat = db.istatistik(kullanici_id)
    return jsonify({'kalan_hak': kalan_hak, 'is_premium': is_premium, 'toplam_analiz': istat['toplam']})


@app.route('/privacy')
@app.route('/gizlilik')
def gizlilik_politikasi():
    return '''<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gizlilik Politikası - Satranç Koçu</title>
<style>
  body { font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #333; line-height: 1.6; }
  h1 { color: #1a1a2e; } h2 { color: #16213e; margin-top: 30px; }
  p { margin: 10px 0; }
</style>
</head>
<body>
<h1>Gizlilik Politikası</h1>
<p><strong>Son güncelleme:</strong> Mayıs 2026</p>
<p>Satranç Koçu uygulaması ("uygulama") olarak kullanıcılarımızın gizliliğine önem veriyoruz. Bu politika, hangi verileri topladığımızı ve nasıl kullandığımızı açıklar.</p>

<h2>Toplanan Veriler</h2>
<p>Uygulamamız aşağıdaki verileri toplar:</p>
<ul>
  <li><strong>E-posta adresi:</strong> Hesap oluşturma ve giriş için kullanılır.</li>
  <li><strong>Satranç tahtası fotoğrafları:</strong> Analiz amacıyla yüklenen görseller sunucumuza gönderilir ve analiz sonrası silinir.</li>
  <li><strong>Analiz geçmişi:</strong> Kullanıcının talep etmesi halinde analiz sonuçları kaydedilir.</li>
</ul>

<h2>Verilerin Kullanımı</h2>
<p>Toplanan veriler yalnızca uygulamanın temel işlevlerini sağlamak için kullanılır. Verileriniz üçüncü taraflarla paylaşılmaz veya satılmaz.</p>

<h2>Veri Güvenliği</h2>
<p>Kullanıcı şifreleri şifrelenmiş olarak saklanır. Verileriniz güvenli sunucularda tutulmaktadır.</p>

<h2>Çocukların Gizliliği</h2>
<p>Uygulamamız 13 yaşın altındaki çocuklara yönelik değildir ve bu yaş grubundan bilerek veri toplamayız.</p>

<h2>İletişim</h2>
<p>Gizlilik politikamızla ilgili sorularınız için: <a href="mailto:safakacer55@gmail.com">safakacer55@gmail.com</a></p>
</body>
</html>'''


if __name__ == '__main__':
    db.veritabanini_hazirla()
    app.run(debug=False, host='0.0.0.0', port=5000)
