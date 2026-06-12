# archive/itemfolder.py
import uuid
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.core.files.storage import default_storage
from django.db.models import Max  # <-- IMPORTANT: add this
from .models import AccessRequest, ArchiveItem, Collection, DocumentFile, DocumentFolder, FolderType, ItemFolder, ItemFile, WorkflowStatus
from django.shortcuts import render, redirect, get_object_or_404
import json
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from django.contrib import messages
from django.views.decorators.http import require_POST
from archive.decorators import *



@login_required
@require_http_methods(['GET', 'POST'])
def item_folders_api(request, item_id):
    item = get_object_or_404(ArchiveItem, id=item_id)
    if request.method == 'GET':
        # Get IDs of folders and files that have pending deletion requests
        pending_folder_ids = set(
            DeletionRequest.objects.filter(
                status='pending',
                item_folder__isnull=False
            ).values_list('item_folder_id', flat=True)
        )
        pending_file_ids = set(
            DeletionRequest.objects.filter(
                status='pending',
                item_file__isnull=False
            ).values_list('item_file_id', flat=True)
        )

        # Fetch non‑soft‑deleted folders
        folders = ItemFolder.objects.filter(
            archive_item=item,
            is_deleted=False
        ).select_related('folder_type').order_by('folder_type__order')

        data = []
        for f in folders:
            folder_pending = f.id in pending_folder_ids

            # Fetch non‑soft‑deleted files in this folder
            files_qs = f.files.filter(is_deleted=False)
            files_data = []
            for file in files_qs:
                file_pending = file.id in pending_file_ids
                files_data.append({
                    'asset_uuid': str(file.asset_uuid),
                    'file_name': file.file_name,
                    'file_size': file.file_size,
                    'mime_type': file.mime_type,
                    'has_pending_deletion': file_pending,
                })
            data.append({
                'id': f.id,
                'name': f.folder_type.name,
                'order': f.folder_type.order,
                'folder_type_id': f.folder_type_id,
                'files': files_data,
                'has_pending_deletion': folder_pending,
            })
        return JsonResponse({'folders': data})

    # POST – create a new folder (unchanged)
    folder_type_id = request.POST.get('folder_type_id')
    if not folder_type_id:
        return JsonResponse({'error': 'Folder type ID required'}, status=400)
    
    try:
        folder_type = FolderType.objects.get(id=folder_type_id)
    except FolderType.DoesNotExist:
        return JsonResponse({'error': 'Invalid folder type'}, status=400)
    
    if ItemFolder.objects.filter(archive_item=item, user=request.user, folder_type=folder_type).exists():
        return JsonResponse({'error': 'This folder type already exists for this loan'}, status=400)
    
    folder = ItemFolder.objects.create(
        archive_item=item,
        user=request.user,
        folder_type=folder_type,
    )
    return JsonResponse({
        'id': folder.id,
        'name': folder_type.name,
        'order': folder_type.order,
        'folder_type_id': folder_type.id,
    })





# @login_required
# @admin_or_manager_required
# @require_http_methods(['POST'])
# def add_file_to_folder(request, folder_id):
#     if request.user.is_system_admin() or request.user.is_archive_manager():
#         folder = get_object_or_404(ItemFolder, id=folder_id)
#     else:
#         folder = get_object_or_404(ItemFolder, id=folder_id, user=request.user)
    
#     uploaded_file = request.FILES.get('file')
#     if not uploaded_file:
#         return JsonResponse({'error': 'No file provided'}, status=400)
    
#     asset_uuid = uuid.uuid4()
#     path = f'item_folders/{asset_uuid}/{uploaded_file.name}'
#     saved_path = default_storage.save(path, uploaded_file)
    
#     ItemFile.objects.create(
#         folder=folder,
#         asset_uuid=asset_uuid,
#         file_name=uploaded_file.name,
#         file_size=uploaded_file.size,
#         mime_type=uploaded_file.content_type,
#         stored_path=saved_path,
#     )
#     return JsonResponse({
#         'asset_uuid': str(asset_uuid),
#         'file_name': uploaded_file.name,
#         'file_size': uploaded_file.size,
#         'mime_type': uploaded_file.content_type
#     })


def get_next_version(current_version, existing_versions):
    """
    Given the current version (e.g., 'v1.0') and a list of existing versions (strings),
    return the next version (e.g., 'v1.1').
    If the current version is older than some existing, still increment from the max.
    """
    import re
    max_version_num = 0
    pattern = re.compile(r'v\d+\.(\d+)')
    
    # Check all existing versions including the current one (if we decide to override)
    for v in existing_versions:
        match = pattern.search(v)
        if match:
            minor = int(match.group(1))
            if minor > max_version_num:
                max_version_num = minor
    
    # If current version's minor is greater than any existing, use current+1? But we want increment.
    # Actually we should increment from the highest found.
    next_minor = max_version_num + 1
    # Keep the major part as 'v1' (we could extract from current_version)
    major_match = re.search(r'(v\d+\.)', current_version)
    major = major_match.group(1) if major_match else 'v1.'
    return f"{major}{next_minor}"




from .validators import validate_file_naming_convention 
from django.core.exceptions import ValidationError



