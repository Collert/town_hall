import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

from base.models import HeroSection, SiteSettings, Notification
from education.models import Skill, TrainingModule, TrainingLesson, Quiz, QuizQuestion
from events.models import Event, EventRoleSlot
from jobs.models import Role

class Command(BaseCommand):
    help = 'Populates the database with dummy data for testing purposes'

    def handle(self, *args, **kwargs):
        self.stdout.write('Starting database population...')
        User = get_user_model()

        # 1. Create Users
        self.stdout.write('Creating users...')
        users = []
        for i in range(1, 11):
            username = f'user{i}'
            if not User.objects.filter(username=username).exists():
                user = User.objects.create_user(
                    username=username,
                    email=f'{username}@example.com',
                    password='password123'
                )
                users.append(user)
            else:
                users.append(User.objects.get(username=username))

        if not User.objects.filter(username='admin').exists():
            User.objects.create_superuser('admin', 'admin@example.com', 'admin')

        # 2. Create Skills
        self.stdout.write('Creating skills...')
        skill_names = ['First Aid', 'Public Speaking', 'Event Planning', 'Carpentry', 'Technical Support']
        skills = []
        for name in skill_names:
            skill, _ = Skill.objects.get_or_create(name=name)
            skills.append(skill)

        # 3. Create Roles
        self.stdout.write('Creating roles...')
        role_data = [
            {'name': 'Volunteer Coordinator', 'icon': 'group', 'permanent': True},
            {'name': 'Medic', 'icon': 'medical_services', 'permanent': False},
            {'name': 'Speaker', 'icon': 'mic', 'permanent': False},
            {'name': 'Tech Hand', 'icon': 'computer', 'permanent': False},
            {'name': 'Setup Crew', 'icon': 'build', 'permanent': False},
        ]
        roles = []
        for r_dict in role_data:
            role, created = Role.objects.get_or_create(name=r_dict['name'], defaults={
                'description': f"Description for {r_dict['name']}",
                'icon': r_dict['icon'],
                'permanent': r_dict['permanent']
            })
            roles.append(role)

        # 4. Create Training Modules
        self.stdout.write('Creating training modules...')
        module, created = TrainingModule.objects.get_or_create(
            title='Basic Volunteer Training',
            defaults={
                'description': 'Essential training for all new volunteers.',
                'published': True,
                'icon': 'school'
            }
        )
        if created:
            module.skills.add(skills[2])  # Event Planning
            
            lesson = TrainingLesson.objects.create(
                training_module=module,
                title='Welcome and Safety',
                content='Welcome to the community! Please read these safety guidelines...',
                order=1
            )
            
            question = QuizQuestion.objects.create(
                question_text='What is the primary goal of our events?',
                option_a='To have fun',
                option_b='To build community',
                option_c='To get paid',
                option_d='None of the above',
                correct_option='B'
            )
            quiz = Quiz.objects.create(
                training_module=module,
                title='Safety Quiz'
            )
            quiz.questions.add(question)

        # 5. Create Events
        self.stdout.write('Creating events...')
        now = timezone.now()
        for i in range(1, 6):
            event, created = Event.objects.get_or_create(
                title=f'Community Town Hall {i}',
                defaults={
                    'description': f'Join us for our {i}th community meeting of the year!',
                    'start_date': now + timedelta(days=i*7),
                    'end_date': now + timedelta(days=i*7, hours=3),
                    'location': f'Community Center Room {i % 3 + 1}'
                }
            )
            
            # Create EventRoleSlots for the event
            if created:
                event.coordinators.add(random.choice(users))
                
                # Pick 2 random roles for the event
                event_roles = random.sample(roles, 2)
                for role in event_roles:
                    slot = EventRoleSlot.objects.create(
                        event=event,
                        start_time=event.start_date,
                        end_time=event.end_date,
                        required_qty=random.randint(2, 5),
                        allowed_overstaffing_qty=random.randint(0, 2),
                        is_public=True
                    )
                    slot.role.add(role)

        # SiteSettings and HeroSection
        site_settings, created = SiteSettings.objects.get_or_create(pk=1, defaults={'company_name': 'Town Hall Testing'})
        hero, created = HeroSection.objects.get_or_create(pk=1, defaults={'title': 'Welcome to Town Hall Testing'})

        # 6. Create Notifications
        self.stdout.write('Creating notifications...')
        notification_messages = [
            "Your shift schedule has been updated.",
            "A new training module is available.",
            "Don't forget to complete your compliance quiz!",
            "Thank you for volunteering last weekend.",
            "New community events have been posted."
        ]
        
        all_users = User.objects.all()
        for user in all_users:
            # Create 1-3 random notifications per user
            num_notifications = random.randint(1, 3)
            for _ in range(num_notifications):
                Notification.objects.create(
                    user=user,
                    message=random.choice(notification_messages),
                    read=random.choice([True, False]),
                    link="/opportunities/"
                )

        self.stdout.write(self.style.SUCCESS('Successfully populated database!'))
