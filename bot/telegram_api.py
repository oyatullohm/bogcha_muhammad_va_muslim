"""
Telegramga xabar yuborish (sinxron, faqat standart kutubxona). Saytdan ham (Django view), botdan ham
(rejalashtirilgan vazifalar) ishlatiladi - sayt jarayoniga aiogram import qilish shart emas.
"""
import json
import logging
import urllib.request

from django.conf import settings

log = logging.getLogger('bot')


def _call(method, payload, timeout=10):
    token = getattr(settings, 'BOT_TOKEN', '')
    if not token:
        log.warning("BOT_TOKEN sozlanmagan - xabar yuborilmadi")
        return None
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:                      # bot egasi botni bloklagan, tarmoq xatosi va h.k.
        log.warning("Telegram %s xato: %s", method, e)
        return None


def send_message(chat_id, text, buttons=None):
    """
    buttons: [(matn, url), ...] - har biri alohida qatorda inline tugma.
    Tugma URL i Telegramga yoqmasa (masalan localhost) - tugmasiz, havolani matnga qo'shib qayta yuboradi.
    """
    payload = {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}
    if buttons:
        payload['reply_markup'] = {'inline_keyboard': [[{'text': t, 'url': u}] for t, u in buttons]}
    res = _call('sendMessage', payload)
    if res and res.get('ok'):
        return True
    if buttons:
        payload.pop('reply_markup')
        payload['text'] = text + '\n\n' + '\n'.join(f"{t}: {u}" for t, u in buttons)
        res = _call('sendMessage', payload)
        return bool(res and res.get('ok'))
    return False