"""
دستیار حرفه‌ای شکار پامپ
========================
- تشخیص انباشت قبل از پامپ (Accumulation Detection)
- تأیید چندصرافی (Coinbase + Kraken)
- ترندهای اجتماعی (CoinGecko Trending)
- معاملات نهنگ‌ها (OKX Whale Trades)
- بازار فیوچرز، عمق بازار، لانگ/شورت (OKX)
- هشدار ساعتی + گزارش روزانه ۸ صبح ایران
- داده OHLCV از OKX (بدون محدودیت CoinGecko)
"""

import os
import time
import json
import statistics
import requests
from datetime import datetime


# ============================================================
# تنظیمات
# ============================================================
STATE_FILE = "alerted.json"
ACCUM_THRESHOLD = 60
PUMP_THRESHOLD = 70


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")


def load_config():
    return {
        "telegram_token": os.environ.get("TELEGRAM_TOKEN", ""),
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
    }


def load_state():
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            if data.get("date") == today:
                return {
                    "alerted": set(data.get("alerted", [])),
                    "daily_sent": data.get("daily_sent", ""),
                }
    except Exception:
        pass
    return {"alerted": set(), "daily_sent": ""}


def save_state(state):
    data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "alerted": list(state["alerted"]),
        "daily_sent": state.get("daily_sent", ""),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)


# ============================================================
# داده‌های OKX (بدون API Key)
# ============================================================
def get_market_chart_okx(symbol, days=30):
    """دریافت OHLCV از OKX"""
    try:
        inst = symbol.upper() + "-USDT"
        bar = "1D" if days > 10 else "4H"
        limit = min(days, 300)
        r = requests.get(
            "https://www.okx.com/api/v5/market/candles",
            params={"instId": inst, "bar": bar, "limit": limit},
            timeout=10,
        )
        if r.status_code != 200:
            return {"prices": [], "volumes": []}
        data = r.json().get("data", [])
        if not data:
            return {"prices": [], "volumes": []}
        data = list(reversed(data))
        prices = [float(c[4]) for c in data]
        volumes = [float(c[5]) for c in data]
        return {"prices": prices, "volumes": volumes}
    except Exception:
        return {"prices": [], "volumes": []}


def get_futures_data(symbol):
    """Funding Rate + Open Interest از OKX"""
    result = {"funding_rate": None, "open_interest": None}
    inst = symbol.upper() + "-USDT-SWAP"
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": inst},
            timeout=8,
        )
        if r.status_code == 200:
            lst = r.json().get("data", [])
            if lst:
                result["funding_rate"] = float(lst[0].get("fundingRate", 0))
    except Exception:
        pass
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/public/open-interest",
            params={"instType": "SWAP", "instId": inst},
            timeout=8,
        )
        if r.status_code == 200:
            lst = r.json().get("data", [])
            if lst:
                result["open_interest"] = float(lst[0].get("oi", 0))
    except Exception:
        pass
    return result


def get_order_book_pressure(symbol):
    """عمق بازار OKX"""
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/market/books",
            params={"instId": symbol.upper() + "-USDT", "sz": 100},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        lst = r.json().get("data", [])
        if not lst:
            return None
        bids = lst[0].get("bids", [])
        asks = lst[0].get("asks", [])
        if not bids or not asks:
            return None
        bid_qty = [float(b[1]) for b in bids]
        ask_qty = [float(a[1]) for a in asks]
        total_bid = sum(bid_qty)
        total_ask = sum(ask_qty)
        ratio = total_bid / total_ask if total_ask > 0 else 0
        return {"ratio": ratio}
    except Exception:
        return None


def get_long_short_ratio(symbol):
    """نسبت لانگ/شورت OKX"""
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",
            params={"ccy": symbol.upper(), "period": "1H"},
            timeout=8,
        )
        if r.status_code == 200:
            lst = r.json().get("data", [])
            if lst:
                ratio = float(lst[0][1])
                long_pct = ratio / (1 + ratio) * 100
                short_pct = 100 - long_pct
                return {"long": long_pct, "short": short_pct, "ratio": ratio}
    except Exception:
        pass
    return None


