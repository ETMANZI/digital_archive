import json
import hashlib
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.files.storage import default_storage
from django.utils import timezone
from django.core.exceptions import PermissionDenied
from dam import settings
from .models import AccessRequest, ActiveSession, ArchiveItem, AuditLog, Collection, DocumentFile, DocumentText, FolderType, ItemFile, RelatedItem, Tag, User, UserFolder, WorkflowStatus, FileAsset
from django.core.paginator import Paginator
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.shortcuts import render, redirect
from django.contrib import messages
from .forms import AccessRequestForm, CollectionForm,CustomUserCreationForm
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required
from collections import defaultdict
from django.db.models import Q
from django.http import FileResponse, Http404
from django.core.files.storage import default_storage
from django.views.decorators.clickjacking import xframe_options_exempt
from .models import FileAsset
from django.shortcuts import get_object_or_404

from django.core.files.base import ContentFile
from django.contrib.auth.models import Group
from datetime import datetime
import uuid as uuid_lib
import uuid
import secrets
from django.db.models import Count, Max
from archive.utils import create_audit_log
from django.views.decorators.http import require_POST
from django.db.models import BooleanField, Case, When, Value, Exists, OuterRef
from django.db.models import Q, Max, Count, BooleanField, Case, When, Value, Exists, OuterRef, F, CharField
from django.core.serializers.json import DjangoJSONEncoder
import re
from collections import defaultdict
from archive.decorators import *



def _normalize_customer_id(value):
    """Strips all whitespace and lowercases for consistent grouping."""
    return re.sub(r'\s+', '', value).lower()


def _check_can_view(user, item):
    """Returns True if the user has permission to view the given item."""
    if user.is_authenticated and (
        user.is_superuser
        or user.is_archive_manager()
        or getattr(user, 'is_system_admin', lambda: False)()
    ):
        return True

    col = item.collection
    if not col:
        return False

    policy = col.access_policy

    if policy == Collection.ACCESS_PUBLIC:
        return True

    if not user.is_authenticated:
        return False

    if policy == Collection.ACCESS_AUTHENTICATED:
        return True

    if policy == Collection.ACCESS_DEPARTMENT:
        return user.department == col.owner.department

    if policy == Collection.ACCESS_PRIVATE:
        if col.owner == user:
            return True
        return AccessRequest.objects.filter(
            archive_item=item,
            requester_user=user,
            status='APPROVED',
            granted_access_until__gt=timezone.now()
        ).exists()

    return False

