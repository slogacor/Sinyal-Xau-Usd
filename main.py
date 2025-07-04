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
from datetime import datetime, timedelta, time, timezone
import asyncio
import pandas as pd
import ta
from telegram.ext import ApplicationBuilder, CommandHandler

# --- KONFIGURASI ---
BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"
API_KEY = "841e95162faf457e8d80207a75c3ca2c"
signals_buffer = []

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
    return recent["low"].min(), recent["high"].max()

def confirm_trend_from_last_3(df):
    candles = df.tail(4)
    c1, c2, c3 = candles.iloc[-4:-1].to_dict('records')
    uptrend = all(c["close"] > c["open"] for c in [c1, c2, c3])
    downtrend = all(c["close"] < c["open"] for c in [c1, c2, c3])
    return "BUY" if uptrend else "SELL" if downtrend else None

def generate_signal(df):
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    df["ma"] = ta.trend.SMAIndicator(df["close"], window=50).sma_indicator()
    df["ema"] = ta.trend.EMAIndicator(df["close"], window=20).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()

    support, resistance = find_snr(df)
    last_candle = df.iloc[-1]
    rsi_now = last_candle["rsi"]
    ma = last_candle["ma"]
    ema = last_candle["ema"]
    atr = last_candle["atr"]

    trend = confirm_trend_from_last_3(df)
    if not trend:
        # Walau tanpa trend, tetap keluarkan sinyal "lemah" dengan entry konservatif
        return ("NONE", last_candle, support, resistance, rsi_now, atr, ma, ema), 0

    score = 0
    if atr > 0.2:
        score += 1
    if trend == "BUY" and last_candle["close"] > ma and last_candle["close"] > ema and rsi_now < 70:
        score += 2
    elif trend == "SELL" and last_candle["close"] < ma and last_candle["close"] < ema and rsi_now > 30:
        score += 2

    return (trend, last_candle, support, resistance, rsi_now, atr, ma, ema), score

async def send_signal(context):
    global signals_buffer
    application = context.application
    try:
        candles = fetch_twelvedata("XAU/USD", "5min", 100)
        if candles is None:
            await application.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD")
            return

        df = prepare_df(candles)
        df = df[:-1]  # candle sudah close
        result, score = generate_signal(df)
        wib_time = datetime.now(timezone.utc) + timedelta(hours=7)

        signal, last_candle, support, resistance, rsi, atr, ma, ema = result

        offset = atr * 0.1  # offset kecil agar entry realistis

        if signal == "BUY":
            entry = round(last_candle["low"] - offset, 2)
            tp1 = round(entry + 0.40, 2)
            tp2 = round(entry + 0.55, 2)
            sl = round(entry - 0.25, 2)
            entry_note = "Entry di bawah harga sinyal"
        elif signal == "SELL":
            entry = round(last_candle["high"] + offset, 2)
            tp1 = round(entry - 0.40, 2)
            tp2 = round(entry - 0.55, 2)
            sl = round(entry + 0.25, 2)
            entry_note = "Entry di atas harga sinyal"
        else:
            # No valid trend, entry konservatif di tengah range candle terakhir
            entry = round((last_candle["high"] + last_candle["low"]) / 2, 2)
            tp1 = round(entry + 0.15, 2)
            tp2 = round(entry + 0.25, 2)
            sl = round(entry - 0.10, 2)
            entry_note = "Sinyal lemah, entry konservatif"

        if score >= 3:
            strength = "GOLDEN MOMENT 🌟"
        elif score == 2:
            strength = "SEDANG ⚠️"
        else:
            strength = "LEMAH ⚠️ Harap berhati-hati saat entry dan gunakan manajemen risiko"

        pips_tp1 = int(abs(tp1 - entry) * 100)
        pips_tp2 = int(abs(tp2 - entry) * 100)
        pips_sl = int(abs(sl - entry) * 100)

        msg = (
            f"🚨 Sinyal {signal if signal in ['BUY','SELL'] else 'LEMAH'} ⬆️ XAU/USD @ {wib_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📊 Status: {strength}\n"
            f"📈 Entry: {entry} ({entry_note})\n"
            f"🎯 TP1: {tp1} (+{pips_tp1} pips)\n"
            f"🎯 TP2: {tp2} (+{pips_tp2} pips)\n"
            f"🛑 SL: {sl} (-{pips_sl} pips)\n"
            f"📊 RSI: {rsi:.2f}, ATR: {atr:.2f}\n"
            f"MA50: {ma:.2f}, EMA20: {ema:.2f}\n"
            f"Support: {support:.2f}, Resistance: {resistance:.2f}"
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

async def main():
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))

    # Sinyal keluar tiap 45 menit (2700 detik)
    application.job_queue.run_repeating(send_signal, interval=2700, first=1)
    # Rekap harian jam 13:00 WIB
    application.job_queue.run_daily(daily_recap, time=time(hour=13, minute=0))

    print("Bot running...")
    await application.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
