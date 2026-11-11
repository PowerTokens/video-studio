"""Remembered API keys: Windows DPAPI, or the macOS login Keychain.

The desktop app calls write_remembered_keys / read_remembered_keys /
clear_remembered_keys. `dpapi_protect` is the Windows blob cipher and stays
the implementation behind `app.protect`.

On macOS the key material is stored as a generic-password item in the login
Keychain (current user, accessible when the session is unlocked). keys.json
only records that the Keychain holds it. No third-party package is required.
Other systems cannot persist keys; callers keep them in memory for the session.
"""
import base64
import ctypes
from ctypes import wintypes
import json
import os
import sys
from pathlib import Path

from i18n import t

# Login Keychain item. The service name is not secret; the password bytes are.
KEYCHAIN_SERVICE = 'PowerTokens Video Studio'
KEYCHAIN_ACCOUNT = 'remembered-api-keys'

# Security.framework OSStatus values we branch on.
_ERR_SEC_SUCCESS = 0
_ERR_SEC_DUPLICATE_ITEM = -25299
_ERR_SEC_ITEM_NOT_FOUND = -25300
_K_CF_STRING_ENCODING_UTF8 = 0x08000100


class SecretStoreError(RuntimeError):
    """The platform secret store refused the operation."""


def storage_kind():
    """'dpapi' on Windows, 'keychain' on macOS, None elsewhere."""
    if os.name == 'nt':
        return 'dpapi'
    if sys.platform == 'darwin':
        return 'keychain'
    return None


def dpapi_protect(raw, decrypt=False):
    """Windows DPAPI: saved secrets are bound to the current Windows account."""
    if os.name != 'nt':
        raise RuntimeError(t('remember_windows_only'))

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(result.data)


def keychain_set(service, account, secret):
    """Create or replace a login-Keychain generic password. `secret` is bytes."""
    _require_darwin()
    secret = bytes(secret)
    status = _secitem_add(service, account, secret)
    if status == _ERR_SEC_DUPLICATE_ITEM:
        status = _secitem_update(service, account, secret)
    if status != _ERR_SEC_SUCCESS:
        raise SecretStoreError(t('keychain_failed', status))


def keychain_get(service, account):
    """Return the password bytes for a login-Keychain generic password."""
    _require_darwin()
    status, data = _secitem_copy(service, account)
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        raise SecretStoreError(t('keychain_missing'))
    if status != _ERR_SEC_SUCCESS:
        raise SecretStoreError(t('keychain_failed', status))
    return data


def keychain_delete(service, account):
    """Remove a login-Keychain item. Missing items are not an error."""
    _require_darwin()
    status = _secitem_delete(service, account)
    if status not in (_ERR_SEC_SUCCESS, _ERR_SEC_ITEM_NOT_FOUND):
        raise SecretStoreError(t('keychain_failed', status))


def write_remembered_keys(path, keys):
    """Persist `keys` (a list of strings) for the current OS.

    Windows writes a DPAPI blob under the JSON key `dpapi` (unchanged format).
    macOS writes a marker file and stores the JSON in the login Keychain.
    """
    raw = json.dumps(list(keys)).encode('utf-8')
    kind = storage_kind()
    path = Path(path)
    if kind == 'dpapi':
        encrypted = dpapi_protect(raw, decrypt=False)
        _atomic_record(path, {'dpapi': base64.b64encode(encrypted).decode('ascii')})
        return
    if kind == 'keychain':
        keychain_set(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, raw)
        _atomic_record(path, {
            'keychain': 'login',
            'service': KEYCHAIN_SERVICE,
            'account': KEYCHAIN_ACCOUNT,
        })
        return
    raise RuntimeError(t('remember_unavailable'))


def read_remembered_keys(path):
    """Inverse of write_remembered_keys. Raises if the record cannot be read."""
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(record, dict):
        raise ValueError('expected an object')
    if 'dpapi' in record:
        raw = dpapi_protect(base64.b64decode(record['dpapi']), decrypt=True)
    elif record.get('keychain') == 'login':
        service = record.get('service') or KEYCHAIN_SERVICE
        account = record.get('account') or KEYCHAIN_ACCOUNT
        raw = keychain_get(service, account)
    else:
        raise ValueError('unknown secret record')
    keys = json.loads(raw.decode('utf-8'))
    if not isinstance(keys, list) or not all(isinstance(item, str) for item in keys):
        raise ValueError('expected a list of keys')
    return keys


def clear_remembered_keys(path):
    """Drop the remembered-key file and, on macOS, the Keychain item."""
    path = Path(path)
    record = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                record = loaded
        except (OSError, ValueError):
            record = {}
    if record.get('keychain') == 'login' or storage_kind() == 'keychain':
        service = record.get('service') or KEYCHAIN_SERVICE
        account = record.get('account') or KEYCHAIN_ACCOUNT
        keychain_delete(service, account)
    path.unlink(missing_ok=True)