@login_required
def home(request):

    folder_query = request.GET.get('q', '').strip()
    if folder_query:
        items = (
            ArchiveItem.objects
            .filter(is_deleted=False)
            .filter(
                Q(folders__folder_type__name__icontains=folder_query)
                | Q(doc_folders__name__icontains=folder_query)
                | Q(doc_folders__files__file_name__icontains=folder_query)
            )
            .distinct()
            .select_related('collection', 'status')
            .prefetch_related(
                'tags', 'files',
                'folders', 'folders__files', 'folders__folder_type',
                'doc_folders', 'doc_folders__files',
            )
            .order_by('-date_of_disbursement', '-created_at')
        )

        # Annotate access permission at the DB level where possible
        user = request.user
        if user.is_authenticated and (
            user.is_superuser
            or user.is_archive_manager()
            or getattr(user, 'is_system_admin', lambda: False)()
        ):
            items = items.annotate(can_view=Value(True, output_field=BooleanField()))
        else:
            if user.is_authenticated:
                can_view_case = Case(
                    When(collection__access_policy=Collection.ACCESS_PUBLIC, then=Value(True)),
                    When(collection__access_policy=Collection.ACCESS_AUTHENTICATED, then=Value(True)),
                    When(
                        collection__access_policy=Collection.ACCESS_DEPARTMENT,
                        collection__owner__department=user.department,
                        then=Value(True),
                    ),
                    When(
                        collection__access_policy=Collection.ACCESS_PRIVATE,
                        collection__owner=user,
                        then=Value(True),
                    ),
                    When(
                        access_requests__requester_user=user,
                        access_requests__status='APPROVED',
                        access_requests__granted_access_until__gt=timezone.now(),
                        then=Value(True),
                    ),
                    default=Value(False),
                    output_field=BooleanField(),
                )
            else:
                can_view_case = Case(
                    When(collection__access_policy=Collection.ACCESS_PUBLIC, then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                )
            items = items.annotate(can_view=can_view_case)

        visible_items = [item for item in items if item.can_view]

        # Group by year
        grouped = defaultdict(list)
        for item in visible_items:
            year = (
                item.date_of_disbursement.year
                if item.date_of_disbursement
                else item.created_at.year
            )
            grouped[year].append(item)

        years_grouped = sorted(grouped.items(), key=lambda x: x[0], reverse=True)

        return render(request, 'archive/search_folders.html', {
            'search_query': folder_query,
            'years_grouped': years_grouped,
            'result_count': len(visible_items),
        })


    user = request.user

    all_items = (
        ArchiveItem.objects
        .filter(is_deleted=False)
        .select_related('collection', 'collection__owner', 'status')
        .prefetch_related('tags')
        .order_by('-created_at')
    )

    # Split into loan items (have a customer_id) and standalone items
    loan_items = []
    non_loan_items = []
    for item in all_items:
        if item.customer_id and item.customer_id.strip():
            loan_items.append(item)
        else:
            non_loan_items.append(item)

    # Group loan items by normalized customer_id, keep only the latest per group
    groups = defaultdict(list)
    for item in loan_items:
        key = _normalize_customer_id(item.customer_id)   # FIX: normalise key
        groups[key].append(item)

    result_items = []
    for items_in_group in groups.values():
        latest = max(items_in_group, key=lambda x: x.created_at)
        latest.item_count = len(items_in_group)
        result_items.append(latest)

    # Add standalone (non-loan) items
    for item in non_loan_items:
        item.item_count = 1
        if not item.client_name:
            item.client_name = f"Document #{item.id}"
        result_items.append(item)

    result_items.sort(key=lambda x: x.created_at, reverse=True)

    pending_item_ids = set()
    if user.is_authenticated:
        pending_item_ids = set(
            AccessRequest.objects
            .filter(requester_user=user, status='PENDING')
            .values_list('archive_item_id', flat=True)
        )

    for item in result_items:
        item.can_view = _check_can_view(user, item)
        item.has_pending_request = item.id in pending_item_ids

    paginator = Paginator(result_items, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'archive/home.html', {
        'collections': Collection.objects.all(),
        'tags': Tag.objects.all().order_by('name')[:20],
        'page_obj': page_obj,
    })







@csrf_exempt
def api_archive_items(request):
    if request.method == 'GET':
        base_qs = (
            ArchiveItem.objects
            .filter(is_deleted=False)
            .select_related('collection', 'collection__owner', 'status')
            .prefetch_related('tags')
            .order_by('-created_at')
        )
        user = request.user

        collection_id = request.GET.get('collection')
        if collection_id:
            base_qs = base_qs.filter(collection_id=collection_id)

        status = request.GET.get('status')
        if status:
            base_qs = base_qs.filter(status__name=status)

        tag_name = request.GET.get('tag')
        if tag_name:
            base_qs = base_qs.filter(tags__name=tag_name)

        search = request.GET.get('search', '').strip()
        if search:
            base_qs = base_qs.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search) |
                Q(tags__name__icontains=search)
            ).distinct()

        customer_id_filter = request.GET.get('customer_id', '').strip()
        if customer_id_filter:
            base_qs = base_qs.filter(customer_id=customer_id_filter)

        # ── Grouping by customer_id ───────────────────────────────────────
        all_items = list(base_qs)

        loan_items = []
        non_loan_items = []
        for item in all_items:
            if item.customer_id and item.customer_id.strip():
                loan_items.append(item)
            else:
                non_loan_items.append(item)

        # Group loan items by normalised customer_id, keep latest per group
        groups = defaultdict(list)
        for item in loan_items:
            key = re.sub(r'\s+', '', item.customer_id).lower()
            groups[key].append(item)

        result_items = []
        for items_in_group in groups.values():
            latest = max(items_in_group, key=lambda x: x.created_at)
            latest.item_count = len(items_in_group)
            result_items.append(latest)

        for item in non_loan_items:
            item.item_count = 1
            if not item.client_name:
                item.client_name = f"Document #{item.id}"
            result_items.append(item)

        result_items.sort(key=lambda x: x.created_at, reverse=True)

        # ── Permission check (bulk pending requests in one query) ─────────
        pending_item_ids = set()
        if user.is_authenticated:
            pending_item_ids = set(
                AccessRequest.objects
                .filter(requester_user=user, status='PENDING')
                .values_list('archive_item_id', flat=True)
            )

        def check_can_view(item):
            if user.is_authenticated and (
                user.is_superuser or user.is_archive_manager()
            ):
                return True
            col = item.collection
            if not col:
                return False
            policy = col.access_policy
            if policy == Collection.ACCESS_PUBLIC:
                return True
            if not user.is_authenticated:
                return False
            if policy == Collection.ACCESS_AUTHENTICATED:
                return True
            if policy == Collection.ACCESS_DEPARTMENT:
                return user.department == col.target_department
            if policy == Collection.ACCESS_PRIVATE:
                if col.owner == user:
                    return True
                return AccessRequest.objects.filter(
                    archive_item=item,
                    requester_user=user,
                    status='APPROVED',
                    granted_access_until__gt=timezone.now()
                ).exists()
            return False

        # ── Serialize ─────────────────────────────────────────────────────
        output_items = []
        for item in result_items:
            output_items.append({
                'id':                   item.id,
                'title':                item.title,
                'document_type':        item.document_type,
                'document_type_display': item.get_document_type_display(),
                'status':               item.status.name if item.status else '',
                'created_at':           item.created_at.strftime('%Y-%m-%d'),
                'collection_name':      item.collection.name if item.collection else '',
                'collection_id':        item.collection_id,
                'description':          item.description or '',
                'tags':                 [tag.name for tag in item.tags.all()],
                'client_name':          item.client_name or '',
                'customer_id':          item.customer_id or '',
                'loan_id':              item.loan_id or '',
                'item_count':           item.item_count,   # ← real count now
                'can_view':             check_can_view(item),
                'has_pending_request':  item.id in pending_item_ids,
            })

        # ── Pagination ────────────────────────────────────────────────────
        page      = int(request.GET.get('page', 1))
        page_size = 20
        start     = (page - 1) * page_size
        end       = start + page_size

        return JsonResponse({
            'total':     len(output_items),
            'items':     output_items[start:end],
            'page':      page,
            'page_size': page_size,
        })

    elif request.method == 'POST':
        try:
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.POST

            user = request.user if request.user.is_authenticated else None
            doc_type = data.get('document_type', 'loan')

            title = data.get('title', '').strip()
            if not title:
                return JsonResponse({'error': 'Title is required'}, status=400)

            client_name = ''
            customer_id = ''
            loan_id = ''
            if doc_type == 'loan':
                client_name = data.get('client_name', '').strip()
                customer_id = data.get('customer_id', '').strip()
                loan_id     = data.get('loan_id', '').strip()
                if not (client_name and customer_id and loan_id):
                    return JsonResponse(
                        {'error': 'For loan documents, Client Name, Customer ID, and Loan ID are required.'},
                        status=400
                    )
                existing = ArchiveItem.objects.filter(
                    loan_id=loan_id, is_deleted=False
                ).exclude(id=data.get('id')).first()
                if existing:
                    return JsonResponse(
                        {'error': f'An item with loan ID {loan_id} already exists.'},
                        status=400
                    )

            collection_id = data.get('collection_id')
            collection = get_object_or_404(Collection, id=collection_id) if collection_id else None

            status_name = data.get('status', 'Draft')
            status_obj, _ = WorkflowStatus.objects.get_or_create(name=status_name)

            item = ArchiveItem.objects.create(
                document_type=doc_type,
                title=title,
                description=data.get('description', ''),
                collection=collection,
                status=status_obj,
                client_name=client_name,
                customer_id=customer_id,
                loan_id=loan_id,
                period=data.get('period', ''),
                product_type=data.get('product_type', ''),
                date_of_disbursement=data.get('date_of_disbursement') or None,
                loan_status=data.get('loan_status', 'active'),
                created_by=user,
                last_modified_by=user,
            )
            create_audit_log(request, AuditLog.ACTION_CREATE, archive_item=item)

            tags_str = data.get('tags', '')
            for tag_name in [t.strip() for t in tags_str.split(',') if t.strip()]:
                tag, _ = Tag.objects.get_or_create(name=tag_name)
                item.tags.add(tag)

            for file_key, uploaded_file in request.FILES.items():
                if file_key.startswith('file_'):
                    idx = file_key.split('_')[-1]
                    custom_name  = data.get(f'file_name_{idx}', uploaded_file.name)
                    file_content = uploaded_file.read()
                    sha256_hash  = hashlib.sha256(file_content).hexdigest()
                    uploaded_file.seek(0)
                    storage_path = f"archive_files/{datetime.now().strftime('%Y/%m/%d')}/{sha256_hash[:16]}_{uuid.uuid4().hex}.bin"
                    saved_path   = default_storage.save(storage_path, ContentFile(file_content))
                    FileAsset.objects.create(
                        archive_item=item,
                        file_name=custom_name,
                        s3_key=saved_path,
                        mime_type=uploaded_file.content_type,
                        file_size=uploaded_file.size,
                        hash_sha256=sha256_hash,
                        encrypted=True,
                        uploaded_by=user,
                        virus_scan_status=FileAsset.VIRUS_PENDING,
                    )

            return JsonResponse({'id': item.id, 'message': 'Created'}, status=201)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)




