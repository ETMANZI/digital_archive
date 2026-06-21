# views_signature.py - Complete version
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.files.base import ContentFile
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
import json
import fitz  # PyMuPDF
import tempfile
import os
from PIL import Image
import io

from .models import (
    SignatureEnvelope, SignatureRecipient, SignatureField, 
    User, ItemFile, DocumentFile, DigitalSignature
)


@login_required
def signature_dashboard(request):
    """Dashboard showing all signature activity"""
    
    # Envelopes created by user
    sent_envelopes = SignatureEnvelope.objects.filter(
        created_by=request.user
    ).order_by('-created_at')[:10]
    
    # Envelopes pending user's signature
    pending_envelopes = SignatureEnvelope.objects.filter(
        recipients__user=request.user,
        recipients__status='pending',
        status__in=['sent', 'in_progress']
    ).distinct().order_by('-created_at')
    
    # Statistics
    stats = {
        'total_sent': SignatureEnvelope.objects.filter(created_by=request.user).count(),
        'completed': SignatureEnvelope.objects.filter(
            created_by=request.user, 
            status='completed'
        ).count(),
        'pending_signatures': SignatureRecipient.objects.filter(
            user=request.user, 
            status='pending'
        ).count(),
        'expired': SignatureEnvelope.objects.filter(
            created_by=request.user, 
            status='expired'
        ).count(),
    }
    
    context = {
        'sent_envelopes': sent_envelopes,
        'pending_envelopes': pending_envelopes,
        'stats': stats,
        'recent_envelopes': sent_envelopes,
        'sent_count': stats['total_sent'],
        'completed_count': stats['completed'],
    }
    
    return render(request, 'signature/dashboard.html', context)


@login_required
def pending_signatures(request):
    """List of documents pending user's signature"""
    
    pending_recipients = SignatureRecipient.objects.filter(
        user=request.user,
        status='pending'
    ).select_related('envelope').order_by('signing_order', 'created_at')
    
    # Group by envelope
    envelopes = {}
    for recipient in pending_recipients:
        if recipient.envelope.id not in envelopes:
            envelopes[recipient.envelope.id] = {
                'envelope': recipient.envelope,
                'recipients': []
            }
        envelopes[recipient.envelope.id]['recipients'].append(recipient)
    
    context = {
        'envelopes': envelopes.values(),
        'pending_count': pending_recipients.count(),
    }
    
    return render(request, 'signature/pending_signatures.html', context)


@login_required
def sent_envelopes(request):
    """List envelopes sent by user"""
    envelopes = SignatureEnvelope.objects.filter(
        created_by=request.user
    ).order_by('-created_at')
    
    return render(request, 'signature/sent_envelopes.html', {
        'envelopes': envelopes
    })


@login_required
def completed_envelopes(request):
    """List completed envelopes"""
    envelopes = SignatureEnvelope.objects.filter(
        created_by=request.user,
        status='completed'
    ).order_by('-completed_at')
    
    return render(request, 'signature/completed_envelopes.html', {
        'envelopes': envelopes
    })

@login_required
def envelope_detail(request, envelope_id):
    """View envelope details and status"""
    
    envelope = get_object_or_404(SignatureEnvelope, id=envelope_id)
    
    # Check permission
    if envelope.created_by != request.user and not request.user.is_superuser:
        messages.error(request, "You don't have permission to view this envelope")
        return redirect('signature_dashboard')
    
    # Determine if the user can manage recipients (add/remove)
    can_manage_recipients = (envelope.created_by == request.user or request.user.is_superuser)
    
    context = {
        'envelope': envelope,
        'recipients': envelope.recipients.all().order_by('signing_order'),
        'fields': envelope.fields.all(),
        'can_void': envelope.status in ['draft', 'sent', 'in_progress'],
        'can_manage_recipients': can_manage_recipients,   # <--- add this
        'MEDIA_URL': settings.MEDIA_URL,
    }
    
    return render(request, 'signature/envelope_detail.html', context)


