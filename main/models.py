from decimal import Decimal
from math import radians, sin, cos, asin, sqrt

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Sum, F  # ✅ TUZATILDI: importlar fayl tepasiga ko'chirildi
from django.utils import timezone

# ✅ TUZATILDI (hamma joyda): DateField uchun default=timezone.now emas, timezone.localdate.
# timezone.now UTC vaqtni beradi, Toshkentda (UTC+5) ertalab 00:00-05:00 oralig'ida
# sana "kechagi kun" bo'lib qolardi.


# ============================================================
# FOYDALANUVCHILAR
# ============================================================
class User(AbstractUser):
    """Admin va Yordamchilar uchun User modeli"""
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('assistant', 'Yordamchi'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='assistant')
    phone = models.CharField(max_length=20, blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True)
    photo = models.ImageField(upload_to='users/', blank=True, null=True)  # pip install Pillow kerak
    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True)

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"

    @property
    def is_admin(self):
        # ✅ TUZATILDI: superuser ham admin hisoblanadi
        return self.role == 'admin' or self.is_superuser


# ============================================================
# BOG'CHA LOKATSIYASI
# ============================================================
class Kindergarten(models.Model):
    """
    ✅ YANGI: bot yuborgan location bog'chaga yaqin ekanini tekshirish uchun.
    Busiz tarbiyachi uydan turib ham "keldim" deb yuborishi mumkin edi.
    """
    name = models.CharField(max_length=150)
    lat = models.FloatField()
    lon = models.FloatField()
    radius_m = models.PositiveIntegerField(default=100, help_text="Metrda")
    work_start = models.TimeField(default="08:00")
    auto_checkout_time = models.TimeField("Ketish vaqti (Ketdim bosilmasa)", default="17:00")

    def __str__(self):
        return self.name

    def distance_m(self, lat, lon):
        lat1, lon1, lat2, lon2 = map(radians, [self.lat, self.lon, lat, lon])
        a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
        return 6371000 * 2 * asin(sqrt(a))

    def is_inside(self, lat, lon):
        return self.distance_m(lat, lon) <= self.radius_m


# ============================================================
# TARBIYACHILAR (User emas - botdan location orqali keladi)
# ============================================================
class Teacher(models.Model):
    full_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, unique=True, help_text="Bot orqali aniqlash uchun")
    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True)
    birth_date = models.DateField()
    salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name


class TeacherAttendance(models.Model):
    """Tarbiyachining keldi-ketdi (location bilan)"""
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(default=timezone.localdate)  # ✅ TUZATILDI

    check_in = models.DateTimeField(null=True, blank=True)
    # ✅ TUZATILDI: avval bitta lat/lon edi - u keldi uchunmi, ketdi uchunmi noma'lum.
    # Endi ikkalasi uchun alohida.
    check_in_lat = models.FloatField(null=True, blank=True)
    check_in_lon = models.FloatField(null=True, blank=True)

    check_out = models.DateTimeField(null=True, blank=True)
    check_out_lat = models.FloatField(null=True, blank=True)
    check_out_lon = models.FloatField(null=True, blank=True)

    is_late = models.BooleanField(default=False)  # ✅ YANGI: kechikish (notification uchun kerak)
    auto_checkout = models.BooleanField("Ketish vaqti avtomatik qo'yilgan", default=False)

    class Meta:
        unique_together = ('teacher', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.teacher.full_name} - {self.date}"

    @property
    def worked_hours(self):
        if self.check_in and self.check_out:
            return round((self.check_out - self.check_in).total_seconds() / 3600, 2)
        return 0


# ============================================================
# GURUHLAR
# ============================================================
class Group(models.Model):
    name = models.CharField(max_length=100, unique=True)
    teacher = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, blank=True, related_name='groups')  # ✅ blank=True (adminda bo'sh qoldirish uchun)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def children_count(self):
        return self.children.filter(is_active=True).count()