def get_whale_trades(symbol):
    """معاملات بزرگ آنی از OKX"""
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/market/trades",
            params={"instId": symbol.upper() + "-USDT", "limit": 100},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        data = r.json().get("data", [])
        if not data or len(data) < 10:
            return None
        trades = []
        for t in data:
            try:
                value = float(t.get("px", 0)) * float(t.get("sz", 0))
                trades.append({"value": value, "side": t.get("side", "")})
            except Exception:
                continue
        if not trades:
            return None
        values = [t["value"] for t in trades]
        avg_val = sum(values) / len(values)
        threshold = max(avg_val * 10, 50000)
        buy_val = 0
        sell_val = 0
        whale_count = 0
        for t in trades:
            if t["value"] >= threshold:
                whale_count += 1
                if t["side"] == "buy":
                    buy_val += t["value"]
                else:
                    sell_val += t["value"]
        if whale_count == 0:
            return None
        total = buy_val + sell_val
        return {
            "count": whale_count,
            "buy_ratio": buy_val / total if total > 0 else 0.5,
        }
    except Exception:
        return None


def get_spot_futures_premium(symbol):
    """پرمیوم اسپات-فیوچرز OKX"""
    spot_price = None
    futures_price = None
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": symbol.upper() + "-USDT"},
            timeout=8,
        )
        if r.status_code == 200:
            lst = r.json().get("data", [])
            if lst:
                spot_price = float(lst[0].get("last", 0) or 0)
    except Exception:
        pass
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": symbol.upper() + "-USDT-SWAP"},
            timeout=8,
        )
        if r.status_code == 200:
            lst = r.json().get("data", [])
            if lst:
                futures_price = float(lst[0].get("last", 0) or 0)
    except Exception:
        pass
    if spot_price and futures_price and spot_price > 0:
        pct = ((futures_price - spot_price) / spot_price) * 100
        return {"premium_pct": round(pct, 4)}
    return None


def get_multi_exchange_data(symbol):
    """تأیید چندصرافی: Coinbase + Kraken"""
    result = {"exchanges_active": 0, "total": 2}
    try:
        r = requests.get(
            f"https://api.exchange.coinbase.com/products/{symbol.upper()}-USD/stats",
            timeout=8,
        )
        if r.status_code == 200:
            vol = float(r.json().get("volume", 0) or 0)
            if vol > 0:
                result["exchanges_active"] += 1
    except Exception:
        pass
    try:
        ksym = symbol.upper()
        if ksym == "BTC":
            ksym = "XBT"
        r = requests.get(
            "https://api.kraken.com/0/public/Ticker",
            params={"pair": ksym + "USD"},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            if not data.get("error"):
                ticker = data.get("result", {})
                if ticker:
                    first = list(ticker.keys())[0]
                    vol = ticker[first].get("v", [])
                    if len(vol) >= 2 and float(vol[1]) > 0:
                        result["exchanges_active"] += 1
    except Exception:
        pass
    return result


def get_fear_greed():
    """شاخص ترس و طمع"""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        if r.status_code == 200:
            data = r.json()["data"][0]
            return {
                "value": int(data["value"]),
                "classification": data["value_classification"],
            }
    except Exception:
        pass
    return None


# ============================================================
# CoinGecko
# ============================================================
class MarketDataFetcher:
    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PumpScreener/1.0"})

    def get_top_coins(self, limit=30):
        try:
            r = self.session.get(
                self.BASE_URL + "/coins/markets",
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": limit,
                    "page": 1,
                    "sparkline": False,
                    "price_change_percentage": "1h,24h,7d",
                },
                timeout=30,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            log(f"خطا در دریافت داده: {e}")
        return []


def get_trending_coins():
    """ترندهای CoinGecko"""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            timeout=10,
        )
        if r.status_code == 200:
            coins = r.json().get("coins", [])
            result = {}
            for i, item in enumerate(coins):
                sym = item.get("item", {}).get("symbol", "").upper()
                if sym:
                    result[sym] = i + 1
            return result
    except Exception:
        pass
    return {}