@login_required
def create_signature_envelope(request, file_id, file_type='item'):
    """Create a new signature envelope - like Adobe Sign 'Send for Signature'"""
    
    if file_type == 'item':
        file_obj = get_object_or_404(ItemFile, id=file_id)
    else:
        file_obj = get_object_or_404(DocumentFile, id=file_id)
    
    if request.method == 'POST':
        envelope = SignatureEnvelope.objects.create(
            item_file=file_obj if file_type == 'item' else None,
            document_file=file_obj if file_type == 'document' else None,
            title=request.POST.get('title'),
            message=request.POST.get('message', ''),
            created_by=request.user,
            signing_order=request.POST.get('signing_order', 'parallel'),
            expires_days=int(request.POST.get('expires_days', 30))
        )
        
        # Add recipients
        recipient_names = request.POST.getlist('recipient_name[]')
        recipient_emails = request.POST.getlist('recipient_email[]')
        recipient_roles = request.POST.getlist('recipient_role[]')
        
        for idx, (name, email, role) in enumerate(zip(recipient_names, recipient_emails, recipient_roles)):
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                user = None
            
            SignatureRecipient.objects.create(
                envelope=envelope,
                user=user,
                email=email,
                full_name=name,
                role=role,
                signing_order=idx
            )
        
        # Send envelope
        envelope.send()
        
        messages.success(request, f"Envelope '{envelope.title}' sent for signature!")
        return redirect('envelope_detail', envelope_id=envelope.id)
    
    context = {
        'file': file_obj,
        'file_type': file_type,
        'users': User.objects.filter(is_active=True),
    }
    return render(request, 'signature/create_envelope.html', context)



import json
from django.http import JsonResponse

def save_signature(request, token):
    recipient = get_object_or_404(SignatureRecipient, signing_token=token)
    data = json.loads(request.body)
    signature_data = data.get('signature')
    if signature_data:
        recipient.saved_signature = signature_data
        recipient.save()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'No signature'})





def apply_signatures_to_pdf_from_json(envelope, recipient, fields_data):
    debug_log = r'D:\digital_archive\json_stamp.log'
    def log(msg):
        try:
            with open(debug_log, 'a') as f:
                f.write(f"{timezone.now().strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
        except:
            pass

    log("=== apply_signatures_to_pdf_from_json START ===")
    file_obj = envelope.get_document()
    if not file_obj or not file_obj.stored_path:
        raise ValueError("Document file not found")
    original_path = os.path.join(settings.MEDIA_ROOT, file_obj.stored_path)
    doc = fitz.open(original_path)

    for field in fields_data:
        page_num = field['page']
        if page_num >= len(doc):
            continue
        page = doc[page_num]
        rect = fitz.Rect(
            field['x'], field['y'],
            field['x'] + field['width'],
            field['y'] + field['height']
        )
        if field['type'] in ('signature', 'initial'):
            # Insert image
            img_data_url = field.get('value')
            if img_data_url and img_data_url.startswith('data:image'):
                try:
                    _, encoded = img_data_url.split(',', 1)
                    img_data = base64.b64decode(encoded)
                    page.insert_image(rect, stream=io.BytesIO(img_data))
                    log(f"Inserted image for field {field['id']}")
                except Exception as e:
                    log(f"Image error: {e}")
            # Insert stamp text if present
            stamp_text = field.get('stampText')
            if stamp_text:
                stamp_rect = fitz.Rect(
                    field['x'],
                    field['y'] + field['height'] - 25,
                    field['x'] + field['width'],
                    field['y'] + field['height']
                )
                page.insert_textbox(stamp_rect, stamp_text, fontsize=9, fontname="helv")
                log(f"Inserted stamp text for field {field['id']}: '{stamp_text}'")
        elif field['type'] == 'date':
            page.insert_textbox(rect, field.get('value', ''), fontsize=10)
        elif field['type'] == 'text':
            page.insert_textbox(rect, field.get('value', ''), fontsize=10)

    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)
    log("=== apply_signatures_to_pdf_from_json END ===")
    return output.read()




import io
import os
import base64
import re
import fitz
from django.conf import settings
from django.utils import timezone