@login_required
@admin_or_manager_required
@require_http_methods(['POST'])
def add_file_to_folder(request, folder_id):
#     # Permission check
    if request.user.is_system_admin() or request.user.is_archive_manager():
        folder = get_object_or_404(ItemFolder, id=folder_id)
    else:
        folder = get_object_or_404(ItemFolder, id=folder_id, user=request.user)

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'error': 'No file provided'}, status=400)

    # Validate and parse file name
    try:
        parsed = validate_file_naming_convention(uploaded_file.name)
    except ValidationError as e:
        error_msg = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
        return JsonResponse({'error': error_msg}, status=400)

    obligor = parsed['obligor_name']
    doc_type = parsed['document_type']
    date_str = parsed['date']
    current_version = parsed['version']
    ext = parsed['extension']

    # Find all files in the same folder with the same obligor, doc_type, date
    # We need to match by the stored file name pattern. Since file_name is stored as original name,
    # we can fetch all files in the folder and parse their names.
    existing_files = ItemFile.objects.filter(folder=folder, is_deleted=False)
    existing_versions = []
    for ef in existing_files:
        try:
            ef_parsed = validate_file_naming_convention(ef.file_name)
            if (ef_parsed['obligor_name'] == obligor and
                ef_parsed['document_type'] == doc_type and
                ef_parsed['date'] == date_str):
                existing_versions.append(ef_parsed['version'])
        except ValidationError:
            continue  # skip files that don't match naming convention

    # Compute new version
    if existing_versions:
        # Extract minor numbers
        import re
        minors = []
        for v in existing_versions:
            m = re.search(r'v\d+\.(\d+)', v)
            if m:
                minors.append(int(m.group(1)))
        max_minor = max(minors) if minors else 0
        # Increment minor from the maximum
        new_minor = max_minor + 1
        # Keep major part (e.g., v1.) from current_version
        major_match = re.search(r'(v\d+\.)', current_version)
        major = major_match.group(1) if major_match else 'v1.'
        new_version = f"{major}{new_minor}"
    else:
        new_version = current_version

    # Build the new file name
    new_file_name = f"{obligor}_{doc_type}_{date_str}_{new_version}{ext}"
    # If you want to preserve the original name in the file system, you can keep as is,
    # but you might want to store the new name in the database.
    # We'll rename the file object before saving.
    uploaded_file.name = new_file_name

    # Now save the file with the new name
    asset_uuid = uuid.uuid4()
    path = f'item_folders/{asset_uuid}/{new_file_name}'
    saved_path = default_storage.save(path, uploaded_file)

    item_file = ItemFile.objects.create(
        folder=folder,
        asset_uuid=asset_uuid,
        file_name=new_file_name,  # store the versioned name
        file_size=uploaded_file.size,
        mime_type=uploaded_file.content_type,
        stored_path=saved_path,
    )

    return JsonResponse({
        'asset_uuid': str(asset_uuid),
        'file_name': new_file_name,
        'file_size': uploaded_file.size,
        'mime_type': uploaded_file.content_type
    })




@login_required
@admin_or_manager_required
@require_http_methods(['DELETE'])
def delete_item_folder(request, folder_id):
    if request.user.is_system_admin() or request.user.is_archive_manager():
        folder = get_object_or_404(ItemFolder, id=folder_id)
    else:
        folder = get_object_or_404(ItemFolder, id=folder_id, user=request.user)
    
    # Soft delete folder and all its files
    folder.is_deleted = True
    folder.deleted_at = timezone.now()
    folder.save()
    for file in folder.files.all():
        file.is_deleted = True
        file.deleted_at = timezone.now()
        file.save()
    
    return JsonResponse({'status': 'soft_deleted'})





from django.utils import timezone

@login_required
@admin_or_manager_required
@require_http_methods(['DELETE'])
def delete_item_file(request, asset_uuid):
    # Allow admins and managers to delete any file; others only their own
    if request.user.is_system_admin() or request.user.is_archive_manager():
        file_obj = get_object_or_404(ItemFile, asset_uuid=asset_uuid)
    else:
        file_obj = get_object_or_404(ItemFile, asset_uuid=asset_uuid, folder__user=request.user)
    
    # Soft delete – do NOT delete the physical file
    file_obj.is_deleted = True
    file_obj.deleted_at = timezone.now()
    file_obj.save()
    

    
    return JsonResponse({'status': 'soft_deleted'})



from django.db import IntegrityError

