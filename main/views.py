"""
Har bo'lim - BITTA template (siz yaratgan fayllar):
    user.html, teacher.html, group.html, tariff.html, child.html, payment.html,
    expense.html, meal.html, product.html, notification.html
+ dashboard.html (bosh sahifa) va login.html (kirish) kerak bo'ladi.

Ishlash tartibi (modal oynalar bilan qulay):
    GET  sahifa      -> ro'yxat + bo'sh forma (yaratish modali uchun)
    POST /.../new/   -> yangi yozuv saqlash, so'ng shu sahifaga qaytadi
    POST /.../<pk>/edit/   -> tahrirlash
    POST /.../<pk>/delete/ yoki toggle/  -> o'chirish / faolsizlantirish
Xato bo'lsa messages orqali ko'rsatiladi.
"""
import hmac
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .decorators import is_login
from django.conf import settings
from django.contrib import messages
# from django.contrib.auth.decorators import is_login
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.forms import ModelChoiceField
from django.db.models import Count, F, ProtectedError, Q, Sum , Exists, OuterRef
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from . import attendance, stats, workdays
from .forms import (
    UserCreateForm, UserUpdateForm, TeacherForm, AttendanceForm, KindergartenForm,
    GroupForm, TariffForm, ChildForm, TransactionForm, PaymentForm, ChargeMonthForm,
    ExpenseCategoryForm, ExpenseForm, ProductForm, ProductInForm, ProductOutForm,
    MealForm, DailyMenuForm, NotificationTypeForm, NotificationPreferenceForm, HolidayForm,
)
from .models import (
    User, Kindergarten, Teacher, TeacherAttendance, Group, Tariff, Child, ChildAccount, ChildAttendance,
    Payment, ExpenseCategory, Expense, Product, ProductIn, ProductOut,
    Meal, DailyMenu, NotificationType, NotificationPreference, Holiday, DayOff, WorkSchedule,
)
from . import notifications as notif
from .notifications import ensure_default_types, get_user_pref
from bot.models import BotUser
from bot import services as bot_services
from bot.models import BotJobRun
from bot.telegram_api import send_message

# Template'laringiz 'templates/' ichida to'g'ridan-to'g'ri bo'lsa - bo'sh qoldiring.
# 'templates/bogcha/' ichida bo'lsa  TEMPLATE_DIR = 'bogcha/'  deb yozing.
TEMPLATE_DIR = ''


def tpl(name):
    return f'{TEMPLATE_DIR}{name}'


# ============================================================ YORDAMCHI FUNKSIYALAR
def admin_required(view):
    """Faqat admin. Yordamchi kirsa - bosh sahifaga qaytariladi."""
    @is_login
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_admin:
            messages.error(request, "Bu amal faqat admin uchun.")
            return redirect('dashboard')
        return view(request, *args, **kwargs)
    return wrapper


def parse_date(value, default=None):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return default


def parse_month(value, default=None):
    try:
        return datetime.strptime(value, '%Y-%m').date()
    except (TypeError, ValueError):
        return default


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def with_query(name, **params):
    url = reverse(name)
    qs = urlencode({k: v for k, v in params.items() if v})
    return f'{url}?{qs}' if qs else url


TRANSIENT_PARAMS = {'child', 'menu', 'sibling_of', 'back', 'cal'}   # oyna ochuvchi vaqtinchalik parametrlar


def back_url(request, default_url):
    """
    Formada <input type=hidden name=next value="..."> bo'lsa shu yerga qaytadi.
    ?menu= / ?child= kabi "oynani ochib qo'yadigan" parametrlar olib tashlanadi, aks holda
    saqlagandan keyin oyna yana ochilib qolardi.
    """
    nxt = request.POST.get('next', '')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        parts = urlsplit(nxt)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in TRANSIENT_PARAMS]
        return urlunsplit(('', '', parts.path, urlencode(query), parts.fragment))
    return default_url


def form_errors(form):
    parts = []
    for name, errs in form.errors.items():
        label = form.fields[name].label if name in form.fields and form.fields[name].label else None
        text = ' '.join(errs)
        parts.append(f'{label}: {text}' if label else text)
    return ' | '.join(parts)


def save_form(request, form_class, back, instance=None, before_save=None, after_save=None,
              ok="Saqlandi ✅"):
    """Hamma POST-saqlashlar shu yerdan o'tadi: tekshiradi, saqlaydi, xabar beradi, qaytadi."""
    form = form_class(request.POST, request.FILES, instance=instance)
    if form.is_valid():
        obj = form.save(commit=False)
        if before_save:
            before_save(obj)
        try:
            obj.save()
            form.save_m2m()
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
            return redirect(back)
        if after_save:
            after_save(obj)
        messages.success(request, ok)
    else:
        messages.error(request, 'Xatolik: ' + form_errors(form))
    return redirect(back)


def toggle_active(request, obj, back, what):
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    messages.success(request, f"{what} {'faollashtirildi ✅' if obj.is_active else 'arxivga olindi 📦'}")
    return redirect(back)


def safe_delete(request, obj, back, what="Yozuv"):
    try:
        obj.delete()
        messages.success(request, f"{what} o'chirildi 🗑")
    except ProtectedError:
        messages.error(request, f"{what}ni o'chirib bo'lmaydi: unga boshqa ma'lumotlar bog'langan.")
    return redirect(back)


def paginate(request, qs, per=25):
    page = Paginator(qs, per).get_page(request.GET.get('page'))
    q = request.GET.copy()
    q.pop('page', None)
    return page, q.urlencode()


def share_choices(*forms_, **overrides):
    """
    SQL TEJASH. Bir sahifada bir xil <select> bir necha formada bo'lsa (yaratish va tahrirlash modali),
    ro'yxat bazadan BIR marta olinadi. overrides={'group': [(id, nom), ...]} - tayyor ro'yxat berilsa
    umuman so'rov ketmaydi. Faqat ko'rsatish (GET) uchun, tekshiruv (POST) formalariga qo'llanmaydi.
    """
    shared = {}
    for form in forms_:
        for name, field in form.fields.items():
            if not isinstance(field, ModelChoiceField):
                continue
            if name in overrides:
                field.choices = overrides[name]
                continue
            try:
                key = (name, str(field.queryset.query))
            except Exception:                      # bo'sh queryset va h.k. - ulashmaymiz
                continue
            if key not in shared:
                shared[key] = [c for c in field.choices]     # list(...) emas: u qo'shimcha COUNT(*) so'rovi yuboradi
            field.choices = shared[key]


def choices_of(objs, label=str):
    return [('', '---------')] + [(o.pk, label(o)) for o in objs]


def month_defaults(request, extra_keys=()):
    """Filtr berilmagan bo'lsa - joriy oy oralig'i."""
    today = timezone.localdate()
    g = request.GET
    given = any(k in g for k in ('date_from', 'date_to') + tuple(extra_keys))
    d_from = parse_date(g.get('date_from'), None if given else today.replace(day=1))
    d_to = parse_date(g.get('date_to'), None if given else today)
    return d_from, d_to


# ============================================================ BOSH SAHIFA  (dashboard.html)
@is_login
def dashboard(request):
    """Hamma raqamlar keshlangan bo'laklardan (notifications.py): iliq keshda atigi 2 ta so'rov (guruhlar soni, bugungi menyu)."""
    today = timezone.localdate()
    counts, att = notif.get_counts(), notif.get_attendance()
    menu = list(DailyMenu.objects.filter(date=today).select_related('meal').order_by('meal_time'))
    ctx = {
        'today': today,
        'children_count': counts['children_count'],
        'teachers_count': att['teachers_count'],
        'groups_count': Group.objects.count(),
        'present_today': att['present_today'],
        'absent_teachers': att['absent'],
        'low_products': counts['low_products'],
        'today_menu': menu,
        'today_menu_total': sum(m.cost for m in menu),
    }
    if request.user.is_admin:
        d = notif.get_debtors()
        ctx.update({'finance': notif.get_finance_month(), 'debtors': d['top'],
                    'debtors_count': d['count'], 'debt_total': d['total']})
    return render(request, tpl('dashboard.html'), ctx)


