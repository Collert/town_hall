from time import timezone

from django.http import JsonResponse
from django.shortcuts import render

from api.models import APIKey, ShiftHeartbeat

def valid_api_key(request):
    api_key = request.headers.get('X-TownHall-API-Key')
    return APIKey.objects.filter(key=api_key, expires_at__gt=timezone.now()).exists()

def register_heartbeat(request):
    if request.method == 'POST' and valid_api_key(request):
        role_id = request.POST.get('role_id')
        heartbeat, created = ShiftHeartbeat.objects.get_or_create(role_id=role_id)
        # Here you would typically validate the role_id and create a ShiftHeartbeat
        # For simplicity, we'll just return a success response
        return JsonResponse({'status': 'success', 'new_heartbeat': created})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)