# @csrf_exempt
# @admin_or_manager_required
# def api_update_archive_item(request, item_id):
#     if not (request.method == 'POST' and request.POST.get('_method') == 'PUT'):
#         return JsonResponse({'error': 'Method not allowed (use POST with _method=PUT)'}, status=405)

#     try:
#         item = get_object_or_404(ArchiveItem, id=item_id, is_deleted=False)
#         user = request.user if request.user.is_authenticated else None
#         data = request.POST

#         doc_type = data.get('document_type', item.document_type)

#         if doc_type == 'loan':
#             client_name = data.get('client_name', '').strip()
#             customer_id = data.get('customer_id', '').strip()
#             loan_id = data.get('loan_id', '').strip()
#             if not (client_name and customer_id and loan_id):
#                 return JsonResponse({'error': 'For loan documents, Client Name, Customer ID, and Loan ID are required.'}, status=400)
#             # Check uniqueness of loan_id if changed
#             if loan_id != item.loan_id:
#                 existing = ArchiveItem.objects.filter(loan_id=loan_id, is_deleted=False).exclude(id=item.id).first()
#                 if existing:
#                     return JsonResponse({'error': f'An item with loan ID {loan_id} already exists.'}, status=400)
#             # Update loan fields
#             item.client_name = client_name
#             item.customer_id = customer_id
#             item.loan_id = loan_id
#         else:
#             # For non‑loan, clear loan fields
#             item.client_name = ''
#             item.customer_id = ''
#             item.loan_id = ''

#         # Update common fields
#         item.document_type = doc_type
#         item.title = data.get('title', item.title)
#         item.description = data.get('description', item.description)
#         item.period = data.get('period', item.period)
#         item.product_type = data.get('product_type', item.product_type)
#         item.date_of_disbursement = data.get('date_of_disbursement') or None
#         item.loan_status = data.get('loan_status', item.loan_status)

#         if data.get('collection_id'):
#             try:
#                 item.collection = Collection.objects.get(id=data['collection_id'])
#             except Collection.DoesNotExist:
#                 pass

#         status_name = data.get('status', 'Draft')
#         item.status, _ = WorkflowStatus.objects.get_or_create(name=status_name)

#         item.last_modified_by = user
#         item.save()

#         tags_str = data.get('tags', '')
#         tag_names = [t.strip() for t in tags_str.split(',') if t.strip()]
#         item.tags.clear()
#         for name in tag_names:
#             tag, _ = Tag.objects.get_or_create(name=name)
#             item.tags.add(tag)

#         delete_uuids = data.get('delete_file_uuids', '[]')
#         if isinstance(delete_uuids, str):
#             try:
#                 delete_uuids = json.loads(delete_uuids)
#             except json.JSONDecodeError:
#                 delete_uuids = []
#         files_to_delete = set(str(u) for u in delete_uuids)

#         for file_asset in item.files.all():
#             if str(file_asset.asset_uuid) in files_to_delete:
#                 try:
#                     if default_storage.exists(file_asset.s3_key):
#                         default_storage.delete(file_asset.s3_key)
#                     if file_asset.thumbnail_s3_key and default_storage.exists(file_asset.thumbnail_s3_key):
#                         default_storage.delete(file_asset.thumbnail_s3_key)
#                 except Exception as e:
#                     print(f"Error deleting file asset {file_asset.asset_uuid}: {e}")
#                 file_asset.delete()

#         for file_key, uploaded_file in request.FILES.items():
#             if file_key.startswith('file_'):
#                 parts = file_key.split('_')
#                 idx = parts[-1] if len(parts) > 1 else '0'
#                 custom_name = data.get(f'file_name_{idx}', uploaded_file.name)

#                 file_content = uploaded_file.read()
#                 sha256_hash = hashlib.sha256(file_content).hexdigest()
#                 uploaded_file.seek(0)

#                 date_path = datetime.now().strftime('%Y/%m/%d')
#                 safe_filename = f"{sha256_hash[:16]}_{uuid.uuid4().hex}.bin"
#                 storage_path = f"archive_files/{date_path}/{safe_filename}"
#                 saved_path = default_storage.save(storage_path, ContentFile(file_content))

#                 FileAsset.objects.create(
#                     archive_item=item,
#                     file_name=custom_name,
#                     s3_key=saved_path,
#                     mime_type=uploaded_file.content_type,
#                     file_size=uploaded_file.size,
#                     hash_sha256=sha256_hash,
#                     encrypted=True,
#                     uploaded_by=user,
#                     virus_scan_status=FileAsset.VIRUS_PENDING,
#                 )

#         return JsonResponse({'success': True, 'item_id': item.id})

