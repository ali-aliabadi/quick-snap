"""Lightweight dict-based UI strings for Persian (fa) / English (en).

Avoids gettext .mo compilation so it works out of the box. Language is chosen by the
APP_LANG setting (env APP_LANG=fa|en). Persian is RTL and uses the Vazirmatn font.
"""

STRINGS = {
    "fa": {
        "dir": "rtl",
        "lang": "fa",
        # join
        "join_eyebrow": "حلقه آماده است",
        "join_tagline": "یک حلقه، بدون تکرار. نشانه بگیر، بزن، و لحظه را همان‌طور که هست نگه دار.",
        "spec_exposures": "فریم",
        "spec_no_retakes": "بدون تکرار",
        "spec_tap": "برای عکس بزن",
        "label_name": "نام شما",
        "label_email": "ایمیل — اختیاری، عکس‌هایت را می‌فرستیم",
        "label_password": "رمز رویداد",
        "btn_load": "حلقه را بگذار",
        "cd_opens": "شروع تا",
        "cd_closes": "پایان تا",
        "cd_closed": "این حلقه بسته شده است",
        # camera
        "exp_left": "فریم مانده",
        "flip": "چرخش",
        "tap_expose": "برای ثبت بزن — بدون تکرار",
        "gate_opens": "عکاسی شروع می‌شود تا",
        "cam_err": "دوربین در دسترس نیست. اجازه بده و دوباره تلاش کن.",
        "upload_err": "این عکس ارسال نشد. اتصال را بررسی کن و دوباره تلاش کن.",
        "retry": "تلاش دوباره",
        # done
        "done_eyebrow": "پایان حلقه",
        "done_title": "حلقه تمام شد",
        "done_frames_pre": "تو",
        "done_frames_post": "فریم ثبت کردی در",
        "done_sending": "در حال ظاهر شدن و ارسال به",
        "done_thanks": "ممنون که عکاسی کردی",
    },
    "en": {
        "dir": "ltr",
        "lang": "en",
        "join_eyebrow": "Loaded & ready",
        "join_tagline": "A single roll, no do-overs. Point, tap, keep the moment as it happened.",
        "spec_exposures": "exposures",
        "spec_no_retakes": "no retakes",
        "spec_tap": "tap to shoot",
        "label_name": "Your name",
        "label_email": "Email — optional, we'll send your shots",
        "label_password": "Event password",
        "btn_load": "Load the roll",
        "cd_opens": "Opens in",
        "cd_closes": "Closes in",
        "cd_closed": "This roll has closed",
        "exp_left": "exp left",
        "flip": "Flip",
        "tap_expose": "Tap to expose — no retakes",
        "gate_opens": "Snapping opens in",
        "cam_err": "Can't reach the camera. Allow access, then retry.",
        "upload_err": "That shot didn't upload. Check your connection, then retry.",
        "retry": "Retry",
        "done_eyebrow": "End of roll",
        "done_title": "Roll finished",
        "done_frames_pre": "You exposed",
        "done_frames_post": "frames at",
        "done_sending": "Developing & sending to",
        "done_thanks": "Thanks for shooting",
    },
}


def get_strings(lang):
    return STRINGS.get(lang, STRINGS["en"])