# ============================================================
# TARIFLAR
# ============================================================
class Tariff(models.Model):
    """
    Masalan:
    - "1 bola" -> max_children=1
    - "Aka-uka" -> max_children=3 (bitta narx, 3 tagacha bola)
    """
    # ✅ TUZATILDI: avval choices yo'q edi, lekin __str__ da get_period_display() ishlatilgan edi
    # (u shunchaki "1_oy" qaytarardi). Endi choices bor.
    PERIOD_CHOICES = (
        ('1_oy', '1 oy'),
        ('3_oy', '3 oy'),
        ('6_oy', '6 oy'),
        ('12_oy', '1 yil'),
    )

    name = models.CharField(max_length=200)
    period = models.CharField(max_length=20, choices=PERIOD_CHOICES, default='1_oy')
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    max_children = models.PositiveIntegerField(
        default=1,
        help_text="Bu tarifda nechta bola bo'lishi mumkin (aka-uka uchun 2,3...)"
    )
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.get_period_display()}) - {self.price} so'm"


# ============================================================
# BOLALAR
# ============================================================
class Child(models.Model):
    GENDER_CHOICES = (
        ('male', "O'g'il bola"),
        ('female', 'Qiz bola'),
    )

    full_name = models.CharField(max_length=200)
    birth_date = models.DateField()
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    parent_name = models.CharField(max_length=200, null=True, blank=True)
    parent_phone = models.CharField(max_length=20)

    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, blank=True, related_name='children')
    tariff = models.ForeignKey(
        Tariff, on_delete=models.SET_NULL, null=True, blank=True, related_name='children',
        help_text="Aka-uka bo'lsa BO'SH qoldiring - asosiy bolaning tarifi ishlatiladi"
    )

    # Aka-uka bog'lash (bir to'lovga birlashtirish)
    family_group = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='siblings',
        help_text="Aka-uka bo'lsa shu yerga ASOSIY bolani tanlang"
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name

    # ✅ TUZATILDI: avval is_family_group bola obyektini qaytarardi (nomi bool ekanini bildiradi).
    @property
    def is_sibling(self):
        """Bu bola boshqa (asosiy) bolaga aka-uka sifatida bog'langanmi?"""
        return self.family_group_id is not None

    # ✅ YANGI: hisob-kitob hamma vaqt ASOSIY bola orqali yuradi
    @property
    def billing_child(self):
        return self.family_group or self

    @property
    def effective_tariff(self):
        """Aka-ukalarda asosiy bolaning tarifi amal qiladi."""
        return self.billing_child.tariff

    def get_account(self):
        """Hisob (ledger) - aka-ukalar uchun ASOSIY bolaning hisobi."""
        account, _ = ChildAccount.objects.get_or_create(child=self.billing_child)
        return account

    # ✅ YANGI: xatolardan himoya (admin/forma orqali saqlaganda ishlaydi)
    def clean(self):
        if self.family_group_id:
            main = self.family_group
            if self.pk and main.pk == self.pk:
                raise ValidationError("Bola o'ziga o'zi aka-uka bo'la olmaydi.")
            if main.family_group_id:
                raise ValidationError("Tanlangan bola o'zi ham boshqaga bog'langan. Asosiy bolani tanlang.")
            if self.pk and self.siblings.exists():
                raise ValidationError("Bu bolaga boshqalar bog'langan, uni boshqaga bog'lab bo'lmaydi.")
            if main.tariff:
                others = main.siblings.exclude(pk=self.pk).count()
                total = 1 + others + 1  # asosiy + boshqa akalar + shu bola
                if total > main.tariff.max_children:
                    raise ValidationError(
                        f"Tarifda ko'pi bilan {main.tariff.max_children} ta bola bo'lishi mumkin."
                    )
            self.tariff = None  # aka-ukaning o'z tarifi bo'lmaydi


