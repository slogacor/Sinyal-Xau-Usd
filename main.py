# === KEEP ALIVE UNTUK RAILWAY/REPLIT ===
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

# === BOT UTAMA ===
import requests
import logging
from datetime import datetime, timedelta, time
import asyncio
import pandas as pd
import ta
from telegram.ext import ApplicationBuilder, CommandHandler

API_KEY = "c008ce51cd314c6590a91df41faa22c6"
BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"
signals_buffer = []

def get_candles(symbol="XAU/USD", interval="5min", outputsize=100):
    url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&apikey={API_KEY}&outputsize={outputsize}"
    res = requests.get(url).json()
    if "values" not in res:
        logging.error(f"Gagal ambil data {interval}: {res.get('message', '')}")
        return None
    return res["values"]

def prepare_df(candles):
    df = pd.DataFrame(candles)
    df["close"] = pd.to_numeric(df["close"])
    df["high"] = pd.to_numeric(df["high"])
    df["low"] = pd.to_numeric(df["low"])
    df["open"] = pd.to_numeric(df["open"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    return df

def find_snr(df):
    recent = df.tail(30)
    support = recent["low"].min()
    resistance = recent["high"].max()
    return support, resistance

def confirm_trend_from_last_3(df):
    candles = df.tail(4)
    c1, c2, c3 = candles.iloc[-4:-1].to_dict('records')
    uptrend = all(c["close"] > c["open"] for c in [c1, c2, c3])
    downtrend = all(c["close"] < c["open"] for c in [c1, c2, c3])
    return "BUY" if uptrend else "SELL" if downtrend else None

def generate_signal(df):
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    df["ma"] = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()

    support, resistance = find_snr(df)
    last_close = df["close"].iloc[-1]
    rsi_now = df["rsi"].iloc[-1]
    ma = df["ma"].iloc[-1]
    atr = df["atr"].iloc[-1]

    trend = confirm_trend_from_last_3(df)
    if not trend:
        return None

    if atr < 0.2:
        return None

    if trend == "BUY" and rsi_now < 70 and last_close > ma:
        return "BUY", last_close, support, resistance, rsi_now, atr, ma
    elif trend == "SELL" and rsi_now > 30 and last_close < ma:
        return "SELL", last_close, support, resistance, rsi_now, atr, ma
    return None

async def send_signal(context):
    global signals_buffer
    application = context.application
    try:
        candles = get_candles("XAU/USD", "5min")
        if candles is None:
            await application.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD")
            return

        df = prepare_df(candles)[:-1]  # pastikan candle terakhir sudah close
        result = generate_signal(df)

        wib_time = datetime.utcnow() + timedelta(hours=7)

        if result:
            signal, entry, support, resis, rsi, atr, ma = result
            if signal == "BUY":
                tp1 = round(entry + 0.30, 2)
                tp2 = round(entry + 0.50, 2)
                sl = round(entry - 0.30, 2)
            else:
                tp1 = round(entry - 0.30, 2)
                tp2 = round(entry - 0.50, 2)
                sl = round(entry + 0.30, 2)

            msg = (
                f"Sinyal {signal} XAU/USD ⚡\n"
                f"📈 Entry: {entry:.2f}\n🎯 TP1: {tp1} (+30 pips)\n🎯 TP2: {tp2} (+50 pips)\n"
                f"🛑 SL: {sl} (-30 pips)\n"
                f"RSI: {rsi:.2f}, ATR: {atr:.2f}\nMA50: {ma:.2f}\n"
                f"Support: {support:.2f}, Resistance: {resis:.2f}\n"
                f"🕒 Time: {wib_time.strftime('%Y-%m-%d %H:%M:%S WIB')}"
            )
        else:
            msg = (
                f"⚠️ Tidak ada sinyal valid, namun arah belum jelas.\n"
                f"📊 Rekomendasi: Tunggu momentum.\n"
                f"🕒 Time: {wib_time.strftime('%Y-%m-%d %H:%M:%S WIB')}"
            )

        signals_buffer.append(msg)
        await application.bot.send_message(chat_id=CHAT_ID, text=msg)

    except Exception as e:
        logging.error(f"Error analisa sinyal: {e}")

async def daily_recap(context):
    global signals_buffer
    application = context.application
    if signals_buffer:
        recap = "\n\n".join(signals_buffer)
        await application.bot.send_message(chat_id=CHAT_ID, text=f"📅 Rekapan Harian Sinyal XAU/USD:\n\n{recap}")
        signals_buffer.clear()

async def start(update, context):
    await update.message.reply_text("✅ Bot sinyal scalping XAU/USD aktif (M5, sinyal tiap 20 menit)")

async def main():
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))

    application.job_queue.run_repeating(send_signal, interval=1200, first=1)  # 20 menit
    application.job_queue.run_daily(daily_recap, time=time(hour=13, minute=0))

    print("Bot running...")
    await application.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
