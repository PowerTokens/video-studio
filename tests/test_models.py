import i18n
i18n.set_language('zh')

import datetime
import unittest
from unittest.mock import patch

import models
import wan_core as w


class RegistryTests(unittest.TestCase):
    def test_default_and_order(self):
        self.assertEqual(models.DEFAULT_MODEL_ID, 'wan3.0-video')
        ids = [m.id for m in models.list_models()]
        self.assertEqual(ids[0], 'wan3.0-video')
        self.assertIn('kling-v3', ids)
        self.assertIn('dreamina-seedance-2-0-fast-260128', ids)
        self.assertIn('dreamina-seedance-2-5-260628', ids)
        self.assertIn('wan3.0-video-prime', ids)
        self.assertNotIn('kling-video-o1', ids)
        self.assertNotIn('kling-v3-omni', ids)

    def test_aliases(self):
        self.assertEqual(models.resolve_model_id('Seedance 2.5'), 'dreamina-seedance-2-5-260628')
        self.assertEqual(models.resolve_model_id('kling v3'), 'kling-v3')
        self.assertEqual(models.resolve_model_id(''), 'wan3.0-video')
        with self.assertRaises(KeyError):
            models.resolve_model_id('kling-video-o1')

    def test_wan_request_builder(self):
        body = w.payload('雨落屋顶', 8, '1080p', '9:16')
        self.assertEqual(body['model'], 'wan3.0-video')
        self.assertEqual(body['size'], '1080P')
        self.assertEqual(body['seconds'], '8')
        self.assertIs(body['generate_audio'], True)

    def test_prime_request_builder(self):
        body = w.payload('hi', 5, '720p', '16:9', model='wan3.0-video-prime')
        self.assertEqual(body['model'], 'wan3.0-video-prime')
        self.assertEqual(body['size'], '720P')

    def test_seedance_fast_uses_media_text_and_lowercase_size(self):
        body = w.payload('a cat runs', 6, '720p', '16:9',
                         model='dreamina-seedance-2-0-fast-260128')
        self.assertEqual(body['model'], 'dreamina-seedance-2-0-fast-260128')
        self.assertEqual(body['size'], '720p')
        self.assertEqual(body['media'][0], {'type': 'text', 'text': 'a cat runs'})
        self.assertNotIn('prompt', body)

    def test_seedance_fast_rejects_1080p_and_short_duration(self):
        with self.assertRaises(w.TaskError):
            w.payload('hi', 5, '1080p', '16:9', model='dreamina-seedance-2-0-fast-260128')
        with self.assertRaises(w.TaskError):
            w.payload('hi', 2, '720p', '16:9', model='dreamina-seedance-2-0-fast-260128')

    def test_seedance_25_allows_30s_1080p(self):
        body = w.payload('story', 30, '1080p', '21:9', model='dreamina-seedance-2-5-260628')
        self.assertEqual(body['seconds'], '30')
        self.assertEqual(body['size'], '1080p')
        self.assertEqual(body['ratio'], '21:9')

    def test_kling_text_and_image_paths(self):
        t2v = w.payload('rabbit reads', 5, '720p', '1:1', model='kling-v3')
        self.assertEqual(t2v['model_name'], 'kling-v3')
        self.assertEqual(t2v['mode'], 'std')
        self.assertEqual(t2v['sound'], 'on')
        self.assertEqual(t2v['_poll_kind'], 'kling_t2v')
        i2v = w.payload('smile', 5, '1080p', '16:9',
                        media=[{'type': 'first_frame', 'url': 'https://example.com/a.png'}],
                        model='kling-v3')
        self.assertEqual(i2v['mode'], 'pro')
        self.assertEqual(i2v['image'], 'https://example.com/a.png')
        self.assertEqual(i2v['_poll_kind'], 'kling_i2v')
        self.assertNotIn('aspect_ratio', i2v)

    def test_kling_rejects_reference_media_and_seed(self):
        with self.assertRaises(w.TaskError):
            w.payload('hi', 5, '720p', '16:9',
                      media=[{'type': 'reference_image', 'url': 'https://example.com/a.png'}],
                      model='kling-v3')
        with self.assertRaises(w.TaskError):
            w.payload('hi', 5, '720p', '16:9', seed='12', model='kling-v3')

    def test_estimate_per_model(self):
        self.assertEqual(w.estimate_cost(10, '720p', model_id='wan3.0-video-prime'), 1.4)
        self.assertEqual(w.estimate_cost(5, '720p', model_id='dreamina-seedance-2-0-fast-260128'), 0.605)
        self.assertEqual(w.estimate_cost(5, '1080p', model_id='dreamina-seedance-2-5-260628'), 2.845)
        self.assertEqual(w.estimate_cost(5, '720p', model_id='kling-v3'), 0.63)
        with patch.object(w, 'local_today', return_value=datetime.date(2026, 10, 7)):
            self.assertEqual(w.estimate_cost(10, '720p', model_id='wan3.0-video'), 0.4)
        with patch.object(w, 'local_today', return_value=datetime.date(2026, 10, 8)):
            self.assertEqual(w.estimate_cost(10, '720p', model_id='wan3.0-video'), 1.0)

    def test_submit_stores_model_and_uses_endpoint(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        out = Path(temp.name) / 'out.mp4'
        client = w.Client(data_dir=Path(temp.name))
        request = w.payload('hi', 5, '720p', '16:9', model='kling-v3')

        def fake_api(method, url, key, payload=None, timeout=30):
            if method == 'POST':
                self.assertIn('/kling/v1/videos/text2video', url)
                return 200, {'code': 0, 'data': {'task_id': 'kling_task_1', 'task_status': 'submitted'}}
            self.assertIn('/kling/v1/videos/text2video/kling_task_1', url)
            return 200, {'code': 0, 'data': {
                'task_status': 'succeed',
                'task_result': {'videos': [{'url': 'https://cdn.example.com/v.mp4'}]},
            }}

        def fake_try_download(key, url, out_path):
            Path(out_path).write_bytes(b'\x00\x00\x00\x18ftypisom' + b'x' * 64)
            client.save(state='已完成', bytes=76, download_url='')
            return 'ok'

        with patch.object(w, 'api', side_effect=fake_api), patch.object(client, 'try_download', side_effect=fake_try_download):
            record = client.submit(['sk-test'], request, out)
        self.assertEqual(record['model'], 'kling-v3')
        self.assertEqual(record['poll_kind'], 'kling_t2v')
        self.assertEqual(record['task_id'], 'kling_task_1')
        self.assertTrue(out.exists())

    def test_legacy_resume_defaults_to_wan_poll(self):
        record = {'task_id': 'old', 'local_id': 'x'}
        self.assertEqual(models.poll_kind_for_record(record), 'unified')
        self.assertEqual(models.family_for_record(record), 'wan')



    def test_descriptions_present_bilingual(self):
        for spec in models.list_models():
            self.assertTrue(spec.description_en.strip(), spec.id)
            self.assertTrue(spec.description_zh.strip(), spec.id)
            self.assertNotIn('$', spec.description_en)
            self.assertNotIn('$', spec.description_zh)
            self.assertNotIn('折扣', spec.description_zh)
            self.assertNotIn('discount', spec.description_en.lower())
            # Bounds mentioned for marketer clarity
            self.assertRegex(spec.description_en, r'\d+')
            en = spec.description('en')
            zh = spec.description('zh')
            self.assertEqual(en, spec.description_en)
            self.assertEqual(zh, spec.description_zh)

    def test_description_follows_ui_language(self):
        import i18n
        i18n.set_language('en')
        self.assertIn('seconds', models.get_model('kling-v3').description().lower())
        i18n.set_language('zh')
        self.assertIn('秒', models.get_model('kling-v3').description())


class BatchModelColumnTests(unittest.TestCase):
    def test_optional_model_column(self):
        from batch_import import build_job
        job = build_job('S', 2, {'prompt': 0, 'model': 1, 'duration': 2, 'resolution': 3},
                        ['一只猫', 'kling-v3', '5', '720p'])
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(job['model'], 'kling-v3')
        self.assertEqual(job['payload']['model_name'], 'kling-v3')

    def test_missing_model_defaults_wan(self):
        from batch_import import build_job
        job = build_job('S', 2, {'prompt': 0, 'duration': 1, 'resolution': 2},
                        ['一只猫', '5', '720p'])
        self.assertEqual(job['model'], 'wan3.0-video')
        self.assertEqual(job['payload']['model'], 'wan3.0-video')


class SnapTests(unittest.TestCase):
    def test_seedance_fast_snaps_1080p_and_long_duration(self):
        spec = models.get_model('dreamina-seedance-2-0-fast-260128')
        duration, resolution, ratio, changes = models.snap_params(spec, 30, '1080p', '16:9')
        self.assertEqual(duration, 15)
        self.assertEqual(resolution, '720p')
        self.assertEqual(ratio, '16:9')
        fields = [c[0] for c in changes]
        self.assertEqual(fields, ['duration', 'resolution'])
        i18n.set_language('zh')
        zh = models.format_adjust_notice(spec, changes)
        self.assertEqual(zh, 'Seedance 2.0 Fast 最长 15 秒、最高 720p，已自动调整')
        i18n.set_language('en')
        en = models.format_adjust_notice(spec, changes)
        self.assertEqual(
            en,
            'Seedance 2.0 Fast supports up to 15 s and 720p, so settings were adjusted automatically.',
        )

    def test_notice_only_mentions_changed_limits(self):
        spec = models.get_model('dreamina-seedance-2-0-fast-260128')
        _d, _r, _ratio, changes = models.snap_params(spec, 30, '720p', '16:9')
        self.assertEqual([c[0] for c in changes], ['duration'])
        i18n.set_language('zh')
        zh = models.format_adjust_notice(spec, changes)
        self.assertEqual(zh, 'Seedance 2.0 Fast 最长 15 秒，已自动调整')
        self.assertNotIn('720p', zh)
        i18n.set_language('en')
        en = models.format_adjust_notice(spec, changes)
        self.assertEqual(
            en,
            'Seedance 2.0 Fast supports up to 15 s, so settings were adjusted automatically.',
        )

    def test_unchanged_when_already_valid(self):
        spec = models.get_model('wan3.0-video')
        duration, resolution, ratio, changes = models.snap_params(spec, 10, '720p', '9:16')
        self.assertEqual((duration, resolution, ratio, changes), (10, '720p', '9:16', []))
        self.assertEqual(models.format_adjust_notice(spec, changes), '')

    def test_kling_snaps_unsupported_ratio_and_resolution(self):
        spec = models.get_model('kling-v3')
        duration, resolution, ratio, changes = models.snap_params(spec, 2, '480p', '4:3')
        self.assertEqual(duration, 3)
        self.assertEqual(resolution, '720p')
        self.assertIn(ratio, spec.ratios)
        self.assertTrue(changes)
        i18n.set_language('zh')
        zh = models.format_adjust_notice(spec, changes)
        self.assertIn('最短 3 秒', zh)
        self.assertIn('最低 720p', zh)  # 480p snapped up; kling min resolution
        self.assertIn('比例仅支持', zh)
        self.assertTrue(zh.endswith('，已自动调整'))


class BatchSnapWarnTests(unittest.TestCase):
    def test_batch_row_warns_with_suggestion_without_changing(self):
        from batch_import import build_job
        i18n.set_language('zh')
        job = build_job('Sheet', 2,
                        {'duration': 0, 'resolution': 1, 'prompt': 2, 'model': 3},
                        ['30', '1080p', 'a short scene', 'dreamina-seedance-2-0-fast-260128'])
        self.assertEqual(job['state'], 'invalid')
        self.assertIsNone(job['payload'])
        self.assertEqual(job['note'], 'Seedance 2.0 Fast 最长 15 秒、最高 720p，请按此修改')
        self.assertEqual(job['suggested']['duration'], 15)
        self.assertEqual(job['suggested']['resolution'], '720p')
        i18n.set_language('en')
        job_en = build_job('Sheet', 2,
                           {'duration': 0, 'resolution': 1, 'prompt': 2, 'model': 3},
                           ['30', '1080p', 'a short scene', 'dreamina-seedance-2-0-fast-260128'])
        self.assertEqual(
            job_en['note'],
            'Seedance 2.0 Fast supports up to 15 s and 720p; please update this row.',
        )


if __name__ == '__main__':
    unittest.main()
