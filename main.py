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

def is_bullish_engulfing(df):
    if len(df) < 2:
        return False
    c1 = df.iloc[-2]
    c2 = df.iloc[-1]
    return (c1["close"] < c1["open"] and
            c2["close"] > c2["open"] and
            c2["close"] > c1["open"] and
            c2["open"] < c1["close"])

def is_bearish_engulfing(df):
    if len(df) < 2:
        return False
    c1 = df.iloc[-2]
    c2 = df.iloc[-1]
    return (c1["close"] > c1["open"] and
            c2["close"] < c2["open"] and
            c2["close"] < c1["open"] and
            c2["open"] > c1["close"])

def is_hammer(candle):
    body = abs(candle["close"] - candle["open"])
    lower_shadow = min(candle["open"], candle["close"]) - candle["low"]
    candle_range = candle["high"] - candle["low"]
    return lower_shadow > 2 * body and body / candle_range < 0.3 if candle_range > 0 else False

def is_inverted_hammer(candle):
    body = abs(candle["close"] - candle["open"])
    upper_shadow = candle["high"] - max(candle["open"], candle["close"])
    candle_range = candle["high"] - candle["low"]
    return upper_shadow > 2 * body and body / candle_range < 0.3 if candle_range > 0 else False

async def analyze_and_send_signal(context):
    global signals_buffer
    application = context.application
    try:
        candles = get_candles("XAU/USD", "5min")
        if candles is None:
            await application.bot.send_message(chat_id=CHAT_ID, text="❌ Gagal ambil data XAU/USD")
            return

        df = prepare_df(candles)[:-1]  # candle terakhir harus yang sudah close
        df["rsi"] = ta.momentum.rsi(df["close"], window=14)

        support, resistance = find_snr(df)
        rsi_now = df["rsi"].iloc[-1]
        last_close = df["close"].iloc[-1]

        bullish = is_bullish_engulfing(df) or is_hammer(df.iloc[-1])
        bearish = is_bearish_engulfing(df) or is_inverted_hammer(df.iloc[-1])

        signal = None
        if rsi_now < 30 and last_close <= support and bullish:
            signal = "BUY"
        elif rsi_now > 70 and last_close >= resistance and bearish:
            signal = "SELL"

        if not signal:
            return

        entry_price = round(last_close, 2)
        if signal == "BUY":
            tp1 = round(entry_price + 0.30, 2)
            tp2 = round(entry_price + 0.50, 2)
            sl = round(entry_price - 0.30, 2)
        else:
            tp1 = round(entry_price - 0.30, 2)
            tp2 = round(entry_price - 0.50, 2)
            sl = round(entry_price + 0.30, 2)

        wib_time = datetime.utcnow() + timedelta(hours=7)
        signal_text = (f"Sinyal {signal} XAU/USD ⚡\n"
                       f"📈 Entry: {entry_price}\n"
                       f"🎯 TP1: {tp1} (+30 pips)\n"
                       f"🎯 TP2: {tp2} (+50 pips)\n"
                       f"🛑 SL: {sl} (-30 pips)\n"
                       f"RSI: {rsi_now:.2f}\n"
                       f"Support: {support:.2f}, Resistance: {resistance:.2f}\n"
                       f"Time (WIB): {wib_time.strftime('%Y-%m-%d %H:%M:%S')}")

        signals_buffer.append(signal_text)

        if len(signals_buffer) >= 5:
            recap = "\n\n".join(signals_buffer)
            await application.bot.send_message(chat_id=CHAT_ID, text=f"📊 Rekapan 5 Sinyal Terbaru:\n\n{recap}")
            signals_buffer.clear()
        else:
            await application.bot.send_message(chat_id=CHAT_ID, text=signal_text)

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
    await update.message.reply_text("✅ Bot sinyal scalping XAU/USD aktif di M5!")

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.job_queue.run_repeating(analyze_and_send_signal, interval=45 * 60, first=10)
    application.job_queue.run_daily(daily_recap, time=time(hour=13, minute=0))

    print("Bot running...")
    await application.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
