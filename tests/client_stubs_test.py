import importlib.util
import os
import unittest

from .utils import no_duplicates


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_generator():
    path = os.path.join(ROOT, 'tools', 'generate_client_stubs.py')
    spec = importlib.util.spec_from_file_location('generate_client_stubs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClientStubsTest(unittest.TestCase):

    @no_duplicates
    def test_stubs_are_up_to_date(self):
        generator = load_generator()
        for module, class_name, is_async in generator.STUBS:
            with self.subTest(module=module):
                with open(generator.stub_path(module)) as f:
                    self.assertEqual(
                        generator.generate(module, class_name, is_async),
                        f.read(),
                        'Client stubs are out of date. Run: '
                        'python tools/generate_client_stubs.py')
