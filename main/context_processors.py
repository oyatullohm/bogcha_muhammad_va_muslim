"""Sidebar sonlari va qo'ng'iroqcha. Hammasi keshlangan bo'laklardan olinadi: iliq keshda 0 ta SQL."""
from .notifications import (build_notifications, clear_nav_cache, get_birthdays,  # noqa: F401
                            get_counts, get_debtors)


def nav_counts(request):
    user = request.user
    if not user.is_authenticated:
        return {}
    counts = get_counts()
    data = {
        'children_count': counts['children_count'],
        'low_stock_count': counts['low_stock_count'],
        'birthdays_today': sum(1 for b in get_birthdays() if b['days_left'] == 0),
        'notifications': build_notifications(user),
    }
    if user.is_admin:
        data['debtors_count'] = get_debtors()['count']
    data['bell_count'] = len(data['notifications'])
    return data
