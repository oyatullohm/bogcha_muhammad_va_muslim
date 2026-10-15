"""
Telegramga xabar yuborish (sinxron, faqat standart kutubxona). Saytdan ham (Django view), botdan ham
(rejalashtirilgan vazifalar) ishlatiladi - sayt jarayoniga aiogram import qilish shart emas.
"""
import contextlib
import json
import logging
import threading
import urllib.error
import urllib.request

from django.conf import settings

log = logging.getLogger('bot')
_local = threading.local()


@contextlib.contextmanager
def collect():
    """
    Shu blok ichida yuborilgan HAMMA xabarning natijasini ro'yxatga yig'adi (sinov sahifasi uchun):
        with collect() as items:
            job()
        # items = [{'chat_id', 'ok', 'error', 'note'}, ...]
    """
    _local.items = []
    items = _local.items
    try:
        yield items
    finally:
        _local.items = None


def _call(method, payload, timeout=10):
    """Telegram API chaqiruvi. Xato bo'lsa ham dict qaytaradi: {'ok': False, 'description': sabab}."""
    token = getattr(settings, 'BOT_TOKEN', '')
    if not token:
        log.warning("BOT_TOKEN sozlanmagan - xabar yuborilmadi")
        return {'ok': False, 'description': "BOT_TOKEN sozlanmagan (settings.py)"}
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:                  # 400/403/404: Telegram sababini JSON'da yozadi
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {}
        desc = body.get('description') or f"HTTP {e.code}"
        log.warning("Telegram %s xato: %s", method, desc)
        return {'ok': False, 'description': desc}
    except Exception as e:                               # tarmoq yo'q, vaqt tugadi va h.k.
        log.warning("Telegram %s xato: %s", method, e)
        return {'ok': False, 'description': str(e)}


def send_message_result(chat_id, text, buttons=None):
    """
    buttons: [(matn, url), ...] - har biri alohida qatorda inline tugma.
    Tugma URL i Telegramga yoqmasa (masalan localhost) - tugmasiz, havolani matnga qo'shib qayta yuboradi.
    Qaytaradi: {'ok', 'error', 'note', 'message_id'}  (message_id - keyin o'chirish uchun)
    """
    payload = {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}
    if buttons:
        payload['reply_markup'] = {'inline_keyboard': [[{'text': t, 'url': u}] for t, u in buttons]}
    res = _call('sendMessage', payload)
    ok, error, note = bool(res.get('ok')), res.get('description', ''), ''

    bad_button = buttons and not ok and any(w in error.lower() for w in ('url', 'button'))
    if bad_button:                                        # faqat tugma URL xatosida havolani matnga qo'shamiz
        payload.pop('reply_markup')
        payload['text'] = text + '\n\n' + '\n'.join(f"{t}: {u}" for t, u in buttons)
        res = _call('sendMessage', payload)
        ok, error = bool(res.get('ok')), res.get('description', '')
        note = "Tugma URL i qabul qilinmadi (https kerak) - havola matn ichida yuborildi" if ok else ''

    out = {'ok': ok, 'error': '' if ok else error, 'note': note,
           'message_id': (res.get('result') or {}).get('message_id') if ok else None}
    items = getattr(_local, 'items', None)
    if items is not None:
        items.append({'chat_id': chat_id, 'ok': out['ok'], 'error': out['error'], 'note': note})
    return out


def send_message_ex(chat_id, text, buttons=None):
    r = send_message_result(chat_id, text, buttons)
    return r['ok'], r['error']


def send_message(chat_id, text, buttons=None):
    return send_message_result(chat_id, text, buttons)['ok']


def delete_message(chat_id, message_id):
    """Botning o'z xabarini chatdan o'chiradi (48 soatdan eski xabar o'chmaydi - bu normal)."""
    return bool(_call('deleteMessage', {'chat_id': chat_id, 'message_id': message_id}).get('ok'))


def get_me():
    """Token to'g'rimi va bot qaysi: {'ok': True, 'result': {'username': ...}}"""
    return _call('getMe', {})
