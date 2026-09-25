"""
اسکنر پامپ — نسخه نهایی
هشدار ساعتی (امتیاز ۷۰+) + گزارش روزانه ۵ توکن برتر (۸ صبح ایران)
"""

import os
import time
import json
import statistics
import requests
from datetime import datetime


# ============================================================
# State — جلوگیری از ارسال تکراری
# ============================================================
STATE_FILE = "alerted.json"


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
# داده‌های OKX (فیوچرز، عمق، لانگ/شورت)
# ============================================================
def get_futures_data(symbol):
    result = {"funding_rate": None, "open_interest": None}
    inst = symbol + "-USDT-SWAP"
    try:
        r = requests.get("https://www.okx.com/api/v5/public/funding-rate",
                         params={"instId": inst}, timeout=8)
        if r.status_code == 200:
            lst = r.json().get("data", [])
            if lst:
                result["funding_rate"] = float(lst[0].get("fundingRate", 0))
    except Exception:
        pass
    try:
        r = requests.get("https://www.okx.com/api/v5/public/open-interest",
                         params={"instType": "SWAP", "instId": inst}, timeout=8)
        if r.status_code == 200:
            lst = r.json().get("data", [])
            if lst:
                result["open_interest"] = float(lst[0].get("oi", 0))
    except Exception:
        pass
    return result


def get_order_book_pressure(symbol):
    try:
        url = "https://www.okx.com/api/v5/market/books"
        params = {"instId": symbol + "-USDT", "sz": 100}
        r = requests.get(url, params=params, timeout=8)
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
        avg_bid = sum(bid_qty) / len(bid_qty)
        avg_ask = sum(ask_qty) / len(ask_qty)
        big_bids = sum(1 for q in bid_qty if q > avg_bid * 10)
        big_asks = sum(1 for q in ask_qty if q > avg_ask * 10)
        total_bid = sum(bid_qty)
        total_ask = sum(ask_qty)
        ratio = total_bid / total_ask if total_ask > 0 else 0
        return {"ratio": ratio, "big_bids": big_bids, "big_asks": big_asks}
    except Exception:
        return None


def get_long_short_ratio(symbol):
    try:
        url = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
        params = {"ccy": symbol, "period": "1H"}
        r = requests.get(url, params=params, timeout=8)
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


def get_fear_greed():
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

    def _get(self, url, params):
        try:
            r = self.session.get(url, params=params, timeout=30)
            if r.status_code == 429:
                log("Rate limit — صبر ۱۰ ثانیه")
                time.sleep(10)
                r = self.session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log(f"خطا در درخواست: {e}")
            return None

    def get_top_coins(self, limit=25):
        url = self.BASE_URL + "/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "sparkline": False,
            "price_change_percentage": "1h,24h,7d",
        }
        data = self._get(url, params)
        return data if data else []

    def get_market_chart(self, coin_id, days=30):
        url = self.BASE_URL + "/coins/" + coin_id + "/market_chart"
        params = {"vs_currency": "usd", "days": days}
        data = self._get(url, params)
        if not data:
            return {"prices": [], "volumes": []}
        prices = [p[1] for p in data.get("prices", [])]
        volumes = [v[1] for v in data.get("total_volumes", [])]
        return {"prices": prices, "volumes": volumes}


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
        avg_gain = statistics.mean(gains[i - period:i])
        avg_loss = statistics.mean(losses[i - period:i])
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rsi_values.append(100 - (100 / (1 + avg_gain / avg_loss)))
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
        window = prices[i - period:i]
        s = statistics.mean(window)
        st = statistics.stdev(window) if len(window) > 1 else 0
        all_bw.append((2 * st) / s if s > 0 else 0)
    if all_bw:
        percentile = sum(1 for bw in all_bw if bw < bandwidth) / len(all_bw) * 100
    else:
        percentile = 50
    return bandwidth, percentile


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
        result = [data[0]]
        for p in data[1:]:
            result.append(alpha * p + (1 - alpha) * result[-1])
        return result

    ema12 = ema(prices, 12)
    ema26 = ema(prices, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = ema(macd_line, 9)
    return macd_line[-1], signal_line[-1]


def calculate_ichimoku(prices):
    if len(prices) < 52:
        return None

    def hl(period):
        w = prices[-period:]
        return (max(w) + min(w)) / 2

    tenkan = hl(9)
    kijun = hl(26)
    senkou_a = (tenkan + kijun) / 2
    senkou_b = hl(52)
    current = prices[-1]

    if current > max(senkou_a, senkou_b):
        signal = "بالای ابر (صعودی)"
        score = 20
    elif current < min(senkou_a, senkou_b):
        signal = "زیر ابر (نزولی)"
        score = 0
    else:
        signal = "داخل ابر (خنثی)"
        score = 10

    if tenkan > kijun:
        signal += " | تنکان > کیجون"
        score += 5

    return {"signal": signal, "score": min(score, 25)}


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
        "current": prices[-1],
        "above": prices[-1] > vwap,
        "diff_pct": ((prices[-1] - vwap) / vwap) * 100 if vwap > 0 else 0,
    }


