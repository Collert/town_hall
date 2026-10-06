from datetime import datetime, timedelta

from django.contrib import messages
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from base.email import notify
from base.models import Notification
from education.models import ExternalCertificate, UserCertification

from ..decorators import staff_required
from ..forms import CertificateForm
from ..utils import CERTIFICATE_ICONS, with_custom_icon


@staff_required
def certificate_list(request):
    certificates = ExternalCertificate.objects.annotate(
        active_count=Count('usercertification', filter=Q(usercertification__verified=True), distinct=True),
        pending_count=Count('usercertification', filter=Q(usercertification__verified=False, usercertification__rejected=False), distinct=True),
    ).order_by('name')
    query = request.GET.get('q', '').strip()
    validity = request.GET.get('validity', '')
    if query:
        certificates = certificates.filter(Q(name__icontains=query) | Q(issuer__icontains=query) | Q(description__icontains=query))
    if validity == 'expiring':
        certificates = certificates.filter(expires_after_days__isnull=False)
    elif validity == 'permanent':
        certificates = certificates.filter(expires_after_days__isnull=True)
    context = {'certificates': certificates, 'query': query, 'validity': validity}
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'console/partials/certificate_grid.html', context)
    return render(request, 'console/certificate_list.html', context)


@staff_required
def certificate_edit(request, cert_id=None):
    certificate = get_object_or_404(ExternalCertificate, pk=cert_id) if cert_id else None

    if request.method == 'POST':
        form = CertificateForm(with_custom_icon(request.POST), instance=certificate)
        docs = [d.strip().replace(',', ';') for d in request.POST.getlist('docs') if d.strip()]
        if form.is_valid():
            certificate = form.save(commit=False)
            certificate.docs_list = ', '.join(docs)
            certificate.save()
            messages.success(request, _('Certificate "%(name)s" saved.') % {'name': certificate.name})
            return redirect('console_certificates')
        messages.error(request, _('Please fix the highlighted fields.'))
    else:
        form = CertificateForm(instance=certificate, initial={} if certificate else {'icon': CERTIFICATE_ICONS[0]})
        docs = certificate.required_docs if certificate else []

    return render(request, 'console/certificate_edit.html', {
        'form': form,
        'certificate': certificate,
        'docs': docs,
        'icons': CERTIFICATE_ICONS,
        'current_icon': form['icon'].value(),
        'holder_count': certificate.usercertification_set.filter(verified=True).count() if certificate else 0,
    })


@staff_required
def doc_row(request):
    return render(request, 'console/partials/doc_row.html', {'doc': ''})


@staff_required
@require_POST
def certificate_delete(request, cert_id):
    certificate = get_object_or_404(ExternalCertificate, pk=cert_id)
    if certificate.usercertification_set.exists():
        messages.error(request, _('Volunteers have submitted this certificate, so it can\'t be deleted.'))
        return redirect('console_certificate_edit', cert_id=certificate.pk)
    certificate.delete()
    messages.success(request, _('Certificate deleted.'))
    return redirect('console_certificates')


def _pending():
    return (UserCertification.objects.filter(verified=False, rejected=False, files__isnull=False)
            .select_related('user', 'user__profile', 'certificate')
            .annotate(file_count=Count('files', distinct=True), submitted_at=Max('files__uploaded_at'))
            .order_by('submitted_at'))


@staff_required
def verification_list(request):
    pending = list(_pending())
    urgent_before = timezone.now() - timedelta(days=3)
    for item in pending:
        item.urgent = item.submitted_at and item.submitted_at < urgent_before
    recent = (UserCertification.objects.filter(Q(verified=True) | Q(rejected=True), reviewed_at__isnull=False)
              .select_related('user', 'certificate').order_by('-reviewed_at')[:6])
    return render(request, 'console/verification_list.html', {
        'pending': pending,
        'recent': recent,
        'pending_count': len(pending),
        'verified_count': UserCertification.objects.filter(verified=True).count(),
    })


def _email_review(user_cert, trigger, link):
    notify(trigger, user_cert.user, {
        'certificate': user_cert.certificate.name, 'notes': user_cert.admin_notes, 'link': link,
    })


@staff_required
def verification_review(request, user_cert_id):
    user_cert = get_object_or_404(UserCertification.objects.select_related('user', 'certificate'), pk=user_cert_id)
    certificate = user_cert.certificate

    if request.method == 'POST':
        action = request.POST.get('action')
        user_cert.admin_notes = request.POST.get('admin_notes', '').strip()
        user_cert.reviewed_by = request.user
        user_cert.reviewed_at = timezone.now()
        link = request.build_absolute_uri(reverse('submit_certificate', args=[certificate.pk]))

        if action == 'approve':
            issue_date = _parse_date(request.POST.get('issue_date')) or timezone.localdate()
            expiry = _parse_date(request.POST.get('expiration_date'))
            if not expiry and certificate.expires_after_days:
                expiry = issue_date + timedelta(days=certificate.expires_after_days)
            user_cert.issue_date = issue_date
            user_cert.expiration_date = (
                timezone.make_aware(datetime.combine(expiry, datetime.max.time().replace(microsecond=0)))
                if expiry else None
            )
            user_cert.verified, user_cert.rejected = True, False
            user_cert.save()
            Notification.objects.create(
                user=user_cert.user, link=link,
                message=_('Your %(cert)s certificate was verified.') % {'cert': certificate.name},
            )
            _email_review(user_cert, 'certificate_approved', link)
            messages.success(request, _('%(cert)s approved for %(name)s.') % {
                'cert': certificate.name, 'name': user_cert.user.get_full_name() or user_cert.user.username})
        elif action == 'reject':
            user_cert.verified, user_cert.rejected = False, True
            user_cert.save()
            Notification.objects.create(
                user=user_cert.user, link=link,
                message=_('Your %(cert)s documents need another look. Please re-upload.') % {'cert': certificate.name},
            )
            _email_review(user_cert, 'certificate_rejected', link)
            messages.info(request, _('Submission rejected. The volunteer has been notified.'))
        else:
            user_cert.save(update_fields=['admin_notes'])
            messages.success(request, _('Notes saved.'))
            return redirect('console_verification_review', user_cert_id=user_cert.pk)

        next_item = _pending().exclude(pk=user_cert.pk).first()
        if next_item and request.POST.get('continue'):
            return redirect('console_verification_review', user_cert_id=next_item.pk)
        return redirect('console_verifications')

    files = []
    for f in user_cert.files.order_by('uploaded_at'):
        name = f.file.name.rsplit('/', 1)[-1]
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        try:
            size = f.file.size
        except (OSError, ValueError):
            size = None
        files.append({
            'obj': f, 'name': name, 'size': size,
            'icon': 'picture_as_pdf' if ext == 'pdf' else 'image' if ext in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'heic') else 'description',
            'is_image': ext in ('jpg', 'jpeg', 'png', 'gif', 'webp'),
        })

    issue_default = user_cert.issue_date or timezone.localdate()
    expiry_default = (timezone.localtime(user_cert.expiration_date).date() if user_cert.expiration_date
                      else issue_default + timedelta(days=certificate.expires_after_days) if certificate.expires_after_days
                      else None)
    return render(request, 'console/verification_review.html', {
        'user_cert': user_cert,
        'certificate': certificate,
        'files': files,
        'issue_default': issue_default,
        'expiry_default': expiry_default,
        'has_next': _pending().exclude(pk=user_cert.pk).exists(),
    })


def _parse_date(value):
    try:
        return datetime.strptime(value or '', '%Y-%m-%d').date()
    except ValueError:
        return None