def apply_signatures_to_pdf(envelope, recipient, signature_images, post_data):
    debug_log = r'D:\digital_archive\pdf_stamp.log'
    def log(msg):
        try:
            with open(debug_log, 'a') as f:
                f.write(f"{timezone.now().strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
        except:
            pass

    log("=== apply_signatures_to_pdf START ===")

    # Locate PDF
    file_obj = envelope.get_document()
    if not file_obj or not file_obj.stored_path:
        raise ValueError("Document file not found")
    original_path = os.path.join(settings.MEDIA_ROOT, file_obj.stored_path)
    if not os.path.exists(original_path):
        raise FileNotFoundError(f"PDF not found at {original_path}")
    log(f"PDF: {original_path}")

    doc = fitz.open(original_path)
    log(f"PDF opened, pages={len(doc)}")

    # Extract fields from POST data (keys like field_123_type, field_123_page, etc.)
    fields_data = {}
    field_pattern = re.compile(r'field_(\d+)_(\w+)')
    for key, value in post_data.items():
        m = field_pattern.match(key)
        if m:
            fid = int(m.group(1))
            prop = m.group(2)
            if fid not in fields_data:
                fields_data[fid] = {}
            fields_data[fid][prop] = value

    log(f"Found {len(fields_data)} fields from POST data")

    for fid, data in fields_data.items():
        field_type = data.get('type')
        if not field_type:
            continue
        page = int(data.get('page', 0))
        x = float(data.get('x', 0))
        y = float(data.get('y', 0))
        width = float(data.get('width', 150))
        height = float(data.get('height', 60))

        log(f"Field {fid}: type={field_type}, page={page}, pos=({x},{y})")

        if page >= len(doc):
            log(f"  SKIP: page out of range")
            continue

        page_obj = doc[page]
        rect = fitz.Rect(x, y, x + width, y + height)

        if field_type in ('signature', 'initial'):
            # Signature image
            sig_key = f'signature_data_{fid}'
            img_data = None
            if sig_key in post_data:
                data_url = post_data.get(sig_key)
                if data_url and data_url.startswith('data:image'):
                    try:
                        _, encoded = data_url.split(',', 1)
                        img_data = base64.b64decode(encoded)
                        log(f"  Got image, size={len(img_data)}")
                    except Exception as e:
                        log(f"  Decoding error: {e}")
            if img_data:
                try:
                    page_obj.insert_image(rect, stream=io.BytesIO(img_data))
                    log(f"  Image inserted")
                except Exception as e:
                    log(f"  Image insertion error: {e}")
            else:
                page_obj.draw_rect(rect, color=(1,0,0), width=2)
                log(f"  No image, drew rectangle")

            # Stamp text
            stamp_key = f'stamp_text_{fid}'
            stamp_text = post_data.get(stamp_key, '')
            if stamp_text:
                stamp_rect = fitz.Rect(x, y + height - 18, x + width, y + height)
                try:
                    page_obj.insert_textbox(stamp_rect, stamp_text, fontsize=8, fontname="helv")
                    log(f"  Stamp inserted: '{stamp_text}'")
                except Exception as e:
                    log(f"  Stamp error: {e}")

        elif field_type == 'date':
            date_text = timezone.now().strftime("%Y-%m-%d %H:%M")
            page_obj.insert_textbox(rect, date_text, fontsize=10)
            log(f"  Date inserted")

        elif field_type == 'text':
            text_value = data.get('value', '')
            page_obj.insert_textbox(rect, text_value, fontsize=10)
            log(f"  Text inserted: {text_value}")

    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)
    log(f"Saved PDF size: {len(output.getvalue())} bytes")
    log("=== apply_signatures_to_pdf END ===")
    return output.read()


from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.urls import reverse
from django.conf import settings
from django.utils import timezone
import os
import shutil

