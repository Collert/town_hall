"""AI features: the Anthropic connection.

Off until staff switch it on in Console > Organization > Backend and give an API key
(saved there, or the ANTHROPIC_API_KEY environment variable). The event planning
assistant that uses it lives in console/planner.py.
"""
from .models import SiteSettings

MODELS = [
    ('claude-opus-5-5', 'Claude Opus 5.5'),
    ('claude-sonnet-5-5', 'Claude Sonnet 5.5'),
]
DEFAULT_MODEL = 'claude-opus-5-5'


def config():
    site = SiteSettings.get_settings()
    model = site.ai_model if site.ai_model in dict(MODELS) else DEFAULT_MODEL
    return {
        'enabled': site.ai_enabled,
        'api_key': site.backend_value('anthropic_api_key', 'ANTHROPIC_API_KEY'),
        'model': model,
    }


def is_enabled(cfg=None):
    """Switched on and has a key: the assistant can actually run."""
    cfg = cfg or config()
    return bool(cfg['enabled'] and cfg['api_key'])


def client(cfg=None):
    import anthropic
    cfg = cfg or config()
    return anthropic.Anthropic(api_key=cfg['api_key'], max_retries=3)