#     except Exception as e:
#         import traceback
#         traceback.print_exc()
#         return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@admin_or_manager_required
def api_update_archive_item(request, item_id):
    if not (request.method == 'POST' and request.POST.get('_method') == 'PUT'):
        return JsonResponse({'error': 'Method not allowed (use POST with _method=PUT)'}, status=405)

    try:
        item = get_object_or_404(ArchiveItem, id=item_id, is_deleted=False)
        user = request.user if request.user.is_authenticated else None
        data = request.POST
        
        # Capture old values BEFORE any changes
        old_values = {
            'title': item.title,
            'description': item.description,
            'document_type': item.document_type,
            'client_name': item.client_name,
            'customer_id': item.customer_id,
            'loan_id': item.loan_id,
            'period': item.period,
            'product_type': item.product_type,
            'date_of_disbursement': str(item.date_of_disbursement) if item.date_of_disbursement else None,
            'loan_status': item.loan_status,
            'collection': item.collection.name if item.collection else None,
            'status': item.status.name if item.status else None,
            'tags': list(item.tags.values_list('name', flat=True))
        }

        doc_type = data.get('document_type', item.document_type)

        if doc_type == 'loan':
            client_name = data.get('client_name', '').strip()
            customer_id = data.get('customer_id', '').strip()
            loan_id = data.get('loan_id', '').strip()
            if not (client_name and customer_id and loan_id):
                return JsonResponse({'error': 'For loan documents, Client Name, Customer ID, and Loan ID are required.'}, status=400)
            # Check uniqueness of loan_id if changed
            if loan_id != item.loan_id:
                existing = ArchiveItem.objects.filter(loan_id=loan_id, is_deleted=False).exclude(id=item.id).first()
                if existing:
                    return JsonResponse({'error': f'An item with loan ID {loan_id} already exists.'}, status=400)
            # Update loan fields
            item.client_name = client_name
            item.customer_id = customer_id
            item.loan_id = loan_id
        else:
            # For non‑loan, clear loan fields
            item.client_name = ''
            item.customer_id = ''
            item.loan_id = ''

        # Update common fields
        item.document_type = doc_type
        item.title = data.get('title', item.title)
        item.description = data.get('description', item.description)
        item.period = data.get('period', item.period)
        item.product_type = data.get('product_type', item.product_type)
        
        disbursement_date = data.get('date_of_disbursement')
        item.date_of_disbursement = disbursement_date if disbursement_date else None
        
        item.loan_status = data.get('loan_status', item.loan_status)

        if data.get('collection_id'):
            try:
                item.collection = Collection.objects.get(id=data['collection_id'])
            except Collection.DoesNotExist:
                pass

        status_name = data.get('status', 'Draft')
        item.status, _ = WorkflowStatus.objects.get_or_create(name=status_name)

        item.last_modified_by = user
        item.save()

        # Capture new values AFTER changes
        new_values = {
            'title': item.title,
            'description': item.description,
            'document_type': item.document_type,
            'client_name': item.client_name,
            'customer_id': item.customer_id,
            'loan_id': item.loan_id,
            'period': item.period,
            'product_type': item.product_type,
            'date_of_disbursement': str(item.date_of_disbursement) if item.date_of_disbursement else None,
            'loan_status': item.loan_status,
            'collection': item.collection.name if item.collection else None,
            'status': item.status.name if item.status else None,
        }

        # Handle tags
        tags_str = data.get('tags', '')
        tag_names = [t.strip() for t in tags_str.split(',') if t.strip()]
        item.tags.clear()
        for name in tag_names:
            tag, _ = Tag.objects.get_or_create(name=name)
            item.tags.add(tag)
        new_values['tags'] = tag_names

        # Build separate old and new dictionaries for ONLY changed fields
        old_changes = {}
        new_changes = {}
        
        # Check each field for changes
        for field in ['title', 'description', 'document_type', 'client_name', 'customer_id', 
                      'loan_id', 'period', 'product_type', 'loan_status', 'status', 'collection']:
            if old_values.get(field) != new_values.get(field):
                old_changes[field] = old_values.get(field) or '(empty)'
                new_changes[field] = new_values.get(field) or '(empty)'
        
        # Check date change
        if old_values.get('date_of_disbursement') != new_values.get('date_of_disbursement'):
            old_changes['date_of_disbursement'] = old_values.get('date_of_disbursement') or '(empty)'
            new_changes['date_of_disbursement'] = new_values.get('date_of_disbursement') or '(empty)'
        
        # Check tags change
        if set(old_values.get('tags', [])) != set(new_values.get('tags', [])):
            old_changes['tags'] = ', '.join(old_values.get('tags', [])) or '(empty)'
            new_changes['tags'] = ', '.join(new_values.get('tags', [])) or '(empty)'

        # Create audit log ONLY if there are changes
        if old_changes and new_changes:
            from archive.models import AuditLog
            AuditLog.objects.create(
                user=request.user,
                archive_item=item,
                action=AuditLog.ACTION_MODIFY,
                old_value=old_changes,  # Store ONLY old values of changed fields
                new_value=new_changes,  # Store ONLY new values of changed fields
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )

        # Handle file deletions
        delete_uuids = data.get('delete_file_uuids', '[]')
        if isinstance(delete_uuids, str):
            try:
                delete_uuids = json.loads(delete_uuids)
            except json.JSONDecodeError:
                delete_uuids = []
        files_to_delete = set(str(u) for u in delete_uuids)

        for file_asset in item.files.all():
            if str(file_asset.asset_uuid) in files_to_delete:
                # Log file deletion - store old value as file name
                AuditLog.objects.create(
                    user=request.user,
                    archive_item=item,
                    action=AuditLog.ACTION_DELETE,
                    old_value={'deleted_file': file_asset.file_name},
                    new_value=None,
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', '')
                )
                try:
                    if default_storage.exists(file_asset.s3_key):
                        default_storage.delete(file_asset.s3_key)
                    if file_asset.thumbnail_s3_key and default_storage.exists(file_asset.thumbnail_s3_key):
                        default_storage.delete(file_asset.thumbnail_s3_key)
                except Exception as e:
                    print(f"Error deleting file asset {file_asset.asset_uuid}: {e}")
                file_asset.delete()

        # Handle new file uploads
        for file_key, uploaded_file in request.FILES.items():
            if file_key.startswith('file_'):
                parts = file_key.split('_')
                idx = parts[-1] if len(parts) > 1 else '0'
                custom_name = data.get(f'file_name_{idx}', uploaded_file.name)

                file_content = uploaded_file.read()
                sha256_hash = hashlib.sha256(file_content).hexdigest()
                uploaded_file.seek(0)

                date_path = datetime.now().strftime('%Y/%m/%d')
                safe_filename = f"{sha256_hash[:16]}_{uuid.uuid4().hex}.bin"
                storage_path = f"archive_files/{date_path}/{safe_filename}"
                saved_path = default_storage.save(storage_path, ContentFile(file_content))

                file_asset = FileAsset.objects.create(
                    archive_item=item,
                    file_name=custom_name,
                    s3_key=saved_path,
                    mime_type=uploaded_file.content_type,
                    file_size=uploaded_file.size,
                    hash_sha256=sha256_hash,
                    encrypted=True,
                    uploaded_by=user,
                    virus_scan_status=FileAsset.VIRUS_PENDING,
                )
                
                # Log file creation - store new value as file details
                AuditLog.objects.create(
                    user=request.user,
                    archive_item=item,
                    action=AuditLog.ACTION_CREATE,
                    old_value=None,
                    new_value={'added_file': custom_name, 'size': f"{uploaded_file.size} bytes"},
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', '')
                )

        return JsonResponse({'success': True, 'item_id': item.id})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)









