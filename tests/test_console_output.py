import importlib.util
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_script(module_name: str, filename: str):
    path = REPO_ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeConsole:
    def __init__(self):
        self.errors = None

    def reconfigure(self, **kwargs):
        self.errors = kwargs.get("errors")


class ConsoleOutputTests(unittest.TestCase):
    def test_both_audits_enable_safe_unicode_console_errors(self):
        modules = [
            load_script("wp_url_audit", "wp-url-audit.py"),
            load_script("wp_text_policy_audit", "wp-text-policy-audit.py"),
        ]

        for module in modules:
            with self.subTest(module=module.__name__):
                stream = FakeConsole()
                module.configure_console_stream(stream)
                self.assertEqual(stream.errors, "backslashreplace")

    def test_stream_without_reconfigure_is_accepted(self):
        module = load_script("wp_url_audit_no_stream", "wp-url-audit.py")
        module.configure_console_stream(object())


if __name__ == "__main__":
    unittest.main()
