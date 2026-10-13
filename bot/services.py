"""
Bot mantig'i (sinxron, Django ORM bilan). aiogram handlerlari (handlers.py) shularni chaqiradi.
Mantiq alohida turgani uchun Telegramsiz ham sinash mumkin.
"""
import hmac
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import timedelta
from datetime import time as dtime

from django.conf import settings
from django.db import connections
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from main import attendance
from main.models import Teacher, TeacherAttendance, User
from main.notifications import build_notifications, clear_nav_cache

from .models import BotJobRun, BotUser
from .telegram_api import send_message

log = logging.getLogger('bot')

MAX_FAILS = 5            # shuncha xato paroldan keyin
BLOCK_MINUTES = 30       # shuncha daqiqa bloklanadi
CATCHUP_MINUTES = 45     # bot ertaroq o'chib turgan bo'lsa, vazifa vaqtidan shuncha daqiqagacha bajariladi


@dataclass
class Reply:
    text: str
    buttons: list = field(default_factory=list)      # [(matn, url)]
    delete_incoming: bool = False                    # foydalanuvchi yozgan xabarni (parol) o'chirish


# ------------------------------------------------------------ KIM EKANINI ANIQLASH
def find_account(telegram_id):
    t = Teacher.objects.filter(telegram_id=telegram_id, is_active=True).first()
    if t:
        return 'teacher', t
    u = User.objects.filter(telegram_id=telegram_id, is_active=True).first()
    if u:
        return 'user', u
    return None, None


def parse_command(text):
    if text.startswith('/'):
        return text.split()[0][1:].split('@')[0].lower()
    return None


def touch_bot_user(tg):
    bu, _ = BotUser.objects.get_or_create(telegram_id=tg['id'])
    bu.first_name, bu.last_name, bu.username = (tg.get('first_name') or ''), (tg.get('last_name') or ''), (tg.get('username') or '')
    bu.save(update_fields=['first_name', 'last_name', 'username'])
    return bu


# ------------------------------------------------------------ PAROL
def check_password(bu, text):
    """{'status': ok | wrong | blocked | no_password, 'left': qolgan_urinish, 'minutes': blok_daqiqa}"""
    now = timezone.now()
    if bu.blocked_until and bu.blocked_until > now:
        return {'status': 'blocked', 'minutes': math.ceil((bu.blocked_until - now).total_seconds() / 60)}
    password = getattr(settings, 'BOT_PASSWORD', '')
    if not password:
        return {'status': 'no_password'}
    if hmac.compare_digest(text.strip().encode(), password.encode()):
        bu.verified, bu.fails, bu.blocked_until, bu.verified_at = True, 0, None, now
        bu.save(update_fields=['verified', 'fails', 'blocked_until', 'verified_at'])
        return {'status': 'ok'}
    bu.fails += 1
    if bu.fails >= MAX_FAILS:
        bu.fails, bu.blocked_until = 0, now + timedelta(minutes=BLOCK_MINUTES)
        bu.save(update_fields=['fails', 'blocked_until'])
        return {'status': 'blocked', 'minutes': BLOCK_MINUTES}
    bu.save(update_fields=['fails'])
    return {'status': 'wrong', 'left': MAX_FAILS - bu.fails}


def notify_admins_new_user(bu):
    ids = list(User.objects.filter(is_active=True, telegram_id__isnull=False)
               .filter(Q(role='admin') | Q(is_superuser=True)).values_list('telegram_id', flat=True))
    link = attendance.site_url(reverse('teacher_list')) + '#tarbiyachilar'
    uname = f" (@{bu.username})" if bu.username else ''
    for chat_id in ids:
        send_message(chat_id, f"🆕 Botga yangi odam kirdi: {bu.display_name}{uname}\nTelegram ID: {bu.telegram_id}\n\n"
                             "Saytda «Tarbiyachilar» bo'limidan uni tarbiyachiga yoki xodimga bog'lang.",
                     [("🔗 Saytda ulash", link)])


