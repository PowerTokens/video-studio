import io
import json
import unittest
from unittest.mock import patch
import payment

class PaymentTests(unittest.TestCase):
    def test_packages_and_validation(self):
        self.assertEqual([p['id'] for p in payment.PACKAGES], [22,23,24,33])
        self.assertEqual(payment.payload(22, 5, 'limeng'), dict(amount=20,package_id=22,payment_channel='limeng'))
        for amount in (4,2001,5.5,'bad'):
            with self.assertRaises(ValueError): payment.payload(33, amount, 'paypal')
        for package_id, amount in ((22,20),(23,50),(24,200)):
            self.assertEqual(payment.payload(package_id,5,'paypal')['amount'],amount)
        self.assertEqual(payment.payload(33,2000,'paypal')['amount'],2000)
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
