"""The tools Town Hall offers AI apps through MCP (mcp_server/views.py).

Each module registers its tools with ``@tool``. A tool is a function ``f(ctx, **args)``
that returns something JSON-serializable, or raises ToolError with a message the AI can
act on. Arguments are checked against the tool's schema before the call.

What the tools may change is deliberately limited to event planning: events, their role
slots, areas, invitations and task lists, role templates, and quizzes. Nothing is ever
deleted, people's profiles are read-only, and training lessons are read-only (only the
organization knows how it really operates, so people write that material).
"""
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from console.utils import lang_field


class ToolError(Exception):
    pass


@dataclass
class Tool:
    name: str
    title: str
    description: str
    schema: dict
    func: object
    read_only: bool = False
    open_world: bool = False  # reaches people or services outside Town Hall
    idempotent: bool = False

    def definition(self):
        return {
            'name': self.name,
            'title': self.title,
            'description': self.description,
            'inputSchema': self.schema,
            'annotations': {
                'title': self.title,
                'readOnlyHint': self.read_only,
                'destructiveHint': False,  # no tool deletes or overwrites people's records
                'idempotentHint': self.read_only or self.idempotent,
                'openWorldHint': self.open_world,
            },
        }


@dataclass
class Context:
    user: object
    base_url: str
    extra: dict = field(default_factory=dict)


REGISTRY = {}


def tool(name, title, description, properties=None, required=(), *, read_only=False, open_world=False, idempotent=False):
    def register(func):
        REGISTRY[name] = Tool(name, title, description, obj(properties or {}, required), func,
                              read_only=read_only, open_world=open_world, idempotent=idempotent)
        return func
    return register


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def string(description, **extra):
    return {'type': 'string', 'description': description, **extra}


def integer(description, **extra):
    return {'type': 'integer', 'description': description, **extra}


def boolean(description):
    return {'type': 'boolean', 'description': description}


def array(items, description, **extra):
    return {'type': 'array', 'items': items, 'description': description, **extra}


TIME_FORMAT = 'Local time in the organization\'s time zone, YYYY-MM-DDTHH:MM'


def time(description):
    return string(f'{description}. {TIME_FORMAT}')


def validate(schema, value, path='arguments'):
    """Check ``value`` against the subset of JSON Schema the tools use. Raises ToolError."""
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict):
            raise ToolError(f'{path} must be an object.')
        props = schema.get('properties', {})
        for key in schema.get('required', []):
            if value.get(key) is None:
                raise ToolError(f'{path}.{key} is required.')
        for key, item in value.items():
            if key not in props:
                if schema.get('additionalProperties') is False:
                    raise ToolError(f'{path} has an unknown field "{key}". Allowed: {", ".join(props) or "none"}.')
                continue
            if item is not None:
                validate(props[key], item, f'{path}.{key}')
    elif kind == 'array':
        if not isinstance(value, list):
            raise ToolError(f'{path} must be a list.')
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            raise ToolError(f'{path} can have at most {schema["maxItems"]} items.')
        if 'minItems' in schema and len(value) < schema['minItems']:
            raise ToolError(f'{path} needs at least {schema["minItems"]} items.')
        for index, item in enumerate(value):
            validate(schema['items'], item, f'{path}[{index}]')
    elif kind == 'string':
        if not isinstance(value, str):
            raise ToolError(f'{path} must be a string.')
        if 'enum' in schema and value not in schema['enum']:
            raise ToolError(f'{path} must be one of: {", ".join(schema["enum"])}.')
        if 'maxLength' in schema and len(value) > schema['maxLength']:
            raise ToolError(f'{path} can be at most {schema["maxLength"]} characters.')
    elif kind == 'integer':
        if isinstance(value, bool) or not (isinstance(value, int) or (isinstance(value, float) and value.is_integer())):
            raise ToolError(f'{path} must be a whole number.')
        if 'minimum' in schema and value < schema['minimum']:
            raise ToolError(f'{path} must be at least {schema["minimum"]}.')
    elif kind == 'boolean':
        if not isinstance(value, bool):
            raise ToolError(f'{path} must be true or false.')


def clean_args(schema, args):
    """Whole-number floats become ints (some clients send 3.0)."""
    out = {}
    for key, value in args.items():
        prop = schema.get('properties', {}).get(key, {})
        if prop.get('type') == 'integer' and isinstance(value, float):
            value = int(value)
        elif prop.get('type') == 'array' and prop['items'].get('type') == 'integer' and isinstance(value, list):
            value = [int(v) for v in value]
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# Helpers shared by the tool modules
# ---------------------------------------------------------------------------

def default_language():
    return settings.LANGUAGE_CODE


def set_translated(instance, name, value):
    """Write a translated field in the organization's default language."""
    setattr(instance, lang_field(name, default_language()), value)


def text(value, limit=None):
    value = (value or '').strip()
    return value[:limit] if limit else value


def parse_time(value, name='time'):
    """A local 'YYYY-MM-DDTHH:MM' (an ISO time with an offset works too) as an aware datetime."""
    try:
        parsed = datetime.fromisoformat((value or '').strip().replace('Z', '+00:00'))
    except ValueError:
        raise ToolError(f'{name} must look like 2026-05-01T09:30, got {value!r}.')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def local(dt):
    """'Sat 2026-05-02 09:30' in the organization's time zone."""
    return timezone.localtime(dt).strftime('%a %Y-%m-%d %H:%M') if dt else None


def person(user):
    return {'user_id': user.pk, 'name': user.get_full_name() or user.username}


def get_or_error(queryset, pk, label):
    item = queryset.filter(pk=pk).first()
    if item is None:
        raise ToolError(f'No {label} with id {pk}.')
    return item


# Importing the modules registers their tools.
from . import events, people, roles, tasks, training  # noqa: E402,F401