def detect_rsi_divergence(prices, rsi_values):
    if len(prices) < 30 or len(rsi_values) < 30:
        return None
    recent_p = prices[-20:]
    recent_r = rsi_values[-20:]
    if len(recent_p) != len(recent_r):
        return None

    p_highs = []
    for i in range(2, len(recent_p) - 2):
        if (recent_p[i] > recent_p[i - 1] and recent_p[i] > recent_p[i - 2]
                and recent_p[i] > recent_p[i + 1] and recent_p[i] > recent_p[i + 2]):
            p_highs.append((i, recent_p[i], recent_r[i]))
    if len(p_highs) >= 2:
        p1, p2 = p_highs[-2], p_highs[-1]
        if p2[1] > p1[1] and p2[2] < p1[2]:
            return "واگرایی نزولی (Bearish)"

    p_lows = []
    for i in range(2, len(recent_p) - 2):
        if (recent_p[i] < recent_p[i - 1] and recent_p[i] < recent_p[i - 2]
                and recent_p[i] < recent_p[i + 1] and recent_p[i] < recent_p[i + 2]):
            p_lows.append((i, recent_p[i], recent_r[i]))
    if len(p_lows) >= 2:
        p1, p2 = p_lows[-2], p_lows[-1]
        if p2[1] < p1[1] and p2[2] > p1[2]:
            return "واگرایی صعودی (Bullish)"
    return None


def calculate_volume_profile(prices, volumes, bins=20):
    if not prices or not volumes or len(prices) < 10:
        return None
    min_p = min(prices)
    max_p = max(prices)
    if max_p == min_p:
        return None
    bin_size = (max_p - min_p) / bins
    profile = [0.0] * bins
    for p, v in zip(prices, volumes):
        idx = min(int((p - min_p) / bin_size), bins - 1)
        profile[idx] += v
    poc_idx = profile.index(max(profile))
    poc = min_p + (poc_idx + 0.5) * bin_size
    total_vol = sum(profile)
    target = total_vol * 0.7
    sorted_idx = sorted(range(bins), key=lambda i: profile[i], reverse=True)
    cumulative = 0
    va_idx = []
    for i in sorted_idx:
        cumulative += profile[i]
        va_idx.append(i)
        if cumulative >= target:
            break
    vah = min_p + (max(va_idx) + 1) * bin_size
    val = min_p + min(va_idx) * bin_size
    return {"poc": poc, "vah": vah, "val": val}


