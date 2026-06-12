# archive/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.conf import settings
from .models import SignatureRecipient, SignatureEnvelope


@receiver(post_save, sender=SignatureRecipient)
def send_signing_notification(sender, instance, created, **kwargs):
    """Send email notification when a recipient is added to an envelope"""
    if created and instance.envelope.status != SignatureEnvelope.STATUS_DRAFT:
        instance.send_signing_request()