# ============================================================
# HISOB (LEDGER)
# ============================================================
class ChildAccount(models.Model):
    """
    Hisob. Balans saqlanmaydi, har doim hisoblanadi.
    Aka-ukalar uchun faqat ASOSIY bolaning hisobi ishlatiladi.
    """
    child = models.OneToOneField(Child, on_delete=models.CASCADE, related_name='account')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.child.full_name}"

    @property
    def balance(self):
        """Joriy balans: + ortiqcha, - qarz"""
        return self.transactions.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    @property
    def debt(self):
        bal = self.balance
        return abs(bal) if bal < 0 else Decimal('0')

    @property
    def overpaid(self):
        bal = self.balance
        return bal if bal > 0 else Decimal('0')

    @property
    def has_debt(self):
        return self.balance < 0

    # ✅ YANGI: oylik hisoblash. Avval buni qiladigan kod umuman yo'q edi.
    def charge_month(self, period_start, period_end, created_by=None):
        """
        Oylik yozish. Cron bilan har oyning 1-sanasida faqat ASOSIY bolalar uchun chaqiriladi:
            for c in Child.objects.filter(is_active=True, family_group__isnull=True):
                c.get_account().charge_month(start, end)
        Bir davr uchun ikki marta yozilmaydi.
        """
        tariff = self.child.tariff
        if not tariff:
            return None
        if self.transactions.filter(type='charge', period_start=period_start).exists():
            return None
        return AccountTransaction.objects.create(
            account=self, type='charge', amount=-tariff.price,
            period_start=period_start, period_end=period_end,
            comment=f"{tariff.name}", created_by=created_by,
        )


class AccountTransaction(models.Model):
    TYPE_CHOICES = (
        ('charge', 'Oylik hisoblash'),   # manfiy (-)
        ('payment', "To'lov"),           # musbat (+)
        ('discount', 'Chegirma'),        # musbat (+)
        ('penalty', 'Jarima'),           # manfiy (-)
        ('correction', 'Tuzatish'),      # +/-
    )

    account = models.ForeignKey(ChildAccount, on_delete=models.CASCADE, related_name='transactions')
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    payment = models.ForeignKey('Payment', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')

    comment = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        sign = '+' if self.amount >= 0 else ''
        return f"{self.account.child.full_name}: {sign}{self.amount} ({self.get_type_display()})"

    # ✅ YANGI: belgi xatosidan himoya. Kimdir "charge" ni + bilan kiritib qo'ysa, balans buzilardi.
    def save(self, *args, **kwargs):
        if self.type in ('charge', 'penalty') and self.amount > 0:
            self.amount = -self.amount
        elif self.type in ('payment', 'discount') and self.amount < 0:
            self.amount = -self.amount
        super().save(*args, **kwargs)


class Payment(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Kutilmoqda'),
        ('confirmed', 'Tasdiqlangan'),
        ('cancelled', 'Bekor qilingan'),
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])  # ✅ 0 so'm to'lov bo'lmasin
    children = models.ManyToManyField(Child, related_name='payments')
    tariff = models.ForeignKey(Tariff, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='confirmed')
    paid_date = models.DateField(default=timezone.localdate)  # ✅ TUZATILDI
    received_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='received_payments')
    comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"To'lov #{self.id} - {self.amount} so'm"

    # ✅ TUZATILDI: avval atomic emas edi (yarmi yozilib qolishi mumkin), ikki marta
    # chaqirilsa pul ikki marta tushardi, bolalar to'lovga tegishli ekani tekshirilmasdi,
    # aka-ukalar esa har biri alohida hisobga tushardi (asosiy hisob bilan chalkashlik).
    @transaction.atomic
    def distribute(self, distribution: dict = None):
        """
        distribution = {child_id: amount}. Berilmasa butun summa birinchi bolaga yoziladi.
        Aka-ukalarning summasi ASOSIY bolaning hisobiga tushadi.
        """
        if self.status != 'confirmed':
            raise ValueError("Faqat tasdiqlangan to'lovni taqsimlash mumkin.")
        if self.transactions.filter(type='payment').exists():
            raise ValueError("Bu to'lov allaqachon taqsimlangan.")

        children_ids = set(self.children.values_list('id', flat=True))
        if not children_ids:
            raise ValueError("To'lovga bolalar biriktirilmagan.")

        if distribution is None:
            distribution = {sorted(children_ids)[0]: self.amount}

        distribution = {cid: Decimal(str(a)) for cid, a in distribution.items()}
        if not set(distribution) <= children_ids:
            raise ValueError("Taqsimotda to'lovga tegishli bo'lmagan bola bor.")
        total = sum(distribution.values())
        if total != self.amount:
            raise ValueError(f"Taqsimot yig'indisi ({total}) to'lov miqdoriga ({self.amount}) teng emas!")

        for child_id, amount in distribution.items():
            child = Child.objects.get(id=child_id)
            AccountTransaction.objects.create(
                account=child.get_account(),
                type='payment', amount=amount, payment=self,
                comment=f"To'lov #{self.id} ({child.full_name})",
                created_by=self.received_by,
            )

    # ✅ YANGI: bekor qilish. Pul hisobdan o'chmasdan, teskari yozuv bilan qaytariladi.
    @transaction.atomic
    def cancel(self, user=None):
        if self.status == 'cancelled':
            return
        for t in list(self.transactions.filter(type='payment')):
            AccountTransaction.objects.create(
                account=t.account, type='correction', amount=-t.amount, payment=self,
                comment=f"To'lov #{self.id} bekor qilindi", created_by=user,
            )
        self.status = 'cancelled'
        self.save(update_fields=['status'])