# ============================================================
# امتیازدهی
# ============================================================
def score_technical(chart_data):
    prices = chart_data.get("prices", [])
    volumes = chart_data.get("volumes", [])
    if len(prices) < 52:
        return 0, {}
    details = {}
    score = 0

    vol_ratio = calculate_volume_ratio(volumes)
    if vol_ratio >= 3.0:
        score += 25
        details["volume"] = f"حجم {vol_ratio:.1f}x (قوی)"
    elif vol_ratio >= 2.0:
        score += 15
        details["volume"] = f"حجم {vol_ratio:.1f}x (خوب)"
    elif vol_ratio >= 1.5:
        score += 8
        details["volume"] = f"حجم {vol_ratio:.1f}x (خفیف)"
    else:
        details["volume"] = f"حجم {vol_ratio:.1f}x (عادی)"

    rsi = calculate_rsi(prices)
    if 25 <= rsi <= 35:
        score += 15
        details["rsi"] = f"RSI={rsi:.0f} (اشباع فروش)"
    elif 60 <= rsi <= 85:
        score += 8
        details["rsi"] = f"RSI={rsi:.0f} (محدوده پامپ)"
    else:
        details["rsi"] = f"RSI={rsi:.0f}"

    bw, percentile = calculate_bollinger(prices)
    if percentile <= 20:
        score += 15
        details["bb"] = f"فشردگی باند (صدک {percentile:.0f})"
    elif percentile <= 40:
        score += 8
        details["bb"] = f"باند باریک (صدک {percentile:.0f})"
    else:
        details["bb"] = f"باند باز (صدک {percentile:.0f})"

    macd, signal = calculate_macd(prices)
    if macd > signal and macd > 0:
        score += 15
        details["macd"] = "MACD صعودی"
    elif macd > signal:
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

    rsi_series = calculate_rsi_series(prices)
    div = detect_rsi_divergence(prices, rsi_series)
    if div:
        if "صعودی" in div:
            score += 15
            details["divergence"] = f"🚀 {div}"
        else:
            details["divergence"] = f"⚠️ {div}"

    vp = calculate_volume_profile(prices, volumes)
    if vp:
        current = prices[-1]
        if current > vp["vah"]:
            score += 10
            details["vp"] = f"Volume Profile: بالای VAH ({vp['vah']:.4f})"
        elif current < vp["val"]:
            score += 5
            details["vp"] = f"Volume Profile: زیر VAL ({vp['val']:.4f})"
        else:
            details["vp"] = f"Volume Profile: در محدوده ارزش (POC: {vp['poc']:.4f})"

    return min(score, 100), details

def detect_accumulation(chart_data, coin):
    """تشخیص انباشت پول هوشمند (قبل از پامپ)"""
    prices = chart_data.get("prices", [])
    volumes = chart_data.get("volumes", [])

    if len(prices) < 30 or len(volumes) < 30:
        return 0, {}

    details = {}
    score = 0

    # ۱. واگرایی حجم و قیمت
    recent_vol = sum(volumes[-7:]) / 7
    previous_vol = sum(volumes[-14:-7]) / 7
    vol_change = (recent_vol - previous_vol) / previous_vol if previous_vol > 0 else 0

    recent_price = sum(prices[-7:]) / 7
    previous_price = sum(prices[-14:-7]) / 7
    price_change = (recent_price - previous_price) / previous_price if previous_price > 0 else 0

    if vol_change > 0.5 and abs(price_change) < 0.05:
        score += 30
        details["divergence"] = f"واگرایی قوی: حجم +{vol_change*100:.0f}% / قیمت {price_change*100:+.1f}%"
        details["divergence_signal"] = "strong"
    elif vol_change > 0.3 and abs(price_change) < 0.08:
        score += 20
        details["divergence"] = f"واگرایی متوسط: حجم +{vol_change*100:.0f}% / قیمت {price_change*100:+.1f}%"
    elif vol_change > 0.2:
        score += 10
        details["divergence"] = f"افزایش حجم: +{vol_change*100:.0f}%"

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

    # ۳. فشردگی باند
    bw, percentile = calculate_bollinger(prices)
    if percentile <= 25:
        score += 20
        details["bb"] = f"فشردگی شدید باند (صدک {percentile:.0f})"
    elif percentile <= 40:
        score += 10
        details["bb"] = f"باند باریک (صدک {percentile:.0f})"
    else:
        details["bb"] = f"باند باز (صدک {percentile:.0f})"

    # ۴. VWAP
    vwap_data = calculate_vwap(prices, volumes)
    if vwap_data:
        diff = vwap_data["diff_pct"]
        if -5 <= diff <= 5:
            score += 15
            details["vwap"] = f"قیمت نزدیک VWAP ({diff:+.2f}%) — فرصت ورود"
        elif 5 < diff <= 15:
            score += 5
            details["vwap"] = f"VWAP: {diff:+.2f}% بالای میانگین"
        else:
            details["vwap"] = f"VWAP: {diff:+.2f}%"

    # ۵. شتاب پایین
    change_24h = abs(coin.get("price_change_percentage_24h_in_currency") or 0)
    if change_24h < 3:
        score += 15
        details["momentum"] = f"شتاب {change_24h:.1f}% — هنوز پامپ نشده"
    elif change_24h < 7:
        score += 8
        details["momentum"] = f"شتاب {change_24h:.1f}% — در حال شروع"
    else:
        details["momentum"] = f"شتاب {change_24h:.1f}% — حرکت شروع شده"

    return min(score, 100), details



