
from django.shortcuts import redirect

from functools import wraps

def is_login(view_func):
    @wraps(view_func)   # ✅ nomni saqlaydi
    def _view_wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            return view_func(request, *args, **kwargs)
        return redirect('/login')
    return _view_wrapper