def sign_portal(request, token):
    recipient = get_object_or_404(SignatureRecipient, signing_token=token)
    envelope = recipient.envelope

    # Status checks
    if envelope.status == SignatureEnvelope.STATUS_EXPIRED:
        return render(request, 'signature/expired.html', {'envelope': envelope})
    if envelope.status == SignatureEnvelope.STATUS_VOIDED:
        return render(request, 'signature/voided.html', {'envelope': envelope})
    if recipient.status != SignatureRecipient.STATUS_PENDING:
        return render(request, 'signature/already_signed.html', {'recipient': recipient})

    if not recipient.viewed_at:
        recipient.viewed_at = timezone.now()
        recipient.save()

    file_obj = envelope.get_document()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'decline':
            recipient.status = SignatureRecipient.STATUS_DECLINED
            recipient.decline_reason = request.POST.get('reason', '')
            recipient.save()
            notify_declined(envelope, recipient)
            messages.warning(request, "You have declined.")
            return redirect('archive:signature_declined')

        elif action == 'sign':
            debug_log = r'D:\digital_archive\sign_submit.log'
            def log(msg):
                try:
                    with open(debug_log, 'a') as f:
                        f.write(f"{timezone.now().strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
                except:
                    pass
            try:
                log("=== SIGN SUBMISSION START ===")
                post_and_files = request.POST.copy()
                post_and_files.update(request.FILES)
                signed_pdf = apply_signatures_to_pdf(envelope, recipient, [], post_and_files)
                if not signed_pdf or len(signed_pdf) < 1000:
                    raise ValueError("Signed PDF too small")
                # Overwrite original
                if not file_obj or not file_obj.stored_path:
                    raise ValueError("No document")
                original_path = os.path.join(settings.MEDIA_ROOT, file_obj.stored_path)
                if not os.path.exists(original_path):
                    raise FileNotFoundError("Original PDF missing")
                backup_path = original_path + '.backup'
                if not os.path.exists(backup_path):
                    import shutil
                    shutil.copy2(original_path, backup_path)
                with open(original_path, 'wb') as f:
                    f.write(signed_pdf)
                log("Original file overwritten")
                recipient.status = SignatureRecipient.STATUS_SIGNED
                recipient.signed_at = timezone.now()
                recipient.save()
                envelope.status = SignatureEnvelope.STATUS_COMPLETED
                envelope.completed_at = timezone.now()
                envelope.save()
                notify_completion(envelope)
                messages.success(request, "Document signed successfully!")
                log("SUCCESS, redirecting to signature_complete")
                return redirect('archive:signature_complete')
            except Exception as e:
                log(f"ERROR: {str(e)}")
                import traceback
                log(traceback.format_exc())
                messages.error(request, f"Error: {str(e)}")
                return redirect('archive:sign_portal', token=token)

    # GET request – show signing interface
    file_url = None
    if file_obj and file_obj.stored_path:
        file_url = request.build_absolute_uri(reverse('archive:serve_file', args=[file_obj.stored_path]))

    context = {
        'envelope': envelope,
        'recipient': recipient,
        'file': file_obj,
        'file_url': file_url,
        'fields': recipient.fields.all(),
        'saved_signature': recipient.saved_signature,
    }
    return render(request, 'signature/sign_portal.html', context)




def notify_next_signer(envelope):
    """Notify the next signer in sequential order"""
    next_recipient = envelope.recipients.filter(
        status=SignatureRecipient.STATUS_PENDING
    ).order_by('signing_order').first()
    
    if next_recipient:
        next_recipient.send_signing_request()


def notify_completion(envelope):
    """Notify all parties that signing is complete"""
    recipients_emails = list(envelope.recipients.values_list('email', flat=True))
    creator_email = envelope.created_by.email
    
    subject = f"Document signed: {envelope.title}"
    message = f"""
    All parties have signed "{envelope.title}".
    
    Document: {envelope.title}
    Completed: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}
    
    The signed document is now available in the archive.
    
    Regards,
    Digital Archive System
    """
    
    try:
        send_mail(
            subject, 
            message, 
            settings.DEFAULT_FROM_EMAIL, 
            recipients_emails + [creator_email]
        )
    except Exception as e:
        print(f"Email notification error: {e}")


def notify_declined(envelope, recipient):
    """Notify creator that a recipient declined to sign"""
    subject = f"Signature declined: {envelope.title}"
    message = f"""
    {recipient.full_name} has declined to sign "{envelope.title}".
    
    Reason: {recipient.decline_reason}
    
    You may need to contact them for more information.
    
    Regards,
    Digital Archive System
    """
    
    try:
        send_mail(
            subject, 
            message, 
            settings.DEFAULT_FROM_EMAIL, 
            [envelope.created_by.email]
        )
    except Exception as e:
        print(f"Email notification error: {e}")


# Simple completion/declined views
def signature_complete(request):
    return render(request, 'signature/complete.html')


def signature_declined(request):
    return render(request, 'signature/declined.html')







# views_signature.py - Clean version focused on Digital Archive

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from .models import (
    ArchiveItem, ItemFile, DocumentFile,
    SignatureEnvelope, SignatureRecipient, User
)

@login_required
def loan_signature_requests(request):
    """Display ArchiveItems that can be sent for signature"""
    
    # Get ArchiveItems of type LOAN that are active
    loans = ArchiveItem.objects.filter(
        document_type=ArchiveItem.DOCUMENT_TYPE_LOAN,
        is_deleted=False
    ).order_by('-created_at')
    
    # If user is not admin/manager, only show loans they created
    if not (request.user.is_system_admin() or request.user.is_archive_manager()):
        loans = loans.filter(created_by=request.user)
    
    context = {
        'loans': loans,
    }
    return render(request, 'signature/loan_signature_requests.html', context)


@login_required
def loan_signature_pending(request):
    """Display pending signature requests for loan documents"""
    
    # Get pending envelopes for loan documents
    pending_envelopes = SignatureEnvelope.objects.filter(
        item_file__folder__archive_item__document_type=ArchiveItem.DOCUMENT_TYPE_LOAN,
        status__in=['sent', 'in_progress']
    )
    
    # If user is not admin/manager, only show their requests
    if not (request.user.is_system_admin() or request.user.is_archive_manager()):
        pending_envelopes = pending_envelopes.filter(created_by=request.user)
    
    pending_envelopes = pending_envelopes.select_related(
        'item_file__folder__archive_item'
    ).order_by('-created_at')
    
    context = {
        'pending_envelopes': pending_envelopes,
    }
    return render(request, 'signature/loan_signature_pending.html', context)


@login_required
def loan_signature_history(request):
    """Display completed signature history for loan documents"""
    
    completed_envelopes = SignatureEnvelope.objects.filter(
        item_file__folder__archive_item__document_type=ArchiveItem.DOCUMENT_TYPE_LOAN,
        status='completed'
    )
    
    # If user is not admin/manager, only show their requests
    if not (request.user.is_system_admin() or request.user.is_archive_manager()):
        completed_envelopes = completed_envelopes.filter(created_by=request.user)
    
    completed_envelopes = completed_envelopes.select_related(
        'item_file__folder__archive_item'
    ).order_by('-completed_at')
    
    context = {
        'completed_envelopes': completed_envelopes,
    }
    return render(request, 'signature/loan_signature_history.html', context)


import logging
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from .models import ArchiveItem, ItemFile
import traceback
logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["GET"])
def get_loan_documents_api(request, loan_id):
    """Simple API endpoint to get documents for a loan"""
    try:
        from .models import ArchiveItem, ItemFile, ItemFolder
        
        print(f"\n=== GET LOAN DOCUMENTS API CALLED ===")
        print(f"Loan ID received: {loan_id}")
        print(f"Request user: {request.user}")
        print(f"User authenticated: {request.user.is_authenticated}")
        
        # Try to find the ArchiveItem
        try:
            archive_item = ArchiveItem.objects.get(id=loan_id)
            print(f"Found ArchiveItem: {archive_item.title} (ID: {archive_item.id})")
        except ArchiveItem.DoesNotExist:
            print(f"ArchiveItem with ID {loan_id} not found")
            return JsonResponse({
                'success': False,
                'error': f'Loan with ID {loan_id} not found'
            }, status=404)
        
        # Get all ItemFolders for this ArchiveItem
        folders = ItemFolder.objects.filter(archive_item=archive_item)
        print(f"Found {folders.count()} folders")
        
        # Get all ItemFiles in those folders
        item_files = ItemFile.objects.filter(folder__in=folders)
        print(f"Found {item_files.count()} files")
        
        # Build documents list
        documents = []
        for file in item_files:
            documents.append({
                'id': file.id,
                'name': file.file_name,
                'size': file.file_size,
                'type': file.mime_type,
            })
            print(f"  - File: {file.file_name} (ID: {file.id})")
        
        return JsonResponse({
            'success': True,
            'documents': documents,
            'count': len(documents),
            'loan_title': archive_item.title,
            'loan_id': archive_item.loan_id,
        })
        
    except Exception as e:
        print(f"ERROR in get_loan_documents_api: {str(e)}")
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__
        }, status=500)
    





