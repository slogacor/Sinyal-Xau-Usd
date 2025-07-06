from flask import Flask
from threading import Thread
import requests
import logging
from datetime import datetime, timedelta, time, timezone
import asyncio
import pandas as pd
import ta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackContext
import pytz

# --- KONFIGURASI ---
BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"
AUTHORIZED_USER_ID = 1305881282
API_KEY = "841e95162faf457e8d80207a75c3ca2c"
signals_buffer = []

# === KEEP ALIVE ===
app = Flask('')
@app.route('/')
def home():
    return "Bot is alive!"
def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

# === DATA DAN ANALISIS ===
def fetch_twelvedata(symbol="XAU/USD", interval="5min", outputsize=100):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&outputsize={outputsize}&apikey={API_KEY}"
    try:
        res = requests.get(url)
        data = res.json()
        if "values" not in data:
            logging.error("Data tidak tersedia: %s", data.get("message", ""))
            return None
        candles = [{
            "datetime": datetime.strptime(d["datetime"], "%Y-%m-%d %H:%M:%S"),
            "open": float(d["open"]),
            "high": float(d["high"]),
            "low": float(d["low"]),
            "close": float(d["close"])
        } for d in data["values"]]
        return candles
    except Exception as e:
        logging.error(f"Gagal ambil data dari Twelve Data: {e}")
        return None

def prepare_df(candles):
    df = pd.DataFrame(candles)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df

def find_snr(df):
    recent = df.tail(30)
    support = recent["low"].min()
    resistance = recent["high"].max()
    return support, resistance

def confirm_trend_from_last_3(df):
    candles = df.tail(4)
    if len(candles) < 4:
        return None
    c1, c2, c3 = candles.iloc[-4:-1].to_dict('records')
    uptrend = all(c["close"] > c["open"] for c in [c1, c2, c3])
    downtrend = all(c["close"] < c["open"] for c in [c1, c2, c3])
    if uptrend:
        return "BUY"
    elif downtrend:
        return "SELL"
    else:
        return None

def generate_signal(df):
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    df["ma"] = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
    df["ema"] = ta.trend.EMAIndicator(df["close"], window=20).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()

    support, resistance = find_snr(df)
    last_close = df["close"].iloc[-1]
    rsi_now = df["rsi"].iloc[-1]
    ma = df["ma"].iloc[-1]
    ema = df["ema"].iloc[-1]
    atr = df["atr"].iloc[-1]

    trend = confirm_trend_from_last_3(df)
    if not trend:
        if atr > 0.2:
            return ("LEMAH", last_close, rsi_now, atr, ma, ema), 1, support, resistance
        else:
            return None, 0, support, resistance

    score = 0
    if atr > 0.2:
        score += 1
    if trend == "BUY" and last_close > ma and last_close > ema and rsi_now < 70:
        score += 2
    elif trend == "SELL" and last_close < ma and last_close < ema and rsi_now > 30:
        score += 2

    if score >= 3:
        return (trend, last_close, rsi_now, atr, ma, ema), score, support, resistance
    elif score >= 1:
        return (trend, last_close, rsi_now, atr, ma, ema), score, support, resistance
    else:
        return None, score, support, resistance

def calculate_tp_sl(signal, entry, score):
    if score >= 3:
        tp1_pips, tp2_pips, sl_pips = 30, 55, 20
    elif score == 2:
        tp1_pips, tp2_pips, sl_pips = 25, 40, 20
    else:
        tp1_pips, tp2_pips, sl_pips = 15, 25, 15

    tp1 = entry + tp1_pips * 0.1 if signal == "BUY" else entry - tp1_pips * 0.1
    tp2 = entry + tp2_pips * 0.1 if signal == "BUY" else entry - tp2_pips * 0.1
    sl = entry - sl_pips * 0.1 if signal == "BUY" else entry + sl_pips * 0.1

    return tp1, tp2, sl, tp1_pips, tp2_pips, sl_pips

def adjust_entry(signal, entry, last_close):
    if signal == "BUY" and entry >= last_close:
        entry = last_close - 0.01
    elif signal == "SELL" and entry <= last_close:
        entry = last_close + 0.01
    return round(entry, 2)

def format_status(score):
    if score >= 3:
        return "GOLDEN MOMENT 🌟"
    elif score == 2:
        return "MODERATE ⚠️"
    else:
        return "LEMAH ⚠️ Harap berhati-hati saat entry dan gunakan manajemen risiko"

