from bot.telegram_api import send_message
from .models import NotificationPreference, Product
from decimal import Decimal

def format_quantity(value):
    value = Decimal(str(value))
    return format(value.normalize(), 'f')


def notify_product_low(product_id):
    product = Product.objects.get(id=product_id)
    if product.quantity > product.min_quantity:
        return

    preferences = (
        NotificationPreference.objects
        .filter(
            is_active=True,
            user__is_active=True,
            user__telegram_id__isnull=False,
            enabled_types__code='product_low',
            enabled_types__is_active=True,
        )
        .select_related('user')
        .distinct()
    )

    quantity = format_quantity(product.quantity)
    min_quantity = format_quantity(product.min_quantity)

    text = (
        "⚠️ Mahsulot qoldig'i kamaydi!\n\n"
        f"📦 Mahsulot: {product.name}\n"
        f"📊 Hozirgi qoldiq: {quantity} "
        f"{product.get_unit_display()}\n"
        f"🔔 Minimal miqdor: {min_quantity} "
        f"{product.get_unit_display()}"
    )

    for preference in preferences:
        send_message(
            preference.user.telegram_id,
            text
        )