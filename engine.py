import requests
import pandas as pd
import numpy as np
import ta
import time
import sqlite3
from datetime import datetime, date

# =============================
# CONFIG
# =============================

import os

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://test.deribit.com"

SYMBOLS = ["BTC-PERPETUAL", "ETH-PERPETUAL"]

RISK_PERCENT = 0.003
LEVERAGE = 5
RR_RATIO = 2
MAX_DAILY_LOSS_PERCENT = 0.02

access_token = None
tracked_positions = {}
daily_start_equity = None
daily_locked = False

# =============================
# DATABASE
# =============================

conn = sqlite3.connect("trades.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    side TEXT,
    entry REAL,
    stop REAL,
    target REAL,
    size REAL,
    exit REAL,
    pnl REAL,
    entry_time TEXT,
    exit_time TEXT
)
""")
conn.commit()

# =============================
# TELEGRAM
# =============================

def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
    except:
        pass

# =============================
# AUTH
# =============================

def authenticate():
    global access_token

    r = requests.get(
        f"{BASE_URL}/api/v2/public/auth",
        params={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET
        }
    ).json()

    if "result" not in r:
        print("AUTH FAILED:", r)
        return False

    access_token = r["result"]["access_token"]
    print("Authenticated successfully")
    return True


def private(method, params=None):
    global access_token

    headers = {"Authorization": f"Bearer {access_token}"}

    r = requests.get(
        f"{BASE_URL}/api/v2/{method}",
        params=params,
        headers=headers
    ).json()

    if "error" in r:
        authenticate()
        headers = {"Authorization": f"Bearer {access_token}"}
        r = requests.get(
            f"{BASE_URL}/api/v2/{method}",
            params=params,
            headers=headers
        ).json()

    return r


def public(method, params=None):
    return requests.get(f"{BASE_URL}/api/v2/{method}", params=params).json()

# =============================
# ACCOUNT
# =============================

def get_equity(currency):
    r = private("private/get_account_summary", {"currency": currency})
    if "result" not in r:
        return 0
    return r["result"]["equity"]


def get_positions(currency):
    r = private("private/get_positions", {"currency": currency})
    if "result" not in r:
        return []
    return r["result"]

# =============================
# DATA
# =============================

def get_klines(symbol, resolution):
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - (int(resolution) * 200 * 60 * 1000)

    r = public("public/get_tradingview_chart_data", {
        "instrument_name": symbol,
        "resolution": resolution,
        "start_timestamp": start_ts,
        "end_timestamp": end_ts
    })

    if "result" not in r:
        return None

    d = r["result"]

    return pd.DataFrame({
        "open": d["open"],
        "high": d["high"],
        "low": d["low"],
        "close": d["close"],
        "volume": d["volume"],
        "timestamp": d["ticks"]
    })

# =============================
# EXECUTION
# =============================

def place_orders(symbol, side, size, stop, target):

    entry_method = "private/buy" if side == "buy" else "private/sell"
    exit_method = "private/sell" if side == "buy" else "private/buy"

    private(entry_method, {
        "instrument_name": symbol,
        "amount": round(size, 3),
        "type": "market"
    })

    private(exit_method, {
        "instrument_name": symbol,
        "amount": round(size, 3),
        "type": "stop_market",
        "stop_price": stop
    })

    private(exit_method, {
        "instrument_name": symbol,
        "amount": round(size, 3),
        "type": "take_profit_market",
        "stop_price": target
    })

# =============================
# POSITION MONITOR
# =============================

def monitor_positions():
    for symbol in SYMBOLS:

        currency = "BTC" if "BTC" in symbol else "ETH"
        positions = get_positions(currency)

        current_pos = next(
            (p for p in positions if p["instrument_name"] == symbol and abs(p["size"]) > 0),
            None
        )

        if symbol in tracked_positions and current_pos is None:

            last_trade = private(
                "private/get_user_trades_by_instrument",
                {"instrument_name": symbol, "count": 1}
            )

            if "result" in last_trade and len(last_trade["result"]) > 0:
                t = last_trade["result"][0]
                exit_price = t["price"]
                pnl = t["profit_loss"]

                cursor.execute("""
                UPDATE trades
                SET exit=?, pnl=?, exit_time=?
                WHERE id=(SELECT id FROM trades WHERE symbol=? ORDER BY id DESC LIMIT 1)
                """, (
                    exit_price,
                    pnl,
                    str(datetime.utcnow()),
                    symbol
                ))
                conn.commit()

                msg = f"""
{symbol} CLOSED
Exit: {round(exit_price,2)}
PnL: {round(pnl,6)}
"""
                print(msg)
                send_telegram(msg)

            del tracked_positions[symbol]

        if current_pos and symbol not in tracked_positions:
            tracked_positions[symbol] = current_pos

# =============================
# STRATEGY
# =============================

def check_symbol(symbol):

    if daily_locked:
        return

    currency = "BTC" if "BTC" in symbol else "ETH"

    positions = get_positions(currency)
    if any(abs(p["size"]) > 0 and p["instrument_name"] == symbol for p in positions):
        return

    df_htf = get_klines(symbol, "360")
    df_15 = get_klines(symbol, "15")

    if df_htf is None or df_15 is None:
        return

    df_htf["ema100"] = ta.trend.ema_indicator(df_htf["close"], 100)
    df_15["rsi"] = ta.momentum.rsi(df_15["close"], 14)
    df_15["atr"] = ta.volatility.average_true_range(
        df_15["high"], df_15["low"], df_15["close"], 14
    )

    bias_long = df_htf.iloc[-1]["close"] > df_htf.iloc[-1]["ema100"]

    row = df_15.iloc[-1]
    prev = df_15.iloc[-2]
    atr = row["atr"]

    equity = get_equity(currency)
    if equity == 0:
        return

    risk_amount = equity * RISK_PERCENT
    stop_distance = atr * 1.5
    size = (risk_amount * LEVERAGE) / stop_distance

    if bias_long and row["rsi"] < 40 and row["close"] > prev["high"]:

        entry = row["close"]
        stop = entry - stop_distance
        target = entry + stop_distance * RR_RATIO

        place_orders(symbol, "buy", size, stop, target)

        cursor.execute("""
        INSERT INTO trades (symbol, side, entry, stop, target, size, entry_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol,
            "buy",
            entry,
            stop,
            target,
            size,
            str(datetime.utcnow())
        ))
        conn.commit()

        msg = f"""
{symbol} LONG ENTERED
Entry: {round(entry,2)}
Stop: {round(stop,2)}
Target: {round(target,2)}
Size: {round(size,3)}
Leverage: {LEVERAGE}x
"""
        print(msg)
        send_telegram(msg)

    elif not bias_long and row["rsi"] > 60 and row["close"] < prev["low"]:

        entry = row["close"]
        stop = entry + stop_distance
        target = entry - stop_distance * RR_RATIO

        place_orders(symbol, "sell", size, stop, target)

        cursor.execute("""
        INSERT INTO trades (symbol, side, entry, stop, target, size, entry_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol,
            "sell",
            entry,
            stop,
            target,
            size,
            str(datetime.utcnow())
        ))
        conn.commit()

        msg = f"""
{symbol} SHORT ENTERED
Entry: {round(entry,2)}
Stop: {round(stop,2)}
Target: {round(target,2)}
Size: {round(size,3)}
Leverage: {LEVERAGE}x
"""
        print(msg)
        send_telegram(msg)

# =============================
# MAIN
# =============================

print("FINAL INSTITUTIONAL ENGINE RUNNING...")

if not authenticate():
    exit()

daily_start_equity = get_equity("BTC")

while True:
    try:
        for s in SYMBOLS:
            check_symbol(s)

        monitor_positions()

        time.sleep(20)

    except Exception as e:
        print("Runtime error:", e)
        time.sleep(10)