import io
import json
import unittest
from unittest.mock import patch
import payment

class PaymentTests(unittest.TestCase):
    def test_packages_and_validation(self):
        self.assertEqual([p['id'] for p in payment.PACKAGES], [22,23,24,33])
        self.assertEqual(payment.payload(22, 5, 'limeng')['amount'], 20)
        for amount in (4,2001,5.5,'bad'):
            with self.assertRaises(ValueError): payment.payload(33, amount, 'paypal')
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
        self.assertEqual(request.get_header('Authorization'),'Bearer mock-token')
        self.assertEqual(json.loads(request.data),dict(amount=20,package_id=22,payment_channel='limeng'))
    @patch('payment.post')
    def test_failure_status_and_missing_redirect(self, post):
        post.return_value = {'code':200,'data':{'status':2}}
        self.assertEqual(payment.status('mock','mock-order'),2)
        with self.assertRaises(ValueError): payment.create('mock',{})
        post.return_value = {'code':500,'msg':'Try again'}
        with self.assertRaisesRegex(ValueError,'Try again'): payment.create('mock',{})
