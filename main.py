from flask import Flask
from threading import Thread
import requests
from datetime import datetime, time, timedelta
import pytz
import asyncio
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, filters
)
from ta.trend import EMAIndicator, SMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange, BollingerBands
import pandas as pd
from bs4 import BeautifulSoup

# Konfigurasi
BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"
AUTHORIZED_USER_ID = 1305881282
API_KEY = "21a0860958e641cc934bec6277415088"

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running"

def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

def is_bot_working_now():
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    if now.weekday() == 4 and now.time() >= time(22, 0):
        return False
    if now.weekday() == 0 and now.time() < time(8, 0):
        return False
    return now.weekday() < 5

def fetch_twelvedata(symbol="XAU/USD", interval="15min", count=10):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&apikey={API_KEY}&outputsize={count}&format=JSON"
    response = requests.get(url)

    if response.status_code != 200:
        print(f"❌ Gagal ambil data: HTTP {response.status_code}")
        return None
    
    json_data = response.json()

    if "code" in json_data and json_data["code"] == 429:
        print(f"❌ Limit API habis: {json_data['message']}")
        return None

    data = json_data.get("values", [])
    return data[::-1] if data else None

def prepare_df(data):
    df = pd.DataFrame(data)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df.set_index("datetime", inplace=True)
    df = df.astype(float)
    return df

def find_snr(df):
    highs = df["high"].tail(30)
    lows = df["low"].tail(30)
    return highs.max(), lows.min()

def generate_signal(df):
    # Analisa 4 candle M15 terakhir sebelum candle berjalan
    # Jadi ambil candle ke-5 sampai ke-2 dari akhir (index -5 sampai -2)
    if len(df) < 6:
        return None, 0, "", None, None, None

    df_analyze = df.iloc[-6:-2].copy()  # 4 candle sebelum candle berjalan

    rsi = RSIIndicator(df_analyze["close"], window=14).rsi()
    ema = EMAIndicator(df_analyze["close"], window=9).ema_indicator()
    sma = SMAIndicator(df_analyze["close"], window=50).sma_indicator()
    atr = AverageTrueRange(df_analyze["high"], df_analyze["low"], df_analyze["close"], window=14).average_true_range()
    macd_line = MACD(df_analyze["close"]).macd()
    macd_signal = MACD(df_analyze["close"]).macd_signal()
    bollinger = BollingerBands(df_analyze["close"])
    bb_upper = bollinger.bollinger_hband()
    bb_lower = bollinger.bollinger_lband()

    df_analyze["rsi"] = rsi
    df_analyze["ema"] = ema
    df_analyze["sma"] = sma
    df_analyze["atr"] = atr
    df_analyze["macd"] = macd_line
    df_analyze["macd_signal"] = macd_signal
    df_analyze["bb_upper"] = bb_upper
    df_analyze["bb_lower"] = bb_lower
    df_analyze.dropna(inplace=True)

    last = df_analyze.iloc[-1]
    prev = df_analyze.iloc[-2]
    snr_res, snr_sup = find_snr(df_analyze)

    score = 0
    note = ""

    if last["rsi"] < 30 and last["close"] > last["ema"]:
        score += 1
        note += "✅ RSI oversold + harga di atas EMA\n"
    if last["ema"] > last["sma"]:
        score += 1
        note += "✅ EMA > SMA (tren naik)\n"
    if last["macd"] > last["macd_signal"]:
        score += 1
        note += "✅ MACD crossover ke atas\n"
    if last["close"] > last["bb_upper"] or last["close"] < last["bb_lower"]:
        score += 1
        note += "✅ Harga breakout dari Bollinger Band\n"

    signal = "BUY" if last["close"] > prev["close"] else "SELL"

    return signal, score, note, last, snr_res, snr_sup

def calculate_tp_sl(signal, price, score, atr):
    pip = 0.01
    min_tp = 30 * pip
    min_sl = 20 * pip

    if signal == "BUY":
        tp1 = round(max(price + atr * (1 + score / 2), price + min_tp), 2)
        tp2 = round(max(price + atr * (1.5 + score / 2), price + min_tp + 10 * pip), 2)
        sl = round(min(price - atr * 0.8, price - min_sl), 2)
    else:
        tp1 = round(min(price - atr * (1 + score / 2), price - min_tp), 2)
        tp2 = round(min(price - atr * (1.5 + score / 2), price - min_tp - 10 * pip), 2)
        sl = round(max(price + atr * 0.8, price + min_sl), 2)

    return tp1, tp2, sl