@login_required
@csrf_exempt
def create_loan_signature_envelope(request):
    """Create signature envelope for loan document"""
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    
    try:
        file_id = request.POST.get('file_id')
        title = request.POST.get('title')
        message = request.POST.get('message', '')
        signing_order = request.POST.get('signing_order', 'parallel')
        expires_days = int(request.POST.get('expires_days', 30))
        
        # Get the ItemFile object
        item_file = get_object_or_404(ItemFile, id=file_id)
        
        # Verify user has permission to request signature for this document
        archive_item = item_file.folder.archive_item
        if not (request.user.is_system_admin() or 
                request.user.is_archive_manager() or 
                archive_item.created_by == request.user):
            return JsonResponse({
                'success': False,
                'error': 'You do not have permission to request signature for this document'
            }, status=403)
        
        # Create envelope
        envelope = SignatureEnvelope.objects.create(
            item_file=item_file,
            title=title,
            message=message,
            created_by=request.user,
            signing_order=signing_order,
            expires_days=expires_days
        )
        
        # Add recipients
        recipient_names = request.POST.getlist('recipient_name[]')
        recipient_emails = request.POST.getlist('recipient_email[]')
        recipient_roles = request.POST.getlist('recipient_role[]')
        
        for idx, (name, email, role) in enumerate(zip(recipient_names, recipient_emails, recipient_roles)):
            if name and email:  # Only add if both name and email are provided
                try:
                    user = User.objects.get(email=email)
                except User.DoesNotExist:
                    user = None
                
                SignatureRecipient.objects.create(
                    envelope=envelope,
                    user=user,
                    email=email,
                    full_name=name,
                    role=role,
                    signing_order=idx
                )
        
        # Send envelope
        envelope.send()
        
        return JsonResponse({
            'success': True,
            'envelope_id': envelope.id,
            'message': 'Signature request sent successfully'
        })
        
    except ItemFile.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Document not found'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)
    