@admin_or_manager_required
def folder_type_manage(request):
    if request.method == 'GET':
        folder_types = FolderType.objects.all().order_by('parent__id', 'order')
        return render(request, 'archive/folder_type_list.html', {'folder_types': folder_types})

    elif request.method == 'POST':
        data = json.loads(request.body)
        action = data.get('action')
        try:
            if action == 'create':
                name = data.get('name')
                parent_id = data.get('parent_id') or None
                description = data.get('description', '')
                
                # If order not provided, auto‑calculate for subfolders
                order = data.get('order')
                if order is None and parent_id is not None:
                    from django.db.models import Max
                    max_order = FolderType.objects.filter(parent_id=parent_id).aggregate(Max('order'))['order__max']
                    order = (max_order or 0) + 1
                elif order is None:
                    return JsonResponse({'status': 'error', 'message': 'Order is required for top‑level folder types'}, status=400)
                else:
                    order = int(order)
                
                ft = FolderType.objects.create(
                    name=name,
                    order=order,
                    parent_id=parent_id,
                    description=description
                )
                return JsonResponse({'status': 'ok', 'id': ft.id})

            elif action == 'edit':
                ft_id = data.get('id')
                name = data.get('name')
                order = data.get('order')
                parent_id = data.get('parent_id') or None
                description = data.get('description', '')
                ft = FolderType.objects.get(id=ft_id)
                ft.name = name
                ft.order = int(order)
                ft.parent_id = parent_id
                ft.description = description
                ft.save()
                return JsonResponse({'status': 'ok'})

            elif action == 'delete':
                ft_id = data.get('id')
                ft = FolderType.objects.get(id=ft_id)
                ft.delete()
                return JsonResponse({'status': 'ok'})

        except IntegrityError as e:
            # Provide user-friendly message
            if 'duplicate key' in str(e).lower():
                msg = "A folder type with this order (under the same parent) already exists. Please choose a different order number."
            else:
                msg = f"Database error: {str(e)}"
            return JsonResponse({'status': 'error', 'message': msg}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

        return JsonResponse({'status': 'error', 'message': 'Invalid action'}, status=400)





from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from .models import SharedFolder, SharedFolderFile, SharedFolderAccess, ItemFile, User
from .forms import AccessRequestForm, SharedFolderForm  # we'll create a simple form

@admin_or_manager_required
def shared_folders_manage(request):
    """List, create, edit shared folders (admin only)."""
    folders = SharedFolder.objects.filter(is_deleted=False).order_by('-created_at')
    if request.method == 'POST':
        form = SharedFolderForm(request.POST)
        if form.is_valid():
            folder = form.save(commit=False)
            folder.created_by = request.user
            folder.save()
            return redirect('archive:shared_folder_edit', pk=folder.pk)
    else:
        form = SharedFolderForm()
    return render(request, 'archive/shared_folders_manage.html', {
        'folders': folders,
        'form': form,
    })


@admin_or_manager_required
def shared_folder_edit(request, pk):
    folder = get_object_or_404(SharedFolder, pk=pk)
    all_users = User.objects.filter(is_active=True).order_by('username') 
    allowed_user_ids = list(folder.allowed_users.values_list('user_id', flat=True))
    return render(request, 'archive/shared_folder_edit.html', {
        'folder': folder,
        'all_users':all_users,
        'allowed_user_ids':allowed_user_ids,
    })


@admin_or_manager_required
@require_http_methods(['POST'])
def add_files_to_shared_folder(request, pk):
    folder = get_object_or_404(SharedFolder, pk=pk)
    file_ids = request.POST.getlist('file_ids')
    for fid in file_ids:
        item_file = get_object_or_404(ItemFile, pk=fid)
        SharedFolderFile.objects.get_or_create(shared_folder=folder, item_file=item_file)
    return JsonResponse({'status': 'ok'})





@login_required
@admin_or_manager_required
@require_http_methods(['DELETE'])
def remove_file_from_shared_folder(request, pk, file_pk):
    folder = get_object_or_404(SharedFolder, pk=pk)
    
    if not (folder.created_by == request.user or request.user.is_superuser or request.user.is_archive_manager()):
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    file_type = request.GET.get('file_type')
    if not file_type:
        return JsonResponse({'error': 'Missing file_type parameter'}, status=400)
    
    if file_type == 'loan':
        try:
            loan_file = ItemFile.objects.get(pk=file_pk)
            folder.loan_files.remove(loan_file)   # removes relationship only
        except ItemFile.DoesNotExist:
            return JsonResponse({'error': 'Loan file not found'}, status=404)
    elif file_type == 'document':
        try:
            doc_file = DocumentFile.objects.get(pk=file_pk)
            folder.doc_files.remove(doc_file)     # removes relationship only
        except DocumentFile.DoesNotExist:
            return JsonResponse({'error': 'Document file not found'}, status=404)
    else:
        return JsonResponse({'error': 'Invalid file_type'}, status=400)
    
    return JsonResponse({'status': 'ok'})



@admin_or_manager_required
@require_http_methods(['POST'])
def manage_folder_access(request, pk):
    folder = get_object_or_404(SharedFolder, pk=pk)
    user_ids = request.POST.getlist('user_ids')
    # Clear existing and add new
    folder.allowed_users.clear()
    for uid in user_ids:
        user = get_object_or_404(User, pk=uid)
        SharedFolderAccess.objects.create(shared_folder=folder, user=user, granted_by=request.user)
    return JsonResponse({'status': 'ok'})




from django.db.models import Q, Count

@login_required
def my_shared_folders(request):
 
    folders = SharedFolder.objects.annotate(
        total_loan=Count('loan_files'),
        total_doc=Count('doc_files'),
    ).filter(
        Q(allowed_users__user=request.user) &
        (Q(total_loan__gt=0) | Q(total_doc__gt=0))
    ).distinct().order_by('-created_at')
    
    for folder in folders:
        folder.total_files = folder.total_loan + folder.total_doc
    
    return render(request, 'archive/my_shared_folders.html', {'folders': folders})





@login_required
def view_shared_folder(request, pk):
    folder = get_object_or_404(SharedFolder, pk=pk, is_active=True)
    # Permission: creator OR explicitly granted user
    if not (folder.created_by == request.user or folder.allowed_users.filter(user=request.user).exists()):
        raise PermissionDenied("You do not have access to this folder.")
    
    # Get loan files and document files
    loan_files = folder.loan_files.all().select_related('folder__archive_item', 'folder__folder_type')
    doc_files = folder.doc_files.all().select_related('folder__archive_item')
    
    # Combine them into a single list for the template
    all_files = []
    for f in loan_files:
        all_files.append({
            'type': 'loan',
            'file': f,
            'file_name': f.file_name,
            'file_size': f.file_size,
            'mime_type': f.mime_type,
            'asset_uuid': f.asset_uuid,
            'uploaded_at': f.uploaded_at,
            'customer_name': f.folder.archive_item.client_name,
            'customer_id': f.folder.archive_item.customer_id,
            'loan_id': f.folder.archive_item.loan_id,
            'folder_name': f.folder.folder_type.name if f.folder.folder_type else '',
        })
    for f in doc_files:
        all_files.append({
            'type': 'document',
            'file': f,
            'file_name': f.file_name,
            'file_size': f.file_size,
            'mime_type': f.mime_type,
            'asset_uuid': f.asset_uuid,
            'uploaded_at': f.uploaded_at,
            'title': f.folder.archive_item.title,
            'folder_name': f.folder.name,
        })
    
    return render(request, 'archive/view_shared_folder.html', {
        'folder': folder,
        'files': all_files,
    })




from django.http import JsonResponse
from django.db.models import Q
from .models import ItemFile, SharedFolder, SharedFolderAccess



@login_required
@admin_or_manager_required
def search_files_api1(request):
    q = request.GET.get('q', '')
    files = ItemFile.objects.select_related(
        'folder__archive_item', 
        'folder__folder_type'
    ).filter(
        Q(file_name__icontains=q) |
        Q(folder__archive_item__client_name__icontains=q) |
        Q(folder__archive_item__customer_id__icontains=q) |
        Q(folder__folder_type__name__icontains=q)
    )[:50]
    
    data = []
    for f in files:
        item = f.folder.archive_item
        data.append({
            'id': f.id,
            'file_name': f.file_name,
            'customer_name': item.client_name,
            'customer_id': item.customer_id,
            'loan_id': item.loan_id,
            'loan_title': item.title,
            'date': item.date_of_disbursement.strftime('%Y-%m-%d') if item.date_of_disbursement else item.created_at.strftime('%Y-%m-%d'),
            'folder_name': f.folder.folder_type.name,
        })
    return JsonResponse({'files': data})



def search_files_api(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'files': []})

    loan_files = ItemFile.objects.filter(
        Q(file_name__icontains=q) |
        Q(folder__archive_item__client_name__icontains=q) |
        Q(folder__archive_item__loan_id__icontains=q) |
        Q(folder__folder_type__name__icontains=q)
    ).select_related('folder__archive_item', 'folder__folder_type')[:50]

    doc_files = DocumentFile.objects.filter(
        Q(file_name__icontains=q) |
        Q(folder__archive_item__title__icontains=q)
    ).select_related('folder__archive_item')[:50]

    # Build combined results
    results = []
    for f in loan_files:
        item = f.folder.archive_item
        results.append({
            'id': f.id,
            'type': 'loan',
            'file_name': f.file_name,
            'customer_name': item.client_name or '',
            'customer_id': item.customer_id or '',
            'loan_id': item.loan_id or '',
            'folder_name': f.folder.folder_type.name if f.folder.folder_type else '',
            'date': item.date_of_disbursement.strftime('%Y-%m-%d') if item.date_of_disbursement else '',
        })
    for f in doc_files:
        item = f.folder.archive_item
        results.append({
            'id': f.id,
            'type': 'document',
            'file_name': f.file_name,
            'customer_name': item.title,   # show document title
            'customer_id': '',
            'loan_id': '',
            'folder_name': f.folder.name,
            'date': item.created_at.strftime('%Y-%m-%d'),
        })

    return JsonResponse({'files': results})





