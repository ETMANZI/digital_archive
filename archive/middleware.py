# middleware.py
from django.shortcuts import redirect
from django.utils import timezone
from .models import ActiveSession
from django.contrib.auth import logout
from django.conf import settings

class ActiveSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            token = request.session.get('active_session_token')
            if not token or not ActiveSession.objects.filter(
                user=request.user,
                session_token=token,
                expires_at__gt=timezone.now()
            ).exists():
                from django.contrib.auth import logout
                logout(request)
                return redirect('archive:login')
        return self.get_response(request)
    


class InactivityTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout = getattr(settings, 'INACTIVITY_TIMEOUT_SECONDS', 1800)

    def __call__(self, request):
        if request.user.is_authenticated:
            token = request.session.get('active_session_token')
            if token:
                try:
                    active_session = ActiveSession.objects.get(
                        user=request.user,
                        session_token=token,
                        expires_at__gt=timezone.now()
                    )
                    last_activity = active_session.last_activity
                    now = timezone.now()
                    idle_seconds = (now - last_activity).total_seconds()
                    if idle_seconds > self.timeout:
                        active_session.delete()
                        logout(request)
                        return redirect('archive:login')
                    else:
                        active_session.last_activity = now
                        active_session.save(update_fields=['last_activity'])
                except ActiveSession.DoesNotExist:
                    logout(request)
                    return redirect('archive:login')
        return self.get_response(request)