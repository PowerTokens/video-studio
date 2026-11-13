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
