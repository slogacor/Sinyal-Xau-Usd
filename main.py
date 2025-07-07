from flask import Flask
from threading import Thread
import requests
from datetime import datetime, time
import pytz
import asyncio
from telegram import Update, Message
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from ta.trend import EMAIndicator, SMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange, BollingerBands
import pandas as pd

BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"
AUTHORIZED_USER_ID = 1305881282
API_KEY = "841e95162faf457e8d80207a75c3ca2c"

signals_buffer = []
last_signal_price = None
job_running = False

app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is running"

def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

def is_bot_working_now():
    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    if now.weekday() == 4 and now.time() >= time(22, 0):  # Jumat setelah jam 22:00
        return False
    if now.weekday() == 0 and now.time() < time(8, 0):  # Senin sebelum jam 08:00
        return False
    return now.weekday() < 5  # Senin-Jumat

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
    if confirm_trend_from_last_3(df):
        score += 1
        note += "✅ Tiga candle mendukung arah\n"
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

async def send_signal(context):
    if not is_bot_working_now():
        return

    now = datetime.now(pytz.timezone("Asia/Jakarta"))
    candles = fetch_twelvedata("XAU/USD")
    if candles is None:
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
{note}"""

    global last_signal_price
    last_signal_price = price
    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
    signals_buffer.append({"signal": signal, "price": price, "tp1": tp1, "tp2": tp2, "sl": sl})

async def send_daily_summary(context):
    if not is_bot_working_now():
        return

    if not signals_buffer:
        await context.bot.send_message(chat_id=CHAT_ID, text="📊 Tidak ada sinyal yang dikirim hari ini.")
        return

    summary = "*📋 Rekap Sinyal Harian XAU/USD:*\n"
    for i, sig in enumerate(signals_buffer, 1):
        summary += f"{i}. {sig['signal']} @ {sig['price']} → TP1: {sig['tp1']}, TP2: {sig['tp2']}, SL: {sig['sl']}\n"

    await context.bot.send_message(chat_id=CHAT_ID, text=summary, parse_mode='Markdown')
    signals_buffer.clear()

async def monday_greeting(context):
    await context.bot.send_message(chat_id=CHAT_ID, text="📈 Selamat hari Senin! Semoga pekan ini penuh cuan 💰")

async def friday_closing(context):
    await context.bot.send_message(chat_id=CHAT_ID, text="📴 Sesi trading minggu ini ditutup. Selamat beristirahat dan sampai jumpa hari Senin! 🌴📉")

def ignore_bot_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.from_user and update.message.from_user.is_bot:
        return

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, ignore_bot_messages))

    jakarta_tz = pytz.timezone("Asia/Jakarta")
    app.job_queue.run_repeating(send_signal, interval=1800, first=10)
    app.job_queue.run_daily(send_daily_summary, time=time(hour=21, minute=59, tzinfo=jakarta_tz))
    app.job_queue.run_daily(monday_greeting, time=time(hour=8, minute=0, tzinfo=jakarta_tz), days=(0,))
    app.job_queue.run_daily(friday_closing, time=time(hour=22, minute=0, tzinfo=jakarta_tz), days=(4,))

    await app.run_polling()

if __name__ == '__main__':
    keep_alive()
    asyncio.run(main())
