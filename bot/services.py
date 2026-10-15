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

from django.conf import settings
from django.db import connections
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from main import attendance, workdays
from main.models import Kindergarten, Teacher, TeacherAttendance, User
from main.notifications import build_notifications, clear_nav_cache

from .models import BotButtonMessage, BotJobRun, BotUser
from .telegram_api import collect, delete_message, get_me, send_message, send_message_result

log = logging.getLogger('bot')

MAX_FAILS = 5            # shuncha xato paroldan keyin
BLOCK_MINUTES = 30       # shuncha daqiqa bloklanadi
CATCHUP_MINUTES = 45     # bot ertaroq o'chib turgan bo'lsa, vazifa vaqtidan shuncha daqiqagacha bajariladi


@dataclass
class Reply:
    text: str
    buttons: list = field(default_factory=list)      # [(matn, url)]
    delete_incoming: bool = False                    # foydalanuvchi yozgan xabarni (parol) o'chirish
    track: tuple = None                              # (teacher_id, 'in'|'out') - yuborilgan tugmali xabarni eslab qolish


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


# ------------------------------------------------------------ KELDIM / KETDIM TUGMALI XABARLAR
BUTTON_LABELS = {'in': "📍 Keldim", 'out': "🏠 Ketdim"}


def track_button(teacher_id, action, chat_id, message_id):
    BotButtonMessage.objects.create(teacher_id=teacher_id, action=action, date=timezone.localdate(),
                                    chat_id=chat_id, message_id=message_id)


def send_button(teacher, action, text):
    """
    Tarbiyachiga «Keldim»/«Ketdim» tugmali xabar yuboradi va xabar ID sini eslab qoladi
    (tugma bosilgach xabar o'chirilishi uchun). Qaytaradi: (ok, xato, havola)
    """
    url = attendance.build_url(teacher, action)
    res = send_message_result(teacher.telegram_id, text, [(BUTTON_LABELS[action], url)])
    if res['ok'] and res['message_id']:
        track_button(teacher.pk, action, teacher.telegram_id, res['message_id'])
    return res['ok'], res['error'], url


def clear_buttons(teacher_id, action):
    """Tugma bosildi -> shu tarbiyachining shu turdagi (keldim/ketdim) xabarlari chatdan o'chadi."""
    msgs = list(BotButtonMessage.objects.filter(teacher_id=teacher_id, action=action))
    for m in msgs:
        delete_message(m.chat_id, m.message_id)
    BotButtonMessage.objects.filter(pk__in=[m.pk for m in msgs]).delete()
    return len(msgs)


def cleanup_old_buttons():
    BotButtonMessage.objects.filter(date__lt=timezone.localdate() - timedelta(days=3)).delete()


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
        return Reply("Bog'chaga kelganingizda tugmani bosing 👇", [("📍 Keldim", attendance.build_url(t, 'in'))],
                     track=(t.pk, 'in'))
    if cmd == 'ketdim':
        if not (att and att.check_in):
            return Reply("Avval kelganingizni belgilang: /keldim")
        return Reply("Ketayotganingizda tugmani bosing 👇", [("🏠 Ketdim", attendance.build_url(t, 'out'))],
                     track=(t.pk, 'out'))
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
    cleanup_old_buttons()
    today = timezone.localdate()
    came = list(TeacherAttendance.objects.filter(date=today, check_in__isnull=False).values_list('teacher_id', flat=True))
    sent = 0
    for t in Teacher.objects.filter(is_active=True, telegram_id__isnull=False).exclude(pk__in=came):
        text = (f"Assalomu alaykum, {t.full_name}! 🌅\n\nIsh kuni boshlandi. "
                "Bog'chaga kelganingizda quyidagi tugmani bosing 👇")
        sent += send_button(t, 'in', text)[0]
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
        sent += send_button(t, 'out', f"{t.full_name}, ish kuni tugadimi? 🏠\nKetayotganingizda tugmani bosing 👇")[0]
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


