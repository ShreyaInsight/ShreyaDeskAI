import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from backend import telegram_commands as commands

class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for p in [patch.dict(os.environ, {'TELEGRAM_COMMAND_USER_ID':'123'}),
                  patch.object(commands.notifications, 'DB_PATH', Path(self.temp.name)/'telegram.db')]:
            p.start(); self.addCleanup(p.stop)
        commands.pending = None
    def event(self, text, uid=1, sender=123, chat=123, kind='private', age=0):
        return {'update_id':uid, 'message':{'date':time.time()-age, 'from':{'id':sender},
                 'chat':{'id':chat,'type':kind}, 'text':text}}
    @patch.object(commands, 'deallocate')
    @patch.object(commands, 'reply')
    @patch('backend.autotrade.pause')
    def test_confirm_once(self, pause, reply, azure):
        commands.handle(self.event('/stopvm'))
        azure.assert_not_called(); pause.assert_not_called()
        nonce = commands.pending[0]
        event = self.event('/stopvm confirm '+nonce, uid=2)
        commands.handle(event); commands.handle(event)
        commands.handle(self.event('/stopvm confirm '+nonce, uid=3))
        azure.assert_called_once(); pause.assert_called_once()
    @patch.object(commands, 'reply')
    def test_untrusted_and_stale_ignored(self, reply):
        for event in [self.event('/stopvm',sender=456), self.event('/stopvm',chat=-100,kind='supergroup'),
                      self.event('/stopvm',age=180)]:
            commands.handle(event)
        reply.assert_not_called(); self.assertIsNone(commands.pending)
    @patch.object(commands, 'deallocate')
    @patch.object(commands, 'reply')
    def test_expired_and_restart_confirmation(self, reply, azure):
        commands.pending=('abc',time.monotonic()-1)
        commands.handle(self.event('/stopvm confirm abc'))
        commands.pending=None
        commands.handle(self.event('/stopvm confirm abc',uid=2))
        azure.assert_not_called()
    @patch.object(commands, 'deallocate', side_effect=RuntimeError('secret'))
    @patch.object(commands, 'reply')
    @patch('backend.autotrade.pause')
    def test_failure_is_not_retried_or_leaked(self, pause, reply, azure):
        commands.pending=('abc',time.monotonic()+60)
        commands.handle(self.event('/stopvm confirm abc'))
        commands.handle(self.event('/stopvm confirm abc',uid=2))
        azure.assert_called_once()
        self.assertNotIn('secret',str(reply.call_args_list))
    @patch.object(commands.subprocess, 'run')
    def test_fixed_azure_target(self, run):
        with patch.object(commands, 'VM', 'example-vm'), patch.object(commands, 'RESOURCE_GROUP', 'example-group'), patch.object(commands, 'SUBSCRIPTION', 'example-subscription'):
            commands.deallocate()
        args=run.call_args.args[0]
        self.assertIn('example-group',args); self.assertIn('example-vm',args)
        self.assertNotIn('shell',run.call_args.kwargs)
