# models.py
import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.urls import reverse
from django.utils import timezone
from django.contrib.postgres.search import SearchVectorField
from django.contrib.postgres.indexes import GinIndex
from dateutil.relativedelta import relativedelta

from dam import settings

class User(AbstractUser):
    user_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    department = models.CharField(max_length=100, blank=True)
    security_clearance_level = models.SmallIntegerField(default=1)  # 1..5
    mfa_enabled = models.BooleanField(default=False)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    password_updated_at = models.DateTimeField(auto_now_add=True)
    must_change_password = models.BooleanField(default=False)
    groups = models.ManyToManyField(
        'auth.Group',
        related_name='archive_user_set',
        blank=True,
        help_text='The groups this user belongs to.',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='archive_user_set',
        blank=True,
        help_text='Specific permissions for this user.',
        verbose_name='user permissions',
    )

    REQUIRED_FIELDS = ['email']

    def __str__(self):
        return f"{self.username} ({self.department})"

    def is_system_admin(self):
        return self.is_superuser or self.groups.filter(name='SystemAdmin').exists()

    def is_archive_manager(self):
        """ Manager with approval rights and sensitive metadata access """
        return (self.groups.filter(name='ArchiveManager').exists() or
                self.has_perm('archive.can_approve_items'))

    def is_collection_owner(self, collection):
        """ Check if user owns a specific collection """
        return collection.owner == self

    def is_contributor(self):
        """ Contributor can create and edit own items within allowed collections """
        return (self.groups.filter(name='Contributor').exists() or
                self.security_clearance_level >= 2)

    def is_viewer(self):
        """ Basic viewer (any authenticated user) """
        return self.is_authenticated

    def is_auditor(self):
        return self.groups.filter(name='Auditor').exists()

    def can_approve_items(self):
        return self.has_perm('archive.can_approve_items')

    def can_view_sensitive_metadata(self):
        return self.has_perm('archive.can_view_sensitive_metadata')

    def can_audit_all(self):
        return self.has_perm('archive.can_audit_all') or self.is_auditor()

    def meets_clearance(self, required_level):
        """ Return True if user's clearance >= required_level """
        return self.security_clearance_level >= required_level

    def can_view_item(self, item):
        """ Check if user can view an ArchiveItem based on collection policy and clearance """
        collection = item.collection
        if not collection:
            return self.is_system_admin() or self.is_archive_manager()

        policy = collection.access_policy
        if policy == Collection.ACCESS_PUBLIC:
            return True
        if policy == Collection.ACCESS_AUTHENTICATED:
            return self.is_authenticated
        if policy == Collection.ACCESS_DEPARTMENT:
            return self.is_authenticated and self.department == collection.owner.department
        if policy == Collection.ACCESS_PRIVATE:
            active_request = item.access_requests.filter(
                requester_user=self,
                status=AccessRequest.STATUS_APPROVED,
                granted_access_until__gt=timezone.now()
            ).exists()
            if active_request:
                return True
            return self == collection.owner or self.is_system_admin() or self.is_archive_manager()
        return False

    def can_modify_item(self, item):
        """ Check if user can edit/update an archive item """
        if self.is_system_admin() or self.is_archive_manager():
            return True
        if item.collection and item.collection.owner == self:
            return True
        if self.is_contributor() and item.created_by == self:
            return True
        return False

    def can_delete_item(self, item):
        """ Soft-delete permission – only admins, managers, and collection owners """
        return (self.is_system_admin() or self.is_archive_manager() or
                (item.collection and item.collection.owner == self))

    def managed_collections(self):
        """ Collections where the user is owner or admin/manager """
        if self.is_system_admin() or self.is_archive_manager():
            return Collection.objects.all()
        return Collection.objects.filter(owner=self)

    def get_active_sessions(self):
        from .models import ActiveSession
        return self.active_sessions.filter(expires_at__gt=timezone.now())


