"""
سرویس پس‌زمینه اسکنر پامپ
==========================
این سرویس در پس‌زمینه اندروید اجرا می‌شود
و هر ۲۴ ساعت یک‌بار اسکن را انجام می‌دهد.
"""

import os
import json
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime

# --- مسیرهای ذخیره‌سازی (سازگار با اندروید) ---
def get_base_dir():
    if os.path.exists("/data/data/org.pumpscreener.pumpscreener"):
        return "/data/data/org.pumpscreener.pumpscreener/files"
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()
os.makedirs(BASE_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DATA_PATH = os.path.join(BASE_DIR, "training_data.csv")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# بخش ۱: دریافت داده از CoinGecko
# ============================================================
class MarketDataFetcher:
    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PumpScreener/1.0"})

    def get_top_coins(self, limit=100):
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
            df = pd.DataFrame(resp.json())
            df = df[
                (df["market_cap"] >= 50_000_000) &
                (df["total_volume"] >= 5_000_000)
            ].copy()
            return df
        except Exception as e:
            log(f"خطا در دریافت داده: {e}")
            return pd.DataFrame()

    def get_ohlc(self, coin_id, days=14):
        url = f"{self.BASE_URL}/coins/{coin_id}/ohlc"
        params = {"vs_currency": "usd", "days": days}
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            df = pd.DataFrame(resp.json(), columns=["timestamp", "open", "high", "low", "close"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return df
        except Exception:
            return pd.DataFrame()


# ============================================================
# بخش ۲: شاخص‌های تکنیکال
# ============================================================
class TechnicalIndicators:
    @staticmethod
    def calculate_rsi(df, period=14):
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def calculate_bollinger_bands(df, period=20, std_dev=2):
        sma = df["close"].rolling(window=period).mean()
        std = df["close"].rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        bandwidth = (upper - lower) / sma
        return upper, lower, bandwidth

    @staticmethod
    def calculate_macd(df, fast=12, slow=26, signal=9):
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram


# ============================================================
# بخش ۳: امتیازدهی پامپ
# ============================================================
class PumpScorer:
    def __init__(self):
        self.indicators = TechnicalIndicators()

    def score_technical(self, df_ohlc):
        if df_ohlc.empty or len(df_ohlc) < 30:
            return 0, {}

        details = {}
        score = 0

        # حجم
        vol_ratio = df_ohlc["volume"] / df_ohlc["volume"].rolling(20).mean()
        current_vol_ratio = vol_ratio.iloc[-1] if not vol_ratio.empty else 0

        if current_vol_ratio >= 3.0:
            score += 40
            details["volume"] = f"حجم {current_vol_ratio:.1f}x (قوی)"
        elif current_vol_ratio >= 2.0:
            score += 25
            details["volume"] = f"حجم {current_vol_ratio:.1f}x (خوب)"
        elif current_vol_ratio >= 1.5:
            score += 10
            details["volume"] = f"حجم {current_vol_ratio:.1f}x (خفیف)"
        else:
            details["volume"] = f"حجم {current_vol_ratio:.1f}x (عادی)"

        # RSI
        rsi = self.indicators.calculate_rsi(df_ohlc)
        current_rsi = rsi.iloc[-1] if not rsi.empty else 50

        if 25 <= current_rsi <= 35:
            score += 20
            details["rsi"] = f"RSI={current_rsi:.0f} (اشباع فروش)"
        elif 60 <= current_rsi <= 85:
            score += 10
            details["rsi"] = f"RSI={current_rsi:.0f} (محدوده پامپ)"
        else:
            details["rsi"] = f"RSI={current_rsi:.0f}"

        # باند بولینگر
        upper, lower, bandwidth = self.indicators.calculate_bollinger_bands(df_ohlc)
        if not bandwidth.empty and len(bandwidth.dropna()) > 20:
            current_bw = bandwidth.iloc[-1]
            bw_percentile = (bandwidth.dropna() < current_bw).mean() * 100
            if bw_percentile <= 20:
                score += 20
                details["bb"] = f"فشردگی باند (صدک {bw_percentile:.0f})"
            elif bw_percentile <= 40:
                score += 10
                details["bb"] = f"باند نسبتاً باریک (صدک {bw_percentile:.0f})"
            else:
                details["bb"] = f"باند باز (صدک {bw_percentile:.0f})"

        # MACD
        macd_line, signal_line, hist = self.indicators.calculate_macd(df_ohlc)
        if not hist.empty and len(hist.dropna()) > 5:
            if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
                score += 20
                details["macd"] = "کراس صعودی MACD"
            elif hist.iloc[-1] > hist.iloc[-2] and hist.iloc[-1] > 0:
                score += 10
                details["macd"] = "MACD صعودی"
            else:
                details["macd"] = "MACD خنثی/نزولی"

        return min(score, 100), details

    def score_onchain(self, coin_data):
        score = 0
        details = {}

        volume = coin_data.get("total_volume", 0)
        market_cap = coin_data.get("market_cap", 1)
        vol_mcap_ratio = volume / market_cap if market_cap > 0 else 0

        if vol_mcap_ratio >= 0.15:
            score += 50
            details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={vol_mcap_ratio:.3f} (غیرعادی)"
        elif vol_mcap_ratio >= 0.10:
            score += 30
            details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={vol_mcap_ratio:.3f} (بالا)"
        elif vol_mcap_ratio >= 0.05:
            score += 15
            details["vol_mcap"] = f"نسبت حجم/مارکت‌کپ={vol_mcap_ratio:.3f}"

        change_1h = abs(coin_data.get("price_change_percentage_1h_in_currency", 0) or 0)
        change_24h = abs(coin_data.get("price_change_percentage_24h_in_currency", 0) or 0)
        momentum = (change_1h * 0.6) + (change_24h * 0.4)

        if momentum >= 5:
            score += 50
            details["momentum"] = f"شتاب قیمت={momentum:.1f}% (بسیار بالا)"
        elif momentum >= 3:
            score += 35
            details["momentum"] = f"شتاب قیمت={momentum:.1f}% (بالا)"
        elif momentum >= 1.5:
            score += 20
            details["momentum"] = f"شتاب قیمت={momentum:.1f}% (متوسط)"

        return min(score, 100), details


# ============================================================
# بخش ۴: ارسال هشدار تلگرام
# ============================================================
def send_telegram_alert(token, chat_id, alerts):
    """ارسال پیام هشدار به تلگرام"""
    if not token or not chat_id:
        log("⚠️ توکن یا chat_id تنظیم نشده.")
        return False

    if not alerts:
        msg = "ℹ️ اسکن روزانه: هیچ توکنی با امتیاز بالا شناسایی نشد."
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=15)
        except Exception:
            pass
        return True

    lines = [
        f"🚨 <b>هشدار پامپ — {datetime.now().strftime('%Y-%m-%d %H:%M')}</b>",
        f"تعداد: <b>{len(alerts)}</b> توکن\n",
    ]

    for i, a in enumerate(alerts[:10], 1):
        emoji = "🔴" if a["pump_score"] >= 85 else "🟠" if a["pump_score"] >= 70 else "🟡"
        lines.append(f"{emoji} <b>#{i} {a['symbol']}</b> — امتیاز: <b>{a['pump_score']}/100</b>")
        lines.append(f"   💰 قیمت: ${a['price']:.6f}")
        lines.append(f"   📈 تغییر ۲۴h: {a['change_24h']:+.2f}%")
        for detail in a.get("details", {}).values():
            lines.append(f"   • {detail}")
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
# بخش ۵: لاگ‌گیری
# ============================================================
def log(message):
    """ثبت پیام در فایل لاگ و کنسول"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        log_path = os.path.join(BASE_DIR, "screener.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ============================================================
# بخش ۶: اجرای اسکن
# ============================================================
def run_scan():
    """اجرای یک اسکن کامل"""
    config = load_config()
    token = config.get("telegram_token", "")
    chat_id = config.get("chat_id", "")

    log("🔍 شروع اسکن...")
    fetcher = MarketDataFetcher()
    scorer = PumpScorer()

    coins_df = fetcher.get_top_coins(limit=80)
    if coins_df.empty:
        log("❌ دریافت داده ناموفق بود.")
        return []

    log(f"✅ {len(coins_df)} توکن دریافت شد.")
    results = []

    for i, (_, coin) in enumerate(coins_df.iterrows()):
        coin_id = coin["id"]
        symbol = coin["symbol"].upper()
        log(f"  تحلیل {i+1}/{len(coins_df)}: {symbol}")

        df_ohlc = fetcher.get_ohlc(coin_id, days=14)
        if df_ohlc.empty:
            continue

        tech_score, tech_details = scorer.score_technical(df_ohlc)
        onchain_score, onchain_details = scorer.score_onchain(coin)
        final_score = tech_score * 0.55 + onchain_score * 0.45

        results.append({
            "symbol": symbol,
            "name": coin["name"],
            "pump_score": round(final_score, 1),
            "technical_score": round(tech_score, 1),
            "onchain_score": round(onchain_score, 1),
            "price": coin["current_price"],
            "market_cap": coin["market_cap"],
            "volume_24h": coin["total_volume"],
            "change_24h": coin.get("price_change_percentage_24h_in_currency", 0),
            "details": {**tech_details, **onchain_details},
        })
        time.sleep(0.3)

    results.sort(key=lambda x: x["pump_score"], reverse=True)

    # فیلتر و ارسال هشدار
    alerts = [r for r in results if r["pump_score"] >= 60]
    log(f"📊 {len(alerts)} توکن با امتیاز بالای ۶۰ شناسایی شد.")
    send_telegram_alert(token, chat_id, alerts)

    # ذخیره نتایج برای آموزش مدل
    try:
        pd.DataFrame(results).to_csv(DATA_PATH, index=False)
    except Exception:
        pass

    return alerts


# ============================================================
# بخش ۷: حلقه اصلی سرویس
# ============================================================
def main():
    """حلقه اصلی سرویس پس‌زمینه"""
    log("=" * 50)
    log("🚀 سرویس اسکنر پامپ شروع شد.")
    log(f"📁 مسیر داده: {BASE_DIR}")

    while True:
        try:
            run_scan()
            log("⏳ اسکن بعدی تا ۲۴ ساعت دیگر...")
            time.sleep(24 * 60 * 60)
        except Exception as e:
            log(f"❌ خطای غیرمنتظره: {e}")
            time.sleep(60 * 60)


if __name__ == "__main__":
    main()