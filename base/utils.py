import hashlib
import urllib.error
import urllib.request
from contextlib import nullcontext
from urllib.parse import urlencode

from django.utils import formats, timezone, translation


def short_datetime(value, lang=None):
    """"October 5, 9:30 AM" / "5 жовтня, 09:30": date and time in the language's own order and clock.

    Uses the active language unless ``lang`` is given (e.g. an email recipient's).
    """
    value = timezone.localtime(value)
    with translation.override(lang) if lang else nullcontext():
        return f"{formats.date_format(value, 'MONTH_DAY_FORMAT')}, {formats.time_format(value)}"


def time_range(start, end):
    """"9:00 AM – 5:00 PM" / "09:00 – 17:00" in the active language."""
    return f"{formats.time_format(timezone.localtime(start))} – {formats.time_format(timezone.localtime(end))}"


def slot_when(start, end, event_start):
    """A shift's times for the event page: "9:00 AM – 5:00 PM", with the date only when
    the shift falls on another day than the event starts ("Sat, Oct 25 · 9:00 AM – …"),
    and the end's date too when the shift runs past midnight."""
    start, end = timezone.localtime(start), timezone.localtime(end)
    day = timezone.localtime(event_start).date()
    first = formats.time_format(start)
    if start.date() != day:
        first = f"{formats.date_format(start, 'D, M j')} · {first}"
    last = formats.time_format(end)
    if end.date() != start.date():
        last = f"{formats.date_format(end, 'D, M j')} · {last}"
    return f"{first} – {last}"


def qr_svg(data):
    """Inline SVG QR code for `data`. It's drawn in `currentColor`, so the surrounding CSS
    sets its color (the text color, which keeps contrast in light and dark mode)."""
    import io
    import re

    import qrcode
    import qrcode.image.svg

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    buffer = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    svg = buffer.getvalue().decode('utf-8')
    svg = re.sub(r'<\?xml[^>]*\?>\s*', '', svg)
    svg = re.sub(r'\s(?:width|height|fill_color|back_color)="[^"]*"', '', svg, count=4)
    svg = svg.replace(' id="qr-path"', '').replace('fill="#000000"', 'fill="currentColor"')
    return svg.replace('<svg ', '<svg aria-hidden="true" ', 1)


def gravatar_url(email, size=160, default='mp'):
    """Gravatar image URL for an email address (SHA-256 hash, per Gravatar's current API)."""
    digest = hashlib.sha256(email.strip().lower().encode('utf-8')).hexdigest()
    return f'https://gravatar.com/avatar/{digest}?{urlencode({"s": size, "d": default})}'


def gravatar_exists(email):
    """True if the email has a Gravatar; None if Gravatar couldn't be reached."""
    request = urllib.request.Request(gravatar_url(email, size=1, default='404'), method='HEAD')
    try:
        with urllib.request.urlopen(request, timeout=5):
            return True
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except (urllib.error.URLError, OSError):
        return None
