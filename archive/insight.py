from django.db.models import Count, Sum, Q
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render
from .models import ArchiveItem, Collection, AccessRequest, AuditLog, User, FileAsset, ItemFile

def insights_accessible(user):
    return user.is_authenticated and (user.is_superuser or user.is_archive_manager() or user.is_auditor())

@login_required
@user_passes_test(insights_accessible, login_url='archive:home')
def insights_dashboard(request):
    today = timezone.now().date()
    thirty_days_ago = today - timedelta(days=30)

    total_loans = ArchiveItem.objects.filter(is_deleted=False).count()
    total_collections = Collection.objects.count()
    total_customers = ArchiveItem.objects.filter(is_deleted=False).values('customer_id').distinct().count()

    loans_by_collection = (
        Collection.objects.annotate(loan_count=Count('items', filter=Q(items__is_deleted=False)))
        .values('name', 'loan_count')
        .order_by('-loan_count')[:10]
    )

    loans_by_workflow_status = (
        ArchiveItem.objects.filter(is_deleted=False)
        .values('status__name')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    loans_by_loan_status = (
        ArchiveItem.objects.filter(is_deleted=False)
        .values('loan_status')
        .annotate(count=Count('id'))
    )

    top_customers_by_loans = (
        ArchiveItem.objects.filter(is_deleted=False)
        .values('customer_id', 'client_name')
        .annotate(loan_count=Count('id'))
        .order_by('-loan_count')[:10]
    )

    loans_per_year = {}
    for item in ArchiveItem.objects.filter(is_deleted=False):
        year = item.date_of_disbursement.year if item.date_of_disbursement else item.created_at.year
        loans_per_year[year] = loans_per_year.get(year, 0) + 1
    loans_per_year_list = [{'year': y, 'count': c} for y, c in sorted(loans_per_year.items())]

    loans_without_metadata = ArchiveItem.objects.filter(custom_metadata__isnull=True, is_deleted=False).count()


    total_documents = FileAsset.objects.count() + ItemFile.objects.count()

    total_storage_asset = FileAsset.objects.aggregate(total=Sum('file_size'))['total'] or 0
    total_storage_itemfile = ItemFile.objects.aggregate(total=Sum('file_size'))['total'] or 0
    total_storage = total_storage_asset + total_storage_itemfile

    total_files_asset = FileAsset.objects.count()
    total_files_itemfile = ItemFile.objects.count()
    total_files = total_files_asset + total_files_itemfile
    avg_file_size = total_storage / total_files if total_files > 0 else 0


    pending_requests = AccessRequest.objects.filter(status='PENDING').count()
    approved_requests = AccessRequest.objects.filter(status='APPROVED').count()
    denied_requests = AccessRequest.objects.filter(status='DENIED').count()
    expired_requests = AccessRequest.objects.filter(status='APPROVED', granted_access_until__lt=timezone.now()).count()

    most_requested_loans = (
        AccessRequest.objects.values('archive_item__title', 'archive_item__id')
        .annotate(request_count=Count('id'))
        .order_by('-request_count')[:5]
    )

    audit_actions = AuditLog.objects.filter(timestamp__date__gte=thirty_days_ago) \
        .values('action') \
        .annotate(count=Count('id'))
    audit_labels = [a['action'] for a in audit_actions]
    audit_counts = [a['count'] for a in audit_actions]

    active_users_last_30d = User.objects.filter(last_login__date__gte=thirty_days_ago).count()
    total_active_users = User.objects.filter(is_active=True).count()
    inactive_users_30d = total_active_users - active_users_last_30d

    def human_readable_size(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    context = {
        'total_loans': total_loans,
        'total_collections': total_collections,
        'total_customers': total_customers,
        'loans_by_collection': list(loans_by_collection),
        'loans_by_workflow_status': list(loans_by_workflow_status),
        'loans_by_loan_status': list(loans_by_loan_status),
        'top_customers_by_loans': list(top_customers_by_loans),
        'loans_per_year': loans_per_year_list,
        'loans_without_metadata': loans_without_metadata,
        'total_documents': total_documents,
        'total_storage_human': human_readable_size(total_storage),
        'total_files': total_files,
        'avg_file_size_human': human_readable_size(avg_file_size),
        'pending_requests': pending_requests,
        'approved_requests': approved_requests,
        'denied_requests': denied_requests,
        'expired_requests': expired_requests,
        'most_requested_loans': list(most_requested_loans),
        'audit_labels': audit_labels,
        'audit_counts': audit_counts,
        'active_users_last_30d': active_users_last_30d,
        'total_active_users': total_active_users,
        'inactive_users_30d': inactive_users_30d,
    }
    return render(request, 'archive/insights.html', context)