# ============================================================
# شاخص‌های تکنیکال
# ============================================================
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = statistics.mean(gains[-period:]) if gains else 0
    avg_loss = statistics.mean(losses[-period:]) if losses else 0
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))


def calculate_rsi_series(prices, period=14):
    if len(prices) < period + 1:
        return []
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(diff if diff > 0 else 0)
        losses.append(abs(diff) if diff < 0 else 0)
    rsi_values = [50.0] * period
    for i in range(period, len(gains) + 1):
        ag = statistics.mean(gains[i - period:i])
        al = statistics.mean(losses[i - period:i])
        if al == 0:
            rsi_values.append(100.0)
        else:
            rsi_values.append(100 - (100 / (1 + ag / al)))
    return rsi_values


def calculate_bollinger(prices, period=20):
    if len(prices) < period:
        return 0, 50
    recent = prices[-period:]
    sma = statistics.mean(recent)
    std = statistics.stdev(recent) if len(recent) > 1 else 0
    upper = sma + 2 * std
    lower = sma - 2 * std
    bandwidth = (upper - lower) / sma if sma > 0 else 0
    all_bw = []
    for i in range(period, len(prices) + 1):
        w = prices[i - period:i]
        s = statistics.mean(w)
        st = statistics.stdev(w) if len(w) > 1 else 0
        all_bw.append((2 * st) / s if s > 0 else 0)
    if all_bw:
        pct = sum(1 for bw in all_bw if bw < bandwidth) / len(all_bw) * 100
    else:
        pct = 50
    return bandwidth, pct


def calculate_volume_ratio(volumes, period=20):
    if len(volumes) < period:
        return 1.0
    recent = volumes[-period:]
    avg = statistics.mean(recent) if recent else 1
    return volumes[-1] / avg if avg > 0 else 1.0


def calculate_macd(prices):
    if len(prices) < 26:
        return 0, 0

    def ema(data, span):
        alpha = 2 / (span + 1)
        res = [data[0]]
        for p in data[1:]:
            res.append(alpha * p + (1 - alpha) * res[-1])
        return res

    e12 = ema(prices, 12)
    e26 = ema(prices, 26)
    macd_line = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd_line, 9)
    return macd_line[-1], sig[-1]


def calculate_ichimoku(prices):
    if len(prices) < 52:
        return None

    def hl(p):
        w = prices[-p:]
        return (max(w) + min(w)) / 2

    tenkan = hl(9)
    kijun = hl(26)
    senkou_a = (tenkan + kijun) / 2
    senkou_b = hl(52)
    cur = prices[-1]
    if cur > max(senkou_a, senkou_b):
        sig = "بالای ابر (صعودی)"
        sc = 20
    elif cur < min(senkou_a, senkou_b):
        sig = "زیر ابر (نزولی)"
        sc = 0
    else:
        sig = "داخل ابر (خنثی)"
        sc = 10
    if tenkan > kijun:
        sig += " | تنکان > کیجون"
        sc += 5
    return {"signal": sig, "score": min(sc, 25)}


def calculate_vwap(prices, volumes):
    if not prices or not volumes or len(prices) != len(volumes):
        return None
    total_pv = sum(p * v for p, v in zip(prices, volumes))
    total_v = sum(volumes)
    if total_v == 0:
        return None
    vwap = total_pv / total_v
    return {
        "vwap": vwap,
        "above": prices[-1] > vwap,
        "diff_pct": ((prices[-1] - vwap) / vwap) * 100 if vwap > 0 else 0,
    }


def detect_rsi_divergence(prices, rsi_values):
    if len(prices) < 30 or len(rsi_values) < 30:
        return None
    rp = prices[-20:]
    rr = rsi_values[-20:]
    if len(rp) != len(rr):
        return None
    highs = []
    for i in range(2, len(rp) - 2):
        if (rp[i] > rp[i-1] and rp[i] > rp[i-2]
                and rp[i] > rp[i+1] and rp[i] > rp[i+2]):
            highs.append((i, rp[i], rr[i]))
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if b[1] > a[1] and b[2] < a[2]:
            return "واگرایی نزولی (Bearish)"
    lows = []
    for i in range(2, len(rp) - 2):
        if (rp[i] < rp[i-1] and rp[i] < rp[i-2]
                and rp[i] < rp[i+1] and rp[i] < rp[i+2]):
            lows.append((i, rp[i], rr[i]))
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if b[1] < a[1] and b[2] > a[2]:
            return "واگرایی صعودی (Bullish)"
    return None