def score_onchain(coin):
    score = 0
    details = {}
    volume = coin.get("total_volume") or 0
    market_cap = coin.get("market_cap") or 1
    ratio = volume / market_cap if market_cap > 0 else 0

    if ratio >= 0.15:
        score += 40
        details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={ratio:.3f} (غیرعادی)"
    elif ratio >= 0.10:
        score += 25
        details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={ratio:.3f} (بالا)"
    elif ratio >= 0.05:
        score += 12
        details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={ratio:.3f}"

    change_1h = abs(coin.get("price_change_percentage_1h_in_currency") or 0)
    change_24h = abs(coin.get("price_change_percentage_24h_in_currency") or 0)
    momentum = (change_1h * 0.6) + (change_24h * 0.4)

    if momentum >= 5:
        score += 40
        details["momentum"] = f"شتاب={momentum:.1f}% (بسیار بالا)"
    elif momentum >= 3:
        score += 25
        details["momentum"] = f"شتاب={momentum:.1f}% (بالا)"
    elif momentum >= 1.5:
        score += 15
        details["momentum"] = f"شتاب={momentum:.1f}% (متوسط)"

    return min(score, 100), details


# ============================================================
# تولید تحلیل
# ============================================================
def generate_token_analysis(token):
    symbol = token["symbol"]
    score = token["pump_score"]
    details = token.get("details", {})
    lines = []

    if score >= 70:
        level = "🟢 سیگنال قوی"
    elif score >= 50:
        level = "🟡 سیگنال متوسط"
    elif score >= 30:
        level = "🟠 سیگنال ضعیف"
    else:
        level = "🔴 بدون سیگنال"

    lines.append(f"<b>{symbol}</b> — {level}")

    strengths = []
    weaknesses = []

    vol = details.get("volume", "")
    if "قوی" in vol or "خوب" in vol:
        strengths.append(f"📊 {vol}")
    elif "عادی" in vol:
        weaknesses.append(f"📊 {vol}")

    rsi = details.get("rsi", "")
    if "اشباع فروش" in rsi or "محدوده پامپ" in rsi:
        strengths.append(f"📈 {rsi}")

    bb = details.get("bb", "")
    if "فشردگی" in bb or "باریک" in bb:
        strengths.append(f"📉 {bb}")
    elif "باز" in bb:
        weaknesses.append(f"📉 {bb}")

    macd = details.get("macd", "")
    if "صعودی" in macd or "کراس" in macd:
        strengths.append(f"📊 {macd}")
    elif "نزولی" in macd:
        weaknesses.append(f"📊 {macd}")

    ichi = details.get("ichimoku", "")
    if "صعودی" in ichi:
        strengths.append(f"☁️ {ichi}")
    elif "نزولی" in ichi:
        weaknesses.append(f"☁️ {ichi}")

    vwap = details.get("vwap", "")
    if vwap and "بالای" in vwap:
        strengths.append(f"📏 {vwap}")
    elif vwap and "زیر" in vwap:
        weaknesses.append(f"📏 {vwap}")

    div = details.get("divergence", "")
    if div:
        if "صعودی" in div:
            strengths.append(f"🔀 {div}")
        else:
            weaknesses.append(f"🔀 {div}")

    vp = details.get("vp", "")
    if vp:
        strengths.append(f"📦 {vp}")

    vm = details.get("vol_mcap", "")
    if "غیرعادی" in vm or "بالا" in vm:
        strengths.append(f"🐋 {vm}")

    mom = details.get("momentum", "")
    if "بسیار بالا" in mom or "بالا" in mom:
        strengths.append(f"🚀 {mom}")

    # --- بازار فیوچرز (OKX) ---
    deriv = get_futures_data(symbol)
    deriv_lines = []
    if deriv["funding_rate"] is not None:
        fr = deriv["funding_rate"]
        if fr < -0.01:
            deriv_lines.append(f"💹 فاندینگ: {fr*100:.4f}% (فشار فروش — احتمال اسکوییز)")
            strengths.append(f"💹 فاندینگ منفی")
        elif fr > 0.03:
            deriv_lines.append(f"💹 فاندینگ: {fr*100:.4f}% (فشار خرید — احتمال اصلاح)")
            weaknesses.append(f"💹 فاندینگ مثبت بالا")
        else:
            deriv_lines.append(f"💹 فاندینگ: {fr*100:.4f}% (نرمال)")
    if deriv["open_interest"] is not None:
        deriv_lines.append(f"📊 بهره باز: {deriv['open_interest']:,.0f}")
    if deriv_lines:
        lines.append("🎯 <b>بازار فیوچرز:</b>")
        for dl in deriv_lines:
            lines.append(f"   {dl}")

    # --- عمق بازار (OKX) ---
    ob = get_order_book_pressure(symbol)
    if ob:
        ratio = ob["ratio"]
        if ratio > 1.5:
            lines.append(f"📋 عمق بازار: خرید/فروش={ratio:.2f} (فشار خرید)")
            strengths.append(f"📋 فشار خرید")
        elif ratio < 0.7:
            lines.append(f"📋 عمق بازار: خرید/فروش={ratio:.2f} (فشار فروش)")
            weaknesses.append(f"📋 فشار فروش")
        else:
            lines.append(f"📋 عمق بازار: خرید/فروش={ratio:.2f} (متعادل)")

    # --- لانگ/شورت (OKX) ---
    ls = get_long_short_ratio(symbol)
    if ls:
        lines.append(f"⚖️ لانگ/شورت: {ls['long']:.1f}% / {ls['short']:.1f}%")
        if ls["ratio"] > 2.0:
            weaknesses.append(f"⚖️ لانگ بیش از حد")
        elif ls["ratio"] < 0.8:
            strengths.append(f"⚖️ شورت بیش از حد")

    # --- ترس و طمع (BTC) ---
    if symbol == "BTC":
        fg = get_fear_greed()
        if fg:
            lines.append(f"😱 ترس و طمع: {fg['value']} ({fg['classification']})")

    if strengths:
        lines.append("✅ <b>نقاط قوت:</b>")
        for s in strengths:
            lines.append(f"   • {s}")
    if weaknesses:
        lines.append("⚠️ <b>نقاط ضعف:</b>")
        for w in weaknesses:
            lines.append(f"   • {w}")

    lines.append("")
    if score >= 70:
        lines.append("🎯 <b>پیشنهاد: بررسی جدی برای ورود</b> (حد ضرر ۵٪)")
    elif score >= 50:
        lines.append("🎯 <b>پیشنهاد: در لیست رصد</b>")
    elif score >= 30:
        lines.append("🎯 <b>پیشنهاد: فعلاً ورود نکنید</b>")
    else:
        lines.append("🎯 <b>پیشنهاد: صبر کنید</b>")

    return "\n".join(lines)