# views_signature.py - Add this function

@login_required
@require_http_methods(["POST"])
def void_envelope(request, envelope_id):
    """Void a signature envelope"""
    envelope = get_object_or_404(SignatureEnvelope, id=envelope_id)
    
    # Check permission
    if envelope.created_by != request.user and not request.user.is_superuser:
        return JsonResponse({'error': 'Permission denied'}, status=403)
    
    try:
        reason = json.loads(request.body).get('reason', 'No reason provided')
        envelope.void(reason)
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
    
from django.http import FileResponse, Http404
import os
from django.conf import settings

from django.views.decorators.clickjacking import xframe_options_exempt

@xframe_options_exempt
def serve_file(request, file_path):
    full_path = os.path.join(settings.MEDIA_ROOT, file_path)
    if os.path.exists(full_path):
        return FileResponse(open(full_path, 'rb'), content_type='application/pdf')
    raise Http404("File not found")



from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt  # or use @csrf_protect with token
from django.views.decorators.http import require_POST
import json

@require_POST
def save_signature(request, token):
    """Save the drawn signature for this recipient."""
    recipient = get_object_or_404(SignatureRecipient, signing_token=token)
    data = json.loads(request.body)
    signature_data_url = data.get('signature')
    if signature_data_url:
        recipient.saved_signature = signature_data_url
        recipient.save()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'No signature data'})




