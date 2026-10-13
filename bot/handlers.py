"""aiogram handlerlari: faqat Telegram bilan muloqot. Mantiq services.py da."""
import logging

from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from asgiref.sync import sync_to_async

from . import services

log = logging.getLogger('bot')
router = Router()


def _keyboard(buttons):
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, url=u)] for t, u in buttons])


@router.message(F.text)
async def on_text(message: Message):
    """Hamma matn va buyruqlar (/start, /keldim, parol...) shu yerdan o'tadi."""
    u = message.from_user
    tg = {'id': u.id, 'first_name': u.first_name, 'last_name': u.last_name, 'username': u.username}
    reply = await sync_to_async(services.handle, thread_sensitive=True)(tg, message.text)
    await message.answer(reply.text, reply_markup=_keyboard(reply.buttons))
    if reply.delete_incoming:                         # parol chatda ko'rinib qolmasin
        try:
            await message.delete()
        except Exception:
            log.debug("Xabarni o'chirib bo'lmadi")


@router.message()
async def on_other(message: Message):
    """Rasm, stiker va h.k. - matn bilan javob beramiz."""
    await message.answer("Iltimos, matn yozing yoki /start ni bosing.")