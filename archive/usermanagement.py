# views.py
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, redirect
from django.contrib import messages
from .forms import AdminUserCreationForm
from django.contrib.auth.models import Group
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.forms import PasswordResetForm
from .models import ActiveSession, User
from .models import AuditLog
from .utils import create_audit_log  
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import logout
import secrets
from django.contrib.auth import authenticate, login


def is_system_admin(user):
    return user.is_authenticated and user.groups.filter(name='SystemAdmin').exists() or user.groups.filter(name='ArchiveManager').exists()


@user_passes_test(is_system_admin, login_url='archive:home')
def admin_create_user(request):
    if request.method == 'POST':
        form = AdminUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            
            # Get roles BEFORE any superuser/staff updates
            assigned_roles = list(user.groups.values_list('name', flat=True))
            roles_display = ', '.join(assigned_roles) if assigned_roles else 'No role'
            
            # Update superuser/staff based on SystemAdmin presence
            if user.groups.filter(name='SystemAdmin').exists():
                user.is_superuser = True
                user.is_staff = True
                user.save()
            
            # Capture all user details for audit log
            old_value = None  # No old value for creation
            new_value = {
                'user_id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'department': user.department,
                'security_clearance_level': user.security_clearance_level,
                'roles': assigned_roles,
                'roles_display': roles_display,
                'is_superuser': user.is_superuser,
                'is_staff': user.is_staff,
                'is_active': user.is_active,
                'type': 'user_creation'
            }
            
            # Create comprehensive audit log for user creation
            from archive.models import AuditLog
            
            AuditLog.objects.create(
                user=request.user,
                archive_item=None,
                action=AuditLog.ACTION_CREATE,
                old_value=old_value,
                new_value=new_value,
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            messages.success(
                request, 
                f'User {user.username} created successfully with roles: {roles_display}'
            )
            return redirect('archive:manage_users')  # Redirect to user management list
        else:
            for error in form.errors.values():
                messages.error(request, error)
    else:
        form = AdminUserCreationForm()
    
    return render(request, 'archive/admin_register.html', {'form': form})



@user_passes_test(is_system_admin, login_url='archive:home')
def edit_user(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid method'}, status=405)
    
    user = get_object_or_404(User, id=user_id)
    
    # Capture old values BEFORE any changes
    old_values = {
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'department': user.department,
    }
    
    # Get new values from form
    first_name = request.POST.get('first_name', '').strip()
    last_name = request.POST.get('last_name', '').strip()
    email = request.POST.get('email', '').strip()
    department = request.POST.get('department', '').strip()
    
    if not email:
        return JsonResponse({'error': 'Email is required.'}, status=400)
    
    # Update user
    user.first_name = first_name
    user.last_name = last_name
    user.email = email
    user.department = department
    user.save()
    
    # Capture new values AFTER changes
    new_values = {
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'department': user.department,
    }
    
    # Track what actually changed - separate old and new
    old_changes = {}
    new_changes = {}
    
    for field in ['first_name', 'last_name', 'email', 'department']:
        if old_values[field] != new_values[field]:
            old_changes[field] = old_values[field] or '(empty)'
            new_changes[field] = new_values[field] or '(empty)'
    
    # Create audit log if any changes were made
    if old_changes and new_changes:
        from archive.models import AuditLog
        
        AuditLog.objects.create(
            user=request.user,
            archive_item=None,
            action=AuditLog.ACTION_MODIFY,
            old_value={
                'user_id': user.id,
                'username': user.username,
                'type': 'user_edit',
                'changed_fields': old_changes  # Only old values of changed fields
            },
            new_value={
                'user_id': user.id,
                'username': user.username,
                'type': 'user_edit',
                'changed_fields': new_changes  # Only new values of changed fields
            },
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
    
    return JsonResponse({'success': True, 'changes': new_changes})


@user_passes_test(is_system_admin, login_url='archive:home')
def manage_users(request):
    """List all users and their current roles"""
    users_list = User.objects.all().order_by('username')
    paginator = Paginator(users_list, 10)
    page = request.GET.get('page')
    users = paginator.get_page(page)

    # Add has_active_session flag to each user
    for user in users:
        user.has_active_session = ActiveSession.objects.filter(
            user=user,
            expires_at__gt=timezone.now()
        ).exists()

    groups = Group.objects.all()
    return render(request, 'archive/manage_users.html', {
        'users': users,
        'groups': groups,
    })

@user_passes_test(is_system_admin, login_url='archive:home')
def assign_role(request, user_id):
    user = get_object_or_404(User, id=user_id)
    
    if request.method == 'POST':
        # Get old roles BEFORE any changes
        old_groups = list(user.groups.values_list('name', flat=True))
        old_roles_display = ', '.join(old_groups) if old_groups else 'No role'
        
        # Get new role IDs from form
        group_ids = request.POST.getlist('groups')
        
        # Set new groups
        user.groups.set(group_ids)
        
        # Update superuser/staff based on SystemAdmin presence
        if user.groups.filter(name='SystemAdmin').exists():
            user.is_superuser = True
            user.is_staff = True
        else:
            user.is_superuser = False
            user.is_staff = False
        user.save()
        
        # Get new roles AFTER changes
        new_groups = list(user.groups.values_list('name', flat=True))
        new_roles_display = ', '.join(new_groups) if new_groups else 'No role'
        
        # Check if roles actually changed
        if set(old_groups) != set(new_groups):
            from archive.models import AuditLog
            
            # Create audit log WITH THE USERNAME
            AuditLog.objects.create(
                user=request.user,
                archive_item=None,
                action=AuditLog.ACTION_CHANGE_PERMISSION,
                old_value={
                    'target_username': user.username,  # ADD THIS
                    'user_id': user.id,
                    'roles': old_groups,
                    'roles_display': old_roles_display
                },
                new_value={
                    'target_username': user.username,  # ADD THIS
                    'user_id': user.id,
                    'roles': new_groups,
                    'roles_display': new_roles_display
                },
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            messages.success(request, f'Role for {user.username} changed from "{old_roles_display}" to "{new_roles_display}".')
        else:
            messages.info(request, f'No role changes made for {user.username}.')
    
    return redirect('archive:manage_users')



@user_passes_test(is_system_admin, login_url='archive:home')
def toggle_user_active(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if user == request.user:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect('archive:manage_users')
    user.is_active = not user.is_active
    user.save()
    status = "activated" if user.is_active else "deactivated"
    messages.success(request, f"User {user.username} has been {status}.")
    return redirect('archive:manage_users')

@user_passes_test(is_system_admin, login_url='archive:home')
def reset_user_password(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            user.must_change_password = True
            user.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': True})
            messages.success(request, f"Password for {user.username} has been reset.")
            return redirect('archive:manage_users')
        else:
            errors = {field: error[0] for field, error in form.errors.items()}
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'errors': errors}, status=400)
            for error in form.errors.values():
                messages.error(request, error)
            return redirect('archive:manage_users')
    return redirect('archive:manage_users')


@login_required
def active_sessions(request):
    sessions = ActiveSession.objects.filter(user=request.user, expires_at__gt=timezone.now())
    return render(request, 'archive/active_sessions.html', {'sessions': sessions})


@user_passes_test(is_system_admin, login_url='archive:home')
def force_logout_user(request, user_id):
    """
    Terminate all active sessions for a specific user.
    Admin only – cannot force logout yourself.
    """
    user = get_object_or_404(User, id=user_id)

    if user == request.user:
        messages.error(request, "You cannot force logout your own account.")
        return redirect('archive:manage_users')

    deleted_count, _ = ActiveSession.objects.filter(user=user).delete()

    if deleted_count:
        messages.success(request, f"User '{user.username}' has been logged out from all devices ({deleted_count} session(s) terminated).")
    else:
        messages.info(request, f"User '{user.username}' had no active sessions.")

    return redirect('archive:manage_users')


@login_required
@csrf_exempt
def extend_session(request):
    token = request.session.get('active_session_token')
    if token:
        try:
            active_session = ActiveSession.objects.get(
                user=request.user,
                session_token=token,
                expires_at__gt=timezone.now()
            )
            active_session.last_activity = timezone.now()
            active_session.save(update_fields=['last_activity'])
            return JsonResponse({'status': 'ok'})
        except ActiveSession.DoesNotExist:
            pass
    return JsonResponse({'status': 'error'}, status=400)



@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your password has been changed successfully.')
            return redirect('archive:home')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = PasswordChangeForm(user=request.user)
    return render(request, 'archive/change_password.html', {'form': form})



def force_change_password(request):
    user_id = request.session.get('force_change_user_id')
    if not user_id:
        return redirect('archive:login')

    user = get_object_or_404(User, id=user_id)

    if request.user.is_authenticated:
        from django.contrib.auth import logout
        logout(request)

    if request.method == 'POST':
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            user.must_change_password = False
            user.save()
            del request.session['force_change_user_id']
            login(request, user)
            user.last_login = timezone.now()
            user.save(update_fields=['last_login'])
            session_token = secrets.token_urlsafe(32)
            expires_at = timezone.now() + timezone.timedelta(seconds=request.session.get_expiry_age())
            ActiveSession.objects.create(
                user=user,
                session_token=session_token,
                expires_at=expires_at,
                mfa_verified=False
            )
            request.session['active_session_token'] = session_token
            messages.success(request, 'Password changed successfully. You are now logged in.')
            return redirect('archive:home')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = SetPasswordForm(user)

    return render(request, 'archive/force_change_password.html', {
        'form': form,
        'username': user.username
    })