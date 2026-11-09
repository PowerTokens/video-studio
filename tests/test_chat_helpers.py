import i18n
i18n.set_language('zh')

import json
import unittest

import chat_helpers as ch
from storyboard import ALLOWED_TEXT_MODELS, DEFAULT_TEXT_MODEL, resolve_text_model


class AllowlistTests(unittest.TestCase):
    def test_five_models_free_first(self):
        self.assertEqual(ALLOWED_TEXT_MODELS[0], DEFAULT_TEXT_MODEL)
        self.assertEqual(list(ALLOWED_TEXT_MODELS), [
            'glm-4.7-flash',
            'seed-1-6-flash-250715',
            'deepseek-flash',
            'MiniMax-M2.7',
            'glm-5.3-flash',
        ])
        self.assertNotIn('qwen3-max', ALLOWED_TEXT_MODELS)
        self.assertEqual(resolve_text_model('deepseek-v4'), DEFAULT_TEXT_MODEL)
        self.assertEqual(resolve_text_model('qwen3-max'), DEFAULT_TEXT_MODEL)
        self.assertEqual(resolve_text_model('MiniMax-M2.7'), 'MiniMax-M2.7')


class MessageBuildTests(unittest.TestCase):
    def test_system_plus_history(self):
        msgs = ch.build_api_messages(
            [{'role': 'user', 'content': '一只猫'}, {'role': 'assistant', 'content': '{"assistant_message":"好","ready":false,"plan":null}'}],
            lang='zh')
        self.assertEqual(msgs[0]['role'], 'system')
        self.assertIn('JSON', msgs[0]['content'])
        self.assertEqual(msgs[1]['role'], 'user')
        self.assertEqual(msgs[2]['role'], 'assistant')

    def test_optional_new_user_turn(self):
        msgs = ch.build_api_messages([], user_text='hello', lang='en')
        self.assertEqual(msgs[-1], {'role': 'user', 'content': 'hello'})
        self.assertIn('Fill Generate', msgs[0]['content'])


class PlanParseTests(unittest.TestCase):
    def test_ready_plan_clamped(self):
        raw = json.dumps({
            'assistant_message': '可以了',
            'ready': True,
            'plan': {
                'title': '猫',
                'prompt': '一只橘色猫在窗台晒太阳，柔和侧光，缓慢推镜。',
                'model': 'wan3.0-video',
                'duration': 20,
                'resolution': '1080p',
                'ratio': '9:16',
                'summary': '窗台橘猫',
            },
        })
        parsed = ch.parse_assistant_payload(raw)
        self.assertTrue(parsed['parse_ok'])
        self.assertTrue(parsed['ready'])
        self.assertEqual(parsed['assistant_message'], '可以了')
        plan = parsed['plan']
        self.assertEqual(plan['prompt'][:4], '一只橘色')
        self.assertLess(plan['duration'], 16)
        self.assertEqual(plan['resolution'], '720p')

    def test_bad_json_falls_back(self):
        parsed = ch.parse_assistant_payload('not json', fallback_message='fallback')
        self.assertFalse(parsed['parse_ok'])
        self.assertFalse(parsed['ready'])
        self.assertEqual(parsed['assistant_message'], 'not json')

    def test_outline_and_usable_flags(self):
        messages = [{'role': 'user', 'content': '短剧开场，雨夜街头'}]
        plan = {'title': '开场', 'prompt': '雨夜，霓虹倒映在湿柏油路上。'}
        outline = ch.script_outline_from_chat(messages, plan)
        self.assertIn('雨夜街头', outline)
        self.assertIn('霓虹', outline)
        self.assertTrue(ch.has_usable_fill_content(plan))
        self.assertTrue(ch.has_usable_batch_content(messages, plan))
        self.assertFalse(ch.has_usable_fill_content(None))


if __name__ == '__main__':
    unittest.main()