class WorkflowStatus(models.Model):
    name = models.CharField(max_length=50, unique=True)
    requires_approval = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Collection(models.Model):
    ACCESS_PRIVATE = 1
    ACCESS_DEPARTMENT = 2
    ACCESS_AUTHENTICATED = 3
    ACCESS_PUBLIC = 4
    ACCESS_CHOICES = [
        (ACCESS_PRIVATE, 'Private'),
        (ACCESS_DEPARTMENT, 'Department'),
        (ACCESS_AUTHENTICATED, 'Authenticated Users'),
        (ACCESS_PUBLIC, 'Public'),
    ]

    collection_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.PROTECT, related_name='owned_collections')
    access_policy = models.SmallIntegerField(choices=ACCESS_CHOICES, default=ACCESS_PRIVATE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    target_department = models.CharField(max_length=100, blank=True, help_text="Department that can view this collection (if access_policy = Department)")
    class Meta:
        indexes = [models.Index(fields=['access_policy'])]



    def save(self, *args, **kwargs):
        if self.access_policy == self.ACCESS_DEPARTMENT:
            self.target_department = self.owner.department
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name




class ArchiveItem(models.Model):
    DOCUMENT_TYPE_LOAN = 'loan'
    DOCUMENT_TYPE_DEPARTMENT = 'department'
    DOCUMENT_TYPE_PUBLIC = 'public'
    DOCUMENT_TYPE_CHOICES = [
        (DOCUMENT_TYPE_LOAN, 'Loan Document'),
        (DOCUMENT_TYPE_DEPARTMENT, 'Department Document'),
        (DOCUMENT_TYPE_PUBLIC, 'Public Document'),
    ]

    LOAN_STATUS_ACTIVE = 'active'
    LOAN_STATUS_CLOSED = 'closed'
    LOAN_STATUS_DEFAULTED = 'defaulted'
    LOAN_STATUS_CHOICES = [
        (LOAN_STATUS_ACTIVE, 'Active'),
        (LOAN_STATUS_CLOSED, 'Closed'),
        (LOAN_STATUS_DEFAULTED, 'Defaulted'),
    ]

    item_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    collection = models.ForeignKey(Collection, on_delete=models.SET_NULL, null=True, blank=True, related_name='items')
    
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPE_CHOICES, default=DOCUMENT_TYPE_LOAN)

    client_name = models.CharField(max_length=200, blank=True, null=True, db_index=True, help_text="Name of the customer/borrower (only for loan documents)")
    customer_id = models.CharField(max_length=50, blank=True, null=True, db_index=True, help_text="Unique customer identifier (only for loan documents)")
    loan_id = models.CharField(max_length=50, blank=True, null=True, db_index=True, help_text="Unique loan identifier (only for loan documents)")
    period = models.CharField(max_length=100, blank=True, help_text="e.g., 'Annual Review 2024', 'Q1 2025'")
    product_type = models.CharField(max_length=100, blank=True, help_text="Type of loan (e.g., Mortgage, Personal, Business)")
    date_of_disbursement = models.DateField(null=True, blank=True, help_text="Date the loan was disbursed")
    loan_status = models.CharField(
        max_length=20,
        choices=LOAN_STATUS_CHOICES,
        default=LOAN_STATUS_ACTIVE,
        db_index=True,
        help_text="Current status of the loan"
    )
    
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_items', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_modified_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='modified_items', null=True, blank=True)
    last_modified_at = models.DateTimeField(auto_now=True)
    
    status = models.ForeignKey(WorkflowStatus, on_delete=models.PROTECT, default=1)

    retention_until = models.DateField(null=True, blank=True)
    encryption_key_id = models.CharField(max_length=100, blank=True, help_text="Reference to HSM/KMS key")
    integrity_hash = models.CharField(max_length=128, blank=True, help_text="SHA‑256 of all file hashes")
    
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    user_folders = models.ManyToManyField('UserFolder', blank=True, related_name='items')

    class Meta:
        indexes = [
            models.Index(fields=['collection', 'status']),
            models.Index(fields=['status', 'retention_until']),
            models.Index(fields=['is_deleted', 'deleted_at']),
            models.Index(fields=['client_name']),
            models.Index(fields=['customer_id']),
            models.Index(fields=['loan_id']),
            models.Index(fields=['loan_status']),
            models.Index(fields=['document_type']),  # new
        ]
        permissions = [
            ('can_approve_items', 'Can approve archive items for publishing'),
            ('can_view_sensitive_metadata', 'Can view encrypted/restricted metadata'),
            ('can_change_loan_status', 'Can update the loan status (active/closed/defaulted)'),
        ]

    def __str__(self):
        if self.document_type == self.DOCUMENT_TYPE_LOAN:
            return f"{self.client_name or 'Unknown'} – {self.loan_id or 'No loan'} ({self.title})"
        else:
            return f"{self.get_document_type_display()}: {self.title}"

    @property
    def loan_age_years_months(self):
        if not self.date_of_disbursement:
            return "Not disbursed"
        from dateutil.relativedelta import relativedelta
        delta = relativedelta(timezone.now().date(), self.date_of_disbursement)
        years = delta.years
        months = delta.months
        if years == 0 and months == 0:
            return "Less than a month"
        parts = []
        if years:
            parts.append(f"{years} year{'s' if years != 1 else ''}")
        if months:
            parts.append(f"{months} month{'s' if months != 1 else ''}")
        return ", ".join(parts)

    @property
    def loan_age_months_total(self):
        if not self.date_of_disbursement:
            return None
        from dateutil.relativedelta import relativedelta
        delta = relativedelta(timezone.now().date(), self.date_of_disbursement)
        return delta.years * 12 + delta.months


