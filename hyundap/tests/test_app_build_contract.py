"""실제 화면 구성에서 필수 컴포넌트 계약이 유지되는지 확인한다."""
import unittest

from hyundap.app import build_app


class AppBuildContractTests(unittest.TestCase):
    def test_backend_and_demo_build(self):
        for mode in ('backend', 'demo'):
            with self.subTest(mode=mode):
                app = build_app(mode=mode)
                self.assertTrue(app.blocks)
