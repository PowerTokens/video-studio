import unittest
from input_helpers import parse_keys, infer_prompt

# Reported on Windows: leading total + 16:9 + shot ranges.
REPORTED_PROMPT = ('16秒，16:9横屏。写实美式青春剧，夜街余波。美国大学城夜晚街道：路灯映在微湿柏油路上，'
                   '稀疏停靠车辆。女主穿夹克，男主穿连帽衫，两人并肩走。0-5秒：侧后方跟拍双人镜头，街道环境音与轻风。'
                   '男主说：“你没必要拿我当笑点。”5-10秒：走到斑马线附近放慢。红灯禁止通行。中近景交替。'
                   '10-16秒：两人在路灯下对视，最后1秒定格。')


class KeyTests(unittest.TestCase):
    def test_accepts_other_prefixes(self):
        for key in ('sk-pt-abc123', 'sk-abc123', 'pt_abc123', 'abc123XYZ', 'a.b_c-d+/='):
            self.assertEqual(parse_keys(key), [key])

    def test_pasted_headers_quotes_and_invisible_chars(self):
        for text in ('Bearer sk-abc123', 'Authorization: Bearer sk-abc123', '"sk-abc123"', '“sk-abc123”', '\ufeffsk-abc\u200b123', 'API_KEY=sk-abc123'):
            self.assertEqual(parse_keys(text), ['sk-abc123'])

    def test_multiple_and_deduplicated(self):
        self.assertEqual(parse_keys('sk-aaa, sk-bbb；sk-aaa\nBearer sk-ccc'), ['sk-aaa', 'sk-bbb', 'sk-ccc'])

    def test_rejects_empty_and_urls_without_echoing_input(self):
        for text in ('', '   ', 'https://example.com/secret', '这是我的密钥'):
            with self.assertRaises(ValueError) as caught:
                parse_keys(text)
            if text.strip():
                self.assertNotIn(text, str(caught.exception))

class PromptTests(unittest.TestCase):
    def test_chinese_prompt(self):
        result = infer_prompt('生成一个20秒的视频，9:16竖屏，一只猫在草原奔跑')
        self.assertEqual((result['duration'], result['ratio'], result['warnings']), (20, '9:16', []))

    def test_english_prompt(self):
        result = infer_prompt('A 10-second video, 16:9 landscape')
        self.assertEqual((result['duration'], result['ratio'], result['warnings']), (10, '16:9', []))

    def test_chinese_numbers_and_minutes(self):
        for text, seconds in [('二十秒', 20), ('十五秒', 15), ('三十秒', 30), ('半分钟', 30), ('0.5 minutes', 30), ('两秒', 2)]:
            self.assertEqual(infer_prompt(text)['duration'], seconds)

    def test_ratio_variants(self):
        for text, ratio in [('竖屏', '9:16'), ('横版', '16:9'), ('正方形', '1:1'), ('9：16', '9:16'), ('16 x 9', '16:9'), ('9/16', '9:16')]:
            self.assertEqual(infer_prompt(text)['ratio'], ratio)

    def test_missing_values_keep_manual_selection(self):
        result = infer_prompt('一只猫在草原奔跑')
        self.assertIsNone(result['duration'])
        self.assertIsNone(result['ratio'])
        self.assertFalse(result['warnings'])

    def test_conflicting_duration(self):
        result = infer_prompt('5秒或10秒')
        self.assertIsNone(result['duration'])
        self.assertTrue(result['warnings'])

    def test_total_duration_has_priority(self):
        result = infer_prompt('总时长20秒，开场5秒，收尾3秒')
        self.assertEqual(result['duration'], 20)
        self.assertFalse(result['warnings'])

    def test_ranges_are_not_silently_chosen(self):
        for text in ('5-10秒', '5到10秒', '5 to 10 seconds'):
            self.assertIsNone(infer_prompt(text)['duration'])
            self.assertTrue(infer_prompt(text)['warnings'])

    def test_out_of_bounds_are_clamped_with_a_note(self):
        for text, seconds in (('120秒', 30), ('1 minute', 30), ('总时长1秒', 2), ('0-5秒，5-40秒', 30)):
            result = infer_prompt(text)
            self.assertEqual(result['duration'], seconds, text)
            self.assertFalse(result['warnings'], text)
            self.assertTrue(any('2–30' in note for note in result['notes']), text)

    def test_fraction_is_refused(self):
        result = infer_prompt('1.5秒')
        self.assertIsNone(result['duration'])
        self.assertTrue(result['warnings'])

    def test_ratio_conflict_or_unsupported(self):
        for text in ('16:9竖屏', '9:16或1:1', '比例4:3'):
            self.assertIsNone(infer_prompt(text)['ratio'])
            self.assertTrue(infer_prompt(text)['warnings'])