# ============================================================
# CHIQIM
# ============================================================
class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)  # Oylik, Kommunal, Oziq-ovqat, Boshqa
    description = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name_plural = "Expense categories"

    def __str__(self):
        return self.name


class Expense(models.Model):
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])  # ✅ 0 emas
    date = models.DateField(default=timezone.localdate)  # ✅ TUZATILDI
    description = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        indexes = [models.Index(fields=['date'])]  # ✅ statistika tezroq ishlaydi

    def __str__(self):
        return f"{self.category.name} - {self.amount} ({self.date})"


# ============================================================
# OSHXONA - MAHSULOTLAR (ombor)
# ============================================================
class Product(models.Model):
    UNIT_CHOICES = (
        ('kg', 'Kilogram'),
        ('g', 'Gram'),
        ('l', 'Litr'),
        ('ml', 'Millilitr'),
        ('dona', 'Dona'),
    )

    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default='kg')
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0, help_text="Ombordagi joriy miqdor")
    min_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0, help_text="Shu miqdorga tushganda bot xabar beradi")

    def __str__(self):
        return f"{self.name} - {self.quantity} {self.unit}"

    @property
    def is_low(self):
        return self.quantity <= self.min_quantity

    @property
    def avg_price(self):
        """
        O'rtacha narx (1 birlik uchun) = jami kirim summasi / jami kirim miqdori.
        Ro'yxatda .annotate(in_qty=Sum('incomes__quantity'), in_sum=Sum('incomes__total_price')) qilingan bo'lsa
        qo'shimcha SQL so'rov ketmaydi.
        """
        if 'in_qty' in self.__dict__:
            q, p = self.in_qty, self.in_sum
        else:
            agg = self.incomes.aggregate(q=Sum('quantity'), p=Sum('total_price'))
            q, p = agg['q'], agg['p']
        return (p / q).quantize(Decimal('0.01')) if q else Decimal('0')