@login_required
@admin_or_manager_required
def add_file_to_shared_folder_api(request, pk):
    folder = get_object_or_404(SharedFolder, pk=pk)
    data = json.loads(request.body)
    file_id = data.get('file_id')
    item_file = get_object_or_404(ItemFile, pk=file_id)
    obj, created = SharedFolderFile.objects.get_or_create(shared_folder=folder, item_file=item_file)
    return JsonResponse({'status': 'ok'})





@login_required
@admin_or_manager_required
@require_http_methods(['POST'])
def add_file_to_shared_folder(request, pk):   # <-- change folder_id to pk
    try:
        folder = SharedFolder.objects.get(pk=pk)
    except SharedFolder.DoesNotExist:
        return JsonResponse({'error': 'Shared folder not found'}, status=404)

    if not (folder.created_by == request.user or request.user.is_superuser or request.user.is_archive_manager()):
        return JsonResponse({'error': 'You do not have permission to modify this shared folder'}, status=403)

    try:
        data = json.loads(request.body)
        file_id = data.get('file_id')
        file_type = data.get('file_type')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not file_id or not file_type:
        return JsonResponse({'error': 'Missing file_id or file_type'}, status=400)

    if file_type == 'loan':
        try:
            loan_file = ItemFile.objects.get(id=file_id)
            folder.loan_files.add(loan_file)
        except ItemFile.DoesNotExist:
            return JsonResponse({'error': 'Loan file not found'}, status=404)
    elif file_type == 'document':
        try:
            doc_file = DocumentFile.objects.get(id=file_id)
            folder.doc_files.add(doc_file)
        except DocumentFile.DoesNotExist:
            return JsonResponse({'error': 'Document file not found'}, status=404)
    else:
        return JsonResponse({'error': 'Invalid file_type'}, status=400)

    return JsonResponse({'status': 'ok'})




