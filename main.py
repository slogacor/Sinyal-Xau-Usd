import requests
import logging
from datetime import datetime, timedelta
import asyncio
from telegram.ext import ApplicationBuilder, CommandHandler

API_KEY = "JYNPbotCjnfRMKS151Ng3eEIzI7lfw4i"
BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"  # bisa chat id atau username channel

def utc_to_wib(utc_time_str):
    dt_utc = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S")
    dt_wib = dt_utc + timedelta(hours=7)
    return dt_wib

def get_xau_data():
    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=15min&apikey={API_KEY}&format=json&outputsize=100"
    res = requests.get(url).json()
    if "values" not in res:
        logging.error("Gagal ambil data")
        return None
    candles = res["values"]
    # Return candles terbaru (index 0) setelah convert ke WIB
    latest = candles[0]
    latest["wib_time"] = utc_to_wib(latest["datetime"])
    return latest

async def send_signal(application):
    data = get_xau_data()
    if not data:
        await application.bot.send_message(chat_id=CHAT_ID, text="Gagal ambil data XAU/USD")
        return
    # TODO: Analisa data => buat logika sinyal buy/sell, tp/sl dll
    signal = f"Sinyal XAU/USD @ {data['wib_time']}\nOpen: {data['open']}\nClose: {data['close']}\nAnalisa: Buy/Sell (contoh)"
    await application.bot.send_message(chat_id=CHAT_ID, text=signal)

async def start(update, context):
    await update.message.reply_text("Bot sinyal XAU/USD aktif!")

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))

    # run send_signal setiap 45 menit, mulai setelah 10 detik
    application.job_queue.run_repeating(send_signal, interval=45*60, first=10)

    print("Bot running...")
    await application.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    import asyncio
    nest_asyncio.apply()
    asyncio.run(main())
