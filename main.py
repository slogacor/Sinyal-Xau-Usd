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

    score = 0
    if atr > 0.2:
        score += 1
    if trend == "BUY" and last_close > ma and last_close > ema and rsi_now < 70:
        score += 2
    elif trend == "SELL" and last_close < ma and last_close < ema and rsi_now > 30:
        score += 2

    # Note: kita tetap return trend walau score rendah, supaya sinyal tetap keluar
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

        if result:
            signal, entry, support, resis, rsi, atr, ma, ema = result
            if signal == "BUY":
                tp1 = round(entry + 0.30, 2)  # 30 pips
                tp2 = round(entry + 0.50, 2)  # 50 pips
                sl = round(entry - 0.25, 2)   # 25 pips
            elif signal == "SELL":
                tp1 = round(entry - 0.30, 2)
                tp2 = round(entry - 0.50, 2)
                sl = round(entry + 0.25, 2)
            else:
                # Kalau trend tidak terdeteksi, keluarkan pesan khusus
                msg = (
                    f"⚠️ Sinyal GAGAL dikeluarkan, kondisi market tidak mendukung.\n"
                    f"📅 Waktu: {wib_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Support: {support:.2f}\nResistance: {resis:.2f}\n"
                    f"💡 Harap evaluasi kondisi pasar dan gunakan manajemen risiko yang baik."
                )
                signals_buffer.append(msg)
                await application.bot.send_message(chat_id=CHAT_ID, text=msg)
                return

            # Tentukan kategori akurasi sinyal
            if score >= 3:
                status = "Goldent Moment ✨"
                tp_note = "TP diperlebar karena sinyal kuat"
            elif score == 2:
                status = "Sinyal Sedang ⚠️"
                tp_note = "TP dan SL sedang"
            else:
                status = "⚠️ Sinyal Lemah"
                tp_note = "TP dan SL disesuaikan, hati-hati entry"

            # Saran entry untuk buy/sell
            entry_saran = ("pastikan entry di bawah harga sinyal" if signal == "BUY" else "pastikan entry di atas harga sinyal")

            msg = (
                f"🚨 Sinyal {signal} XAU/USD @ {wib_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"📊 Status: {status}\n"
                f"📈 Entry: {entry:.2f}\n"
                f"🎯 TP1: {tp1} (30-45 pips)\n🎯 TP2: {tp2} (40-60 pips)\n"
                f"🛑 SL: {sl} (15-25 pips)\n"
                f"Support: {support:.2f}, Resistance: {resis:.2f}\n"
                f"📊 RSI: {rsi:.2f}, ATR: {atr:.2f}\n"
                f"MA50: {ma:.2f}, EMA20: {ema:.2f}\n"
                f"💡 {tp_note}\n"
                f"💡 Saran entry: {entry_saran}"
            )
        else:
            # Kondisi no result (tidak mungkin karena kita tetap return trend)
            msg = (
                f"⚠️ Sinyal GAGAL dikeluarkan, kondisi market tidak mendukung.\n"
                f"🕒 {wib_time.strftime('%Y-%m-%d %H:%M:%S')}"
            )

        signals_buffer.append(msg)
        await application.bot.send_message(chat_id=CHAT_ID, text=msg)

        # Kirim rekap setiap 5 sinyal
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

    # Job: kirim sinyal tiap 45 menit (2700 detik)
    application.job_queue.run_repeating(send_signal, interval=2700, first=1)

    # Job: rekap harian jam 13:00 WIB (GMT+7)
    application.job_queue.run_daily(daily_recap, time=time(hour=13, minute=0))

    print("Bot running...")
    await application.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