@login_required
@admin_or_manager_required
def remove_file_from_shared_folder_api(request, pk, file_pk):
    folder = get_object_or_404(SharedFolder, pk=pk)
    sff = get_object_or_404(SharedFolderFile, shared_folder=folder, item_file__pk=file_pk)
    sff.delete()
    return JsonResponse({'status': 'ok'})



@admin_or_manager_required
@require_http_methods(['POST'])
def shared_folder_access_api(request, pk):
    folder = get_object_or_404(SharedFolder, pk=pk)
    try:
        data = json.loads(request.body)
        user_ids = [int(uid) for uid in data.get('user_ids', [])]
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'status': 'error', 'error': 'Invalid user_ids'}, status=400)

    folder.allowed_users.exclude(user__id__in=user_ids).delete()
    for uid in user_ids:
        user = get_object_or_404(User, id=uid)
        SharedFolderAccess.objects.get_or_create(
            shared_folder=folder,
            user=user,
            defaults={'granted_by': request.user}
        )
    return JsonResponse({'status': 'ok'})




@admin_or_manager_required
@require_http_methods(['POST'])
def revoke_shared_folder_access(request, pk):
    folder = get_object_or_404(SharedFolder, pk=pk)
    data = json.loads(request.body)
    user_id = data.get('user_id')
    if not user_id:
        return JsonResponse({'status': 'error', 'error': 'user_id required'}, status=400)
    folder.allowed_users.filter(user_id=user_id).delete()
    return JsonResponse({'status': 'ok'})






@admin_or_manager_required
@require_http_methods(['POST'])
def shared_folder_update_api(request, pk):
    folder = get_object_or_404(SharedFolder, pk=pk)
    data = json.loads(request.body)
    name = data.get('name')
    description = data.get('description', '')
    if not name:
        return JsonResponse({'status': 'error', 'error': 'Name is required'}, status=400)
    folder.name = name
    folder.description = description
    folder.save()
    return JsonResponse({'status': 'ok'})






############## DOCUMENT ############################


from django.core.files.storage import default_storage

@login_required
@require_http_methods(['GET', 'POST'])
def document_folders_api(request, item_id):
    item = get_object_or_404(ArchiveItem, id=item_id)
    if request.method == 'GET':
        folders = DocumentFolder.objects.filter(archive_item=item, user=request.user).order_by('created_at')
        data = [{
            'id': f.id,
            'name': f.name,
            'files': [{
                'asset_uuid': str(file.asset_uuid),
                'file_name': file.file_name,
                'file_size': file.file_size,
                'mime_type': file.mime_type,
            } for file in f.files.all()]
        } for f in folders]
        return JsonResponse({'folders': data})
    # POST – create a new folder
    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Folder name required'}, status=400)
    if DocumentFolder.objects.filter(archive_item=item, user=request.user, name=name).exists():
        return JsonResponse({'error': 'A folder with this name already exists'}, status=400)
    folder = DocumentFolder.objects.create(
        archive_item=item,
        user=request.user,
        name=name,
    )
    return JsonResponse({'id': folder.id, 'name': folder.name})


@login_required
@admin_or_manager_required
@require_http_methods(['DELETE'])
def delete_document_folder(request, folder_id):
    folder = get_object_or_404(DocumentFolder, id=folder_id, user=request.user)
    folder.delete()
    return JsonResponse({'status': 'ok'})


@login_required
@require_http_methods(['POST'])
def add_file_to_document_folder(request, folder_id):
    folder = get_object_or_404(DocumentFolder, id=folder_id, user=request.user)
    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'error': 'No file provided'}, status=400)
    asset_uuid = uuid.uuid4()
    path = f'document_folders/{asset_uuid}/{uploaded_file.name}'
    saved_path = default_storage.save(path, uploaded_file)
    DocumentFile.objects.create(
        folder=folder,
        asset_uuid=asset_uuid,
        file_name=uploaded_file.name,
        file_size=uploaded_file.size,
        mime_type=uploaded_file.content_type,
        stored_path=saved_path,
    )
    return JsonResponse({'asset_uuid': str(asset_uuid), 'file_name': uploaded_file.name, 'file_size': uploaded_file.size, 'mime_type': uploaded_file.content_type})


@login_required
@require_http_methods(['DELETE'])
def delete_document_file(request, asset_uuid):
    try:
        file_obj = DocumentFile.objects.get(asset_uuid=asset_uuid, folder__user=request.user)
    except DocumentFile.DoesNotExist:
        return JsonResponse({'error': 'File not found'}, status=404)
    if file_obj.stored_path and default_storage.exists(file_obj.stored_path):
        default_storage.delete(file_obj.stored_path)
    file_obj.delete()
    return JsonResponse({'status': 'ok'})