class FileAsset(models.Model):
    VIRUS_PENDING = 0
    VIRUS_CLEAN = 1
    VIRUS_INFECTED = 2
    VIRUS_CHOICES = [
        (VIRUS_PENDING, 'Pending'),
        (VIRUS_CLEAN, 'Clean'),
        (VIRUS_INFECTED, 'Infected'),
    ]

    asset_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    archive_item = models.ForeignKey(ArchiveItem, on_delete=models.CASCADE, related_name='files')
    file_name = models.CharField(max_length=255)
    s3_key = models.CharField(max_length=500, help_text="Path in secure object storage")
    mime_type = models.CharField(max_length=100)
    file_size = models.BigIntegerField()
    hash_sha256 = models.CharField(max_length=64, db_index=True, help_text="SHA‑256 for integrity")
    encrypted = models.BooleanField(default=True)
    thumbnail_s3_key = models.CharField(max_length=500, blank=True, help_text="Pre‑generated thumbnail")
    
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='uploads')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    virus_scan_status = models.SmallIntegerField(choices=VIRUS_CHOICES, default=VIRUS_PENDING)
    virus_scan_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['archive_item', 'virus_scan_status']),
            models.Index(fields=['hash_sha256']),
        ]

    def __str__(self):
        return f"{self.file_name} ({self.archive_item.title})"


class MetadataField(models.Model):
    FIELD_TYPE_STRING = 'string'
    FIELD_TYPE_INTEGER = 'integer'
    FIELD_TYPE_DATE = 'date'
    FIELD_TYPE_JSON = 'json'
    FIELD_TYPE_ENCRYPTED = 'encrypted'
    TYPE_CHOICES = [
        (FIELD_TYPE_STRING, 'String'),
        (FIELD_TYPE_INTEGER, 'Integer'),
        (FIELD_TYPE_DATE, 'Date'),
        (FIELD_TYPE_JSON, 'JSON'),
        (FIELD_TYPE_ENCRYPTED, 'Encrypted'),
    ]

    archive_item = models.ForeignKey(ArchiveItem, on_delete=models.CASCADE, related_name='custom_metadata')
    field_name = models.CharField(max_length=100)
    field_value = models.TextField()
    field_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=FIELD_TYPE_STRING)

    class Meta:
        unique_together = [['archive_item', 'field_name']]
        indexes = [models.Index(fields=['field_name'])]

    def __str__(self):
        return f"{self.field_name}: {self.field_value[:50]}"


class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


ArchiveItem.add_to_class('tags', models.ManyToManyField(Tag, blank=True, related_name='items'))


