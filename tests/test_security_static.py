import ast
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")


class StaticSecurityTests(unittest.TestCase):
    def test_no_known_hardcoded_secrets(self):
        forbidden = (
            "a488812c-f62c-4644-b427-7cfa14114676",
            "sk-ywxwqzrhixrezlbuslmrnxhhkvghfkunzgnupvzjhqxbtcyz",
            "super_secret_key_for_research_project",
        )
        for secret in forbidden:
            self.assertNotIn(secret, APP_SOURCE)

    def test_task_helpers_are_not_shadowed(self):
        tree = ast.parse(APP_SOURCE)
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertEqual(names.count("get_current_task"), 1)
        self.assertEqual(names.count("save_current_task"), 1)

    def test_mobile_routes_do_not_reference_missing_templates(self):
        for missing_template in (
            "login_mobile.html",
            "student_mobile.html",
            "teacher_mobile.html",
        ):
            self.assertNotIn(missing_template, APP_SOURCE)

    def test_all_html_forms_and_fetch_clients_have_csrf_support(self):
        login_html = (ROOT / "templates" / "login.html").read_text(encoding="utf-8")
        student_html = (ROOT / "templates" / "student.html").read_text(encoding="utf-8")
        teacher_html = (ROOT / "templates" / "teacher_pc.html").read_text(encoding="utf-8")
        self.assertIn('name="_csrf_token"', login_html)
        self.assertIn("X-CSRF-Token", student_html)
        self.assertIn("X-CSRF-Token", teacher_html)

    def test_environment_backed_api_keys(self):
        self.assertRegex(APP_SOURCE, r'API_KEY\s*=\s*os\.environ\.get\(')
        self.assertRegex(APP_SOURCE, r'EMBEDDING_API_KEY\s*=\s*os\.environ\.get\(')
        self.assertIsNone(
            re.search(r'API_KEY\s*=\s*["\'][A-Za-z0-9_-]{20,}["\']', APP_SOURCE)
        )

    def test_full_license_files_exist(self):
        agpl = ROOT / "LICENSES" / "AGPL-3.0-only.txt"
        apache = ROOT / "LICENSES" / "Apache-2.0.txt"
        self.assertGreater(agpl.stat().st_size, 30_000)
        self.assertGreater(apache.stat().st_size, 10_000)
        self.assertIn(
            "GNU AFFERO GENERAL PUBLIC LICENSE",
            agpl.read_text(encoding="utf-8"),
        )
        self.assertIn("Apache License", apache.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
