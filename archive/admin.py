# archive/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    DocumentText,
    User,
    WorkflowStatus,
    Collection,
    ArchiveItem,
    FileAsset,
    MetadataField,
    Tag,
    AuditLog,
    ActiveSession,
    AccessRequest,
    UserFolder,
    FolderType,
    ItemFolder,
    ItemFile,
    SharedFolder,
    SharedFolderFile,
    SharedFolderAccess,
)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'department', 'is_active', 'is_staff', 'security_clearance_level')
    list_filter = ('is_active', 'is_staff', 'department', 'security_clearance_level')
    search_fields = ('username', 'email', 'first_name', 'last_name', 'department')
    fieldsets = UserAdmin.fieldsets + (
        ('Extra Info', {'fields': ('department', 'security_clearance_level', 'mfa_enabled', 'last_login_ip', 'password_updated_at', 'must_change_password', 'user_uuid')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Extra Info', {'fields': ('department', 'security_clearance_level')}),
    )
    readonly_fields = ('user_uuid', 'last_login_ip', 'password_updated_at')


@admin.register(WorkflowStatus)
class WorkflowStatusAdmin(admin.ModelAdmin):
    list_display = ('name', 'requires_approval')
    search_fields = ('name',)


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'access_policy', 'target_department', 'created_at')
    list_filter = ('access_policy', 'owner')
    search_fields = ('name', 'description', 'owner__username', 'target_department')
    raw_id_fields = ('owner',)
    date_hierarchy = 'created_at'


@admin.register(ArchiveItem)
class ArchiveItemAdmin(admin.ModelAdmin):
    list_display = ('title', 'customer_id', 'client_name', 'loan_id', 'collection', 'status', 'loan_status', 'created_at')
    list_filter = ('collection', 'status', 'loan_status', 'is_deleted', 'created_at')
    search_fields = ('title', 'customer_id', 'client_name', 'loan_id', 'description')
    raw_id_fields = ('collection', 'created_by', 'last_modified_by')
    readonly_fields = ('item_uuid', 'created_at', 'last_modified_at', 'integrity_hash')
    date_hierarchy = 'created_at'
    fieldsets = (
        (None, {'fields': ('title', 'description', 'collection')}),
        ('Customer / Loan', {'fields': ('client_name', 'customer_id', 'loan_id', 'period', 'product_type', 'date_of_disbursement', 'loan_status')}),
        ('Workflow', {'fields': ('status', 'is_deleted', 'deleted_at')}),
        ('Tracking', {'fields': ('created_by', 'created_at', 'last_modified_by', 'last_modified_at', 'item_uuid', 'integrity_hash', 'encryption_key_id', 'retention_until')}),
        ('Folders', {'fields': ('user_folders',)}),
    )
    filter_horizontal = ('user_folders', 'tags')


@admin.register(FileAsset)
class FileAssetAdmin(admin.ModelAdmin):
    list_display = ('file_name', 'archive_item', 'file_size', 'mime_type', 'virus_scan_status', 'uploaded_at')
    list_filter = ('virus_scan_status', 'encrypted', 'uploaded_at')
    search_fields = ('file_name', 'archive_item__title', 's3_key')
    raw_id_fields = ('archive_item', 'uploaded_by')
    readonly_fields = ('asset_uuid', 'hash_sha256')


@admin.register(MetadataField)
class MetadataFieldAdmin(admin.ModelAdmin):
    list_display = ('field_name', 'archive_item', 'field_type', 'field_value')
    list_filter = ('field_type',)
    search_fields = ('field_name', 'field_value', 'archive_item__title')
    raw_id_fields = ('archive_item',)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'user', 'archive_item', 'timestamp', 'ip_address')
    list_filter = ('action', 'timestamp')
    search_fields = ('user__username', 'archive_item__title', 'ip_address')
    raw_id_fields = ('user', 'archive_item')
    date_hierarchy = 'timestamp'
    readonly_fields = ('log_uuid', 'old_value', 'new_value')


@admin.register(ActiveSession)
class ActiveSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'session_token', 'expires_at', 'last_activity', 'mfa_verified')
    list_filter = ('mfa_verified',)
    search_fields = ('user__username', 'session_token')
    raw_id_fields = ('user',)


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = ('requester_user', 'archive_item', 'status', 'created_at', 'granted_access_until')
    list_filter = ('status', 'created_at')
    search_fields = ('requester_user__username', 'archive_item__title', 'reason')
    raw_id_fields = ('requester_user', 'archive_item', 'approver')
    date_hierarchy = 'created_at'


@admin.register(UserFolder)
class UserFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'parent', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('name', 'user__username')
    raw_id_fields = ('user', 'parent')
    list_select_related = ('user', 'parent')


@admin.register(FolderType)
class FolderTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'order', 'description')   # 'name' is first → becomes the link
    list_editable = ('order',)                        # 'order' is editable, fine now
    search_fields = ('name',)


@admin.register(ItemFolder)
class ItemFolderAdmin(admin.ModelAdmin):
    list_display = ('archive_item', 'user', 'folder_type', 'created_at')
    list_filter = ('folder_type', 'created_at')
    search_fields = ('archive_item__title', 'user__username', 'folder_type__name')
    raw_id_fields = ('archive_item', 'user', 'folder_type')
    list_select_related = ('archive_item', 'user', 'folder_type')


@admin.register(ItemFile)
class ItemFileAdmin(admin.ModelAdmin):
    list_display = ('file_name', 'folder', 'file_size', 'uploaded_at')
    list_filter = ('uploaded_at',)
    search_fields = ('file_name', 'folder__archive_item__title')
    raw_id_fields = ('folder',)
    readonly_fields = ('asset_uuid',)
    list_select_related = ('folder',)


@admin.register(SharedFolder)
class SharedFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'created_at', 'is_active')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description', 'created_by__username')
    raw_id_fields = ('created_by',)


@admin.register(SharedFolderFile)
class SharedFolderFileAdmin(admin.ModelAdmin):
    list_display = ('shared_folder', 'item_file', 'added_at')
    list_filter = ('added_at',)
    search_fields = ('shared_folder__name', 'item_file__file_name')
    raw_id_fields = ('shared_folder', 'item_file')


@admin.register(SharedFolderAccess)
class SharedFolderAccessAdmin(admin.ModelAdmin):
    list_display = ('shared_folder', 'user', 'granted_by', 'granted_at')
    list_filter = ('granted_at',)
    search_fields = ('shared_folder__name', 'user__username', 'granted_by__username')
    raw_id_fields = ('shared_folder', 'user', 'granted_by')



@admin.register(DocumentText)
class DocumentTextAdmin(admin.ModelAdmin):
    list_display = ('id', 'item_file', 'indexed_at')
    search_fields = ('extracted_text',)
    readonly_fields = ('indexed_at',)