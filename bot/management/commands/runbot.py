"""
Telegram botni (aiogram) va kunlik rejalashtiruvchini ishga tushiradi:
    python manage.py runbot
Doimiy ishlashi uchun serverda systemd/supervisor bilan yurgizing.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bot import services
from bot.handlers import router

log = logging.getLogger('bot')


async def scheduler_loop():
    while True:
        try:
            await asyncio.to_thread(services.run_due_jobs_threadsafe)
        except Exception:
            log.exception("Rejalashtiruvchi xatosi")
        await asyncio.sleep(30)


async def main(token):
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)
    scheduler = asyncio.create_task(scheduler_loop())
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.cancel()
        await bot.session.close()


class Command(BaseCommand):
    help = "Telegram bot + kunlik rejalashtiruvchi (aiogram)"

    def handle(self, *args, **options):
        token = getattr(settings, 'BOT_TOKEN', '')
        if not token:
            raise CommandError("settings.py da BOT_TOKEN yo'q")
        logging.basicConfig(level=logging.INFO)
        self.stdout.write(self.style.SUCCESS("Bot ishga tushdi. To'xtatish: Ctrl+C"))
        asyncio.run(main(token))
