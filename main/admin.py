from django.contrib import admin
from .models import *

admin.site.register(User)
admin.site.register(Teacher)
admin.site.register(Child)
admin.site.register(Group)
admin.site.register(Payment)
admin.site.register(Tariff)
admin.site.register(Expense)
admin.site.register(Product)
admin.site.register(Meal)
admin.site.register(NotificationType)
admin.site.register(NotificationPreference)