@login_required
def request_document_access(request, item_id):
    item = get_object_or_404(ArchiveItem, id=item_id, is_deleted=False)

    # Check for existing pending request
    existing = AccessRequest.objects.filter(
        requester_user=request.user,
        archive_item=item,
        status=AccessRequest.STATUS_PENDING
    ).first()
    if existing:
        messages.warning(request, 'You already have a pending request for this document.')
        return redirect('archive:my_access_requests')

    if request.method == 'POST':
        # Get reason from the form (validate using form)
        form = AccessRequestForm(request.POST)
        expiry_str = request.POST.get('granted_access_until')

        if not expiry_str:
            messages.error(request, 'Please select an expiry date and time.')
            return render(request, 'archive/request_access.html', {'form': form, 'item': item})

        # Parse the local datetime string to naive datetime
        try:
            naive_expiry = datetime.datetime.strptime(expiry_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            messages.error(request, 'Invalid expiry date format.')
            return render(request, 'archive/request_access.html', {'form': form, 'item': item})

        aware_expiry = timezone.make_aware(naive_expiry)

        if aware_expiry <= timezone.now():
            messages.error(request, 'Expiration date must be in the future.')
            return render(request, 'archive/request_access.html', {'form': form, 'item': item})

        if aware_expiry > timezone.now() + timezone.timedelta(days=30):
            messages.error(request, 'Access cannot be granted for more than 30 days.')
            return render(request, 'archive/request_access.html', {'form': form, 'item': item})

        if form.is_valid():
            access_req = form.save(commit=False)
            access_req.requester_user = request.user
            access_req.archive_item = item
            access_req.granted_access_until = aware_expiry
            access_req.save()
            messages.success(request, 'Your access request has been submitted for review.')
            return redirect('archive:my_access_requests')
        else:
            # Form errors will be displayed in template
            return render(request, 'archive/request_access.html', {'form': form, 'item': item})
    else:
        # GET: create a blank form (no initial expiry, JavaScript will set it)
        form = AccessRequestForm()

    return render(request, 'archive/request_access.html', {'form': form, 'item': item})





import logging
import datetime
logger = logging.getLogger(__name__)

@login_required
@require_POST
def update_document_full(request, item_id):
    try:
        item = ArchiveItem.objects.get(id=item_id, is_deleted=False)
    except ArchiveItem.DoesNotExist:
        return JsonResponse({'error': 'Document not found'}, status=404)

    if not request.user.can_modify_item(item):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    logger.info(f"Updating item {item_id} with data: {data}")

    title = data.get('title', '').strip()
    if not title:
        return JsonResponse({'error': 'Title is required'}, status=400)
    item.title = title

    item.description = data.get('description', '')

    old_doc_type = item.document_type
    new_doc_type = data.get('document_type')
    if new_doc_type in ['loan', 'department', 'public']:
        item.document_type = new_doc_type

    status_id = data.get('status_id')
    if status_id:
        try:
            item.status = WorkflowStatus.objects.get(id=status_id)
        except WorkflowStatus.DoesNotExist:
            logger.warning(f"Status id {status_id} not found")

    collection_id = data.get('collection_id')
    if collection_id:
        try:
            item.collection = Collection.objects.get(id=collection_id)
        except Collection.DoesNotExist:
            logger.warning(f"Collection id {collection_id} not found")

    if item.document_type == ArchiveItem.DOCUMENT_TYPE_LOAN:
        item.client_name = data.get('client_name', '')
        item.customer_id = data.get('customer_id', '')
        item.loan_id = data.get('loan_id', '')
        item.period = data.get('period', '')
        item.product_type = data.get('product_type', '')
        item.loan_status = data.get('loan_status', 'active')

        disbursement_date = data.get('disbursement_date')
        if disbursement_date:
            try:
                item.date_of_disbursement = datetime.datetime.strptime(disbursement_date, '%Y-%m-%d').date()
            except:
                pass
        else:
            year_str = data.get('year')
            if year_str and year_str.isdigit():
                try:
                    item.date_of_disbursement = datetime.date(int(year_str), 1, 1)
                except:
                    pass

    else:
        if old_doc_type == ArchiveItem.DOCUMENT_TYPE_LOAN:
            item.client_name = ''
            item.customer_id = ''
            item.loan_id = ''
            item.period = ''
            item.product_type = ''
            item.loan_status = 'active'
            item.date_of_disbursement = None

    item.save()
    logger.info(f"Item {item_id} updated successfully")
    return JsonResponse({'success': True})




@login_required
@require_POST
def update_item_tags(request, item_id):
    try:
        item = ArchiveItem.objects.get(id=item_id, is_deleted=False)
    except ArchiveItem.DoesNotExist:
        return JsonResponse({'error': 'Document not found'}, status=404)

    if not request.user.can_modify_item(item):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    tags = data.get('tags', [])
    from archive.models import Tag
    tag_objs = []
    for tag_name in tags:
        tag, _ = Tag.objects.get_or_create(name=tag_name.strip())
        tag_objs.append(tag)
    item.tags.set(tag_objs)
    item.save()

    return JsonResponse({'success': True})







from django.core.files.storage import default_storage
from django.contrib.admin.views.decorators import staff_member_required

@login_required
@user_passes_test(lambda u: u.is_system_admin())
def deleted_items_list(request):
    deleted_files = ItemFile.objects.filter(is_deleted=True).select_related('folder__archive_item')
    deleted_folders = ItemFolder.objects.filter(is_deleted=True).select_related('archive_item')
    context = {
        'deleted_files': deleted_files,
        'deleted_folders': deleted_folders,
    }
    return render(request, 'archive/deleted_items.html', context)

@login_required
@user_passes_test(lambda u: u.is_system_admin())
@require_http_methods(['POST'])
def restore_item_file(request, file_id):
    file_obj = get_object_or_404(ItemFile, id=file_id, is_deleted=True)
    file_obj.is_deleted = False
    file_obj.deleted_at = None
    file_obj.save()
    return JsonResponse({'status': 'restored'})

@login_required
@user_passes_test(lambda u: u.is_system_admin())
@require_http_methods(['DELETE'])
def permanent_delete_item_file(request, file_id):
    file_obj = get_object_or_404(ItemFile, id=file_id, is_deleted=True)
    if file_obj.stored_path:
        default_storage.delete(file_obj.stored_path)
    file_obj.delete()
    return JsonResponse({'status': 'permanently_deleted'})

# Same for folders
@login_required
@user_passes_test(lambda u: u.is_system_admin())
@require_http_methods(['POST'])
def restore_item_folder(request, folder_id):
    folder = get_object_or_404(ItemFolder, id=folder_id, is_deleted=True)
    folder.is_deleted = False
    folder.deleted_at = None
    folder.save()
    return JsonResponse({'status': 'restored'})

@login_required
@user_passes_test(lambda u: u.is_system_admin())
@require_http_methods(['DELETE'])
def permanent_delete_item_folder(request, folder_id):
    folder = get_object_or_404(ItemFolder, id=folder_id, is_deleted=True)
    # Optionally delete all files inside the folder permanently
    for file in folder.files.all():
        if file.stored_path:
            default_storage.delete(file.stored_path)
        file.delete()
    folder.delete()
    return JsonResponse({'status': 'permanently_deleted'})








from django.http import JsonResponse
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import DeletionRequest, ItemFile, ItemFolder

@login_required
@admin_or_manager_required
@require_http_methods(['POST'])
def request_delete_item(request):
    """Create a deletion request (pending approval)"""
    data = json.loads(request.body)
    item_type = data.get('type')      # 'file' or 'folder'
    item_id = data.get('id')
    reason = data.get('reason', '')

    if item_type == 'file':
        if request.user.is_system_admin() or request.user.is_archive_manager():
            item = get_object_or_404(ItemFile, asset_uuid=item_id)
        else:
            item = get_object_or_404(ItemFile, asset_uuid=item_id, folder__user=request.user)
        req = DeletionRequest.objects.create(
            request_type='file',
            item_file=item,
            requested_by=request.user,
            reason=reason
        )
    elif item_type == 'folder':
        if request.user.is_system_admin() or request.user.is_archive_manager():
            item = get_object_or_404(ItemFolder, id=item_id)
        else:
            item = get_object_or_404(ItemFolder, id=item_id, user=request.user)
        req = DeletionRequest.objects.create(
            request_type='folder',
            item_folder=item,
            requested_by=request.user,
            reason=reason
        )
    else:
        return JsonResponse({'error': 'Invalid type'}, status=400)

    return JsonResponse({'status': 'request_created', 'request_id': req.id})


@user_passes_test(lambda u: u.is_system_admin())
@require_http_methods(['POST'])
def approve_deletion(request, request_id):
    """Admin approves a deletion request -> soft-delete the item"""
    req = get_object_or_404(DeletionRequest, id=request_id, status='pending')
    if req.request_type == 'file':
        file_obj = req.item_file
        if file_obj:
            file_obj.is_deleted = True
            file_obj.deleted_at = timezone.now()
            file_obj.deleted_by = request.user  
            file_obj.save()
    elif req.request_type == 'folder':
        folder = req.item_folder
        if folder:
            folder.is_deleted = True
            folder.deleted_at = timezone.now()
            folder.deleted_by = request.user  
            folder.save()
            for file in folder.files.all():
                file.is_deleted = True
                file.deleted_at = timezone.now()
                file.deleted_by = request.user 
                file.save()
    req.status = 'approved'
    req.reviewed_by = request.user
    req.reviewed_at = timezone.now()
    req.save()
    return JsonResponse({'status': 'approved'})


@user_passes_test(lambda u: u.is_system_admin())
@require_http_methods(['POST'])
def reject_deletion(request, request_id):
    """Admin rejects the deletion request"""
    req = get_object_or_404(DeletionRequest, id=request_id, status='pending')
    reason = json.loads(request.body).get('reason', '')
    req.status = 'rejected'
    req.rejection_reason = reason
    req.reviewed_by = request.user
    req.reviewed_at = timezone.now()
    req.save()
    return JsonResponse({'status': 'rejected'})



# @user_passes_test(lambda u: u.is_system_admin())
# def pending_deletions_list(request):
#     requests = DeletionRequest.objects.filter(status='pending').order_by('-requested_at')
#     return render(request, 'archive/pending_deletions.html', {'requests': requests})
@user_passes_test(lambda u: u.is_system_admin())
def pending_deletions_list(request):
    requests = DeletionRequest.objects.filter(status='pending').select_related('item_file', 'item_folder')
    files_count = requests.exclude(item_file__isnull=True).count()
    folders_count = requests.exclude(item_folder__isnull=True).count()
    return render(request, 'archive/pending_deletions.html', {
        'requests': requests,
        'files_count': files_count,
        'folders_count': folders_count,
    })


# archive/views.py
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from .models import ItemFolder, ItemFile

@login_required
@require_http_methods(["GET"])
def deleted_folder_files(request, folder_id):
    """Return all soft-deleted files inside a soft-deleted ItemFolder."""
    try:
        # Ensure folder exists, is deleted, and user has permission to view it
        folder = ItemFolder.objects.get(id=folder_id, is_deleted=True)
    except ItemFolder.DoesNotExist:
        return JsonResponse({'error': 'Folder not found or not deleted'}, status=404)

    # Optional: check user permission (only admins or the folder owner)
    # Adjust according to your access logic – example:
    if not (request.user.is_system_admin() or request.user == folder.user):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    # Get all files inside this folder that are also soft-deleted
    files = ItemFile.objects.filter(
        folder_id=folder.id,
        is_deleted=True
    ).select_related('folder')

    # Build response
    file_list = []
    for f in files:
        # Build relative path: FolderType name / file_name (since no subfolders)
        relative_path = f"{folder.folder_type.name}/{f.file_name}" if folder.folder_type else f.file_name

        file_list.append({
            'id': f.id,
            'name': f.file_name,
            'relative_path': relative_path,
            'size': f.file_size,
            'stored_path': f.stored_path,
            'deleted_at': f.deleted_at.isoformat() if f.deleted_at else None,
        })

    return JsonResponse({'files': file_list})




from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from .models import ItemFolder, ItemFile

@login_required
@require_http_methods(["GET"])
def folder_files(request, folder_id):
    """Return all files inside a non‑deleted folder (for preview before approving deletion)."""
    try:
        folder = ItemFolder.objects.get(id=folder_id, is_deleted=False)
    except ItemFolder.DoesNotExist:
        return JsonResponse({'error': 'Folder not found'}, status=404)

    # Permission check: only the folder owner or system admin / archive manager
    if not (request.user.is_system_admin() or request.user == folder.user or request.user.is_archive_manager()):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    files = ItemFile.objects.filter(folder=folder).order_by('uploaded_at')

    file_list = []
    for f in files:
        # Build a relative path (folder_type.name / file_name)
        relative_path = f"{folder.folder_type.name}/{f.file_name}" if folder.folder_type else f.file_name
        file_list.append({
            'id': f.id,
            'name': f.file_name,
            'relative_path': relative_path,
            'size': f.file_size,
            'stored_path': f.stored_path,
            'uploaded_at': f.uploaded_at.isoformat(),
        })
    return JsonResponse({'files': file_list})





# Example snippet
@login_required
@require_http_methods(["POST"])
def create_item_folder(request, item_id):
    """
    Create a new folder (top‑level or subfolder) for a given loan.
    If top‑level, automatically creates all descendant subfolders.
    """
    try:
        item = ArchiveItem.objects.get(id=item_id)
    except ArchiveItem.DoesNotExist:
        return JsonResponse({'error': 'Loan not found'}, status=404)

    if not (request.user.is_system_admin() or request.user == item.created_by):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    data = request.POST
    folder_type_id = data.get('folder_type_id')
    parent_id = data.get('parent_id')

    if not folder_type_id:
        return JsonResponse({'error': 'Folder type ID required'}, status=400)

    try:
        folder_type = FolderType.objects.get(id=folder_type_id)
    except FolderType.DoesNotExist:
        return JsonResponse({'error': 'Invalid folder type'}, status=400)

    parent_folder = None
    if parent_id:
        try:
            parent_folder = ItemFolder.objects.get(id=parent_id, is_deleted=False, archive_item=item)
        except ItemFolder.DoesNotExist:
            return JsonResponse({'error': 'Parent folder not found or deleted'}, status=400)

    # Check for active folder of the same type under the same parent
    if ItemFolder.objects.filter(
        archive_item=item,
        user=request.user,
        folder_type=folder_type,
        parent=parent_folder,
        is_deleted=False
    ).exists():
        return JsonResponse({'error': 'This folder type already exists for this loan'}, status=400)

    # Create the main folder
    new_folder = ItemFolder.objects.create(
        archive_item=item,
        user=request.user,
        folder_type=folder_type,
        parent=parent_folder,
        is_deleted=False
    )

    # If this is a top‑level folder (no parent), recursively create all child subfolders
    if not parent_id:
        create_child_folders(new_folder, request.user, item, folder_type)

    return JsonResponse({'status': 'ok', 'id': new_folder.id})


def create_child_folders(parent_folder, user, archive_item, parent_folder_type):
    """Recursively create child folder instances for all child folder types."""
    child_types = FolderType.objects.filter(parent=parent_folder_type)
    for child_type in child_types:
        # Skip if already exists (non‑deleted)
        if not ItemFolder.objects.filter(
            archive_item=archive_item,
            user=user,
            folder_type=child_type,
            parent=parent_folder,
            is_deleted=False
        ).exists():
            child_folder = ItemFolder.objects.create(
                archive_item=archive_item,
                user=user,
                folder_type=child_type,
                parent=parent_folder,
                is_deleted=False
            )
            # Go deeper
            create_child_folders(child_folder, user, archive_item, child_type)