from django.contrib.auth import get_user_model
from django.http import JsonResponse
import traceback
from django.utils import timezone

User = get_user_model()

def search_users(request):
    debug_log = r'D:\digital_archive\search_debug.log'
    try:
        with open(debug_log, 'a') as f:
            f.write(f"\n=== {timezone.now()} - search_users called ===\n")
            f.write(f"GET: {request.GET}\n")
        
        query = request.GET.get('q', '').strip()
        if len(query) < 2:
            return JsonResponse([], safe=False)
        
        # Search using the custom user model
        users = User.objects.filter(username__icontains=query)
        users = users | User.objects.filter(first_name__icontains=query)
        users = users | User.objects.filter(last_name__icontains=query)
        users = users | User.objects.filter(email__icontains=query)
        users = users.distinct()[:20]
        
        results = []
        for u in users:
            name = u.get_full_name() or u.username
            results.append({
                'id': u.id,
                'name': name,
                'email': u.email,
            })
        
        with open(debug_log, 'a') as f:
            f.write(f"Found {len(results)} users\n")
        
        return JsonResponse(results, safe=False)
    except Exception as e:
        with open(debug_log, 'a') as f:
            f.write(f"ERROR: {str(e)}\n")
            f.write(traceback.format_exc())
        return JsonResponse([], safe=False)




import uuid
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.core.mail import send_mail
from django.urls import reverse
from django.contrib.auth import get_user_model
from .models import SignatureEnvelope, SignatureRecipient

User = get_user_model()

def add_recipient_to_envelope(request, envelope_id):
    envelope = get_object_or_404(SignatureEnvelope, id=envelope_id)
    if request.user != envelope.created_by and not request.user.is_staff:
        messages.error(request, "Permission denied.")
        return redirect('archive:envelope_detail', envelope_id=envelope_id)
    
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        if user_id:
            try:
                user = get_object_or_404(User, id=user_id)
                # Check if already a recipient
                if envelope.recipients.filter(email=user.email).exists():
                    messages.warning(request, f"{user.get_full_name() or user.username} is already a recipient.")
                else:
                    # Generate a valid UUID
                    token = uuid.uuid4()
                    SignatureRecipient.objects.create(
                        envelope=envelope,
                        full_name=user.get_full_name() or user.username,
                        email=user.email,
                        signing_token=token,
                        status='pending',
                        signing_order=envelope.recipients.count() + 1,
                    )
                    messages.success(request, f"Added {user.get_full_name() or user.username} as a recipient.")
                    # Optional: send email
                    try:
                        sign_link = request.build_absolute_uri(reverse('archive:sign_portal', args=[token]))
                        send_mail(
                            f'Please sign "{envelope.title}"',
                            f'Click here to sign: {sign_link}',
                            'noreply@yourdomain.com',
                            [user.email],
                            fail_silently=True,
                        )
                    except Exception:
                        pass
            except Exception as e:
                messages.error(request, f"Error adding recipient: {str(e)}")
        else:
            messages.error(request, "No user selected.")
    return redirect('archive:envelope_detail', envelope_id=envelope_id)



def remove_recipient(request, recipient_id):
    recipient = get_object_or_404(SignatureRecipient, id=recipient_id)
    envelope = recipient.envelope
    if request.user != envelope.created_by and not request.user.is_staff:
        messages.error(request, "Permission denied.")
        return redirect('archive:envelope_detail', envelope_id=envelope.id)
    if request.method == 'POST':
        recipient_name = recipient.full_name
        recipient.delete()
        messages.success(request, f"Removed {recipient_name} from recipients.")
    return redirect('archive:envelope_detail', envelope_id=envelope.id)


from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import json
import io
import os
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

def create_letter(request):
    return render(request, 'create_letter.html')

