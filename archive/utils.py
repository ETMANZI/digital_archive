from .models import ArchiveItem
from django.utils import timezone
from .models import AuditLog



def get_filtered_items_queryset(request):
    """Apply collection, tag, search filters from request.GET."""
    queryset = ArchiveItem.objects.filter(is_deleted=False)
    
    collection_id = request.GET.get('collection')
    if collection_id:
        queryset = queryset.filter(collection_id=collection_id)
    
    tag_id = request.GET.get('tag')
    if tag_id:
        queryset = queryset.filter(tags__id=tag_id)
    
    search = request.GET.get('search')
    if search:
        queryset = queryset.filter(title__icontains=search) | queryset.filter(description__icontains=search)
    
    return queryset.distinct()







# def create_audit_log(request, action, archive_item=None, old_value=None, new_value=None):
#     """Helper to create an audit log entry from the request."""
#     user = request.user if request.user.is_authenticated else None
#     ip_address = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR')
#     user_agent = request.META.get('HTTP_USER_AGENT', '')[:200]  

#     return AuditLog.objects.create(
#         user=user,
#         archive_item=archive_item,
#         action=action,
#         old_value=old_value,
#         new_value=new_value,
#         ip_address=ip_address,
#         user_agent=user_agent,
#         timestamp=timezone.now()
#     )


def create_audit_log(request, action, archive_item=None, old_value=None, new_value=None):
    """Helper function to create audit logs with proper formatting."""
    from archive.models import AuditLog
    
    # For user creation, ensure we have all user details
    if action == AuditLog.ACTION_CREATE and new_value and 'username' in new_value:
        if 'type' not in new_value:
            new_value['type'] = 'user_creation'
        if 'roles_display' not in new_value and 'roles' in new_value:
            new_value['roles_display'] = ', '.join(new_value['roles']) if new_value['roles'] else 'No role'
    
    # For permission changes, ensure we have username and role display
    if action == AuditLog.ACTION_CHANGE_PERMISSION and old_value and new_value:
        if 'username' not in old_value:
            old_value['username'] = getattr(archive_item, 'username', None) if archive_item else None
        if 'username' not in new_value:
            new_value['username'] = getattr(archive_item, 'username', None) if archive_item else None
        if 'roles_display' not in old_value and 'roles' in old_value:
            old_value['roles_display'] = ', '.join(old_value['roles']) if old_value['roles'] else 'No role'
        if 'roles_display' not in new_value and 'roles' in new_value:
            new_value['roles_display'] = ', '.join(new_value['roles']) if new_value['roles'] else 'No role'
    
    return AuditLog.objects.create(
        user=request.user,
        archive_item=archive_item,
        action=action,
        old_value=old_value,
        new_value=new_value,
        ip_address=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', '')
    )






# utils/pdf_signer.py
from pyhanko.sign import signers, fields
from pyhanko.sign.fields import SigFieldSpec, append_signature_field
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign.timestamps import TimeStamper
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
import tempfile
import os

class PDFDigitalSigner:
    def __init__(self, pfx_file_path, pfx_password):
        """Initialize with PKCS#12 certificate (.pfx or .p12 file)"""
        self.pfx_file_path = pfx_file_path
        self.pfx_password = pfx_password
        self.signer = None
    
    def load_certificate(self):
        """Load the digital certificate"""
        with open(self.pfx_file_path, 'rb') as f:
            pfx_data = f.read()
        
        self.signer = signers.SimpleSigner.load_pkcs12(
            pfx_data, 
            passphrase=self.pfx_password.encode()
        )
        return self.signer
    
    def sign_pdf_with_visible_signature(self, input_pdf_path, output_pdf_path, 
                                         signature_image_path=None,
                                         page_number=0,
                                         x=100, y=100, 
                                         width=200, height=100,
                                         reason="Loan Approval",
                                         location="Kigali, Rwanda",
                                         contact_info=None):
        """
        Sign a PDF with a visible signature field
        
        Args:
            input_pdf_path: Path to input PDF
            output_pdf_path: Path for signed output
            signature_image_path: Path to signature image (PNG with transparent background)
            page_number: Page number (0-indexed)
            x, y: Coordinates for signature box
            width, height: Size of signature box
            reason: Reason for signing
            location: Location where signing occurs
            contact_info: Contact information of signer
        """
        if not self.signer:
            self.load_certificate()
        
        # Open the PDF
        with open(input_pdf_path, 'rb') as inf:
            w = IncrementalPdfFileWriter(inf)
            
            # Prepare signature field specifications
            field_spec = SigFieldSpec(
                sig_field_name="Signature",
                on_page=page_number,
                box=(x, y, x + width, y + height)
            )
            
            # Create visible signature appearance
            if signature_image_path:
                # Create a signature appearance with image
                from pyhanko.sign.visible import build_visible_signature
                from pyhanko.sign.visible import VisibleSignatureSettings
                from pyhanko.pdf_utils.layout import SimpleTextLayout, TextBox, AppearanceSettings
                
                v_sig_settings = VisibleSignatureSettings(
                    image=signature_image_path,
                    # Optionally add text
                    text="Digitally signed by",
                    text_box=TextBox("Approved", x, y, width, height)
                )
            else:
                v_sig_settings = None
            
            # Sign the PDF
            with open(output_pdf_path, 'wb') as outf:
                signers.sign_pdf(
                    w,
                    signature_meta=signers.PDFSignatureMetadata(
                        field_name="Signature",
                        location=location,
                        reason=reason,
                        contact_info=contact_info,
                    ),
                    signer=self.signer,
                    output=outf,
                    appearance_text=v_sig_settings
                )
        
        return output_pdf_path