import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from proxy_parser import load_domains, load_proxy_file, normalize_proxy, select_domains


class NormalizeProxyTests(unittest.TestCase):
    def test_skips_empty_comments_and_headers(self):
        self.assertEqual(normalize_proxy("", "http"), "")
        self.assertEqual(normalize_proxy("# comment", "http"), "")
        self.assertEqual(normalize_proxy("Login:Pass@Host:Port(SOCKS5/HTTPs)", "http"), "")

    def test_adds_default_http_scheme_to_host_port(self):
        self.assertEqual(
            normalize_proxy("176.211.125.59:20207", "http"),
            "http://176.211.125.59:20207",
        )

    def test_uses_requested_fallback_scheme(self):
        self.assertEqual(
            normalize_proxy("176.211.125.59:20207", "socks5"),
            "socks5://176.211.125.59:20207",
        )

    def test_normalizes_host_port_login_password(self):
        self.assertEqual(
            normalize_proxy("176.211.125.59:20207:lagrange100001:lagrange100001", "http"),
            "http://lagrange100001:lagrange100001@176.211.125.59:20207",
        )

    def test_normalizes_login_password_host_port(self):
        self.assertEqual(
            normalize_proxy("egor:f6oTHEQpa5zx:46.161.4.144:8007", "http"),
            "http://egor:f6oTHEQpa5zx@46.161.4.144:8007",
        )

    def test_normalizes_login_password_at_host_port_without_scheme(self):
        self.assertEqual(
            normalize_proxy(
                "4aCCvbsbGc5StTKs:LxHoSjWA5IYEMBiX@egor.samie-krasivie-proxy.ru:33000",
                "http",
            ),
            (
                "http://4aCCvbsbGc5StTKs:LxHoSjWA5IYEMBiX"
                "@egor.samie-krasivie-proxy.ru:33000"
            ),
        )

    def test_keeps_explicit_scheme_in_login_password_at_host_port(self):
        self.assertEqual(
            normalize_proxy("http://lagrange100001:lagrange100001@176.211.125.59:20207", "socks5"),
            "http://lagrange100001:lagrange100001@176.211.125.59:20207",
        )

    def test_normalizes_scheme_host_port_login_password(self):
        self.assertEqual(
            normalize_proxy("socks5://46.161.4.144:8007:egor:f6oTHEQpa5zx", "http"),
            "socks5://egor:f6oTHEQpa5zx@46.161.4.144:8007",
        )

    def test_url_encodes_credentials(self):
        self.assertEqual(
            normalize_proxy("example.com:8080:user name:p@ss", "http"),
            "http://user%20name:p%40ss@example.com:8080",
        )


class LoadProxyFileTests(unittest.TestCase):
    def test_loads_proxy_file_without_required_header(self):
        with TemporaryDirectory() as tmp_dir:
            proxy_file = Path(tmp_dir) / "proxies.txt"
            proxy_file.write_text(
                "\n".join(
                    [
                        "Login:Pass@Host:Port(SOCKS5/HTTPs)",
                        "176.211.125.59:20207:lagrange100001:lagrange100001",
                        "4aCCvbsbGc5StTKs:LxHoSjWA5IYEMBiX@egor.samie-krasivie-proxy.ru:33000",
                    ]
                ),
                encoding="utf-8",
            )

            proxies, skipped = load_proxy_file(proxy_file, "http")

        self.assertEqual(skipped, 1)
        self.assertEqual(
            proxies,
            [
                "http://lagrange100001:lagrange100001@176.211.125.59:20207",
                (
                    "http://4aCCvbsbGc5StTKs:LxHoSjWA5IYEMBiX"
                    "@egor.samie-krasivie-proxy.ru:33000"
                ),
            ],
        )


class DomainTests(unittest.TestCase):
    def test_loads_domains_from_json_objects(self):
        with TemporaryDirectory() as tmp_dir:
            domains_file = Path(tmp_dir) / "domains.json"
            domains_file.write_text(
                json.dumps([{"domain": "example.com"}, {"domain": "example.org"}]),
                encoding="utf-8",
            )

            self.assertEqual(load_domains(domains_file), ["example.com", "example.org"])

    def test_selects_all_domains(self):
        self.assertEqual(select_domains(["a.test", "b.test"], "all"), ["a.test", "b.test"])

    def test_selects_random_domain_sample(self):
        with patch("proxy_parser.random.sample", return_value=["b.test"]) as sample:
            self.assertEqual(select_domains(["a.test", "b.test"], "1"), ["b.test"])
        sample.assert_called_once_with(["a.test", "b.test"], 1)

    def test_rejects_zero_domain_count(self):
        with self.assertRaises(ValueError):
            select_domains(["a.test"], "0")


if __name__ == "__main__":
    unittest.main()
