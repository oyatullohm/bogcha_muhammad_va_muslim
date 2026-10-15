from datetime import timedelta

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import (
    User, Kindergarten, Teacher, TeacherAttendance, Group, Tariff, Child,
    AccountTransaction, Payment, ExpenseCategory, Expense,
    Product, ProductIn, ProductOut, Meal, DailyMenu,
    NotificationType, NotificationPreference, Holiday,
)


# ------------------------------------------------------------ YORDAMCHILAR
def date_w():
    return forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')


def datetime_w():
    return forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')


def time_w():
    return forms.TimeInput(attrs={'type': 'time'}, format='%H:%M')


class BootstrapMixin:
    """Hamma maydonga Bootstrap klasslarini qo'yadi (template'da {{ form.maydon }} chiroyli chiqadi)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxInput):
                cls = 'form-check-input'
            elif isinstance(w, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                cls = 'form-select'
            else:
                cls = 'form-control'
            w.attrs['class'] = (w.attrs.get('class', '') + ' ' + cls).strip()


class BForm(BootstrapMixin, forms.ModelForm):
    pass


# ------------------------------------------------------------ USER
class UserCreateForm(BootstrapMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'role', 'phone', 'birth_date', 'telegram_id')
        widgets = {'birth_date': date_w()}


class UserUpdateForm(BForm):
    new_password = forms.CharField(
        label="Yangi parol (o'zgartirmasangiz bo'sh qoldiring)",
        required=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'role', 'phone', 'birth_date',
                  'photo', 'telegram_id', 'is_active')
        widgets = {'birth_date': date_w()}

    def clean_new_password(self):
        pw = self.cleaned_data.get('new_password')
        if pw:
            password_validation.validate_password(pw, self.instance)
        return pw

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get('new_password'):
            user.set_password(self.cleaned_data['new_password'])
        if commit:
            user.save()
        return user


# ------------------------------------------------------------ TARBIYACHI / DAVOMAT / BOG'CHA
class TeacherForm(BForm):
    class Meta:
        model = Teacher
        fields = ('full_name', 'phone', 'telegram_id', 'birth_date', 'salary', 'is_active')
        widgets = {'birth_date': date_w()}


class AttendanceForm(BForm):
    class Meta:
        model = TeacherAttendance
        fields = ('teacher', 'date', 'check_in', 'check_out', 'is_late')
        widgets = {'date': date_w(), 'check_in': datetime_w(), 'check_out': datetime_w()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for n in ('check_in', 'check_out'):
            self.fields[n].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M']

    def clean(self):
        data = super().clean()
        a, b = data.get('check_in'), data.get('check_out')
        if a and b and b < a:
            raise forms.ValidationError("Ketish vaqti kelish vaqtidan oldin bo'lishi mumkin emas.")
        return data


class KindergartenForm(BForm):
    class Meta:
        model = Kindergarten
        fields = ('name', 'lat', 'lon', 'radius_m', 'work_start', 'auto_checkout_time')
        widgets = {'work_start': time_w(), 'auto_checkout_time': time_w()}


# ------------------------------------------------------------ GURUH / TARIF / BOLA
class GroupForm(BForm):
    class Meta:
        model = Group
        fields = ('name', 'teacher')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['teacher'].queryset = Teacher.objects.filter(is_active=True)


class TariffForm(BForm):
    class Meta:
        model = Tariff
        fields = ('name', 'period', 'price', 'max_children', 'description', 'is_active')
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}


class ChildForm(BForm):
    class Meta:
        model = Child
        fields = ('full_name', 'birth_date', 'gender', 'parent_name', 'parent_phone',
                  'group', 'tariff', 'family_group', 'is_active')
        widgets = {'birth_date': date_w()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # tarif: faqat faol tariflar (bolaning hozirgi tarifi nofaol bo'lsa ham ko'rinadi)
        from django.db.models import Q
        self.fields['tariff'].queryset = Tariff.objects.filter(Q(is_active=True) | Q(pk=self.instance.tariff_id))
        # aka-uka: faqat ASOSIY bolalar tanlanadi, o'zi o'zini tanlay olmaydi
        mains = Child.objects.filter(family_group__isnull=True).only('full_name')
        if self.instance.pk:
            mains = mains.exclude(pk=self.instance.pk)
        self.fields['family_group'].queryset = mains
        self.fields['family_group'].label = "Aka-uka bo'lsa - asosiy bola"
        self.fields['tariff'].required = False


class TransactionForm(BForm):
    """Qo'lda chegirma / jarima / tuzatish (oylik va to'lov alohida yo'l bilan yoziladi)."""

    class Meta:
        model = AccountTransaction
        fields = ('type', 'amount', 'comment')
        widgets = {'comment': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = ('discount', 'penalty', 'correction')
        self.fields['type'].choices = [c for c in AccountTransaction.TYPE_CHOICES if c[0] in allowed]


# ------------------------------------------------------------ TO'LOV
class PaymentForm(BForm):
    """Bitta bola tanlanadi (aka-uka bo'lsa to'lov butun oilaga yoziladi). Bola qidiruv orqali tanlanadi."""
    child = forms.ModelChoiceField(
        queryset=Child.objects.filter(is_active=True), widget=forms.HiddenInput,
        label="Bola", error_messages={'required': "Avval bolani tanlang."},
    )

    class Meta:
        model = Payment
        fields = ('amount', 'paid_date', 'comment')
        widgets = {'paid_date': date_w(), 'comment': forms.Textarea(attrs={'rows': 2})}


class ChargeMonthForm(BootstrapMixin, forms.Form):
    month = forms.DateField(label="Qaysi oy uchun (oyning istalgan kuni)", widget=date_w(),
                            initial=timezone.localdate)


# ------------------------------------------------------------ CHIQIM
class ExpenseCategoryForm(BForm):
    class Meta:
        model = ExpenseCategory
        fields = ('name', 'description')
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}