def calculate_volume_profile(prices, volumes, bins=20):
    if not prices or not volumes or len(prices) < 10:
        return None
    mn = min(prices)
    mx = max(prices)
    if mx == mn:
        return None
    bs = (mx - mn) / bins
    profile = [0.0] * bins
    for p, v in zip(prices, volumes):
        i = min(int((p - mn) / bs), bins - 1)
        profile[i] += v
    poc_i = profile.index(max(profile))
    poc = mn + (poc_i + 0.5) * bs
    total = sum(profile)
    target = total * 0.7
    si = sorted(range(bins), key=lambda i: profile[i], reverse=True)
    cum = 0
    va = []
    for i in si:
        cum += profile[i]
        va.append(i)
        if cum >= target:
            break
    vah = mn + (max(va) + 1) * bs
    val = mn + min(va) * bs
    return {"poc": poc, "vah": vah, "val": val}


# ============================================================
# امتیازدهی تکنیکال
# ============================================================
def score_technical(chart_data):
    prices = chart_data.get("prices", [])
    volumes = chart_data.get("volumes", [])
    if len(prices) < 52:
        return 0, {}
    details = {}
    score = 0

    vr = calculate_volume_ratio(volumes)
    if vr >= 3.0:
        score += 25
        details["volume"] = f"حجم {vr:.1f}x (قوی)"
    elif vr >= 2.0:
        score += 15
        details["volume"] = f"حجم {vr:.1f}x (خوب)"
    elif vr >= 1.5:
        score += 8
        details["volume"] = f"حجم {vr:.1f}x (خفیف)"
    else:
        details["volume"] = f"حجم {vr:.1f}x (عادی)"

    rsi = calculate_rsi(prices)
    if 25 <= rsi <= 35:
        score += 15
        details["rsi"] = f"RSI={rsi:.0f} (اشباع فروش)"
    elif 60 <= rsi <= 85:
        score += 8
        details["rsi"] = f"RSI={rsi:.0f} (محدوده پامپ)"
    else:
        details["rsi"] = f"RSI={rsi:.0f}"

    bw, pct = calculate_bollinger(prices)
    if pct <= 20:
        score += 15
        details["bb"] = f"فشردگی باند (صدک {pct:.0f})"
    elif pct <= 40:
        score += 8
        details["bb"] = f"باند باریک (صدک {pct:.0f})"
    else:
        details["bb"] = f"باند باز (صدک {pct:.0f})"

    macd, sig = calculate_macd(prices)
    if macd > sig and macd > 0:
        score += 15
        details["macd"] = "MACD صعودی"
    elif macd > sig:
        score += 8
        details["macd"] = "MACD کراس صعودی"
    else:
        details["macd"] = "MACD خنثی/نزولی"

    ichi = calculate_ichimoku(prices)
    if ichi:
        score += ichi["score"]
        details["ichimoku"] = f"ایچیموکو: {ichi['signal']}"

    vwap = calculate_vwap(prices, volumes)
    if vwap:
        if vwap["above"]:
            score += 10
            details["vwap"] = f"VWAP: بالای {vwap['vwap']:.4f} (+{vwap['diff_pct']:.2f}%)"
        else:
            details["vwap"] = f"VWAP: زیر {vwap['vwap']:.4f} ({vwap['diff_pct']:.2f}%)"

    rs = calculate_rsi_series(prices)
    div = detect_rsi_divergence(prices, rs)
    if div:
        if "صعودی" in div:
            score += 15
            details["divergence"] = f"🚀 {div}"
        else:
            details["divergence"] = f"⚠️ {div}"

    vp = calculate_volume_profile(prices, volumes)
    if vp:
        cur = prices[-1]
        if cur > vp["vah"]:
            score += 10
            details["vp"] = f"Volume Profile: بالای VAH ({vp['vah']:.4f})"
        elif cur < vp["val"]:
            score += 5
            details["vp"] = f"Volume Profile: زیر VAL ({vp['val']:.4f})"
        else:
            details["vp"] = f"Volume Profile: در محدوده ارزش (POC: {vp['poc']:.4f})"

    return min(score, 100), details