def _atomic_record(path, record):
    """Local writer so this module does not import wan_core (import cycle)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    try:
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _require_darwin():
    if sys.platform != 'darwin':
        raise SecretStoreError(t('remember_unavailable'))


_frameworks = None


def _frameworks_or_load():
    """Load Security + CoreFoundation the first time a Keychain call needs them."""
    global _frameworks
    if _frameworks is None:
        _frameworks = _load_frameworks()
    return _frameworks


def _load_frameworks():
    cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    sec = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFDataCreate.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
    cf.CFDataCreate.restype = ctypes.c_void_p
    cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    cf.CFDataGetBytePtr.restype = ctypes.c_void_p
    cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDictionaryCreate.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
    ]
    cf.CFDictionaryCreate.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    # The second argument is an optional out-pointer. c_void_p accepts NULL (None).
    sec.SecItemAdd.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sec.SecItemAdd.restype = ctypes.c_int32
    sec.SecItemCopyMatching.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    sec.SecItemCopyMatching.restype = ctypes.c_int32
    sec.SecItemUpdate.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sec.SecItemUpdate.restype = ctypes.c_int32
    sec.SecItemDelete.argtypes = [ctypes.c_void_p]
    sec.SecItemDelete.restype = ctypes.c_int32
    return cf, sec


def _const(lib, name):
    value = ctypes.c_void_p.in_dll(lib, name).value
    if not value:
        raise SecretStoreError('missing Security constant %s' % name)
    return value


def _struct_addr(lib, name):
    """Address of an exported callbacks struct (not a pointer stored in the symbol)."""
    return ctypes.addressof(ctypes.c_byte.in_dll(lib, name))


def _cf_string(cf, text):
    ref = cf.CFStringCreateWithCString(None, str(text).encode('utf-8'), _K_CF_STRING_ENCODING_UTF8)
    if not ref:
        raise SecretStoreError('could not create a Keychain attribute string')
    return ref


def _cf_data(cf, secret):
    buf = ctypes.create_string_buffer(secret)
    ref = cf.CFDataCreate(None, ctypes.cast(buf, ctypes.c_void_p), len(secret))
    if not ref:
        raise SecretStoreError('could not create Keychain password data')
    return ref


def _cf_dict(cf, pairs):
    keys = (ctypes.c_void_p * len(pairs))()
    values = (ctypes.c_void_p * len(pairs))()
    for index, (key, value) in enumerate(pairs):
        keys[index] = key
        values[index] = value
    ref = cf.CFDictionaryCreate(
        None, keys, values, len(pairs),
        _struct_addr(cf, 'kCFTypeDictionaryKeyCallBacks'),
        _struct_addr(cf, 'kCFTypeDictionaryValueCallBacks'),
    )
    if not ref:
        raise SecretStoreError('could not create a Keychain query')
    return ref


def _release_all(cf, refs):
    for ref in refs:
        if ref:
            cf.CFRelease(ref)


def _identity_pairs(cf, sec, service, account):
    """(pairs, refs_to_release) for a generic-password lookup in the login keychain."""
    service_ref = _cf_string(cf, service)
    account_ref = _cf_string(cf, account)
    pairs = [
        (_const(sec, 'kSecClass'), _const(sec, 'kSecClassGenericPassword')),
        (_const(sec, 'kSecAttrService'), service_ref),
        (_const(sec, 'kSecAttrAccount'), account_ref),
    ]
    return pairs, [service_ref, account_ref]


def _secitem_add(service, account, secret):
    cf, sec = _frameworks_or_load()
    pairs, owned = _identity_pairs(cf, sec, service, account)
    data = _cf_data(cf, secret)
    owned.append(data)
    pairs.append((_const(sec, 'kSecValueData'), data))
    # When unlocked: the item belongs to this login session, not to other users.
    pairs.append((_const(sec, 'kSecAttrAccessible'), _const(sec, 'kSecAttrAccessibleWhenUnlocked')))
    query = _cf_dict(cf, pairs)
    owned.append(query)
    try:
        return int(sec.SecItemAdd(query, None))
    finally:
        _release_all(cf, owned)


def _secitem_update(service, account, secret):
    cf, sec = _frameworks_or_load()
    pairs, owned = _identity_pairs(cf, sec, service, account)
    query = _cf_dict(cf, pairs)
    owned.append(query)
    data = _cf_data(cf, secret)
    owned.append(data)
    update = _cf_dict(cf, [(_const(sec, 'kSecValueData'), data)])
    owned.append(update)
    try:
        return int(sec.SecItemUpdate(query, update))
    finally:
        _release_all(cf, owned)


def _secitem_copy(service, account):
    cf, sec = _frameworks_or_load()
    pairs, owned = _identity_pairs(cf, sec, service, account)
    pairs.append((_const(sec, 'kSecReturnData'), _const(cf, 'kCFBooleanTrue')))
    pairs.append((_const(sec, 'kSecMatchLimit'), _const(sec, 'kSecMatchLimitOne')))
    query = _cf_dict(cf, pairs)
    owned.append(query)
    result = ctypes.c_void_p()
    try:
        status = int(sec.SecItemCopyMatching(query, ctypes.byref(result)))
        if status != _ERR_SEC_SUCCESS or not result.value:
            return status, b''
        owned.append(result.value)
        length = int(cf.CFDataGetLength(result.value))
        pointer = cf.CFDataGetBytePtr(result.value)
        return status, ctypes.string_at(pointer, length) if length else b''
    finally:
        _release_all(cf, owned)


def _secitem_delete(service, account):
    cf, sec = _frameworks_or_load()
    pairs, owned = _identity_pairs(cf, sec, service, account)
    query = _cf_dict(cf, pairs)
    owned.append(query)
    try:
        return int(sec.SecItemDelete(query))
    finally:
        _release_all(cf, owned)
