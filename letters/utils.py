import os
import base64
from io import BytesIO
from django.template.loader import render_to_string
from django.conf import settings
from xhtml2pdf import pisa

def generate_letter_pdf(letter):
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'ecobank_logo.png')
    logo_data_uri = ''
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as f:
            logo_base64 = base64.b64encode(f.read()).decode('utf-8')
            logo_data_uri = f'data:image/png;base64,{logo_base64}'

    context = {
        'title': letter.title,
        'content': letter.content,
        'created_by': letter.created_by,
        'logo_data_uri': logo_data_uri,
    }
    html_string = render_to_string('letters/letter_pdf_template.html', context)
    result = BytesIO()
    pdf = pisa.pisaDocument(BytesIO(html_string.encode('UTF-8')), result)
    if pdf.err:
        raise Exception('PDF generation error')
    return result.getvalue()




