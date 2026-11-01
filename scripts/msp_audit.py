#!/usr/bin/env python3
"""Audit one Azure client environment against the landing-zone rules.

Checks
  tags     every resource carries the required tags
  nsg      no custom inbound Allow rule is open to the internet
  secrets  Key Vault secrets have an expiry date that is not near or past

Why it exists: the pipeline only sees changes made through Terraform. This script also
catches drift, such as a rule added by hand in the portal or a resource created
without tags.

Usage (after `az login`):
    python scripts/msp_audit.py --resource-group rg-acme-dev

The exit code is 1 when any check fails, so the script can also gate a pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

DEFAULT_REQUIRED_TAGS = ("client", "environment", "project", "managed_by", "owner")
OPEN_SOURCES = {"*", "internet", "any", "0.0.0.0/0", "::/0"}


@dataclass
class Finding:
    check: str
    resource: str
    status: str  # PASS, FAIL or SKIP
    detail: str


# --------------------------------------------------------------------------------------
# Pure checks: plain data in, findings out. These are what the unit tests exercise.
# --------------------------------------------------------------------------------------
def check_tags(resources, required):
    """resources: list of {"name", "type", "tags"}; required: iterable of tag names."""
    findings = []
    for res in resources:
        present = {str(k).lower(): v for k, v in (res.get("tags") or {}).items()}
        missing = [t for t in required if not str(present.get(t.lower(), "")).strip()]
        if missing:
            findings.append(
                Finding("tags", res["name"], "FAIL", "missing tags: " + ", ".join(missing))
            )
        else:
            findings.append(Finding("tags", res["name"], "PASS", "all required tags present"))
    return findings


def check_nsg_rules(nsgs):
    """nsgs: list of {"name", "rules": [{"name", "direction", "access", "sources", "ports"}]}."""
    findings = []
    for nsg in nsgs:
        risky = []
        for rule in nsg.get("rules", []):
            if str(rule.get("direction", "")).lower() != "inbound":
                continue
            if str(rule.get("access", "")).lower() != "allow":
                continue
            open_sources = [
                s for s in rule.get("sources", []) if str(s).strip().lower() in OPEN_SOURCES
            ]
            if open_sources:
                ports = ", ".join(rule.get("ports", [])) or "any"
                risky.append(
                    f"rule '{rule.get('name')}' allows inbound from {open_sources[0]} to port {ports}"
                )
        if risky:
            for detail in risky:
                findings.append(Finding("nsg", nsg["name"], "FAIL", detail))
        else:
            findings.append(Finding("nsg", nsg["name"], "PASS", "no inbound rule open to the internet"))
    return findings


def check_secret_expiry(secrets, now, warn_days):
    """secrets: list of {"name", "expires_on": datetime or None}."""
    findings = []
    soon = now + timedelta(days=warn_days)
    for secret in secrets:
        expires = secret.get("expires_on")
        if expires is None:
            findings.append(Finding("secrets", secret["name"], "FAIL", "no expiry date set"))
            continue
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= now:
            findings.append(
                Finding("secrets", secret["name"], "FAIL", f"expired on {expires.date()}")
            )
        elif expires <= soon:
            days = (expires - now).days
            findings.append(
                Finding("secrets", secret["name"], "FAIL", f"expires in {days} days ({expires.date()})")
            )
        else:
            findings.append(
                Finding("secrets", secret["name"], "PASS", f"expires {expires.date()}")
            )
    return findings


# --------------------------------------------------------------------------------------
# Azure access. SDK imports are inside the functions so the checks above can be tested
# without the Azure packages installed.
# --------------------------------------------------------------------------------------
def _value(x):
    """Azure enums may arrive as plain strings or enum members; return a plain string."""
    return str(getattr(x, "value", x))


def get_credential():
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError:
        sys.exit("Azure packages missing. Run: pip install -r scripts/requirements.txt")
    return DefaultAzureCredential()


def resolve_subscription(arg):
    if arg:
        return arg
    env = os.environ.get("AZURE_SUBSCRIPTION_ID")
    if env:
        return env
    az = shutil.which("az")
    if az:
        try:
            out = subprocess.run(
                [az, "account", "show", "--query", "id", "-o", "tsv"],
                capture_output=True, text=True, check=True,
            )
            if out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    sys.exit("Could not determine the subscription. Pass --subscription-id or run `az login`.")


def fetch_resources(credential, subscription_id, resource_group):
    try:  # azure-mgmt-resource 24+ moved the client into a sub-package
        from azure.mgmt.resource.resources import ResourceManagementClient
    except ImportError:
        from azure.mgmt.resource import ResourceManagementClient

    client = ResourceManagementClient(credential, subscription_id)
    group = client.resource_groups.get(resource_group)
    items = [{"name": group.name, "type": "resourceGroup", "tags": group.tags}]
    for res in client.resources.list_by_resource_group(resource_group):
        items.append({"name": res.name, "type": res.type, "tags": res.tags})
    return items


def fetch_nsgs(credential, subscription_id, resource_group):
    from azure.mgmt.network import NetworkManagementClient

    client = NetworkManagementClient(credential, subscription_id)
    nsgs = []
    for nsg in client.network_security_groups.list(resource_group):
        rules = []
        for rule in nsg.security_rules or []:
            sources = list(rule.source_address_prefixes or [])
            if rule.source_address_prefix:
                sources.append(rule.source_address_prefix)
            ports = list(rule.destination_port_ranges or [])
            if rule.destination_port_range:
                ports.append(rule.destination_port_range)
            rules.append({
                "name": rule.name,
                "direction": _value(rule.direction),
                "access": _value(rule.access),
                "sources": sources,
                "ports": ports,
            })
        nsgs.append({"name": nsg.name, "rules": rules})
    return nsgs


def fetch_secrets(credential, resources):
    """Return (secrets, skip_findings). Needs a Key Vault role such as Secrets User."""
    try:
        from azure.core.exceptions import HttpResponseError
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        sys.exit("Azure packages missing. Run: pip install -r scripts/requirements.txt")

    vaults = [r for r in resources if r["type"].lower() == "microsoft.keyvault/vaults"]
    if not vaults:
        return [], [Finding("secrets", "-", "SKIP", "no Key Vault in this resource group")]

    secrets, skips = [], []
    for vault in vaults:
        url = f"https://{vault['name']}.vault.azure.net/"
        try:
            client = SecretClient(vault_url=url, credential=credential)
            for props in client.list_properties_of_secrets():
                secrets.append({"name": f"{vault['name']}/{props.name}", "expires_on": props.expires_on})
        except HttpResponseError as err:
            skips.append(Finding(
                "secrets", vault["name"], "SKIP",
                f"cannot list secrets ({err.status_code}); your account needs a Key Vault secrets role",
            ))
    return secrets, skips


# --------------------------------------------------------------------------------------
def run_audit(args):
    credential = get_credential()
    subscription_id = resolve_subscription(args.subscription_id)

    resources = fetch_resources(credential, subscription_id, args.resource_group)
    findings = check_tags(resources, args.required_tags)
    findings += check_nsg_rules(fetch_nsgs(credential, subscription_id, args.resource_group))

    secrets, skips = fetch_secrets(credential, resources)
    findings += skips
    findings += check_secret_expiry(secrets, datetime.now(timezone.utc), args.expiry_days)
    return findings


def print_report(findings):
    order = {"FAIL": 0, "SKIP": 1, "PASS": 2}
    for f in sorted(findings, key=lambda f: (f.check, order[f.status], f.resource)):
        print(f"[{f.status}] {f.check:<8} {f.resource:<40} {f.detail}")
    counts = Counter(f.status for f in findings)
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Audit an Azure client environment for drift.")
    p.add_argument("--resource-group", default="rg-acme-dev")
    p.add_argument("--subscription-id", help="defaults to AZURE_SUBSCRIPTION_ID or `az account show`")
    p.add_argument("--required-tags", default=",".join(DEFAULT_REQUIRED_TAGS),
                   type=lambda s: [t.strip() for t in s.split(",") if t.strip()])
    p.add_argument("--expiry-days", type=int, default=30, help="warn when a secret expires within N days")
    p.add_argument("--json", action="store_true", help="print findings as JSON")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    findings = run_audit(args)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        print_report(findings)
    return 1 if any(f.status == "FAIL" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
