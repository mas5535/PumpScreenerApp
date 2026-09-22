[app]
title = Pump Screener
package.name = pumpscreener
package.domain = org.pumpscreener

source.dir = .
source.include_exts = py,png,jpg,kv,json,txt,md

version = 1.0.0

requirements = python3,kivy==2.3.1,requests,numpy,pandas,pyTelegramBotAPI,openssl,urllib3,chardet,idna,certifi

# دسترسی‌های موردنیاز
android.permissions = INTERNET,FOREGROUND_SERVICE,RECEIVE_BOOT_COMPLETED,WAKE_LOCK,POST_NOTIFICATIONS

# سرویس پس‌زمینه
services = pumpscreener:service.py

# تنظیمات نمایش
fullscreen = 0
orientation = portrait
android.api = 31
android.minapi = 21
android.arch = arm64-v8a

# آیکون (اختیاری)
# icon.filename = %(source.dir)s/assets/icon.png