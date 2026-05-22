import logging
import chess
import chess.engine
import os
import io
import numpy as np
import cv2
from PIL import Image
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler

import veritabani as db

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TELEGRAM_TOKEN = "8931626734:AAG_d0YmV8dtVpFqMAntFmodchqv25ZNxyk"
GEMINI_API_KEY = "AIzaSyBxoN2uWYEbbzbFQENoHurAn_0Ht8pOjuo"

ai_client = genai.Client(api_key=GEMINI_API_KEY)

su_anki_klasor = os.path.dirname(os.path.abspath(__file__))
stockfish_dosyalari = [f for f in os.listdir(su_anki_klasor) if f.endswith('.exe')]
if not stockfish_dosyalari:
    raise FileNotFoundError("Stockfish .exe dosyasını klasörde bulamadım!")
STOCKFISH_PATH = os.path.join(su_anki_klasor, stockfish_dosyalari[0])


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
            return Image.fromarray(cv2.cvtColor(open_cv_image[y1:y2, x1:x2], cv2.COLOR_BGR2RGB))

        if h > w * 1.1:
            fark = (h - w) // 2
            return Image.fromarray(cv2.cvtColor(open_cv_image[fark:fark + w, 0:w], cv2.COLOR_BGR2RGB))

        return pil_image
    except Exception as e:
        print(f"Kırpma hatası: {e}")
        return pil_image


def resimden_fen_uret(pil_image, hamle_sirasi):
    sira_harfi = "w" if hamle_sirasi == "beyaz" else "b"
    prompt = f"""Bu görselde bir satranç tahtası var. Tahtada uygulama arayüzü, oklar veya butonlar olabilir, onları yoksay.
Tahta beyazın veya siyahın perspektifinden gösterilebilir. Koordinat harflerine bakarak yönü anla.
Tahtadaki tüm taşların tam yerini tespit et ve standart FEN notasyonuyla tek satır olarak döndür.
KURALLAR: Sadece FEN yaz. Markdown kullanma. Hamle sırası '{sira_harfi}'. 6 bölüm olsun.
Örnek: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR {sira_harfi} KQkq - 0 1"""

    buf = io.BytesIO()
    pil_image.save(buf, format='JPEG', quality=95)
    img_part = types.Part.from_bytes(data=buf.getvalue(), mime_type='image/jpeg')

    modeller = ['models/gemini-flash-lite-latest', 'models/gemini-2.5-flash', 'models/gemini-2.0-flash']
    for model in modeller:
        for deneme in range(2):
            try:
                response = ai_client.models.generate_content(model=model, contents=[img_part, prompt])
                fen = response.text.strip().split('\n')[0]
                fen = fen.replace("`", "").replace("text", "").replace("fen", "").strip()
                chess.Board(fen)
                print(f"[{model}] FEN: {fen}")
                return fen
            except ValueError as e:
                print(f"[{model}] Deneme {deneme+1}: Geçersiz FEN: {e}")
            except Exception as e:
                hata = str(e)
                print(f"[{model}] Deneme {deneme+1}: {hata[:150]}")
                if '429' in hata or 'RESOURCE_EXHAUSTED' in hata:
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
    except Exception as e:
        print(f"Yorum hatası: {e}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    kullanici_id, kalan_hak, is_premium = db.kullanici_getir_telegram(user_id)
    durum = "Premium Üye 🌟" if is_premium else f"{kalan_hak} Ücretsiz Analiz Hakkı"
    istat = db.istatistik(kullanici_id)

    mesaj = (
        f"♟️ **Satranç Koçuna Hoş Geldin!** ♟️\n\n"
        f"📊 Durum: **{durum}**\n"
        f"🔢 Toplam analizin: **{istat['toplam']}**\n\n"
        f"Tahta fotoğrafını at, en iyi hamleyi söyleyeyim!\n\n"
        f"💎 _Premium için /premium komutunu kullan_"
    )
    await update.message.reply_text(mesaj, parse_mode="Markdown")


async def id_goster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Senin Chat ID'n: `{update.effective_user.id}`", parse_mode="Markdown")


async def premium_bilgi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    klavye = [[InlineKeyboardButton("⭐ Telegram Stars ile Satın Al", callback_data="stars_satin_al")]]
    mesaj = (
        "💎 **Premium Üyelik**\n\n"
        "✅ Sınırsız analiz\n"
        "✅ Top 3 hamle\n"
        "✅ Daha derin Stockfish analizi\n"
        "✅ Analiz geçmişi\n\n"
        "💰 Fiyat: **50 ⭐ Stars/ay**"
    )
    await update.message.reply_text(mesaj, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(klavye))


async def stars_odeme_baslat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title="Satranç Koçu Premium",
        description="1 aylık sınırsız analiz, top 3 hamle, derin Stockfish analizi",
        payload="premium_1ay",
        currency="XTR",
        prices=[LabeledPrice("Premium - 1 Ay", 50)]
    )


async def on_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def odeme_tamamlandi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    kullanici_id, _, _ = db.kullanici_getir_telegram(user_id)
    bitis = db.premium_ver(kullanici_id, ay=1)
    await update.message.reply_text(
        f"🎉 **Premium Aktif!**\n\n"
        f"⭐ Telegram Stars ödemeni aldık.\n"
        f"📅 Bitiş tarihi: `{bitis}`\n\n"
        f"Artık sınırsız analiz yapabilirsin! ♟️",
        parse_mode="Markdown"
    )


