import subprocess
import unittest
from unittest.mock import patch

import engine


class GpuExportTests(unittest.TestCase):
    def tearDown(self):
        engine.available_encoders.cache_clear()

    @patch('engine.run')
    @patch('engine.binary', return_value='ffmpeg')
    def test_probe_uses_supported_vertical_export_size_and_real_options(self, _binary, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 1),
            subprocess.CompletedProcess([], 1),
        ]

        encoders = engine.available_encoders()

        self.assertEqual([item['id'] for item in encoders], ['cpu', 'nvidia'])
        command = run.call_args_list[0].args[0]
        self.assertIn('color=c=black:s=720x1280:r=30:d=0.1', command)
        self.assertEqual(command[command.index('-c:v') + 1], 'h264_nvenc')
        self.assertEqual(command[command.index('-preset') + 1], 'p4')
        self.assertEqual(run.call_args_list[0].kwargs['timeout'], 30)

    def test_gpu_speed_presets_are_translated_per_vendor(self):
        expected = {
            'nvidia': ('h264_nvenc', 'p1'),
            'intel': ('h264_qsv', 'veryfast'),
            'amd': ('h264_amf', 'speed'),
        }
        for encoder, (codec, speed) in expected.items():
            with self.subTest(encoder=encoder):
                args = engine._video_encoder_args(encoder, 'ultrafast')
                self.assertEqual(args[args.index('-c:v') + 1], codec)
                speed_option = '-quality' if encoder == 'amd' else '-preset'
                self.assertEqual(args[args.index(speed_option) + 1], speed)


if __name__ == '__main__':
    unittest.main()
