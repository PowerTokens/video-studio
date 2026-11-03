import i18n
import unittest

import app
import models
import wan_core


class InAppUtmTests(unittest.TestCase):
    def test_in_app_links_use_medium_app(self):
        urls = [app.OFFICIAL_KEY_URL, app.OFFICIAL_KEY_URL_EN, app.KEY_QUOTA_TIP_URL, app.KEY_QUOTA_TIP_URL_EN]
        for spec in models.list_models():
            urls += [spec.docs_url, spec.docs_url_zh, spec.model_page_url, spec.model_page_url_zh]
        for lang in ('zh', 'en'):
            i18n.set_language(lang)
            urls.append(i18n.t('onboard_signup_url'))
        self.assertEqual(wan_core.UTM, 'utm_source=videostudio&utm_medium=app&utm_campaign=video-studio')
        self.assertEqual(models.UTM, wan_core.UTM)
        for url in urls:
            self.assertIn('utm_source=videostudio', url, url)
            self.assertNotIn('utm_source=github', url, url)
            self.assertIn('utm_medium=app', url, url)
            self.assertNotIn('utm_medium=oss', url, url)
            self.assertIn('utm_campaign=video-studio', url, url)

    def test_onboarding_points_at_api_keys_not_signup(self):
        i18n.set_language('zh')
        self.assertTrue(i18n.t('onboard_signup_url').startswith('https://powertokens.ai/zh-Hans/api-keys?'))
        i18n.set_language('en')
        self.assertTrue(i18n.t('onboard_signup_url').startswith('https://powertokens.ai/api-keys?'))


    def test_billing_url_force_product(self):
        import tempfile
        from pathlib import Path
        import urllib.parse
        import utm_attr
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            utm_attr.capture({
                'utm_source': 'google',
                'utm_medium': 'cpc',
                'utm_campaign': 'ads',
                'gclid': 'g.1',
            }, data_dir=data_dir)
            for lang, expect_path in (('zh', '/zh-Hans/billing'), ('en', '/billing')):
                i18n.set_language(lang)
                # Patch DATA_DIR via append path used by billing_url helper logic
                base = app.BILLING_URL if lang == 'zh' else app.BILLING_URL_EN
                self.assertTrue(base.endswith(expect_path), base)
                url = utm_attr.append_to_url(
                    base, content=utm_attr.CONTENT_BILLING, data_dir=data_dir, force_product=True,
                )
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
                self.assertEqual(q['utm_source'], 'videostudio')
                self.assertEqual(q['utm_medium'], 'app')
                self.assertEqual(q['utm_campaign'], 'video-studio')
                self.assertEqual(q['utm_content'], 'app.billing')
                self.assertEqual(q['gclid'], 'g.1')
            # Live helper uses real DATA_DIR; still must force product defaults.
            live = app.billing_url()
            q2 = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(live).query))
            self.assertEqual(q2['utm_source'], 'videostudio')
            self.assertEqual(q2['utm_medium'], 'app')
            self.assertEqual(q2['utm_campaign'], 'video-studio')
            self.assertEqual(q2['utm_content'], 'app.billing')

if __name__ == '__main__':
    unittest.main()
