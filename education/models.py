import re
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from math import ceil
from django.db.models import Sum

class TrainingModule(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    skills = models.ManyToManyField('education.Skill', related_name='courses')
    topic = models.ForeignKey('education.TrainingTopic', related_name='courses', blank=True, null=True, on_delete=models.SET_NULL)
    published = models.BooleanField(default=False)
    photo = models.ImageField(upload_to='training_module_photos/', blank=True, null=True)
    icon = models.CharField(max_length=50, help_text='Icon name from Google Material Icons')
    expires_after_days = models.PositiveIntegerField(blank=True, null=True, help_text='Number of days after which the training module expires for a user')
    started_by = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='started_training_modules', blank=True)
    complexity_level = models.PositiveIntegerField(default=0, help_text='Complexity level of the training module (0-4)')

    def __str__(self):
        return self.title

    def get_total_length(self):
        """Calculate total length of the training module based on its lessons and quizzes."""
        total_length = 0
        for lesson in self.lessons.all():
            total_length += lesson.overall_length
        for quiz in self.quizzes.all():
            total_length += quiz.total_length
        return total_length
    
    def completion_percentage_for_user(self, user):
        """Calculate the completion percentage of this training module for a given user."""
        total_items = self.lessons.count() + self.quizzes.count()
        if total_items == 0:
            return 100  # Consider it complete if there are no lessons or quizzes
        completed_items = 0
        for lesson in self.lessons.all():
            if user in lesson.completed_by.all():
                completed_items += 1
        for quiz in self.quizzes.all():
            if user in quiz.completed_by.all():
                completed_items += 1
        return ceil((completed_items / total_items) * 100)
    
    def validate_completion_for_user(self, user):
        """Check if the user has completed all mandatory lessons and quizzes."""
        for lesson in self.lessons.all():
            if user not in lesson.completed_by.all():
                return False
        for quiz in self.quizzes.all():
            if user not in quiz.completed_by.all():
                return False
        return True
    
    def mark_as_completed_for_user(self, user):
        """Mark the training module as completed for the user if all requirements are met."""
        if self.validate_completion_for_user(user):
            return TrainingModuleCompletion.objects.get_or_create(training_module=self, user=user)
        return None
    
    def has_expired_for_user(self, user):
        """Check if the training module has expired for the user based on the completion date."""
        if not self.expires_after_days:
            return False  # No expiration if not set
        try:
            completion = TrainingModuleCompletion.objects.get(training_module=self, user=user)
            expiration_date = completion.completed_at + timezone.timedelta(days=self.expires_after_days)
            return timezone.now() > expiration_date
        except TrainingModuleCompletion.DoesNotExist:
            return False  # Not completed yet, so not expired
        
    def completion_date_for_user(self, user):
        """Get the completion date for the user if they have completed the training module."""
        try:
            completion = TrainingModuleCompletion.objects.get(training_module=self, user=user)
            return completion.completed_at
        except TrainingModuleCompletion.DoesNotExist:
            return None  # Not completed yet

class TrainingModuleCompletion(models.Model):
    training_module = models.ForeignKey(TrainingModule, on_delete=models.CASCADE, related_name='completions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='training_module_completions')
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('training_module', 'user')
        ordering = ['-completed_at']
    def __str__(self):
        return f"{self.user.username} completed {self.training_module.title} on {self.completed_at.strftime('%Y-%m-%d')}"
    
class Skill(models.Model):
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name
    
    def get_related_training_modules(self):
        return self.training_modules.all()

class TrainingTopic(models.Model):
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

class TrainingLesson(models.Model):
    training_module = models.ForeignKey(TrainingModule, on_delete=models.CASCADE, related_name='lessons')
    title = models.CharField(max_length=100)
    content = models.TextField()
    video_url = models.URLField(blank=True, null=True)
    video_length_minutes = models.PositiveIntegerField(blank=True, null=True)
    order = models.PositiveIntegerField(default=0)
    completed_by = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='completed_lessons', blank=True)

    def __str__(self):
        return f"{self.training_module.title} - {self.title}"

    @property
    def youtube_video_id(self):
        """Extract the YouTube video ID from the video URL."""
        if not self.video_url:
            return None
        match = re.search(r'(?:youtu\.be/|youtube\.com/(?:embed/|v/|watch\?v=|.*&v=))([^&?#]+)', self.video_url)
        return match.group(1) if match else None

    def save(self, user=None, *args, **kwargs):
        if user and self.pk:  # Only validate completion if this is an update and a user is provided
            self.training_module.mark_as_completed_for_user(user)  # Validate completion for the provided user
        super().save(*args, **kwargs)
        if self.video_url and not self.video_length_minutes:
            self.get_video_length()

    def get_video_length(self):
        """Fetch and save the video duration from YouTube using yt-dlp."""
        if not self.video_url:
            return
        import yt_dlp
        ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.video_url, download=False)
            duration_seconds = info.get('duration')
            if duration_seconds:
                self.video_length_minutes = ceil(duration_seconds / 60)
                self.save(update_fields=['video_length_minutes'])
    
    @property
    def overall_length(self):
        """Calculate total length of the lesson based on its video and text content."""
        text_length_minutes = len(self.content.split()) / 200  # Assuming 200 words per minute reading speed
        video_length = self.video_length_minutes or 0
        return ceil(text_length_minutes + video_length)