# ------------------------------------------------------------ XABARGA JAVOB
def handle(tg, text):
    """tg = {'id', 'first_name', 'last_name', 'username'}; qaytaradi: Reply"""
    text = (text or '').strip()
    cmd = parse_command(text)
    kind, acc = find_account(tg['id'])
    if kind == 'teacher':
        return _teacher_reply(acc, cmd)
    if kind == 'user':
        return _user_reply(acc, cmd)

    bu = touch_bot_user(tg)
    if bu.verified:
        return Reply(f"Siz botga kirgansiz ✅\n\nSizning Telegram ID: {bu.telegram_id}\n\n"
                     "Administrator sizni tizimga ulashini kuting. Ulangach shu yerga xabar keladi.")
    if cmd or not text:                               # /start va boshqa buyruqlar -> parol so'raymiz
        if bu.blocked_until and bu.blocked_until > timezone.now():
            return Reply("Ko'p marta xato kiritildi. Birozdan keyin qayta urinib ko'ring.")
        return Reply("Assalomu alaykum! 👋\n\nBu yopiq bot. Davom etish uchun parolni kiriting:")

    res = check_password(bu, text)                    # oddiy matn = parol urinishi
    if res['status'] == 'ok':
        notify_admins_new_user(bu)
        return Reply(f"✅ Parol to'g'ri!\n\nSizning Telegram ID: {bu.telegram_id}\n\n"
                     "Administrator sizni tizimga ulaydi, ulangach shu yerga xabar keladi.", delete_incoming=True)
    if res['status'] == 'wrong':
        return Reply(f"❌ Parol noto'g'ri. Qolgan urinishlar: {res['left']}", delete_incoming=True)
    if res['status'] == 'blocked':
        return Reply(f"⛔ Juda ko'p xato urinish. {res['minutes']} daqiqadan keyin qayta urinib ko'ring.", delete_incoming=True)
    return Reply("Bot paroli sozlanmagan. Administratorga murojaat qiling.", delete_incoming=True)


def _teacher_reply(t, cmd):
    today = timezone.localdate()
    att = TeacherAttendance.objects.filter(teacher=t, date=today).first()
    if cmd == 'id':
        return Reply(f"Telegram ID: {t.telegram_id}")
    if cmd == 'keldim':
        if att and att.check_in:
            return Reply(f"✅ Siz bugun {timezone.localtime(att.check_in):%H:%M} da kelgansiz.\nKetishni belgilash: /ketdim")
        return Reply("Bog'chaga kelganingizda tugmani bosing 👇", [("📍 Keldim", attendance.build_url(t, 'in'))])
    if cmd == 'ketdim':
        if not (att and att.check_in):
            return Reply("Avval kelganingizni belgilang: /keldim")
        return Reply("Ketayotganingizda tugmani bosing 👇", [("🏠 Ketdim", attendance.build_url(t, 'out'))])
    return Reply(f"Assalomu alaykum, {t.full_name}! 👋\n\nHar ish kuni ertalab «Keldim» tugmasi keladi.\n\n"
                 "/keldim — kelganimni belgilash\n/ketdim — ketganimni belgilash\n/id — Telegram ID")


def _user_reply(u, cmd):
    if cmd == 'id':
        return Reply(f"Telegram ID: {u.telegram_id}")
    if cmd == 'xabarlar':
        clear_nav_cache()                             # bot jarayoni keshi eskirgan bo'lmasin
        items = build_notifications(u)
        if not items:
            return Reply("Hozircha yangi bildirishnoma yo'q ✅")
        return Reply("🔔 Bildirishnomalar\n\n" + "\n".join(f"{i['icon']} {i['text']}" for i in items))
    return Reply(f"Assalomu alaykum, {u.get_full_name() or u.username}! 👋\n\n"
                 "Bildirishnomalar har kuni ertalab shu yerga keladi.\n\n/xabarlar — hozirgi bildirishnomalar\n/id — Telegram ID")