def api_archive_item_detail(request, item_id):
    item = get_object_or_404(ArchiveItem, id=item_id, is_deleted=False)
    data = {
        'id': item.id,
        'title': item.title,
        'status': item.status.name,
        'created_at': item.created_at.strftime('%Y-%m-%d'),
        'collection_name': item.collection.name if item.collection else None,
        'collection_id': item.collection.id if item.collection else None,
        'description': item.description,
        'tags': [tag.name for tag in item.tags.all()],
        'client_name': item.client_name,
        'customer_id': item.customer_id,
        'loan_id': item.loan_id,
        'period': item.period,
        'product_type': item.product_type,
        'date_of_disbursement': item.date_of_disbursement,
        'loan_status': item.loan_status,
        'files': [
            {
                'asset_uuid': f.asset_uuid,
                'file_name': f.file_name,
                'file_size': f.file_size,
                'url': f.s3_key,
            } for f in item.files.all()
        ],
    }
    return JsonResponse(data)




def collections_view(request):
    user = request.user
    if user.is_authenticated and (user.is_superuser or user.is_archive_manager()):
        collections = Collection.objects.all().order_by('-created_at')
    else:
        accessible = Q()
        if user.is_authenticated:
            accessible |= Q(access_policy=Collection.ACCESS_PRIVATE, owner=user)
            if user.department:
                accessible |= Q(access_policy=Collection.ACCESS_DEPARTMENT, owner__department=user.department)
            accessible |= Q(access_policy=Collection.ACCESS_AUTHENTICATED)
            accessible |= Q(access_policy=Collection.ACCESS_PUBLIC)
        else:
            accessible = Q(access_policy=Collection.ACCESS_PUBLIC)
        
        collections = Collection.objects.filter(accessible).order_by('-created_at')

    if request.method == 'POST':
        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to create a collection.')
            return redirect('login')
        
        if not (request.user.is_superuser or request.user.is_archive_manager() or request.user.is_contributor()):
            messages.error(request, 'You do not have permission to create collections.')
            return redirect('archive:collections')
        
        form = CollectionForm(request.POST)
        if form.is_valid():
            collection = form.save(commit=False)
            collection.owner = request.user
            collection.save()
            messages.success(request, f'Collection "{collection.name}" created successfully.')
            return redirect('archive:collections')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = CollectionForm()
    
    context = {
        'collections': collections,
        'form': form,
    }
    return render(request, 'archive/collections.html', context)


@admin_or_manager_required
def register_view(request):
    if request.user.is_authenticated:
        return redirect('archive:home')
    
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            try:
                viewer_group = Group.objects.get(name='Viewer')
                user.groups.add(viewer_group)
            except Group.DoesNotExist:
                viewer_group = Group.objects.create(name='Viewer')
                user.groups.add(viewer_group)
            
            # If the user has SystemAdmin group (somehow), set superuser/staff
            if user.groups.filter(name='SystemAdmin').exists():
                user.is_superuser = True
                user.is_staff = True
                user.save()
            
            login(request, user)
            messages.success(request, f'Welcome {user.username}! Your account has been created.')
            return redirect('archive:home')
        else:
            for error in form.errors.values():
                messages.error(request, error)
    else:
        form = CustomUserCreationForm()
    
    return render(request, 'archive/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('archive:home')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if user.must_change_password:
                request.session['force_change_user_id'] = user.id
                return redirect('archive:force_change_password')
            
            existing_active = ActiveSession.objects.filter(
                user=user,
                expires_at__gt=timezone.now()
            ).exists()
            if existing_active:
                messages.error(request, 'You are already logged in on another device. Please log out from that device first, or wait for the session to expire.')
                return redirect('archive:login')
            
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
            
            messages.success(request, f'Welcome back {user.username}!')
            next_url = request.GET.get('next', 'archive:home')
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'archive/login.html')


def customer_autocomplete(request):
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse([], safe=False)
    
    customers = ArchiveItem.objects.filter(
        Q(customer_id__icontains=query) | Q(client_name__icontains=query),
        is_deleted=False
    ).values('customer_id', 'client_name').distinct().order_by('client_name')[:20]
    
    results = [
        {'id': c['customer_id'], 'name': c['client_name']}
        for c in customers
    ]
    return JsonResponse(results, safe=False)



def customer_detail(request, customer_id):
    items = ArchiveItem.objects.filter(
        customer_id=customer_id,
        is_deleted=False
    ).select_related('collection', 'status').prefetch_related(
        'tags', 'files', 'user_folders'                       # ← add user_folders
    ).order_by('-date_of_disbursement', '-created_at')

    user = request.user

    for item in items:
        if user.is_authenticated and (user.is_superuser or user.is_archive_manager() or user.is_system_admin()):
            item.can_view = True
        else:
            col = item.collection
            if col:
                policy = col.access_policy
                if policy == Collection.ACCESS_PUBLIC:
                    item.can_view = True
                elif policy == Collection.ACCESS_AUTHENTICATED and user.is_authenticated:
                    item.can_view = True
                elif policy == Collection.ACCESS_DEPARTMENT and user.is_authenticated and user.department == col.owner.department:
                    item.can_view = True
                elif policy == Collection.ACCESS_PRIVATE:
                    if user.is_authenticated:
                        active_req = AccessRequest.objects.filter(
                            archive_item=item,
                            requester_user=user,
                            status='APPROVED',
                            granted_access_until__gt=timezone.now()
                        ).exists()
                        if active_req or col.owner == user:
                            item.can_view = True
                        else:
                            item.can_view = False
                    else:
                        item.can_view = False
            else:
                item.can_view = (user.is_authenticated and (user.is_superuser or user.is_archive_manager() or user.is_system_admin()))

        if user.is_authenticated:
            item.has_pending_request = AccessRequest.objects.filter(
                archive_item=item,
                requester_user=user,
                status='PENDING'
            ).exists()
        else:
            item.has_pending_request = False

        if user.is_authenticated:
            item.can_modify = user.can_modify_item(item)
        else:
            item.can_modify = False

        if user.is_authenticated and (user.is_superuser or user.is_archive_manager()):
            item.can_manage_folders = True
        else:
            item.can_manage_folders = False

        if user.is_authenticated:
            item.can_add_metadata = (
                not user.is_auditor() and
                (user.is_contributor() or user.is_superuser or user.is_archive_manager())
            )
        else:
            item.can_add_metadata = False

        if request.user.is_authenticated:
            assigned_folders = item.user_folders.filter(user=request.user)
            item.assigned_folder_ids = list(assigned_folders.values_list('id', flat=True))
            item.assigned_folder_names = list(assigned_folders.values_list('name', flat=True))
            item.current_folder_id = item.assigned_folder_ids[0] if item.assigned_folder_ids else None
            item.current_folder_name = item.assigned_folder_names[0] if item.assigned_folder_names else None
        else:
            item.assigned_folder_ids = []
            item.assigned_folder_names = []
            item.current_folder_id = None
            item.current_folder_name = None

    if not items.exists():
        return render(request, 'archive/customer_detail.html', {
            'customer_id': customer_id,
            'no_loans': True,
            'customer_name': None
        })

    customer_name = items[0].client_name

    grouped = defaultdict(list)
    for item in items:
        if item.date_of_disbursement:
            year = item.date_of_disbursement.year
        else:
            year = item.created_at.year
        grouped[year].append(item)
    years_grouped = sorted(grouped.items(), key=lambda x: x[0], reverse=True)

    all_collections = Collection.objects.all().order_by('name')
    workflow_statuses = WorkflowStatus.objects.all()
    user_folders = []
    if request.user.is_authenticated:
        user_folders = list(request.user.folders.all().values('id', 'name', 'parent_id'))
    user_folders_json = json.dumps(user_folders, cls=DjangoJSONEncoder)
    folder_types = FolderType.objects.all().values('id', 'name', 'order')
    # folder_types_list = list(folder_types)
    context = {
        'customer_id': customer_id,
        'customer_name': customer_name,
        'years_grouped': years_grouped,
        'collections': all_collections,
        'workflow_statuses': workflow_statuses,
        'user_folders_json': user_folders_json,
        'folder_types': list(folder_types),
        
    }
    return render(request, 'archive/customer_detail.html', context)






