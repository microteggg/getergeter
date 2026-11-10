"""Kirim pesan/foto Telegram. TELEGRAM_CHAT_ID boleh beberapa ID dipisah koma: 123456789,987654321"""
import requests
import config

API = "https://api.telegram.org/bot{}/{}"


def chat_ids():
    raw = (config.TELEGRAM_CHAT_ID or "").replace(";", ",").replace("\n", ",").replace(" ", ",")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _ok():
    return bool(config.TELEGRAM_BOT_TOKEN and chat_ids())


def send_message(text):
    if not _ok():
        return False, "Token/Chat ID belum diisi"
    errors = []
    for cid in chat_ids():
        try:
            r = requests.post(API.format(config.TELEGRAM_BOT_TOKEN, "sendMessage"), timeout=20,
                              data={"chat_id": cid, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"})
            if r.status_code != 200:
                errors.append(f"{cid}: {r.text[:150]}")
        except requests.RequestException as e:
            errors.append(f"{cid}: {e}")
    return not errors, "; ".join(errors) or None


def send_photo(path, caption):
    if not _ok():
        return False, "Token/Chat ID belum diisi"
    errors = []
    for cid in chat_ids():
        try:
            with open(path, "rb") as f:
                r = requests.post(API.format(config.TELEGRAM_BOT_TOKEN, "sendPhoto"), timeout=40,
                                  data={"chat_id": cid, "caption": caption[:1000], "parse_mode": "HTML"}, files={"photo": f})
            if r.status_code != 200:
                errors.append(f"{cid}: {r.text[:150]}")
        except (requests.RequestException, OSError) as e:
            errors.append(f"{cid}: {e}")
    return len(errors) < len(chat_ids()), "; ".join(errors) or None
