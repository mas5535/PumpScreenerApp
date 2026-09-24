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

    def _get_with_retry(self, url, params, max_retries=3):
        """درخواست با تلاش مجدد در صورت خطا"""
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 429:  # Too Many Requests
                    wait = 10 * (attempt + 1)
                    log(f"⏳ Rate limit — صبر {wait} ثانیه...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt < max_retries - 1:
                    log(f"⚠️ تلاش {attempt+1} ناموفق: {e}")
                    time.sleep(5)
                else:
                    log(f"❌ همه تلاش‌ها ناموفق: {e}")
                    return None
        return None

    def get_top_coins(self, limit=50):
        url = f"{self.BASE_URL}/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "sparkline": False,
            "price_change_percentage": "1h,24h,7d",
        }
        data = self._get_with_retry(url, params)
        return data if data else []

    def get_market_chart(self, coin_id, days=14):
        url = f"{self.BASE_URL}/coins/{coin_id}/market_chart"
        params = {"vs_currency": "usd", "days": days}
        data = self._get_with_retry(url, params)
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
def generate_token_analysis(token):
    """تولید تحلیل متنی مفصل برای یک توکن"""
    symbol = token["symbol"]
    name = token.get("name", symbol)
    score = token["pump_score"]
    tech = token["technical_score"]
    onchain = token["onchain_score"]
    price = token["price"]
    change = token["change_24h"]
    details = token.get("details", {})

    lines = []

    # سطح سیگنال
    if score >= 60:
        level = "🟢 سیگنال قوی — فرصت بررسی"
    elif score >= 40:
        level = "🟡 سیگنال متوسط — نیاز به صبر"
    elif score >= 20:
        level = "🟠 سیگنال ضعیف — فقط رصد"
    else:
        level = "🔴 بدون سیگنال — صبر کنید"

    lines.append(f"<b>{symbol}</b> ({name})")
    lines.append(f"{level}")
    lines.append(f"💵 قیمت: ${price:.6f} | تغییر ۲۴h: {change:+.2f}%")
    lines.append(f"🎯 امتیاز کل: <b>{score}/100</b>")
    lines.append(f"   • امتیاز تکنیکال: {tech}/100")
    lines.append(f"   • امتیاز آن‌چین: {onchain}/100")
    lines.append("")

    strengths = []
    weaknesses = []
    insights = []

    # حجم
    vol = details.get("volume", "")
    if "قوی" in vol:
        strengths.append(f"📊 {vol} — ورود پول قوی به بازار")
    elif "خوب" in vol:
        strengths.append(f"📊 {vol} — افزایش علاقه خریداران")
    elif "خفیف" in vol:
        insights.append(f"📊 {vol} — فعالیت کمی بیشتر از حد معمول")
    else:
        weaknesses.append(f"📊 {vol} — حجم معاملات معمولی، بدون هیجان")

    # RSI
    rsi = details.get("rsi", "")
    if "اشباع فروش" in rsi:
        strengths.append(f"📈 {rsi} — قیمت بیش از حد پایین، احتمال برگشت بالا")
    elif "محدوده پامپ" in rsi:
        strengths.append(f"📈 {rsi} — مومنتوم صعودی فعال")
    else:
        insights.append(f"📈 {rsi} — در محدوده خنثی")

    # باند بولینگر
    bb = details.get("bb", "")
    if "فشردگی" in bb:
        strengths.append(f"📉 {bb} — فشردگی شدید، شکست قریب‌الوقوع")
    elif "باریک" in bb:
        strengths.append(f"📉 {bb} — باند در حال تنگ شدن")
    elif "باز" in bb:
        weaknesses.append(f"📉 {bb} — نوسان بالا، ریسک زیاد")

    # MACD
    macd = details.get("macd", "")
    if "صعودی" in macd or "کراس" in macd:
        strengths.append(f"📊 {macd} — تغییر مومنتوم به صعودی")
    elif "نزولی" in macd:
        weaknesses.append(f"📊 {macd} — مومنتوم نزولی")

    # نسبت حجم/مارکت‌کپ
    vm = details.get("vol_mcap", "")
    if "غیرعادی" in vm:
        strengths.append(f"🐋 {vm} — احتمال فعالیت نهنگ‌ها")
    elif "بالا" in vm:
        strengths.append(f"🐋 {vm} — نسبت حجم به مارکت‌کپ قابل توجه")

    # شتاب قیمت
    mom = details.get("momentum", "")
    if "بسیار بالا" in mom:
        strengths.append(f"🚀 {mom} — شتاب قیمت بسیار قوی")
    elif "بالا" in mom:
        strengths.append(f"🚀 {mom} — شتاب قیمت بالا")
    elif "متوسط" in mom:
        insights.append(f"🚀 {mom} — شتاب متوسط")

    # چاپ
    if strengths:
        lines.append("✅ <b>نقاط قوت:</b>")
        for s in strengths:
            lines.append(f"   {s}")
    if insights:
        lines.append("🔍 <b>نکات قابل توجه:</b>")
        for i in insights:
            lines.append(f"   {i}")
    if weaknesses:
        lines.append("⚠️ <b>نقاط ضعف:</b>")
        for w in weaknesses:
            lines.append(f"   {w}")

    # جمع‌بندی نهایی
    lines.append("")
    if score >= 60:
        lines.append("🎯 <b>پیشنهاد: بررسی جدی برای ورود</b>")
        lines.append("   (حد ضرر ۵٪ زیر قیمت فعلی)")
    elif score >= 40:
        lines.append("🎯 <b>پیشنهاد: در لیست رصد قرار دهید</b>")
        lines.append("   (منتظر تاییدیه سیگنال بمانید)")
    elif score >= 20:
        lines.append("🎯 <b>پیشنهاد: فعلاً ورود نکنید</b>")
        lines.append("   (سیگنال کافی نیست)")
    else:
        lines.append("🎯 <b>پیشنهاد: صبر کنید</b>")
        lines.append("   (بدون سیگنال)")

    return "\n".join(lines)
