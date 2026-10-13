"""
Tarbiyachi davomati: botdagi tugma -> sayt sahifasi -> joylashuv avtomatik olinadi -> bazaga yoziladi.
Tarbiyachi tizimga LOGIN qilmaydi: havolaning ichidagi imzolangan token (SECRET_KEY bilan) uning kimligini bildiradi.
"""
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import Kindergarten, Teacher, TeacherAttendance

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


@transaction.atomic
def mark(teacher, action, lat, lon):
    """Joylashuvni bog'cha radiusi bilan tekshirib, keldi/ketdini yozadi. (ok, xabar, info) qaytaradi."""
    kg = Kindergarten.objects.first()
    if not kg:
        return False, "Bog'cha lokatsiyasi sozlanmagan. Adminga murojaat qiling.", {}
    dist = kg.distance_m(lat, lon)
    if dist > kg.radius_m:
        return False, f"Siz bog'chadan uzoqdasiz ({int(dist)} m). Bog'cha hududida turib qayta urinib ko'ring.", {}

    now = timezone.localtime()
    if action == 'in':
        att, _ = TeacherAttendance.objects.select_for_update().get_or_create(teacher=teacher, date=now.date())
        if att.check_in:
            return True, f"Siz bugun allaqachon {timezone.localtime(att.check_in):%H:%M} da kelgansiz ✅", {'new': False}
        att.check_in, att.check_in_lat, att.check_in_lon, att.check_in_inside = now, lat, lon, True
        att.is_late = now.time() > kg.work_start
        att.save()
        msg = f"Keldingiz ✅ {now:%H:%M}" + (" — kechikdingiz ⏰" if att.is_late else " — vaqtida 👍")
        return True, msg, {'new': True, 'late': att.is_late}

    # 'Ketdim': bo'sh qator yaratilmaydi - faqat avval 'Keldim' bosilgan bo'lsa
    att = TeacherAttendance.objects.select_for_update().filter(teacher=teacher, date=now.date(), check_in__isnull=False).first()
    if not att:
        return False, "Avval «Keldim» tugmasini bosib kelganingizni belgilang.", {}
    att.check_out, att.check_out_lat, att.check_out_lon, att.check_out_inside = now, lat, lon, True
    att.save()
    return True, f"Ketdingiz 👋 {now:%H:%M}. Bugun ishlagan vaqt: {att.worked_hours} soat", {'new': True}


def send_checkout_button(teacher):
    """Keldi tasdiqlangach botga 'Ketdim' tugmasini qaytaradi (xato bo'lsa davomatga ta'sir qilmaydi)."""
    if not teacher.telegram_id:
        return False
    from bot.telegram_api import send_message          # kech import: bot ilovasi bo'lmasa ham sayt ishlayveradi
    return send_message(
        teacher.telegram_id,
        "✅ Kelganingiz qayd etildi.\n\nIsh kuni tugagach, ketayotganda quyidagi tugmani bosing 👇",
        [("🏠 Ketdim", build_url(teacher, 'out'))])