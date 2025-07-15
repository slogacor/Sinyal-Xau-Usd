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
    if now.weekday() == 4 and now.time() >= time(22, 0):
        return False
    if now.weekday() == 0 and now.time() < time(8, 0):
        return False
    return now.weekday() < 5

def fetch_twelvedata(symbol="XAU/USD", interval="5min", count=100):
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

    last = df.iloc[-1]
    prev = df.iloc[-2]
    snr_res, snr_sup = find_snr(df)

    score = 0
    note = ""

    if last["rsi"] < 30 and last["close"] > last["ema"]:
        score += 1
        note += "✅ RSI oversold + harga di atas EMA\n"
    if last["ema"] > last["sma"]:
        score += 1
        note += "✅ EMA > SMA (tren naik)\n"
    # Removed 3 candle confirmation as requested
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

async def send_signal(context):
    if not is_bot_working_now():
        return

    if check_high_impact_news():
        await context.bot.send_message(chat_id=CHAT_ID, text="🚨 Ada berita berdampak tinggi sekarang, sinyal di-skip dulu ya.")
        return

    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    candles = fetch_twelvedata("XAU/USD")
    if candles is None:
        print("⚠️ Tidak bisa ambil candle. Mungkin limit API habis?")
        await context.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD.")
        return

    df = prepare_df(candles)
    signal, score, note, last, res, sup = generate_signal(df)
    price = last["close"]
    tp1, tp2, sl = calculate_tp_sl(signal, price, score, last["atr"])
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
📌 *Cara menggunakan sinyal:* Tunggu candle 5-menit ini selesai, lalu entry sesuai arah sinyal jika harga masih mendukung.
"""

    global last_signal_price
    last_signal_price = price
    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
    signals_buffer.append({"signal": signal, "price": price, "tp1": tp1, "tp2": tp2, "sl": sl})

# Tambahan fungsi dasar handler agar tidak error

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Halo! Bot sudah aktif. Gunakan /help untuk info lebih lanjut."
    )

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Pesan diterima.")

async def ignore_bot_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Error terjadi: {context.error}")

# Contoh fungsi dummy lainnya, kamu bisa buat sendiri sesuai kebutuhan
async def send_daily_summary(context):
    # Implementasi sesuai kebutuhan
    pass

async def monday_greeting(context):
    # Implementasi sesuai kebutuhan
    pass

async def friday_closing(context):
    # Implementasi sesuai kebutuhan
    pass

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    job_queue = application.job_queue
    jakarta_tz = pytz.timezone("Asia/Jakarta")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_user_message))
    application.add_handler(MessageHandler(filters.ALL, ignore_bot_messages))
    application.add_error_handler(error_handler)

    # Jalankan cek sinyal setiap 30 menit (1800 detik)
    job_queue.run_repeating(send_signal, interval=3600, first=10)
    job_queue.run_daily(send_daily_summary, time=time(hour=21, minute=59, tzinfo=jakarta_tz))
    job_queue.run_daily(monday_greeting, time=time(hour=8, minute=0, tzinfo=jakarta_tz), days=(0,))
    job_queue.run_daily(friday_closing, time=time(hour=22, minute=0, tzinfo=jakarta_tz), days=(4,))

    await application.run_polling()

if __name__ == '__main__':
    keep_alive()

    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass

    loop = asyncio.get_event_loop()
    loop.create_task(main())
    loop.run_forever()
