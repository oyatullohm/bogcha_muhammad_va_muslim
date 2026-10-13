"""
Statistika va bot uchun yordamchi funksiyalar.
(Views faqat shularni chaqiradi - hisob-kitob shu yerda turadi.)
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import (
    Child, ChildAccount, Teacher, TeacherAttendance, Kindergarten,
    Payment, Expense, User, Holiday,
)

PERIODS = {'1m': 1, '3m': 3, '6m': 6, '1y': 12}
PERIOD_LABELS = {'1m': '1 oylik', '3m': '3 oylik', '6m': '6 oylik', '1y': 'Yillik'}


# ------------------------------------------------------------ SANA YORDAMCHILARI
def add_months(d, n):
    """d ning oyiga n oy qo'shadi (manfiy ham bo'ladi). Natija - oyning 1-kuni."""
    idx = d.year * 12 + (d.month - 1) + n
    return date(idx // 12, idx % 12 + 1, 1)


def period_range(period):
    months = PERIODS.get(period, 1)
    end = timezone.localdate()
    start = add_months(end, -(months - 1))  # joriy oy ham hisobga kiradi
    return start, end


# ------------------------------------------------------------ KALENDAR
def month_weeks(month_start, data=None):
    """
    Oy kalendari: haftalar ro'yxati (dushanbadan yakshanbagacha).
    data = {sana: istalgan_qiymat} - shu sanaga tegishli ma'lumot cell['data'] da bo'ladi.
    """
    data = data or {}
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month):
        weeks.append([{'date': d, 'in_month': d.month == month_start.month,
                       'weekend': d.weekday() >= 5, 'data': data.get(d)} for d in week])
    return weeks


# ------------------------------------------------------------ BOLA TO'LOV HOLATI (rang uchun)
NO_STATUS = {'code': 'none', 'label': '—', 'due': Decimal(0), 'paid': Decimal(0)}
STATUS_LABELS = {'paid': "To'langan", 'partial': "Qisman to'langan", 'unpaid': "To'lamagan"}


def _fmt(x):
    return f"{x:,.0f}".replace(',', ' ')


def _status(tariff, bal, paid, charged):
    """
    bal     - hisob balansi (+ ortiqcha, - qarz)
    paid    - shu oy ichida tushgan sof to'lov
    charged - shu oy uchun oylik allaqachon yozilganmi
    Oylik yozilgan bo'lsa qarz = -balans. Yozilmagan bo'lsa = tarif narxi - balans.
    """
    bal, paid = bal or Decimal(0), paid or Decimal(0)
    if not tariff and not charged and bal >= 0:
        return dict(NO_STATUS)
    price = tariff.price if tariff else Decimal(0)
    due = max(-bal, Decimal(0)) if charged else max(price - bal, Decimal(0))
    if due <= 0:
        code = 'paid'
    elif paid > 0 or bal > 0:
        code = 'partial'
    else:
        code = 'unpaid'
    return {'code': code, 'label': STATUS_LABELS[code], 'due': due, 'paid': paid}


def payment_status_map(billing_ids, known=None):
    """
    {asosiy_bola_id: {'code': paid|partial|unpaid|none, 'label', 'due', 'paid'}}
    Yashil - to'liq, sariq - qisman, qizil - umuman to'lamagan.
    known - allaqachon yuklangan (tarifi select_related qilingan) bola obyektlari: ularni qayta so'ramaydi.
    """
    month_start = timezone.localdate().replace(day=1)
    billing_ids = set(billing_ids)
    kids = {c.pk: c for c in (known or []) if c.pk in billing_ids}
    missing = billing_ids - set(kids)
    if missing:
        kids.update({c.pk: c for c in Child.objects.filter(pk__in=missing).select_related('tariff')})
    accounts = {a.child_id: a for a in ChildAccount.objects.filter(child_id__in=billing_ids).annotate(
        bal=Sum('transactions__amount'),
        paid=Sum('transactions__amount', filter=Q(transactions__payment__isnull=False,
                                                 transactions__created_at__date__gte=month_start)),
        ch=Count('transactions', filter=Q(transactions__type='charge',
                                         transactions__period_start__gte=month_start)),
    )}
    out = {}
    for cid, kid in kids.items():
        a = accounts.get(cid)
        out[cid] = _status(kid.tariff, a.bal if a else 0, a.paid if a else 0, bool(a and a.ch))
    return out


def pay_info(child):
    """To'lov oynasi uchun bola ma'lumoti (JSON ga ham mos: faqat son, matn va ro'yxat)."""
    main = child.billing_child
    st = payment_status_map({main.pk}, known=[main]).get(main.pk, dict(NO_STATUS))
    family = Child.objects.filter(Q(pk=main.pk) | Q(family_group=main), is_active=True).only('full_name').order_by('full_name')
    return {
        'id': child.pk, 'name': child.full_name,
        'group': child.group.name if child.group else '',
        'parent': child.parent_name or '', 'phone': child.parent_phone,
        'family': [c.full_name for c in family],
        'tariff': main.tariff.name if main.tariff else '',
        'price': int(main.tariff.price) if main.tariff else 0,
        'paid': int(st['paid']), 'due': int(st['due']),
        'status': st['code'], 'status_label': st['label'],
    }


def remaining_to_pay(child):
    """To'lov formasi uchun: (qolgan summa, izoh). 2 marta bo'lib to'lansa 2-sida qolgani chiqadi."""
    main = child.billing_child
    st = payment_status_map({main.pk}).get(main.pk, dict(NO_STATUS))
    parts = []
    if main.tariff:
        parts.append(f"Tarif: {_fmt(main.tariff.price)} so'm")
    if st['paid'] > 0:
        parts.append(f"Shu oy to'langan: {_fmt(st['paid'])} so'm")
    parts.append(f"Qolgan: {_fmt(st['due'])} so'm")
    return st['due'], ' · '.join(parts)


# ------------------------------------------------------------ MOLIYA
def finance_summary(period='1m'):
    """Kirim, chiqim, foyda va kategoriya bo'yicha chiqimlar."""
    start, end = period_range(period)
    income = (Payment.objects.filter(status='confirmed', paid_date__range=(start, end))
              .aggregate(s=Sum('amount'))['s'] or Decimal(0))
    expense = (Expense.objects.filter(date__range=(start, end))
               .aggregate(s=Sum('amount'))['s'] or Decimal(0))

    by_category = list(
        Expense.objects.filter(date__range=(start, end))
        .values('category__name').annotate(total=Sum('amount')).order_by('-total')
    )
    for row in by_category:
        row['percent'] = round(float(row['total']) / float(expense) * 100, 1) if expense else 0

    return {
        'period': period, 'label': PERIOD_LABELS.get(period, period),
        'from': start, 'to': end,
        'income': income, 'expense': expense, 'profit': income - expense,
        'expense_by_category': by_category,
    }


def finance_overview(period='1m'):
    """
    Statistika sahifasi uchun: tanlangan davr + 4 davr solishtirmasi + oylik grafik.
    Oldin ~20 ta so'rov ketardi, endi atigi 2 ta (12 oylik ma'lumot oylar bo'yicha olinib, Python'da yig'iladi).
    """
    period = period if period in PERIODS else '1m'
    today = timezone.localdate()
    start12 = add_months(today, -11)
    months = [add_months(start12, i) for i in range(12)]

    inc = {r['m']: r['t'] for r in
           Payment.objects.filter(status='confirmed', paid_date__gte=start12, paid_date__lte=today)
           .annotate(m=TruncMonth('paid_date')).values('m').annotate(t=Sum('amount')).order_by('m')}
    exp_rows = list(Expense.objects.filter(date__gte=start12, date__lte=today)
                    .annotate(m=TruncMonth('date')).values('m', 'category__name')
                    .annotate(t=Sum('amount')).order_by('m'))
    exp_by_month = defaultdict(Decimal)
    for r in exp_rows:
        exp_by_month[r['m']] += r['t']

    def summary(p):
        ms = months[-PERIODS[p]:]
        mset = set(ms)
        income = sum((inc.get(m, Decimal(0)) for m in ms), Decimal(0))
        expense = sum((exp_by_month.get(m, Decimal(0)) for m in ms), Decimal(0))
        cats = defaultdict(Decimal)
        for r in exp_rows:
            if r['m'] in mset:
                cats[r['category__name']] += r['t']
        by_category = sorted(
            ({'category__name': k, 'total': v,
              'percent': round(float(v) / float(expense) * 100, 1) if expense else 0} for k, v in cats.items()),
            key=lambda x: -x['total'])
        return {'period': p, 'label': PERIOD_LABELS.get(p, p), 'from': ms[0], 'to': today,
                'income': income, 'expense': expense, 'profit': income - expense,
                'expense_by_category': by_category}

    chart_months = months[-max(PERIODS[period], 3):]
    rows = [{'month': m.strftime('%Y-%m'), 'income': inc.get(m, Decimal(0)), 'expense': exp_by_month.get(m, Decimal(0)),
             'profit': inc.get(m, Decimal(0)) - exp_by_month.get(m, Decimal(0))} for m in chart_months]
    return {'summary': summary(period), 'all_periods': [summary(k) for k in PERIODS], 'rows': rows}


def month_totals():
    """Joriy oy kirim/chiqim/foyda (dashboard uchun, 2 ta so'rov)."""
    today = timezone.localdate()
    start = today.replace(day=1)
    income = (Payment.objects.filter(status='confirmed', paid_date__gte=start, paid_date__lte=today)
              .aggregate(s=Sum('amount'))['s'] or Decimal(0))
    expense = (Expense.objects.filter(date__gte=start, date__lte=today).aggregate(s=Sum('amount'))['s'] or Decimal(0))
    return {'income': income, 'expense': expense, 'profit': income - expense}


def monthly_chart(months=12):
    """Grafik uchun: oxirgi N oy bo'yicha kirim / chiqim / foyda."""
    end = timezone.localdate()
    start = add_months(end, -(months - 1))

    inc = {r['m']: r['t'] for r in
           Payment.objects.filter(status='confirmed', paid_date__gte=start, paid_date__lte=end)
           .annotate(m=TruncMonth('paid_date')).values('m').annotate(t=Sum('amount')).order_by('m')}
    exp = {r['m']: r['t'] for r in
           Expense.objects.filter(date__gte=start, date__lte=end)
           .annotate(m=TruncMonth('date')).values('m').annotate(t=Sum('amount')).order_by('m')}

    rows, cur = [], start
    while cur <= end:
        i, e = inc.get(cur, Decimal(0)), exp.get(cur, Decimal(0))
        rows.append({'month': cur.strftime('%Y-%m'), 'income': i, 'expense': e, 'profit': i - e})
        cur = add_months(cur, 1)
    return rows


def debtors():
    """Qarzdor hisoblar (balansi manfiy). Aka-ukalar bitta - asosiy bola hisobida."""
    return (ChildAccount.objects.select_related('child', 'child__group')
            .annotate(balance_sum=Sum('transactions__amount'))
            .filter(balance_sum__lt=0).order_by('balance_sum'))


def debtors_summary(limit=5):
    """Qarzdorlar: soni, jami qarz va eng katta qarzdorlar (1 ta so'rov)."""
    accs = list(debtors())
    return {'count': len(accs),
            'total': -sum((a.balance_sum for a in accs), Decimal(0)),
            'top': [{'name': a.child.full_name, 'phone': a.child.parent_phone, 'due': -a.balance_sum}
                    for a in accs[:limit]]}


# ------------------------------------------------------------ TUG'ILGAN KUNLAR
def _birthday_in_year(b, year):
    try:
        return b.replace(year=year)
    except ValueError:  # 29-fevral
        return date(year, 3, 1)


def _month_day_filter(today, days):
    """Q: tug'ilgan oy/kun [bugun, bugun+days] oralig'iga tushadiganlar (29-fevral hisobga olingan)."""
    q, seen = Q(), set()
    for i in range(days + 1):
        d = today + timedelta(days=i)
        pairs = [(d.month, d.day)]
        if d.month == 3 and d.day == 1 and not calendar.isleap(d.year):
            pairs.append((2, 29))                      # 29-fevralda tug'ilganlar 1-martda nishonlanadi
        for m, dd in pairs:
            if (m, dd) not in seen:
                seen.add((m, dd))
                q |= Q(birth_date__month=m, birth_date__day=dd)
    return q


def upcoming_birthdays(days=30, kinds=None):
    """
    Yaqin tug'ilgan kunlar. kinds - {'child', 'teacher', 'user'} dan qaysilari kerak (None bo'lsa hammasi).
    Oyna kichik bo'lsa bazadan faqat mos oy/kun bo'yicha olinadi.
    """
    today = timezone.localdate()
    result = []
    window = _month_day_filter(today, days) if days <= 60 else Q()

    def add(name, label, kind, b):
        nxt = _birthday_in_year(b, today.year)
        if nxt < today:
            nxt = _birthday_in_year(b, today.year + 1)
        left = (nxt - today).days
        if left <= days:
            result.append({'name': name, 'type': label, 'kind': kind, 'date': nxt,
                           'days_left': left, 'turns': nxt.year - b.year})

    if kinds is None or 'child' in kinds:
        for c in Child.objects.filter(window, is_active=True).only('full_name', 'birth_date'):
            add(c.full_name, 'bola', 'child', c.birth_date)
    if kinds is None or 'teacher' in kinds:
        for t in Teacher.objects.filter(window, is_active=True).only('full_name', 'birth_date'):
            add(t.full_name, 'tarbiyachi', 'teacher', t.birth_date)
    if kinds is None or 'user' in kinds:
        for u in (User.objects.filter(window, is_active=True, birth_date__isnull=False)
                  .only('username', 'first_name', 'last_name', 'birth_date')):
            add(u.get_full_name() or u.username, 'xodim', 'user', u.birth_date)

    return sorted(result, key=lambda x: (x['days_left'], x['name']))


# ------------------------------------------------------------ BAYRAMLAR
def next_occurrence(holiday, today=None):
    """Bayramning eng yaqin kelasi sanasi (o'tib ketgan bir martalik bayram uchun None)."""
    today = today or timezone.localdate()
    if not holiday.repeat_yearly:
        return holiday.date if holiday.date >= today else None
    nxt = _birthday_in_year(holiday.date, today.year)
    if nxt < today:
        nxt = _birthday_in_year(holiday.date, today.year + 1)
    return nxt


def holidays_with_next():
    """Hamma bayram + keyingi sanasi va necha kun qolgani (yaqinlari tepada)."""
    today = timezone.localdate()
    rows = []
    for h in Holiday.objects.all():
        nxt = next_occurrence(h, today)
        rows.append({'holiday': h, 'next_date': nxt,
                     'days_left': (nxt - today).days if nxt else None})
    rows.sort(key=lambda r: (r['next_date'] is None, r['next_date'] or date.max))
    return rows


def upcoming_holidays(days=None):
    """Eslatish vaqti kelgan bayramlar. days berilmasa - har bayramning o'z 'necha kun oldin' qiymati."""
    out = []
    for r in holidays_with_next():
        h = r['holiday']
        if not h.is_active or r['next_date'] is None:
            continue
        limit = h.remind_days_before if days is None else days
        if r['days_left'] <= limit:
            out.append(r)
    return out


# ------------------------------------------------------------ BOT
@transaction.atomic
def register_location(telegram_id, lat, lon):
    """
    Bot location yuborganda chaqiriladi.
    Birinchi location = keldi, keyingilari = ketdi (oxirgisi saqlanadi).
    Qaytaradi: (ok: bool, xabar: str)
    """
    teacher = Teacher.objects.filter(telegram_id=telegram_id, is_active=True).first()
    if not teacher:
        return False, "Siz tarbiyachilar ro'yxatida yo'qsiz. Avval telefon raqamingizni yuboring."

    kg = Kindergarten.objects.first()
    if not kg:
        return False, "Bog'cha lokatsiyasi sozlanmagan. Adminga murojaat qiling."

    dist = kg.distance_m(lat, lon)
    if dist > kg.radius_m:
        return False, f"Siz bog'chadan uzoqdasiz ({int(dist)} m). Bog'cha hududida yuboring."

    now = timezone.localtime()
    att, _ = TeacherAttendance.objects.get_or_create(teacher=teacher, date=now.date())

    if att.check_in is None:
        att.check_in, att.check_in_lat, att.check_in_lon = now, lat, lon
        att.is_late = now.time() > kg.work_start
        att.save()
        return True, "Keldingiz deb belgilandi ✅" + (" (kechikdingiz ⏰)" if att.is_late else "")

    att.check_out, att.check_out_lat, att.check_out_lon = now, lat, lon
    att.save()
    return True, f"Ketdingiz deb belgilandi 👋 Bugun ishlagan vaqt: {att.worked_hours} soat"


@transaction.atomic
def link_telegram(phone, telegram_id):
    """Tarbiyachi botga telefon raqamini yuborganda telegram_id ni bog'laydi."""
    digits = ''.join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) < 9:
        return False, "Telefon raqam noto'g'ri."
    teacher = Teacher.objects.filter(phone__endswith=digits[-9:], is_active=True).first()
    if not teacher:
        return False, "Bu raqam tarbiyachilar ro'yxatida topilmadi."
    teacher.telegram_id = telegram_id
    teacher.save(update_fields=['telegram_id'])
    return True, f"Xush kelibsiz, {teacher.full_name}! Endi location yuborib keldi-ketdini belgilashingiz mumkin."