class ExpenseForm(BForm):
    class Meta:
        model = Expense
        fields = ('category', 'amount', 'date', 'description')
        widgets = {'date': date_w(), 'description': forms.Textarea(attrs={'rows': 2})}


# ------------------------------------------------------------ OMBOR
class ProductForm(BForm):
    class Meta:
        model = Product
        fields = ('name', 'unit', 'min_quantity')   # quantity ni faqat kirim/chiqim o'zgartiradi


class ProductInForm(BForm):
    class Meta:
        model = ProductIn
        fields = ('product', 'quantity', 'total_price', 'date', 'supplier')
        widgets = {'date': date_w()}


class MenuSelect(forms.Select):
    """Menyu variantlariga sanani yozib qo'yadi (data-date) - sahifadagi JS sana bo'yicha filtrlaydi."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opt = super().create_option(name, value, label, selected, index, subindex, attrs)
        inst = getattr(value, 'instance', None)
        if inst is not None:
            opt['attrs']['data-date'] = inst.date.isoformat()
        return opt


class ProductOutForm(BForm):
    class Meta:
        model = ProductOut
        fields = ('product', 'quantity', 'date', 'menu')
        widgets = {'date': date_w(), 'menu': MenuSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        menus = (DailyMenu.objects.select_related('meal')
                 .filter(date__gte=today - timedelta(days=30), date__lte=today + timedelta(days=1))
                 .order_by('-date', 'meal_time'))
        self.fields['menu'].queryset = menus
        self.fields['menu'].label_from_instance = (
            lambda m: f"{m.date:%d.%m.%Y} · {m.get_meal_time_display()} · {m.meal.name}")
        self.fields['menu'].empty_label = "— taom belgilanmagan —"
        self.fields['menu'].help_text = ("Qaysi taom uchun ishlatilsa shuni tanlang: narxi avtomatik hisoblanadi. "
                                         "Taom avval \"Taomlar va menyu\" da shu kunga qo'shilgan bo'lishi kerak.")

    def clean(self):
        data = super().clean()
        product, qty, menu = data.get('product'), data.get('quantity'), data.get('menu')
        if menu:
            data['date'] = menu.date          # sana taom menyusining sanasi bilan bir xil bo'ladi
        if product and qty and product.quantity < qty:
            raise forms.ValidationError(
                f"Omborda yetarli {product.name} yo'q (bor: {product.quantity} {product.unit})."
            )
        return data


# ------------------------------------------------------------ TAOM
class MealForm(BForm):
    class Meta:
        model = Meal
        fields = ('name', 'description', 'photo', 'is_active')
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}


class DailyMenuForm(BForm):
    class Meta:
        model = DailyMenu
        fields = ('date', 'meal_time', 'meal', 'note')
        widgets = {'date': date_w()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['meal'].queryset = Meal.objects.filter(is_active=True).order_by('name')


# ------------------------------------------------------------ BILDIRISHNOMA
class NotificationTypeForm(BForm):
    """Tur kodi (code) o'zgarmaydi - dastur shu kod bilan taniydi. Faqat nom/tavsif/faollik tahrirlanadi."""

    class Meta:
        model = NotificationType
        fields = ('name', 'description', 'is_active')


class HolidayForm(BForm):
    class Meta:
        model = Holiday
        fields = ('name', 'date', 'repeat_yearly', 'remind_days_before', 'is_day_off', 'note', 'is_active')
        widgets = {'date': date_w()}


class NotificationPreferenceForm(BForm):
    class Meta:
        model = NotificationPreference
        fields = ('enabled_types', 'is_active')
        widgets = {'enabled_types': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['enabled_types'].queryset = NotificationType.objects.filter(is_active=True)
