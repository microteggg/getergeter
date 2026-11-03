import requests
import config

API = "https://api.telegram.org/bot{}/{}"


def _ok():
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def send_message(text):
    if not _ok():
        return False, "Token/Chat ID belum diisi"
    try:
        r = requests.post(API.format(config.TELEGRAM_BOT_TOKEN, "sendMessage"), timeout=20,
                          data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000], "parse_mode": "HTML",
                                "disable_web_page_preview": "true"})
        return r.status_code == 200, None if r.status_code == 200 else r.text[:200]
    except requests.RequestException as e:
        return False, str(e)


def send_photo(path, caption):
    if not _ok():
        return False, "Token/Chat ID belum diisi"
    try:
        with open(path, "rb") as f:
            r = requests.post(API.format(config.TELEGRAM_BOT_TOKEN, "sendPhoto"), timeout=40,
                              data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption[:1000], "parse_mode": "HTML"},
                              files={"photo": f})
        return r.status_code == 200, None if r.status_code == 200 else r.text[:200]
    except (requests.RequestException, OSError) as e:
        return False, str(e)