def score_onchain(coin):
    score = 0
    details = {}
    vol = coin.get("total_volume") or 0
    mc = coin.get("market_cap") or 1
    ratio = vol / mc if mc > 0 else 0
    if ratio >= 0.15:
        score += 40
        details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={ratio:.3f} (غیرعادی)"
    elif ratio >= 0.10:
        score += 25
        details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={ratio:.3f} (بالا)"
    elif ratio >= 0.05:
        score += 12
        details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={ratio:.3f}"

    c1 = abs(coin.get("price_change_percentage_1h_in_currency") or 0)
    c24 = abs(coin.get("price_change_percentage_24h_in_currency") or 0)
    mom = (c1 * 0.6) + (c24 * 0.4)
    if mom >= 5:
        score += 40
        details["momentum"] = f"شتاب={mom:.1f}% (بسیار بالا)"
    elif mom >= 3:
        score += 25
        details["momentum"] = f"شتاب={mom:.1f}% (بالا)"
    elif mom >= 1.5:
        score += 15
        details["momentum"] = f"شتاب={mom:.1f}% (متوسط)"
    return min(score, 100), details


# ============================================================
# تشخیص انباشت (قلب دستیار)
# ============================================================
def detect_accumulation(chart_data, coin):
    prices = chart_data.get("prices", [])
    volumes = chart_data.get("volumes", [])
    if len(prices) < 30 or len(volumes) < 30:
        return 0, {}
    details = {}
    score = 0
    symbol = (coin.get("symbol") or "").upper()

    # ۱. واگرایی حجم و قیمت
    rv = sum(volumes[-7:]) / 7
    pv = sum(volumes[-14:-7]) / 7
    vch = (rv - pv) / pv if pv > 0 else 0
    rp = sum(prices[-7:]) / 7
    pp = sum(prices[-14:-7]) / 7
    pch = (rp - pp) / pp if pp > 0 else 0
    if vch > 0.5 and abs(pch) < 0.05:
        score += 30
        details["divergence"] = f"🔥 واگرایی قوی: حجم +{vch*100:.0f}% / قیمت {pch*100:+.1f}%"
    elif vch > 0.3 and abs(pch) < 0.08:
        score += 20
        details["divergence"] = f"واگرایی متوسط: حجم +{vch*100:.0f}% / قیمت {pch*100:+.1f}%"
    elif vch > 0.2:
        score += 10
        details["divergence"] = f"افزایش حجم: +{vch*100:.0f}%"

    # ۲. RSI
    rsi = calculate_rsi(prices)
    if 35 <= rsi <= 55:
        score += 20
        details["rsi"] = f"RSI={rsi:.0f} (محدوده انباشت)"
    elif 55 < rsi <= 65:
        score += 10
        details["rsi"] = f"RSI={rsi:.0f} (نزدیک به انفجار)"
    else:
        details["rsi"] = f"RSI={rsi:.0f}"

    # ۳. باند بولینگر
    bw, pct = calculate_bollinger(prices)
    if pct <= 25:
        score += 20
        details["bb"] = f"🎯 فشردگی شدید باند (صدک {pct:.0f})"
    elif pct <= 40:
        score += 10
        details["bb"] = f"باند باریک (صدک {pct:.0f})"
    else:
        details["bb"] = f"باند باز (صدک {pct:.0f})"

    # ۴. VWAP
    vw = calculate_vwap(prices, volumes)
    if vw:
        d = vw["diff_pct"]
        if -5 <= d <= 5:
            score += 15
            details["vwap"] = f"💎 قیمت نزدیک VWAP ({d:+.2f}%) — فرصت ورود"
        elif 5 < d <= 15:
            score += 5
            details["vwap"] = f"VWAP: {d:+.2f}% بالای میانگین"
        else:
            details["vwap"] = f"VWAP: {d:+.2f}%"

    # ۵. شتاب پایین
    c24 = abs(coin.get("price_change_percentage_24h_in_currency") or 0)
    if c24 < 3:
        score += 15
        details["momentum"] = f"✅ شتاب {c24:.1f}% — هنوز پامپ نشده"
    elif c24 < 7:
        score += 8
        details["momentum"] = f"⚡ شتاب {c24:.1f}% — در حال شروع"
    else:
        details["momentum"] = f"⚠️ شتاب {c24:.1f}% — حرکت شروع شده"

    # ۶. تأیید چندصرافی
    me = get_multi_exchange_data(symbol)
    if me and me["exchanges_active"] >= 2:
        score += 15
        details["multi_exchange"] = f"🏦 ✅ تأیید چندصرافی (2/2)"
    elif me and me["exchanges_active"] == 1:
        score += 8
        details["multi_exchange"] = f"🏦 🟡 فقط ۱ صرافی"

    # ۷. پرمیوم اسپات-فیوچرز
    pr = get_spot_futures_premium(symbol)
    if pr:
        p = pr["premium_pct"]
        if p > 0.5:
            score += 10
            details["premium"] = f"💱 فیوچرز {p:+.3f}% بالای اسپات"
        elif p < -0.5:
            score -= 10
            details["premium"] = f"💱 فیوچرز {p:+.3f}% زیر اسپات"

    # ۸. ترند
    tr = coin.get("_trending_rank")
    if tr:
        if tr <= 3:
            score += 20
            details["trending"] = f"🔥 ترند #{tr} (هیجان بالا)"
        elif tr <= 7:
            score += 12
            details["trending"] = f"🔥 ترند #{tr}"
        else:
            score += 5
            details["trending"] = f"📈 در ترندها (#{tr})"

    # ۹. نهنگ‌ها
    wh = get_whale_trades(symbol)
    if wh:
        if wh["buy_ratio"] > 0.7:
            score += 20
            details["whale"] = f"🐋 {wh['count']} معامله بزرگ — {wh['buy_ratio']*100:.0f}% خرید"
        elif wh["buy_ratio"] < 0.3:
            score -= 10
            details["whale"] = f"🐋 {wh['count']} معامله بزرگ — {wh['buy_ratio']*100:.0f}% فروش"
        else:
            score += 5
            details["whale"] = f"🐋 {wh['count']} معامله بزرگ — متعادل"

    return max(0, min(score, 100)), details


