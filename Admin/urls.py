
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

handler404 = 'main.home_views.custom_404'
handler500 = 'main.home_views.custom_500'
handler403 = 'main.home_views.custom_403'
handler400 = 'main.home_views.custom_400'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('main.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