async def fotograf_yakala(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    kullanici_id, kalan_hak, is_premium = db.kullanici_getir_telegram(user_id)

    if kalan_hak <= 0 and not is_premium:
        await update.message.reply_text(
            "❌ **Analiz Hakkın Bitti!**\n\n"
            "Yarın 5 hakkın yenilenir.\n"
            "💎 Sınırsız analiz için: /premium"
        )
        return

    foto_dosyasi = await update.message.photo[-1].get_file()
    foto_bayt = await foto_dosyasi.download_as_bytearray()
    context.user_data['gecici_foto'] = foto_bayt
    context.user_data['kullanici_id'] = kullanici_id
    context.user_data['is_premium'] = is_premium

    klavye = [[
        InlineKeyboardButton("⚪ Beyazda", callback_data='beyaz'),
        InlineKeyboardButton("⚫ Siyahda", callback_data='siyah')
    ]]
    await update.message.reply_text("📸 Fotoğrafı aldım! Hamle sırası kimde?", reply_markup=InlineKeyboardMarkup(klavye))


async def buton_yakala(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    hamle_sirasi = query.data
    foto_bayt = context.user_data.get('gecici_foto')
    kullanici_id = context.user_data.get('kullanici_id')
    is_premium = context.user_data.get('is_premium', False)

    if not foto_bayt:
        await query.edit_message_text("❌ Fotoğraf hafızadan silinmiş. Tekrar gönder.")
        return

    await query.edit_message_text("✂️ Tahta taranıyor... ⚡")

    resim = Image.open(io.BytesIO(foto_bayt))
    temiz_resim = sadece_tahtayi_kirp(resim)

    try:
        fen = resimden_fen_uret(temiz_resim, hamle_sirasi)
    except RuntimeError:
        await query.edit_message_text("⏳ Günlük AI kotası doldu. Yarın tekrar dene veya /premium.")
        return

    if not fen:
        await query.edit_message_text(
            "❌ Tahta okunamadı.\n\n"
            "💡 Tahtanın tamamı görünür olsun, daha net bir fotoğraf dene."
        )
        return

    try:
        board = chess.Board(fen)

        if board.is_game_over():
            kazanan = "Siyah" if board.turn == chess.WHITE else "Beyaz"
            sonuc = f"🏁 **Şah Mat!** {kazanan} kazandı." if board.is_checkmate() else "🏁 **Beraberlik!**"
            await query.edit_message_text(sonuc, parse_mode="Markdown")
            return

        sure = 1.5 if is_premium else 0.5
        multipv = 3 if is_premium else 1

        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            analiz = engine.analyse(board, chess.engine.Limit(time=sure), multipv=multipv)

        if not isinstance(analiz, list):
            analiz = [analiz]

        hamleler = [a["pv"][0].uci() for a in analiz if a.get("pv")]
        en_iyi_hamle = hamleler[0] if hamleler else "Bulunamadı"
        alternatifler = " | ".join(hamleler[1:]) if len(hamleler) > 1 else ""
        skor_raw = analiz[0]["score"].white().score(mate_score=10000)
        skor_durumu = f"{skor_raw / 100:+.2f}" if skor_raw is not None else "Mat Yakın!"

        sira = "Beyazda" if board.turn == chess.WHITE else "Siyahda"
        yorum = yapay_zeka_yorumu_al(fen, en_iyi_hamle, skor_durumu)

        db.hak_dusur(kullanici_id)
        db.analiz_kaydet(kullanici_id, "telegram", fen, en_iyi_hamle, skor_durumu)
        _, yeni_hak, _ = db.kullanici_getir_telegram(user_id)
        hak_bilgisi = "Sınırsız 🌟" if is_premium else str(yeni_hak)

        cevap = (
            f"🧠 **Koçun Analizi:**\n"
            f"Sıra: **{sira}** | Hamle: `{en_iyi_hamle}` | Skor: `{skor_durumu}`\n"
        )
        if alternatifler:
            cevap += f"🔄 Alternatifler: `{alternatifler}`\n"
        if yorum:
            cevap += f"\n💬 **Koç:** {yorum}\n"
        cevap += f"\n📉 _Kalan hakkın: {hak_bilgisi}_"

        await query.edit_message_text(cevap, parse_mode="Markdown")

    except Exception as e:
        print(f"Analiz hatası: {e}")
        await query.edit_message_text("❌ Analiz sırasında hata oluştu. Tekrar dene.")


def main():
    db.veritabanini_hazirla()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", id_goster))
    app.add_handler(CommandHandler("premium", premium_bilgi))
    app.add_handler(MessageHandler(filters.PHOTO, fotograf_yakala))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, odeme_tamamlandi))
    app.add_handler(PreCheckoutQueryHandler(on_checkout))
    app.add_handler(CallbackQueryHandler(stars_odeme_baslat, pattern="stars_satin_al"))
    app.add_handler(CallbackQueryHandler(buton_yakala))
    print("Satranç Koçu Botu Aktif! 🚀")
    app.run_polling()


if __name__ == '__main__':
    main()