def format_status(score):
    return "🟢 KUAT" if score >= 4 else "🟡 MODERAT" if score >= 2 else "🔴 LEMAH"

def check_high_impact_news():
    try:
        url = "https://www.forexfactory.com/calendar.php?week=this"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"❌ Gagal akses Forex Factory: HTTP {response.status_code}")
            return False

        soup = BeautifulSoup(response.text, "html.parser")
        now = datetime.now(pytz.timezone("Asia/Jakarta"))

        rows = soup.select("tr.calendar__row")
        for row in rows:
            impact = row.select_one("td.calendar__impact")
            time_td = row.select_one("td.calendar__time")
            if not impact or not time_td:
                continue

            impact_text = impact.get("title", "").lower()
            if "high" not in impact_text:
                continue

            time_str = time_td.get_text(strip=True)
            if not time_str or time_str.lower() in ["all day", "tentative"]:
                continue

            try:
                news_time = datetime.strptime(time_str, "%H:%M").time()
            except:
                continue

            ny_tz = pytz.timezone("America/New_York")
            jakarta_tz = pytz.timezone("Asia/Jakarta")
            today_ny = datetime.now(ny_tz).replace(hour=news_time.hour, minute=news_time.minute, second=0, microsecond=0)
            news_jakarta_time = today_ny.astimezone(jakarta_tz)

            delta = abs((news_jakarta_time - now).total_seconds())
            if delta <= 1800:
                print(f"🚨 Ada berita berdampak tinggi sekarang atau ±30 menit: {news_jakarta_time.strftime('%H:%M')} WIB")
                return True

        return False

    except Exception as e:
        print(f"❌ Error cek news: {e}")
        return False

last_signal_hour = None  # Untuk mencegah duplikat kirim sinyal per jam

async def send_signal(context):
    global last_signal_hour

    if not is_bot_working_now():
        return

    now = datetime.now(pytz.timezone("Asia/Jakarta"))

    # Cek apakah sekarang menit 0, 15, 30, 45 (akhir candle M15)
    if now.minute not in [0, 15, 30, 45]:
        return

    # Kirim sinyal hanya di menit 0 setiap jam (sesuai permintaan)
    if now.minute == 0:
        if last_signal_hour == now.hour:
            # Sudah kirim sinyal di jam ini, skip
            return
        last_signal_hour = now.hour
    else:
        return  # Skip menit 15,30,45 karena gak kirim sinyal

    if check_high_impact_news():
        await context.bot.send_message(chat_id=CHAT_ID, text="🚨 Ada berita berdampak tinggi, sinyal di-skip dulu ya.")
        return

    data = fetch_twelvedata()
    if not data:
        await context.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data harga.")
        return

    df = prepare_df(data)
    signal, score, note, last_candle, snr_res, snr_sup = generate_signal(df)

    if not signal:
        await context.bot.send_message(chat_id=CHAT_ID, text="⚠️ Data tidak cukup untuk analisa sinyal.")
        return

    tp1, tp2, sl = calculate_tp_sl(signal, last_candle["close"], score, last_candle["atr"] if "atr" in last_candle else 0.5)
    status = format_status(score)
    waktu = now.strftime("%Y-%m-%d %H:%M WIB")

    message = (
        f"💰 <b>{signal} XAU/USD</b>\n"
        f"⌚️ Waktu: {waktu}\n"
        f"🎯 TP1: {tp1}\n"
        f"🎯 TP2: {tp2}\n"
        f"🛑 SL: {sl}\n"
        f"⚡️ Status: {status}\n"
        f"📊 Catatan:\n{note}"
        f"\n\n⚠️ Entry di candle M5 setelah candle M15 selesai."
    )

    await context.bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("⛔️ Anda tidak berhak menggunakan bot ini.")
        return
    await update.message.reply_text("Bot sudah aktif!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ketik /start untuk memulai.")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Perintah tidak dikenal.")

def main():
    keep_alive()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    # Run job setiap 60 detik cek sinyal
    application.job_queue.run_repeating(send_signal, interval=60, first=10)

    print("Bot started...")
    application.run_polling()

if __name__ == "__main__":
    main()
