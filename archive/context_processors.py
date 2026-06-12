from django.conf import settings

def inactivity_timeout(request):
    return {
        'INACTIVITY_TIMEOUT_SECONDS': getattr(settings, 'INACTIVITY_TIMEOUT_SECONDS', 1800)
    }