# ============================================================
# ارسال تلگرام
# ============================================================
def _send_one(token, chat_id, text):
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        log(f"خطا در ارسال: {e}")
        return False
def generate_accumulation_analysis(token):
    """تولید تحلیل برای سیگنال انباشت (قبل از پامپ)"""
    symbol = token["symbol"]
    score = token["accum_score"]
    details = token.get("details", {})
    lines = []

    if score >= 80:
        level = "🟢 انباشت بسیار قوی — احتمال پامپ بالا"
    elif score >= 70:
        level = "🟢 انباشت قوی — در رادار"
    elif score >= 60:
        level = "🟡 انباشت متوسط — زیر نظر"
    else:
        level = "🟠 انباشت اولیه"

    lines.append(f"<b>{symbol}</b> — {level}")
    lines.append(f"📊 امتیاز پامپ فعلی: {token.get('pump_score', 0)}/100 (هنوز پایین = فرصت)")

    signals = []

    div = details.get("divergence", "")
    if div:
        if details.get("divergence_signal") == "strong":
            signals.append(f"🔥 {div} — نشانه قوی انباشت")
        else:
            signals.append(f"📊 {div}")

    rsi = details.get("rsi", "")
    if "محدوده انباشت" in rsi:
        signals.append(f"📈 {rsi} — آماده برای حرکت")
    elif "نزدیک به انفجار" in rsi:
        signals.append(f"📈 {rsi}")

    bb = details.get("bb", "")
    if "فشردگی" in bb:
        signals.append(f"🎯 {bb} — شکست قریب‌الوقوع")
    elif "باریک" in bb:
        signals.append(f"📉 {bb}")

    vwap = details.get("vwap", "")
    if "فرصت ورود" in vwap:
        signals.append(f"💎 {vwap}")
    elif vwap:
        signals.append(f"📏 {vwap}")

    mom = details.get("momentum", "")
    if "هنوز پامپ نشده" in mom:
        signals.append(f"✅ {mom}")
    elif "در حال شروع" in mom:
        signals.append(f"⚡ {mom}")

    if signals:
        lines.append("🎯 <b>سیگنال‌های انباشت:</b>")
        for s in signals:
            lines.append(f"   • {s}")

    lines.append("")
    lines.append("💡 <b>استراتژی پیشنهادی:</b>")
    lines.append("   • این توکن هنوز پامپ نشده — فرصت ورود اولیه")
    lines.append("   • حد ضرر: ۸٪ زیر قیمت فعلی")
    lines.append("   • هدف: ۲۰-۵۰٪ سود")
    lines.append("   • حجم پیشنهادی: ۱-۲٪ سرمایه")

    return "\n".join(lines)



