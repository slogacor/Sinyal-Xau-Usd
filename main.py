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

signals_buffer = []
last_signal_price = None

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running"

def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

def is_bot_working_now():
    now = datetime.now(pytz.timezone("Asia/Jakarta"))

    # Jumat setelah jam 22:00 libur
    if now.weekday() == 4 and now.time() >= time(22, 0):
        return False

    # Sabtu dan Minggu libur
    if now.weekday() in [5, 6]:
        return False

    # Senin – Kamis full 24 jam
    return True

def fetch_twelvedata(symbol="XAU/USD", interval="5min", count=50):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&apikey={API_KEY}&outputsize={count}&format=JSON"
    try:
        response = requests.get(url, timeout=10)
    except requests.RequestException as e:
        print(f"❌ Request error: {e}")
        return None

    if response.status_code != 200:
        print(f"❌ Gagal ambil data: HTTP {response.status_code}")
        return None
    
    json_data = response.json()

    if "code" in json_data:
        if json_data["code"] == 429:
            print(f"❌ Limit API habis: {json_data.get('message', '')}")
            return None
        else:
            print(f"❌ Error API: {json_data.get('message', 'Unknown error')}")
            return None

    data = json_data.get("values", [])
    if not data:
        print("❌ Data kosong dari API.")
        return None

    return data[::-1]

def prepare_df(data):
    try:
        df = pd.DataFrame(data)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df.set_index("datetime", inplace=True)
        df = df.astype(float)
        return df
    except Exception as e:
        print(f"❌ Error prepare_df: {e}")
        return None

def find_snr(df):
    highs = df["high"].tail(30)
    lows = df["low"].tail(30)
    return highs.max(), lows.min()

def generate_signal(df):
    if df is None or len(df) < 50:
        print("Data kurang dari 50 baris.")
        return None, None, None, None, None, None

    try:
        rsi = RSIIndicator(df["close"], window=14).rsi()
        ema = EMAIndicator(df["close"], window=9).ema_indicator()
        sma = SMAIndicator(df["close"], window=50).sma_indicator()
        atr = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        macd_line = MACD(df["close"]).macd()
        macd_signal = MACD(df["close"]).macd_signal()
        bollinger = BollingerBands(df["close"])
        bb_upper = bollinger.bollinger_hband()
        bb_lower = bollinger.bollinger_lband()

        df["rsi"] = rsi
        df["ema"] = ema
        df["sma"] = sma
        df["atr"] = atr
        df["macd"] = macd_line
        df["macd_signal"] = macd_signal
        df["bb_upper"] = bb_upper
        df["bb_lower"] = bb_lower

        df.dropna(inplace=True)

        if len(df) < 4:
            print("Data kurang setelah dropna.")
            return None, None, None, None, None, None

        df_analyze = df.tail(4)
        last = df_analyze.iloc[-1]
        prev = df_analyze.iloc[-2]

        snr_res, snr_sup = find_snr(df)

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
            note += "✅ Breakout Bollinger Band\n"

        signal = "BUY" if last["close"] > prev["close"] else "SELL"
        return signal, score, note, last, snr_res, snr_sup

    except Exception as e:
        print(f"❌ Error generate_signal: {e}")
        return None, None, None, None, None, None

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
            if time_str.lower() in ["all day", "tentative"]:
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
                print(f"🚨 Ada berita berdampak tinggi: {news_jakarta_time.strftime('%H:%M')} WIB")
                return True

        return False

    except Exception as e:
        print(f"❌ Error cek news: {e}")
        return False

async def send_signal(context):
    if not is_bot_working_now():
        return

    if check_high_impact_news():
        await context.bot.send_message(chat_id=CHAT_ID, text="🚨 Ada berita berdampak tinggi. Sinyal di-skip.")
        return

    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    candles = fetch_twelvedata("XAU/USD", interval="5min", count=50)
    if candles is None:
        await context.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD.")
        return

    df = prepare_df(candles)
    if df is None:
        await context.bot.send_message(chat_id=CHAT_ID, text="❌ Error saat proses data candle.")
        return

    result = generate_signal(df)
    if result[0] is None:
        await context.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal generate sinyal.")
        return

    signal, score, note, last_candle, snr_res, snr_sup = result

    price = last_candle["close"]
    tp1, tp2, sl = calculate_tp_sl(signal, price, score, last_candle["atr"])
    time_now = now.strftime("%H:%M:%S")
    alert = "\n⚠️ *Hati-hati*, sinyal tidak terlalu kuat.\n" if score < 3 else ""

    msg = f"""📡 *Sinyal XAU/USD*
🕒 Waktu: {time_now} WIB
📈 Arah: *{signal}*
💰 Harga entry: `{price}`
🎯 TP1: `{tp1}` | TP2: `{tp2}`
🛑 SL: `{sl}`{alert}
📊 Status: {format_status(score)}
🔍 Analisa:
{note}
📌 *Gunakan sinyal setelah candle M5 selesai.*
"""

    global last_signal_price
    last_signal_price = price
    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
    signals_buffer.append({"signal": signal, "price": price, "tp1": tp1, "tp2": tp2, "sl": sl})

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("🚫 Anda tidak berhak menggunakan bot ini.")
        return
    await update.message.reply_text("Bot sudah aktif.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Perintah yang tersedia:\n/start\n/help\n/info")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "Bot sinyal XAU/USD\nSenin-Kamis: 24 jam\nJumat: Sampai 22:00 WIB\nSabtu & Minggu libur\nAnalisa candle: M5 (5 menit)"
    await update.message.reply_text(msg)

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Maaf, perintah tidak dikenali.")

def main():
    keep_alive()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    job_queue = application.job_queue

    # Kirim sinyal langsung saat pertama kali deploy
    asyncio.get_event_loop().create_task(send_signal(context=type('obj', (object,), {"bot": application.bot})()))

    # Kirim sinyal tiap jam tepat di menit 00
    job_queue.run_repeating(send_signal, interval=3600, first=0)

    print("Bot started...")
    application.run_polling()

if __name__ == "__main__":
    main()
