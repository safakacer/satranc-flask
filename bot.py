import logging
import chess
import chess.engine
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Hataları ekranda görmek için loglama ayarı
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- BİLGİLERİNİZ ---
TOKEN = "8931626734:AAG_d0YmV8dtVpFqMAntFmodchqv25ZNxyk"

# Kodun, yanındaki Stockfish dosyasını otomatik bulması için ayar
su_anki_klasor = os.path.dirname(os.path.abspath(__file__))
# Klasördeki .exe dosyasının adını otomatik tarar
stockfish_dosyalari = [f for f in os.listdir(su_anki_klasor) if f.endswith('.exe')]

if not stockfish_dosyalari:
    raise FileNotFoundError("Knk, Stockfish .exe dosyasını bu klasörün içine atmayı unuttun!")

STOCKFISH_PATH = os.path.join(su_anki_klasor, stockfish_dosyalari[0])
# --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mesaj = (
        "♟️ **Yapay Zeka Satranç Koçuna Hoş Geldin!** ♟️\n\n"
        "Bana analiz etmemi istediğin pozisyonun **FEN kodunu** gönder, "
        "sana en iyi hamleyi söyleyeyim!"
    )
    await update.message.reply_text(mesaj, parse_mode="Markdown")

async def analiz_et(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kullanici_mesaji = update.message.text.strip()
    try:
        board = chess.Board(kullanici_mesaji)
        bekle_mesaj = await update.message.reply_text("Pozisyonu inceliyorum... 🧠")
        
        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            analiz = engine.analyse(board, chess.engine.Limit(time=0.8))
            en_iyi_hamle = analiz.get("pv")[0].uci() if "pv" in analiz else "Bulunamadı"
            skor = analiz.get("score").white().score(mate_score=10000)
            skor_durumu = f"{skor / 100:+}" if skor is not None else "Mat Yakın!"

        sıra = "Beyazda" if board.turn == chess.WHITE else "Siyahda"
        cevap = (
            f"🧠 **Koçun Analizi:**\n\n"
            f"Sıra şu an **{sıra}** tarafında.\n"
            f"🚀 En iyi hamle: `{en_iyi_hamle}`\n"
            f"📊 Skor: `{skor_durumu}`"
        )
        await bekle_mesaj.edit_text(cevap, parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Bu geçerli bir FEN kodu değil knk. Lütfen doğru bir kod gönder.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analiz_et))
    print("Bot şu an aktif ve mesajları bekliyor...")
    app.run_polling()

if __name__ == '__main__':
    main()