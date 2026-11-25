import importlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
import conversation_store as store
from chat_ui import ChatTab
from tests.test_app_flow import Value


class CreditAndPersistenceTests(unittest.TestCase):
    def host(self):
        host = app.App.__new__(app.App)
        host.busy = False
        host.oauth_session = {'access_token': 'test', 'profile': {'credits': 1}}
        host.model_id = Value('wan3.0-video')
        host.duration = Value(5)
        host.resolution = Value('720p')
        host.ratio = Value('16:9')
        host.prompt = Mock()
        host.prompt.get.return_value = 'a long prompt' * 1000
        host.generate_btn = Mock()
        host.credit_tip = Mock()
        host.credit_tip_text = Value('')
        return host

    def test_gate_tip_and_payment_refresh_never_submit(self):
        host = self.host()
        host.launch = Mock()
        host.sync_generate_credits(500)
        host.generate_btn.configure.assert_called_with(state='disabled')
        self.assertTrue(host.credit_tip_text.get())
        host.credit_tip.pack.assert_called_once()
        host.oauth_session['profile']['credits'] = 1000
        host.sync_generate_credits(500)
        host.generate_btn.configure.assert_called_with(state='normal')
        host.credit_tip.pack_forget.assert_called_once()
        host.launch.assert_not_called()
        host.oauth_session['profile']['credits'] = None
        self.assertIsNone(host._generate_shortfall())
        host.oauth_session = None
        self.assertIsNone(host._generate_shortfall())

    def test_topup_intent_clear_and_restart(self):
        host = self.host()
        host.open_billing = Mock()
        host._clear_ai_review = Mock()
        with tempfile.TemporaryDirectory() as folder, patch.object(store, 'DATA_DIR', Path(folder)):
            host.open_generate_topup()
            host.open_billing.assert_called_with('generate_tip')
            self.assertEqual(store.intended()['prompt'], host.prompt.get.return_value)
            host.prompt.get.return_value = ''
            host.clear_generate_prompt()
            host.prompt.delete.assert_called_with('1.0', 'end')
            self.assertEqual(store.intended()['prompt'], '')
            self.assertEqual(store.read('generate_form')['prompt'], '')
            store.write('intended_generate', {'at':time.time()-store.TTL-1,'form':{'prompt':'old'}})
            self.assertIsNone(store.intended())

    def test_chat_atomic_persistence_and_explicit_clear(self):
        chat = ChatTab.__new__(ChatTab)
        chat.messages = [{'role':'user','content':'hello'}, {'role':'assistant','content':'{"assistant_message":"hello","ready":true,"plan":{"prompt":"cat"}}'}]
        chat.last_plan = {'prompt':'cat'}
        chat._busy = False
        chat.input = Mock()
        chat.input.get.return_value = 'draft'
        chat._selected_text_model = lambda: 'glm-4.7-flash'
        chat.thread = Mock()
        chat._append_system_hint = Mock()
        chat._sync_exit_buttons = Mock()
        chat.status_var = Value('')
        with tempfile.TemporaryDirectory() as folder, patch.object(store,'DATA_DIR',Path(folder)):
            chat.persist_state()
            reloaded = store.read('chat_history')
            self.assertEqual(reloaded['messages'], chat.messages)
            self.assertEqual(reloaded['last_plan'], chat.last_plan)
            self.assertEqual(reloaded['draft'], 'draft')
            self.assertFalse(list(Path(folder).glob('*.tmp')))
            self.assertNotIn('access_token', str(reloaded))
            chat.clear()
            self.assertIsNone(store.read('chat_history'))

    def test_credit_conversion_packages_and_fail_open(self):
        self.assertEqual(store.estimate_credits(.1234),124)
        self.assertEqual([store.suggested_amount(n,0) for n in (5000,5001,20000,20001)],[5,20,20,50])
        host=self.host()
        with patch.object(app,'estimate_cost',side_effect=ValueError):
            self.assertIsNone(host._generate_shortfall())
