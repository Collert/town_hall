from django.http import JsonResponse

def get_user_level_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Unauthorized'}, status=401)
    
    profile = request.user.profile
    level = profile.level()
    
    if level:
        return JsonResponse({
            'level_name': level.name,
            'benefits': level.benefits.split(',') if level.benefits else []
        })
    else:
        return JsonResponse({'level_name': 'Unranked', 'benefits': []})