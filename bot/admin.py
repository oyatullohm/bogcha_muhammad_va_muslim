from django.contrib import admin

from .models import BotJobRun, BotUser

admin.site.register(BotUser)
admin.site.register(BotJobRun)
