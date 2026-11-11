"""Kirim pesan/foto Telegram. TELEGRAM_CHAT_ID boleh beberapa ID dipisah koma: 123456789,987654321

v19:
- Sukses HANYA bila Telegram membalas {"ok": true}; message_id disimpan di LAST (untuk log status).
- Bila HTML ditolak ("can't parse entities") pesan dikirim ulang sebagai teks biasa (tidak hilang).
- Caption foto > 1024 karakter: foto dikirim dengan caption pendek lalu teks lengkap sebagai pesan.
- Signature tetap (ok, err) agar simulate.py / kode lama kompatibel.
"""
import re

import requests

import config

API = "https://api.telegram.org/bot{}/{}"
LAST = {"ids": [], "error": None}


def chat_ids():
    raw = (config.TELEGRAM_CHAT_ID or "").replace(";", ",").replace("\n", ",").replace(" ", ",")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _token():
    tok = (config.TELEGRAM_BOT_TOKEN or "").strip()
    return tok[3:] if tok.lower().startswith("bot") else tok


def configured():
    return bool(_token() and chat_ids())


_ok = configured


def _plain(text):
    return re.sub(r"<[^>]+>", "", text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _post(method, data, files=None, timeout=25):
    """Return (ok, message_id, err)."""
    try:
        r = requests.post(API.format(_token(), method), data=data, files=files, timeout=timeout)
    except requests.RequestException as e:
        return False, None, f"{e.__class__.__name__}"
    try:
        js = r.json()
    except ValueError:
        js = {}
    if r.status_code == 200 and js.get("ok"):
        return True, (js.get("result") or {}).get("message_id"), None
    return False, None, f"HTTP {r.status_code} {js.get('description') or r.text[:120]}"


def _send_text(cid, text):
    ok, mid, err = _post("sendMessage", {"chat_id": cid, "text": text[:4000], "parse_mode": "HTML",
                                         "disable_web_page_preview": "true"})
    if not ok and err and "parse entities" in err:
        ok, mid, err = _post("sendMessage", {"chat_id": cid, "text": _plain(text)[:4000],
                                             "disable_web_page_preview": "true"})
    return ok, mid, err


def send_message(text):
    LAST.update(ids=[], error=None)
    if not configured():
        LAST["error"] = "Token/Chat ID belum diisi di environment engine"
        return False, LAST["error"]
    errors = []
    for cid in chat_ids():
        ok, mid, err = _send_text(cid, text)
        if ok:
            LAST["ids"].append(mid)
        else:
            errors.append(f"{cid}: {err}")
    LAST["error"] = "; ".join(errors) or None
    return not errors, LAST["error"]


def send_photo(path, caption):
    LAST.update(ids=[], error=None)
    if not configured():
        LAST["error"] = "Token/Chat ID belum diisi di environment engine"
        return False, LAST["error"]
    errors = []
    long_caption = len(_plain(caption)) > 1000
    for cid in chat_ids():
        cap = caption.split("\n")[0][:200] if long_caption else caption
        try:
            with open(path, "rb") as fh:
                ok, mid, err = _post("sendPhoto", {"chat_id": cid, "caption": cap, "parse_mode": "HTML"},
                                     files={"photo": fh}, timeout=45)
            if not ok and err and "parse entities" in err:
                with open(path, "rb") as fh:
                    ok, mid, err = _post("sendPhoto", {"chat_id": cid, "caption": _plain(cap)[:1000]},
                                         files={"photo": fh}, timeout=45)
        except OSError as e:
            ok, mid, err = False, None, f"file: {e}"
        if ok and long_caption:
            ok, mid2, err = _send_text(cid, caption)
            mid = mid2 or mid
        if ok:
            LAST["ids"].append(mid)
        else:
            errors.append(f"{cid}: {err}")
    LAST["error"] = "; ".join(errors) or None
    return not errors, LAST["error"]
