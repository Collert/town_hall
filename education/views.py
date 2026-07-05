from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.views.decorators.http import require_POST
from .models import TrainingModule, TrainingLesson, TrainingModuleCompletion, Quiz, ExternalCertificate, UserCertification, UserCertificationFile
from django.db.models import Count, Exists, OuterRef


def get_module_sidebar_context(module):
    """Build the shared sidebar context for module_layout templates."""
    lessons = module.lessons.all().order_by('order')
    quizzes = module.quizzes.all()
    total_units = lessons.count() + quizzes.count() + 1  # +1 for Introduction
    return {
        'module': module,
        'lessons': lessons,
        'quizzes': quizzes,
        'total_units': total_units,
    }


def training_directory(request):
    user = request.user
    
    last_viewed = None
    if user.is_authenticated and hasattr(user, 'profile'):
        last_viewed = user.profile.last_viewed_training_module
        if last_viewed and not last_viewed.published:
            last_viewed = None  # Ignore if the last viewed module is unpublished
        if last_viewed.completion_percentage_for_user(user) >= 100:
            last_viewed = None  # Ignore if the last viewed module is already completed

    # Order modules by number of roles they are required for
    impactful_modules = TrainingModule.objects.annotate(
        role_count=Count('roles')
    ).order_by('-role_count').filter(published=True)

    if last_viewed:
        impactful_modules = impactful_modules.exclude(id=last_viewed.id)
        current_training_modules = list(impactful_modules[:1])
        last_viewed.user_progress = last_viewed.completion_percentage_for_user(user) if user.is_authenticated else 0
    else:
        current_training_modules = list(impactful_modules[:3])

    for mod in current_training_modules:
        mod.user_progress = mod.completion_percentage_for_user(user) if user.is_authenticated else 0

    if request.GET.get('topic'):
        topic = request.GET['topic']
        available_modules = impactful_modules.filter(topic__name__icontains=topic)
    else:
        available_modules = impactful_modules.all()

    if user.is_authenticated:
        available_modules = available_modules.annotate(
            is_completed=Exists(
                TrainingModuleCompletion.objects.filter(
                    training_module=OuterRef('pk'),
                    user=user
                )
            )
        ).order_by('is_completed', '-role_count')

    context = {
        'last_viewed': last_viewed,
        'current_training_modules': current_training_modules,
        'available_modules': available_modules,
        'topics': TrainingModule.objects.values_list('topic__name', flat=True).distinct(),
    }
    return render(request, 'education/training_directory.html', context)


def module_overview(request, module_id):
    module = get_object_or_404(TrainingModule, id=module_id)
    user = request.user
    print(module.complexity_level())

    # Calculate progress
    progress = module.completion_percentage_for_user(user) if user.is_authenticated else 0
    
    # Total duration
    total_length = module.get_total_length()
    
    # Participants count (started_by)
    participants_count = module.started_by.count()
    
    # Update last viewed for the user profile if authenticated
    if user.is_authenticated and hasattr(user, 'profile'):
        user.profile.last_viewed_training_module = module
        user.profile.save()
    
    # Gather roles that require this module
    roles = module.roles.all()
    
    # Organize module structure (lessons and quizzes)
    sidebar = get_module_sidebar_context(module)
    
    context = {
        **sidebar,
        'progress': progress,
        'total_length': total_length,
        'participants_count': participants_count,
        'roles': roles,
    }
    return render(request, 'education/module_overview.html', context)

def lesson_detail(request, module_id, lesson_id):
    module = get_object_or_404(TrainingModule, id=module_id)
    lesson = get_object_or_404(TrainingLesson, id=lesson_id, training_module=module)
    user = request.user

    sidebar = get_module_sidebar_context(module)
    lessons = sidebar['lessons']
    quizzes = sidebar['quizzes']

    progress = module.completion_percentage_for_user(user) if user.is_authenticated else 0

    # Update last viewed
    if user.is_authenticated and hasattr(user, 'profile'):
        user.profile.last_viewed_training_module = module
        user.profile.save()

    # Determine next lesson or quiz
    next_lesson = None
    next_quiz = None
    lessons_list = list(lessons)
    current_index = None
    for i, l in enumerate(lessons_list):
        if l.id == lesson.id:
            current_index = i
            break

    if current_index is not None and current_index + 1 < len(lessons_list):
        next_lesson = lessons_list[current_index + 1]
    elif quizzes.exists():
        next_quiz = quizzes.first()

    # Count completed lessons
    completed_count = 0
    lesson_already_completed = False
    if user.is_authenticated:
        for l in lessons_list:
            if user in l.completed_by.all():
                completed_count += 1
                if l.id == lesson.id:
                    lesson_already_completed = True
        for q in quizzes:
            if user in q.completed_by.all():
                completed_count += 1

    context = {
        **sidebar,
        'lesson': lesson,
        'current_lesson': lesson,
        'progress': progress,
        'next_lesson': next_lesson,
        'next_quiz': next_quiz,
        'completed_count': completed_count,
        'lesson_already_completed': lesson_already_completed,
    }
    return render(request, 'education/lesson_detail.html', context)


