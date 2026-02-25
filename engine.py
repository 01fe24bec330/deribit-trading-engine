import requests
import pandas as pd
import ta
import time
from datetime import datetime, date

# =========================
# CONFIG
# =========================

SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT"
}

START_CAPITAL = 100
RISK_PERCENT = 0.01
STOP_PERCENT = 0.006
TP_PERCENT = 0.012
MAX_TRADES_PER_DAY = 5
ADX_THRESHOLD = 20

TELEGRAM_TOKEN = "8688486536:AAGkjcujF9xRfB6w-UuiexM7iSg7Scs-GS0"
TELEGRAM_CHAT_ID = "7225721600"

capital = START_CAPITAL
open_positions = {}
trades_today = 0
current_day = date.today()
last_heartbeat = 0

# =========================
# TELEGRAM
# =========================

def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
    except:
        pass

# =========================
# DATA FUNCTIONS
# =========================

def get_price(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    return float(requests.get(url).json()["price"])

def get_klines(symbol, interval):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=200"
    data = requests.get(url).json()

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close",
        "v1","v2","v3","v4","v5","v6","v7"
    ])

    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)

    return df

# =========================
# STRATEGY
# =========================

def check_signal(symbol):

    df_4h = get_klines(symbol, "4h")
    df_1h = get_klines(symbol, "1h")
    df_15m = get_klines(symbol, "15m")

    df_4h["ema200"] = ta.trend.ema_indicator(df_4h["close"], 200)

    df_1h["ema50"] = ta.trend.ema_indicator(df_1h["close"], 50)
    df_1h["ema200"] = ta.trend.ema_indicator(df_1h["close"], 200)

    df_15m["ema20"] = ta.trend.ema_indicator(df_15m["close"], 20)
    df_15m["rsi"] = ta.momentum.rsi(df_15m["close"], 14)
    df_15m["atr"] = ta.volatility.average_true_range(
        df_15m["high"], df_15m["low"], df_15m["close"], 14
    )
    df_15m["adx"] = ta.trend.adx(
        df_15m["high"], df_15m["low"], df_15m["close"], 14
    )

    last_4h = df_4h.iloc[-1]
    last_1h = df_1h.iloc[-1]
    last_15m = df_15m.iloc[-1]

    bias_long = last_4h["close"] > last_4h["ema200"]
    trend_long = last_1h["ema50"] > last_1h["ema200"]

    atr_avg = df_15m["atr"].rolling(20).mean().iloc[-1]
    volatility_ok = last_15m["atr"] > atr_avg
    strength_ok = last_15m["adx"] > ADX_THRESHOLD

    near_ema = abs(last_15m["close"] - last_15m["ema20"]) / last_15m["close"] < 0.002

    # LONG
    if bias_long and trend_long and volatility_ok and strength_ok:
        if near_ema and 40 <= last_15m["rsi"] <= 55:
            return "LONG", last_15m["close"]

    # SHORT
    if not bias_long and not trend_long and volatility_ok and strength_ok:
        if near_ema and 45 <= last_15m["rsi"] <= 60:
            return "SHORT", last_15m["close"]

    return None

# =========================
# TRADE MANAGEMENT
# =========================

def open_trade(symbol, direction, entry):
    global capital, trades_today

    risk_amount = capital * RISK_PERCENT

    if direction == "LONG":
        stop = entry * (1 - STOP_PERCENT)
        target = entry * (1 + TP_PERCENT)
    else:
        stop = entry * (1 + STOP_PERCENT)
        target = entry * (1 - TP_PERCENT)

    open_positions[symbol] = {
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk": risk_amount
    }

    trades_today += 1

    send_telegram(f"""
📥 {symbol} {direction}
Entry: {round(entry,4)}
Stop: {round(stop,4)}
Target: {round(target,4)}
Capital: {round(capital,2)} USDT
""")

def check_exit(symbol):
    global capital

    pos = open_positions[symbol]
    price = get_price(SYMBOLS[symbol])

    if pos["direction"] == "LONG":
        if price <= pos["stop"]:
            pnl = -pos["risk"]
        elif price >= pos["target"]:
            pnl = pos["risk"] * 2
        else:
            return
    else:
        if price >= pos["stop"]:
            pnl = -pos["risk"]
        elif price <= pos["target"]:
            pnl = pos["risk"] * 2
        else:
            return

    capital += pnl

    send_telegram(f"""
❌ {symbol} CLOSED
PnL: {round(pnl,2)} USDT
New Capital: {round(capital,2)} USDT
""")

    del open_positions[symbol]

# =========================
# HEARTBEAT (1 HOUR)
# =========================

def heartbeat():
    global last_heartbeat

    if time.time() - last_heartbeat >= 3600:

        msg = f"""
🤖 ENGINE ALIVE (1H)
Time: {datetime.utcnow().strftime('%H:%M:%S')} UTC
Capital: {round(capital,2)} USDT
Open Positions: {len(open_positions)}
Trades Today: {trades_today}
"""

        print(msg)
        send_telegram(msg)

        last_heartbeat = time.time()

# =========================
# MAIN LOOP
# =========================

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
                        direction, entry = signal
                        open_trade(coin, direction, entry)

        time.sleep(60)

    except Exception as e:
        print("Error:", e)
        time.sleep(10)