# ============================================================
# تولید تحلیل‌ها
# ============================================================
def generate_accumulation_analysis(token):
    symbol = token["symbol"]
    score = token["accum_score"]
    details = token.get("details", {})
    lines = []
    if score >= 80:
        lvl = "🟢 انباشت بسیار قوی — احتمال پامپ بالا"
    elif score >= 70:
        lvl = "🟢 انباشت قوی — در رادار"
    elif score >= 60:
        lvl = "🟡 انباشت متوسط — زیر نظر"
    else:
        lvl = "🟠 انباشت اولیه"

    lines.append(f"<b>{symbol}</b> — {lvl}")
    lines.append(f"📊 امتیاز پامپ فعلی: {token.get('pump_score', 0)}/100 (هنوز پایین = فرصت)")

    sig = []
    for k in ["divergence", "rsi", "bb", "vwap", "momentum",
              "multi_exchange", "premium", "trending", "whale"]:
        if details.get(k):
            sig.append(details[k])

    if sig:
        lines.append("🎯 <b>سیگنال‌های انباشت:</b>")
        for s in sig:
            lines.append(f"   • {s}")

    lines.append("")
    lines.append("💡 <b>استراتژی پیشنهادی:</b>")
    lines.append("   • این توکن هنوز پامپ نشده — فرصت ورود اولیه")
    lines.append("   • حد ضرر: ۸٪ زیر قیمت فعلی")
    lines.append("   • هدف: ۲۰-۵۰٪ سود")
    lines.append("   • حجم پیشنهادی: ۱-۲٪ سرمایه")
    return "\n".join(lines)