# (nomi, vaqt sozlamasi, standart vaqt, faqat ish kunlari, funksiya)
JOBS = [
    ('checkin_prompt', 'BOT_CHECKIN_TIME', '07:20', True, job_checkin_prompts),
    ('notify_users', 'BOT_NOTIFY_TIME', '07:30', False, lambda: job_notify_users(exclude={'teacher_late'})),
    ('late_check', 'BOT_LATE_CHECK_TIME', '08:30', True, lambda: job_notify_users(include={'teacher_late'})),
    ('checkout_prompt', 'BOT_CHECKOUT_TIME', '17:00', True, job_checkout_prompts),
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
        if workdays_only and not workdays.is_workday(today):      # dam olish kuni: tarbiyachilarga xabar yuborilmaydi
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


# ------------------------------------------------------------ SINOV SAHIFASI UCHUN (admin: "Bot sinovi")
JOB_LABELS = {
    'checkin_prompt': "Tarbiyachilarga «📍 Keldim» tugmasi (hali kelmaganlarga)",
    'notify_users': "Foydalanuvchilarga bildirishnomalar (tug'ilgan kun, bayram, ombor, qarzdor)",
    'late_check': "Kelmagan / kechikkan tarbiyachilar haqida foydalanuvchilarga xabar",
    'checkout_prompt': "«🏠 Ketdim» eslatmasi (kelgan, lekin ketmaganlarga)",
}


def jobs_info():
    """[{'name', 'label', 'time', 'workdays'}] - sozlamalardagi vaqt bilan."""
    return [{'name': n, 'label': JOB_LABELS[n], 'time': getattr(settings, st, df), 'workdays': wd}
            for n, st, df, wd, _ in JOBS]


def explain_error(error):
    """Telegram xatosini tushunarli qilib tushuntiradi."""
    e = (error or '').lower()
    if 'bot can\'t initiate' in e or 'chat not found' in e:
        return "Tarbiyachi botga hali /start bosmagan (yoki ID noto'g'ri). U botni ochib /start bosishi kerak."
    if 'blocked by the user' in e:
        return "Foydalanuvchi botni bloklagan."
    if 'user is deactivated' in e:
        return "Telegram akkaunt o'chirilgan."
    if 'unauthorized' in e or 'not found' == e.strip():
        return "BOT_TOKEN noto'g'ri."
    if 'bot_token' in e:
        return "settings.py da BOT_TOKEN yozilmagan."
    if 'timed out' in e or 'urlopen' in e or 'resolution' in e or 'unreachable' in e:
        return "Serverdan Telegramga ulanib bo'lmayapti (internet yoki firewall)."
    return ''


def _names_by_chat():
    names = {t.telegram_id: f"{t.full_name} (tarbiyachi)" for t in Teacher.objects.filter(telegram_id__isnull=False)}
    names.update({u.telegram_id: f"{u.get_full_name() or u.username} (xodim)" for u in User.objects.filter(telegram_id__isnull=False)})
    return names


def _rows_from_items(items):
    names = _names_by_chat()
    return [{'name': names.get(i['chat_id'], str(i['chat_id'])), 'chat_id': i['chat_id'], 'ok': i['ok'],
             'error': i['error'], 'hint': explain_error(i['error']), 'note': i['note'], 'link': ''} for i in items]


def bot_status(check_token=False):
    """Sinov sahifasining tepasidagi tekshiruv ro'yxati. check_token=True bo'lsa Telegramga getMe yuboradi."""
    kg = Kindergarten.objects.first()
    site = getattr(settings, 'SITE_URL', '')
    st = {
        'token': bool(getattr(settings, 'BOT_TOKEN', '')),
        'password': bool(getattr(settings, 'BOT_PASSWORD', '')),
        'site_url': site, 'https': site.startswith('https://'),
        'kindergarten': bool(kg), 'radius': kg.radius_m if kg else None,
        'teachers_total': Teacher.objects.filter(is_active=True).count(),
        'teachers_linked': Teacher.objects.filter(is_active=True, telegram_id__isnull=False).count(),
        'users_linked': User.objects.filter(is_active=True, telegram_id__isnull=False).count(),
        'me': None,
        'today': workdays.status_map(timezone.localdate(), timezone.localdate())[timezone.localdate()],
    }
    if check_token:
        res = get_me()
        st['me'] = {'ok': bool(res.get('ok')), 'username': (res.get('result') or {}).get('username', ''),
                    'error': res.get('description', ''), 'hint': explain_error(res.get('description', ''))}
    return st


def send_checkin_buttons(teacher_ids, action, preview=False):
    """
    Tanlangan tarbiyachilarga 'Keldim' (action='in') yoki 'Ketdim' ('out') tugmasini yuboradi.
    preview=True - Telegramga YUBORMAYDI, faqat havolani ko'rsatadi (o'zingiz ochib sinab ko'rish uchun).
    """
    rows = []
    for t in Teacher.objects.filter(pk__in=teacher_ids, is_active=True).order_by('full_name'):
        row = {'name': t.full_name, 'chat_id': t.telegram_id, 'link': attendance.build_url(t, action),
               'ok': None, 'error': '', 'hint': '', 'note': ''}
        if preview:
            row['note'] = "Faqat havola (yuborilmadi)"
        elif not t.telegram_id:
            row.update(ok=False, error="Telegram ID yo'q", hint="Tarbiyachi hali botga ulanmagan: «Tarbiyachilar» → «Botga kirganlar».")
        else:
            if action == 'in':
                text = (f"🧪 Sinov xabari\n\nAssalomu alaykum, {t.full_name}! 🌅\nBog'chaga kelganingizda "
                        "quyidagi tugmani bosing 👇")
            else:
                text = f"🧪 Sinov xabari\n\n{t.full_name}, ketayotganingizda quyidagi tugmani bosing 👇"
            ok, err, _ = send_button(t, action, text)
            row.update(ok=ok, error=err, hint=explain_error(err))
        rows.append(row)
    return rows


def send_text_to_teachers(teacher_ids, text):
    rows = []
    for t in Teacher.objects.filter(pk__in=teacher_ids, is_active=True).order_by('full_name'):
        row = {'name': t.full_name, 'chat_id': t.telegram_id, 'link': '', 'ok': False, 'error': '', 'hint': '', 'note': ''}
        if not t.telegram_id:
            row.update(error="Telegram ID yo'q", hint="Tarbiyachi hali botga ulanmagan.")
        else:
            res = send_message_result(t.telegram_id, text)
            ok, err = res['ok'], res['error']
            row.update(ok=ok, error=err, hint=explain_error(err))
        rows.append(row)
    return rows


def run_job_now(name):
    """
    Kunlik vazifani VAQTINI KUTMASDAN hozir bajaradi (sinov uchun). BotJobRun ga yozilmaydi,
    shuning uchun real vaqtida vazifa baribir o'z vaqtida ishlaydi. Qaytaradi: (qatorlar, xato yoki '')
    """
    job = next((j for j in JOBS if j[0] == name), None)
    if not job:
        return [], "Noma'lum vazifa."
    try:
        with collect() as items:
            job[4]()
    except Exception as e:
        log.exception("Sinov vazifasi xato berdi: %s", name)
        return [], f"Vazifa xato berdi: {e}"
    return _rows_from_items(items), ''
