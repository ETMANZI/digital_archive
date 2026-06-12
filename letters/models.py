from django.conf import settings
from django.db import models
import uuid

class Letter(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='letters')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pdf_file = models.FileField(upload_to='letters/pdfs/', blank=True, null=True)
    signed_pdf = models.FileField(upload_to='letters/signed/', blank=True, null=True)

    def __str__(self):
        return self.title

class SignatureRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('signed', 'Signed'),
        ('declined', 'Declined'),
        ('expired', 'Expired'),
    )
    letter = models.ForeignKey(Letter, on_delete=models.CASCADE, related_name='signature_requests')
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='signature_requests')
    signing_token = models.UUIDField(default=uuid.uuid4, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    signing_order = models.PositiveIntegerField(default=1)
    viewed_at = models.DateTimeField(blank=True, null=True)
    signed_at = models.DateTimeField(blank=True, null=True)
    decline_reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['signing_order']

    def __str__(self):
        return f"{self.letter.title} - {self.recipient.username}"

class SignatureField(models.Model):
    FIELD_TYPES = (
        ('signature', 'Signature'),
        ('date', 'Date'),
        ('text', 'Text'),
        ('initial', 'Initials'),
    )
    signature_request = models.ForeignKey(SignatureRequest, on_delete=models.CASCADE, related_name='fields')
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES)
    page_number = models.IntegerField()
    x_position = models.FloatField()
    y_position = models.FloatField()
    width = models.FloatField(default=150)
    height = models.FloatField(default=50)
    value = models.TextField(blank=True, null=True)
    stamp_text = models.CharField(max_length=255, blank=True, null=True)
    filled_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.field_type} on page {self.page_number}"