def document_detail(request, item_id):
    item = get_object_or_404(ArchiveItem, id=item_id, is_deleted=False)
    user = request.user

    # Default: no access
    can_view = False

    # Admin override
    if user.is_authenticated and (user.is_superuser or user.is_archive_manager() or getattr(user, 'is_system_admin', lambda: False)()):
        can_view = True
    else:
        col = item.collection
        if col:
            policy = col.access_policy
            if policy == Collection.ACCESS_PUBLIC:
                can_view = True
            elif policy == Collection.ACCESS_AUTHENTICATED and user.is_authenticated:
                can_view = True
            elif policy == Collection.ACCESS_DEPARTMENT and user.is_authenticated and user.department == col.owner.department:
                can_view = True
            elif policy == Collection.ACCESS_PRIVATE and user.is_authenticated:
                active_req = AccessRequest.objects.filter(
                    archive_item=item,
                    requester_user=user,
                    status='APPROVED',
                    granted_access_until__gt=timezone.now()
                ).exists()
                can_view = active_req or (col.owner == user)
        # else: no collection, can_view remains False (already set)

    if not can_view:
        return render(request, 'archive/document_access_denied.html', {'item': item})

    can_modify = user.can_modify_item(item) if user.is_authenticated else False
    can_add_metadata = (user.is_authenticated and not user.is_auditor() and
                        (user.is_contributor() or user.is_superuser or user.is_archive_manager())) if user.is_authenticated else False

    if user.is_authenticated:
        if item.document_type == 'loan':
            can_manage_folders = user.is_superuser or user.is_archive_manager()
        else:
            can_manage_folders = True
    else:
        can_manage_folders = False

    folder_types = FolderType.objects.all().values('id', 'name', 'order')
    context = {
        'item': item,
        'is_loan_document': item.document_type == 'loan',
        'can_modify': can_modify,
        'can_add_metadata': can_add_metadata,
        'can_manage_folders': can_manage_folders,
        'folder_types': list(folder_types),
        'statuses': WorkflowStatus.objects.all(),
        'collections': Collection.objects.all(),
    }
    return render(request, 'archive/document_detail.html', context)




@login_required
def logout_view(request):
    token = request.session.get('active_session_token')
    if token:
        ActiveSession.objects.filter(user=request.user, session_token=token).delete()
    logout(request)
    messages.info(request, 'You have been logged out.')
    return redirect('archive:login')




from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.http import JsonResponse, HttpResponseForbidden
# @admin_or_manager_required

@xframe_options_sameorigin
def download_file(request, asset_uuid):
    is_download = request.GET.get('download') == 'true'
    
    if is_download:
        if not request.user.is_authenticated:
            return HttpResponseForbidden("Please login to download files.")
        
        user_roles = set(request.user.groups.values_list('name', flat=True))
        allowed_roles = {'SystemAdmin', 'ArchiveManager'}
        
        if not (request.user.is_superuser or bool(user_roles & allowed_roles)):
            return HttpResponseForbidden("Access Denied. Only System Administrators and Archive Managers can download files.")
    
    try:
        file_asset = FileAsset.objects.get(asset_uuid=asset_uuid)
        if default_storage.exists(file_asset.s3_key):
            file_handle = default_storage.open(file_asset.s3_key, 'rb')
            mime_type = file_asset.mime_type or 'application/octet-stream'
            response = FileResponse(file_handle, content_type=mime_type)
            
            if is_download:
                response['Content-Disposition'] = f'attachment; filename="{file_asset.file_name}"'
            else:
                response['Content-Disposition'] = f'inline; filename="{file_asset.file_name}"'
            return response
    except FileAsset.DoesNotExist:
        pass

    try:
        item_file = ItemFile.objects.get(asset_uuid=asset_uuid)
        if hasattr(item_file, 'stored_path') and item_file.stored_path:
            file_path = item_file.stored_path
        else:
            file_path = f'item_folders/{asset_uuid}/{item_file.file_name}'
        
        if default_storage.exists(file_path):
            file_handle = default_storage.open(file_path, 'rb')
            response = FileResponse(file_handle, content_type=item_file.mime_type)
            
            if is_download:
                response['Content-Disposition'] = f'attachment; filename="{item_file.file_name}"'
            else:
                response['Content-Disposition'] = f'inline; filename="{item_file.file_name}"'
            return response
    except ItemFile.DoesNotExist:
        pass

    try:
        doc_file = DocumentFile.objects.get(asset_uuid=asset_uuid)
        if hasattr(doc_file, 'stored_path') and doc_file.stored_path:
            file_path = doc_file.stored_path
        else:
            file_path = f'document_folders/{asset_uuid}/{doc_file.file_name}'
        
        if default_storage.exists(file_path):
            file_handle = default_storage.open(file_path, 'rb')
            response = FileResponse(file_handle, content_type=doc_file.mime_type)
            
            if is_download:
                response['Content-Disposition'] = f'attachment; filename="{doc_file.file_name}"'
            else:
                response['Content-Disposition'] = f'inline; filename="{doc_file.file_name}"'
            return response
    except DocumentFile.DoesNotExist:
        pass

    raise Http404("File not found")





