import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import uvicorn
from app.runtime import windows_server_loop


class RuntimeTests(unittest.TestCase):
    def test_uvicorn_uses_the_explicit_selector_factory(self):
        config = uvicorn.Config('app.main:app', loop='app.runtime:windows_server_loop')
        loop = config.get_loop_factory()()
        try:
            self.assertIsInstance(loop, asyncio.SelectorEventLoop)
        finally:
            loop.close()

    def test_factory_returns_selector_without_a_global_policy_change(self):
        loop = windows_server_loop()
        try:
            self.assertIsInstance(loop, asyncio.SelectorEventLoop)
        finally:
            loop.close()


if __name__ == '__main__':
    unittest.main()