class StoryboardTests(unittest.TestCase):
    def test_intro_duration_wins_over_shot_ranges(self):
        text = '生成一段10秒、9:16竖屏、1080P、24fps的超写实短视频。节奏：0-2秒出现钩子；2-7秒升级；7-10秒完成反转。'
        result = infer_prompt(text)
        self.assertEqual(result['duration'], 10)
        self.assertEqual(result['ratio'], '9:16')
        self.assertFalse(result['warnings'])

    def test_total_wins_over_shot_ranges_and_last_second(self):
        result = infer_prompt('总时长12秒，节奏0–2秒，2–8秒，8–12秒；最后1秒循环。')
        self.assertEqual(result['duration'], 12)
        self.assertFalse(result['warnings'])

    def test_ranges_only_use_timeline_end(self):
        for text in ('节奏0-2秒，2-7秒，7-10秒', '0-5秒：走路；5-10秒：停下；10-16秒：回头'):
            result = infer_prompt(text)
            self.assertEqual(result['duration'], int(text.rsplit('-', 1)[1].split('秒')[0]))
            self.assertEqual(result['duration_source'], 'timeline')
            self.assertFalse(result['warnings'])
            self.assertIn('分镜时间轴', result['notes'][0])

    def test_ranges_without_timeline_start_stay_ambiguous(self):
        # "5-10秒" alone is a duration range, not a shot timeline.
        result = infer_prompt('一只猫在奔跑，5-10秒')
        self.assertIsNone(result['duration'])
        self.assertTrue(result['warnings'])

    def test_short_standalone_with_timeline_is_refused(self):
        # "停顿2秒" cannot be the total of a timeline that runs to 10 s.
        result = infer_prompt('0-5秒奔跑，5-10秒跳跃，停顿2秒')
        self.assertIsNone(result['duration'])
        self.assertTrue(result['warnings'])

    def test_reported_prompt_leading_total_and_ratio(self):
        result = infer_prompt(REPORTED_PROMPT)
        self.assertEqual((result['duration'], result['ratio'], result['warnings']), (16, '16:9', []))
        self.assertEqual(result['duration_source'], 'lead')

    def test_standalone_total_variants_ignore_ranges(self):
        for text in ('16秒，0-5秒，5-10秒', '16 秒，0-5秒，5-10秒', '16s, 0-5s, 5-10s', '总时长16秒。0-5秒，5-10秒',
                     '时长：16秒。0-5秒，5-10秒', 'Total duration: 16 seconds. 0-5s, 5-10s', '猫咪视频 16秒，0-5秒，5-16秒'):
            result = infer_prompt(text)
            self.assertEqual((result['duration'], result['warnings']), (16, []), text)

    def test_english_16s(self):
        for text in ('16s, 16:9 landscape. 0-5s: walk; 5-10s: stop; 10-16s: smile', 'A 16s clip, 16:9 landscape'):
            result = infer_prompt(text)
            self.assertEqual((result['duration'], result['ratio'], result['warnings']), (16, '16:9', []), text)

    def test_conflicting_totals_are_refused(self):
        for text in ('总时长16秒，0-5秒，5-10秒，总时长20秒', '16秒，16:9横屏。0-5秒：走。后半段20秒慢镜头。', '猫咪 16秒 或 20秒'):
            result = infer_prompt(text)
            self.assertIsNone(result['duration'], text)
            self.assertIn('多个时长', result['warnings'][0], text)

    def test_explicit_total_over_intro(self):
        result = infer_prompt('生成一段10秒视频，修正：总时长20秒。节奏0-2秒，2-20秒。')
        self.assertEqual(result['duration'], 20)

if __name__ == '__main__':
    unittest.main()