@require_POST
@login_required
def mark_lesson_complete(request, module_id, lesson_id):
    module = get_object_or_404(TrainingModule, id=module_id)
    module.started_by.add(request.user)  # Ensure user is marked as having started the module
    lesson = get_object_or_404(TrainingLesson, id=lesson_id, training_module=module)
    lesson.completed_by.add(request.user)
    progress = module.completion_percentage_for_user(request.user)
    return JsonResponse({'status': 'ok', 'progress': progress})


def complete_module(request, module_id):
    if request.method != 'POST':
        messages.error(request, _('Invalid request method.'))
        return redirect('module_overview', module_id=module_id)
    module = get_object_or_404(TrainingModule, id=module_id)
    user = request.user

    result = module.mark_as_completed_for_user(user)

    if result:
        completion, created = result
        return redirect(f"{reverse('completed_module', kwargs={'completion_id': completion.id})}?first_time=1")
    else:
        messages.error(request, _('Failed to mark module as completed.'))
        return redirect('module_overview', module_id=module_id)

def completed_module(request, completion_id):
    completion = get_object_or_404(TrainingModuleCompletion, id=completion_id)
    first_time = request.GET.get('first_time') == '1'
    module = completion.training_module

    # Build the verification URL for the QR code
    verification_path = reverse('completed_module', kwargs={'completion_id': completion.id})
    verification_url = request.build_absolute_uri(verification_path)

    # Generate QR code as base64 data URI
    import qrcode
    import qrcode.image.svg
    import io
    import base64

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(verification_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#00434d", back_color="transparent", image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    img.save(buffer)
    qr_svg = buffer.getvalue().decode('utf-8')

    context = {
        'completion': completion,
        'module': module,
        'user': completion.user,
        'first_time': first_time,
        'qr_svg': qr_svg,
        'verification_url': verification_url,
    }
    return render(request, 'education/completed_module.html', context)


def printable_certificate(request, completion_id):
    completion = get_object_or_404(TrainingModuleCompletion, id=completion_id)
    module = completion.training_module

    verification_path = reverse('completed_module', kwargs={'completion_id': completion.id})
    verification_url = request.build_absolute_uri(verification_path)

    import qrcode
    import qrcode.image.svg
    import io

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(verification_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#00434d", back_color="transparent", image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    img.save(buffer)
    qr_svg = buffer.getvalue().decode('utf-8')

    context = {
        'completion': completion,
        'module': module,
        'user': completion.user,
        'qr_svg': qr_svg,
    }
    return render(request, 'education/printable_certificate.html', context)


@login_required
def quiz_detail(request, module_id, quiz_id):
    module = get_object_or_404(TrainingModule, id=module_id)
    quiz = get_object_or_404(Quiz, id=quiz_id, training_module=module)

    sidebar = get_module_sidebar_context(module)
    questions = quiz.questions.all()

    context = {
        **sidebar,
        'quiz': quiz,
        'current_quiz': quiz,
        'questions': questions,
    }
    return render(request, 'education/quiz_detail.html', context)


@require_POST
@login_required
def submit_quiz(request, module_id, quiz_id):
    module = get_object_or_404(TrainingModule, id=module_id)
    quiz = get_object_or_404(Quiz, id=quiz_id, training_module=module)

    questions = quiz.questions.all()
    results = []
    correct_count = 0

    for question in questions:
        user_answer = request.POST.get(f'question_{question.id}')
        is_correct = user_answer == question.correct_option

        if is_correct:
            correct_count += 1

        answer_map = {
            'A': question.option_a,
            'B': question.option_b,
            'C': question.option_c,
            'D': question.option_d,
        }

        results.append({
            'question_id': question.id,
            'question_text': question.question_text,
            'user_answer': answer_map.get(user_answer, ''),
            'correct_answer': answer_map.get(question.correct_option, ''),
            'is_correct': is_correct,
        })

    total = questions.count()
    score = round((correct_count / total) * 100) if total > 0 else 0
    passed = score >= quiz.percentage_to_pass

    if passed:
        quiz.completed_by.add(request.user)

    request.session[f'quiz_results_{quiz_id}'] = {
        'results': results,
        'correct_count': correct_count,
        'total': total,
        'score': score,
        'passed': passed,
    }

    return redirect('quiz_results', module_id=module_id, quiz_id=quiz_id)


@login_required
def quiz_results(request, module_id, quiz_id):
    module = get_object_or_404(TrainingModule, id=module_id)
    quiz = get_object_or_404(Quiz, id=quiz_id, training_module=module)

    sidebar = get_module_sidebar_context(module)

    results_data = request.session.get(f'quiz_results_{quiz_id}')
    if not results_data:
        return redirect('quiz_detail', module_id=module_id, quiz_id=quiz_id)

    # Determine next action after quiz
    quizzes_list = list(sidebar['quizzes'])
    current_index = None
    for i, q in enumerate(quizzes_list):
        if q.id == quiz.id:
            current_index = i
            break

    next_quiz = None
    if current_index is not None and current_index + 1 < len(quizzes_list):
        next_quiz = quizzes_list[current_index + 1]

    can_complete = results_data['passed'] and module.validate_completion_for_user(request.user)

    context = {
        **sidebar,
        'quiz': quiz,
        'current_quiz': quiz,
        'results': results_data['results'],
        'correct_count': results_data['correct_count'],
        'total': results_data['total'],
        'score': results_data['score'],
        'passed': results_data['passed'],
        'next_quiz': next_quiz,
        'can_complete': can_complete,
    }
    return render(request, 'education/quiz_results.html', context)


def verify_certificates(request):
    user_certifications = UserCertification.objects.filter(user=request.user).select_related('certificate') if request.user.is_authenticated else UserCertification.objects.none()
    verified_certs = user_certifications.filter(verified=True)
    verified_cert_ids = verified_certs.values_list('certificate_id', flat=True)
    available_certs = ExternalCertificate.objects.exclude(id__in=verified_cert_ids)

    context = {
        'available_certs': available_certs,
        'verified_certs': verified_certs if request.user.is_authenticated else [],
    }
    return render(request, 'education/verify_certificates.html', context)


@login_required
def training_dashboard(request):
    user = request.user

    # Completed module completions, ordered latest first
    completions = TrainingModuleCompletion.objects.filter(user=user).select_related('training_module').order_by('-completed_at')
    completed_module_ids = completions.values_list('training_module_id', flat=True)
    completed_count = completions.count()

    # In-progress: started but not yet completed
    in_progress_modules = list(
        TrainingModule.objects.filter(started_by=user, published=True).exclude(id__in=completed_module_ids)
    )
    for module in in_progress_modules:
        module.user_progress = module.completion_percentage_for_user(user)

    # Attach completion date to completed modules
    for completion in completions:
        completion.training_module.completion_date = completion.completed_at

    # Total published modules
    total_modules = TrainingModule.objects.filter(published=True).count()
    progress_pct = round((completed_count / total_modules) * 100) if total_modules else 0

    # Time invested: sum lengths of completed lessons + completed quizzes
    time_minutes = 0
    for lesson in user.completed_lessons.all():
        time_minutes += lesson.overall_length()
    for quiz in user.completed_quizzes.all():
        for question in quiz.questions.all():
            time_minutes += question.length_minutes or 1
    time_hours = round(time_minutes / 60, 1)

    # External certifications
    certifications = UserCertification.objects.filter(user=user).select_related('certificate').order_by('-issued_at')

    # Profile & level
    profile = getattr(user, 'profile', None)
    impact_points = profile.impact_points if profile else 0
    level = profile.level() if profile else None

    # Suggested modules for empty state
    suggested_modules = (
        TrainingModule.objects.filter(published=True)
        .exclude(started_by=user)
        .order_by('?')[:3]
    )

    has_started_training = bool(in_progress_modules) or completions.exists()

    context = {
        'in_progress_modules': in_progress_modules,
        'completions': completions,
        'completed_count': completed_count,
        'total_modules': total_modules,
        'progress_pct': progress_pct,
        'time_hours': time_hours,
        'certifications': certifications,
        'impact_points': impact_points,
        'level': level,
        'suggested_modules': suggested_modules,
        'has_started_training': has_started_training,
    }
    return render(request, 'education/training_dashboard.html', context)

@login_required
def submit_certificate(request, cert_id):
    certificate = get_object_or_404(ExternalCertificate, id=cert_id)
    user = request.user

    user_cert = UserCertification.objects.filter(
        user=user, certificate=certificate
    ).first()

    if request.method == 'POST':
        user_cert = user_cert or UserCertification.objects.create(user=user, certificate=certificate)
        files = request.FILES.getlist('documents')
        if not files:
            messages.error(request, _('Please attach at least one document.'))
        else:
            for f in files:
                UserCertificationFile.objects.create(certification=user_cert, file=f)
            messages.success(request, _('Your documents have been submitted for verification.'))
            return redirect('verify_certificates')

    required_docs = [d.strip().capitalize() for d in (certificate.docs_list or '').split(',') if d.strip()]
    existing_files = user_cert.files.all() if user_cert else []
    status = {
        "code": 0,  # 0 = not submitted, 1 = pending, 2 = verified
        "text": _("Not submitted"),
    }
    if user_cert:
        if user_cert.verified:
            status = {"code": 2, "text": _("Verified")}
        else:
            status = {"code": 1, "text": _("Pending verification")}
    context = {
        'certificate': certificate,
        'user_cert': user_cert,
        'required_docs': required_docs,
        'existing_files': existing_files,
        'status': status,
    }
    return render(request, 'education/submit_certificate.html', context)