class ProductIn(models.Model):
    """Omborga mahsulot qo'shish"""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='incomes')
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))])
    total_price = models.DecimalField(max_digits=12, decimal_places=0)  # ✅ TUZATILDI: "summma" -> "total_price"
    date = models.DateField(default=timezone.localdate)  # ✅ TUZATILDI
    supplier = models.CharField(max_length=200, blank=True, null=True)
    # ✅ YANGI: sotib olingan mahsulot avtomatik "Oziq-ovqat" chiqimiga yoziladi
    expense = models.OneToOneField(Expense, on_delete=models.SET_NULL, null=True, blank=True, editable=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"+{self.quantity} {self.product.unit} {self.product.name}"

    # ✅ TUZATILDI: eng katta xato - avval Product.quantity hech qachon o'zgarmasdi!
    @transaction.atomic
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            Product.objects.filter(pk=self.product_id).update(quantity=F('quantity') + self.quantity)
            cat, _ = ExpenseCategory.objects.get_or_create(name="Oziq-ovqat")
            exp = Expense.objects.create(
                category=cat, amount=self.total_price, date=self.date,
                description=f"{self.product.name} {self.quantity} {self.product.unit}",
                created_by=self.created_by,
            )
            type(self).objects.filter(pk=self.pk).update(expense=exp)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        Product.objects.filter(pk=self.product_id).update(quantity=F('quantity') - self.quantity)
        if self.expense_id:
            self.expense.delete()
        return super().delete(*args, **kwargs)


class ProductOut(models.Model):
    """Oshxonaga ishlatish uchun mahsulot chiqim qilish. Qaysi taom uchun ekani belgilansa, taom narxi avtomatik hisoblanadi."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='outcomes')
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))])
    date = models.DateField(default=timezone.localdate)
    menu = models.ForeignKey('DailyMenu', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='outs', verbose_name="Qaysi taom uchun")
    # Chiqim paytidagi narx (keyin mahsulot narxi o'zgarsa ham eski hisob o'zgarmaydi)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    cost = models.DecimalField(max_digits=12, decimal_places=0, default=0, editable=False)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"-{self.quantity} {self.product.unit} {self.product.name}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if is_new:
            product = Product.objects.select_for_update().get(pk=self.product_id)
            if product.quantity < self.quantity:
                raise ValidationError(f"Omborda yetarli {product.name} yo'q (bor: {product.quantity} {product.unit}).")
            self.unit_price = product.avg_price
            self.cost = (self.quantity * self.unit_price).quantize(Decimal('1'))
        super().save(*args, **kwargs)
        if is_new:
            Product.objects.filter(pk=self.product_id).update(quantity=F('quantity') - self.quantity)
            from .service import notify_product_low
            notify_product_low(product.id)
        if self.menu_id:
            self.menu.recalc_cost()

    @transaction.atomic
    def delete(self, *args, **kwargs):
        menu = self.menu
        Product.objects.filter(pk=self.product_id).update(quantity=F('quantity') + self.quantity)
        result = super().delete(*args, **kwargs)
        if menu:
            menu.recalc_cost()
        return result


# ============================================================
# TAOMLAR (SODDALASHTIRILGAN: retsept yo'q, faqat kunlik summa)
# ============================================================
class Meal(models.Model):
    """Taom nomi (Osh, Shavla, Manti...)."""
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True, null=True)
    photo = models.ImageField(upload_to='meals/', blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class DailyMenu(models.Model):
    """
    Kunlik menyu + shu taomga ketgan summa.
    Kunlik / haftalik / oylik summa shu `cost` lardan yig'iladi.
    """
    MEAL_TIME_CHOICES = (
        ('breakfast', 'Nonushta'),
        ('lunch', 'Tushlik'),
        ('snack', 'Poldnik'),
        ('dinner', 'Kechki ovqat'),
    )

    date = models.DateField(default=timezone.localdate)
    meal_time = models.CharField(max_length=20, choices=MEAL_TIME_CHOICES, default='lunch')
    meal = models.ForeignKey(Meal, on_delete=models.CASCADE, related_name='daily_menus')
    cost = models.DecimalField(
        max_digits=12, decimal_places=0, default=0, validators=[MinValueValidator(0)],
        editable=False,
        help_text="Avtomatik: shu taomga yozilgan mahsulot chiqimlari yig'indisi (so'm)"
    )
    note = models.CharField(max_length=255, blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('date', 'meal_time', 'meal')
        ordering = ['-date', 'meal_time']
        indexes = [models.Index(fields=['date'])]

    def __str__(self):
        return f"{self.date} - {self.get_meal_time_display()}: {self.meal.name} ({self.cost} so'm)"

    def recalc_cost(self):
        """Taom narxi = shu taomga belgilangan barcha mahsulot chiqimlarining summasi."""
        self.cost = self.outs.aggregate(s=Sum('cost'))['s'] or 0
        self.save(update_fields=['cost'])


# ============================================================
# BILDIRISHNOMALAR
# ============================================================
class NotificationType(models.Model):
    code = models.SlugField(max_length=50, unique=True)  # 'product_low'
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class NotificationPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_preferences')
    enabled_types = models.ManyToManyField(NotificationType, blank=True, related_name='subscribers')
    # ✅ TUZATILDI: telegram_id bu yerda ham, User'da ham bor edi (ikki xil bo'lib qolishi mumkin).
    # Endi faqat User.telegram_id ishlatiladi.
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} sozlamalari"

    @property
    def telegram_id(self):
        return self.user.telegram_id

    def wants(self, code: str) -> bool:
        if not self.is_active:
            return False
        return self.enabled_types.filter(code=code, is_active=True).exists()


# ============================================================
# BAYRAMLAR (Yangi yil, Navro'z, Hayit...)
# ============================================================
class Holiday(models.Model):
    """
    Bayram sanasi. Har yili bir xil kunga tushsa (Yangi yil 1-yanvar, Navro'z 21-mart)
    "repeat_yearly" yoqiladi - yil har safar o'zi almashadi.
    Hayit kabi sanasi har yili o'zgaradigan bayramlar uchun uni o'chirib, har yil yangi sana kiriting.
    """
    name = models.CharField("Bayram nomi", max_length=200)
    date = models.DateField("Sana")
    repeat_yearly = models.BooleanField("Har yili takrorlansin", default=True)
    remind_days_before = models.PositiveSmallIntegerField("Necha kun oldin eslatilsin", default=3)
    note = models.CharField("Izoh", max_length=255, blank=True)
    is_day_off = models.BooleanField("Dam olish kuni (bot tarbiyachilarga yozmaydi)", default=False)
    is_active = models.BooleanField("Faol", default=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.name} ({self.date:%d.%m})"


# ============================================================
# BOLALAR DAVOMATI (guruh sahifasida "keldi" belgilanadi)
# ============================================================
class ChildAttendance(models.Model):
    """Faqat KELGAN kunlar saqlanadi: belgi qo'yilsa yozuv yaratiladi, olinsa o'chadi."""
    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(default=timezone.localdate)
    marked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('child', 'date')
        ordering = ['-date']
        indexes = [models.Index(fields=['date'])]

    def __str__(self):
        return f"{self.child} - {self.date}"


# ============================================================
# ISH KUNLARI: dam olish kunlari (bot shu kunlari tarbiyachilarga xabar yubormaydi)
# ============================================================
class DayOff(models.Model):
    """Aniq sana dam olish kuni (bayram, ta'til, ob-havo...). Admin ham, yordamchi ham belgilay oladi."""
    date = models.DateField(unique=True)
    reason = models.CharField("Sababi", max_length=200, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.date} {self.reason}".strip()


class WorkSchedule(models.Model):
    """Haftalik ish kunlari (yagona yozuv). 0=dushanba ... 6=yakshanba. Bo'sh qolsa dushanba-juma."""
    work_weekdays = models.CharField(max_length=20, default='0,1,2,3,4', blank=True)

    def __str__(self):
        return self.work_weekdays
