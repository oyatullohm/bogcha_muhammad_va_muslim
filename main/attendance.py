"""
Tarbiyachi davomati: botdagi tugma -> sayt sahifasi -> joylashuv avtomatik olinadi -> bazaga yoziladi.
Tarbiyachi tizimga LOGIN qilmaydi: havolaning ichidagi imzolangan token (SECRET_KEY bilan) uning kimligini bildiradi.
"""
import logging
import threading
from datetime import datetime
from datetime import time as dtime

from django.conf import settings
from django.core import signing
from django.db import connections, transaction
from django.urls import reverse
from django.utils import timezone

from .models import Kindergarten, Teacher, TeacherAttendance

log = logging.getLogger('bot')
SALT = 'teacher-attendance'
TOKEN_MAX_AGE = 36 * 3600


def site_url(path=''):
    return getattr(settings, 'SITE_URL', 'http://localhost:8000').rstrip('/') + path


def make_token(teacher, action):
    """action: 'in' (keldim) yoki 'out' (ketdim). Token faqat shu kun uchun amal qiladi."""
    return signing.dumps({'t': teacher.pk, 'a': action, 'd': timezone.localdate().isoformat()}, salt=SALT)


def build_url(teacher, action):
    return site_url(reverse('attendance_mark', args=[make_token(teacher, action)]))


def read_token(token):
    """(teacher, action, xato_matni) qaytaradi."""
    try:
        data = signing.loads(token, salt=SALT, max_age=TOKEN_MAX_AGE)
    except signing.BadSignature:
        return None, None, "Havola noto'g'ri yoki eskirgan. Botdan yangisini oling."
    if data.get('d') != timezone.localdate().isoformat():
        return None, None, "Bu havola faqat o'sha kun uchun amal qiladi. Botda /keldim yoki /ketdim yozing."
    teacher = Teacher.objects.filter(pk=data.get('t'), is_active=True).first()
    if not teacher or data.get('a') not in ('in', 'out'):
        return None, None, "Tarbiyachi topilmadi."
    return teacher, data['a'], None


def _auto_checkout_time(kg):
    return getattr(kg, 'auto_checkout_time', None) or dtime(17, 0)


def _auto_close_previous(teacher, kg, today):
    """
    Oldingi kunlarda 'Ketdim' BOSILMAGAN davomatlarga ketish vaqti avtomatik qo'yiladi
    (Bog'cha sozlamasidagi «Ketish vaqti», standart 17:00). Tarbiyachi ertasiga «Keldim» bossa ishga tushadi.
    Qaytaradi: yopilgan kunlar soni.
    """
    end_time = _auto_checkout_time(kg)
    n = 0
    open_days = TeacherAttendance.objects.select_for_update().filter(
        teacher=teacher, date__lt=today, check_in__isnull=False, check_out__isnull=True)
    for att in open_days:
        end = timezone.make_aware(datetime.combine(att.date, end_time))
        if end < att.check_in:                         # 17:00 dan keyin kelgan bo'lsa - ketish = kelish vaqti
            end = att.check_in
        att.check_out, att.auto_checkout = end, True
        att.save(update_fields=['check_out', 'auto_checkout'])
        n += 1
    return n

