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
    last_close = df["close"].iloc[-1]
    rsi_now = df["rsi"].iloc[-1]
    ma = df["ma"].iloc[-1]
    ema = df["ema"].iloc[-1]
    atr = df["atr"].iloc[-1]

    trend = confirm_trend_from_last_3(df)
    if not trend:
        return None, 0

    score = 0
    if atr > 0.2:
        score += 1
    if trend == "BUY" and last_close > ma and last_close > ema and rsi_now < 70:
        score += 2
    elif trend == "SELL" and last_close < ma and last_close < ema and rsi_now > 30:
        score += 2

    if score >= 3:
        return (trend, last_close, support, resistance, rsi_now, atr, ma, ema), score
    elif score == 2:
        return (trend, last_close, support, resistance, rsi_now, atr, ma, ema), score
    else:
        # tetap kembalikan signal tapi dengan score rendah untuk jaga sinyal tetap keluar
        return (trend, last_close, support, resistance, rsi_now, atr, ma, ema), score

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

        support, resistance = None, None
        if result:
            signal, entry, support, resistance, rsi, atr, ma, ema = result

            pips_to_price = 0.01  # 1 pip = 0.01 pada XAU/USD
            tp1_pips = 30
            tp2_pips = 40
            sl_pips = 20

            if signal == "BUY":
                tp1 = round(entry + tp1_pips * pips_to_price, 2)
                tp2 = round(entry + tp2_pips * pips_to_price, 2)
                sl = round(entry - sl_pips * pips_to_price, 2)
                # Saran entry: entry di bawah harga sinyal
                entry_saran = f"Entry di bawah harga sinyal (realtime): {entry:.2f}"
            else:  # SELL
                tp1 = round(entry - tp1_pips * pips_to_price, 2)
                tp2 = round(entry - tp2_pips * pips_to_price, 2)
                sl = round(entry + sl_pips * pips_to_price, 2)
                # Saran entry: entry di atas harga sinyal
                entry_saran = f"Entry di atas harga sinyal (realtime): {entry:.2f}"

            # Kategori sinyal berdasar score
            if score >= 3:
                status = "GOLDEN MOMENT 🌟"
            elif score == 2:
                status = "SEDANG ⚠️"
            else:
                status = "LEMAH ⚠️ Harap berhati-hati saat entry dan gunakan manajemen risiko"

            msg = (
                f"🚨 Sinyal {signal} {'⬆️' if signal=='BUY' else '⬇️'} XAU/USD @ {wib_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"📊 Status: {status}\n"
                f"📈 {entry_saran}\n"
                f"🎯 TP1: {tp1} (+{tp1_pips} pips)\n"
                f"🎯 TP2: {tp2} (+{tp2_pips} pips)\n"
                f"🛑 SL: {sl} (-{sl_pips} pips)\n"
                f"📊 RSI: {rsi:.2f}, ATR: {atr:.2f}\n"
                f"MA50: {ma:.2f}, EMA20: {ema:.2f}\n"
                f"Support: {support:.2f}, Resistance: {resistance:.2f}"
            )
        else:
            # Jangan pernah kirim sinyal gagal, buat sinyal lemah paksa
            msg = (
                f"⚠️ Sinyal LEMAH ⚠️ Kondisi pasar kurang jelas, gunakan manajemen risiko\n"
                f"📅 Waktu: {wib_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Support: {support:.2f if support else 0:.2f}, Resistance: {resistance:.2f if resistance else 0:.2f}\n"
                f"💡 Harap evaluasi kondisi pasar dengan hati-hati."
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

async def weekend_notice(context):
    application = context.application
    wib_time = datetime.now(timezone.utc) + timedelta(hours=7)
    weekday = wib_time.weekday()
    hour = wib_time.hour

    # Jumat jam 24:00 (tepatnya Sabtu 00:00 WIB)
    if weekday == 4 and hour == 23:
        await application.bot.send_message(chat_id=CHAT_ID, text=(
            "📢 Pasar XAU/USD akan tutup pada Jumat malam pukul 24:00 WIB.\n"
            "Selamat beristirahat, sampai jumpa di Senin dini hari!"
        ))

    # Senin jam 01:00 WIB
    if weekday == 0 and hour == 1:
        await application.bot.send_message(chat_id=CHAT_ID, text=(
            "🚀 Selamat bekerja, mari kita siap membantai market XAU/USD di Senin dini hari!"
        ))

async def start(update, context):
    await update.message.reply_text("✅ Bot sinyal scalping XAU/USD aktif. Sinyal keluar tiap 45 menit.")

async def main():
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))

    application.job_queue.run_repeating(send_signal, interval=2700, first=1)  # setiap 45 menit
    application.job_queue.run_daily(daily_recap, time=time(hour=13, minute=0))
    application.job_queue.run_repeating(weekend_notice, interval=3600, first=1)  # cek tiap jam

    print("Bot running...")
    await application.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
