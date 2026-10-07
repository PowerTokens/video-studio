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
        self.assertIn('utm_medium=app', wan_core.UTM)
        for url in urls:
            self.assertIn('utm_medium=app', url, url)
            self.assertNotIn('utm_medium=oss', url, url)
            self.assertIn('utm_campaign=video-studio', url, url)

    def test_onboarding_points_at_api_keys_not_signup(self):
        i18n.set_language('zh')
        self.assertTrue(i18n.t('onboard_signup_url').startswith('https://powertokens.ai/zh-Hans/api-keys?'))
        i18n.set_language('en')
        self.assertTrue(i18n.t('onboard_signup_url').startswith('https://powertokens.ai/api-keys?'))


if __name__ == '__main__':
    unittest.main()
