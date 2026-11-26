import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import payment
import payment_ui
import payment_recovery as recovery


class RecoveryFlows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'pending.json'
        self.now = 1000000
        self.store = self.restore()
        self.account = recovery.owner({'profile': {'sub': 'TEST_ACCOUNT'}})
        self.notify = patch.object(payment, '_notify').start()
        self.flush = patch.object(recovery.analytics, 'flush').start()
        self.addCleanup(patch.stopall)

    def restore(self):
        return recovery.PendingOrders(self.path, clock=lambda: self.now)

    def add(self, order='ORDER_TEST_1'):
        return self.store.add(self.account, order, 10, 35, 'paypal')

    def test_terminal_and_fallback_flows(self):
        for status, current, event in [(1, 0, 'success'), (2, 20, 'cancelled'), (0, 20, 'success')]:
            with self.subTest(status=status):
                order = 'ORDER_TEST_' + str(status + 1)
                key = self.add(order)
                self.assertTrue(self.store.apply(key, status, {'credits': current}))
                self.assertNotIn(key, self.store.pending)
                self.flush.assert_called()
                self.notify.assert_called_with(event, package_id=35, channel='paypal')
                restored = self.restore()
                restored.add(self.account, order, 10, 35, 'paypal')
                self.assertFalse(restored.apply(key, status, {'credits': current}))
        self.assertEqual(self.notify.call_count, 3)

    def test_flush_precedes_clear(self):
        key = self.add()
        self.flush.side_effect = lambda: self.assertIn(key, self.store.pending)
        self.store.apply(key, 1, None)
        self.assertNotIn(key, self.restore().pending)

    def test_ttl_and_multiple_orders(self):
        first = self.add()
        second = self.add('ORDER_TEST_2')
        self.now += 301
        self.store = self.restore()
        self.assertFalse(self.store.apply(first, 0, {'credits': 10}))
        self.assertTrue(self.store.apply(second, 2, None))
        self.now += recovery.TTL
        self.store = self.restore()
        self.assertFalse(self.store.pending)
        self.assertEqual(self.notify.call_count, 1)

    def test_closed_restart_logout_relogin(self):
        key = self.add()
        self.now += 601
        callbacks = []
        root = Mock()
        root.after.side_effect = lambda delay, callback: callbacks.append(callback)
        app = SimpleNamespace(root=root, oauth_session=None, _is_oauth_logged_in=lambda: bool(app.oauth_session),
                              _pending_payments=self.restore(), _payment_state={'order': 'ORDER_TEST_1'},
                              _refresh_oauth_header=lambda: None)
        payment_ui.resume_pending(app)
        self.assertIn(key, self.restore().pending)
        app.oauth_session = {'access_token': 'FAKE_NEW_TOKEN', 'profile': {'sub': 'TEST_ACCOUNT'}}
        def thread(**kwargs):
            return SimpleNamespace(start=kwargs['target'])
        with patch.object(payment_ui.threading, 'Thread', side_effect=thread), \
             patch.object(payment, 'status', return_value=1) as status, \
             patch.object(payment_ui.oauth_desktop, 'fetch_userinfo', side_effect=OSError):
            payment_ui.resume_pending(app)
            while callbacks:
                callbacks.pop(0)()
            self.assertEqual(status.call_count, 1)
        self.assertIsNone(app._payment_state['order'])
        self.assertFalse(self.restore().pending)
        self.assertEqual(self.notify.call_count, 1)
        self.flush.assert_called_once()

    def test_fail_open_and_minimal_file(self):
        self.path.write_text('invalid')
        self.store = self.restore()
        self.add()
        record = next(iter(json.loads(self.path.read_text())['pending'].values()))
        self.assertEqual(set(record), {'owner', 'order', 'baseline', 'package_id', 'channel', 'created_at'})
        self.assertNotEqual(record['owner'], 'TEST_ACCOUNT')
        bad = recovery.PendingOrders(self.path / 'impossible')
        bad.add(self.account, 'ORDER_TEST_2', None, 35, 'paypal')

    def test_payload_and_headers(self):
        import io
        response = io.BytesIO(b'{"code":200,"data":{"status":0}}')
        with patch.object(payment, 'urlopen', return_value=response) as request:
            payment.status('FAKE_TOKEN', 'ORDER_TEST_1')
        headers = {key.lower() for key in request.call_args.args[0].headers}
        self.assertNotIn('x-client', headers)
        self.assertEqual(set(payment_ui.checkout_payload()), {'amount', 'package_id', 'payment_channel'})
        self.assertEqual(set(json.loads(request.call_args.args[0].data)), {'order_no'})
