import i18n
i18n.set_language('zh')

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storyboard as sb
import wan_core as w


class JsonExtractTests(unittest.TestCase):
    def test_plain_and_fenced(self):
        data = {'clips': [{'title': 'A', 'duration': 8, 'prompt': 'a cat runs'}]}
        self.assertEqual(sb.extract_json_payload(json.dumps(data))['clips'][0]['title'], 'A')
        fenced = 'Here you go:\n```json\n%s\n```\nThanks' % json.dumps(data)
        self.assertEqual(sb.normalize_rows(sb.extract_json_payload(fenced))[0]['prompt'], 'a cat runs')

    def test_prose_wrapped_array(self):
        raw = 'Result:\n[{"title":"1","duration":5,"prompt":"rain"},{"title":"2","duration":5,"prompt":"sun"}]\nDone.'
        rows = sb.normalize_rows(sb.extract_json_payload(raw))
        self.assertEqual(len(rows), 2)


class StoryboardCallTests(unittest.TestCase):
    def test_default_text_model_is_free_glm(self):
        i18n.set_language('zh')
        self.assertEqual(sb.DEFAULT_TEXT_MODEL, 'glm-4.7-flash')
        self.assertTrue(sb.is_free_text_model('glm-4.7-flash'))
        self.assertFalse(sb.is_free_text_model('qwen3-max'))
        self.assertIn('免费', sb.text_model_label('glm-4.7-flash'))
        i18n.set_language('en')
        self.assertIn('free', sb.text_model_label('glm-4.7-flash'))

    def test_retry_on_bad_json_then_success(self):
        good = json.dumps({'clips': [
            {'title': 'Hook', 'duration': 8, 'prompt': 'Product on white table, soft light.'},
            {'title': 'Detail', 'duration': 8, 'prompt': 'Macro of the logo, shallow DOF.'},
        ]})
        calls = []

        def fake_api(method, url, key, body=None, timeout=30):
            calls.append(body)
            # First reply is messy; second is clean JSON
            if len(calls) == 1:
                content = 'Sure! Here are ideas without JSON.'
            else:
                content = good
            return 200, {'choices': [{'message': {'content': content}}]}

        with patch.object(sb, 'api', side_effect=fake_api):
            jobs, meta = sb.storyboard_from_script(
                'A product launch script about earbuds.', 'sk-test',
                clip_count=2, duration=8, text_model='glm-4.7-flash')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['model'], 'glm-4.7-flash')
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]['state'], 'queued')
        self.assertTrue(meta['free_text'])
        self.assertIn('soft light', jobs[0]['prompt'])

    def test_banned_text_model_falls_back_to_default(self):
        good = json.dumps({'clips': [{'title': 'A', 'duration': 5, 'prompt': 'hello world scene'}]})

        def fake_api(method, url, key, body=None, timeout=30):
            self.assertEqual(body['model'], 'glm-4.7-flash')
            return 200, {'choices': [{'message': {'content': good}}]}

        with patch.object(sb, 'api', side_effect=fake_api):
            jobs, meta = sb.storyboard_from_script('x', 'sk', text_model='deepseek-v4')
        self.assertEqual(meta['text_model'], 'glm-4.7-flash')
        self.assertEqual(len(jobs), 1)

    def test_rows_to_xlsx_roundtrip(self):
        good = json.dumps({'clips': [
            {'title': 'SKU', 'duration': 8, 'prompt': 'Ear buds on marble, 8 seconds.'},
        ]})
        with patch.object(sb, 'api', return_value=(200, {'choices': [{'message': {'content': good}}]})):
            jobs, _ = sb.storyboard_from_script('script', 'sk', duration=8, model_id='wan3.0-video')
        path = Path(tempfile.mkdtemp()) / 'out.xlsx'
        sb.rows_to_xlsx(jobs, path)
        from batch_import import import_jobs
        imported, skipped = import_jobs(path)
        self.assertEqual(skipped, [])
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]['state'], 'queued')
