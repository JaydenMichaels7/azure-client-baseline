import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import new_client as nc  # noqa: E402

VARIABLES = '''variable "client_name" {
  description = "Short client identifier"
  type        = string
  default     = "acme"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "location" {
  description = "Region"
  type        = string
  default     = "uksouth"
}

variable "admin_ssh_public_key" {
  description = "No default here"
  type        = string
}
'''
BACKEND = '''terraform {
  backend "azurerm" {
    container_name   = "tfstate"
    key              = "dev.terraform.tfstate"
    use_azuread_auth = true
  }
}
'''


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.template = self.root / "dev"
        self.template.mkdir()
        (self.template / "variables.tf").write_text(VARIABLES)
        (self.template / "backend.tf").write_text(BACKEND)
        (self.template / "main.tf").write_text("# main\n")
        (self.template / ".terraform").mkdir()
        (self.template / ".terraform" / "junk").write_text("x")
        (self.template / "terraform.tfstate").write_text("{}")
        (self.template / "terraform.tfvars").write_text('secret = "x"')

    def tearDown(self):
        self.tmp.cleanup()

    def gen(self, client="globex", env="dev", **kw):
        return nc.generate(client, env, template=self.template, dest_root=self.root, **kw)

    def test_creates_folder_with_rewritten_values(self):
        target = self.gen(location="westeurope")
        self.assertEqual(target.name, "globex-dev")
        variables = (target / "variables.tf").read_text()
        self.assertIn('default     = "globex"', variables)
        self.assertIn('default = "dev"', variables)
        self.assertIn('default     = "westeurope"', variables)
        self.assertNotIn('"acme"', variables)
        self.assertIn('key              = "globex-dev.terraform.tfstate"', (target / "backend.tf").read_text())

    def test_variable_without_default_is_untouched(self):
        target = self.gen(env="prod")
        self.assertIn("No default here", (target / "variables.tf").read_text())
        self.assertIn('default = "prod"', (target / "variables.tf").read_text())

    def test_state_and_local_files_are_not_copied(self):
        target = self.gen()
        for name in (".terraform", "terraform.tfstate", "terraform.tfvars"):
            self.assertFalse((target / name).exists(), name)

    def test_onboarding_file_has_the_right_names(self):
        text = (self.gen() / "ONBOARDING.md").read_text()
        self.assertIn("rg-globex-dev", text)
        self.assertIn("env_globex_dev", text)
        self.assertIn("environment:globex-dev", text)
        self.assertIn("${var.github_org}", text)

    def test_existing_target_is_never_overwritten(self):
        self.gen()
        with self.assertRaises(FileExistsError):
            self.gen()

    def test_dry_run_writes_nothing(self):
        target = self.gen(dry_run=True)
        self.assertFalse(target.exists())

    def test_invalid_names_are_rejected(self):
        for bad in ("A", "Globex", "1abc", "has space", "toolongclient", "x"):
            with self.assertRaises(ValueError, msg=bad):
                nc.validate(bad, "dev")
        with self.assertRaises(ValueError):
            nc.validate("globex", "Dev")

    def test_missing_template_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            nc.generate("globex", "dev", template=self.root / "nope", dest_root=self.root)


if __name__ == "__main__":
    unittest.main()
