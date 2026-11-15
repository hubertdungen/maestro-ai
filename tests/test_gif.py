import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import maestro_gif as G
import maestro_features as F


class GifTests(unittest.TestCase):
    def scene(self):
        return {'background': '#102030', 'layers': [{'svg': '<circle cx="32" cy="32" r="12" fill="#ff8040"/>', 'dy': 8}]}

    def test_renderer_outputs_moving_frames_loop_and_duration(self):
        from PIL import Image, ImageChops
        raw = G.render_scene(G.validate_scene(self.scene()), 64, 64)
        im = Image.open(io.BytesIO(raw))
        self.assertEqual(im.size, (64, 64))
        self.assertEqual(im.n_frames, 48)
        self.assertEqual(im.info['loop'], 0)
        first = im.convert('RGB'); im.seek(12)
        self.assertIsNotNone(ImageChops.difference(first, im.convert('RGB')).getbbox())
        duration = 0
        for frame in range(im.n_frames):
            im.seek(frame); duration += im.info['duration']
        self.assertEqual(duration, 3000)

    def test_scene_rejects_resources_code_entities_and_nonfinite_motion(self):
        for fragment in ['<image href="file:///etc/passwd"/>', '<script>alert(1)</script>',
                         '<rect style="fill:url(https://example.org)"/>', '<g onload="hi"/>',
                         '<!DOCTYPE g><g/>', '<path fill="url(file:///etc/passwd)"/>']:
            scene = self.scene(); scene['layers'][0]['svg'] = fragment
            with self.subTest(fragment=fragment), self.assertRaises(ValueError):
                G.validate_scene(scene)
        scene = self.scene(); scene['layers'][0]['dy'] = float('nan')
        with self.assertRaises(ValueError): G.validate_scene(scene)

    def test_prompt_and_aspect_reach_scene_generator(self):
        with patch.object(G, 'create_scene', return_value=G.validate_scene(self.scene())) as create, patch.object(G, 'render_scene', return_value=b'GIF89a'):
            data, width, height = G.generate('a red sailboat', '16:9')
            create.assert_called_once_with('a red sailboat', 640, 360)
            self.assertEqual((width, height), (640, 360))

    def test_failure_never_writes_a_canned_gif_to_gallery(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(F, 'GALLERY_DIR', pathlib.Path(folder)), patch.object(G, 'generate', side_effect=RuntimeError('API unavailable')):
            with self.assertRaises(RuntimeError): F.generate_animated_gif('a cat')
            self.assertEqual(list(pathlib.Path(folder).iterdir()), [])

    def test_blank_prompt_invalid_aspect_and_concurrent_work_do_not_call_provider(self):
        with patch.object(G, 'create_scene') as create:
            for prompt, aspect in [('', '1:1'), ('cat', 'bad')]:
                with self.assertRaises(ValueError): G.generate(prompt, aspect)
            G.RENDER_LOCK.acquire()
            try:
                with self.assertRaises(RuntimeError): G.generate('cat')
            finally: G.RENDER_LOCK.release()
            create.assert_not_called()

    def test_malformed_art_gets_one_repair_but_network_failures_do_not_retry(self):
        good = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.scene())}}]}
        bad = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"background":'}}]}
        with patch('maestro._extract_deepseek_key', return_value='test-key'), patch('maestro_readonly._request', side_effect=[bad, good]) as request:
            scene = G.create_scene('a cat', 640, 640)
            self.assertEqual(scene['background'], '#102030')
            self.assertEqual(request.call_count, 2)
        with patch('maestro._extract_deepseek_key', return_value='test-key'), patch('maestro_readonly._request', side_effect=RuntimeError('API down')) as request:
            with self.assertRaises(RuntimeError): G.create_scene('a cat', 640, 640)
            self.assertEqual(request.call_count, 1)

    def test_success_records_original_prompt_format_and_animation_metadata(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(F, 'GALLERY_DIR', pathlib.Path(folder)), patch.object(F, 'ensure_feature_dirs'), patch.object(G, 'generate', return_value=(b'GIF89a', 360, 640)) as generate:
            meta = F.generate_animated_gif('orange cat waving', '9:16')
            generate.assert_called_once_with('orange cat waving', '9:16')
            self.assertEqual(meta['prompt'], 'orange cat waving')
            self.assertEqual((meta['width'], meta['height'], meta['fps']), (360, 640, 16))
            self.assertEqual(pathlib.Path(meta['path']).read_bytes(), b'GIF89a')
            self.assertEqual(json.loads((pathlib.Path(folder) / 'index.json').read_text())[0], meta)
