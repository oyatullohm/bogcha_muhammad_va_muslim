from django.urls import path
from . import home_views
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views as v
urlpatterns = [
    # Add your URL patterns here
    path('login/', home_views.LoginView.as_view(), name='login'),
    path('logout/', home_views.LogoutView.as_view(), name='logout'),
    path('register/', home_views.RegisterView.as_view(), name='register'),
    
    path('', v.dashboard, name='dashboard'),
    path('users/', v.user_list, name='user_list'),
    path('users/new/', v.user_create, name='user_create'),
    path('users/<int:pk>/edit/', v.user_update, name='user_update'),
    path('users/<int:pk>/toggle/', v.user_toggle, name='user_toggle'),
 
    # ---- teacher.html (tarbiyachi + davomat + bog'cha lokatsiyasi)
    path('teachers/', v.teacher_list, name='teacher_list'),
    path('teachers/new/', v.teacher_create, name='teacher_create'),
    path('teachers/<int:pk>/edit/', v.teacher_update, name='teacher_update'),
    path('teachers/<int:pk>/toggle/', v.teacher_toggle, name='teacher_toggle'),
    path('attendance/save/', v.attendance_save, name='attendance_save'),
    path('kindergarten/save/', v.kindergarten_save, name='kindergarten_save'),
 
    # ---- group.html
    path('groups/', v.group_list, name='group_list'),
    path('groups/new/', v.group_create, name='group_create'),
    path('groups/<int:pk>/', v.group_detail, name='group_detail'),
    path('groups/<int:pk>/attendance/', v.group_attendance_save, name='group_attendance_save'),
    path('groups/<int:pk>/edit/', v.group_update, name='group_update'),
    path('groups/<int:pk>/delete/', v.group_delete, name='group_delete'),
 
    # ---- tariff.html
    path('tariffs/', v.tariff_list, name='tariff_list'),
    path('tariffs/new/', v.tariff_create, name='tariff_create'),
    path('tariffs/<int:pk>/edit/', v.tariff_update, name='tariff_update'),
    path('tariffs/<int:pk>/toggle/', v.tariff_toggle, name='tariff_toggle'),
 
    # ---- child.html
    path('children/', v.child_list, name='child_list'),
    path('children/new/', v.child_create, name='child_create'),
    path('children/<int:pk>/edit/', v.child_update, name='child_update'),
    path('children/<int:pk>/toggle/', v.child_toggle, name='child_toggle'),
    path('children/<int:pk>/transaction/', v.transaction_create, name='transaction_create'),
 
    # ---- payment.html (to'lovlar + qarzdorlar + oylik hisoblash)
    path('payments/', v.payment_list, name='payment_list'),
    path('payments/new/', v.payment_create, name='payment_create'),
    path('payments/child-search/', v.payment_child_search, name='payment_child_search'),
    path('payments/child/<int:pk>/info/', v.payment_child_info, name='payment_child_info'),
    path('payments/<int:pk>/cancel/', v.payment_cancel, name='payment_cancel'),
    path('payments/charge/', v.charge_month, name='charge_month'),
 
    # ---- expense.html (chiqimlar + kategoriyalar + statistika)
    path('expenses/', v.expense_list, name='expense_list'),
    path('expenses/new/', v.expense_create, name='expense_create'),
    path('expenses/<int:pk>/edit/', v.expense_update, name='expense_update'),
    path('expenses/<int:pk>/delete/', v.expense_delete, name='expense_delete'),
    path('expense-categories/new/', v.category_create, name='category_create'),
    path('expense-categories/<int:pk>/edit/', v.category_update, name='category_update'),
    path('expense-categories/<int:pk>/delete/', v.category_delete, name='category_delete'),
 
    # ---- meal.html (taomlar + kunlik menyu + kunlik/haftalik/oylik summa)
    path('meals/', v.meal_list, name='meal_list'),
    path('meals/new/', v.meal_create, name='meal_create'),
    path('meals/<int:pk>/edit/', v.meal_update, name='meal_update'),
    path('meals/<int:pk>/toggle/', v.meal_toggle, name='meal_toggle'),
    path('menu/new/', v.menu_create, name='menu_create'),
    path('menu/<int:pk>/edit/', v.menu_update, name='menu_update'),
    path('menu/<int:pk>/delete/', v.menu_delete, name='menu_delete'),
 
    # ---- product.html (ombor)
    path('products/', v.product_list, name='product_list'),
    path('products/new/', v.product_create, name='product_create'),
    path('products/<int:pk>/edit/', v.product_update, name='product_update'),
    path('products/<int:pk>/delete/', v.product_delete, name='product_delete'),
    path('products/in/new/', v.product_in_create, name='product_in_create'),
    path('products/in/<int:pk>/delete/', v.product_in_delete, name='product_in_delete'),
    path('products/out/new/', v.product_out_create, name='product_out_create'),
    path('products/out/<int:pk>/delete/', v.product_out_delete, name='product_out_delete'),
 
    # ---- notification.html
    path('notifications/', v.notification_list, name='notification_list'),
    path('notifications/my/', v.my_preferences_save, name='my_preferences_save'),
    path('notifications/types/<int:pk>/edit/', v.notification_type_update, name='notification_type_update'),
    path('holidays/new/', v.holiday_create, name='holiday_create'),
    path('holidays/<int:pk>/edit/', v.holiday_update, name='holiday_update'),
    path('holidays/<int:pk>/delete/', v.holiday_delete, name='holiday_delete'),
 
    # ---- tarbiyachi: botdagi tugma ochadigan sahifa (login talab qilmaydi, token bilan himoyalangan)
    path('m/<str:token>/', v.attendance_mark, name='attendance_mark'),
    # ---- admin: botga kirganlarni ulash
    path('bot/users/<int:pk>/link/', v.bot_user_link, name='bot_user_link'),
    path('bot/users/<int:pk>/delete/', v.bot_user_delete, name='bot_user_delete'),
 
    # ---- Telegram bot API
    path('api/bot/location/', v.bot_location, name='bot_location'),
    path('api/bot/link/', v.bot_link, name='bot_link'),
]