def generate_token_analysis(token):
    symbol = token["symbol"]
    score = token["pump_score"]
    details = token.get("details", {})
    lines = []
    if score >= 70:
        lvl = "🟢 سیگنال قوی"
    elif score >= 50:
        lvl = "🟡 سیگنال متوسط"
    else:
        lvl = "🟠 سیگنال ضعیف"

    lines.append(f"<b>{symbol}</b> — {lvl}")

    # داده فیوچرز
    deriv = get_futures_data(symbol)
    if deriv["funding_rate"] is not None or deriv["open_interest"] is not None:
        lines.append("🎯 <b>بازار فیوچرز:</b>")
        if deriv["funding_rate"] is not None:
            lines.append(f"   💹 فاندینگ: {deriv['funding_rate']*100:.4f}%")
        if deriv["open_interest"] is not None:
            lines.append(f"   📊 بهره باز: {deriv['open_interest']:,.0f}")

    # عمق
    ob = get_order_book_pressure(symbol)
    if ob:
        lines.append(f"📋 عمق بازار: خرید/فروش={ob['ratio']:.2f}")

    # لانگ/شورت
    ls = get_long_short_ratio(symbol)
    if ls:
        lines.append(f"⚖️ لانگ/شورت: {ls['long']:.1f}% / {ls['short']:.1f}%")

    # BTC fear&greed
    if symbol == "BTC":
        fg = get_fear_greed()
        if fg:
            lines.append(f"😱 ترس و طمع: {fg['value']} ({fg['classification']})")

    if details:
        lines.append("✅ <b>نقاط قوت:</b>")
        for v in details.values():
            lines.append(f"   • {v}")

    lines.append("")
    if score >= 70:
        lines.append("🎯 <b>پیشنهاد: بررسی جدی برای ورود</b> (حد ضرر ۵٪)")
    elif score >= 50:
        lines.append("🎯 <b>پیشنهاد: در لیست رصد</b>")
    else:
        lines.append("🎯 <b>پیشنهاد: فعلاً ورود نکنید</b>")
    return "\n".join(lines)


# ============================================================
# تلگرام
# ============================================================
def _send_one(token, chat_id, text):
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log(f"خطا در ارسال: {e}")
        return False


def send_accumulation_alerts(token, chat_id, alerts, alerted_set):
    if not token or not chat_id:
        return False
    new = [a for a in alerts if a["symbol"] not in alerted_set]
    if not new:
        log("هیچ هشدار انباشت جدیدی نیست.")
        return True
    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    header = f"🎯 <b>هشدار انباشت پول هوشمند — {date_str}</b>\n"
    header += f"تعداد: <b>{len(new)}</b> توکن در حال انباشت (قبل از پامپ)"
    _send_one(token, chat_id, header)
    time.sleep(2)
    for i, a in enumerate(new[:5], 1):
        msg = f"<b>🎯 #{i} {a['symbol']}</b> — امتیاز انباشت: <b>{a['accum_score']}/100</b>\n"
        msg += f"💵 ${a['price']:.6f} | 📈 {a['change_24h']:+.2f}%\n\n"
        msg += generate_accumulation_analysis(a)
        _send_one(token, chat_id, msg)
        time.sleep(3)
        alerted_set.add(a["symbol"])
    log(f"{len(new)} هشدار انباشت ارسال شد.")
    return True


def send_daily_top5(token, chat_id, top5):
    if not token or not chat_id or not top5:
        return False
    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    _send_one(token, chat_id, f"🏆 <b>گزارش روزانه — {date_str}</b>\n۵ توکن برتر")
    time.sleep(2)
    for i, a in enumerate(top5, 1):
        msg = f"<b>🏆 رتبه #{i} — {a['symbol']}</b> (امتیاز {a['pump_score']}/100)\n"
        msg += f"💵 ${a['price']:.6f} | 📈 {a['change_24h']:+.2f}%\n\n"
        msg += generate_token_analysis(a)
        _send_one(token, chat_id, msg)
        time.sleep(3)
    log("گزارش روزانه ارسال شد.")
    return True


