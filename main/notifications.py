"""
Bildirishnomalar: turlar, har bir user sozlamasi va xabarlarni yig'ish.

SQL TEJASH: ma'lumotlar "bo'laklar" (piece) ga bo'lib keshlanadi. Har bo'lak o'z versiyasiga ega va
faqat unga tegishli model o'zgarganda (signals.py) yangilanadi. Masalan to'lov yozilsa faqat
'debtors' va 'finance' qayta hisoblanadi, qolganlari keshdan olinadi. Iliq keshda 0 ta SQL.
"""
import time

from django.core.cache import cache
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from . import stats, workdays
from .models import (Child, Kindergarten, NotificationPreference, NotificationType, Product,
                     Teacher, TeacherAttendance)

REMIND_DAYS = 3        # tug'ilgan kun va bayramdan necha kun oldin eslatish boshlansin (3, 2, 1 kun va bugun)
CACHE_SECONDS = 60 * 60

# (kod, nomi, tavsif) - kod dasturga ma'lum, shuning uchun adminga yangi tur yaratish shart emas
DEFAULT_TYPES = [
    ('birthday_child', "Bolalar tug'ilgan kuni", "Bolalarning tug'ilgan kuni: 3, 2, 1 kun qolganda va bugun"),
    ('birthday_teacher', "Tarbiyachilar tug'ilgan kuni", "Tarbiyachilarning tug'ilgan kuni: 3, 2, 1 kun qolganda va bugun"),
    ('birthday_user', "Xodimlar tug'ilgan kuni", "Admin va yordamchilarning tug'ilgan kuni: 3, 2, 1 kun qolganda va bugun"),
    ('holiday', "Bayramlar", "Yangi yil, Navro'z va boshqa bayramlar (sanalari \"Bayramlar\" jadvalida)"),
    ('product_low', "Mahsulot kamayishi", "Omborda minimal miqdorga tushgan mahsulotlar"),
    ('payment_debt', "Qarzdorlar", "Qarzdor oilalar (faqat admin uchun)"),
    ('teacher_late', "Tarbiyachi kechikishi", "Bugun kechikkan yoki hali kelmagan tarbiyachilar"),
]

PIECES = ('counts', 'birthdays', 'holidays', 'debtors', 'attendance', 'finance', 'pref')


# ------------------------------------------------------------ KESH INFRATUZILMASI
def _ver(group):
    key = f'nav_ver:{group}'
    v = cache.get(key)
    if v is None:
        v = int(time.time() * 1000)
        cache.set(key, v, None)
    return v


def bump(*groups):
    """Bo'lak(lar) keshini eskirtiradi (versiyani oshirish)."""
    for g in groups:
        try:
            cache.incr(f'nav_ver:{g}')
        except ValueError:
            cache.set(f'nav_ver:{g}', int(time.time() * 1000), None)


def clear_nav_cache():
    bump(*PIECES)


def piece(name, func, ttl=CACHE_SECONDS, group=None):
    """Keshdan oladi; bo'lmasa func() ni hisoblab saqlaydi. Kalitda sana bor - har kuni yangilanadi."""
    key = f'nav:{name}:{_ver(group or name)}:{timezone.localdate()}'
    data = cache.get(key)
    if data is None:
        data = func()
        cache.set(key, data, ttl)
    return data


# ------------------------------------------------------------ BO'LAKLAR (har biri alohida keshlanadi)
def _p_counts():
    low = list(Product.objects.filter(quantity__lte=F('min_quantity')).order_by('name').values('name', 'quantity', 'unit'))
    return {'children_count': Child.objects.filter(is_active=True).count(),
            'low_stock_count': len(low), 'low_products': low[:10]}


def _p_birthdays():
    return stats.upcoming_birthdays(REMIND_DAYS)


def _p_holidays():
    # har bayramning o'z "necha kun oldin" qiymati (remind_days_before) ishlatiladi
    return [{'name': r['holiday'].name, 'days_left': r['days_left'], 'next_date': r['next_date']}
            for r in stats.upcoming_holidays()]


def _p_debtors():
    return stats.debtors_summary()


def _p_finance():
    return stats.month_totals()


def _p_attendance():
    today = timezone.localdate()
    atts = list(TeacherAttendance.objects.filter(date=today, check_in__isnull=False)
                .values('teacher_id', 'teacher__full_name', 'check_in', 'is_late'))
    teachers = list(Teacher.objects.filter(is_active=True).order_by('full_name').values('id', 'full_name', 'phone'))
    kg = Kindergarten.objects.first()
    came = {a['teacher_id'] for a in atts}
    workday = workdays.is_workday(today)          # dam olish kuni (yakshanba, bayram...) tarbiyachi 'kelmagan' hisoblanmaydi
    return {
        'teachers_count': len(teachers),
        'present_today': sum(1 for t in teachers if t['id'] in came),
        'absent': ([{'full_name': t['full_name'], 'phone': t['phone']} for t in teachers if t['id'] not in came]
                   if workday else []),
        'workday': workday,
        'late': [(a['teacher__full_name'], a['check_in']) for a in atts if a['is_late']],
        'work_started': bool(workday and kg and timezone.localtime().time() > kg.work_start),
    }


