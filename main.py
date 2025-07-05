from flask import Flask
from threading import Thread
import requests
import logging
from datetime import datetime, timedelta, time, timezone
import asyncio
import pandas as pd
import ta
from telegram.ext import ApplicationBuilder, CommandHandler

# === KEEP ALIVE UNTUK RAILWAY/REPLIT ===
app = Flask('')
@app.route('/')
def home():
    return "Bot is alive!"
def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

# --- KONFIGURASI ---
BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"
API_KEY = "841e95162faf457e8d80207a75c3ca2c"
signals_buffer = []

# Fungsi ambil data
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
        # Jika tidak ada tren, tetap berikan sinyal LEMAH berdasarkan ATR > 0.2
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
    if score >= 3:  # GOLDEN MOMENT
        tp1_pips = 30
        tp2_pips = 55
        sl_pips = 20
    elif score == 2:  # MODERATE
        tp1_pips = 25
        tp2_pips = 40
        sl_pips = 20
    else:  # LEMAH
        tp1_pips = 15
        tp2_pips = 25
        sl_pips = 15

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
    return now.weekday() in [5,6]

async def send_signal(context):
    global signals_buffer
    application = context.application
    now = datetime.now(timezone.utc) + timedelta(hours=7)

    if is_weekend(now):
        msg = (
            f"📢 Market tutup hari ini ({now.strftime('%A')}).\n"
            "Sebaiknya istirahat dan siapkan strategi untuk pekan depan."
        )
        await application.bot.send_message(chat_id=CHAT_ID, text=msg)
        return

    try:
        candles = fetch_twelvedata("XAU/USD", "5min", 100)
        if candles is None:
            await application.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD")
            return

        df = prepare_df(candles)
        df = df[:-1]
        result, score, support, resistance = generate_signal(df)

        if result:
            signal, entry, rsi, atr, ma, ema = result
            entry = adjust_entry(signal, entry, df["close"].iloc[-1])
            tp1, tp2, sl, tp1_pips, tp2_pips, sl_pips = calculate_tp_sl(signal if signal != "LEMAH" else "BUY", entry, score)

            status_text = format_status(score)
            entry_note = "Entry di bawah harga sinyal" if signal == "BUY" else "Entry di atas harga sinyal" if signal == "SELL" else "Sinyal lemah"
            msg = (
                f"🚨 Sinyal {signal if signal != 'LEMAH' else 'LEMAH'} {'⬆️' if signal=='BUY' else '⬇️' if signal=='SELL' else ''} XAU/USD @ {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"📊 Status: {status_text}\n"
                f"📈 Entry: {entry:.2f} ({entry_note})\n"
                f"🎯 TP1: {tp1:.2f} (+{tp1_pips} pips)\n"
                f"🎯 TP2: {tp2:.2f} (+{tp2_pips} pips)\n"
                f"🛑 SL: {sl:.2f} (-{sl_pips} pips)\n"
                f"📊 RSI: {rsi:.2f}, ATR: {atr:.2f}\n"
                f"MA50: {ma:.2f}, EMA20: {ema:.2f}\n"
                f"Support: {support:.2f}, Resistance: {resistance:.2f}"
            )
        else:
            msg = (
                f"⚠️ Tidak ada sinyal kuat atau lemah hari ini.\n"
                f"📅 Waktu: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Support: {support:.2f}\n"
                f"Resistance: {resistance:.2f}\n"
                f"💡 Harap evaluasi kondisi pasar dan gunakan manajemen risiko yang baik."
            )

        signals_buffer.append(msg)
        await application.bot.send_message(chat_id=CHAT_ID, text=msg)

        if len(signals_buffer) >= 5:
            recap = "📊 Rekap 5 sinyal terakhir:\n\n" + "\n\n".join(signals_buffer[-5:])
            await application.bot.send_message(chat_id=CHAT_ID, text=recap)

    except Exception as e:
        logging.error(f"Error analisa sinyal: {e}")
        await application.bot.send_message(chat_id=CHAT_ID, text=f"⚠️ Error: {e}")

async def daily_recap(context):
    global signals_buffer
    application = context.application
    if signals_buffer:
        recap = "\n\n".join(signals_buffer)
        await application.bot.send_message(chat_id=CHAT_ID, text=f"📅 Rekapan Harian XAU/USD:\n\n{recap}")
        signals_buffer.clear()

async def start(update, context):
    await update.message.reply_text("✅ Bot sinyal scalping XAU/USD aktif. Sinyal keluar tiap 45 menit.")

async def weekend_message(update, context):
    now = datetime.now(timezone.utc) + timedelta(hours=7)
    if is_weekend(now):
        await update.message.reply_text(f"📢 Market tutup hari ini ({now.strftime('%A')}).\nSebaiknya istirahat dan siapkan strategi untuk pekan depan.")
    else:
        await update.message.reply_text("Market buka dan siap memberikan sinyal setiap 45 menit.")

async def weekend_check_and_notify(context):
    now = datetime.now(timezone.utc) + timedelta(hours=7)
    if is_weekend(now):
        msg = f"📢 Market tutup hari ini ({now.strftime('%A')}).\nSebaiknya istirahat dan siapkan strategi untuk pekan depan."
        await context.application.bot.send_message(chat_id=CHAT_ID, text=msg)

async def main():
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("market", weekend_message))

    application.job_queue.run_repeating(send_signal, interval=2700, first=10)
    application.job_queue.run_daily(daily_recap, time=time(hour=23, minute=50))
    application.job_queue.run_daily(weekend_check_and_notify, time=time(hour=9, minute=0))

    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