# ============================================================
# اجرای اصلی
# ============================================================
def run_scan():
    config = load_config()
    token = config.get("telegram_token", "")
    chat_id = config.get("chat_id", "")

    state = load_state()
    alerted_set = state["alerted"]

    hour_utc = datetime.utcnow().hour
    today = datetime.now().strftime("%Y-%m-%d")
    should_daily = (hour_utc >= 4) and (state["daily_sent"] != today)

    log(f"شروع اسکن انباشت... (هشدارها: {len(alerted_set)}, UTC: {hour_utc})")

    fetcher = MarketDataFetcher()
    coins = fetcher.get_top_coins(limit=30)
    if not coins:
        log("دریافت داده ناموفق.")
        return []

    # فیلتر استیبل‌کوین
    stable = {
        "USDT", "USDC", "DAI", "USDS", "BUSD", "TUSD", "USDP", "FDUSD",
        "USDD", "PYUSD", "GUSD", "FRAX", "UST", "USTC", "MIM", "LUSD",
        "SUSD", "ALUSD", "DOLA", "CUSD", "USDE", "SUSDE", "USD1", "RLUSD",
        "EURT", "EURC", "EURS", "XSGD", "BIDR", "IDRT", "TRYB", "BRZ",
    }
    coins = [c for c in coins if (c.get("symbol") or "").upper() not in stable]
    log(f"پس از فیلتر استیبل‌کوین: {len(coins)} توکن")

    # ترندها
    trending = get_trending_coins()
    log(f"تعداد ترندها: {len(trending)}")
    for c in coins:
        s = (c.get("symbol") or "").upper()
        c["_trending_rank"] = trending.get(s)

    accum_alerts = []
    all_results = []

    for i, coin in enumerate(coins):
        try:
            symbol = (coin.get("symbol") or "?").upper()
            if symbol in alerted_set:
                continue

            chart = get_market_chart_okx(symbol, days=30)
            if not chart.get("prices"):
                continue

            accum, accum_det = detect_accumulation(chart, coin)

            tech, tech_det = score_technical(chart)
            onc, onc_det = score_onchain(coin)
            final = tech * 0.55 + onc * 0.45

            c24 = abs(coin.get("price_change_percentage_24h_in_currency") or 0)
            not_pumped = c24 < 10

            if accum >= ACCUM_THRESHOLD and not_pumped:
                accum_alerts.append({
                    "symbol": symbol,
                    "name": coin.get("name", symbol),
                    "accum_score": round(accum, 1),
                    "pump_score": round(final, 1),
                    "price": coin.get("current_price") or 0,
                    "change_24h": coin.get("price_change_percentage_24h_in_currency") or 0,
                    "details": accum_det,
                })

            all_results.append({
                "symbol": symbol,
                "name": coin.get("name", symbol),
                "pump_score": round(final, 1),
                "price": coin.get("current_price") or 0,
                "change_24h": coin.get("price_change_percentage_24h_in_currency") or 0,
                "details": {**tech_det, **onc_det},
            })
            time.sleep(1)
        except Exception as e:
            log(f"خطا در {i+1}: {e}")
            continue

    all_results.sort(key=lambda x: x["pump_score"], reverse=True)

    if accum_alerts:
        log(f"🎯 {len(accum_alerts)} توکن در حال انباشت!")
        send_accumulation_alerts(token, chat_id, accum_alerts, alerted_set)
        state["alerted"] = alerted_set
        save_state(state)
    else:
        log("هیچ توکنی در حال انباشت نیست.")

    if should_daily and all_results:
        log("📊 ارسال گزارش روزانه...")
        send_daily_top5(token, chat_id, all_results[:5])
        state["daily_sent"] = today
        save_state(state)

    return all_results


if __name__ == "__main__":
    log("=" * 40)
    try:
        run_scan()
        log("کامل شد.")
    except Exception as e:
        log(f"خطای کلی: {e}")
