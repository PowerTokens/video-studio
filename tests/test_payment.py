import io
import json
import unittest
from unittest.mock import patch
import payment
import payment_ui

class PaymentTests(unittest.TestCase):
    def test_packages_and_validation(self):
        self.assertEqual([p['id'] for p in payment.PACKAGES], [33,22,23])
        self.assertEqual(payment.payload(22, 5, 'limeng'), dict(amount=20,package_id=22,payment_channel='limeng'))
        for amount in (4,2001,5.5,'bad'):
            with self.assertRaises(ValueError): payment.payload(33, amount, 'paypal')
        for package_id, amount in ((22,20),(23,50)):
            self.assertEqual(payment.payload(package_id,5,'paypal')['amount'],amount)
        self.assertEqual(payment.payload(33, 5, 'limeng'),
                         dict(amount=5, package_id=33, payment_channel='limeng'))
        self.assertEqual(payment.payload(33,2000,'paypal')['amount'],2000)
    def test_official_package_metadata(self):
        packages = {p['id']: p for p in payment.PACKAGES}
        self.assertEqual(set(packages), {22, 23, 33})
        self.assertNotIn(24, packages)
        self.assertEqual(packages[33]['amount_type'], 2)
        self.assertEqual(packages[33]['min_recharge_amount'], 5)
        self.assertEqual(packages[33]['max_recharge_amount'], 2000)
        self.assertEqual(packages[22]['is_recommended'], 0)
        self.assertEqual(packages[33]['is_recommended'], 0)

    def test_default_ui_selection_and_badge(self):
        cards = payment_ui.CHECKOUT_CARDS
        self.assertEqual(len(cards), 4)
        self.assertEqual(len({c['key'] for c in cards}), 4)
        self.assertEqual({c['package_id'] for c in cards}, {22, 23, 33})
        self.assertNotIn(24, {c['package_id'] for c in cards})
        self.assertEqual([c['amount'] for c in cards], [5, 20, 50, None])
        self.assertEqual([c['credits'] for c in cards], [5000, 20000, 50000, None])
        self.assertEqual(payment_ui.DEFAULT_SELECTION, 'fixed_5')
        self.assertEqual([c['key'] for c in cards if c['recommended']], ['fixed_5'])
        self.assertFalse(payment_ui.custom_input_visible())
        self.assertEqual(payment_ui.checkout_payload(),
                         dict(package_id=33, amount=5, payment_channel='limeng'))

    def test_ui_selection_visibility_and_payload(self):
        for selection, package_id, amount in (
                ('fixed_5', 33, 5), ('fixed_20', 22, 20), ('fixed_50', 23, 50)):
            self.assertFalse(payment_ui.custom_input_visible(selection))
            for leftover in ('80', 'bad'):
                self.assertEqual(payment_ui.checkout_payload(selection, leftover, 'paypal'),
                                 dict(package_id=package_id, amount=amount, payment_channel='paypal'))
        self.assertTrue(payment_ui.custom_input_visible('custom'))
        for amount in ('80', '2000'):
            self.assertEqual(payment_ui.checkout_payload('custom', amount),
                             dict(package_id=33, amount=int(amount), payment_channel='limeng'))
            self.assertEqual(payment_ui.checkout_payload('fixed_5', amount)['amount'], 5)
        for amount in (4, 2001, 5.5, 'bad'):
            with self.assertRaises(ValueError):
                payment_ui.checkout_payload('custom', amount)

    def test_redirect(self):
        for url in ('http://powertokens.ai/x','https://powertokens.ai.evil.test','https://user@powertokens.ai','https://evil.test','https://powertokens.ai:444'):
            self.assertFalse(payment.valid_redirect(url))
        self.assertTrue(payment.valid_redirect('https://gateway.powerwinpay.com/payment/test'))
    @patch('payment.urlopen')
    def test_create_transport(self, network):
        network.return_value.__enter__.return_value = io.StringIO(json.dumps({'code':200,'data':{'orderNo':'mock-order','redirectUrl':'https://powertokens.ai/payment'}}))
        self.assertEqual(payment.create('mock-token',payment.payload(22,20,'limeng'))['orderNo'],'mock-order')
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, payment.BASE + '/v1/payment/create')
        self.assertEqual(request.get_header('Authorization'),'Bearer mock-token')
        self.assertEqual(json.loads(request.data),dict(amount=20,package_id=22,payment_channel='limeng'))
    @patch('payment.post')
    def test_failure_status_and_missing_redirect(self, post):
        post.return_value = {'code':200,'data':{'status':2}}
        self.assertEqual(payment.status('mock','mock-order'),2)
        post.assert_called_with('/finance/get-order-info', 'mock', {'order_no': 'mock-order'})
        with self.assertRaises(ValueError): payment.create('mock',{})
        post.return_value = {'code':500,'msg':'Try again'}
        with self.assertRaisesRegex(ValueError,'Could not start checkout'): payment.create('mock',{})

    @patch('payment.urlopen')
    def test_custom_and_normalization(self, network):
        network.return_value.__enter__.side_effect = lambda: io.StringIO(json.dumps({'code':200,'data':{'orderNo':'mock-order','redirectUrl':'https://powertokens.ai/payment'}}))
        payment.create(' Bearer bearer mock-token ', payment.payload(33, 50, 'paypal'))
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, payment.BASE + '/v1/payment/create')
        self.assertEqual(request.get_header('Authorization'), 'Bearer mock-token')
        self.assertEqual(json.loads(request.data), dict(amount=50,package_id=33,payment_channel='paypal'))

    @patch('payment.post')
    def test_strict_checkout_contract(self, post):
        for result in (
            {'code':'200','data':{'orderNo':'mock','redirectUrl':'https://powertokens.ai/test'}},
            {'code':200,'data':{'order_no':'mock','redirect_url':'https://powertokens.ai/test'}},
            {'code':200,'data':{'orderNo':'mock','redirectUrl':'https://evil.test'}},
            {'code':401,'msg':'private server detail'},
        ):
            post.return_value = result
            with self.assertRaisesRegex(ValueError, r'^Could not start checkout\.$'):
                payment.create('mock', {})

    @patch('payment.urlopen')
    def test_status_bearer_transport(self, network):
        network.return_value.__enter__.return_value = io.StringIO(json.dumps({'code':200,'data':{'status':1}}))
        self.assertEqual(payment.status(' Bearer bearer mock-token ', 'mock-order'), 1)
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, payment.BASE + '/finance/get-order-info')
        self.assertEqual(request.get_header('Authorization'), 'Bearer mock-token')
        self.assertEqual(json.loads(request.data), {'order_no':'mock-order'})
