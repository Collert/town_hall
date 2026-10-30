"""
URL configuration for town_hall project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf.urls.i18n import i18n_patterns
from django.conf import settings
from django.views.static import serve

from base import push_views, views as base_views

urlpatterns = [
    # URL for switching language
    path('i18n/', include('django.conf.urls.i18n')),
    # Claude connector (MCP) and its OAuth endpoints: fixed paths, no language prefix
    path('', include('mcp_server.urls')),
    # Icons browsers and home screens fetch from the site root
    path('favicon.ico', base_views.favicon, name='favicon'),
    path('site.webmanifest', base_views.web_manifest, name='web_manifest'),
    # Installable app and web push (base/push.py); the service worker must sit at the root
    path('sw.js', push_views.service_worker, name='service_worker'),
    path('offline/', push_views.offline, name='offline'),
    path('push/subscribe/', push_views.subscribe, name='push_subscribe'),
    path('push/unsubscribe/', push_views.unsubscribe, name='push_unsubscribe'),
    path('push/renew/', push_views.renew, name='push_renew'),
    # listmonk messenger: listmonk posts campaign messages here (Communication > Notifications)
    path('push/listmonk/', push_views.listmonk_messenger, name='push_listmonk'),
]

# URLs that should be prefixed with language code
urlpatterns += i18n_patterns(
    path('admin/', admin.site.urls),
    # Add your app URLs here
    path('', include('base.urls')),
    path('opportunities/', include('events.urls')),
    path('training/', include('education.urls')),
    path('jobs/', include('jobs.urls')),
    path('console/', include('console.urls')),
    prefix_default_language=True,
)

# Serve local media files; with S3_BUCKET set, uploads are served from the bucket instead.
if not settings.USE_S3_MEDIA:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]
