from django.db import models


class BotUser(models.Model):
    """
    Botga kirgan odam. Parolni to'g'ri kiritsa `verified=True` bo'ladi va admin uni saytda
    tarbiyachi yoki xodimga bog'laydi (Telegram ID ni qo'lda ko'chirish shart emas).
    """
    telegram_id = models.BigIntegerField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    username = models.CharField(max_length=100, blank=True)
    verified = models.BooleanField(default=False)
    fails = models.PositiveSmallIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-verified_at', '-created_at']

    @property
    def display_name(self):
        return (f"{self.first_name} {self.last_name}".strip() or self.username or str(self.telegram_id))

    def __str__(self):
        return f"{self.display_name} ({self.telegram_id})"


class BotJobRun(models.Model):
    """Kunlik vazifa shu kuni bajarilganmi (bot qayta ishga tushsa ham ikki marta yuborilmasin)."""
    job = models.CharField(max_length=50)
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('job', 'date')

    def __str__(self):
        return f"{self.job} {self.date}"


class BotButtonMessage(models.Model):
    """
    Tarbiyachiga yuborilgan «Keldim» / «Ketdim» tugmali xabar. Tugma bosilib davomat yozilgach,
    shu xabarlar chatdan o'chiriladi (qayta bosilmasin va chat toza qolsin).
    """
    teacher_id = models.PositiveIntegerField(db_index=True)
    action = models.CharField(max_length=3)                   # 'in' | 'out'
    date = models.DateField()
    chat_id = models.BigIntegerField()
    message_id = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.teacher_id} {self.action} #{self.message_id}"
