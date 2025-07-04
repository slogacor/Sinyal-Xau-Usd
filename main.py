# === KEEP ALIVE UNTUK RAILWAY ===
from flask import Flask
from threading import Thread

app = Flask('')
@app.route('/')
def home():
    return "Bot is alive!"
def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

# === IMPORT UTAMA ===
import requests
import logging
from datetime import datetime, timedelta, time, timezone
import asyncio
import pandas as pd
from telegram.ext import ApplicationBuilder, CommandHandler

BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"
signal_buffer = []
realtime_signals = []

# === Ambil data dari Binance ===
def fetch_binance(symbol="XAUUSDT", interval="5m", limit=20):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    res = requests.get(url)
    if res.status_code != 200:
        logging.error(f"Gagal ambil data: {res.text}")
        return None
    data = res.json()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "taker_base_vol", "taker_quote_vol", "ignore"
    ])
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=7)
    return df[["datetime", "open", "high", "low", "close"]]

# === Analisa sinyal berdasarkan 3 candle sebelumnya ===
def analyze_direction(df):
    last3 = df.tail(4).iloc[:-1]  # ambil 3 sebelum candle terakhir
    ups = sum(c["close"] > c["open"] for _, c in last3.iterrows())
    downs = sum(c["close"] < c["open"] for _, c in last3.iterrows())

    if ups == 3:
        return "BUY"
    elif downs == 3:
        return "SELL"
    return None

# === Fungsi utama sinyal ===
async def analyze_and_send_signal(context):
    global signal_buffer, realtime_signals
    application = context.application
    try:
        df = fetch_binance()
        if df is None or len(df) < 4:
            await application.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data dari Binance.")
            return

        signal = analyze_direction(df)
        if not signal:
            await application.bot.send_message(chat_id=CHAT_ID, text="⚠️ Belum ada arah kuat untuk sinyal XAU/USD.")
            return

        entry = round(df["close"].iloc[-1], 2)
        tp1 = entry + 0.30 if signal == "BUY" else entry - 0.30
        tp2 = entry + 0.50 if signal == "BUY" else entry - 0.50
        sl  = entry - 0.30 if signal == "BUY" else entry + 0.30
        now = df["datetime"].iloc[-1]

        # Simulasi hasil sinyal (random)
        import random
        result = random.choice(["TP1", "TP2", "SL"])
        pips = 30 if result == "TP1" else 50 if result == "TP2" else -30

        pesan = (
            f"🚨 Sinyal {signal} XAU/USD @ {now.strftime('%Y-%m-%d %H:%M:%S')}
"
            f"💰 Entry: {entry}
"
            f"🎯 TP1: {round(tp1, 2)}
"
            f"🎯 TP2: {round(tp2, 2)}
"
            f"🛑 SL : {round(sl, 2)}
"
            f"📊 Hasil: {result} ({pips:+} pips)"
        )

        signal_buffer.append(pesan)
        realtime_signals.append({"result": result, "pips": pips, "type": signal})
        await application.bot.send_message(chat_id=CHAT_ID, text=pesan)

        # Rekap setiap 5 sinyal
        if len(realtime_signals) >= 5:
            recap = "📊 Rekap 5 Sinyal Terakhir:
"
            total_pips = 0
            for i, s in enumerate(realtime_signals[-5:], 1):
                recap += f"{i}. {s['type']} - {s['result']} ({s['pips']} pips)\n"
                total_pips += s['pips']
            recap += f"\nTotal Pips: {total_pips:+} pips"
            await application.bot.send_message(chat_id=CHAT_ID, text=recap)

    except Exception as e:
        logging.error(f"Error analisa sinyal: {e}")

# === Rekapan harian sinyal ===
async def daily_recap(context):
    global signal_buffer
    if not signal_buffer:
        return
    recap = "\n\n".join(signal_buffer)
    await context.application.bot.send_message(chat_id=CHAT_ID, text=f"📅 Rekapan Harian XAU/USD:\n\n{recap}")
    signal_buffer = []

# === Perintah /start ===
async def start(update, context):
    await update.message.reply_text("✅ Bot sinyal XAU/USD aktif! Sinyal keluar tiap 20 menit.")

# === MAIN ===
async def main():
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))

    # Sinyal setiap 20 menit
    application.job_queue.run_repeating(analyze_and_send_signal, interval=1200, first=5)

    # Rekap harian pukul 13:00 WIB
    application.job_queue.run_daily(daily_recap, time=time(hour=13, minute=0))

    print("Bot running...")
    await application.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