def can_edit_collection(user, collection):
    """Check if user can edit/delete this collection"""
    return (user.is_superuser or 
            user.groups.filter(name='ArchiveManager').exists() or 
            collection.owner == user)

@login_required
@require_http_methods(['POST'])
def edit_collection(request, pk):
    collection = get_object_or_404(Collection, pk=pk)
    if not can_edit_collection(request.user, collection):
        messages.error(request, "You don't have permission to edit this collection.")
        return redirect('archive:collections')
    
    if request.POST.get('_method') == 'PUT':
        name = request.POST.get('name')
        description = request.POST.get('description')
        access_policy = request.POST.get('access_policy')
        
        if name:
            collection.name = name
        collection.description = description
        collection.access_policy = access_policy
        collection.save()
        messages.success(request, f'Collection "{collection.name}" updated.')
    else:
        pass
    
    return redirect('archive:collections')

@admin_or_manager_required
@login_required
@require_http_methods(['POST'])
def delete_collection(request, pk):
    collection = get_object_or_404(Collection, pk=pk)
    if not can_edit_collection(request.user, collection):
        messages.error(request, "You don't have permission to delete this collection.")
        return redirect('archive:collections')
    
    if request.POST.get('_method') == 'DELETE':
        name = collection.name
        collection.delete()
        messages.success(request, f'Collection "{name}" deleted.')
    
    return redirect('archive:collections')






def filter_accessible_items(request, queryset):
    """Return a queryset of ArchiveItems the current user can view."""
    user = request.user
    if user.is_authenticated and (user.is_superuser or user.is_archive_manager()):
        return queryset 

    if not user.is_authenticated:
        return queryset.filter(collection__access_policy=Collection.ACCESS_PUBLIC)

    accessible_collections = Q()
    accessible_collections |= Q(collection__owner=user, collection__access_policy=Collection.ACCESS_PRIVATE)
    if user.department:
        accessible_collections |= Q(
            collection__access_policy=Collection.ACCESS_DEPARTMENT,
            collection__owner__department=user.department
        )
    accessible_collections |= Q(collection__access_policy=Collection.ACCESS_AUTHENTICATED)
    accessible_collections |= Q(collection__access_policy=Collection.ACCESS_PUBLIC)

    return queryset.filter(accessible_collections)





@login_required
def audit_logs_view(request):
    if not (request.user.is_superuser or request.user.is_archive_manager() or request.user.is_auditor()):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You do not have permission to view audit logs.")

    logs = AuditLog.objects.select_related('user', 'archive_item').order_by('-timestamp')

    user_id = request.GET.get('user')
    if user_id:
        logs = logs.filter(user_id=user_id)

    action = request.GET.get('action')
    if action:
        logs = logs.filter(action=action)

    item_id = request.GET.get('item')
    if item_id:
        logs = logs.filter(archive_item_id=item_id)

    date_from = request.GET.get('date_from')
    if date_from:
        logs = logs.filter(timestamp__date__gte=date_from)

    date_to = request.GET.get('date_to')
    if date_to:
        logs = logs.filter(timestamp__date__lte=date_to)

    paginator = Paginator(logs, 10)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    users = User.objects.filter(audit_logs__isnull=False).distinct()
    actions = [choice[0] for choice in AuditLog.ACTION_CHOICES]

    context = {
        'page_obj': page_obj,
        'users': users,
        'actions': actions,
        'selected_user': user_id,
        'selected_action': action,
        'selected_item': item_id,
        'date_from': date_from,
        'date_to': date_to,
    }
    return render(request, 'archive/audit_logs.html', context)





@login_required
def request_access(request, item_id):
    """User requests access to a specific archive item."""
    item = get_object_or_404(ArchiveItem, id=item_id, is_deleted=False)
    
    if request.user.can_view_item(item):
        messages.info(request, 'You already have access to this item.')
        return redirect('archive:customer_detail', customer_id=item.customer_id)
    
    existing = AccessRequest.objects.filter(
        requester_user=request.user,
        archive_item=item,
        status=AccessRequest.STATUS_PENDING
    ).first()
    if existing:
        messages.warning(request, 'You already have a pending request for this item.')
        return redirect('archive:my_access_requests')
    
    if request.method == 'POST':
        form = AccessRequestForm(request.POST)
        if form.is_valid():
            access_req = form.save(commit=False)
            access_req.requester_user = request.user
            access_req.archive_item = item
            access_req.save()
            messages.success(request, 'Your access request has been submitted for review.')
            return redirect('archive:my_access_requests')
    else:
        form = AccessRequestForm(initial={'granted_access_until': timezone.now() + timezone.timedelta(days=7)})
    
    return render(request, 'archive/request_access.html', {'form': form, 'item': item})




@login_required
def my_access_requests(request):
    requests = AccessRequest.objects.filter(requester_user=request.user).order_by('-approved_at', '-id')
    # Convert datetime fields to local time for display
    for req in requests:
        req.created_at_local = timezone.localtime(req.created_at)
        if req.approved_at:
            req.approved_at_local = timezone.localtime(req.approved_at)
        if req.granted_access_until:
            req.granted_access_until_local = timezone.localtime(req.granted_access_until)
    return render(request, 'archive/my_access_requests.html', {'requests': requests})


@login_required
def pending_access_requests(request):
    """Show all pending requests for items the user can approve (owner of collection or manager)."""
    user = request.user

    if not (user.is_archive_manager() or user.is_superuser):
        owned_collections = user.managed_collections()
        pending = AccessRequest.objects.filter(
            archive_item__collection__in=owned_collections,
            status=AccessRequest.STATUS_PENDING
        ).select_related('archive_item', 'requester_user').order_by('created_at')
    else:
        pending = AccessRequest.objects.filter(
            status=AccessRequest.STATUS_PENDING
        ).select_related('archive_item', 'requester_user')

    context = {
        'pending_requests': pending,
        'now': timezone.now(),
        'max_expiry': timezone.now() + timezone.timedelta(days=30),
    }
    return render(request, 'archive/pending_requests.html', context)


