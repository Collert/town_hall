from django.utils import timezone

from django.http import JsonResponse

from api.models import APIKey, ShiftHeartbeat
from jobs.models import Role


def valid_api_key(request):
    api_key = request.headers.get('X-TownHall-API-Key')
    return APIKey.objects.filter(key=api_key, expires_at__gt=timezone.now()).exists()


def register_heartbeat(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)

    if not valid_api_key(request):
        return JsonResponse({'status': 'error', 'message': 'Invalid API key'}, status=401)

    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Authentication required'}, status=401)

    role_id = request.POST.get('role_id')
    if not role_id:
        return JsonResponse({'status': 'error', 'message': 'Missing role_id'}, status=400)

    try:
        role = Role.objects.get(pk=role_id)
    except Role.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Invalid role_id'}, status=404)

    heartbeat = ShiftHeartbeat(role=role)
    heartbeat.save(user=request.user, role=role)
    return JsonResponse({'status': 'success', 'new_heartbeat': True})