class AuditLog(models.Model):
    ACTION_CREATE = 'CREATE'
    ACTION_VIEW = 'VIEW'
    ACTION_MODIFY = 'MODIFY'
    ACTION_DELETE = 'DELETE'
    ACTION_EXPORT = 'EXPORT'
    ACTION_CHANGE_PERMISSION = 'CHANGE_PERMISSION'
    ACTION_CHOICES = [
        (ACTION_CREATE, 'Create'),
        (ACTION_VIEW, 'View'),
        (ACTION_MODIFY, 'Modify'),
        (ACTION_DELETE, 'Delete'),
        (ACTION_EXPORT, 'Export'),
        (ACTION_CHANGE_PERMISSION, 'Change Permission'),
    ]

    log_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    archive_item = models.ForeignKey(ArchiveItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs')
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.CharField(max_length=200, blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['archive_item', 'timestamp']),
            models.Index(fields=['action']),
        ]

    def __str__(self):
        return f"{self.action} by {self.user} at {self.timestamp}"


class ActiveSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='active_sessions')
    session_token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    mfa_verified = models.BooleanField(default=False)
    last_activity = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Session for {self.user} (expires {self.expires_at})"


class AccessRequest(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_DENIED = 'DENIED'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_DENIED, 'Denied'),
    ]

    requester_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='access_requests')
    archive_item = models.ForeignKey(ArchiveItem, on_delete=models.CASCADE, related_name='access_requests')
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    approver = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_requests')
    approved_at = models.DateTimeField(null=True, blank=True)
    granted_access_until = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)

    @property
    def is_expired(self):
        """Return True if the request is approved and the access period has passed."""
        return self.status == self.STATUS_APPROVED and self.granted_access_until < timezone.now()

    class Meta:
        indexes = [
            models.Index(fields=['requester_user', 'status']),
            models.Index(fields=['archive_item', 'status']),
        ]

    def __str__(self):
        return f"{self.requester_user} -> {self.archive_item} ({self.status})"
    


class UserFolder(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='folders')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subfolders')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'name', 'parent']  # folder name unique per user per parent

    def __str__(self):
        return f"{self.user.username}/{self.name}"

class FolderType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveIntegerField(unique=True)   
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    description = models.TextField(blank=True)

    class Meta:

        ordering = ['parent__id', 'order']
        unique_together = [['parent', 'order']]
    def __str__(self):
        return f"{self.order}. {self.name}"


class ItemFolder(models.Model):
    """Folder inside a specific ArchiveItem (per user per loan)"""
    archive_item = models.ForeignKey('ArchiveItem', on_delete=models.CASCADE, related_name='folders')
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='item_folders')
    folder_type = models.ForeignKey(FolderType, on_delete=models.PROTECT) 
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children'
    )
    custom_name = models.CharField(max_length=200, blank=True, help_text="Custom name for subfolders (if folder_type is null)")
    created_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='deleted_%(class)ss')



    class Meta:
        unique_together = ['archive_item', 'user', 'folder_type']  
        ordering = ['parent__id','folder_type__order']   
    @property
    def has_pending_deletion(self):
        return self.deletion_requests.filter(status='pending').exists()
    
    def __str__(self):
        return f"{self.archive_item.title} - {self.folder_type.name}"


class ItemFile(models.Model):
    """File stored inside an ItemFolder"""
    folder = models.ForeignKey(ItemFolder, on_delete=models.CASCADE, related_name='files')
    asset_uuid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    file_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField()
    mime_type = models.CharField(max_length=100)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    stored_path = models.CharField(max_length=500, blank=True, help_text="Storage path relative to MEDIA_ROOT")
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='deleted_%(class)ss')
    @property
    def has_pending_deletion(self):
        return self.deletion_requests.filter(status='pending').exists()

    def __str__(self):
        return self.file_name
    