@csrf_exempt
@require_http_methods(["POST"])
def download_pdf(request):
    try:
        # Parse JSON data
        data = json.loads(request.body)
        title = data.get('title', 'Letter')
        content = data.get('content', '')
        
        if not content:
            return JsonResponse({'error': 'No content provided'}, status=400)
        
        # Create HTML content for PDF
        html_content = create_pdf_html(title, content)
        
        # Generate PDF
        result = io.BytesIO()
        
        # Use pisa with simpler CSS
        pdf = pisa.pisaDocument(
            io.BytesIO(html_content.encode("UTF-8")), 
            result,
            encoding='UTF-8'
        )
        
        if pdf.err:
            logger.error(f"PDF generation error: {pdf.err}")
            return JsonResponse({'error': 'PDF generation failed'}, status=500)
        
        # Return PDF
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{title}.pdf"'
        response['Content-Length'] = len(result.getvalue())
        return response
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return JsonResponse({'error': str(e)}, status=500)

def create_pdf_html(title, content):
    """Create HTML content for PDF with header and footer - SIMPLIFIED CSS"""
    
    # Get the path to static files
    static_path = settings.STATIC_ROOT if settings.STATIC_ROOT else settings.STATICFILES_DIRS[0] if settings.STATICFILES_DIRS else ''
    
    # Header and footer HTML
    header_html = ''
    footer_html = ''
    
    if static_path:
        header_path = os.path.join(static_path, 'images', 'ecologo.png')
        footer_path = os.path.join(static_path, 'images', 'ecologo.png')
        
        if os.path.exists(header_path):
            header_html = f'<img src="file://{header_path}" alt="Header" style="width:100%;display:block;">'
        else:
            header_html = '<div style="padding:20px;background:#1a3c6e;color:white;text-align:center;font-size:24px;font-weight:bold;font-family:Arial,sans-serif;">ECOBANK<br><span style="font-size:12px;font-weight:normal;">The Pan African Bank</span></div>'
        
        if os.path.exists(footer_path):
            footer_html = f'<img src="file://{footer_path}" alt="Footer" style="width:100%;display:block;">'
        else:
            footer_html = '<div style="padding:15px;background:#1a3c6e;color:white;text-align:center;font-size:12px;font-family:Arial,sans-serif;">ECOBANK - The Pan African Bank</div>'
    else:
        header_html = '<div style="padding:20px;background:#1a3c6e;color:white;text-align:center;font-size:24px;font-weight:bold;font-family:Arial,sans-serif;">ECOBANK<br><span style="font-size:12px;font-weight:normal;">The Pan African Bank</span></div>'
        footer_html = '<div style="padding:15px;background:#1a3c6e;color:white;text-align:center;font-size:12px;font-family:Arial,sans-serif;">ECOBANK - The Pan African Bank</div>'
    
    # SIMPLIFIED HTML - NO @page with element() that causes errors
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: 'Times New Roman', Times, serif;
            background: white;
        }}
        .page {{
            width: 100%;
            min-height: 100vh;
            position: relative;
        }}
        .header {{
            width: 100%;
            background: white;
            border-bottom: 1px solid #ddd;
        }}
        .header img {{
            width: 100%;
            display: block;
        }}
        .footer {{
            width: 100%;
            background: white;
            border-top: 1px solid #ddd;
            margin-top: 20px;
        }}
        .footer img {{
            width: 100%;
            display: block;
        }}
        .content {{
            padding: 30px 60px 20px 60px;
            line-height: 1.8;
            font-size: 12pt;
        }}
        .title {{
            font-size: 20px;
            font-weight: bold;
            text-align: center;
            margin-bottom: 30px;
            font-family: 'Times New Roman', Times, serif;
        }}
        .content p {{
            margin-bottom: 10px;
        }}
        .page-break {{
            page-break-after: always;
            border-bottom: 2px dashed #ccc;
            margin: 20px 0;
            padding: 10px;
            text-align: center;
            color: #999;
        }}
    </style>
</head>
<body>
    <div class="page">
        <div class="header">
            {header_html}
        </div>
        <div class="content">
            <div class="title">{title}</div>
            {content}
        </div>
        <div class="footer">
            {footer_html}
        </div>
    </div>
</body>
</html>'''
    
    return html