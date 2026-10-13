from django.shortcuts import render
from django.contrib.auth import authenticate, login
from django.http import HttpResponseRedirect
from django.views import View

from aiohttp import request

class LoginView(View):
    def get(self, request):
        return render(request, "login.html")
    def post(self, request):
        username = request.POST.get("username")
        password = request.POST.get("password")
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return HttpResponseRedirect("/")
        return render(request, "login.html")

class RegisterView(View):
    def get(self, request):
        if request.user.is_authenticated and request.user.is_admin:
            return HttpResponseRedirect("/")
        return render(request, "register.html")

    def post(self, request):
        username = request.POST.get("username")
        password = request.POST.get("password")
        telegram_id = request.POST.get("telegram_id")
        if request.user.is_authenticated and request.user.is_admin:
            return HttpResponseRedirect("/")
        # Add logic to create a new user and handle registration
        # For example:
        # user = User.objects.create_user(username=username, password=password)
        # login(request, user)
        return HttpResponseRedirect("/")

class LogoutView(View):
    def post(self, request):
        from django.contrib.auth import logout
        logout(request)
        return HttpResponseRedirect("/login/")


def custom_404(request, exception):
    """Maxsus 404 sahifasi"""
    return render(request, '404.html', status=404)


def custom_500(request):
    """Maxsus 500 sahifasi"""
    return render(request, '500.html', status=500)


def custom_403(request, exception):
    """Maxsus 403 sahifasi"""
    return render(request, '403.html', status=403)


def custom_400(request, exception):
    """Maxsus 400 sahifasi"""
    return render(request, '400.html', status=400)