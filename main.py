"""
Crypto Pump Screener - اپلیکیشن اندروید
========================================
رابط کاربری با Kivy + شروع سرویس پس‌زمینه
"""

import os
import json
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.utils import platform


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"telegram_token": "", "chat_id": ""}


class PumpScreenerApp(App):
    def build(self):
        self.title = "Pump Screener"
        self.config = load_config()

        root = BoxLayout(orientation="vertical", padding=20, spacing=15)

        title = Label(
            text="[b]Crypto Pump Screener[/b]",
            markup=True,
            font_size="24sp",
            size_hint_y=0.15,
        )
        root.add_widget(title)

        self.status_label = Label(
            text="وضعیت: آماده",
            font_size="16sp",
            size_hint_y=0.1,
        )
        root.add_widget(self.status_label)

        start_btn = Button(
            text="شروع اسکن خودکار",
            font_size="18sp",
            size_hint_y=0.15,
            background_color=(0.2, 0.7, 0.3, 1),
        )
        start_btn.bind(on_press=self.start_service)
        root.add_widget(start_btn)

        stop_btn = Button(
            text="توقف اسکن",
            font_size="18sp",
            size_hint_y=0.15,
            background_color=(0.8, 0.3, 0.3, 1),
        )
        stop_btn.bind(on_press=self.stop_service)
        root.add_widget(stop_btn)

        scroll = ScrollView(size_hint_y=0.45)
        self.log_label = Label(
            text="",
            size_hint_y=None,
            halign="left",
            valign="top",
            font_size="13sp",
        )
        self.log_label.bind(
            width=lambda *x: setattr(self.log_label, "text_size", (self.log_label.width, None))
        )
        scroll.add_widget(self.log_label)
        root.add_widget(scroll)

        return root

    def log(self, message):
        current = self.log_label.text
        self.log_label.text = f"{current}\n{message}" if current else message

    def start_service(self, instance):
        if platform == "android":
            try:
                from jnius import autoclass
                service = autoclass("org.pumpscreener.pumpscreener.ServicePumpscreener")
                mActivity = autoclass("org.kivy.android.PythonActivity").mActivity
                service.start(mActivity, "")
                self.status_label.text = "وضعیت: سرویس در حال اجرا"
                self.log("✅ سرویس شروع شد.")
            except Exception as e:
                self.log(f"❌ خطا: {e}")
        else:
            self.log("ℹ️ فقط روی اندروید.")

    def stop_service(self, instance):
        if platform == "android":
            try:
                from jnius import autoclass
                service = autoclass("org.pumpscreener.pumpscreener.ServicePumpscreener")
                mActivity = autoclass("org.kivy.android.PythonActivity").mActivity
                service.stop(mActivity)
                self.status_label.text = "وضعیت: متوقف شده"
                self.log("🛑 سرویس متوقف شد.")
            except Exception as e:
                self.log(f"❌ خطا: {e}")


if __name__ == "__main__":
    PumpScreenerApp().run()
