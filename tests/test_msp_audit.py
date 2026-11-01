import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import msp_audit as audit  # noqa: E402

REQUIRED = ["client", "environment", "project", "managed_by", "owner"]
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def statuses(findings):
    return [f.status for f in findings]


class TagTests(unittest.TestCase):
    def test_all_tags_present_passes(self):
        res = [{"name": "vnet", "type": "x", "tags": {t: "v" for t in REQUIRED}}]
        self.assertEqual(statuses(audit.check_tags(res, REQUIRED)), ["PASS"])

    def test_missing_tag_fails_and_is_named(self):
        res = [{"name": "vnet", "type": "x", "tags": {"client": "acme"}}]
        findings = audit.check_tags(res, REQUIRED)
        self.assertEqual(statuses(findings), ["FAIL"])
        self.assertIn("owner", findings[0].detail)

    def test_tag_names_are_case_insensitive(self):
        res = [{"name": "kv", "type": "x", "tags": {t.upper(): "v" for t in REQUIRED}}]
        self.assertEqual(statuses(audit.check_tags(res, REQUIRED)), ["PASS"])

    def test_none_tags_and_blank_values_fail(self):
        res = [
            {"name": "a", "type": "x", "tags": None},
            {"name": "b", "type": "x", "tags": {**{t: "v" for t in REQUIRED}, "owner": "  "}},
        ]
        self.assertEqual(statuses(audit.check_tags(res, REQUIRED)), ["FAIL", "FAIL"])


def rule(name, direction="Inbound", access="Allow", sources=("10.10.2.0/24",), ports=("22",)):
    return {"name": name, "direction": direction, "access": access,
            "sources": list(sources), "ports": list(ports)}


class NsgTests(unittest.TestCase):
    def test_ssh_from_mgmt_subnet_passes(self):
        nsgs = [{"name": "nsg-app", "rules": [rule("allow-ssh-from-mgmt")]}]
        self.assertEqual(statuses(audit.check_nsg_rules(nsgs)), ["PASS"])

    def test_open_to_internet_fails(self):
        for src in ("*", "Internet", "0.0.0.0/0", "Any"):
            nsgs = [{"name": "nsg-app", "rules": [rule("bad", sources=[src])]}]
            findings = audit.check_nsg_rules(nsgs)
            self.assertEqual(statuses(findings), ["FAIL"], src)
            self.assertIn("bad", findings[0].detail)

    def test_deny_rule_open_to_internet_is_fine(self):
        nsgs = [{"name": "nsg-mgmt", "rules": [rule("deny-internet", access="Deny", sources=["Internet"])]}]
        self.assertEqual(statuses(audit.check_nsg_rules(nsgs)), ["PASS"])

    def test_outbound_allow_is_ignored(self):
        nsgs = [{"name": "nsg", "rules": [rule("out", direction="Outbound", sources=["*"])]}]
        self.assertEqual(statuses(audit.check_nsg_rules(nsgs)), ["PASS"])

    def test_each_bad_rule_is_reported(self):
        nsgs = [{"name": "nsg", "rules": [rule("a", sources=["*"]), rule("b", sources=["Internet"])]}]
        self.assertEqual(statuses(audit.check_nsg_rules(nsgs)), ["FAIL", "FAIL"])


class SecretTests(unittest.TestCase):
    def test_no_expiry_fails(self):
        out = audit.check_secret_expiry([{"name": "kv/s", "expires_on": None}], NOW, 30)
        self.assertEqual(statuses(out), ["FAIL"])
        self.assertIn("no expiry", out[0].detail)

    def test_expired_fails(self):
        out = audit.check_secret_expiry([{"name": "s", "expires_on": NOW - timedelta(days=1)}], NOW, 30)
        self.assertEqual(statuses(out), ["FAIL"])
        self.assertIn("expired", out[0].detail)

    def test_expiring_soon_fails(self):
        out = audit.check_secret_expiry([{"name": "s", "expires_on": NOW + timedelta(days=10)}], NOW, 30)
        self.assertEqual(statuses(out), ["FAIL"])
        self.assertIn("10 days", out[0].detail)

    def test_far_expiry_passes(self):
        out = audit.check_secret_expiry([{"name": "s", "expires_on": NOW + timedelta(days=400)}], NOW, 30)
        self.assertEqual(statuses(out), ["PASS"])

    def test_naive_datetime_is_treated_as_utc(self):
        naive = datetime(2027, 12, 31)
        out = audit.check_secret_expiry([{"name": "s", "expires_on": naive}], NOW, 30)
        self.assertEqual(statuses(out), ["PASS"])


class HelperTests(unittest.TestCase):
    def test_enum_values_are_flattened(self):
        class Fake:
            value = "Inbound"
        self.assertEqual(audit._value(Fake()), "Inbound")
        self.assertEqual(audit._value("Allow"), "Allow")


if __name__ == "__main__":
    unittest.main()