# ------------------------------------------------------------ KUNLIK VAZIFALAR
def job_checkin_prompts():
    """Har ertalab: bugun hali kelmagan tarbiyachilarga 'Keldim' tugmasi."""
    today = timezone.localdate()
    came = list(TeacherAttendance.objects.filter(date=today, check_in__isnull=False).values_list('teacher_id', flat=True))
    sent = 0
    for t in Teacher.objects.filter(is_active=True, telegram_id__isnull=False).exclude(pk__in=came):
        text = (f"Assalomu alaykum, {t.full_name}! 🌅\n\nIsh kuni boshlandi. "
                "Bog'chaga kelganingizda quyidagi tugmani bosing 👇")
        sent += send_message(t.telegram_id, text, [("📍 Keldim", attendance.build_url(t, 'in'))])
        time.sleep(0.05)                              # Telegram cheklovidan oshib ketmaslik uchun
    return sent


def job_checkout_prompts():
    """Ish kuni oxirida: kelgan, lekin hali 'Ketdim' bosmagan tarbiyachilarga eslatma."""
    today = timezone.localdate()
    pending = TeacherAttendance.objects.filter(date=today, check_in__isnull=False, check_out__isnull=True,
                                               teacher__is_active=True, teacher__telegram_id__isnull=False
                                               ).select_related('teacher')
    sent = 0
    for att in pending:
        t = att.teacher
        sent += send_message(t.telegram_id, f"{t.full_name}, ish kuni tugadimi? 🏠\nKetayotganingizda tugmani bosing 👇",
                             [("🏠 Ketdim", attendance.build_url(t, 'out'))])
        time.sleep(0.05)
    return sent


def job_notify_users(include=None, exclude=None):
    """Har bir foydalanuvchiga o'zi yoqqan bildirishnomalar (include/exclude - tur kodlari)."""
    clear_nav_cache()                                 # bot jarayonida eskirgan kesh bo'lmasin
    sent = 0
    for u in User.objects.filter(is_active=True, telegram_id__isnull=False):
        items = build_notifications(u)
        if include:
            items = [i for i in items if i['code'] in include]
        if exclude:
            items = [i for i in items if i['code'] not in exclude]
        if not items:
            continue
        sent += send_message(u.telegram_id, "🔔 Bildirishnomalar\n\n" + "\n".join(f"{i['icon']} {i['text']}" for i in items))
        time.sleep(0.05)
    return sent


def _work_days():
    return tuple(getattr(settings, 'BOT_WORK_DAYS', (0, 1, 2, 3, 4)))      # dushanba-juma


# (nomi, vaqt sozlamasi, standart vaqt, faqat ish kunlari, funksiya)
JOBS = [
    ('checkin_prompt', 'BOT_CHECKIN_TIME', '07:20', True, job_checkin_prompts),
    ('notify_users', 'BOT_NOTIFY_TIME', '07:30', False, lambda: job_notify_users(exclude={'teacher_late'})),
    ('late_check', 'BOT_LATE_CHECK_TIME', '08:30', True, lambda: job_notify_users(include={'teacher_late'})),
    ('checkout_prompt', 'BOT_CHECKOUT_TIME', '18:00', True, job_checkout_prompts),
]


def run_due_jobs(now=None):
    """Vaqti kelgan va bugun hali bajarilmagan vazifalarni bajaradi. Qaytaradi: [(nomi, yuborilgan_soni)]"""
    now = now or timezone.localtime()
    today, done = now.date(), []
    for name, setting, default, workdays_only, func in JOBS:
        h, m = (int(x) for x in getattr(settings, setting, default).split(':'))
        scheduled = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if not (scheduled <= now <= scheduled + timedelta(minutes=CATCHUP_MINUTES)):
            continue
        if workdays_only and today.weekday() not in _work_days():
            continue
        run, created = BotJobRun.objects.get_or_create(job=name, date=today)
        if not created:                               # bugun allaqachon bajarilgan (bot qayta ishga tushgan bo'lsa ham)
            continue
        try:
            done.append((name, func()))
            log.info("Vazifa bajarildi: %s", name)
        except Exception:
            log.exception("Vazifa xato berdi: %s", name)
            run.delete()                              # keyingi tekshiruvda qayta urinadi
    return done


def run_due_jobs_threadsafe():
    try:
        return run_due_jobs()
    finally:
        connections.close_all()                       # har oqim o'z DB ulanishini yopsin