def send_telegram(token, chat_id, alerts, alerted_set):
    if not token or not chat_id:
        log("توکن یا chat_id تنظیم نشده.")
        return False

    new_alerts = [a for a in alerts if a["symbol"] not in alerted_set]
    if not new_alerts:
        log("هیچ هشدار جدیدی نیست.")
        return True

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    header = f"🚨 <b>هشدار پامپ — {date_str}</b>\nتعداد: <b>{len(new_alerts)}</b> توکن"
    _send_one(token, chat_id, header)
    time.sleep(2)

    for i, a in enumerate(new_alerts[:5], 1):
        msg = f"<b>🚨 هشدار #{i} — {a['symbol']}</b> (امتیاز {a['pump_score']}/100)\n"
        msg += f"💵 ${a['price']:.6f} | 📈 {a['change_24h']:+.2f}%\n\n"
        msg += generate_token_analysis(a)
        _send_one(token, chat_id, msg)
        time.sleep(3)
        alerted_set.add(a["symbol"])

    log(f"{len(new_alerts)} هشدار جدید ارسال شد.")
    return True

def send_accumulation_alerts(token, chat_id, alerts, alerted_set):
    """ارسال هشدار انباشت (قبل از پامپ)"""
    if not token or not chat_id:
        return False

    new_alerts = [a for a in alerts if a["symbol"] not in alerted_set]
    if not new_alerts:
        log("هیچ هشدار انباشت جدیدی نیست.")
        return True

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    header = f"🎯 <b>هشدار انباشت پول هوشمند — {date_str}</b>\n"
    header += f"تعداد: <b>{len(new_alerts)}</b> توکن در حال انباشت (قبل از پامپ)"
    _send_one(token, chat_id, header)
    time.sleep(2)

    for i, a in enumerate(new_alerts[:5], 1):
        msg = f"<b>🎯 #{i} {a['symbol']}</b> — امتیاز انباشت: <b>{a['accum_score']}/100</b>\n"
        msg += f"💵 ${a['price']:.6f} | 📈 {a['change_24h']:+.2f}% (هنوز پامپ نشده)\n\n"
        msg += generate_accumulation_analysis(a)
        _send_one(token, chat_id, msg)
        time.sleep(3)
        alerted_set.add(a["symbol"])

    log(f"{len(new_alerts)} هشدار انباشت ارسال شد.")
    return True


