"""
Ish kunlari: bot qaysi kunlari tarbiyachilarga xabar yubormasligi kerak.
Kun DAM OLISH hisoblanadi, agar:
  1) shu sana DayOff jadvalida belgilangan bo'lsa (admin yoki yordamchi kalendardan belgilagan), yoki
  2) shu kunga tushgan bayram "dam olish kuni" deb belgilangan bo'lsa (har yili o'zi takrorlanadi), yoki
  3) haftalik ish kunlariga kirmasa (standart: dushanba-juma ishlaydi, shanba-yakshanba dam).
"""
from datetime import timedelta

from . import stats
from .models import DayOff, Holiday, WorkSchedule

DEFAULT_WEEKDAYS = (0, 1, 2, 3, 4)
WEEKDAY_NAMES = [(0, 'Du'), (1, 'Se'), (2, 'Ch'), (3, 'Pa'), (4, 'Ju'), (5, 'Sh'), (6, 'Ya')]


def get_weekdays():
    ws = WorkSchedule.objects.first()
    if ws is None:
        return DEFAULT_WEEKDAYS
    try:
        return tuple(sorted({int(x) for x in ws.work_weekdays.split(',') if x.strip() != '' and 0 <= int(x) <= 6}))
    except ValueError:
        return DEFAULT_WEEKDAYS


def _holiday_off(start, end):
    """{sana: bayram nomi} - 'dam olish kuni' deb belgilangan bayramlar (yillik takrorlanadiganlari ham)."""
    out = {}
    for h in Holiday.objects.filter(is_active=True, is_day_off=True):
        years = range(start.year, end.year + 1) if h.repeat_yearly else [h.date.year]
        for y in years:
            d = stats._birthday_in_year(h.date, y) if h.repeat_yearly else h.date
            if start <= d <= end:
                out[d] = h.name
    return out


def status_map(start, end):
    """
    {sana: {'workday': bool, 'kind': ''|'dayoff'|'holiday'|'weekly', 'reason': str, 'label': str}}
    Atigi 3 ta so'rov (haftalik sozlama, belgilangan kunlar, bayramlar) - butun oy uchun ham.
    """
    weekly = set(get_weekdays())
    offs = {o.date: o.reason for o in DayOff.objects.filter(date__range=(start, end))}
    holidays = _holiday_off(start, end)
    out, d = {}, start
    while d <= end:
        if d in offs:
            out[d] = {'workday': False, 'kind': 'dayoff', 'reason': offs[d], 'label': offs[d] or 'Dam olish'}
        elif d in holidays:
            out[d] = {'workday': False, 'kind': 'holiday', 'reason': holidays[d], 'label': holidays[d]}
        elif d.weekday() not in weekly:
            out[d] = {'workday': False, 'kind': 'weekly', 'reason': '', 'label': 'Dam olish'}
        else:
            out[d] = {'workday': True, 'kind': '', 'reason': '', 'label': ''}
        d += timedelta(days=1)
    return out


def is_workday(d):
    return status_map(d, d)[d]['workday']
