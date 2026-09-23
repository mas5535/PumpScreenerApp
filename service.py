"""
سرویس پس‌زمینه اسکنر پامپ — نسخه اصلاح‌شده
"""

import os
import json
import time
import statistics
import requests
from datetime import datetime


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)


def load_config():
    """خواندن تنظیمات از Environment Variables"""
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    return {"telegram_token": token, "chat_id": chat_id}


# ============================================================
# دریافت داده از CoinGecko
# ============================================================
class MarketDataFetcher:
    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PumpScreener/1.0"})

    def get_top_coins(self, limit=80):
        url = f"{self.BASE_URL}/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "sparkline": False,
            "price_change_percentage": "1h,24h,7d",
        }
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log(f"خطا در دریافت داده: {e}")
            return []

    def get_market_chart(self, coin_id, days=14):
        url = f"{self.BASE_URL}/coins/{coin_id}/market_chart"
        params = {"vs_currency": "usd", "days": days}
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            prices = [p[1] for p in data.get("prices", [])]
            volumes = [v[1] for v in data.get("total_volumes", [])]
            return {"prices": prices, "volumes": volumes}
        except Exception:
            return {"prices": [], "volumes": []}


# ============================================================
# شاخص‌های تکنیکال
# ============================================================
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains = []
    losses = []
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
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


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
    percentile = sum(1 for bw in all_bw if bw < bandwidth) / len(all_bw) * 100 if all_bw else 50
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


# ============================================================
# امتیازدهی
# ============================================================
def score_technical(chart_data):
    prices = chart_data.get("prices", [])
    volumes = chart_data.get("volumes", [])
    if len(prices) < 30:
        return 0, {}

    details = {}
    score = 0

    vol_ratio = calculate_volume_ratio(volumes)
    if vol_ratio >= 3.0:
        score += 40
        details["volume"] = f"حجم {vol_ratio:.1f}x (قوی)"
    elif vol_ratio >= 2.0:
        score += 25
        details["volume"] = f"حجم {vol_ratio:.1f}x (خوب)"
    elif vol_ratio >= 1.5:
        score += 10
        details["volume"] = f"حجم {vol_ratio:.1f}x (خفیف)"
    else:
        details["volume"] = f"حجم {vol_ratio:.1f}x (عادی)"

    rsi = calculate_rsi(prices)
    if 25 <= rsi <= 35:
        score += 20
        details["rsi"] = f"RSI={rsi:.0f} (اشباع فروش)"
    elif 60 <= rsi <= 85:
        score += 10
        details["rsi"] = f"RSI={rsi:.0f} (محدوده پامپ)"
    else:
        details["rsi"] = f"RSI={rsi:.0f}"

    bw, percentile = calculate_bollinger(prices)
    if percentile <= 20:
        score += 20
        details["bb"] = f"فشردگی باند (صدک {percentile:.0f})"
    elif percentile <= 40:
        score += 10
        details["bb"] = f"باند نسبتاً باریک (صدک {percentile:.0f})"
    else:
        details["bb"] = f"باند باز (صدک {percentile:.0f})"

    macd, signal = calculate_macd(prices)
    if macd > signal and macd > 0:
        score += 20
        details["macd"] = "MACD صعودی"
    elif macd > signal:
        score += 10
        details["macd"] = "MACD کراس صعودی"
    else:
        details["macd"] = "MACD خنثی/نزولی"

    return min(score, 100), details


def score_onchain(coin):
    score = 0
    details = {}
    volume = coin.get("total_volume") or 0
    market_cap = coin.get("market_cap") or 1
    ratio = volume / market_cap if market_cap > 0 else 0

    if ratio >= 0.15:
        score += 50
        details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={ratio:.3f} (غیرعادی)"
    elif ratio >= 0.10:
        score += 30
        details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={ratio:.3f} (بالا)"
    elif ratio >= 0.05:
        score += 15
        details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={ratio:.3f}"

    change_1h = abs(coin.get("price_change_percentage_1h_in_currency") or 0)
    change_24h = abs(coin.get("price_change_percentage_24h_in_currency") or 0)
    momentum = (change_1h * 0.6) + (change_24h * 0.4)

    if momentum >= 5:
        score += 50
        details["momentum"] = f"شتاب={momentum:.1f}% (بسیار بالا)"
    elif momentum >= 3:
        score += 35
        details["momentum"] = f"شتاب={momentum:.1f}% (بالا)"
    elif momentum >= 1.5:
        score += 20
        details["momentum"] = f"شتاب={momentum:.1f}% (متوسط)"

    return min(score, 100), details