@admin_or_manager_required
@login_required
@require_POST
def approve_access_request(request, request_id):
    access_req = get_object_or_404(AccessRequest, id=request_id)
    item = access_req.archive_item

    if not (request.user.is_system_admin() or request.user.is_archive_manager() or
            (item.collection and item.collection.owner == request.user)):
        messages.error(request, 'You do not have permission to approve this request.')
        return redirect('archive:pending_access_requests')

    if access_req.status != AccessRequest.STATUS_PENDING:
        messages.error(request, 'This request has already been processed.')
        return redirect('archive:pending_access_requests')

    new_expiry_str = request.POST.get('expiry', '').strip()
    if new_expiry_str:
        try:
            new_expiry = timezone.datetime.strptime(new_expiry_str, '%Y-%m-%dT%H:%M')
            new_expiry = timezone.make_aware(new_expiry)
        except ValueError:
            messages.error(request, 'Invalid expiry date format.')
            return redirect('archive:pending_access_requests')

        if new_expiry <= timezone.now():
            messages.error(request, 'Expiration date must be in the future.')
            return redirect('archive:pending_access_requests')
        if new_expiry > timezone.now() + timezone.timedelta(days=30):
            messages.error(request, 'Access cannot be granted for more than 30 days.')
            return redirect('archive:pending_access_requests')

        access_req.granted_access_until = new_expiry
    else:
        if access_req.granted_access_until <= timezone.now():
            messages.error(request, 'Cannot approve: the requested access period has already ended. Please set a new expiry date.')
            return redirect('archive:pending_access_requests')

    # Apply approval
    access_req.status = AccessRequest.STATUS_APPROVED
    access_req.approver = request.user
    access_req.approved_at = timezone.now()
    access_req.save()

    messages.success(request, f'Access granted to {access_req.requester_user.username} until {access_req.granted_access_until}.')
    return redirect('archive:pending_access_requests')



@login_required
@admin_or_manager_required
@require_POST
def deny_access_request(request, request_id):
    access_req = get_object_or_404(AccessRequest, id=request_id)
    item = access_req.archive_item
    if not (request.user.is_system_admin() or request.user.is_archive_manager() or 
            (item.collection and item.collection.owner == request.user)):
        messages.error(request, 'You do not have permission to deny this request.')
        return redirect('archive:pending_access_requests')
    
    access_req.status = AccessRequest.STATUS_DENIED
    access_req.approver = request.user
    access_req.approved_at = timezone.now()
    access_req.save()
    messages.success(request, f'Access request from {access_req.requester_user.username} denied.')
    return redirect('archive:pending_access_requests')



@login_required
def my_folders(request):
    folders = UserFolder.objects.filter(user=request.user, parent__isnull=True).prefetch_related('subfolders')
    return render(request, 'archive/folders.html', {'folders': folders})


@login_required
def create_folder(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        parent_id = request.POST.get('parent')
        parent = UserFolder.objects.filter(id=parent_id, user=request.user).first() if parent_id else None
        folder = UserFolder.objects.create(user=request.user, name=name, parent=parent)

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'id': folder.id,
                'name': folder.name,
                'parent_id': folder.parent_id,
            })
    return redirect('archive:my_folders')



@login_required

def assign_to_folder(request, item_id):
    item = get_object_or_404(ArchiveItem, id=item_id)
    if request.method == 'POST':
        folder_id = request.POST.get('folder')
        if folder_id:
            folder = get_object_or_404(UserFolder, id=folder_id, user=request.user)
            item.user_folders.add(folder)
        else:
            item.user_folders.clear()
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'status': 'ok'})
    return redirect('archive:customer_detail', customer_id=item.customer_id)



from django.views.decorators.csrf import ensure_csrf_cookie

@login_required
@require_http_methods(['POST'])
@ensure_csrf_cookie
def extend_session(request):
    # Reset the session expiry time
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)  
    return JsonResponse({'success': True})


def search_ocr(request):
    keyword = request.GET.get('q', '')
    results = DocumentText.objects.filter(extracted_text__icontains=keyword)
    return render(request, 'archive/search_results.html', {'results': results, 'keyword': keyword})

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

def get_related_items(request, item_id):
    try:
        item = ArchiveItem.objects.get(id=item_id)
    except ArchiveItem.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    if not request.user.can_view_item(item):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    outgoing = RelatedItem.objects.filter(from_item=item).select_related('to_item')
    incoming = RelatedItem.objects.filter(to_item=item).select_related('from_item')

    relations = []
    for rel in outgoing:
        relations.append({
            'id': rel.id,
            'direction': 'out',
            'other_item_id': rel.to_item.id,
            'other_title': rel.to_item.title,
            'other_loan_id': rel.to_item.loan_id,
            'other_client': rel.to_item.client_name,
            'relation_type': rel.get_relation_type_display(),
            'type_key': rel.relation_type,
        })
    for rel in incoming:
        relations.append({
            'id': rel.id,
            'direction': 'in',
            'other_item_id': rel.from_item.id,
            'other_title': rel.from_item.title,
            'other_loan_id': rel.from_item.loan_id,
            'other_client': rel.from_item.client_name,
            'relation_type': rel.get_relation_type_display(),
            'type_key': rel.relation_type,
        })
    return JsonResponse({'relations': relations})

def get_relationship_graph(request, item_id):
    from collections import deque
    try:
        root = ArchiveItem.objects.get(id=item_id)
    except ArchiveItem.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    if not request.user.can_view_item(root):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    visited = set()
    nodes = []
    edges = []
    queue = deque([(root, 0)])
    visited.add(root.id)

    while queue:
        current, depth = queue.popleft()
        nodes.append({
            'id': current.id,
            'label': f"{current.client_name}\n{current.loan_id}",
            'title': current.title,
            'loan_id': current.loan_id,
            'client_name': current.client_name,
        })
        if depth >= 2:
            continue

        for rel in RelatedItem.objects.filter(from_item=current).select_related('to_item'):
            other = rel.to_item
            if other.id not in visited:
                visited.add(other.id)
                queue.append((other, depth+1))
            edges.append({
                'source': current.id,
                'target': other.id,
                'label': rel.get_relation_type_display(),
                'type': rel.relation_type,
            })
        for rel in RelatedItem.objects.filter(to_item=current).select_related('from_item'):
            other = rel.from_item
            if other.id not in visited:
                visited.add(other.id)
                queue.append((other, depth+1))
            edges.append({
                'source': other.id,
                'target': current.id,
                'label': rel.get_relation_type_display(),
                'type': rel.relation_type,
            })

    return JsonResponse({'nodes': nodes, 'edges': edges})



















from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from .models import ArchiveItem, AuditLog

@login_required
@csrf_exempt
def log_item_view(request, item_id):
    """Log when a user views a loan document."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        item = ArchiveItem.objects.get(id=item_id, is_deleted=False)
    except ArchiveItem.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)
    
    if not request.user.can_view_item(item):
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    AuditLog.objects.create(
        user=request.user,
        archive_item=item,
        action=AuditLog.ACTION_VIEW,
        new_value={
            'item_id': item.id,
            'item_title': item.title,
            'item_type': item.document_type,
            'viewed_at': timezone.now().isoformat()
        },
        ip_address=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', '')
    )
    
    return JsonResponse({'status': 'ok'})