class Quiz(models.Model):
    training_module = models.ForeignKey(TrainingModule, on_delete=models.CASCADE, related_name='quizzes')
    title = models.CharField(max_length=100)
    percentage_to_pass = models.PositiveIntegerField(default=70)
    questions = models.ManyToManyField('education.QuizQuestion', related_name='quizzes')
    completed_by = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='completed_quizzes', blank=True)
    order = models.PositiveIntegerField(default=0)

    def save(self, user=None, *args, **kwargs):
        if user and self.pk:  # Only validate completion if this is an update and a user is provided
            self.training_module.mark_as_completed_for_user(user)  # Validate completion for the provided user
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.training_module.title} - {self.title}"
    
    @property
    def total_length(self):
        """Calculate total length of the quiz based on its questions."""
        total_length = 0
        for question in self.questions.all():
            total_length += question.length_minutes or 1  # Default to 1 minute if not set
        return total_length
    
class QuizQuestion(models.Model):
    question_text = models.TextField()
    option_a = models.CharField(max_length=200)
    option_b = models.CharField(max_length=200)
    option_c = models.CharField(max_length=200, blank=True, null=True)
    option_d = models.CharField(max_length=200, blank=True, null=True)
    correct_option = models.CharField(max_length=1, choices=[('A', 'Option A'), ('B', 'Option B'), ('C', 'Option C'), ('D', 'Option D')])
    is_true_false = models.BooleanField(default=False)
    length_minutes = models.PositiveIntegerField(blank=True, null=True)

    def __str__(self):
        return self.question_text

    def set_true_false_options(self):
        """If the question is a true/false type, set options accordingly."""
        if self.is_true_false:
            self.option_a = 'True'
            self.option_b = 'False'
            self.option_c = None
            self.option_d = None
            if self.correct_option not in ['A', 'B']:
                self.correct_option = 'A'  # Default to True if correct option is invalid
            self.save(update_fields=['option_a', 'option_b', 'option_c', 'option_d', 'correct_option'])

    def save(self, *args, **kwargs):
        if self.is_true_false:
            self.set_true_false_options()
        self.length_minutes = ceil((len(self.question_text.split()) / 200) * 1.2)  # Estimate time to read the question plus time to answer
        super().save(*args, **kwargs)

class ExternalCertificate(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField()
    icon = models.CharField(max_length=50, blank=True, null=True, help_text='Material Symbols icon name for this certificate')
    expires_after_days = models.PositiveIntegerField(blank=True, null=True, help_text='Number of days after which the certificate expires for a user')
    docs_list = models.TextField(blank=True, null=True, help_text='List of required documents for this certificate, separated by commas')

    def __str__(self):
        return f"{self.name} certificate"
    
class UserCertification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='certifications')
    certificate = models.ForeignKey(ExternalCertificate, on_delete=models.CASCADE)
    issued_at = models.DateTimeField(auto_now_add=True)
    expiration_date = models.DateTimeField(blank=True, null=True)
    verified = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'certificate')

    def __str__(self):
        return f"{self.user.username} - {self.certificate.name} Certification"

class UserCertificationFile(models.Model):
    certification = models.ForeignKey(UserCertification, on_delete=models.CASCADE, related_name='files')
    file = models.FileField(upload_to='certification_files/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"File for {self.certification.user.username} - {self.certification.certificate.name}"
