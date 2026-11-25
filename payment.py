"""Payment transport. No cookie, token logging, or runtime package scraping."""
import analytics
import urllib.error
import json
import logging
import re
from urllib.request import Request, urlopen
from urllib.parse import urlparse
PACKAGES = [
    {'id': 35, 'amount_type': 1, 'package_name': '5元档', 'sales_price': 5, 'credits_amount': 5000, 'is_recommended': 1, 'status': 1, 'currency': 'USD'},
    {'id': 33, 'amount_type': 2, 'package_name': '15', 'sales_price': 0, 'min_recharge_amount': 5, 'max_recharge_amount': 2000, 'credits_amount': 0, 'is_recommended': 0, 'status': 1, 'currency': 'USD'},
    {'id': 22, 'amount_type': 1, 'package_name': '01', 'sales_price': 20, 'credits_amount': 20000, 'is_recommended': 0, 'status': 1, 'currency': 'USD'},
    {'id': 23, 'amount_type': 1, 'package_name': '02', 'sales_price': 50, 'credits_amount': 50000, 'is_recommended': 0, 'status': 1, 'currency': 'USD'},
]
BASE = 'https://powertokens.ai/api'

def valid_redirect(url):
    p = urlparse(url)
    return p.scheme == 'https' and p.hostname in ('gateway.powerwinpay.com', 'powertokens.ai', 'www.powertokens.ai', 'www.paypal.com', 'paypal.com') and not p.username and not p.password and p.port in (None, 443)

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
        try:
            result = json.load(response)
        except ValueError as exc:
            exc.http_status = getattr(response, 'status', None)
            raise
        if path == '/v1/payment/create':
            return CreateResponse(result, getattr(response, 'status', None))
        return result

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


class CreateResponse:
    def __init__(self, body, http_status):
        self.body = body
        self.http_status = http_status


def response_diagnostics(result):
    if not isinstance(result, dict):
        return {}
    props = {}
    value = result.get('code')
    if type(value) in (int, float) and 0 <= value <= 99999 and value == int(value):
        props['api_status'] = int(value)
    message = result.get('msg') or result.get('message')
    props['message_category'] = ('absent' if message is None else
        'authentication' if re.search(r'token|unauthori|credential|登录|令牌', str(message), re.I) else 'other-redacted')
    return props


def redirect_diagnostics(value):
    props = dict(redirect_ok=False, redirect_protocol='invalid', redirect_host='invalid')
    try:
        if not isinstance(value, str) or not value:
            return props
        parsed = urlparse(value)
        host = parsed.hostname
        props['redirect_protocol'] = parsed.scheme if parsed.scheme in ('http', 'https') else 'other' if parsed.scheme else 'invalid'
        props['redirect_host'] = host if host in ('gateway.powerwinpay.com', 'powertokens.ai', 'www.powertokens.ai') else 'unlisted' if host else 'invalid'
        props['redirect_ok'] = bool(valid_redirect(value))
    except (ValueError, TypeError, AttributeError):
        pass
    return props


def create(token, body):
    props = dict(package_id=body.get('package_id'), channel=body.get('payment_channel'))
    _notify('start', **props, source='dialog')
    diagnostics = {}
    try:
        result = post('/v1/payment/create', token, body)
    except urllib.error.HTTPError as exc:
        diagnostics['http_status'] = exc.code
        try:
            diagnostics.update(response_diagnostics(json.loads(exc.read(65536))))
        except Exception:
            pass
        _notify('failed', **props, **diagnostics, fail_stage='response',
                error_code='AUTH' if exc.code in (401, 403) or diagnostics.get('api_status') in (401, 403) else 'UNKNOWN')
        raise
    except ValueError as exc:
        _notify('failed', **props, fail_stage='response', http_status=getattr(exc, 'http_status', None), error_code='UNKNOWN')
        raise
    except Exception:
        _notify('failed', **props, fail_stage='transport', error_code='NETWORK')
        raise
    if isinstance(result, CreateResponse):
        diagnostics['http_status'] = result.http_status
        result = result.body
    diagnostics.update(response_diagnostics(result))
    http_status = diagnostics.get('http_status')
    if (not isinstance(result, dict) or result.get('code') != 200 or
            (type(http_status) is int and not 200 <= http_status < 300)):
        _notify('failed', **props, **diagnostics, fail_stage='response',
                error_code='AUTH' if http_status in (401, 403) or diagnostics.get('api_status') in (401, 403) else 'UNKNOWN')
        raise ValueError('Could not start checkout.')
    data = result.get('data')
    data = data if isinstance(data, dict) else {}
    diagnostics['has_payment_ref'] = bool(data.get('orderNo'))
    diagnostics.update(redirect_diagnostics(data.get('redirectUrl')))
    if not diagnostics['has_payment_ref'] or not diagnostics['redirect_ok']:
        _notify('failed', **props, **diagnostics, fail_stage='checkout', error_code='VALIDATION')
        raise ValueError('Could not start checkout.')
    _notify('checkout_opened', **props)
    return data

def status(token, order):
    result = post('/finance/get-order-info', token, {'order_no': order})
    if result.get('code', 200) != 200: raise ValueError('status unavailable')
    return (result.get('data') or result).get('status')