def split_message(text, max_length=3800):
    """تقسیم پیام طولانی به چند بخش"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_length:
            parts.append(current)
            current = line
        else:
            current += "\n" + line if current else line
    
    if current:
        parts.append(current)
    
    return parts


def send_single_message(token, chat_id, text):
    """ارسال یک پیام ساده"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"❌ خطا در ارسال: {e}")
        return False


def send_telegram(token, chat_id, alerts, top5):
    if not token or not chat_id:
        log("⚠️ توکن یا chat_id تنظیم نشده.")
        return False

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    # ---------- پیام اول: هشدارها ----------
    if alerts:
        header = (f"🚨 <b>هشدار پامپ — {date_str}</b>\n"
                  f"تعداد: <b>{len(alerts)}</b> توکن با امتیاز بالای ۶۰")
        send_single_message(token, chat_id, header)
        time.sleep(1)

        for i, a in enumerate(alerts[:10], 1):
            msg = f"<b>🚨 هشدار #{i}</b>\n\n" + generate_token_analysis(a)
            send_single_message(token, chat_id, msg)
            time.sleep(1)
    else:
        msg = (f"ℹ️ <b>گزارش {date_str}</b>\n"
               f"امروز توکنی با امتیاز بالای ۶۰ شناسایی نشد.")
        send_single_message(token, chat_id, msg)
        time.sleep(1)

    # ---------- پیام دوم: ۵ توکن برتر (هر کدام جداگانه) ----------
    if not top5:
        log("⚠️ لیست top5 خالی است.")
        return True

    log(f"📤 ارسال {len(top5)} توکن برتر...")

    header = (f"🏆 <b>۵ توکن برتر امروز — {date_str}</b>\n"
              f"تعداد: {len(top5)} توکن")
    send_single_message(token, chat_id, header)
    time.sleep(1)

    for i, a in enumerate(top5, 1):
        symbol = a.get("symbol", "?")
        score = a.get("pump_score", 0)
        msg = f"<b>🏆 رتبه #{i} — {symbol}</b> (امتیاز {score}/100)\n\n"
        msg += generate_token_analysis(a)
        log(f"  ارسال #{i} {symbol} — طول: {len(msg)} کاراکتر")
        success = send_single_message(token, chat_id, msg)
        log(f"  نتیجه: {'✅ موفق' if success else '❌ ناموفق'}")
        time.sleep(2)  # ۲ ثانیه صبر بین پیام‌ها

    log("✅ همه پیام‌ها ارسال شد.")
    return True


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
    coins = [c for c in coins if (c.get("total_volume") or 0) > 5_000_000]

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
            time.sleep(1.5)
        except Exception as e:
            log(f"⚠️ خطا در تحلیل توکن {i + 1}: {e}")
            continue

    results.sort(key=lambda x: x["pump_score"], reverse=True)
    alerts = [r for r in results if r["pump_score"] >= 60]
    log(f"📊 {len(alerts)} توکن با امتیاز بالای ۶۰.")
    top5 = results[:5]
        log(f"🔍 DEBUG: تعداد نتایج = {len(results)}")
    log(f"🔍 DEBUG: تعداد top5 = {len(top5)}")
    for i, t in enumerate(top5):
        log(f"  #{i+1}: {t['symbol']} — امتیاز {t['pump_score']}")
    send_telegram(token, chat_id, alerts, top5)
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
