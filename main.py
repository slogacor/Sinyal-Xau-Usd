import requests
import logging
from datetime import datetime, timedelta, time
import asyncio
import pandas as pd
import numpy as np
import ta
from telegram.ext import ApplicationBuilder, CommandHandler

API_KEY = "c008ce51cd314c6590a91df41faa22c6"
BOT_TOKEN = "8114552558:AAFpnQEYHYa8P43g5rjOwPs5TSbjtYh9zS4"
CHAT_ID = "-1002883903673"

signals_buffer = []

def get_candles(symbol="XAU/USD", interval="15min", outputsize=100):
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
    df = df.sort_values("datetime")
    return df

def find_snr(df):
    recent = df.tail(30)
    support = recent["low"].min()
    resistance = recent["high"].max()
    return support, resistance

def is_bullish_engulfing(df):
    if len(df) < 2:
        return False
    c1 = df.iloc[-2]
    c2 = df.iloc[-1]
    return (c1["close"] < c1["open"] and
            c2["close"] > c2["open"] and
            c2["close"] > c1["open"] and
            c2["open"] < c1["close"])

def is_hammer(candle):
    body = abs(candle["close"] - candle["open"])
    lower_shadow = (candle["open"] - candle["low"] if candle["close"] > candle["open"]
                    else candle["close"] - candle["low"])
    candle_range = candle["high"] - candle["low"]
    return lower_shadow > 2 * body and body / candle_range < 0.3 if candle_range > 0 else False

def calculate_tp_sl(df_m5, base_tp1=30, base_tp2=50, base_sl=20):
    if len(df_m5) == 0:
        return base_tp1, base_tp2, base_sl
    last_candle = df_m5.iloc[-1]
    range_pips = (last_candle["high"] - last_candle["low"]) * 100  # pip approx
    multiplier = 1 if range_pips < 10 else 1.5
    tp1 = base_tp1 * multiplier
    tp2 = base_tp2 * multiplier
    sl = base_sl * multiplier
    return round(tp1), round(tp2), round(sl)

async def analyze_and_send_signal(application):
    global signals_buffer
    try:
        candles_m15 = get_candles("XAU/USD", "15min")
        candles_m5 = get_candles("XAU/USD", "5min")

        if candles_m15 is None or candles_m5 is None:
            await application.bot.send_message(chat_id=CHAT_ID, text="Gagal ambil data XAU/USD")
            return

        df_m15 = prepare_df(candles_m15)
        df_m5 = prepare_df(candles_m5)

        df_m15["rsi"] = ta.momentum.rsi(df_m15["close"], window=14)
        bb = ta.volatility.BollingerBands(df_m15["close"], window=20, window_dev=2)
        df_m15["bb_high"] = bb.bollinger_hband()
        df_m15["bb_low"] = bb.bollinger_lband()

        support, resistance = find_snr(df_m15)

        bullish_engulfing = is_bullish_engulfing(df_m15)
        hammer = is_hammer(df_m15.iloc[-1])

        rsi_now = df_m15["rsi"].iloc[-1]
        last_close = df_m15["close"].iloc[-1]

        signal = None
        if rsi_now < 30 and last_close <= support and (bullish_engulfing or hammer):
            signal = "BUY"
        elif rsi_now > 70 and last_close >= resistance:
            signal = "SELL"

        if not signal:
            return

        tp1, tp2, sl = calculate_tp_sl(df_m5)

        wib_time = datetime.utcnow() + timedelta(hours=7)
        signal_text = (f"Sinyal {signal} XAU/USD\n"
                       f"TP1: {tp1} pips\nTP2: {tp2} pips\nSL: {sl} pips\n"
                       f"RSI: {rsi_now:.2f}\n"
                       f"Time WIB: {wib_time.strftime('%Y-%m-%d %H:%M:%S')}")

        signals_buffer.append(signal_text)

        if len(signals_buffer) >= 5:
            recap = "\n\n".join(signals_buffer)
            await application.bot.send_message(chat_id=CHAT_ID, text=f"📊 Rekapan 5 Sinyal Terbaru:\n\n{recap}")
            signals_buffer.clear()
        else:
            await application.bot.send_message(chat_id=CHAT_ID, text=signal_text)

    except Exception as e:
        logging.error(f"Error analisa sinyal: {e}")

async def daily_recap(application):
    global signals_buffer
    if signals_buffer:
        recap = "\n\n".join(signals_buffer)
        await application.bot.send_message(chat_id=CHAT_ID, text=f"📅 Rekapan Harian Sinyal XAU/USD:\n\n{recap}")
        signals_buffer.clear()

async def start(update, context):
    await update.message.reply_text("Bot sinyal XAU/USD aktif!")

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))

    # Run analyze_and_send_signal setiap 45 menit mulai 10 detik setelah bot start
    application.job_queue.run_repeating(analyze_and_send_signal, interval=45*60, first=10)

    # Run daily recap jam 8 malam WIB = jam 13 UTC
    application.job_queue.run_daily(daily_recap, time=time(hour=13, minute=0))

    print("Bot running...")
    await application.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