class SharedFolder(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shared_folders')
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    loan_files = models.ManyToManyField('ItemFile', blank=True, related_name='shared_folders')
    doc_files = models.ManyToManyField('DocumentFile', blank=True, related_name='shared_folders')
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class SharedFolderFile(models.Model):
    shared_folder = models.ForeignKey(SharedFolder, on_delete=models.CASCADE, related_name='files')
    item_file = models.ForeignKey('ItemFile', on_delete=models.CASCADE, related_name='shared_in')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['shared_folder', 'item_file']

    def __str__(self):
        return f"{self.shared_folder.name} - {self.item_file.file_name}"


class SharedFolderAccess(models.Model):
    shared_folder = models.ForeignKey(SharedFolder, on_delete=models.CASCADE, related_name='allowed_users')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shared_folders_access')
    granted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='granted_access')
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['shared_folder', 'user']

    def __str__(self):
        return f"{self.user.username} can view {self.shared_folder.name}"
    




class DocumentText(models.Model):
    item_file = models.OneToOneField('ItemFile', on_delete=models.CASCADE, related_name='ocr_text')
    extracted_text = models.TextField()
    indexed_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def search_by_text(cls, keyword):
        return cls.objects.filter(extracted_text__icontains=keyword)
    
    def __str__(self):
        return f"OCR for {self.item_file.file_name}"
    


class RelatedItem(models.Model):
    RELATION_CHOICES = [
        ('guarantees', 'Guarantees'),
        ('collateral_of', 'Collateral Of'),
        ('amendment_to', 'Amendment To'),
        ('parent_loan', 'Parent Loan'),
        ('sub_loan', 'Sub Loan'),
        ('cross_default', 'Cross Default'),
        ('associated', 'Associated'),
    ]

    from_item = models.ForeignKey(
        'ArchiveItem',
        on_delete=models.CASCADE,
        related_name='outgoing_relations'
    )
    to_item = models.ForeignKey(
        'ArchiveItem',
        on_delete=models.CASCADE,
        related_name='incoming_relations'
    )
    relation_type = models.CharField(max_length=20, choices=RELATION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ('from_item', 'to_item', 'relation_type')
        indexes = [
            models.Index(fields=['from_item', 'relation_type']),
            models.Index(fields=['to_item', 'relation_type']),
        ]

    def __str__(self):
        return f"{self.from_item} {self.relation_type} {self.to_item}"
    




class DocumentFolder(models.Model):
    archive_item = models.ForeignKey('ArchiveItem', on_delete=models.CASCADE, related_name='doc_folders')
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='doc_folders')
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        unique_together = ['archive_item', 'user', 'name']

    def __str__(self):
        return f"{self.archive_item.title} - {self.name}"


class DocumentFile(models.Model):
    folder = models.ForeignKey(DocumentFolder, on_delete=models.CASCADE, related_name='files')
    asset_uuid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    file_name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField()
    mime_type = models.CharField(max_length=100)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    stored_path = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return self.file_name
    


