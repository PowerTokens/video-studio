"""English UI: string tables, language choice, English prompts and English spreadsheets."""
import i18n
i18n.set_language('zh')

import csv
import datetime
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from batch_import import build_job, batch_identity, header_kind, import_jobs
from input_helpers import infer_prompt
import studio_ui

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r'%(?:\.\d+)?[sdgf%]')


class English(unittest.TestCase):
    """Run the test body with the English UI; always restore Chinese for the other test modules."""
    def setUp(self):
        i18n.set_language('en')
        self.addCleanup(i18n.set_language, 'zh')


class StringTableTests(unittest.TestCase):
    def test_every_zh_key_has_an_en_key_and_vice_versa(self):
        self.assertEqual(sorted(set(i18n.ZH) - set(i18n.EN)), [])
        self.assertEqual(sorted(set(i18n.EN) - set(i18n.ZH)), [])

    def test_placeholders_match(self):
        for key, zh in i18n.ZH.items():
            en = i18n.EN[key]
            variants = en if isinstance(en, tuple) and not isinstance(zh, tuple) else (en,)
            for value in variants:
                if isinstance(zh, str):
                    self.assertEqual(PLACEHOLDER.findall(zh), PLACEHOLDER.findall(value), key)

    def test_english_strings_are_english_and_mention_no_other_model(self):
        for key, value in i18n.EN.items():
            for text in (value if isinstance(value, tuple) else (value,)):
                self.assertIsNone(re.search('[\u4e00-\u9fff]', text), key)
                for other in ('wan 2', 'wan2', 'kling', 'veo', 'sora', 'runway', 'seedance', 'hailuo'):
                    self.assertNotIn(other, text.lower(), key)

    def test_chinese_wording_is_unchanged(self):
        # Spot-check the original v1.10 strings.
        self.assertEqual(i18n.ZH['subtitle'], 'Wan 3.0 视频批量生成')
        self.assertEqual(i18n.ZH['promo'], 'Wan 3.0 限时折扣至 10 月 7 日')
        self.assertEqual(i18n.ZH['tab_history'], '任务记录 / 恢复')
        self.assertEqual(i18n.ZH['start_all'], '开始 / 继续全部（付费生成）')

    def test_requested_english_copy(self):
        self.assertEqual(i18n.EN['subtitle'], 'Wan 3.0 batch video generation')
        self.assertEqual(i18n.EN['promo'], 'Wan 3.0 limited-time discount until Oct 7')
        self.assertEqual([i18n.EN[k] for k in ('tab_generate', 'tab_batch', 'tab_keys')],
                         ['Generate video', 'Batch import', 'API Key'])


class LanguageChoiceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.settings = Path(folder.name) / 'PowerTokensWan' / 'settings.json'
        patcher = patch.object(i18n, 'SETTINGS_PATH', self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(i18n.set_language, 'zh')

    def test_t_switches_immediately(self):
        i18n.set_language('en')
        self.assertEqual(i18n.t('tab_generate'), 'Generate video')
        self.assertEqual(i18n.t('key_count', 2, count=2), '2 keys')
        self.assertEqual(i18n.t('key_count', 1, count=1), '1 key')
        i18n.set_language('zh')
        self.assertEqual(i18n.t('tab_generate'), '生成视频')
        self.assertEqual(i18n.t('key_count', 2, count=2), '共 2 个 Key')

    def test_choice_persists_and_keeps_other_settings(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text('{"other": 1}', encoding='utf-8')
        i18n.set_language('en', persist=True)
        with patch.object(i18n, '_current', None), patch.object(i18n, 'system_language', return_value='zh'):
            self.assertEqual(i18n.get_language(), 'en')
        self.assertEqual(i18n.load_settings(), {'other': 1, 'language': 'en'})
        i18n.set_language('zh', persist=True)
        with patch.object(i18n, '_current', None), patch.object(i18n, 'system_language', return_value='en'):
            self.assertEqual(i18n.get_language(), 'zh')

    def test_default_follows_windows_display_language(self):
        with patch.object(i18n, '_current', None):
            for langid, expected in ((0x0804, 'zh'), (0x0404, 'zh'), (0x0409, 'en'), (0x0411, 'en')):
                with patch.object(i18n, 'windows_ui_language', return_value=langid):
                    self.assertEqual(i18n.system_language(), expected, hex(langid))
            with patch.object(i18n, 'windows_ui_language', return_value=None), \
                    patch.dict(os.environ, {'LC_ALL': '', 'LC_MESSAGES': '', 'LANG': 'zh_CN.UTF-8'}):
                self.assertEqual(i18n.system_language(), 'zh')
            with patch.object(i18n, 'windows_ui_language', return_value=0x0409):
                i18n._current = None
                self.assertEqual(i18n.get_language(), 'en')  # No settings file yet.

    def test_broken_settings_file_falls_back_to_system(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text('{not json', encoding='utf-8')
        with patch.object(i18n, '_current', None), patch.object(i18n, 'system_language', return_value='en'):
            self.assertEqual(i18n.get_language(), 'en')

    def test_rejects_unknown_language(self):
        with self.assertRaises(ValueError):
            i18n.set_language('fr')


class PromoTests(English):
    def test_english_badge_until_end_date(self):
        self.assertEqual(studio_ui.promotion_text(datetime.date(2026, 10, 7)), 'Wan 3.0 limited-time discount until Oct 7')
        self.assertEqual(studio_ui.promotion_text(datetime.date(2026, 10, 8)), '')
        self.assertEqual(studio_ui.subtitle(), 'Wan 3.0 batch video generation')


class EnglishDurationTests(English):
    def duration(self, text):
        result = infer_prompt(text)
        return result['duration'], result['warnings']

    def test_units(self):
        for text in ('10s', '10 s', '10 sec', '10 secs', '10 seconds', '10-second', 'a 10-sec clip',
                     'A ten-second video of a cat'):
            self.assertEqual(self.duration(text), (10, []), text)

    def test_stated_total(self):
        for text in ('total length 15s', 'Total length: 15 seconds', 'Duration: 15s', 'duration of 15 seconds',
                     'Length is 15 sec', 'total runtime 15s', 'Duration: about 15 seconds', '15 seconds total',
                     'Make a 15s video', 'Generate a 15-second clip'):
            result = infer_prompt(text)
            self.assertEqual((result['duration'], result['warnings'], result['duration_source']),
                             (15, [], 'declared'), text)

    def test_stated_total_beats_everything_else(self):
        result = infer_prompt('12s teaser. Total length 15s. 0-3s: hook; 3-8s: build; 8-12s: twist')
        self.assertEqual((result['duration'], result['duration_source']), (15, 'declared'))

    def test_duration_at_start_beats_timeline(self):
        result = infer_prompt('16s, 16:9 landscape. 0-5s: walk; 5-10s: stop; 10-16s: smile, hold 1 second')
        self.assertEqual((result['duration'], result['ratio'], result['duration_source'], result['warnings']),
                         (16, '16:9', 'lead', []))

    def test_standalone_duration(self):
        result = infer_prompt('A cat runs across a meadow at sunset, 9:16 vertical, 12 seconds, native birdsong')
        self.assertEqual((result['duration'], result['ratio'], result['duration_source']), (12, '9:16', 'standalone'))

    def test_timeline_ranges(self):
        for text, expected in (('0-3s: hook; 3-8s: build; 8-12s: twist', 12),
                               ('0s-3s intro, 3s-9s chase', 9),
                               ('0 to 4 seconds: close-up. 4 to 11 seconds: wide shot.', 11),
                               ('00:00-00:03 hook. 00:03-00:08 build. 00:08-00:14 payoff', 14),
                               ('[0:00–0:05] walk [0:05–0:12] stop', 12),
                               ('00:00:00 - 00:00:07 opening; 00:00:07 - 00:00:18 chase', 18)):
            result = infer_prompt(text)
            self.assertEqual((result['duration'], result['duration_source'], result['warnings']),
                             (expected, 'timeline', []), text)
            self.assertIn('shot timeline', result['notes'][0])

    def test_timecodes_do_not_break_aspect_ratio(self):
        result = infer_prompt('9:16, 00:00-00:04 close-up; 00:04-00:09 wide shot')
        self.assertEqual((result['duration'], result['ratio'], result['warnings']), (9, '9:16', []))

    def test_clamped_to_2_30_with_note(self):
        for text, expected in (('45 seconds', 30), ('Total length 1s', 2), ('00:00-00:20, 00:20-00:40', 30)):
            result = infer_prompt(text)
            self.assertEqual((result['duration'], result['warnings']), (expected, []), text)
            self.assertTrue(any('2–30' in note for note in result['notes']), text)

    def test_ambiguous_prompts_are_refused_in_english(self):
        for text in ('5 to 10 seconds', '5-10s', '10s or 20s', 'total 16s, later total 20s', '0-5s walk, 5-10s stop, pause 2 seconds',
                     'hold for 1.5 seconds'):
            duration, warnings = self.duration(text)
            self.assertIsNone(duration, text)
            self.assertTrue(warnings, text)
            self.assertIsNone(re.search('[\u4e00-\u9fff]', warnings[0]), warnings[0])

    def test_decades_are_not_durations(self):
        for text in ('A 1980s-style diner, neon lights', 'the 80s, VHS look', "a '90s sitcom set", '80s-style synth music'):
            self.assertEqual(self.duration(text), (None, []), text)
        self.assertEqual(self.duration('A 1980s diner, 12 seconds')[0], 12)

    def test_chinese_prompts_still_work_in_the_english_ui(self):
        self.assertEqual(infer_prompt('16秒，16:9横屏。0-5秒：走路；5-10秒：停下；10-16秒：回头')['duration'], 16)
        self.assertEqual(infer_prompt('生成一段二十秒，9:16竖屏')['duration'], 20)


class ChineseTimelineAdditionsTests(unittest.TestCase):
    def test_chinese_behaviour_with_new_forms(self):
        i18n.set_language('zh')
        result = infer_prompt('00:00-00:03 开场；00:03-00:10 反转')
        self.assertEqual((result['duration'], result['duration_source']), (10, 'timeline'))
        self.assertIn('分镜时间轴', result['notes'][0])
        self.assertIn('多个时长', infer_prompt('5秒或10秒')['warnings'][0])


class EnglishImportTests(English):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def write_csv(self, header, rows):
        path = self.dir / 'episodes.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def test_english_headers(self):
        path = self.write_csv(['Title', 'Duration (s)', 'Resolution', 'Aspect ratio', 'Wan 3.0 Prompt'],
                              [['Episode 1', '12', '1080p', '9:16', 'A lighthouse keeper finds a message in a bottle.'],
                               ['Episode 2', '8 sec', '720P', '1:1', 'The keeper rows out into the fog.']])
        jobs, skipped = import_jobs(path)
        self.assertEqual(skipped, [])
        self.assertEqual([(j['title'], j['payload']['seconds'], j['payload']['size'], j['payload']['ratio'])
                          for j in jobs], [('Episode 1', '12', '1080P', '9:16'), ('Episode 2', '8', '720P', '1:1')])
        self.assertTrue(all(j['state'] == 'queued' for j in jobs))
        self.assertIn('Spreadsheet values first', jobs[0]['note'])

    def test_header_variants(self):
        for header, kind in (('Prompt', 'prompt'), ('Full prompt', 'prompt'), ('Video prompt', 'prompt'),
                             ('Wan 3.0 Prompt', 'prompt'), ('Duration', 'duration'), ('Duration (sec)', 'duration'),
                             ('Length', 'duration'), ('Seconds', 'duration'), ('Resolution', 'resolution'),
                             ('Ratio', 'ratio'), ('Aspect Ratio', 'ratio'), ('aspect_ratio', 'ratio'),
                             ('Name', 'title'), ('Episode', 'title'), ('No.', 'id'), ('ID', 'id'),
                             ('Negative prompt', None), ('Notes', None)):
            self.assertEqual(header_kind(header), kind, header)

    def test_prompt_only_uses_english_detection(self):
        path = self.write_csv(['Prompt'], [['Total length 14s, 9:16 vertical, 1080p. 0-4s: hook; 4-14s: chase']])
        job = import_jobs(path)[0][0]
        self.assertEqual((job['payload']['seconds'], job['payload']['ratio'], job['payload']['size']), ('14', '9:16', '1080P'))
        self.assertEqual(job['title'], 'episodes_row2')

    def test_english_character_setting_and_stable_batch_identity(self):
        english = build_job('S', 2, {'prompt': 0}, ['A 10s chase in the rain'], 'Ning wears a red coat.')
        self.assertTrue(english['prompt'].startswith('[Series characters]\nNing wears a red coat.'))
        self.assertIn('[This episode]\nA 10s chase in the rain', english['payload']['prompt'])
        i18n.set_language('zh')
        chinese_ui = build_job('S', 2, {'prompt': 0}, ['A 10s chase in the rain'], 'Ning wears a red coat.')
        self.assertEqual(chinese_ui['prompt'], english['prompt'])  # Wrapper follows the content, not the UI.
        mixed = build_job('S', 2, {'prompt': 0}, ['雨中追逐10秒'], 'Ning wears a red coat.')
        self.assertTrue(mixed['prompt'].startswith('【全剧固定人物设定】'))
        # The v1.10 form of the same prompt keeps the same batch identity (no resubmission after upgrading).
        legacy = dict(english, prompt='【全剧固定人物设定】\nNing wears a red coat.\n\n【本集剧情】\nA 10s chase in the rain')
        legacy['payload'] = dict(english['payload'], prompt=legacy['prompt'])
        self.assertEqual(batch_identity([english]), batch_identity([legacy]))

    def test_english_template_imports_cleanly(self):
        jobs, skipped = import_jobs(ROOT / 'short-drama-batch-template.xlsx')
        self.assertEqual(skipped, [])
        self.assertEqual([j['title'] for j in jobs], ['Episode 001', 'Episode 002', 'Episode 003'])
        self.assertTrue(all(j['state'] == 'queued' and j['payload']['seconds'] == '10' and
                            j['payload']['ratio'] == '9:16' for j in jobs))
        self.assertIsNone(re.search('[\u4e00-\u9fff]', ''.join(j['prompt'] for j in jobs)))

    def test_template_matches_ui_language(self):
        import batch_ui
        self.assertEqual(batch_ui.template_file(), 'short-drama-batch-template.xlsx')
        i18n.set_language('zh')
        self.assertEqual(batch_ui.template_file(), '短剧批量示例模板.xlsx')
        for name in batch_ui.TEMPLATE_FILES.values():
            self.assertTrue(studio_ui.resource_path(name).is_file(), name)

    def test_english_state_labels_and_errors(self):
        from batch_engine import LABELS
        self.assertEqual(LABELS['completed'], 'Completed')
        self.assertEqual(dict(LABELS)['queued'], 'Queued')
        job = build_job('S', 2, {'prompt': 0, 'duration': 1}, ['hello', '7.5'])
        self.assertEqual((job['state'], job['note']), ('invalid', 'Duration must be a whole number of seconds.'))


if __name__ == '__main__':
    unittest.main()
