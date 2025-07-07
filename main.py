from flask import Flask
from threading import Thread
import requests
from datetime import datetime
import pytz
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from ta.trend import EMAIndicator, SMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
import pandas as pd

# === CONFIG ===
BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"
AUTHORIZED_USER_ID = 1305881282
API_KEY = "841e95162faf457e8d80207a75c3ca2c"

signals_buffer = []
last_signal_price = None
job_running = False

# === KEEP ALIVE ===
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is running"

def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

# === FETCH DATA ===
def fetch_twelvedata(symbol="XAU/USD", interval="5min", count=100):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&apikey={API_KEY}&outputsize={count}&format=JSON"
    response = requests.get(url)
    if response.status_code != 200:
        return None
    data = response.json().get("values", [])
    return data[::-1] if data else None

def prepare_df(data):
    df = pd.DataFrame(data)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df.set_index("datetime", inplace=True)
    df = df.astype(float)
    return df

# === ANALISA ===
def generate_signal(df):
    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df["ema"] = EMAIndicator(df["close"], window=9).ema_indicator()
    df["sma"] = SMAIndicator(df["close"], window=50).sma_indicator()
    df["atr"] = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    df.dropna(inplace=True)

    last = df.iloc[-1]
    prev = df.iloc[-2]
    rsi = last["rsi"]
    price = float(last["close"])
    atr = float(last["atr"])

    score = 0
    note = []

    if rsi < 30:
        score += 1
        note.append("✅ RSI oversold")
    if last["ema"] > last["sma"]:
        score += 1
        note.append("✅ EMA > SMA (tren naik)")
    if last["close"] > last["open"] and prev["close"] > prev["open"]:
        score += 1
        note.append("✅ Dua candle naik berurutan")

    signal = "BUY" if last["close"] > prev["close"] else "SELL"
    return signal, score, "\n".join(note), last, atr

def calculate_tp_sl(signal, price, score, atr):
    pip_value = 0.01
    min_tp_pips = 30
    min_sl_pips = 20

    if signal == "BUY":
        tp1 = max(round(price + atr * (1 + score / 2), 2), round(price + min_tp_pips * pip_value, 2))
        tp2 = max(round(price + atr * (1.5 + score / 2), 2), round(price + (min_tp_pips + 10) * pip_value, 2))
        sl = min(round(price - atr * 0.8, 2), round(price - min_sl_pips * pip_value, 2))
    else:
        tp1 = min(round(price - atr * (1 + score / 2), 2), round(price - min_tp_pips * pip_value, 2))
        tp2 = min(round(price - atr * (1.5 + score / 2), 2), round(price - (min_tp_pips + 10) * pip_value, 2))
        sl = max(round(price + atr * 0.8, 2), round(price + min_sl_pips * pip_value, 2))

    return tp1, tp2, sl

def format_status(score):
    return "🟢 KUAT" if score == 3 else "🟡 MODERAT" if score == 2 else "🔴 LEMAH"

# === SIGNAL JOB ===
async def send_signal(context):
    candles = fetch_twelvedata("XAU/USD")
    if candles is None:
        await context.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD.")
        return

    df = prepare_df(candles)
    signal, score, note, last, atr = generate_signal(df)
    price = float(last["close"])
    tp1, tp2, sl = calculate_tp_sl(signal, price, score, atr)
    time_now = datetime.now(pytz.timezone("Asia/Jakarta")).strftime("%H:%M:%S")

    alert = ""
    if score < 3:
        alert = "\n⚠️ *Hati-hati*, sinyal tidak terlalu kuat."

    msg = (
        f"📡 *Sinyal XAU/USD*
"
        f"🕒 Waktu: {time_now} WIB\n"
        f"📈 Arah: *{signal}*\n"
        f"💰 Harga entry: `{price}`\n"
        f"🎯 TP1: `{tp1}` | TP2: `{tp2}`\n"
        f"🛑 SL: `{sl}`\n"
        f"{alert}\n"
        f"📊 Status: {format_status(score)}\n"
        f"🔍 Analisa:\n{note}"
    )

    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
    signals_buffer.append({"signal": signal, "price": price, "tp1": tp1, "tp2": tp2, "sl": sl})

# === REKAP ===
async def rekap_harian(context):
    jakarta = pytz.timezone("Asia/Jakarta")
    now = datetime.now(jakarta)
    df = prepare_df(fetch_twelvedata("XAU/USD", "5min", 60)).tail(60)

    tp_total = sum(20 for i in df.itertuples() if i.close > i.open)
    sl_total = sum(10 for i in df.itertuples() if i.close <= i.open)

    msg = (
        f"📊 *Rekap Harian XAU/USD - {now.strftime('%A, %d %B %Y')}*
"
        f"🕙 Waktu: {now.strftime('%H:%M')} WIB\n"
        f"🎯 Total TP: {tp_total} pips\n"
        f"🛑 Total SL: {sl_total} pips\n"
        f"📌 Berdasarkan 5-menit candle terakhir 5 jam"
    )

    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global job_running

    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("❌ Anda tidak diizinkan menjalankan bot ini.")
        return

    if job_running:
        await update.message.reply_text("⚠️ Bot sudah berjalan.")
        return

    job_running = True
    await update.message.reply_text("✅ Bot aktif. Sinyal akan dikirim setiap 2 jam.")

    async def sinyal_job():
        while True:
            await context.bot.send_message(chat_id=CHAT_ID, text="📣 *Sinyal 5 menit lagi!* Bersiap entry.", parse_mode='Markdown')
            await asyncio.sleep(5 * 60)
            await send_signal(context)
            await asyncio.sleep(115 * 60)

    async def rekap_job():
        while True:
            now = datetime.now(pytz.timezone("Asia/Jakarta"))
            if now.weekday() < 5 and now.hour == 21 and now.minute == 59:
                await rekap_harian(context)
            await asyncio.sleep(60)

    asyncio.create_task(sinyal_job())
    asyncio.create_task(rekap_job())

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    candles = fetch_twelvedata("XAU/USD", "1min", 1)
    if candles:
        price = candles[-1]["close"]
        await update.message.reply_text(f"Harga XAU/USD sekarang: {price}")
    else:
        await update.message.reply_text("❌ Tidak bisa mengambil harga.")

# === MAIN ===
if __name__ == "__main__":
    keep_alive()
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("price", price))
    app_bot.run_polling()