class DigitalSignature(models.Model):
    """Digital signature applied to a file"""
    SIGNATURE_TYPE_SIMPLE = 'simple'
    SIGNATURE_TYPE_CERTIFICATE = 'certificate'
    SIGNATURE_TYPE_CHOICES = [
        (SIGNATURE_TYPE_SIMPLE, 'Simple/Visible'),
        (SIGNATURE_TYPE_CERTIFICATE, 'Digital Certificate'),
    ]
    
    signature_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
    item_file = models.ForeignKey('ItemFile', on_delete=models.CASCADE, null=True, blank=True, related_name='signatures')
    document_file = models.ForeignKey('DocumentFile', on_delete=models.CASCADE, null=True, blank=True, related_name='signatures')
    
    signed_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='applied_signatures')
    signature_type = models.CharField(max_length=20, choices=SIGNATURE_TYPE_CHOICES, default=SIGNATURE_TYPE_SIMPLE)
    
    page_number = models.IntegerField(default=0)
    x_position = models.FloatField()
    y_position = models.FloatField()
    signature_width = models.FloatField(default=200)
    signature_height = models.FloatField(default=80)
    
    signature_image = models.ImageField(upload_to='signatures/', null=True, blank=True)
    
    certificate_signer_name = models.CharField(max_length=200, blank=True)
    certificate_serial_number = models.CharField(max_length=100, blank=True)
    certificate_issuer = models.CharField(max_length=200, blank=True)
    signing_time = models.DateTimeField(auto_now_add=True)
    
    reason = models.CharField(max_length=200, blank=True, help_text="Reason for signing")
    location = models.CharField(max_length=200, blank=True, help_text="Location where signed")
    contact_info = models.CharField(max_length=200, blank=True)
    
    signed_file = models.FileField(upload_to='signed_documents/', null=True, blank=True)
    signed_file_hash = models.CharField(max_length=64, blank=True, help_text="SHA-256 of signed file")
    
    is_valid = models.BooleanField(default=True)
    validation_details = models.JSONField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['signed_by', 'created_at']),
            models.Index(fields=['item_file', 'is_valid']),
            models.Index(fields=['document_file', 'is_valid']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        target = self.item_file or self.document_file
        return f"Signature on {target} by {self.signed_by}"


# class SignatureEnvelope(models.Model):
#     """Similar to Adobe Sign 'Agreement' - contains documents and signers"""
#     STATUS_DRAFT = 'draft'
#     STATUS_SENT = 'sent'
#     STATUS_IN_PROGRESS = 'in_progress'
#     STATUS_COMPLETED = 'completed'
#     STATUS_DECLINED = 'declined'
#     STATUS_EXPIRED = 'expired'
#     STATUS_VOIDED = 'voided'
    
#     STATUS_CHOICES = [
#         (STATUS_DRAFT, 'Draft'),
#         (STATUS_SENT, 'Sent'),
#         (STATUS_IN_PROGRESS, 'In Progress'),
#         (STATUS_COMPLETED, 'Completed'),
#         (STATUS_DECLINED, 'Declined'),
#         (STATUS_EXPIRED, 'Expired'),
#         (STATUS_VOIDED, 'Voided'),
#     ]
    
#     envelope_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
#     item_file = models.ForeignKey('ItemFile', on_delete=models.CASCADE, null=True, blank=True, related_name='envelopes')
#     document_file = models.ForeignKey('DocumentFile', on_delete=models.CASCADE, null=True, blank=True, related_name='envelopes')
#     title = models.CharField(max_length=255)
#     message = models.TextField(blank=True, help_text="Message to signers")
#     created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_envelopes')
#     status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
#     signing_order = models.CharField(max_length=20, choices=[('parallel', 'Parallel'), ('sequential', 'Sequential')], default='parallel')
#     reminder_days = models.IntegerField(default=3, help_text="Days between reminders")
#     expires_days = models.IntegerField(default=30, help_text="Days until envelope expires")
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)
#     sent_at = models.DateTimeField(null=True, blank=True)
#     completed_at = models.DateTimeField(null=True, blank=True)
#     signed_document = models.FileField(upload_to='signed_envelopes/', null=True, blank=True)
    
#     class Meta:
#         indexes = [
#             models.Index(fields=['status', 'created_at']),
#             models.Index(fields=['created_by', 'status']),
#         ]
    
#     def __str__(self):
#         return f"{self.title} - {self.get_status_display()}"
    
#     def get_document(self):
#         return self.item_file or self.document_file





class SignatureEnvelope(models.Model):
    """Similar to Adobe Sign 'Agreement' - contains documents and signers"""
    STATUS_DRAFT = 'draft'
    STATUS_SENT = 'sent'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED = 'completed'
    STATUS_DECLINED = 'declined'
    STATUS_EXPIRED = 'expired'
    STATUS_VOIDED = 'voided'
    
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SENT, 'Sent'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_DECLINED, 'Declined'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_VOIDED, 'Voided'),
    ]
    
    envelope_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    item_file = models.ForeignKey('ItemFile', on_delete=models.CASCADE, null=True, blank=True, related_name='envelopes')
    document_file = models.ForeignKey('DocumentFile', on_delete=models.CASCADE, null=True, blank=True, related_name='envelopes')
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True, help_text="Message to signers")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_envelopes')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    signing_order = models.CharField(max_length=20, choices=[('parallel', 'Parallel'), ('sequential', 'Sequential')], default='parallel')
    reminder_days = models.IntegerField(default=3, help_text="Days between reminders")
    expires_days = models.IntegerField(default=30, help_text="Days until envelope expires")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    signed_document = models.FileField(upload_to='signed_envelopes/', null=True, blank=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['created_by', 'status']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.get_status_display()}"
    
    def get_document(self):
        return self.item_file or self.document_file
    
    @property
    def is_complete(self):
        return self.signature_recipients.filter(status='pending').count() == 0
    
    def send(self):
        """Send envelope to all recipients"""
        self.status = self.STATUS_SENT
        self.sent_at = timezone.now()
        self.save()
        
        for recipient in self.recipients.all():
            recipient.send_signing_request()
    
    def void(self, reason):
        """Void the envelope"""
        self.status = self.STATUS_VOIDED
        self.save()
        
        for recipient in self.recipients.all():
            recipient.notify_voided(reason)