def get_counts():
    return piece('counts', _p_counts)


def get_birthdays():
    return piece('birthdays', _p_birthdays)


def get_debtors():
    return piece('debtors', _p_debtors)


def get_finance_month():
    return piece('finance', _p_finance)


def get_attendance():
    return piece('attendance', _p_attendance, ttl=300)      # vaqtga bog'liq (ish boshlandimi) - 5 daqiqa


# ------------------------------------------------------------ TURLAR VA USER SOZLAMASI
def ensure_default_types():
    """Yetishmayotgan tayyor turlarni yaratadi. Hammasi bor bo'lsa - atigi 1 ta so'rov."""
    have = set(NotificationType.objects.values_list('code', flat=True))
    missing = [t for t in DEFAULT_TYPES if t[0] not in have]
    if missing:
        NotificationType.objects.bulk_create(
            [NotificationType(code=c, name=n, description=d) for c, n, d in missing])
        bump('pref')


def get_user_pref(user):
    """User sozlamasi. Birinchi marta yaratilsa - hamma tur yoqilgan (keraksizini o'zi o'chiradi)."""
    pref = NotificationPreference.objects.filter(user=user).first()
    if pref is None:
        pref = NotificationPreference.objects.create(user=user)
        ensure_default_types()
        pref.enabled_types.set(NotificationType.objects.filter(is_active=True))
    return pref


def _prefs(user):
    def load():
        pref = get_user_pref(user)
        codes = set(pref.enabled_types.filter(is_active=True).values_list('code', flat=True)) if pref.is_active else set()
        return {'active': pref.is_active, 'codes': codes}
    return piece(f'pref:{user.pk}', load, group='pref')


# ------------------------------------------------------------ MATNLAR
def _birthday_text(b):
    if b['days_left'] == 0:
        return f"Bugun {b['name']} ({b['type']}) ning tug'ilgan kuni! {b['turns']} yoshga to'ldi 🎉"
    return f"{b['name']} ({b['type']}) tug'ilgan kuniga {b['days_left']} kun qoldi ({b['date']:%d.%m}, {b['turns']} yosh)"


def _holiday_text(h):
    if h['days_left'] == 0:
        return f"Bugun {h['name']} bayrami! 🎉"
    return f"{h['name']} bayramiga {h['days_left']} kun qoldi ({h['next_date']:%d.%m.%Y})"


def build_notifications(user, days=REMIND_DAYS):
    """User yoqib qo'ygan turlar bo'yicha xabarlar: [{code, icon, text, url}]. Iliq keshda 0 ta SQL."""
    prefs = _prefs(user)
    if not prefs['active']:
        return []
    codes = prefs['codes']
    items = []

    kinds = {k for c, k in (('birthday_child', 'child'), ('birthday_teacher', 'teacher'),
                            ('birthday_user', 'user')) if c in codes}
    if kinds:
        birthdays = get_birthdays() if days == REMIND_DAYS else stats.upcoming_birthdays(days)
        for b in birthdays:
            if b['kind'] in kinds:
                items.append({'code': f"birthday_{b['kind']}", 'icon': '🎂', 'url': reverse('dashboard'),
                              'text': _birthday_text(b)})

    if 'holiday' in codes:
        for h in piece('holidays', _p_holidays):
            items.append({'code': 'holiday', 'icon': '🎉', 'url': reverse('notification_list'),
                          'text': _holiday_text(h)})

    if 'product_low' in codes:
        for p in get_counts()['low_products']:
            items.append({'code': 'product_low', 'icon': '⚠️', 'url': reverse('product_list') + '?low=1',
                          'text': f"{p['name']} tugayapti: {p['quantity'].normalize():f} {p['unit']} qoldi"})

    if 'payment_debt' in codes and user.is_admin:
        d = get_debtors()
        if d['count']:
            items.append({'code': 'payment_debt', 'icon': '💸', 'url': reverse('payment_list') + '#qarzdorlar',
                          'text': f"{d['count']} ta qarzdor, jami {d['total']:,.0f} so'm".replace(',', ' ')})

    if 'teacher_late' in codes:
        att = get_attendance()
        for name, check_in in att['late']:
            items.append({'code': 'teacher_late', 'icon': '⏰', 'url': reverse('teacher_list'),
                          'text': f"{name} bugun kechikdi ({timezone.localtime(check_in):%H:%M})"})
        if att['work_started']:
            for t in att['absent']:
                items.append({'code': 'teacher_late', 'icon': '🚫', 'url': reverse('teacher_list'),
                              'text': f"{t['full_name']} hali kelmadi"})
    return items
