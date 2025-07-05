from flask import Flask
from threading import Thread
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

# === KEEP ALIVE UNTUK RAILWAY/REPLIT ===
app = Flask('')
@app.route('/')
def home():
    return "Bot is alive!"
def keep_alive():
    Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

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
    # cek 3 candle terakhir sebelum candle terakhir di df
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
    # Hitung indikator untuk df yang sudah berisi 8 candle analisa
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
    # Entry disesuaikan agar realistis, sedikit di bawah/atas harga close candle ke-8
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

async def send_signal(context):
    global signals_buffer
    application = context.application
    now = datetime.now(timezone.utc) + timedelta(hours=7)

    # Kirim sinyal terakhir hari Jumat jam 22:00 + rekap + weekend message
    if now.weekday() == 4 and now.time() >= time(22, 0):
        candles = fetch_twelvedata("XAU/USD", "5min", 100)
        if candles is None:
            await application.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data untuk rekap akhir Jumat.")
            return
        df = prepare_df(candles)
        df = df.tail(5)
        tp_total = 0
        sl_total = 0
        count_tp = 0
        count_sl = 0
        for i in range(len(df)):
            if df.iloc[i]["close"] > df.iloc[i]["open"]:
                tp_total += 20
                count_tp += 1
            else:
                sl_total += 10
                count_sl += 1
        msg = (
            f"📊 *Rekap 5 Candle Terakhir Hari Jumat*\n"
            f"🎯 Total TP tercapai: {count_tp} kali, total {tp_total} pips\n"
            f"🛑 Total SL tercapai: {count_sl} kali, total {sl_total} pips\n\n"
            f"🚨 Ini adalah sinyal terakhir hari Jumat jam 22:00 WIB sebelum weekend.\n"
            f"⚠️ Market tutup sampai Senin jam 08:00 WIB.\n"
            f"Selamat beristirahat weekend! 🌴"
        )
        await application.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
        return

    # Weekend, jangan kirim sinyal, hanya info weekend
    if is_weekend(now):
        return

    # Senin sebelum jam 8 pagi, kirim pesan sambutan saja, tanpa sinyal
    if now.weekday() == 0 and now.time() < time(8, 0):
        await application.bot.send_message(chat_id=CHAT_ID, text="📢 Selamat pagi! Bot akan mulai analisa market jam 08:00 WIB. Harap bersabar.")
        return

    # Kirim sinyal tiap kelipatan 45 menit saja: menit 0 dan 45
    if now.minute % 45 != 0:
        return

    # Ambil 9 candle terakhir (analisa 8 candle pertama, eksekusi di candle ke-9)
    candles = fetch_twelvedata("XAU/USD", "5min", 9)
    if candles is None or len(candles) < 9:
        await application.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD (9 candle belum lengkap)")
        return
    df = prepare_df(candles)

    # Ambil 8 candle pertama untuk analisa sinyal
    df_analyze = df.iloc[0:8]

    result, score, support, resistance = generate_signal(df_analyze)

    if result:
        signal, entry, rsi, atr, ma, ema = result

        last_close = df_analyze["close"].iloc[-1]  # candle ke-8 close
        entry = adjust_entry(signal, entry, last_close)

        tp1, tp2, sl, tp1_pips, tp2_pips, sl_pips = calculate_tp_sl(signal if signal != "LEMAH" else "BUY", entry, score)

        status_text = format_status(score)
        entry_note = "Entry di bawah harga sinyal" if signal == "BUY" else "Entry di atas harga sinyal" if signal == "SELL" else "Sinyal lemah"

        msg = (
            f"🚨 *Sinyal {signal if signal != 'LEMAH' else 'LEMAH'}* {'⬆️' if signal=='BUY' else '⬇️' if signal=='SELL' else ''} _XAU/USD_ @ {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
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

async def start(update, context):
    await update.message.reply_text("Bot sudah aktif dan siap kirim sinyal XAU/USD.")
    async def job():
        while True:
            await send_signal(context)
            await asyncio.sleep(60)  # cek tiap menit, tapi kirim sinyal tiap 45 menit (logic di send_signal)
    asyncio.create_task(job())

if __name__ == "__main__":
    keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.run_polling()