class SignatureRecipient(models.Model):
    """Similar to Adobe Sign 'Recipient' - person who needs to sign"""
    ROLE_SIGNER = 'signer'
    ROLE_APPROVER = 'approver'
    ROLE_CARBON_COPY = 'cc'
    ROLE_CHOICES = [
        (ROLE_SIGNER, 'Needs to Sign'),
        (ROLE_APPROVER, 'Needs to Approve'),
        (ROLE_CARBON_COPY, 'Receives Copy'),
    ]
    
    STATUS_PENDING = 'pending'
    STATUS_SIGNED = 'signed'
    STATUS_DECLINED = 'declined'
    STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SIGNED, 'Signed'),
        (STATUS_DECLINED, 'Declined'),
        (STATUS_EXPIRED, 'Expired'),
    ]
    
    recipient_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    envelope = models.ForeignKey(SignatureEnvelope, on_delete=models.CASCADE, related_name='recipients')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='signature_recipients')
    email = models.EmailField()
    full_name = models.CharField(max_length=200)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_SIGNER)
    signing_order = models.IntegerField(default=0, help_text="Order in which this recipient signs")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)

    signing_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)

    decline_reason = models.TextField(blank=True)
    saved_signature = models.TextField(blank=True, null=True)
    saved_signature_path = models.CharField(max_length=500, blank=True, null=True)
    
    class Meta:
        ordering = ['signing_order', 'created_at']
    
    def __str__(self):
        return f"{self.full_name} ({self.get_role_display()}) - {self.get_status_display()}"
    
    def get_signing_url(self):
        """Generate secure signing URL"""
        return reverse('sign_portal', kwargs={'token': self.signing_token})
    
    def send_signing_request(self):
        """Send email with signing link"""
        from django.core.mail import send_mail
        from django.conf import settings
        
        subject = f"Please sign: {self.envelope.title}"
        message = f"""
        Dear {self.full_name},
        
        {self.envelope.created_by.get_full_name()} has requested your signature on: {self.envelope.title}
        
        {self.envelope.message}
        
        Click the link below to review and sign:
        {settings.BASE_URL}{self.get_signing_url()}
        
        This request will expire on: {(timezone.now() + timezone.timedelta(days=self.envelope.expires_days)).strftime('%Y-%m-%d')}
        
        Thank you,
        Loan Management System
        """
        
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [self.email])
    
    def notify_voided(self, reason):
        """Notify recipient that envelope was voided"""
        from django.core.mail import send_mail
        
        subject = f"Envelope voided: {self.envelope.title}"
        message = f"""
        Dear {self.full_name},
        
        The envelope "{self.envelope.title}" has been voided.
        Reason: {reason}
        
        You no longer need to sign this document.
        
        Regards,
        Loan Management System
        """
        
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [self.email])