def is_weekend(now):
    return now.weekday() in [5, 6]

# === KIRIM SINYAL ===
async def send_signal(context):
    global signals_buffer
    application = context.application
    now = datetime.now(timezone.utc) + timedelta(hours=7)

    if is_weekend(now) or (now.weekday() == 0 and now.time() < time(8, 0)):
        return

    candles = fetch_twelvedata("XAU/USD", "5min", 9)
    if candles is None or len(candles) < 9:
        await application.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD (kurang dari 9 candle)")
        return

    df = prepare_df(candles)
    df_analyze = df.iloc[0:8]
    result, score, support, resistance = generate_signal(df_analyze)

    if result:
        signal, entry, rsi, atr, ma, ema = result
        last_close = df_analyze["close"].iloc[-1]
        entry = adjust_entry(signal, entry, last_close)
        tp1, tp2, sl, tp1_pips, tp2_pips, sl_pips = calculate_tp_sl(signal, entry, score)
        status_text = format_status(score)
        entry_note = "Entry di bawah harga sinyal" if signal == "BUY" else "Entry di atas harga sinyal"
        msg = (
            f"🚨 *Sinyal {signal}* {'⬆️' if signal=='BUY' else '⬇️'} _XAU/USD_ @ {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📊 Status: {status_text}\n"
            f"⏳ RSI: {rsi:.2f}, ATR: {atr:.2f}\n"
            f"⚖️ Support: {support:.2f}, Resistance: {resistance:.2f}\n"
            f"💰 Entry: {entry:.2f} ({entry_note})\n"
            f"🎯 TP1: {tp1:.2f} (+{tp1_pips} pips), TP2: {tp2:.2f} (+{tp2_pips} pips)\n"
            f"🛑 SL: {sl:.2f} (-{sl_pips} pips)\n"
            f"⏳ *Eksekusi sinyal dilakukan pada candle berikutnya (candle ke-9)*"
        )
        await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
        signals_buffer.append(signal)
    else:
        await application.bot.send_message(chat_id=CHAT_ID, text="❌ Tidak ada sinyal valid saat ini.")

# === REKAP HARIAN ===
async def daily_recap(context: CallbackContext):
    application = context.application
    now = datetime.now(timezone.utc) + timedelta(hours=7)
    candles = fetch_twelvedata("XAU/USD", "5min", 5)
    if not candles:
        await application.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data untuk rekap harian.")
        return

    df = prepare_df(candles)
    tp_total = sum(20 for i in df.itertuples() if i.close > i.open)
    sl_total = sum(10 for i in df.itertuples() if i.close <= i.open)
    hari = now.strftime("%A")

    if now.weekday() == 4:
        msg = (
            f"📊 *Rekap 5 Candle Terakhir Hari {hari}*\n"
            f"🎯 Total TP: {tp_total} pips\n"
            f"🛑 Total SL: {sl_total} pips\n\n"
            f"🎉 Bot mau healing dulu ke Swiss 🏔️ bawa koper emas 😎.\n"
            f"📴 Market tutup. Kita lanjut lagi hari Senin jam 08:00 WIB ya!\n"
            f"🍹 Happy Weekend!"
        )
    else:
        msg = (
            f"📊 *Rekap 5 Candle Terakhir Hari {hari}*\n"
            f"🎯 Total TP: {tp_total} pips\n"
            f"🛑 Total SL: {sl_total} pips\n\n"
            f"📌 Bot masih siaga, sinyal berikutnya akan dikirim seperti biasa setiap 45 menit ⏱️"
        )
    await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')

# === COMMAND PRICE ===
async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = fetch_twelvedata("XAU/USD", "1min", 1)
    if data:
        price = data[0]["close"]
        time_now = data[0]["datetime"].strftime('%Y-%m-%d %H:%M:%S')
        await update.message.reply_text(f"💱 *Harga Realtime XAU/USD*\n🕒 {time_now}\n💰 {price:.2f}", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Gagal mengambil harga XAU/USD saat ini.")

# === MAIN ===
if __name__ == "__main__":
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("price", price))

    # Jadwal sinyal tiap 45 menit
    application.job_queue.run_repeating(send_signal, interval=2700, first=10)

    # Jadwal rekap harian jam 22:00 WIB
    jakarta = pytz.timezone("Asia/Jakarta")
    jam_22 = time(22, 0, tzinfo=jakarta)
    application.job_queue.run_daily(daily_recap, time=jam_22)

    application.run_polling()
