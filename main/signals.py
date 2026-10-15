"""Ma'lumot o'zgarganda FAQAT unga tegishli kesh bo'lagini eskirtiradi (qolganlari keshda qoladi)."""
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from .models import (AccountTransaction, Child, DayOff, Expense, Holiday, Kindergarten, NotificationPreference,
                     NotificationType, Payment, Product, ProductIn, ProductOut, Teacher,
                     TeacherAttendance, User, WorkSchedule)
from .notifications import bump

AFFECTS = {
    Child: ('counts', 'birthdays', 'debtors'),
    Teacher: ('birthdays', 'attendance'),
    User: ('birthdays',),
    Product: ('counts',), ProductIn: ('counts',), ProductOut: ('counts',),
    Payment: ('debtors', 'finance'), AccountTransaction: ('debtors',),
    Expense: ('finance',),
    TeacherAttendance: ('attendance',), Kindergarten: ('attendance',),
    Holiday: ('holidays', 'attendance'), DayOff: ('attendance',), WorkSchedule: ('attendance',),
    NotificationType: ('pref',), NotificationPreference: ('pref',),
}


@receiver([post_save, post_delete])
def _invalidate(sender, **kwargs):
    groups = AFFECTS.get(sender)
    if groups:
        bump(*groups)


@receiver(m2m_changed, sender=NotificationPreference.enabled_types.through)
def _invalidate_m2m(sender, **kwargs):
    bump('pref')