def send_daily_top5(token, chat_id, top5):
    if not token or not chat_id or not top5:
        return False

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    header = f"🏆 <b>گزارش روزانه — {date_str}</b>\n۵ توکن برتر"
    _send_one(token, chat_id, header)
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
# اجرای اسکن
# ============================================================
def run_scan():
    config = load_config()
    token = config.get("telegram_token", "")
    chat_id = config.get("chat_id", "")

    state = load_state()
    alerted_set = state["alerted"]

    current_hour_utc = datetime.utcnow().hour
    current_date = datetime.now().strftime("%Y-%m-%d")
    should_send_daily = (current_hour_utc >= 4) and (state["daily_sent"] != current_date)

    log(f"شروع اسکن انباشت... (هشدارهای امروز: {len(alerted_set)}, ساعت UTC: {current_hour_utc})")

    fetcher = MarketDataFetcher()
    coins = fetcher.get_top_coins(limit=50)

    if not coins:
        log("دریافت داده ناموفق.")
        return []

    log(f"{len(coins)} توکن دریافت شد.")
    accumulation_alerts = []
    all_results = []

    for i, coin in enumerate(coins):
        try:
            coin_id = coin.get("id", "")
            symbol = (coin.get("symbol") or "?").upper()

            if symbol in alerted_set:
                continue

            chart = fetcher.get_market_chart(coin_id, days=30)
            if not chart.get("prices"):
                continue

            # امتیاز انباشت (برای هشدار ساعتی)
            accum_score, accum_details = detect_accumulation(chart, coin)

            # امتیاز تکنیکال (برای گزارش روزانه)
            tech_score, tech_details = score_technical(chart)
            onchain_score, onchain_details = score_onchain(coin)
            final_score = tech_score * 0.55 + onchain_score * 0.45

            # چک: آیا هنوز پامپ نشده؟
            change_24h = abs(coin.get("price_change_percentage_24h_in_currency") or 0)
            not_pumped_yet = change_24h < 10

            # هشدار انباشت
            if accum_score >= 60 and not_pumped_yet:
                accumulation_alerts.append({
                    "symbol": symbol,
                    "name": coin.get("name", symbol),
                    "accum_score": round(accum_score, 1),
                    "pump_score": round(final_score, 1),
                    "price": coin.get("current_price") or 0,
                    "change_24h": coin.get("price_change_percentage_24h_in_currency") or 0,
                    "details": accum_details,
                })

            all_results.append({
                "symbol": symbol,
                "name": coin.get("name", symbol),
                "pump_score": round(final_score, 1),
                "price": coin.get("current_price") or 0,
                "change_24h": coin.get("price_change_percentage_24h_in_currency") or 0,
                "details": {**tech_details, **onchain_details},
            })
            time.sleep(1.5)
        except Exception as e:
            log(f"خطا در {i + 1}: {e}")
            continue

    all_results.sort(key=lambda x: x["pump_score"], reverse=True)

    # ارسال هشدارهای انباشت
    if accumulation_alerts:
        log(f"🎯 {len(accumulation_alerts)} توکن در حال انباشت!")
        send_accumulation_alerts(token, chat_id, accumulation_alerts, alerted_set)
        state["alerted"] = alerted_set
        save_state(state)
    else:
        log("هیچ توکنی در حال انباشت نیست.")

    # گزارش روزانه
    if should_send_daily and all_results:
        log("📊 ارسال گزارش روزانه...")
        send_daily_top5(token, chat_id, all_results[:5])
        state["daily_sent"] = current_date
        save_state(state)

    return all_results


if __name__ == "__main__":
    log("=" * 40)
    try:
        run_scan()
        log("کامل شد.")
    except Exception as e:
        log(f"خطای کلی: {e}")
