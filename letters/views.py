from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Letter, SignatureRequest, SignatureField
from .utils import generate_letter_pdf
from django.core.files.base import ContentFile
import uuid

import io
import os
import base64
import json
import re
import fitz  
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.core.files.base import ContentFile
from django.conf import settings
from django.utils import timezone
from django.db import models



@login_required
def my_letters(request):
    letters = Letter.objects.filter(created_by=request.user).order_by('-created_at')
    return render(request, 'letters/my_letters.html', {'letters': letters})

@login_required
def create_letter(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        content = request.POST.get('content')
        letter = Letter.objects.create(title=title, content=content, created_by=request.user)
        
        # Generate PDF bytes
        pdf_bytes = generate_letter_pdf(letter)
        
        # Debug: write to a temporary file to see if generation works
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name
        print(f"PDF generated, size={len(pdf_bytes)} bytes, temp file at {tmp_path}")
        
        # Save to model
        filename = f"{letter.id}.pdf"
        letter.pdf_file.save(filename, ContentFile(pdf_bytes))
        letter.save()
        
        # Verify the saved file exists
        if letter.pdf_file and os.path.exists(letter.pdf_file.path):
            print(f"Saved PDF exists at {letter.pdf_file.path}, size={os.path.getsize(letter.pdf_file.path)}")
        else:
            print(f"ERROR: File not saved at {letter.pdf_file.path if letter.pdf_file else 'None'}")
        
        return redirect('letters:manage_letter', letter_id=letter.id)
    return render(request, 'letters/create_letter.html')


# ==================== Helper: Embed signatures into PDF ====================
def embed_signatures_into_pdf(letter, signature_request, post_data):
    """
    Takes the PDF of the letter, reads all fields from post_data (sent by the frontend),
    and embeds signature images, stamp texts, dates, etc.
    Returns the signed PDF as bytes.
    """
    debug_log = r'C:\inetpub\wwwroot\digital_archive\letters_embed.log'
    def log(msg):
        try:
            with open(debug_log, 'a') as f:
                f.write(f"{timezone.now().strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
        except:
            pass

    log("=== embed_signatures_into_pdf START ===")

    # 1. Locate original PDF
    if not letter.pdf_file or not letter.pdf_file.path:
        raise ValueError("Letter PDF file not found")
    original_path = letter.pdf_file.path
    log(f"PDF path: {original_path}")

    # 2. Open PDF
    doc = fitz.open(original_path)
    log(f"PDF opened, pages={len(doc)}")

    # 3. Extract fields from POST data (keys like field_123_type, field_123_page, ...)
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

    # 4. Process each field
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
            # Get signature image data (dataURL) from POST
            sig_key = f'signature_data_{fid}'
            img_data = None
            if sig_key in post_data:
                data_url = post_data.get(sig_key)
                if data_url and data_url.startswith('data:image'):
                    try:
                        _, encoded = data_url.split(',', 1)
                        img_data = base64.b64decode(encoded)
                        log(f"  Got image for field {fid}, size={len(img_data)}")
                    except Exception as e:
                        log(f"  Decoding error: {e}")
            if img_data:
                try:
                    page_obj.insert_image(rect, stream=io.BytesIO(img_data))
                    log(f"  Image inserted")
                except Exception as e:
                    log(f"  Image insertion error: {e}")
            else:
                # Debug: draw a red rectangle
                page_obj.draw_rect(rect, color=(1,0,0), width=2)
                log(f"  No image, drew rectangle")

            # Stamp text (name + date)
            stamp_key = f'stamp_text_{fid}'
            stamp_text = post_data.get(stamp_key, '')
            if stamp_text:
                # Place stamp at bottom of the field
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

    # 5. Save PDF to memory
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)
    log(f"Saved PDF size: {len(output.getvalue())} bytes")
    log("=== embed_signatures_into_pdf END ===")
    return output.read()

# ==================== Manage Letter (Creator) ====================
@login_required
def manage_letter(request, letter_id):
    letter = get_object_or_404(Letter, id=letter_id, created_by=request.user)
    # Get all signature requests for this letter (recipients)
    recipients = letter.signature_requests.all()
    context = {
        'letter': letter,
        'pdf_url': letter.pdf_file.url,
        'recipients': recipients,
        'can_add_recipients': True,  # creator can add/remove
    }
    return render(request, 'letters/manage_letter.html', context)

# ==================== Public Signing Portal ====================
def sign_letter(request, token):
    sig_request = get_object_or_404(SignatureRequest, signing_token=token)
    if sig_request.status != 'pending':
        return render(request, 'letters/already_signed.html', {'status': sig_request.status})
    letter = sig_request.letter
    # Get fields assigned to this signature request
    fields = sig_request.fields.all()
    context = {
        'letter': letter,
        'signature_request': sig_request,
        'pdf_url': letter.pdf_file.url,
        'fields': fields,
        'recipient_name': sig_request.recipient.get_full_name() or sig_request.recipient.username,
    }
    return render(request, 'letters/sign_portal.html', context)

# ==================== API: Add Signature Field ====================
@login_required
def add_signature_field(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'})
    try:
        data = json.loads(request.body)
        field_type = data.get('type')
        page = data.get('page')
        x = data.get('x')
        y = data.get('y')
        width = data.get('width', 150)
        height = data.get('height', 60)
        signature_request_id = data.get('signature_request_id')

        sig_request = get_object_or_404(SignatureRequest, id=signature_request_id, letter__created_by=request.user)
        field = SignatureField.objects.create(
            signature_request=sig_request,
            field_type=field_type,
            page_number=page,
            x_position=x,
            y_position=y,
            width=width,
            height=height,
        )
        return JsonResponse({'success': True, 'field_id': field.id})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

# ==================== API: Add Recipient ====================
@login_required
def add_recipient(request, letter_id):
    letter = get_object_or_404(Letter, id=letter_id, created_by=request.user)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'})
    try:
        data = json.loads(request.body)
        user_id = data.get('user_id')
        user = get_object_or_404(User, id=user_id)
        # Check if already a recipient
        if SignatureRequest.objects.filter(letter=letter, recipient=user).exists():
            return JsonResponse({'success': False, 'error': 'User is already a recipient'})
        # Determine next signing order
        max_order = letter.signature_requests.aggregate(models.Max('signing_order'))['signing_order__max'] or 0
        sig_request = SignatureRequest.objects.create(
            letter=letter,
            recipient=user,
            status='pending',
            signing_order=max_order + 1,
        )
        return JsonResponse({'success': True, 'recipient_id': sig_request.id, 'name': user.get_full_name() or user.username, 'email': user.email})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

# ==================== API: Remove Recipient (Delegation) ====================
@login_required
def remove_recipient(request, request_id):
    sig_request = get_object_or_404(SignatureRequest, id=request_id, letter__created_by=request.user)
    if sig_request.status != 'pending':
        return JsonResponse({'success': False, 'error': 'Only pending recipients can be removed'})
    sig_request.delete()
    return JsonResponse({'success': True})

# ==================== API: Submit Signatures (Finalize) ====================
def submit_signatures(request, token):
    sig_request = get_object_or_404(SignatureRequest, signing_token=token)
    if sig_request.status != 'pending':
        return JsonResponse({'success': False, 'error': 'Already signed or declined'})
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'})

    # Merge POST and FILES (if any)
    post_data = request.POST.copy()
    post_data.update(request.FILES)

    try:
        # Embed signatures into the letter's PDF
        signed_pdf_bytes = embed_signatures_into_pdf(sig_request.letter, sig_request, post_data)

        # Save the signed PDF as a new file in the letter
        filename = f"letters/signed/{sig_request.letter.id}_{sig_request.id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        sig_request.letter.signed_pdf.save(filename, ContentFile(signed_pdf_bytes))

        # Mark this signature request as signed
        sig_request.status = 'signed'
        sig_request.signed_at = timezone.now()
        sig_request.save()

        # If all recipients have signed, the letter is fully signed (optional)
        all_signed = all(r.status == 'signed' for r in sig_request.letter.signature_requests.all())
        if all_signed:
            # You could set a flag or send a notification
            pass

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
    




from django.contrib.auth import get_user_model
User = get_user_model()

def search_users(request):
    q = request.GET.get('q', '')
    if len(q) < 2:
        return JsonResponse([], safe=False)
    users = User.objects.filter(username__icontains=q)[:20]
    data = [{'id': u.id, 'name': u.get_full_name() or u.username, 'email': u.email} for u in users]
    return JsonResponse(data, safe=False)