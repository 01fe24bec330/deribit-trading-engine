import requests
import pandas as pd
import numpy as np
import ta
import time
from datetime import datetime, date

# ==============================
# CONFIG
# ==============================

SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT"
}

START_CAPITAL = 100
RISK_PERCENT = 0.01
MAX_TRADES_PER_DAY = 5
ADX_THRESHOLD = 20

TELEGRAM_TOKEN = "8688486536:AAGkjcujF9xRfB6w-UuiexM7iSg7Scs-GS0"
TELEGRAM_CHAT_ID = "7225721600"

capital = START_CAPITAL
open_positions = {}
trades_today = 0
current_day = date.today()
last_heartbeat = 0


# ==============================
# TELEGRAM
# ==============================

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        })
    except:
        pass


# ==============================
# DATA FETCH
# ==============================

def get_klines(symbol, interval):
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": 250
        }
        response = requests.get(url, params=params)
        data = response.json()

        if not data or isinstance(data, dict):
            return None

        df = pd.DataFrame(data)
        df = df.iloc[:, 0:6]
        df.columns = ["time","open","high","low","close","volume"]
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        return df

    except:
        return None


# ==============================
# STRATEGY
# ==============================

def check_signal(symbol):

    df_4h = get_klines(symbol, "4h")
    df_1h = get_klines(symbol, "1h")
    df_15m = get_klines(symbol, "15m")

    # SAFETY CHECK
    if (
        df_4h is None or df_1h is None or df_15m is None or
        len(df_4h) < 210 or
        len(df_1h) < 210 or
        len(df_15m) < 50
    ):
        return None

    # 4H Trend
    df_4h["ema200"] = ta.trend.ema_indicator(df_4h["close"], 200)

    # 1H Alignment
    df_1h["ema50"] = ta.trend.ema_indicator(df_1h["close"], 50)
    df_1h["ema200"] = ta.trend.ema_indicator(df_1h["close"], 200)

    # 15M Entry
    df_15m["ema20"] = ta.trend.ema_indicator(df_15m["close"], 20)
    df_15m["rsi"] = ta.momentum.rsi(df_15m["close"], 14)
    df_15m["adx"] = ta.trend.adx(
        df_15m["high"],
        df_15m["low"],
        df_15m["close"],
        14
    )
    df_15m["atr"] = ta.volatility.average_true_range(
        df_15m["high"],
        df_15m["low"],
        df_15m["close"],
        14
    )

    last_4h = df_4h.iloc[-1]
    last_1h = df_1h.iloc[-1]
    last_15m = df_15m.iloc[-1]

    # LONG CONDITIONS
    if (
        last_4h["close"] > last_4h["ema200"] and
        last_1h["ema50"] > last_1h["ema200"] and
        last_15m["rsi"] < 60 and
        last_15m["adx"] > ADX_THRESHOLD
    ):
        return ("LONG", last_15m["close"], last_15m["atr"])

    # SHORT CONDITIONS
    if (
        last_4h["close"] < last_4h["ema200"] and
        last_1h["ema50"] < last_1h["ema200"] and
        last_15m["rsi"] > 40 and
        last_15m["adx"] > ADX_THRESHOLD
    ):
        return ("SHORT", last_15m["close"], last_15m["atr"])

    return None


# ==============================
# TRADE MANAGEMENT
# ==============================

def open_trade(coin, direction, entry, atr):
    global capital, trades_today

    risk_amount = capital * RISK_PERCENT
    stop_distance = atr * 1.5
    size = risk_amount / stop_distance

    if direction == "LONG":
        stop = entry - stop_distance
        target = entry + stop_distance * 2
    else:
        stop = entry + stop_distance
        target = entry - stop_distance * 2

    open_positions[coin] = {
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "size": size
    }

    trades_today += 1

    send_telegram(
        f"📈 {coin} {direction}\n"
        f"Entry: {entry}\n"
        f"Stop: {stop}\n"
        f"Target: {target}"
    )


def check_exit(coin):
    global capital

    position = open_positions[coin]
    symbol = SYMBOLS[coin]

    df = get_klines(symbol, "15m")
    if df is None or len(df) < 5:
        return

    price = df.iloc[-1]["close"]

    if position["direction"] == "LONG":
        if price <= position["stop"] or price >= position["target"]:
            pnl = (price - position["entry"]) * position["size"]
        else:
            return
    else:
        if price >= position["stop"] or price <= position["target"]:
            pnl = (position["entry"] - price) * position["size"]
        else:
            return

    capital += pnl
    send_telegram(
        f"💰 {coin} Closed\nPnL: {round(pnl,2)} USDT\nCapital: {round(capital,2)}"
    )

    del open_positions[coin]


# ==============================
# HEARTBEAT
# ==============================

def heartbeat():
    global last_heartbeat

    if time.time() - last_heartbeat > 3600:
        send_telegram(
            f"🤖 ENGINE ALIVE (1H)\n"
            f"Capital: {round(capital,2)} USDT\n"
            f"Open Positions: {len(open_positions)}\n"
            f"Trades Today: {trades_today}"
        )
        last_heartbeat = time.time()


# ==============================
# MAIN LOOP
# ==============================

send_telegram("🚀 Advanced Virtual Engine Online")

while True:
    try:

        if date.today() != current_day:
            trades_today = 0

        heartbeat()

        for coin in SYMBOLS.keys():

            if coin in open_positions:
                check_exit(coin)

            else:
                if trades_today < MAX_TRADES_PER_DAY:
                    signal = check_signal(SYMBOLS[coin])
                    if signal:
                        direction, entry, atr = signal
                        open_trade(coin, direction, entry, atr)

        time.sleep(60)

    except Exception as e:
        print("Error:", e)
        time.sleep(10)
