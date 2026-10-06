from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from base.models import Endorsement, PointsEntry, PointsRules
from education.models import Skill, TrainingModule, TrainingModuleCompletion
from events.models import Event, EventRoleSlot
from jobs.models import Role, Shift


def local(days, hour):
    """An aware datetime `days` from today at `hour`:00, local time."""
    day = timezone.localdate() + timedelta(days=days)
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour))


class PointsTests(TestCase):
    def setUp(self):
        cache.clear()  # PointsRules is cached
        self.vol = User.objects.create_user('vol', 'vol@example.com', 'pw', first_name='Val')
        self.mate = User.objects.create_user('mate', 'mate@example.com', 'pw', first_name='Max')
        self.role = Role.objects.create(name='Greeter', description='Say hello.', icon='person')

    def tearDown(self):
        cache.clear()

    def event(self, start, end, attendees=0):
        # Coordinates are set so Event.save() doesn't geocode over the network.
        return Event.objects.create(title='Cleanup', description='.', location='Park', latitude=49.28, longitude=-123.12,
                                    start_date=start, end_date=end, attendees=attendees)

    def work(self, user, slot, hours=None, clutched=False, start=None):
        """A finished shift on `slot`, from its start (or `start`) for `hours`."""
        start = start or slot.start_time
        shift = Shift.objects.create(user=user, event_role_slot=slot, clutched=clutched)
        Shift.objects.filter(pk=shift.pk).update(start_time=start)
        shift.refresh_from_db()
        shift.end_time = start + timedelta(hours=hours or (slot.end_time - slot.start_time).total_seconds() / 3600)
        shift.save()
        return shift

    def slot(self, start_hour=10, end_hour=13, days=-3, attendees=0, **extra):
        event = self.event(local(days, 8), local(days, 23), attendees=attendees)
        slot = EventRoleSlot.objects.create(event=event, role=extra.pop('role', self.role), start_time=local(days, start_hour),
                                            end_time=local(days, end_hour), required_qty=extra.pop('required_qty', 2))
        return slot

    def test_daytime_shift_is_points_per_hour(self):
        slot = self.slot()
        slot.signups.add(self.vol)
        shift = self.work(self.vol, slot)
        self.assertEqual(shift.end_impact_points, 30)
        self.assertEqual(PointsEntry.objects.get(shift=shift).amount, 30)
        self.vol.profile.refresh_from_db()
        self.assertEqual(self.vol.profile.impact_points, 30)

    def test_reach_is_shared_and_capped(self):
        slot = self.slot(attendees=40)
        slot.signups.add(self.vol, self.mate)
        # 20 people per volunteer: +44% (10% per doubling of 1 + 20)
        self.assertEqual(self.work(self.vol, slot).end_impact_points, 43)
        huge = self.slot(attendees=100000)
        huge.signups.add(self.vol)
        self.assertEqual(self.work(self.vol, huge).end_impact_points, 45)  # capped at +50%

    def test_clutch_bonuses_add_up(self):
        early = self.slot(start_hour=5, end_hour=8)
        early.signups.add(self.vol)
        self.assertEqual(self.work(self.vol, early).end_impact_points, 38)  # 30 + 25%
        late_walk_in = self.slot(start_hour=20, end_hour=23)
        late_walk_in.signups.add(self.vol)
        self.assertEqual(self.work(self.vol, late_walk_in, clutched=True).end_impact_points, 53)  # 30 + 75%

    def test_last_minute_signup(self):
        soon = self.slot(start_hour=0, end_hour=3, days=1)
        EventRoleSlot.objects.filter(pk=soon.pk).update(start_time=timezone.now() + timedelta(hours=2),
                                                         end_time=timezone.now() + timedelta(hours=5))
        soon.refresh_from_db()
        soon.add_signup(self.vol)
        self.assertTrue(soon.signup_log.get(user=self.vol).last_minute)

        later = self.slot(days=10)
        later.add_signup(self.vol)
        self.assertFalse(later.signup_log.get(user=self.vol).last_minute)

    def test_role_weight_override_and_reopening(self):
        weighty = Role.objects.create(name='Lead', description='.', icon='star', points_weight=1.5)
        slot = self.slot(role=weighty)
        slot.signups.add(self.vol)
        shift = self.work(self.vol, slot)
        self.assertEqual(shift.end_impact_points, 45)
        shift.end_time = None
        shift.save()
        self.assertFalse(PointsEntry.objects.filter(shift=shift).exists())

    def test_rules_changes_apply_going_forward(self):
        slot = self.slot()
        slot.signups.add(self.vol)
        first = self.work(self.vol, slot)
        rules = PointsRules.get()
        rules.points_per_hour = 20
        rules.save()
        second = self.work(self.vol, self.slot())
        self.assertEqual((PointsEntry.objects.get(shift=first).amount, second.end_impact_points), (30, 60))

    def test_training_award_once(self):
        module = TrainingModule.objects.create(title='Safety', description='.', icon='school', published=True)
        TrainingModuleCompletion.objects.create(training_module=module, user=self.vol)
        entry = PointsEntry.objects.get(user=self.vol, source=PointsEntry.TRAINING)
        self.assertEqual(entry.amount, 5)  # an empty module gets the minimum

    def test_endorsement_award_rules(self):
        slot = self.slot()
        slot.signups.add(self.vol, self.mate)
        skill = Skill.objects.create(name='First Aid')
        Endorsement.give(self.mate, self.vol, [skill], event=slot.event)
        self.assertEqual(PointsEntry.objects.filter(user=self.vol, source=PointsEntry.ENDORSEMENT).count(), 1)
        # Endorsing back earns nothing
        Endorsement.give(self.vol, self.mate, [skill], event=slot.event)
        self.assertFalse(PointsEntry.objects.filter(user=self.mate, source=PointsEntry.ENDORSEMENT).exists())
        # Without a shared event there's no award
        Endorsement.give(self.mate, self.vol, [Skill.objects.create(name='Logistics')])
        self.assertEqual(PointsEntry.objects.filter(user=self.vol, source=PointsEntry.ENDORSEMENT).count(), 1)

    def test_staff_adjustment_and_rules_page(self):
        staff = User.objects.create_user('boss', 'boss@example.com', 'pw', is_staff=True)
        self.client.force_login(staff)
        self.client.post(reverse('console_adjust_points', args=[self.vol.pk]), {'amount': '-10', 'reason': 'Correction'})
        entry = PointsEntry.objects.get(user=self.vol, source=PointsEntry.ADJUSTMENT)
        self.assertEqual((entry.amount, entry.created_by, entry.label), (-10, staff, 'Correction'))
        self.vol.profile.refresh_from_db()
        self.assertEqual(self.vol.profile.impact_points, -10)

        self.assertEqual(self.client.get(reverse('console_settings_points')).status_code, 200)
        data = {f.name: getattr(PointsRules.get(), f.name) for f in PointsRules._meta.fields if f.name != 'id'}
        data['points_per_hour'] = 12
        self.client.post(reverse('console_settings_points'), data)
        self.assertEqual(PointsRules.get().points_per_hour, 12)
