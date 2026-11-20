"""Offline acceptance: real create/poll paths, in-memory ingest, no browser or Tk."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import analytics
import payment
import utm_attr


class FunnelAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = analytics.Analytics(Path(self.tmp.name), transport=lambda item: None)
        self.client._schedule_flush = lambda: None
        self.patcher = patch.object(analytics, '_client', self.client)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def assert_safe(self):
        for item in self.client.pending():
            props = item['properties']
            for prefix in ('', 'initial_'):
                for key, value in utm_attr.DEFAULTS.items():
                    self.assertEqual(props[prefix + key], value)
            self.assertFalse(set(props) & analytics.FORBIDDEN_PROP_KEYS)
            self.assertFalse(any(part in key.lower() for key in props for part in ('url', 'query', 'credit', 'amount', 'order')))

    def test_defaults_and_session_end(self):
        self.client.notify_billing_click('dialog')
        self.client._enqueue_session_end(self.client.clock())
        self.assert_safe()

    def test_unsafe_attribution(self):
        utm_attr.save_store({'first': {'utm_source': 'https://unsafe.test', 'utm_content': 'a b'},
                             'last': {'utm_campaign': 'x&y=z', 'utm_term': 'x' * 81,
                                      'utm_content': ' safe-token ', 'gclid': 'private'}}, Path(self.tmp.name))
        self.client.track('app_open', {'utm_term': 'caller-value'})
        self.assert_safe()
        props = self.client.pending()[0]['properties']
        self.assertEqual(props['utm_content'], 'safe-token')
        self.assertNotIn('utm_term', props)
        self.assertNotIn('initial_utm_content', props)

    def test_mocked_success_and_failure(self):
        body = payment.payload(22, 20, 'limeng')
        with patch.object(payment, 'post', return_value={'code': 200, 'data': {
                'orderNo': 'synthetic', 'redirectUrl': 'https://powertokens.ai/payment'}}):
            payment.create('synthetic', body)
        state = dict(package_id=22, channel='limeng')
        with patch.object(payment, 'post', return_value={'code': 200, 'data': {'status': 1}}):
            payment.track_paid_status(state, payment.status('synthetic', 'synthetic'))
            payment.track_paid_status(state, 1)
        for result in ({'code': 401}, {'code': 200, 'data': {'orderNo': 'synthetic', 'redirectUrl': 'https://invalid.test'}}):
            with patch.object(payment, 'post', return_value=result):
                with self.assertRaises(ValueError): payment.create('synthetic', body)
        with patch.object(payment, 'post', side_effect=OSError()):
            with self.assertRaises(OSError): payment.create('synthetic', body)
        events = [x['event'] for x in self.client.pending()]
        self.assertEqual(events.count('payment_success'), 1)
        self.assertEqual(events.count('payment_checkout_opened'), 1)
        self.assertEqual(events.count('payment_failed'), 3)
        self.assert_safe()

    def test_closed_dialog_terminal_polling(self):
        import payment_ui
        from unittest.mock import Mock

        for terminal, event in [(1, 'payment_success'), (2, 'payment_cancelled')]:
            with self.subTest(status=terminal):
                app = Mock()
                app.oauth_session = {'access_token': 'synthetic'}
                app._payment_window = None
                app._payment_state = None
                app._oauth_profile.return_value = {'credits': 0}
                callbacks = []
                app.root.after.side_effect = lambda delay, fn: callbacks.append(fn)
                buttons = []
                def button(*args, **kwargs):
                    widget = Mock()
                    buttons.append(kwargs)
                    return widget
                def thread(*args, **kwargs):
                    worker = Mock()
                    worker.start.side_effect = kwargs['target']
                    return worker
                with patch.object(payment_ui.tk, 'Toplevel') as window, \
                        patch.object(payment_ui.tk, 'Frame'), patch.object(payment_ui.tk, 'Label'), \
                        patch.object(payment_ui.tk, 'Canvas'), patch.object(payment_ui.tk, 'Radiobutton'), \
                        patch.object(payment_ui.tk, 'StringVar') as variable, \
                        patch.object(payment_ui.ttk, 'Label'), patch.object(payment_ui.ttk, 'Entry'), \
                        patch.object(payment_ui.ttk, 'Button', side_effect=button), \
                        patch.object(payment_ui.threading, 'Thread', side_effect=thread), \
                        patch.object(payment_ui.webbrowser, 'open'), \
                        patch.object(payment, 'create', return_value={'orderNo': 'synthetic', 'redirectUrl': 'synthetic'}), \
                        patch.object(payment, 'status', side_effect=[0, terminal]), \
                        patch.object(payment_ui.oauth_desktop, 'fetch_userinfo', side_effect=OSError()):
                    variable.return_value.get.return_value = 'fixed_5'
                    with patch.object(payment_ui, 'checkout_payload', return_value=payment.payload(33, 5, 'paypal')):
                        payment_ui.show(app)
                        buttons[0]['command']()
                        callbacks.pop(0)()  # create completes
                    payment_ui.close_payment_dialog(window.return_value, app._payment_state)
                    window.return_value.withdraw.assert_called_once()
                    before = len([x for x in self.client.pending() if x['event'] == event])
                    while callbacks:
                        callbacks.pop(0)()
                    self.assertIsNone(app._payment_state['order'])
                    payment_ui.apply_checkout_status(app._payment_state, terminal)
                    self.assertEqual(len([x for x in self.client.pending() if x['event'] == event]), before + 1)
                    props = [x['properties'] for x in self.client.pending() if x['event'] == event][-1]
                    self.assertEqual(props['package_id'], '33')
                    self.assertEqual(props['channel'], 'paypal')
                    self.assert_safe()

    def test_terminal_event_properties_and_deduplication(self):
        state = dict(package_id=22, channel='paypal', order='synthetic',
                     amount=99, credits=99, token='synthetic')
        with patch.object(payment, '_notify') as notify:
            for status in (0, 1, 1, 2, 2):
                payment.track_paid_status(state, status)
        self.assertEqual(notify.call_args_list, [
            unittest.mock.call('success', package_id=22, channel='paypal'),
            unittest.mock.call('cancelled', package_id=22, channel='paypal')])

    def test_pending_status_ignores_credit_changes(self):
        import payment_ui
        state = dict(order='synthetic', package_id=22, channel='paypal')
        self.assertFalse(payment_ui.apply_checkout_status(state, 0))
        self.assertEqual(state['order'], 'synthetic')
        self.assertEqual(self.client.pending(), [])

    def test_event_allowlists_and_fail_open(self):
        for event in ('payment_start', 'payment_checkout_opened', 'payment_failed', 'payment_success'):
            clean = analytics.sanitize_properties(event, dict(package_id=22, channel='paypal', source='dialog', error_code='private', order_no='private', url='private', amount=99, credits=99))
            self.assertEqual(clean['package_id'], '22')
            self.assertEqual(clean['error_code'], 'UNKNOWN')
            self.assertFalse(set(clean) & analytics.FORBIDDEN_PROP_KEYS)
            bad = analytics.sanitize_properties(event, dict(package_id=True, channel='other'))
            self.assertNotIn('package_id', bad)
            self.assertNotIn('channel', bad)
        with patch.object(analytics, 'notify_payment_start', side_effect=RuntimeError()), patch.object(payment, 'post', return_value={'code': 200, 'data': {'orderNo': 'synthetic', 'redirectUrl': 'https://powertokens.ai/payment'}}):
            self.assertTrue(payment.create('synthetic', payment.payload(22, 20, 'paypal')))

    def test_failure_diagnostics(self):
        import io
        import urllib.error
        cases = [
            ({'code': 200, 'data': {'orderNo': 'private', 'redirectUrl': 'https://evil.test'}},
             dict(error_code='VALIDATION', fail_stage='checkout', has_payment_ref=True, redirect_ok=False, redirect_host='unlisted')),
            ({'code': 403, 'msg': 'TOKEN private'},
             dict(error_code='AUTH', fail_stage='response', api_status=403, message_category='authentication')),
            ({'code': 500, 'message': 'private'},
             dict(error_code='UNKNOWN', fail_stage='response', api_status=500, message_category='other-redacted')),
            (payment.CreateResponse({'code': 401}, 401),
             dict(error_code='AUTH', fail_stage='response', http_status=401, api_status=401)),
            (payment.CreateResponse([], 200), dict(error_code='UNKNOWN', fail_stage='response', http_status=200)),
        ]
        for result, expected in cases:
            with self.subTest(expected=expected), patch.object(payment, 'post', return_value=result):
                with self.assertRaises(ValueError): payment.create('private', {})
                props = self.client.pending()[-1]['properties']
                for key, value in expected.items(): self.assertEqual(props[key], value)
        for error, expected in [
            (OSError('private'), dict(error_code='NETWORK', fail_stage='transport')),
            (urllib.error.HTTPError('private', 403, 'private', {}, io.BytesIO(b'{"code":403,"msg":"credential private"}')),
             dict(error_code='AUTH', fail_stage='response', http_status=403, api_status=403, message_category='authentication')),
        ]:
            with patch.object(payment, 'post', side_effect=error):
                with self.assertRaises(Exception): payment.create('private', {})
            props = self.client.pending()[-1]['properties']
            for key, value in expected.items(): self.assertEqual(props[key], value)
            if expected['fail_stage'] == 'transport': self.assertNotIn('http_status', props)
        self.assert_safe()

    def test_explicit_ui_handlers(self):
        import payment_ui
        from unittest.mock import Mock
        variable = Mock()
        payment_ui.select_payment(variable, 'fixed_5', 'package')
        payment_ui.select_payment(variable, 'limeng', 'channel')
        self.assertEqual(self.client.pending(), [])
        for key, selection, package in [('fixed_5', 'preset-5', '33'), ('fixed_20', 'preset-20', '22'), ('fixed_50', 'preset-50', '23'), ('custom', 'custom', '33')]:
            payment_ui.select_payment(variable, key, 'package', explicit=True)
            props = self.client.pending()[-1]['properties']
            self.assertEqual((props['selection'], props['package_id']), (selection, package))
        payment_ui.select_payment(variable, 'paypal', 'channel', explicit=True)
        self.assertEqual(self.client.pending()[-1]['event'], 'payment_channel_select')
        state = dict(order='private', package_id=22, channel='paypal')
        win = Mock()
        payment_ui.close_payment_dialog(win, state)
        win.withdraw.assert_called_once()
        self.assertTrue(self.client.pending()[-1]['properties']['pending_checkout'])
        payment_ui.cancel_pending_checkout(state)
        payment_ui.cancel_pending_checkout(state)
        self.assertEqual([x['event'] for x in self.client.pending()].count('payment_cancelled'), 1)
        self.assertNotIn('payment_failed', [x['event'] for x in self.client.pending()])
        payment_ui.close_payment_dialog(win, state)
        self.assertFalse(self.client.pending()[-1]['properties']['pending_checkout'])
        self.assert_safe()

    def test_strict_diagnostic_sanitizer(self):
        dirty = dict(http_status=True, api_status='401', has_payment_ref=1, redirect_ok='false',
                     pending_checkout=0, fail_stage='private', message_category='private',
                     redirect_protocol='ftp', redirect_host='evil.test', selection='fixed_5',
                     amount=1, credits=1, balance=1, orderNo='private', checkout_url='private',
                     code=401, url='private', message='private', has_order_number=True, safe_url='private')
        clean = analytics.sanitize_properties('payment_failed', dirty)
        self.assertFalse(set(dirty) & set(clean))
        valid = dict(http_status=599, api_status=99999, has_payment_ref=False, redirect_ok=False,
                     fail_stage='checkout', message_category='absent', redirect_protocol='invalid', redirect_host='invalid')
        clean = analytics.sanitize_properties('payment_failed', valid)
        for key, value in valid.items(): self.assertEqual(clean[key], value)
        clean = analytics.sanitize_properties('payment_start', valid)
        self.assertFalse(set(valid) & set(clean))
        for key, value in [('http_status', 99), ('http_status', 600), ('api_status', -1), ('api_status', 100000)]:
            self.assertNotIn(key, analytics.sanitize_properties('payment_failed', {key: value}))
