"""Exercise UI submission wiring without opening a native window or using an API."""
import i18n
i18n.set_language('zh')  # Existing tests check the original Chinese wording.

import unittest
from unittest.mock import Mock, patch
from app import App
try:
    from test_inputs import REPORTED_PROMPT
except ImportError:
    from tests.test_inputs import REPORTED_PROMPT

class Value:
    def __init__(self, value): self.value = value
    def get(self, *args): return self.value
    def set(self, value): self.value = value

class FlowTests(unittest.TestCase):
    def app(self, prompt, automatic=True):
        app = App.__new__(App)
        app.busy = False
        app.keys = ['sk-any-prefix']
        app.prompt = Value(prompt)
        app.auto_prompt = Value(automatic)
        app.prompt_hint = Value('')
        app.duration, app.ratio, app.resolution = Value('5'), Value('16:9'), Value('720p')
        app.model_id = Value('wan3.0-video')
        app.media, app.seed = {}, Value('')
        app.new_output = lambda: 'test.mp4'
        client = Mock()
        app.launch = lambda operation: operation(client)
        return app, client

    def test_recognized_parameters_reach_request(self):
        app, client = self.app('生成一段二十秒，9:16竖屏，一只猫在奔跑。节奏：0-2秒开场，2-17秒奔跑，17-20秒结尾，最后1秒循环。')
        app.generate()
        request = client.submit.call_args.args[1]
        self.assertEqual(request['seconds'], '20')
        self.assertEqual(request['ratio'], '9:16')
        self.assertEqual(request['model'], 'wan3.0-video')
        self.assertIs(request['generate_audio'], True)
        self.assertEqual(request['prompt'], app.prompt.value)

    def test_disabled_recognition_uses_manual_values(self):
        app, client = self.app('20秒，竖屏', False)
        app.generate()
        request = client.submit.call_args.args[1]
        self.assertEqual(request['seconds'], '5')
        self.assertEqual(request['ratio'], '16:9')

    def test_ambiguous_prompt_does_not_submit(self):
        app, client = self.app('5秒或者20秒')
        with patch('app.messagebox.showerror') as error:
            app.generate()
        client.submit.assert_not_called()
        error.assert_called_once()

    def test_reported_prompt_submits_16_seconds_16_9(self):
        app, client = self.app(REPORTED_PROMPT)
        self.assertEqual(app.apply_prompt()['warnings'], [])
        self.assertEqual(app.prompt_hint.value, '已识别：16 秒 · 16:9')
        app.generate()
        request = client.submit.call_args.args[1]
        self.assertEqual((request['seconds'], request['ratio']), ('16', '16:9'))

    def test_timeline_hint_mentions_inference(self):
        app, client = self.app('0-5秒：走路；5-10秒：停下；10-16秒：回头')
        app.apply_prompt()
        self.assertEqual(app.prompt_hint.value, '已识别：16 秒。时长根据分镜时间轴推断（最后一段结束于 16 秒）')
