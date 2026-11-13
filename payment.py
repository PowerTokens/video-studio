"""Payment transport. No cookie, token logging, or runtime package scraping."""
import analytics
import urllib.error
import json
import logging
import re
from urllib.request import Request, urlopen
from urllib.parse import urlparse
PACKAGES = [{'id': 22, 'amount_type': 1, 'package_name': '01', 'sales_price': 20, 'credits_amount': 20000, 'is_recommended': 1, 'status': 1, 'currency': 'USD'}, {'id': 23, 'amount_type': 1, 'package_name': '02', 'sales_price': 50, 'credits_amount': 50000, 'is_recommended': 0, 'status': 1, 'currency': 'USD'}, {'id': 24, 'amount_type': 1, 'package_name': '03', 'sales_price': 200, 'credits_amount': 200000, 'is_recommended': 0, 'status': 1, 'currency': 'USD'}, {'id': 33, 'amount_type': 2, 'package_name': '15', 'sales_price': 0, 'min_recharge_amount': 5, 'max_recharge_amount': 2000, 'credits_amount': 0, 'is_recommended': 0, 'status': 1, 'currency': 'USD'}]
BASE = 'https://powertokens.ai/api'

def valid_redirect(url):
    p = urlparse(url)
    return p.scheme == 'https' and p.hostname in ('gateway.powerwinpay.com', 'powertokens.ai', 'www.powertokens.ai') and not p.username and not p.password and p.port in (None, 443)

def payload(package_id, amount, channel):
    package = next(p for p in PACKAGES if p['id'] == int(package_id))
    value = float(amount)
    if not value.is_integer() or channel not in ('limeng', 'paypal'):
        raise ValueError('invalid amount or channel')
    value = int(value)
    if package['amount_type'] == 2:
        if not 5 <= value <= 2000: raise ValueError('invalid amount')
    else:
        value = package['sales_price']
    return dict(amount=value, package_id=package['id'], payment_channel=channel)

def post(path, token, body):
    logging.getLogger(__name__).info(
        '[payment] headerUsesRawToken: false, headerUsesBearer: true')
    req = Request(BASE + path, data=json.dumps(body).encode(), headers={
        'Authorization': 'Bearer ' + re.sub(r'^(?:Bearer\s+)+', '', token.strip(), flags=re.I), 'Content-Type': 'application/json'})
    with urlopen(req, timeout=15) as response:
        return json.load(response)

def _notify(event, **props):
    try:
        getattr(analytics, 'notify_payment_' + event)(**props)
    except Exception:
        pass


def track_paid_status(state, status):
    # Only confirmed paid status qualifies; a balance change alone does not.
    if status == 1 and not state.get('payment_success_tracked'):
        _notify('success', package_id=state.get('package_id'), channel=state.get('channel'))
        state['payment_success_tracked'] = True


def create(token, body):
    props = dict(package_id=body.get('package_id'), channel=body.get('payment_channel'))
    _notify('start', **props, source='dialog')
    try:
        try:
            result = post('/v1/payment/create', token, body)
        except ValueError:
            _notify('failed', **props, error_code='VALIDATION')
            raise
        data = result.get('data') or {}
        if result.get('code') != 200:
            _notify('failed', **props, error_code='AUTH' if result.get('code') in (401, 403) else 'UNKNOWN')
            raise ValueError('Could not start checkout.')
        try:
            valid = data.get('orderNo') and valid_redirect(data.get('redirectUrl', ''))
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            _notify('failed', **props, error_code='VALIDATION')
            raise ValueError('Could not start checkout.')
    except ValueError:
        raise
    except Exception as exc:
        code = 'AUTH' if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403) else 'NETWORK' if isinstance(exc, (OSError, TimeoutError)) else 'UNKNOWN'
        _notify('failed', **props, error_code=code)
        raise
    _notify('checkout_opened', **props)
    return data

def status(token, order):
    result = post('/finance/get-order-info', token, {'order_no': order})
    if result.get('code', 200) != 200: raise ValueError('status unavailable')
    return (result.get('data') or result).get('status')
