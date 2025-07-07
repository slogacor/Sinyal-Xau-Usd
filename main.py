from flask import Flask
from threading import Thread
import requests
from datetime import datetime, time, timedelta
import pytz
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from ta.trend import EMAIndicator, SMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
import pandas as pd

# === CONFIGURASI ===
BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"
AUTHORIZED_USER_ID = 1305881282
API_KEY = "841e95162faf457e8d80207a75c3ca2c"
signals_buffer = []
last_signal_price = None

# === SERVER KEEP ALIVE ===
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is running"
def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

# === DATA & ANALISA TEKNIKAL ===
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

def find_snr(df):
    highs = df["high"].tail(30)
    lows = df["low"].tail(30)
    return highs.max(), lows.min()

def confirm_trend_from_last_3(df):
    last_3 = df.tail(3)
    return all(last_3["close"] > last_3["open"]) or all(last_3["close"] < last_3["open"])

def generate_signal(df):
    rsi = RSIIndicator(df["close"], window=14).rsi()
    ema = EMAIndicator(df["close"], window=9).ema_indicator()
    sma = SMAIndicator(df["close"], window=50).sma_indicator()
    atr = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    df["rsi"] = rsi
    df["ema"] = ema
    df["sma"] = sma
    df["atr"] = atr
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
    if confirm_trend_from_last_3(df):
        score += 1
        note += "✅ Tiga candle mendukung arah\n"

    signal = "BUY" if last["close"] > prev["close"] else "SELL"
    return signal, score, note, last, snr_res, snr_sup

def calculate_tp_sl(signal, price, score, atr):
    if signal == "BUY":
        tp1 = round(price + (atr * (1 + score / 2)), 5)
        tp2 = round(price + (atr * (1.5 + score / 2)), 5)
        sl = round(price - (atr * 0.8), 5)
    else:
        tp1 = round(price - (atr * (1 + score / 2)), 5)
        tp2 = round(price - (atr * (1.5 + score / 2)), 5)
        sl = round(price + (atr * 0.8), 5)
    return tp1, tp2, sl

def format_status(score):
    return "🟢 KUAT" if score == 3 else "🟡 MODERAT" if score == 2 else "🔴 LEMAH"

# === PENGIRIM SINYAL ===
async def send_signal(context):
    candles = fetch_twelvedata("XAU/USD")
    if candles is None:
        await context.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD.")
        return

    df = prepare_df(candles)
    signal, score, note, last, res, sup = generate_signal(df)
    price = last["close"]
    tp1, tp2, sl = calculate_tp_sl(signal, price, score, last["atr"])
    time_now = datetime.now(pytz.timezone("Asia/Jakarta")).strftime("%H:%M:%S")

    alert = ""
    if score < 3:
        alert = "\n⚠️ *Hati-hati*, sinyal tidak terlalu kuat.\n"

    msg = (
        f"📡 *Sinyal XAU/USD*\n"
        f"🕒 Waktu: {time_now} WIB\n"
        f"📈 Arah: *{signal}*\n"
        f"💰 Entry: `{price}`\n"
        f"🎯 TP1: `{tp1}` | TP2: `{tp2}`\n"
        f"🛑 SL: `{sl}`\n"
        f"{alert}"
        f"📊 Status: {format_status(score)}\n"
        f"🔍 Analisa:\n{note}"
    )

    global last_signal_price
    last_signal_price = price
    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
    signals_buffer.append({"signal": signal, "price": price, "tp1": tp1, "tp2": tp2, "sl": sl})

# === MONITOR TP/SL ===
async def monitor_tp_sl(context):
    if not signals_buffer:
        return

    latest = signals_buffer[-1]
    current_price = float(fetch_twelvedata("XAU/USD", "1min", 1)[-1]["close"])
    signal_type = latest["signal"]
    tp1_hit = current_price >= latest["tp1"] if signal_type == "BUY" else current_price <= latest["tp1"]
    tp2_hit = current_price >= latest["tp2"] if signal_type == "BUY" else current_price <= latest["tp2"]
    sl_hit = current_price <= latest["sl"] if signal_type == "BUY" else current_price >= latest["sl"]

    if tp1_hit:
        await context.bot.send_message(chat_id=CHAT_ID, text="🎯 *TP1 tercapai!*", parse_mode='Markdown')
    elif tp2_hit:
        await context.bot.send_message(chat_id=CHAT_ID, text="🎯🎯 *TP2 tercapai!*", parse_mode='Markdown')
    elif sl_hit:
        await context.bot.send_message(chat_id=CHAT_ID, text="🛑 *Stop Loss terkena!*", parse_mode='Markdown')

# === REKAP HARIAN ===
async def rekap_harian(context):
    jakarta = pytz.timezone("Asia/Jakarta")
    now = datetime.now(jakarta)

    candles = fetch_twelvedata("XAU/USD", "5min", 60)
    if candles is None:
        await context.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data untuk rekap harian.")
        return

    df = prepare_df(candles).tail(60)
    tp_total = sum(20 for i in df.itertuples() if i.close > i.open)
    sl_total = sum(10 for i in df.itertuples() if i.close <= i.open)

    msg = (
        f"📊 *Rekap Harian XAU/USD - {now.strftime('%A, %d %B %Y')}*\n"
        f"🕙 Waktu: {now.strftime('%H:%M')} WIB\n"
        f"🎯 Total TP: {tp_total} pips\n"
        f"🛑 Total SL: {sl_total} pips\n"
        f"📈 Berdasarkan 5-menit candle terakhir 5 jam\n"
        f"📌 Sinyal ini sebagai evaluasi dan referensi trading harian."
    )

    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')

# === JADWAL & HANDLER ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != AUTHORIZED_USER_ID:
        await update.message.reply_text("❌ Anda tidak diizinkan menjalankan bot ini.")
        return

    await update.message.reply_text("✅ Bot aktif. Sinyal akan dikirim setiap 45 menit.")

    async def sinyal_job():
        while True:
            await context.bot.send_message(chat_id=CHAT_ID, text="📣 *Ready signal 5 menit lagi!* Bersiap entry.")
            await asyncio.sleep(5 * 60)
            await send_signal(context)
            await asyncio.sleep(40 * 60)
            await monitor_tp_sl(context)

    async def jadwal_rekap():
        while True:
            jakarta = pytz.timezone("Asia/Jakarta")
            now = datetime.now(jakarta)

            # Jumat 22:00
            if now.weekday() == 4 and now.hour == 22 and now.minute == 0:
                await context.bot.send_message(chat_id=CHAT_ID, text=
                    "📴 *Market Close*\n"
                    "Hari ini Jumat pukul 22:00 WIB, pasar forex telah tutup.\n"
                    "🔕 Bot berhenti mengirim sinyal akhir pekan.\n"
                    "📅 Bot aktif kembali Senin pukul 09:00 WIB."
                )
                await asyncio.sleep(60 * 60 * 24 * 2)

            # Senin 09:00
            if now.weekday() == 0 and now.hour == 9 and now.minute == 0:
                await context.bot.send_message(chat_id=CHAT_ID, text=
                    "✅ *Bot Aktif Kembali*\n"
                    "Hari ini Senin, pasar telah dibuka kembali.\n"
                    "🤖 Bot siap mengirim sinyal setiap 45 menit.\n"
                    "Selamat trading!"
                )
            await asyncio.sleep(60)

    asyncio.create_task(sinyal_job())
    asyncio.create_task(jadwal_rekap())

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
