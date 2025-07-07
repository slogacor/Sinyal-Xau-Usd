from flask import Flask
from threading import Thread
import requests
from datetime import datetime
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
    # Upayakan minimal TP 30 pips dan SL 20 pips (XAU/USD biasanya 2 desimal, jadi sesuaikan)
    # Satu pip XAU/USD dianggap 0.01 (ini bisa disesuaikan)
    pip_value = 0.01
    min_tp_pips = 30
    min_sl_pips = 20

    # Hitung TP/SL dari ATR dan score
    if signal == "BUY":
        tp1 = max(round(price + (atr * (1 + score / 2)), 2), round(price + min_tp_pips * pip_value, 2))
        tp2 = max(round(price + (atr * (1.5 + score / 2)), 2), round(price + (min_tp_pips + 10) * pip_value, 2))
        sl = min(round(price - (atr * 0.8), 2), round(price - min_sl_pips * pip_value, 2))
    else:
        tp1 = min(round(price - (atr * (1 + score / 2)), 2), round(price - min_tp_pips * pip_value, 2))
        tp2 = min(round(price - (atr * (1.5 + score / 2)), 2), round(price - (min_tp_pips + 10) * pip_value, 2))
        sl = max(round(price + (atr * 0.8), 2), round(price + min_sl_pips * pip_value, 2))
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

    # Peringatan entry sesuai harga threshold yang ditentukan (contoh):
    # Jika close candle terakhir >= 3305 -> BUY
    # Jika close candle terakhir <= 3300 -> SELL
    # Jika harga belum tembus level tersebut, beri info tunggu dulu
    entry_buy_level = 3305
    entry_sell_level = 3300

    # TP/SL dihitung berdasarkan analisa + minimal pips
    tp1, tp2, sl = calculate_tp_sl(signal, price, score, last["atr"])
    time_now = datetime.now(pytz.timezone("Asia/Jakarta")).strftime("%H:%M:%S")

    if price >= entry_buy_level:
        signal_final = "BUY"
        entry_note = f"🟢 Harga sudah tembus level BUY di `{entry_buy_level}`.\nSilakan entry BUY sekarang."
    elif price <= entry_sell_level:
        signal_final = "SELL"
        entry_note = f"🔴 Harga sudah tembus level SELL di `{entry_sell_level}`.\nSilakan entry SELL sekarang."
    else:
        signal_final = None
        entry_note = (f"⚠️ Harga belum tembus level entry yang ditentukan.\n"
                      f"• BUY jika harga tembus `{entry_buy_level}`\n"
                      f"• SELL jika harga turun ke `{entry_sell_level}`\n"
                      "Mohon tunggu sampai harga mencapai level tersebut.")

    if signal_final:
        alert = ""
        if score < 3:
            alert = "\n⚠️ *Hati-hati*, sinyal tidak terlalu kuat.\n"

        msg = (
            f"📡 *Sinyal XAU/USD*\n"
            f"🕒 Waktu: {time_now} WIB\n"
            f"📈 Arah: *{signal_final}*\n"
            f"💰 Harga entry: `{price}`\n"
            f"🎯 TP1: `{tp1}` | TP2: `{tp2}`\n"
            f"🛑 SL: `{sl}`\n"
            f"{alert}"
            f"📊 Status: {format_status(score)}\n"
            f"🔍 Analisa:\n{note}\n"
            f"{entry_note}"
        )

        global last_signal_price
        last_signal_price = price
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
        signals_buffer.append({"signal": signal_final, "price": price, "tp1": tp1, "tp2": tp2, "sl": sl})
    else:
        # Kirim pesan tunggu harga tembus level entry
        await context.bot.send_message(chat_id=CHAT_ID, text=entry_note)

# === REKAP HARIAN ===
async def rekap_harian(context):
    jakarta = pytz.timezone("Asia/Jakarta")
    now = datetime.now(jakarta)

    candles = fetch_twelvedata("XAU/USD", "5min", 60)
    if candles is None:
        await context.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data untuk rekap harian.")
        return

    df = prepare_df(candles).tail(60)
    # Simplified calculation, contoh saja
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

    await update.message.reply_text("✅ Bot aktif. Sinyal akan dikirim setiap 2 jam sekali.")

    async def sinyal_job():
        while True:
            await context.bot.send_message(chat_id=CHAT_ID, text="📣 *Ready signal 5 menit lagi!* Bersiap entry.")
            await asyncio.sleep(5 * 60)  # 5 menit tunggu sebelum sinyal
            await send_signal(context)
            await asyncio.sleep(2 * 60 * 60 - 5 * 60)  # Delay total 2 jam - 5 menit

    async def jadwal_rekap():
        while True:
            jakarta = pytz.timezone("Asia/Jakarta")
            now = datetime.now(jakarta)

            # Rekap harian Senin - Jumat jam 21:59 WIB
            if now.weekday() < 5 and now.hour == 21 and now.minute == 59:
                await rekap_harian(context)

            # Market close: Jumat 22:00 WIB
            if now.weekday() == 4 and now.hour == 22 and now.minute == 0:
                await context.bot.send_message(chat_id=CHAT_ID, text=
                    "📴 *Market Close*\n"
                    "Hari ini Jumat pukul 22:00 WIB, pasar forex telah tutup.\n"
                    "🔕 Bot berhenti mengirim sinyal akhir pekan.\n"
                    "📅 Bot aktif kembali Senin pukul 09:00 WIB."
                )
                await asyncio.sleep(60 * 60 * 24 * 2)

            # Market open: Senin 09:00 WIB
            if now.weekday() == 0 and now.hour == 9 and now.minute == 0:
                await context.bot.send_message(chat_id=CHAT_ID, text=
                    "✅ *Bot Aktif Kembali*\n"
                    "Hari ini Senin, pasar telah dibuka kembali.\n"
                    "🤖 Bot siap mengirim sinyal setiap 2 jam.\n"
                    "Selamat trading!"
                )

            await asyncio.sleep(60)  # cek tiap menit

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
    app_bot = ApplicationBuilder
