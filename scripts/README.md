# Automation scripts

| Script | Purpose |
|---|---|
| `msp_audit.py` | Checks a client resource group for drift: missing tags, NSG rules open to the internet, Key Vault secrets without an expiry date |
| `new_client.py` | Creates a new client environment folder from `environments/dev` and writes an onboarding checklist |

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r scripts/requirements.txt
az login --tenant <your-tenant-id> --use-device-code
```

## Audit

```powershell
python scripts/msp_audit.py --resource-group rg-acme-dev
python scripts/msp_audit.py --resource-group rg-acme-dev --json
```

Output is `[PASS]`, `[FAIL]` or `[SKIP]` per resource. The exit code is 1 if anything fails.
Listing Key Vault secrets needs a Key Vault secrets role for your own account; without it
the secrets check is reported as `[SKIP]`, not an error.

## New client

```powershell
python scripts/new_client.py globex --dry-run
python scripts/new_client.py globex
```

The generator only creates `environments/<client>-<env>/`. Identity and pipeline changes
are listed in the generated `ONBOARDING.md` for a person to review in a pull request.

## Tests

```powershell
python -m unittest discover tests -v
```

The tests need no Azure access or extra packages.
