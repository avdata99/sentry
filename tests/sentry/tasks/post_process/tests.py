# -*- coding: utf-8 -*-

from __future__ import absolute_import

from mock import Mock, patch

from sentry.models import Group, Rule
from sentry.testutils import TestCase
from sentry.tasks.post_process import (
    execute_rule, post_process_group, record_affected_user,
    record_affected_code
)


class PostProcessGroupTest(TestCase):
    @patch('sentry.tasks.post_process.record_affected_code')
    @patch('sentry.tasks.post_process.get_rules', Mock(return_value=[]))
    def test_record_affected_code(self, mock_record_affected_code):
        group = self.create_group(project=self.project)
        event = self.create_event(group=group)

        with self.settings(SENTRY_ENABLE_EXPLORE_CODE=False):
            post_process_group(
                group=group,
                event=event,
                is_new=True,
                is_regression=False,
                is_sample=False,
            )

        assert not mock_record_affected_code.delay.called

        with self.settings(SENTRY_ENABLE_EXPLORE_CODE=True):
            post_process_group(
                group=group,
                event=event,
                is_new=True,
                is_regression=False,
                is_sample=False,
            )

        mock_record_affected_code.delay.assert_called_once_with(
            group=group,
            event=event,
        )

    @patch('sentry.tasks.post_process.record_affected_user')
    @patch('sentry.tasks.post_process.get_rules', Mock(return_value=[]))
    def test_record_affected_user(self, mock_record_affected_user):
        group = self.create_group(project=self.project)
        event = self.create_event(group=group)

        with self.settings(SENTRY_ENABLE_EXPLORE_USERS=False):
            post_process_group(
                group=group,
                event=event,
                is_new=True,
                is_regression=False,
                is_sample=False,
            )

        assert not mock_record_affected_user.delay.called

        with self.settings(SENTRY_ENABLE_EXPLORE_USERS=True):
            post_process_group(
                group=group,
                event=event,
                is_new=True,
                is_regression=False,
                is_sample=False,
            )

        mock_record_affected_user.delay.assert_called_once_with(
            group=group,
            event=event,
        )

    @patch('sentry.tasks.post_process.execute_rule')
    @patch('sentry.tasks.post_process.get_rules')
    def test_execute_rule(self, mock_get_rules, mock_execute_rule):
        action_id = 'sentry.rules.actions.notify_event.NotifyEventAction'
        condition_id = 'sentry.rules.conditions.first_seen_event.FirstSeenEventCondition'

        group = self.create_group(project=self.project)
        event = self.create_event(group=group)

        mock_get_rules.return_value = [
            Rule(
                id=1,
                data={
                    'actions': [{'id': action_id}],
                    'conditions': [{'id': condition_id}],
                }
            )
        ]

        post_process_group(
            group=group,
            event=event,
            is_new=False,
            is_regression=False,
            is_sample=False,
        )

        assert not mock_execute_rule.delay.called

        post_process_group(
            group=group,
            event=event,
            is_new=True,
            is_regression=False,
            is_sample=False,
        )

        mock_execute_rule.delay.assert_called_once_with(
            rule_id=1,
            event=event,
            is_new=True,
            is_regression=False,
            is_sample=False,
        )

        # ensure we dont execute again since the object hasnt changed state
        post_process_group(
            group=group,
            event=event,
            is_new=True,
            is_regression=False,
            is_sample=False,
        )

        assert len(mock_execute_rule.mock_calls) == 1

        # and finally test the behavior of cycling back to new
        post_process_group(
            group=group,
            event=event,
            is_new=False,
            is_regression=False,
            is_sample=False,
        )
        post_process_group(
            group=group,
            event=event,
            is_new=True,
            is_regression=False,
            is_sample=False,
        )
        assert len(mock_execute_rule.mock_calls) == 2


class ExecuteRuleTest(TestCase):
    @patch('sentry.tasks.post_process.rules')
    def test_simple(self, mock_rules):
        group = self.create_group(project=self.project)
        event = self.create_event(group=group)
        rule = Rule.objects.create(
            project=event.project,
            data={
                'actions': [
                    {'id': 'a.rule.id'},
                ],
            }
        )

        execute_rule(
            rule_id=rule.id,
            event=event,
            is_new=True,
            is_regression=False,
            is_sample=True,
        )

        mock_rules.get.assert_called_once_with('a.rule.id')
        mock_rule_inst = mock_rules.get.return_value
        mock_rule_inst.assert_called_once_with(self.project)
        mock_rule_inst.return_value.after.assert_called_once_with(
            event=event,
            is_new=True,
            is_regression=False,
            is_sample=True,
        )


class RecordAffectedUserTest(TestCase):
    def test_simple(self):
        event = Group.objects.from_kwargs(1, message='foo', **{
            'sentry.interfaces.User': {
                'email': 'foo@example.com',
            },
        })

        with patch.object(Group.objects, 'add_tags') as add_tags:
            record_affected_user(group=event.group, event=event)

            add_tags.assert_called_once(event.group, [
                ('sentry:user', 'email:foo@example.com', {
                    'id': None,
                    'email': 'foo@example.com',
                    'username': None,
                    'data': None,
                })
            ])


class RecordAffectedCodeTest(TestCase):
    def test_simple(self):
        event = Group.objects.from_kwargs(1, message='foo', **{
            'sentry.interfaces.Exception': {
                'values': [{
                    'type': 'TypeError',
                    'value': 'test',
                    'stacktrace': {
                        'frames': [{
                            'function': 'bar',
                            'filename': 'foo.py',
                            'in_app': True,
                        }],
                    },
                }],
            },
        })

        with patch.object(Group.objects, 'add_tags') as add_tags:
            record_affected_code(group=event.group, event=event)

            add_tags.assert_called_once_with(event.group, [
                ('sentry:filename', '1effb24729ae4c43efa36b460511136a', {
                    'filename': 'foo.py',
                }),
                ('sentry:function', '7823c20ad591da0bbb78d083c118609c', {
                    'filename': 'foo.py',
                    'function': 'bar',
                })
            ])
