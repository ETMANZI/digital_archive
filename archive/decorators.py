from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect
from functools import wraps

def role_required(role_names):
    """
    Decorator to restrict access to users with specific role names.
    Usage: @role_required(['SystemAdmin', 'ArchiveManager'])
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('archive:login')
            
            # Check if user has any of the allowed roles
            user_roles = request.user.groups.values_list('name', flat=True)
            has_role = any(role in user_roles for role in role_names)
            
            # Also allow superusers
            if not (has_role or request.user.is_superuser):
                return redirect('archive:home')
            
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def system_admin_required(view_func):
    """Only SystemAdmin (role id=5) can access."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('archive:login')
        
        user_roles = request.user.groups.values_list('name', flat=True)
        if not (request.user.is_superuser or 'SystemAdmin' in user_roles):
            return redirect('archive:home')
        return view_func(request, *args, **kwargs)
    return wrapper


def archive_manager_required(view_func):
    """Only ArchiveManager (role id=3) can access."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('archive:login')
        
        user_roles = request.user.groups.values_list('name', flat=True)
        if not (request.user.is_superuser or 'ArchiveManager' in user_roles):
            return redirect('archive:home')
        return view_func(request, *args, **kwargs)
    return wrapper


def auditor_required(view_func):
    """Only Auditor (role id=4) can access."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('archive:login')
        
        user_roles = request.user.groups.values_list('name', flat=True)
        if not (request.user.is_superuser or 'Auditor' in user_roles):
            return redirect('archive:home')
        return view_func(request, *args, **kwargs)
    return wrapper


def contributor_required(view_func):
    """Only Contributor (role id=2) or higher can access."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('archive:login')
        
        user_roles = set(request.user.groups.values_list('name', flat=True))
        allowed_roles = {'SystemAdmin', 'ArchiveManager', 'Contributor'}
        
        if not (request.user.is_superuser or bool(user_roles & allowed_roles)):
            return redirect('archive:home')
        return view_func(request, *args, **kwargs)
    return wrapper


def viewer_required(view_func):
    """Any authenticated user (including Viewer role id=1) can access."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('archive:login')
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_or_manager_required(view_func):
    """SystemAdmin (id=5) or ArchiveManager (id=3) can access."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('archive:login')
        
        user_roles = set(request.user.groups.values_list('name', flat=True))
        allowed_roles = {'SystemAdmin', 'ArchiveManager'}
        
        if not (request.user.is_superuser or bool(user_roles & allowed_roles)):
            return redirect('archive:home')
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_or_auditor_required(view_func):
    """SystemAdmin (id=5) or Auditor (id=4) can access."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('archive:login')
        
        user_roles = set(request.user.groups.values_list('name', flat=True))
        allowed_roles = {'SystemAdmin', 'Auditor'}
        
        if not (request.user.is_superuser or bool(user_roles & allowed_roles)):
            return redirect('archive:home')
        return view_func(request, *args, **kwargs)
    return wrapper