# ============================================================ USER  (user.html)
@admin_required
def user_list(request):
    q = request.GET.get('q', '').strip()
    users = User.objects.order_by('-is_active', 'role', 'first_name', 'username')
    if q:
        users = users.filter(Q(username__icontains=q) | Q(first_name__icontains=q) |
                             Q(last_name__icontains=q) | Q(phone__icontains=q))
    return render(request, tpl('user.html'), {
        'users': users, 'q': q,
        'form': UserCreateForm(),            # yaratish modali uchun
        'edit_form': UserUpdateForm(),       # tahrirlash modali uchun (maydonlar nomi)
    })


@admin_required
@require_POST
def user_create(request):
    return save_form(request, UserCreateForm, back_url(request, reverse('user_list')),
                     ok="Foydalanuvchi qo'shildi ✅")


@admin_required
@require_POST
def user_update(request, pk):
    user = get_object_or_404(User, pk=pk)
    return save_form(request, UserUpdateForm, back_url(request, reverse('user_list')), instance=user,
                     ok="O'zgartirildi ✅")


@admin_required
@require_POST
def user_toggle(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        messages.error(request, "O'zingizni faolsizlantira olmaysiz.")
        return redirect('user_list')
    return toggle_active(request, user, reverse('user_list'), 'Foydalanuvchi')


# ============================================================ TARBIYACHI + DAVOMAT  (teacher.html)
def _teacher_month(teacher, month_start, atts=None, info=None):
    month_end = stats.add_months(
        month_start,
        1,
    )

    if atts is None:
        atts = list(
            teacher.attendances
            .filter(
                date__gte=month_start,
                date__lt=month_end,
            )
            .order_by('date')
        )

    today = timezone.localdate()

    att_map = {
        a.date: a
        for a in atts
    }

    weeks = stats.month_weeks(
        month_start,
        att_map,
    )

    # info tashqaridan berilmasa eski usulda ishlaydi
    if info is None:
        info = workdays.status_map(
            weeks[0][0]['date'],
            weeks[-1][-1]['date'],
        )

    for week in weeks:
        for cell in week:
            a = cell['data']
            d = cell['date']

            cell['off'] = not info[d]['workday']
            cell['off_label'] = info[d]['label']

            if a and a.check_in:
                cell['status'] = (
                    'late'
                    if a.is_late
                    else 'ok'
                )

            elif (
                cell['in_month']
                and d < today
                and info[d]['workday']
            ):
                cell['status'] = 'absent'

            else:
                cell['status'] = ''

    return {
        'teacher': teacher,
        'month': month_start,

        'prev': stats.add_months(
            month_start,
            -1,
        ),

        'next': month_end,

        'weeks': weeks,
        'attendances': atts,

        'days': sum(
            1
            for a in atts
            if a.check_in
        ),

        'late': sum(
            1
            for a in atts
            if a.is_late
        ),

        'hours': round(
            sum(
                a.worked_hours
                for a in atts
            ),
            2,
        ),
    }

@is_login
def teacher_list(request):
    today = timezone.localdate()

    day = parse_date(
        request.GET.get('date'),
        today,
    )

    q = request.GET.get('q', '').strip()

    # =========================================================
    # OYLAR
    # =========================================================

    am = parse_month(
        request.GET.get('amonth'),
        today.replace(day=1),
    )

    am_next = stats.add_months(
        am,
        1,
    )

    wm = parse_month(
        request.GET.get('wmonth'),
        today.replace(day=1),
    )

    # =========================================================
    # TEACHERLAR
    # =========================================================

    qs = Teacher.objects.order_by(
        '-is_active',
        'full_name',
    )

    if q:
        qs = qs.filter(
            Q(full_name__icontains=q)
            |
            Q(phone__icontains=q)
        )

    teachers = list(qs)

    if q:
        active = list(
            Teacher.objects
            .filter(is_active=True)
            .order_by('full_name')
        )
    else:
        active = [
            teacher
            for teacher in teachers
            if teacher.is_active
        ]

    active_ids = [
        teacher.pk
        for teacher in active
    ]

    # =========================================================
    # ATTENDANCE
    # =========================================================

    attendance_list = []

    if active_ids:
        attendance_list = list(
            TeacherAttendance.objects
            .filter(
                teacher_id__in=active_ids,
            )
            .filter(
                Q(
                    date__gte=am,
                    date__lt=am_next,
                )
                |
                Q(date=day)
            )
            .order_by('date')
        )

    # day uchun
    att_map = {}

    # oy uchun
    by_teacher = defaultdict(dict)

    for attendance in attendance_list:
        teacher_id = attendance.teacher_id

        if am <= attendance.date < am_next:
            by_teacher[
                teacher_id
            ][attendance.date] = attendance

        if attendance.date == day:
            att_map[teacher_id] = attendance

    # =========================================================
    # KUNLIK ROWLAR
    # =========================================================

    rows = [
        {
            'teacher': teacher,
            'att': att_map.get(teacher.pk),
        }
        for teacher in active
    ]

    present_count = sum(
        1
        for row in rows
        if row['att']
        and row['att'].check_in
    )

    # =========================================================
    # HAFTALIK CALENDAR
    # =========================================================

    w_weeks = stats.month_weeks(wm)

    # =========================================================
    # STATUS_MAP NI FAQAT 1 MARTA OLAMIZ
    # =========================================================
    #
    # Uchta joy:
    #
    #   _teacher_month()
    #   am_info
    #   w_info
    #
    # uchun bir xil katta intervaldan foydalanamiz.
    #

    am_days = [
        am + timedelta(days=i)
        for i in range(
            (am_next - am).days
        )
    ]

    am_start = am_days[0]
    am_end = am_days[-1]

    w_start = w_weeks[0][0]['date']
    w_end = w_weeks[-1][-1]['date']

    # Selected teacher ham shu am oyidan foydalanadi.
    status_start = min(
        am_start,
        w_start,
    )

    status_end = max(
        am_end,
        w_end,
    )

    # FAQAT 1 TA status_map()
    status_info = workdays.status_map(
        status_start,
        status_end,
    )

    # =========================================================
    # SELECTED TEACHER
    # =========================================================

    selected = None

    sel_id = to_int(
        request.GET.get('id')
    )

    if sel_id:

        selected_teacher = next(
            (
                teacher
                for teacher in teachers
                if teacher.pk == sel_id
            ),
            None,
        )

        if selected_teacher is None:
            selected_teacher = (
                Teacher.objects
                .filter(pk=sel_id)
                .first()
            )

        if selected_teacher:

            selected_atts = [
                attendance
                for attendance in attendance_list
                if (
                    attendance.teacher_id
                    == selected_teacher.pk
                    and am
                    <= attendance.date
                    < am_next
                )
            ]

            # Active teacher bo'lsa oldindan olingan
            # attendance ishlatiladi.
            #
            # info ham oldindan olingan.
            #
            # Natijada _teacher_month() ichida
            # SQL qilinmaydi.
            if selected_teacher.pk in active_ids:
                selected = _teacher_month(
                    selected_teacher,
                    am,
                    atts=selected_atts,
                    info=status_info,
                )

            else:
                selected = _teacher_month(
                    selected_teacher,
                    am,
                    info=status_info,
                )

    # =========================================================
    # FORMLAR
    # =========================================================

    form = TeacherForm()
    edit_form = TeacherForm()

    att_form = AttendanceForm(
        initial={
            'date': day,
        }
    )

    if not q:
        share_choices(
            att_form,
            teacher=choices_of(teachers),
        )

    # =========================================================
    # CONTEXT
    # =========================================================

    ctx = {
        'teachers': teachers,
        'q': q,

        'day': day,
        'prev_day': day - timedelta(days=1),
        'next_day': day + timedelta(days=1),

        'rows': rows,
        'present_count': present_count,

        'selected': selected,

        'form': form,
        'edit_form': edit_form,
        'att_form': att_form,

        'link_actions': [
            (
                'in',
                'send',
                'send',
                "«Keldim» tugmasini botga yuborish",
            ),
            (
                'out',
                'send',
                'send',
                "«Ketdim» tugmasini botga yuborish",
            ),
            (
                'in',
                'show',
                'link-45deg',
                "«Keldim» havolasini ko'rsatish",
            ),
            (
                'out',
                'show',
                'link-45deg',
                "«Ketdim» havolasini ko'rsatish",
            ),
        ],
    }

    # =========================================================
    # OYLIK ATTENDANCE
    # =========================================================

    am_info = status_info

    am_rows = []

    for teacher in active:

        teacher_attendance = by_teacher.get(
            teacher.pk,
            {},
        )

        cells = []
        came = 0
        late = 0
        hours = 0

        for d in am_days:

            attendance = teacher_attendance.get(d)

            if attendance and attendance.check_in:

                status = (
                    'late'
                    if attendance.is_late
                    else 'ok'
                )

                came += 1

                if attendance.is_late:
                    late += 1

                hours += attendance.worked_hours

            elif d > today:

                status = 'future'

            elif not am_info[d]['workday']:

                status = 'off'

            else:

                status = 'absent'

            cells.append({
                'date': d,
                'status': status,
                'att': attendance,
            })

        am_rows.append({
            'teacher': teacher,
            'cells': cells,
            'came': came,
            'late': late,
            'hours': round(hours, 2),
        })

    wd_names = [
        'Du',
        'Se',
        'Ch',
        'Pa',
        'Ju',
        'Sh',
        'Ya',
    ]

    ctx.update({
        'am_month': am,

        'am_prev': stats.add_months(
            am,
            -1,
        ),

        'am_next': am_next,

        'am_days': [
            {
                'date': d,
                'wd': wd_names[d.weekday()],
                'off': not am_info[d]['workday'],
            }
            for d in am_days
        ],

        'am_rows': am_rows,
    })

    # =========================================================
    # HAFTALIK
    # =========================================================

    # status_map() qayta chaqirilmaydi.
    w_info = status_info

    for week in w_weeks:
        for cell in week:
            cell['info'] = w_info[
                cell['date']
            ]

    ctx.update({
        'w_month': wm,

        'w_prev': stats.add_months(
            wm,
            -1,
        ),

        'w_next': stats.add_months(
            wm,
            1,
        ),

        'w_weeks': w_weeks,

        'w_weekly': workdays.get_weekdays(),

        'weekday_names': (
            workdays.WEEKDAY_NAMES
        ),

        'w_upcoming': list(
            DayOff.objects
            .filter(
                date__gte=today,
            )
            .select_related('created_by')[:30]
        ),
    })

    # =========================================================
    # ADMIN
    # =========================================================

    if request.user.is_admin:

        ctx['kg_form'] = KindergartenForm(
            instance=Kindergarten.objects.first()
        )

        # SQL ichida EXISTS.
        # Alohida Teacher/User ID query kerak emas.
        teacher_exists = Teacher.objects.filter(
            telegram_id=OuterRef(
                'telegram_id'
            )
        )

        user_exists = User.objects.filter(
            telegram_id=OuterRef(
                'telegram_id'
            )
        )

        pending = list(
            BotUser.objects
            .filter(
                verified=True,
            )
            .annotate(
                is_teacher=Exists(
                    teacher_exists
                ),
                is_user=Exists(
                    user_exists
                ),
            )
            .filter(
                is_teacher=False,
                is_user=False,
            )
        )

        ctx['bot_pending'] = pending

        if pending:

            ctx['bot_teachers'] = list(
                Teacher.objects
                .filter(
                    is_active=True,
                    telegram_id__isnull=True,
                )
                .order_by('full_name')
            )

            ctx['bot_users'] = list(
                User.objects
                .filter(
                    is_active=True,
                    telegram_id__isnull=True,
                )
                .order_by('username')
            )

    # =========================================================
    # RENDER
    # =========================================================

    return render(
        request,
        tpl('teacher.html'),
        ctx,
    )

@admin_required
@require_POST
def teacher_create(request):
    return save_form(request, TeacherForm, back_url(request, reverse('teacher_list')),
                     ok="Tarbiyachi qo'shildi ✅")


@admin_required
@require_POST
def teacher_update(request, pk):
    return save_form(request, TeacherForm, back_url(request, reverse('teacher_list')),
                     instance=get_object_or_404(Teacher, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def teacher_toggle(request, pk):
    return toggle_active(request, get_object_or_404(Teacher, pk=pk), reverse('teacher_list'), 'Tarbiyachi')


@admin_required
@require_POST
def attendance_save(request):
    """Bot ishlamay qolsa - admin qo'lda keldi/ketdini kiritadi yoki tuzatadi."""
    teacher_id = to_int(request.POST.get('teacher'))
    d = parse_date(request.POST.get('date'))
    inst = None
    if teacher_id and d:
        inst = TeacherAttendance.objects.filter(teacher_id=teacher_id, date=d).first()
    back = back_url(request, with_query('teacher_list', date=request.POST.get('date')))
    return save_form(request, AttendanceForm, back, instance=inst, ok="Davomat saqlandi ✅")


@admin_required
@require_POST
def kindergarten_save(request):
    """Bog'cha lokatsiyasi va radiusi (bot shu bo'yicha tekshiradi)."""
    return save_form(request, KindergartenForm, back_url(request, reverse('teacher_list')),
                     instance=Kindergarten.objects.first(), ok="Lokatsiya saqlandi ✅")


# ============================================================ GURUH  (group.html)
@is_login
def group_list(request):
    groups = (Group.objects.select_related('teacher')
              .annotate(child_count=Count('children', filter=Q(children__is_active=True)))
              .order_by('name'))
    selected = None
    sel_id = to_int(request.GET.get('id'))
    if sel_id:
        g = Group.objects.select_related('teacher').filter(pk=sel_id).first()
        if g:
            selected = {'group': g,
                        'children': g.children.filter(is_active=True).order_by('full_name')}
    form, edit_form = GroupForm(), GroupForm()
    share_choices(form, edit_form)
    return render(request, tpl('group.html'), {
        'groups': groups, 'selected': selected,
        'form': form, 'edit_form': edit_form,
    })


@admin_required
@require_POST
def group_create(request):
    return save_form(request, GroupForm, back_url(request, reverse('group_list')), ok="Guruh qo'shildi ✅")


@admin_required
@require_POST
def group_update(request, pk):
    return save_form(request, GroupForm, back_url(request, reverse('group_list')),
                     instance=get_object_or_404(Group, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def group_delete(request, pk):
    return safe_delete(request, get_object_or_404(Group, pk=pk), reverse('group_list'), 'Guruh')


@is_login
def group_detail(request, pk):
    """Guruh sahifasi: bolalar, kunlik 'keldi' belgisi, oylik davomat jadvali va har bola kalendari."""
    group = get_object_or_404(Group.objects.select_related('teacher'), pk=pk)
    today = timezone.localdate()
    day = min(parse_date(request.GET.get('date'), today), today)      # kelajak kunga belgilab bo'lmaydi
    month_start = day.replace(day=1)
    month_end = stats.add_months(month_start, 1)

    children = list(group.children.filter(is_active=True).select_related('tariff').order_by('full_name'))
    came = defaultdict(set)
    for cid, d in (ChildAttendance.objects
                   .filter(child_id__in=[c.pk for c in children], date__gte=month_start, date__lt=month_end)
                   .values_list('child_id', 'date')):
        came[cid].add(d)

    statuses = stats.payment_status_map({c.family_group_id or c.pk for c in children}, known=children)
    days = [{'date': month_start + timedelta(days=i)} for i in range((month_end - month_start).days)]
    for d in days:
        d['weekend'] = d['date'].weekday() >= 5
        d['future'] = d['date'] > today

    rows = []
    for c in children:
        dates = came[c.pk]
        rows.append({
            'child': c, 'came_today': day in dates, 'count': len(dates),
            'status': statuses.get(c.family_group_id or c.pk, stats.NO_STATUS),
            'cells': [{'came': d['date'] in dates, 'weekend': d['weekend'], 'future': d['future']} for d in days],
        })

    # Bitta bolaning kalendari (?cal=7)
    cal_child, cal_weeks, cal_dates = None, [], []
    cal_id = to_int(request.GET.get('cal'))
    if cal_id:
        cal_child = next((c for c in children if c.pk == cal_id), None)
        if cal_child:
            cal_dates = sorted(came[cal_child.pk])
            cal_weeks = stats.month_weeks(month_start, {d: True for d in cal_dates})

    next_month = stats.add_months(month_start, 1)
    return render(request, tpl('group_detail.html'), {
        'group': group, 'today': today, 'day': day,
        'prev_day': day - timedelta(days=1), 'next_day': day + timedelta(days=1) if day < today else None,
        'month_start': month_start, 'prev_month': stats.add_months(month_start, -1),
        'next_month': next_month if next_month <= today else None,
        'days': days, 'rows': rows, 'present_count': sum(1 for r in rows if r['came_today']),
        'cal_child': cal_child, 'cal_weeks': cal_weeks, 'cal_dates': cal_dates,
    })


@is_login
@require_POST
def group_attendance_save(request, pk):
    """Tanlangan kun uchun belgilangan bolalar = keldi. Belgisi olinganlar = kelmagan (yozuv o'chadi)."""
    group = get_object_or_404(Group, pk=pk)
    back = back_url(request, reverse('group_detail', args=[pk]))
    day = parse_date(request.POST.get('date'))
    if not day or day > timezone.localdate():
        messages.error(request, "Sana noto'g'ri yoki kelajakdagi kun.")
        return redirect(back)

    group_ids = set(group.children.filter(is_active=True).values_list('id', flat=True))
    present = {int(x) for x in request.POST.getlist('present') if x.isdigit()} & group_ids
    with transaction.atomic():
        existing = set(ChildAttendance.objects.filter(date=day, child_id__in=group_ids)
                       .values_list('child_id', flat=True))
        ChildAttendance.objects.bulk_create(
            [ChildAttendance(child_id=cid, date=day, marked_by=request.user) for cid in present - existing])
        ChildAttendance.objects.filter(date=day, child_id__in=existing - present).delete()
    messages.success(request, f"{day:%d.%m.%Y}: {len(present)} ta bola keldi deb saqlandi ✅")
    return redirect(back)


# ============================================================ TARIF  (tariff.html)
@is_login
def tariff_list(request):
    tariffs = (Tariff.objects
               .annotate(child_count=Count('children', filter=Q(children__is_active=True)))
               .order_by('-is_active', 'price'))
    return render(request, tpl('tariff.html'), {
        'tariffs': tariffs, 'form': TariffForm(), 'edit_form': TariffForm(),
    })


@admin_required
@require_POST
def tariff_create(request):
    return save_form(request, TariffForm, back_url(request, reverse('tariff_list')), ok="Tarif qo'shildi ✅")


@admin_required
@require_POST
def tariff_update(request, pk):
    return save_form(request, TariffForm, back_url(request, reverse('tariff_list')),
                     instance=get_object_or_404(Tariff, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def tariff_toggle(request, pk):
    return toggle_active(request, get_object_or_404(Tariff, pk=pk), reverse('tariff_list'), 'Tarif')


# ============================================================ BOLA  (child.html)
@is_login
def child_list(request):
    g = request.GET
    q = g.get('q', '').strip()
    status = g.get('status', 'active')
    group_id = to_int(g.get('group'))

    qs = Child.objects.select_related('group', 'tariff', 'family_group').order_by('full_name')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    if group_id:
        qs = qs.filter(group_id=group_id)
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(parent_name__icontains=q) | Q(parent_phone__icontains=q))
    page, querystring = paginate(request, qs)

    # To'lov holati: yashil - to'liq, sariq - qisman, qizil - to'lamagan (tayyor obyektlar qayta so'ralmaydi)
    children = list(page.object_list)
    statuses = stats.payment_status_map({c.family_group_id or c.pk for c in children}, known=children)
    for c in children:
        c.pay_status = statuses.get(c.family_group_id or c.pk, stats.NO_STATUS)

    # Tanlangan bola: balans, aka-ukalar, to'lovlar (?id=7)
    selected = None
    sel_id = to_int(g.get('id'))
    if sel_id:
        child = (Child.objects.select_related('group', 'tariff', 'family_group__tariff')
                 .filter(pk=sel_id).first())
        if child:
            main = child.billing_child
            family = list(Child.objects.filter(Q(pk=main.pk) | Q(family_group=main)).only('full_name'))
            # hisob va balans BITTA so'rovda (balance property har safar alohida SQL yuboradi)
            account = ChildAccount.objects.filter(child=main).annotate(bal=Sum('transactions__amount')).first()
            balance = (account.bal or 0) if account else 0
            m_start = timezone.localdate().replace(day=1)
            came_days = ChildAttendance.objects.filter(
                child=child, date__gte=m_start, date__lt=stats.add_months(m_start, 1)).count()
            selected = {
                'came_days': came_days, 'month': m_start,
                'child': child, 'main': main, 'tariff': child.effective_tariff,
                'family': family, 'account': account, 'balance': balance,
                'transactions': (list(account.transactions.select_related('created_by').order_by('-created_at')[:30])
                                 if account else []),
                'payments': list(Payment.objects.filter(children__in=family).distinct().order_by('-paid_date')[:10]),
            }

    # "Aka-uka qo'shish" tugmasi: ?sibling_of=3 -> forma oldindan to'ldiriladi
    initial = {}
    sib = (Child.objects.select_related('family_group').filter(pk=to_int(g.get('sibling_of'))).first()
           if g.get('sibling_of') else None)
    if sib:
        main = sib.billing_child
        initial = {'family_group': main.pk, 'parent_name': main.parent_name,
                   'parent_phone': main.parent_phone, 'group': main.group_id}

    tx_form = TransactionForm()
    if not request.user.is_admin:
        tx_form.fields['type'].choices = [c for c in tx_form.fields['type'].choices if c[0] != 'correction']

    # guruhlar ro'yxati bir marta olinadi: filtr ham, formadagi <select> ham shundan foydalanadi
    groups = list(Group.objects.order_by('name'))
    form, edit_form = ChildForm(initial=initial), ChildForm()
    share_choices(form, edit_form, group=choices_of(groups, lambda x: x.name))

    return render(request, tpl('child.html'), {
        'page_obj': page, 'children': children, 'querystring': querystring,
        'q': q, 'status': status, 'group_id': group_id, 'groups': groups,
        'total': page.paginator.count, 'selected': selected,
        'form': form, 'open_create': bool(sib),
        'edit_form': edit_form, 'transaction_form': tx_form,
    })


@is_login
@require_POST
def child_create(request):
    return save_form(request, ChildForm, back_url(request, reverse('child_list')), ok="Bola qo'shildi ✅")


@is_login
@require_POST
def child_update(request, pk):
    return save_form(request, ChildForm, back_url(request, with_query('child_list', id=pk)),
                     instance=get_object_or_404(Child, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def child_toggle(request, pk):
    return toggle_active(request, get_object_or_404(Child, pk=pk), reverse('child_list'), 'Bola')


@is_login
@require_POST
def transaction_create(request, pk):
    """Bolaning hisobiga qo'lda chegirma / jarima. 'Tuzatish' (correction) faqat admin uchun."""
    child = get_object_or_404(Child, pk=pk)
    if request.POST.get('type') == 'correction' and not request.user.is_admin:
        messages.error(request, "'Tuzatish' turini faqat admin kirita oladi.")
        return redirect(with_query('child_list', id=pk))
    account = child.get_account()

    def fill(obj):
        obj.account = account
        obj.created_by = request.user

    return save_form(request, TransactionForm, with_query('child_list', id=pk), before_save=fill,
                     ok="Hisobga yozildi ✅")


# ============================================================ TO'LOV + QARZDORLAR  (payment.html)
@is_login
def payment_list(request):
    g = request.GET
    q = g.get('q', '').strip()
    status = g.get('status', '')
    d_from, d_to = month_defaults(request, extra_keys=('q', 'status'))

    qs = (Payment.objects.prefetch_related('children').select_related('received_by')
          .order_by('-paid_date', '-id'))
    if q:
        qs = qs.filter(Q(children__full_name__icontains=q) | Q(children__parent_name__icontains=q) |
                       Q(children__parent_phone__icontains=q)).distinct()
    if status:
        qs = qs.filter(status=status)
    if d_from:
        qs = qs.filter(paid_date__gte=d_from)
    if d_to:
        qs = qs.filter(paid_date__lte=d_to)

    total = qs.filter(status='confirmed').aggregate(s=Sum('amount'))['s'] or 0
    page, querystring = paginate(request, qs)

    debtors = list(stats.debtors())

    # ?child=5 -> to'lov oynasi FAQAT shu bola bilan ochiladi; summa = QOLGAN qarz
    # (tarif 300 000, 100 000 to'langan bo'lsa - 2-to'lovda 200 000 chiqadi)
    child = (Child.objects.select_related('group', 'tariff', 'family_group__tariff')
             .filter(pk=to_int(g.get('child')), is_active=True).first() if g.get('child') else None)
    pay_child = stats.pay_info(child) if child else None
    initial = {'paid_date': timezone.localdate()}
    if pay_child and pay_child['due'] > 0:
        initial['amount'] = pay_child['due']

    # ?back=/groups/3/ -> to'lovdan keyin shu sahifaga qaytadi (xavfsiz havolalar bo'lsa)
    back = g.get('back', '')
    if not (back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()},
                                                     require_https=request.is_secure())):
        back = ''

    return render(request, tpl('payment.html'), {
        'page_obj': page, 'payments': page.object_list, 'querystring': querystring,
        'q': q, 'status': status, 'date_from': d_from, 'date_to': d_to,
        'total_amount': total, 'status_choices': Payment.STATUS_CHOICES,
        'debtors': debtors, 'debt_total': -sum(a.balance_sum for a in debtors),
        'form': PaymentForm(initial=initial), 'open_create': bool(child),
        'pay_child': pay_child, 'back': back,
        'charge_form': ChargeMonthForm(),
    })


@is_login
@require_POST
def payment_create(request):
    back = back_url(request, reverse('payment_list'))
    form = PaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Xatolik: ' + form_errors(form))
        return redirect(back)

    child = form.cleaned_data['child']
    main = child.billing_child
    try:
        with transaction.atomic():
            p = form.save(commit=False)
            p.received_by = request.user
            p.status = 'confirmed'
            p.tariff = main.tariff
            p.save()
            # aka-uka bo'lsa butun oila to'lovga yoziladi, pul esa asosiy bolaning umumiy hisobiga tushadi
            p.children.set(Child.objects.filter(Q(pk=main.pk) | Q(family_group=main), is_active=True))
            p.distribute()
        messages.success(request, f"To'lov qabul qilindi ✅ {child.full_name}: {p.amount:,.0f} so'm".replace(',', ' '))
    except ValueError as e:
        messages.error(request, str(e))
    return redirect(back)


@is_login
def payment_child_info(request, pk):
    """JSON: tanlangan bolaning tarifi, to'langani va QOLGAN summasi (to'lov oynasi uchun)."""
    child = get_object_or_404(Child.objects.select_related('group', 'tariff', 'family_group__tariff'),
                              pk=pk, is_active=True)
    return JsonResponse(stats.pay_info(child))


@is_login
def payment_child_search(request):
    """JSON: to'lov oynasida bolani ism / ota-ona / telefon bo'yicha qidirish."""
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': []})
    kids = list(Child.objects.filter(is_active=True)
                .filter(Q(full_name__icontains=q) | Q(parent_name__icontains=q) | Q(parent_phone__icontains=q))
                .select_related('group', 'tariff').order_by('full_name')[:12])
    st = stats.payment_status_map({c.family_group_id or c.pk for c in kids}, known=kids)
    return JsonResponse({'results': [{
        'id': c.pk, 'name': c.full_name, 'group': c.group.name if c.group else '',
        'parent': c.parent_name or '', 'phone': c.parent_phone,
        'status': st.get(c.family_group_id or c.pk, stats.NO_STATUS)['code'],
    } for c in kids]})


@admin_required
@require_POST
def payment_cancel(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    payment.cancel(user=request.user)
    messages.success(request, f"To'lov #{payment.pk} bekor qilindi")
    return redirect(back_url(request, reverse('payment_list')))


@admin_required
@require_POST
def charge_month(request):
    """Oylik hisoblash: har ASOSIY bolaga (aka-uka bo'lsa ham BITTA) oylik yoziladi."""
    form = ChargeMonthForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Xatolik: ' + form_errors(form))
        return redirect('payment_list')

    start = form.cleaned_data['month'].replace(day=1)
    end = stats.add_months(start, 1) - timedelta(days=1)
    mains = (Child.objects.filter(family_group__isnull=True, tariff__isnull=False)
             .filter(Q(is_active=True) | Q(siblings__is_active=True)).distinct().select_related('tariff'))
    created = skipped = 0
    with transaction.atomic():
        for child in mains:
            if child.get_account().charge_month(start, end, created_by=request.user):
                created += 1
            else:
                skipped += 1
    messages.success(request, f"{start:%Y-%m} uchun {created} ta oilaga oylik yozildi"
                              + (f" ({skipped} tasi oldin yozilgan)" if skipped else ""))
    return redirect('payment_list')


# ============================================================ CHIQIM + STATISTIKA  (expense.html)
@admin_required
def expense_list(request):
    g = request.GET
    q = g.get('q', '').strip()
    cat_id = to_int(g.get('category'))
    d_from, d_to = month_defaults(request, extra_keys=('q', 'category'))

    qs = Expense.objects.select_related('category', 'created_by').order_by('-date', '-id')
    if cat_id:
        qs = qs.filter(category_id=cat_id)
    if q:
        qs = qs.filter(description__icontains=q)
    if d_from:
        qs = qs.filter(date__gte=d_from)
    if d_to:
        qs = qs.filter(date__lte=d_to)

    by_category = list(qs.values('category__name').annotate(t=Sum('amount')).order_by('-t'))
    total = sum((r['t'] for r in by_category), 0)         # alohida SUM so'rovi kerak emas
    page, querystring = paginate(request, qs, per=30)

    # ---- Statistika: 1 oy / 3 oy / 6 oy / yil - hammasi atigi 2 ta so'rovda
    ov = stats.finance_overview(g.get('period', '1m'))
    summary, rows = ov['summary'], ov['rows']
    period = summary['period']
    chart = {  # template'da: {{ chart|json_script:"chart-data" }}
        'labels': [r['month'] for r in rows],
        'income': [float(r['income']) for r in rows],
        'expense': [float(r['expense']) for r in rows],
        'profit': [float(r['profit']) for r in rows],
    }
    cat_chart = {
        'labels': [c['category__name'] for c in summary['expense_by_category']],
        'values': [float(c['total']) for c in summary['expense_by_category']],
    }

    categories = list(ExpenseCategory.objects.order_by('name'))       # filtr, jadval va formalar uchun BIR marta
    form, edit_form = ExpenseForm(initial={'date': timezone.localdate()}), ExpenseForm()
    share_choices(form, edit_form, category=choices_of(categories, lambda c: c.name))

    return render(request, tpl('expense.html'), {
        'page_obj': page, 'expenses': page.object_list, 'querystring': querystring,
        'q': q, 'category_id': cat_id, 'date_from': d_from, 'date_to': d_to,
        'total_amount': total, 'by_category': by_category, 'categories': categories,
        # statistika
        'period': period, 'periods': stats.PERIOD_LABELS, 'summary': summary,
        'all_periods': ov['all_periods'],
        'chart_rows': rows, 'chart': chart, 'cat_chart': cat_chart,
        # formalar
        'form': form, 'edit_form': edit_form,
        'category_form': ExpenseCategoryForm(), 'category_edit_form': ExpenseCategoryForm(),
    })


@admin_required
@require_POST
def expense_create(request):
    return save_form(request, ExpenseForm, back_url(request, reverse('expense_list')),
                     before_save=lambda o: setattr(o, 'created_by', request.user), ok="Chiqim qo'shildi ✅")


@admin_required
@require_POST
def expense_update(request, pk):
    return save_form(request, ExpenseForm, back_url(request, reverse('expense_list')),
                     instance=get_object_or_404(Expense, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def expense_delete(request, pk):
    return safe_delete(request, get_object_or_404(Expense, pk=pk), back_url(request, reverse('expense_list')), 'Chiqim')


@admin_required
@require_POST
def category_create(request):
    return save_form(request, ExpenseCategoryForm, back_url(request, reverse('expense_list')),
                     ok="Kategoriya qo'shildi ✅")


@admin_required
@require_POST
def category_update(request, pk):
    return save_form(request, ExpenseCategoryForm, back_url(request, reverse('expense_list')),
                     instance=get_object_or_404(ExpenseCategory, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def category_delete(request, pk):
    return safe_delete(request, get_object_or_404(ExpenseCategory, pk=pk), reverse('expense_list'), 'Kategoriya')


# ============================================================ TAOM + KUNLIK/HAFTALIK/OYLIK SUMMA  (meal.html)
@is_login
def meal_list(request):
    today = timezone.localdate()
    day = parse_date(request.GET.get('date'), today)

    week_start = day - timedelta(days=day.weekday())          # dushanba
    week_end = week_start + timedelta(days=6)                 # yakshanba
    month_start = day.replace(day=1)
    month_end = stats.add_months(day, 1) - timedelta(days=1)

    # 1) Kunlik: shu kunning menyusi (mahsulotlari bilan)
    menus = list(DailyMenu.objects.filter(date=day).select_related('meal')
                 .order_by('meal_time').prefetch_related('outs__product'))
    day_total = sum(m.cost for m in menus)

    # 2) Haftalik va 3) Oylik: hafta ham, oy ham BIR so'rov bilan olinadi, qolganini Python yig'adi
    rows = DailyMenu.objects.filter(date__range=(min(month_start, week_start), max(month_end, week_end))) \
        .values('date', 'meal_time').annotate(t=Sum('cost'))
    week_sums, month_sums, by_time = defaultdict(int), defaultdict(int), defaultdict(int)
    for r in rows:
        d, t = r['date'], r['t'] or 0
        if week_start <= d <= week_end:
            week_sums[d] += t
        if month_start <= d <= month_end:
            month_sums[d] += t
            by_time[r['meal_time']] += t

    week_days = [{'date': week_start + timedelta(days=i), 'total': week_sums.get(week_start + timedelta(days=i), 0),
                  'selected': week_start + timedelta(days=i) == day} for i in range(7)]
    labels = dict(DailyMenu.MEAL_TIME_CHOICES)
    month_days = [{'date': d, 't': t} for d, t in sorted(month_sums.items())]
    month_by_time = [{'meal_time': k, 'label': labels.get(k, k), 't': v} for k, v in sorted(by_time.items())]

    # taomlar bir marta olinadi: ro'yxat ham, menyu formasidagi <select> ham shundan (faqat faollari)
    meals = list(Meal.objects.order_by('-is_active', 'name'))
    menu_form, menu_edit_form = DailyMenuForm(initial={'date': day}), DailyMenuForm()
    share_choices(menu_form, menu_edit_form, meal=choices_of([m for m in meals if m.is_active], lambda m: m.name))

    return render(request, tpl('meal.html'), {
        'day': day, 'prev_day': day - timedelta(days=1), 'next_day': day + timedelta(days=1),
        'menus': menus, 'day_total': day_total,
        'week_start': week_start, 'week_end': week_end, 'week_days': week_days,
        'week_total': sum(week_sums.values()),
        'month_start': month_start, 'month_days': month_days, 'month_total': sum(month_sums.values()),
        'month_by_time': month_by_time,
        'meals': meals,
        'menu_form': menu_form, 'menu_edit_form': menu_edit_form,
        'form': MealForm(), 'edit_form': MealForm(),
    })


@is_login
@require_POST
def meal_create(request):
    return save_form(request, MealForm, back_url(request, reverse('meal_list')), ok="Taom qo'shildi ✅")


@is_login
@require_POST
def meal_update(request, pk):
    return save_form(request, MealForm, back_url(request, reverse('meal_list')),
                     instance=get_object_or_404(Meal, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def meal_toggle(request, pk):
    return toggle_active(request, get_object_or_404(Meal, pk=pk), reverse('meal_list'), 'Taom')


@is_login
@require_POST
def menu_create(request):
    back = back_url(request, with_query('meal_list', date=request.POST.get('date')))
    return save_form(request, DailyMenuForm, back,
                     before_save=lambda o: setattr(o, 'created_by', request.user), ok="Menyuga qo'shildi ✅")


@is_login
@require_POST
def menu_update(request, pk):
    back = back_url(request, with_query('meal_list', date=request.POST.get('date')))
    return save_form(request, DailyMenuForm, back, instance=get_object_or_404(DailyMenu, pk=pk),
                     ok="O'zgartirildi ✅")


@is_login
@require_POST
def menu_delete(request, pk):
    menu = get_object_or_404(DailyMenu, pk=pk)
    back = back_url(request, with_query('meal_list', date=str(menu.date)))
    return safe_delete(request, menu, back, 'Menyu yozuvi')


# ============================================================ OMBOR  (product.html)
@is_login
def product_list(request):
    g = request.GET
    q = g.get('q', '').strip()
    low = bool(g.get('low'))
    d_from, d_to = month_defaults(request, extra_keys=('q', 'low'))

    # o'rtacha narx shu so'rovning o'zida hisoblanadi (oldin har mahsulot uchun alohida so'rov ketardi)
    products = Product.objects.annotate(in_qty=Sum('incomes__quantity'), in_sum=Sum('incomes__total_price')).order_by('name')
    if q:
        products = products.filter(name__icontains=q)
    if low:
        products = products.filter(quantity__lte=F('min_quantity'))
    products = list(products)

    ins = ProductIn.objects.select_related('product', 'created_by').order_by('-date', '-id')
    outs = ProductOut.objects.select_related('product', 'created_by', 'menu__meal').order_by('-date', '-id')
    if d_from:
        ins, outs = ins.filter(date__gte=d_from), outs.filter(date__gte=d_from)
    if d_to:
        ins, outs = ins.filter(date__lte=d_to), outs.filter(date__lte=d_to)

    # ?menu=12 (taomlar sahifasidagi "+ mahsulot" tugmasi) -> chiqim oynasi shu taom bilan ochiladi
    menu = DailyMenu.objects.filter(pk=to_int(g.get('menu'))).first() if g.get('menu') else None
    out_initial = {'date': menu.date if menu else timezone.localdate()}
    if menu:
        out_initial['menu'] = menu.pk

    # kirim/chiqim formalaridagi mahsulot <select>: filtr bo'lmasa tayyor ro'yxatdan, bo'lmasa 1 ta so'rov bilan
    all_products = products if not (q or low) else list(Product.objects.order_by('name'))
    in_form = ProductInForm(initial={'date': timezone.localdate()})
    out_form = ProductOutForm(initial=out_initial)
    share_choices(in_form, out_form, product=choices_of(all_products))

    ins_list, outs_list = list(ins[:100]), list(outs[:100])
    return render(request, tpl('product.html'), {
        'out_total': outs.aggregate(s=Sum('cost'))['s'] or 0, 'open_out': bool(menu),
        'products': products, 'q': q, 'low': low,
        'low_count': notif.get_counts()['low_stock_count'],      # keshdan, SQL yo'q
        'date_from': d_from, 'date_to': d_to,
        'ins': ins_list, 'in_total': ins.aggregate(s=Sum('total_price'))['s'] or 0,
        'outs': outs_list,
        'form': ProductForm(), 'edit_form': ProductForm(),
        'in_form': in_form, 'out_form': out_form,
    })


@is_login
@require_POST
def product_create(request):
    return save_form(request, ProductForm, back_url(request, reverse('product_list')),
                     ok="Mahsulot qo'shildi ✅")


@is_login
@require_POST
def product_update(request, pk):
    return save_form(request, ProductForm, back_url(request, reverse('product_list')),
                     instance=get_object_or_404(Product, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def product_delete(request, pk):
    # DIQQAT: mahsulot o'chsa, uning kirim/chiqim tarixi ham o'chadi
    return safe_delete(request, get_object_or_404(Product, pk=pk), reverse('product_list'), 'Mahsulot')


@is_login
@require_POST
def product_in_create(request):
    return save_form(request, ProductInForm, back_url(request, reverse('product_list')),
                     before_save=lambda o: setattr(o, 'created_by', request.user),
                     ok="Omborga kirim qilindi ✅ (chiqimlarga ham yozildi)")


@admin_required
@require_POST
def product_in_delete(request, pk):
    return safe_delete(request, get_object_or_404(ProductIn, pk=pk), back_url(request, reverse('product_list')),
                       'Kirim')


@is_login
@require_POST
def product_out_create(request):
    return save_form(request, ProductOutForm, back_url(request, reverse('product_list')),
                     before_save=lambda o: setattr(o, 'created_by', request.user),
                     ok="Ombordan chiqim qilindi ✅")


@admin_required
@require_POST
def product_out_delete(request, pk):
    return safe_delete(request, get_object_or_404(ProductOut, pk=pk), back_url(request, reverse('product_list')),
                       'Chiqim')


# ============================================================ BILDIRISHNOMA + BAYRAMLAR  (notification.html)
@is_login
def notification_list(request):
    types = list(NotificationType.objects.order_by('id'))
    if {t.code for t in types} < {c for c, _, _ in notif.DEFAULT_TYPES}:     # tayyor turlar yetishmasa - yaratamiz
        ensure_default_types()
        types = list(NotificationType.objects.order_by('id'))
    pref = get_user_pref(request.user)           # birinchi marta - hammasi yoqilgan
    ctx = {
        'types': types,
        'pref': pref,
        'enabled_ids': set(pref.enabled_types.values_list('id', flat=True)),
        'holidays': stats.holidays_with_next(),   # [{holiday, next_date, days_left}]
        'holiday_form': HolidayForm(initial={'repeat_yearly': True, 'remind_days_before': 3}),
        'holiday_edit_form': HolidayForm(),
        'type_edit_form': NotificationTypeForm(),
    }
    if request.user.is_admin:
        ctx['all_prefs'] = (NotificationPreference.objects.select_related('user')
                            .prefetch_related('enabled_types').order_by('user__username'))
    return render(request, tpl('notification.html'), ctx)


@is_login
@require_POST
def my_preferences_save(request):
    pref = get_user_pref(request.user)
    return save_form(request, NotificationPreferenceForm, reverse('notification_list'), instance=pref,
                     ok="Sozlamalar saqlandi ✅")


@admin_required
@require_POST
def notification_type_update(request, pk):
    return save_form(request, NotificationTypeForm, reverse('notification_list'),
                     instance=get_object_or_404(NotificationType, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def holiday_create(request):
    return save_form(request, HolidayForm, back_url(request, reverse('notification_list')),
                     ok="Bayram qo'shildi ✅")


@admin_required
@require_POST
def holiday_update(request, pk):
    return save_form(request, HolidayForm, back_url(request, reverse('notification_list')),
                     instance=get_object_or_404(Holiday, pk=pk), ok="O'zgartirildi ✅")


@admin_required
@require_POST
def holiday_delete(request, pk):
    return safe_delete(request, get_object_or_404(Holiday, pk=pk), back_url(request, reverse('notification_list')), 'Bayram')


# ============================================================ TARBIYACHI: BOTDAGI TUGMA -> KELDI/KETDI SAHIFASI
@csrf_exempt                      # tarbiyachi login qilmaydi: havoladagi imzolangan token kimligini bildiradi
@never_cache
@require_http_methods(['GET', 'POST'])
def attendance_mark(request, token):
    teacher, action, error = attendance.read_token(token)

    if request.method == 'GET':        # sahifa ochilishi bilan JS joylashuvni o'zi oladi va shu manzilga POST qiladi
        return render(request, tpl('attendance_mark.html'), {'teacher': teacher, 'action': action, 'error': error})

    if error:
        return JsonResponse({'ok': False, 'message': error}, status=400)
    try:
        data = json.loads(request.body or b'{}')
        lat, lon = float(data['lat']), float(data['lon'])
        assert -90 <= lat <= 90 and -180 <= lon <= 180
    except (KeyError, TypeError, ValueError, AssertionError):
        return JsonResponse({'ok': False, 'message': "Joylashuv noto'g'ri yuborildi."}, status=400)

    ok, message, info = attendance.mark(teacher, action, lat, lon)
    if ok:
        attendance.after_mark(teacher, action, info)      # bosilgan tugma xabari botdan o'chadi, 'Keldim' bo'lsa 'Ketdim' tugmasi keladi
    return JsonResponse({'ok': ok, 'message': message})


# ============================================================ ISH KUNLARI (admin va yordamchi belgilaydi)
@is_login
@require_POST
def workdays_save(request):
    """Kunni (yoki sanalar oralig'ini) dam olish kuni qilish / ish kuniga qaytarish. Bot dam olish kunlari yozmaydi."""
    back = back_url(request, reverse('teacher_list') + '#ishkunlari')
    d1 = parse_date(request.POST.get('date_from'))
    d2 = parse_date(request.POST.get('date_to')) or d1
    if not d1 or d2 < d1 or (d2 - d1).days > 90:
        messages.error(request, "Sana noto'g'ri (oraliq 90 kundan oshmasin).")
        return redirect(back)
    reason = request.POST.get('reason', '').strip()[:200]
    days = [d1 + timedelta(days=i) for i in range((d2 - d1).days + 1)]

    if request.POST.get('action') == 'on':
        DayOff.objects.filter(date__in=days).delete()
        messages.success(request, f"{len(days)} kun ish kuniga qaytarildi ✅")
    else:
        existing = set(DayOff.objects.filter(date__in=days).values_list('date', flat=True))
        DayOff.objects.bulk_create([DayOff(date=d, reason=reason, created_by=request.user)
                                    for d in days if d not in existing])
        if reason and existing:
            DayOff.objects.filter(date__in=existing).update(reason=reason)
        messages.success(request, f"{len(days)} kun dam olish kuni deb belgilandi 🏖 — bot tarbiyachilarga xabar yubormaydi")
    notif.bump('attendance')                               # bulk amallar signal yubormaydi - keshni o'zimiz yangilaymiz
    return redirect(back)


@admin_required
@require_POST
def workdays_weekly(request):
    """Haftalik ish kunlari (masalan faqat dushanba-shanba)."""
    days = sorted({int(x) for x in request.POST.getlist('weekdays') if x.isdigit() and 0 <= int(x) <= 6})
    ws = WorkSchedule.objects.first() or WorkSchedule()
    ws.work_weekdays = ','.join(str(x) for x in days)
    ws.save()
    notif.bump('attendance')
    messages.success(request, "Haftalik ish kunlari saqlandi ✅")
    return redirect(back_url(request, reverse('teacher_list') + '#ishkunlari'))


# ============================================================ TARBIYACHIGA "KELDIM/KETDIM" LINKINI QO'LDA YUBORISH
@is_login
@require_POST
def teacher_send_link(request, pk):
    """
    Bot link yubormagan bo'lsa admin yoki yordamchi qo'lda yuboradi (mode=send) yoki havolani
    ekranda ko'rsatadi (mode=show) - uni boshqa yo'l bilan (qo'ng'iroq, boshqa messenjer) jo'natish mumkin.
    """
    t = get_object_or_404(Teacher, pk=pk, is_active=True)
    kind = 'out' if request.POST.get('kind') == 'out' else 'in'
    label = '🏠 Ketdim' if kind == 'out' else '📍 Keldim'
    back = back_url(request, reverse('teacher_list') + '#davomat')
    url = attendance.build_url(t, kind)

    if request.POST.get('mode') == 'show':
        messages.info(request, f"{t.full_name} — «{label}» havolasi (faqat bugun uchun amal qiladi): {url}")
    elif not t.telegram_id:
        messages.error(request, f"{t.full_name} botga ulanmagan (Telegram ID yo'q). Havolani qo'lda yuboring: {url}")
    else:
        text = (f"{t.full_name}, administrator sizga «{label.split(' ', 1)[1]}» tugmasini yubordi 👇" )
        ok, err, _ = bot_services.send_button(t, kind, text)
        if ok:
            messages.success(request, f"{t.full_name}ga «{label}» tugmasi yuborildi ✅")
        else:
            messages.error(request, f"Yuborilmadi: {err}. {bot_services.explain_error(err)} Havola: {url}")
    return redirect(back)


# ============================================================ BOT SINOVI (admin)
@admin_required
def bot_test(request):
    """Bot sinov sahifasi: tarbiyachilarga tugma/matn yuborish, kunlik vazifalarni hozir ishga tushirish, holat tekshiruvi."""
    results, title, run_error = None, '', ''
    check_token = False
    if request.method == 'POST':
        action = request.POST.get('action')
        ids = [i for i in (to_int(x) for x in request.POST.getlist('teachers')) if i]
        preview = bool(request.POST.get('preview'))
        if action in ('send_in', 'send_out', 'send_text') and not ids:
            messages.error(request, "Kamida bitta tarbiyachini belgilang.")
        elif action in ('send_in', 'send_out'):
            kind = 'in' if action == 'send_in' else 'out'
            results = bot_services.send_checkin_buttons(ids, kind, preview)
            title = ("«Keldim»" if kind == 'in' else "«Ketdim»") + (" havolalari" if preview else " tugmasi yuborildi")
        elif action == 'send_text':
            text = request.POST.get('text', '').strip()
            if not text:
                messages.error(request, "Matnni yozing.")
            else:
                results, title = bot_services.send_text_to_teachers(ids, text), "Matn yuborildi"
        elif action == 'run_job':
            name = request.POST.get('job')
            results, run_error = bot_services.run_job_now(name)
            title = f"Vazifa: {name}"
        elif action == 'check_token':
            check_token = True

    today = timezone.localdate()
    att = {a.teacher_id: a for a in TeacherAttendance.objects.filter(date=today)}
    teachers = [{'t': t, 'att': att.get(t.pk)} for t in Teacher.objects.filter(is_active=True).order_by('full_name')]
    return render(request, tpl('bot_test.html'), {
        'status': bot_services.bot_status(check_token), 'teachers': teachers, 'jobs': bot_services.jobs_info(),
        'runs': BotJobRun.objects.filter(date=today), 'results': results, 'results_title': title, 'run_error': run_error,
    })


# ============================================================ ADMIN: BOTGA KIRGANLARNI ULASH
@admin_required
@require_POST
def bot_user_link(request, pk):
    """Botga kirgan odamni tarbiyachi (t:ID) yoki xodim (u:ID) bilan bog'laydi - Telegram ID o'zi yoziladi."""
    bu = get_object_or_404(BotUser, pk=pk)
    back = back_url(request, reverse('teacher_list') + '#tarbiyachilar')
    kind, _, raw_id = request.POST.get('target', '').partition(':')
    model = {'t': Teacher, 'u': User}.get(kind)
    obj = model.objects.filter(pk=to_int(raw_id), is_active=True).first() if model else None
    if not obj:
        messages.error(request, "Kimligini tanlang.")
        return redirect(back)
    if (Teacher.objects.filter(telegram_id=bu.telegram_id).exclude(pk=obj.pk if kind == 't' else 0).exists()
            or User.objects.filter(telegram_id=bu.telegram_id).exclude(pk=obj.pk if kind == 'u' else 0).exists()):
        messages.error(request, "Bu Telegram ID allaqachon boshqaga ulangan.")
        return redirect(back)

    obj.telegram_id = bu.telegram_id
    obj.save(update_fields=['telegram_id'])
    name = obj.full_name if kind == 't' else (obj.get_full_name() or obj.username)
    send_message(bu.telegram_id,
                 f"✅ Siz tizimga ulandingiz: {name}.\n\n"
                 + ("Har ish kuni ertalab «Keldim» tugmasi keladi. Hozir belgilash: /keldim, /ketdim."
                    if kind == 't' else "Bildirishnomalar shu yerga keladi. Hozirgilarini ko'rish: /xabarlar."))
    messages.success(request, f"{bu.display_name} → {name} ulandi ✅")
    return redirect(back)


@admin_required
@require_POST
def bot_user_delete(request, pk):
    """Noto'g'ri odamni ro'yxatdan olib tashlash (botga qayta kirishi uchun parol kerak bo'ladi)."""
    return safe_delete(request, get_object_or_404(BotUser, pk=pk),
                       back_url(request, reverse('teacher_list') + '#tarbiyachilar'), "Botga kirgan")


# ============================================================ TELEGRAM BOT API
def _bot_request(request):
    """Token tekshiradi va JSON'ni o'qiydi. Qaytaradi: (data, xato_javob)."""
    expected = getattr(settings, 'BOT_API_TOKEN', '')
    token = request.headers.get('X-Bot-Token', '')
    if not expected or not hmac.compare_digest(token, expected):
        return None, JsonResponse({'ok': False, 'message': 'Ruxsat yoq'}, status=403)
    try:
        return json.loads(request.body or b'{}'), None
    except ValueError:
        return None, JsonResponse({'ok': False, 'message': "JSON noto'g'ri"}, status=400)


@csrf_exempt
@require_POST
def bot_location(request):
    """POST {"telegram_id": 123, "lat": 41.3, "lon": 69.2}  Header: X-Bot-Token"""
    data, err = _bot_request(request)
    if err:
        return err
    try:
        ok, msg = stats.register_location(int(data['telegram_id']), float(data['lat']), float(data['lon']))
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'ok': False, 'message': "telegram_id, lat, lon kerak"}, status=400)
    return JsonResponse({'ok': ok, 'message': msg})


@csrf_exempt
@require_POST
def bot_link(request):
    """POST {"phone": "+998901234567", "telegram_id": 123} - tarbiyachini botga bog'laydi."""
    data, err = _bot_request(request)
    if err:
        return err
    try:
        ok, msg = stats.link_telegram(data['phone'], int(data['telegram_id']))
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'ok': False, 'message': "phone va telegram_id kerak"}, status=400)
    return JsonResponse({'ok': ok, 'message': msg})
