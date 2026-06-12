import re
from django.core.exceptions import ValidationError

def validate_file_naming_convention(file_name):
    """
    Validates file name against format:
    [Obligor Name]_[Document Type]_[YYYY-MM-DD]_[vX.Y][.extension]
    Example: ABC Holdings Ltd_Offer Letter_2026-01-15_v1.0.pdf
    """
    # ========== CHECK FOR DUPLICATE EXTENSIONS ==========
    common_exts = ['.pdf', '.docx', '.jpg', '.png']
    for ext in common_exts:
        if file_name.lower().endswith(ext + ext):
            # Convert .pdf.pdf -> .pdf for a helpful error message
            correct_name = file_name[:-len(ext)]
            raise ValidationError(
                f'File name "{file_name}" has a duplicate extension.\n'
                f'Please rename the file to remove the extra "{ext}".\n\n'
                f'Correct example: {correct_name}'
            )
    # ==================================================

    # Split extension (only the last dot)
    if '.' in file_name:
        base_name, extension = file_name.rsplit('.', 1)
        extension = '.' + extension
    else:
        base_name = file_name
        extension = ''

    # Pattern: anything_anything_YYYY-MM-DD_vX.Y
    pattern = r'^(.+)_(.+)_(\d{4}-\d{2}-\d{2})_(v\d+\.\d+)$'
    match = re.match(pattern, base_name)

    if not match:
        raise ValidationError(
            f'File name "{file_name}" is not in the required format.\n\n'
            'Expected format: [Obligor Name]_[Document Type]_[YYYY-MM-DD]_[v1.0][.extension]\n'
            'Example: ABC Holdings Ltd_Offer Letter_2026-01-15_v1.0\n\n'
            'Rules:\n'
            '- Use underscores (_) as separators (not spaces, dashes, or other characters)\n'
            '- Date must be YYYY-MM-DD\n'
            '- Version must start with v, then number, then dot, then number (e.g., v1.0, v2.3)\n'
            '- Extension is optional (e.g., .pdf, .docx)'
        )

    obligor_name, document_type, date_str, version_str = match.groups()
    return {
        'obligor_name': obligor_name.strip(),
        'document_type': document_type.strip(),
        'date': date_str,
        'version': version_str,
        'extension': extension
    }

def generate_file_name(obligor_name, document_type, date, version="v1.0", extension="pdf"):
    return f"{obligor_name}_{document_type}_{date}_{version}.{extension}"