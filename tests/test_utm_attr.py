import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import utm_attr


class UtmAttrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_capture_sets_first_and_last(self):
        last = utm_attr.capture({
            'utm_source': 'newsletter',
            'utm_medium': 'email',
            'utm_campaign': 'launch',
        }, data_dir=self.data_dir)
        self.assertEqual(last['utm_source'], 'newsletter')
        store = utm_attr.load_store(self.data_dir)
        self.assertEqual(store['first']['utm_source'], 'newsletter')
        self.assertEqual(store['last']['utm_campaign'], 'launch')
        # Second capture updates last only
        utm_attr.capture({'utm_source': 'ads', 'utm_medium': 'cpc', 'utm_campaign': 'retarget'},
                         data_dir=self.data_dir)
        store = utm_attr.load_store(self.data_dir)
        self.assertEqual(store['first']['utm_source'], 'newsletter')
        self.assertEqual(store['last']['utm_source'], 'ads')

    def test_defaults_when_empty(self):
        last = utm_attr.capture({}, data_dir=self.data_dir)
        self.assertEqual(last['utm_source'], 'videostudio')
        self.assertEqual(last['utm_medium'], 'app')
        self.assertEqual(last['utm_campaign'], 'video-studio')

    def test_params_for_sets_content(self):
        utm_attr.capture({}, data_dir=self.data_dir)
        p = utm_attr.params_for(utm_attr.CONTENT_OAUTH, data_dir=self.data_dir)
        self.assertEqual(p['utm_content'], 'app.oauth_authorize')
        self.assertEqual(p['utm_medium'], 'app')

    def test_append_to_url(self):
        utm_attr.capture({'utm_source': 'videostudio', 'utm_medium': 'app', 'utm_campaign': 'video-studio'},
                         data_dir=self.data_dir)
        url = utm_attr.append_to_url(
            'https://powertokens.ai/api-keys?utm_source=old&x=1',
            content='app.signup',
            data_dir=self.data_dir,
        )
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(q['x'], '1')
        self.assertEqual(q['utm_source'], 'videostudio')
        self.assertEqual(q['utm_content'], 'app.signup')
        self.assertNotEqual(q.get('utm_source'), 'old')

    def test_capture_from_env(self):
        with patch.dict(os.environ, {
            'POWERTTOKENS_UTM_SOURCE': 'partner',
            'POWERTTOKENS_UTM_MEDIUM': 'referral',
            'POWERTTOKENS_UTM_CAMPAIGN': 'video-studio',
        }, clear=False):
            last = utm_attr.capture_from_env(data_dir=self.data_dir)
        self.assertEqual(last['utm_source'], 'partner')
        self.assertEqual(last['utm_medium'], 'referral')


if __name__ == '__main__':
    unittest.main()