@transaction.atomic
def mark(teacher, action, lat=None, lon=None):
    """Keldi/ketdini yozadi.

    Keldim:
        - lat/lon majburiy
        - bog'cha radiusi tekshiriladi

    Ketdim:
        - lat/lon kerak emas
        - 17:00 gacha haqiqiy vaqt yoziladi
        - 17:00 dan keyin 17:00 yoziladi
    """
    kg = Kindergarten.objects.first()

    if not kg:
        return False, "Bog'cha sozlanmagan. Adminga murojaat qiling.", {}

    now = timezone.localtime()

    # =========================
    # KELDIM
    # =========================
    if action == 'in':

        # Keldim uchun location majburiy
        if lat is None or lon is None:
            return False, "Keldimni belgilash uchun joylashuvingizni yuboring.", {}

        # Bog'cha radiusini tekshirish
        dist = kg.distance_m(lat, lon)

        if dist > kg.radius_m:
            return (
                False,
                f"Siz bog'chadan uzoqdasiz ({int(dist)} m). "
                f"Bog'cha hududida turib qayta urinib ko'ring.",
                {}
            )

        # Kechagi Ketdim unutilgan bo'lsa
        closed = _auto_close_previous(
            teacher,
            kg,
            now.date()
        )

        att, _ = TeacherAttendance.objects.select_for_update().get_or_create(
            teacher=teacher,
            date=now.date()
        )

        if att.check_in:
            return (
                True,
                f"Siz bugun allaqachon "
                f"{timezone.localtime(att.check_in):%H:%M} da kelgansiz ✅",
                {'new': False}
            )

        # Keldim vaqtini yozish
        att.check_in = now
        att.check_in_lat = lat
        att.check_in_lon = lon
        att.is_late = now.time() > kg.work_start
        att.save()

        msg = (
            f"Keldingiz ✅ {now:%H:%M}"
            + (
                " — kechikdingiz ⏰"
                if att.is_late
                else " — vaqtida 👍"
            )
        )

        if closed:
            msg += (
                f"\n(Oldingi {closed} kunda «Ketdim» bosilmagani uchun "
                f"ketish vaqti {_auto_checkout_time(kg):%H:%M} deb qo'yildi)"
            )

        return (
            True,
            msg,
            {
                'new': True,
                'late': att.is_late,
                'auto_closed': closed
            }
        )

    # =========================
    # KETDIM
    # =========================

    # Ketdim uchun location KERAK EMAS
    att = (
        TeacherAttendance.objects
        .select_for_update()
        .filter(
            teacher=teacher,
            date=now.date(),
            check_in__isnull=False
        )
        .first()
    )

    if not att:
        return (
            False,
            "Avval «Keldim» tugmasini bosib kelganingizni belgilang.",
            {}
        )

    # 17:00 chegarasi
    auto_checkout_time = _auto_checkout_time(kg)

    # Bugungi 17:00 datetime
    checkout_limit = timezone.make_aware(
        timezone.datetime.combine(
            now.date(),
            auto_checkout_time
        )
    )

    # Agar 17:00 dan keyin bosilsa -> 17:00
    if now >= checkout_limit:
        checkout_time = checkout_limit
        is_auto = True
    else:
        # 17:00 gacha -> haqiqiy bosilgan vaqt
        checkout_time = now
        is_auto = False

    att.check_out = checkout_time
    att.check_out_lat = None
    att.check_out_lon = None
    att.auto_checkout = is_auto
    att.save()

    return (
        True,
        f"Ketdingiz 👋 {checkout_time:%H:%M}. "
        f"Bugun ishlagan vaqt: {att.worked_hours} soat",
        {
            'new': True,
            'auto_checkout': is_auto
        }
    )



# ------------------------------------------------------------ BOT BILAN ISHLASH (Telegram)
def send_checkout_button(teacher):
    """Keldi tasdiqlangach botga 'Ketdim' tugmasini qaytaradi (xato bo'lsa davomatga ta'sir qilmaydi)."""
    if not teacher.telegram_id:
        return False
    from bot import services as bot_services           # kech import: bot ilovasi bo'lmasa ham sayt ishlayveradi
    return bot_services.send_button(
        teacher, 'out',
        "✅ Kelganingiz qayd etildi.\n\nIsh kuni tugagach, ketayotganda quyidagi tugmani bosing 👇")[0]


def _after_mark(teacher, action, info):
    from bot import services as bot_services
    try:
        bot_services.clear_buttons(teacher.pk, action)              # bosilgan tugmaning xabarlari chatdan o'chadi
        if action == 'in' and info.get('new'):
            send_checkout_button(teacher)                           # va «Ketdim» tugmasi qaytariladi
    except Exception:
        log.exception("Davomatdan keyingi bot amallari xato berdi")
    finally:
        connections.close_all()


def after_mark(teacher, action, info):
    """
    Davomat yozilgach: (1) bosilgan tugmaning xabari botdan o'chiriladi, (2) 'Keldim' bo'lsa 'Ketdim' tugmasi yuboriladi.
    Telegram sekin javob bersa sahifa kutib qolmasligi uchun fon oqimida bajariladi.
    """
    if teacher.telegram_id:
        threading.Thread(target=_after_mark, args=(teacher, action, info), daemon=True).start()
