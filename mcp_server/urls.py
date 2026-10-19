"""MCP connector URLs. Mounted at the site root, outside the language prefix, because
AI clients build these paths from the discovery metadata."""
from django.urls import path

from . import oauth, views

urlpatterns = [
    path('mcp', views.mcp, name='mcp'),
    path('.well-known/oauth-protected-resource', oauth.protected_resource_metadata),
    path('.well-known/oauth-protected-resource/mcp', oauth.protected_resource_metadata, name='mcp_resource_metadata'),
    path('.well-known/oauth-authorization-server', oauth.authorization_server_metadata, name='mcp_oauth_metadata'),
    # ChatGPT and other clients probe these variants too (RFC 8414 path insertion, OIDC discovery).
    path('.well-known/oauth-authorization-server/mcp', oauth.authorization_server_metadata),
    path('.well-known/openid-configuration', oauth.authorization_server_metadata),
    path('.well-known/openid-configuration/mcp', oauth.authorization_server_metadata),
    path('mcp/.well-known/openid-configuration', oauth.authorization_server_metadata),
    path('oauth/register', oauth.register, name='mcp_oauth_register'),
    path('oauth/authorize', oauth.authorize, name='mcp_oauth_authorize'),
    path('oauth/token', oauth.token, name='mcp_oauth_token'),
    path('oauth/revoke', oauth.revoke, name='mcp_oauth_revoke'),
]
