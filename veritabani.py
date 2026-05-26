import sqlite3
import os
import secrets
from datetime import date, datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "satranç_botu.db")

UCRETSIZ_GUNLUK_HAK = 5
PREMIUM_AYLIK_FIYAT_TL = 50


def baglanti():
    return sqlite3.connect(DB_PATH)


def veritabanini_hazirla():
    conn = baglanti()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS kullanicilar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            sifre_hash TEXT,
            telegram_id TEXT UNIQUE,
            web_token TEXT UNIQUE,
            kalan_hak INTEGER DEFAULT 5,
            son_yenileme TEXT DEFAULT '',
            is_premium INTEGER DEFAULT 0,
            premium_bitis TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            bildirim_chat_id TEXT DEFAULT NULL
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS analizler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_id INTEGER,
            platform TEXT,
            fen TEXT,
            en_iyi_hamle TEXT,
            skor TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (kullanici_id) REFERENCES kullanicilar(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS sifre_sifirlama (
            token TEXT PRIMARY KEY,
            kullanici_id INTEGER NOT NULL,
            gecerlilik TEXT NOT NULL
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS odemeler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_id INTEGER,
            platform TEXT,
            miktar REAL,
            not_bilgisi TEXT,
            durum TEXT DEFAULT 'bekliyor',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (kullanici_id) REFERENCES kullanicilar(id)
        )
    ''')

    # Eski tablodaki sütunları yeni şemaya taşı
    for sutun, tanim in [
        ("web_token", "TEXT UNIQUE"),
        ("premium_bitis", "TEXT DEFAULT ''"),
        ("created_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
        ("google_id", "TEXT UNIQUE"),
        ("apple_id", "TEXT UNIQUE"),
        ("reklam_hak_gun", "TEXT DEFAULT ''"),
        ("reklam_hak_sayisi", "INTEGER DEFAULT 0"),
    ]:
        try:
            c.execute(f"ALTER TABLE kullanicilar ADD COLUMN {sutun} {tanim}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


def _premium_aktif_mi(row_is_premium, row_premium_bitis):
    if not row_is_premium:
        return False
    if not row_premium_bitis:
        return True  # Süresiz premium
    try:
        bitis = datetime.strptime(row_premium_bitis, "%Y-%m-%d").date()
        return date.today() <= bitis
    except ValueError:
        return True


def sosyal_giris_veya_kayit(email, google_id=None, apple_id=None):
    """Google veya Apple ile giriş/kayıt. kullanici_id döner."""
    conn = baglanti()
    c = conn.cursor()
    bugun = str(date.today())

    # Önce google_id veya apple_id ile ara
    if google_id:
        c.execute("SELECT id FROM kullanicilar WHERE google_id = ?", (google_id,))
        row = c.fetchone()
        if row:
            conn.close()
            return row[0]

    if apple_id:
        c.execute("SELECT id FROM kullanicilar WHERE apple_id = ?", (apple_id,))
        row = c.fetchone()
        if row:
            conn.close()
            return row[0]

    # Email ile ara (daha önce normal kayıt olmuş olabilir)
    if email:
        c.execute("SELECT id FROM kullanicilar WHERE email = ?", (email.lower().strip(),))
        row = c.fetchone()
        if row:
            uid = row[0]
            # google_id veya apple_id'yi güncelle
            if google_id:
                try:
                    c.execute("UPDATE kullanicilar SET google_id = ? WHERE id = ?", (google_id, uid))
                    conn.commit()
                except Exception:
                    pass
            if apple_id:
                try:
                    c.execute("UPDATE kullanicilar SET apple_id = ? WHERE id = ?", (apple_id, uid))
                    conn.commit()
                except Exception:
                    pass
            conn.close()
            return uid

    # Yeni kullanıcı oluştur
    try:
        c.execute(
            "INSERT INTO kullanicilar (email, google_id, apple_id, kalan_hak, son_yenileme) VALUES (?, ?, ?, ?, ?)",
            (email.lower().strip() if email else None, google_id, apple_id, UCRETSIZ_GUNLUK_HAK, bugun)
        )
        conn.commit()
        uid = c.lastrowid
        conn.close()
        return uid
    except sqlite3.IntegrityError:
        conn.close()
        return None


def kayit_ol(email, sifre, bildirim_chat_id=None):
    """Yeni kullanıcı oluştur. Başarılıysa kullanici_id döner, hata varsa None."""
    conn = baglanti()
    c = conn.cursor()
    try:
        sifre_hash = generate_password_hash(sifre)
        bugun = str(date.today())
        c.execute(
            "INSERT INTO kullanicilar (email, sifre_hash, kalan_hak, son_yenileme, bildirim_chat_id) VALUES (?, ?, ?, ?, ?)",
            (email.lower().strip(), sifre_hash, UCRETSIZ_GUNLUK_HAK, bugun, bildirim_chat_id)
        )
        conn.commit()
        return c.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def kullanici_bildirim_id(kullanici_id):
    """Kullanıcının Telegram chat ID'sini döner."""
    conn = baglanti()
    c = conn.cursor()
    c.execute("SELECT bildirim_chat_id FROM kullanicilar WHERE id = ?", (kullanici_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def giris_yap(email, sifre):
    """Email ve şifre doğruysa (kullanici_id, kalan_hak, is_premium) döner, yanlışsa None."""
    conn = baglanti()
    c = conn.cursor()
    c.execute("SELECT id, sifre_hash, kalan_hak, is_premium, premium_bitis, son_yenileme FROM kullanicilar WHERE email = ?",
              (email.lower().strip(),))
    row = c.fetchone()
    conn.close()

    if row is None or not check_password_hash(row[1], sifre):
        return None

    kullanici_id, _, kalan_hak, is_premium, premium_bitis, son_yenileme = row
    aktif = _premium_aktif_mi(is_premium, premium_bitis)

    # Günlük hak yenile
    bugun = str(date.today())
    if not aktif and son_yenileme != bugun:
        conn = baglanti()
        conn.execute("UPDATE kullanicilar SET kalan_hak = ?, son_yenileme = ? WHERE id = ?",
                     (UCRETSIZ_GUNLUK_HAK, bugun, kullanici_id))
        conn.commit()
        conn.close()
        kalan_hak = UCRETSIZ_GUNLUK_HAK

    return kullanici_id, (999 if aktif else kalan_hak), aktif


def kullanici_getir_id(kullanici_id):
    """ID ile kullanıcı bilgilerini getir."""
    conn = baglanti()
    c = conn.cursor()
    bugun = str(date.today())
    c.execute("SELECT kalan_hak, is_premium, premium_bitis, son_yenileme FROM kullanicilar WHERE id = ?", (kullanici_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None, 0, False
    kalan_hak, is_premium, premium_bitis, son_yenileme = row
    aktif = _premium_aktif_mi(is_premium, premium_bitis)
    if not aktif and son_yenileme != bugun:
        conn = baglanti()
        conn.execute("UPDATE kullanicilar SET kalan_hak = ?, son_yenileme = ? WHERE id = ?",
                     (UCRETSIZ_GUNLUK_HAK, bugun, kullanici_id))
        conn.commit()
        conn.close()
        kalan_hak = UCRETSIZ_GUNLUK_HAK
    return kullanici_id, (999 if aktif else kalan_hak), aktif


def kullanici_getir_telegram(telegram_id):
    """Telegram ID ile kullanıcı getir, yoksa oluştur. (hak, is_premium, id) döner."""
    conn = baglanti()
    c = conn.cursor()
    bugun = str(date.today())
    telegram_id = str(telegram_id)

    c.execute("SELECT id, kalan_hak, is_premium, premium_bitis, son_yenileme FROM kullanicilar WHERE telegram_id = ?", (telegram_id,))
    row = c.fetchone()

    if row is None:
        c.execute(
            "INSERT INTO kullanicilar (telegram_id, kalan_hak, is_premium, son_yenileme) VALUES (?, ?, 0, ?)",
            (telegram_id, UCRETSIZ_GUNLUK_HAK, bugun)
        )
        conn.commit()
        kullanici_id = c.lastrowid
        conn.close()
        return kullanici_id, UCRETSIZ_GUNLUK_HAK, False

    kullanici_id, kalan_hak, is_premium, premium_bitis, son_yenileme = row
    aktif_premium = _premium_aktif_mi(is_premium, premium_bitis)

    if aktif_premium:
        # Süresi geçmiş premium'u kapat
        if is_premium and not aktif_premium:
            c.execute("UPDATE kullanicilar SET is_premium = 0 WHERE id = ?", (kullanici_id,))
            conn.commit()
        conn.close()
        return kullanici_id, 999, True

    # Günlük hak yenile
    if son_yenileme != bugun:
        kalan_hak = UCRETSIZ_GUNLUK_HAK
        c.execute("UPDATE kullanicilar SET kalan_hak = ?, son_yenileme = ? WHERE id = ?",
                  (UCRETSIZ_GUNLUK_HAK, bugun, kullanici_id))
        conn.commit()

    conn.close()
    return kullanici_id, kalan_hak, False


def kullanici_getir_web(web_token):
    """Web token ile kullanıcı getir, yoksa oluştur."""
    conn = baglanti()
    c = conn.cursor()
    bugun = str(date.today())

    c.execute("SELECT id, kalan_hak, is_premium, premium_bitis, son_yenileme FROM kullanicilar WHERE web_token = ?", (web_token,))
    row = c.fetchone()

    if row is None:
        c.execute(
            "INSERT INTO kullanicilar (web_token, kalan_hak, is_premium, son_yenileme) VALUES (?, ?, 0, ?)",
            (web_token, UCRETSIZ_GUNLUK_HAK, bugun)
        )
        conn.commit()
        kullanici_id = c.lastrowid
        conn.close()
        return kullanici_id, UCRETSIZ_GUNLUK_HAK, False

    kullanici_id, kalan_hak, is_premium, premium_bitis, son_yenileme = row
    aktif_premium = _premium_aktif_mi(is_premium, premium_bitis)

    if aktif_premium:
        conn.close()
        return kullanici_id, 999, True

    if son_yenileme != bugun:
        kalan_hak = UCRETSIZ_GUNLUK_HAK
        c.execute("UPDATE kullanicilar SET kalan_hak = ?, son_yenileme = ? WHERE id = ?",
                  (UCRETSIZ_GUNLUK_HAK, bugun, kullanici_id))
        conn.commit()

    conn.close()
    return kullanici_id, kalan_hak, False


def hak_dusur(kullanici_id):
    conn = baglanti()
    conn.execute(
        "UPDATE kullanicilar SET kalan_hak = kalan_hak - 1 WHERE id = ? AND is_premium = 0 AND kalan_hak > 0",
        (kullanici_id,)
    )
    conn.commit()
    conn.close()


def sifre_sifirlama_token_olustur(email):
    """Email için token üret, DB'ye kaydet. (token, email) döner ya da None."""
    conn = baglanti()
    c = conn.cursor()
    c.execute("SELECT id FROM kullanicilar WHERE email = ?", (email.lower().strip(),))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    kullanici_id = row[0]
    token = secrets.token_urlsafe(32)
    gecerlilik = str(datetime.now() + timedelta(hours=1))
    c.execute("INSERT OR REPLACE INTO sifre_sifirlama (token, kullanici_id, gecerlilik) VALUES (?, ?, ?)",
              (token, kullanici_id, gecerlilik))
    conn.commit()
    conn.close()
    return token


def sifre_sifirlama_token_dogrula(token):
    """Token geçerliyse kullanici_id döner, değilse None."""
    conn = baglanti()
    c = conn.cursor()
    c.execute("SELECT kullanici_id, gecerlilik FROM sifre_sifirlama WHERE token = ?", (token,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    kullanici_id, gecerlilik = row
    if datetime.now() > datetime.fromisoformat(gecerlilik):
        return None
    return kullanici_id


def sifre_guncelle(kullanici_id, yeni_sifre):
    conn = baglanti()
    conn.execute("UPDATE kullanicilar SET sifre_hash = ? WHERE id = ?",
                 (generate_password_hash(yeni_sifre), kullanici_id))
    conn.execute("DELETE FROM sifre_sifirlama WHERE kullanici_id = ?", (kullanici_id,))
    conn.commit()
    conn.close()


def odeme_talebi_olustur(kullanici_id, not_bilgisi):
    conn = baglanti()
    conn.execute(
        "INSERT INTO odemeler (kullanici_id, platform, miktar, not_bilgisi, durum) VALUES (?, 'web', 50, ?, 'bekliyor')",
        (kullanici_id, not_bilgisi)
    )
    conn.commit()
    conn.close()


def bekleyen_odemeler():
    conn = baglanti()
    c = conn.cursor()
    c.execute("""
        SELECT o.id, k.email, o.not_bilgisi, o.created_at
        FROM odemeler o
        JOIN kullanicilar k ON k.id = o.kullanici_id
        WHERE o.durum = 'bekliyor'
        ORDER BY o.created_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def odeme_onayla(odeme_id):
    conn = baglanti()
    c = conn.cursor()
    c.execute("SELECT kullanici_id FROM odemeler WHERE id = ?", (odeme_id,))
    row = c.fetchone()
    if row:
        kullanici_id = row[0]
        c.execute("UPDATE odemeler SET durum = 'onaylandi' WHERE id = ?", (odeme_id,))
        conn.commit()
        conn.close()
        premium_ver(kullanici_id, ay=1)
        return True
    conn.close()
    return False


def analiz_kaydet(kullanici_id, platform, fen, hamle, skor):
    conn = baglanti()
    conn.execute(
        "INSERT INTO analizler (kullanici_id, platform, fen, en_iyi_hamle, skor) VALUES (?, ?, ?, ?, ?)",
        (kullanici_id, platform, fen, hamle, skor)
    )
    conn.commit()
    conn.close()


def premium_ver(kullanici_id, ay=1):
    """Kullanıcıya premium ver (ay kadar)."""
    conn = baglanti()
    c = conn.cursor()
    c.execute("SELECT premium_bitis FROM kullanicilar WHERE id = ?", (kullanici_id,))
    row = c.fetchone()
    bugun = date.today()

    try:
        mevcut_bitis = datetime.strptime(row[0], "%Y-%m-%d").date() if row and row[0] else bugun
        if mevcut_bitis < bugun:
            mevcut_bitis = bugun
    except (ValueError, TypeError):
        mevcut_bitis = bugun

    yeni_bitis = mevcut_bitis + timedelta(days=30 * ay)
    c.execute(
        "UPDATE kullanicilar SET is_premium = 1, premium_bitis = ? WHERE id = ?",
        (str(yeni_bitis), kullanici_id)
    )
    conn.commit()
    conn.close()
    return yeni_bitis


def reklam_hak_ekle(kullanici_id, gunluk_limit=5):
    """Reklam izleyince +1 hak ekle. Günde max gunluk_limit kez."""
    conn = baglanti()
    c = conn.cursor()
    bugun = str(date.today())
    c.execute("SELECT kalan_hak, reklam_hak_gun, reklam_hak_sayisi FROM kullanicilar WHERE id = ?", (kullanici_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False, "Kullanıcı bulunamadı"
    kalan_hak, reklam_gun, reklam_sayisi = row
    # Yeni gün mü?
    if reklam_gun != bugun:
        reklam_sayisi = 0
    if reklam_sayisi >= gunluk_limit:
        conn.close()
        return False, f"Bugün maksimum {gunluk_limit} reklam izleyebilirsin"
    c.execute(
        "UPDATE kullanicilar SET kalan_hak = kalan_hak + 1, reklam_hak_gun = ?, reklam_hak_sayisi = ? WHERE id = ?",
        (bugun, reklam_sayisi + 1, kullanici_id)
    )
    conn.commit()
    conn.close()
    return True, kalan_hak + 1


def analiz_gecmisi(kullanici_id, limit=10):
    conn = baglanti()
    c = conn.cursor()
    c.execute(
        "SELECT fen, en_iyi_hamle, skor, platform, created_at FROM analizler WHERE kullanici_id = ? ORDER BY created_at DESC LIMIT ?",
        (kullanici_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return rows


def istatistik(kullanici_id):
    conn = baglanti()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM analizler WHERE kullanici_id = ?", (kullanici_id,))
    toplam = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM analizler WHERE kullanici_id = ? AND DATE(created_at) = ?",
              (kullanici_id, str(date.today())))
    bugun_sayi = c.fetchone()[0]
    conn.close()
    return {"toplam": toplam, "bugun": bugun_sayi}
