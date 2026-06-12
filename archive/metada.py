from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import ArchiveItem, MetadataField
import json

@require_http_methods(['GET'])
def api_metadata_list(request, item_id):
    try:
        item = ArchiveItem.objects.get(id=item_id, is_deleted=False)
    except ArchiveItem.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)
    
    metadata = item.custom_metadata.all()
    data = [{
        'id': m.id,
        'field_name': m.field_name,
        'field_value': m.field_value,
        'field_type': m.field_type,
    } for m in metadata]
    return JsonResponse({'metadata': data}, status=200)


@csrf_exempt
@require_http_methods(['POST'])
def api_metadata_upsert(request, item_id):
    try:
        item = ArchiveItem.objects.get(id=item_id, is_deleted=False)
    except ArchiveItem.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    field_name = data.get('field_name')
    field_value = data.get('field_value')
    field_type = data.get('field_type', MetadataField.FIELD_TYPE_STRING)
    
    if not field_name or field_value is None:
        return JsonResponse({'error': 'field_name and field_value are required'}, status=400)
    
    if field_type not in [choice[0] for choice in MetadataField.TYPE_CHOICES]:
        field_type = MetadataField.FIELD_TYPE_STRING
    
    # Upsert
    metadata, created = MetadataField.objects.update_or_create(
        archive_item=item,
        field_name=field_name,
        defaults={'field_value': field_value, 'field_type': field_type}
    )
    
    return JsonResponse({
        'id': metadata.id,
        'field_name': metadata.field_name,
        'field_value': metadata.field_value,
        'field_type': metadata.field_type,
        'created': created
    }, status=200)




@csrf_exempt
@require_http_methods(['DELETE'])
def api_metadata_delete(request, item_id, metadata_id):
    try:
        item = ArchiveItem.objects.get(id=item_id, is_deleted=False)
    except ArchiveItem.DoesNotExist:
        return JsonResponse({'error': 'Item not found'}, status=404)
    
    try:
        metadata = item.custom_metadata.get(id=metadata_id)
        metadata.delete()
        return JsonResponse({'success': True}, status=200)
    except MetadataField.DoesNotExist:
        return JsonResponse({'error': 'Metadata field not found'}, status=404)