class SignatureField(models.Model):
    """Stores signature field positions - like dragging fields in Adobe Sign"""
    FIELD_TYPE_SIGNATURE = 'signature'
    FIELD_TYPE_INITIAL = 'initial'
    FIELD_TYPE_DATE = 'date'
    FIELD_TYPE_TEXT = 'text'
    FIELD_TYPE_CHECKBOX = 'checkbox'
    FIELD_TYPE_CHOICES = [
        (FIELD_TYPE_SIGNATURE, 'Signature'),
        (FIELD_TYPE_INITIAL, 'Initials'),
        (FIELD_TYPE_DATE, 'Date Signed'),
        (FIELD_TYPE_TEXT, 'Text Field'),
        (FIELD_TYPE_CHECKBOX, 'Checkbox'),
    ]
    
    field_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    envelope = models.ForeignKey(SignatureEnvelope, on_delete=models.CASCADE, related_name='fields')
    recipient = models.ForeignKey(SignatureRecipient, on_delete=models.CASCADE, related_name='fields')

    field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES, default=FIELD_TYPE_SIGNATURE)
    label = models.CharField(max_length=100, blank=True, help_text="Field label (e.g., 'Sign Here')")

    page_number = models.IntegerField()
    x_position = models.FloatField()
    y_position = models.FloatField()
    width = models.FloatField(default=150)
    height = models.FloatField(default=50)

    required = models.BooleanField(default=True)

    signed_value = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    filled_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['page_number', 'y_position', 'x_position']
    
    def __str__(self):
        return f"{self.get_field_type_display()} for {self.recipient.full_name} on page {self.page_number}"



# class SignatureField(models.Model):
#     """Stores signature field positions - like dragging fields in Adobe Sign"""
#     FIELD_TYPE_SIGNATURE = 'signature'
#     FIELD_TYPE_INITIAL = 'initial'
#     FIELD_TYPE_DATE = 'date'
#     FIELD_TYPE_TEXT = 'text'
#     FIELD_TYPE_CHECKBOX = 'checkbox'
#     FIELD_TYPE_CHOICES = [
#         (FIELD_TYPE_SIGNATURE, 'Signature'),
#         (FIELD_TYPE_INITIAL, 'Initials'),
#         (FIELD_TYPE_DATE, 'Date Signed'),
#         (FIELD_TYPE_TEXT, 'Text Field'),
#         (FIELD_TYPE_CHECKBOX, 'Checkbox'),
#     ]
    
#     field_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

#     envelope = models.ForeignKey(SignatureEnvelope, on_delete=models.CASCADE, related_name='fields')
#     recipient = models.ForeignKey(SignatureRecipient, on_delete=models.CASCADE, related_name='fields')

#     field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES, default=FIELD_TYPE_SIGNATURE)
#     label = models.CharField(max_length=100, blank=True, help_text="Field label (e.g., 'Sign Here')")

#     page_number = models.IntegerField()
#     x_position = models.FloatField()
#     y_position = models.FloatField()
#     width = models.FloatField(default=150)
#     height = models.FloatField(default=50)

#     required = models.BooleanField(default=True)

#     signed_value = models.TextField(blank=True)

#     created_at = models.DateTimeField(auto_now_add=True)
#     filled_at = models.DateTimeField(null=True, blank=True)
    
#     class Meta:
#         ordering = ['page_number', 'y_position', 'x_position']
    
#     def __str__(self):
#         return f"{self.get_field_type_display()} for {self.recipient.full_name} on page {self.page_number}"
######################################################################################################################################





class DeletionRequest(models.Model):
    REQUEST_TYPES = (
        ('file', 'File'),
        ('folder', 'Folder'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    request_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_type = models.CharField(max_length=10, choices=REQUEST_TYPES)

    item_file = models.ForeignKey('ItemFile', on_delete=models.SET_NULL, null=True, blank=True)

    item_folder = models.ForeignKey('ItemFolder', on_delete=models.SET_NULL, null=True, blank=True)
    
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='deletion_requests')
    requested_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_deletions')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    item_file = models.ForeignKey('ItemFile', on_delete=models.SET_NULL, null=True, blank=True, related_name='deletion_requests')
    item_folder = models.ForeignKey('ItemFolder', on_delete=models.SET_NULL, null=True, blank=True, related_name='deletion_requests')
    
    class Meta:
        indexes = [
            models.Index(fields=['status', 'requested_at']),
        ]