# ============================================================
# ارسال تلگرام
# ============================================================
def send_telegram(token, chat_id, alerts):
    if not token or not chat_id:
        log("⚠️ توکن یا chat_id تنظیم نشده.")
        return False

    if not alerts:
        msg = "ℹ️ اسکن روزانه: هیچ توکنی با امتیاز بالا شناسایی نشد."
    else:
        lines = [
            f"🚨 <b>هشدار پامپ — {datetime.now().strftime('%Y-%m-%d %H:%M')}</b>",
            f"تعداد: <b>{len(alerts)}</b> توکن\n",
        ]
        for i, a in enumerate(alerts[:10], 1):
            emoji = "🔴" if a["pump_score"] >= 85 else "🟠" if a["pump_score"] >= 70 else "🟡"
            lines.append(f"{emoji} <b>#{i} {a['symbol']}</b> — امتیاز: <b>{a['pump_score']}/100</b>")
            lines.append(f"   💰 قیمت: ${a['price']:.6f}")
            lines.append(f"   📈 تغییر ۲۴h: {a['change_24h']:+.2f}%")
            for d in a.get("details", {}).values():
                lines.append(f"   • {d}")
            lines.append("")
        lines.append("⚠️ <i>این هشدار پیش‌بینی قطعی نیست.</i>")
        msg = "\n".join(lines)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        log("✅ هشدار تلگرام ارسال شد.")
        return True
    except Exception as e:
        log(f"❌ خطا در ارسال تلگرام: {e}")
        return False


# ============================================================
# اجرای اسکن
# ============================================================
def run_scan():
    config = load_config()
    token = config.get("telegram_token", "")
    chat_id = config.get("chat_id", "")

    log("🔍 شروع اسکن...")
    fetcher = MarketDataFetcher()
    coins = fetcher.get_top_coins(limit=80)

    if not coins:
        log("❌ دریافت داده ناموفق.")
        return []

    log(f"✅ {len(coins)} توکن دریافت شد.")
    results = []

    for i, coin in enumerate(coins):
        try:
            coin_id = coin.get("id", "")
            symbol = (coin.get("symbol") or "?").upper()
            if i % 10 == 0:
                log(f"  تحلیل {i + 1}/{len(coins)}: {symbol}")

            chart = fetcher.get_market_chart(coin_id, days=14)
            if not chart.get("prices"):
                continue

            tech_score, tech_details = score_technical(chart)
            onchain_score, onchain_details = score_onchain(coin)
            final_score = tech_score * 0.55 + onchain_score * 0.45

            results.append({
                "symbol": symbol,
                "name": coin.get("name", symbol),
                "pump_score": round(final_score, 1),
                "technical_score": round(tech_score, 1),
                "onchain_score": round(onchain_score, 1),
                "price": coin.get("current_price") or 0,
                "market_cap": coin.get("market_cap") or 0,
                "volume_24h": coin.get("total_volume") or 0,
                "change_24h": coin.get("price_change_percentage_24h_in_currency") or 0,
                "details": {**tech_details, **onchain_details},
            })
            time.sleep(0.3)
        except Exception as e:
            log(f"⚠️ خطا در تحلیل توکن {i + 1}: {e}")
            continue

    results.sort(key=lambda x: x["pump_score"], reverse=True)
    alerts = [r for r in results if r["pump_score"] >= 60]
    log(f"📊 {len(alerts)} توکن با امتیاز بالای ۶۰.")
    send_telegram(token, chat_id, alerts)
    return alerts


# ============================================================
# اجرای اصلی (برای Tasks - فقط یک بار اجرا می‌شود)
# ============================================================
if __name__ == "__main__":
    log("=" * 50)
    log("🚀 اسکن شروع شد.")
    try:
        run_scan()
        log("✅ اسکن با موفقیت کامل شد.")
    except Exception as e:
        log(f"❌ خطای کلی: {e}")
