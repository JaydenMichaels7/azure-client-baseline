terraform {
  required_version = ">= 1.6.0"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" }
    azuread = { source = "hashicorp/azuread", version = "~> 3.0" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "azurerm" {
  features {}
  subscription_id     = var.subscription_id
  storage_use_azuread = true
}

provider "azuread" {}

variable "subscription_id" { type = string }
variable "github_org" { type = string }
variable "github_owner_id" { type = string }
variable "github_repo_id" { type = string }

variable "github_repo" {
  type    = string
  default = "azure-client-baseline"
}

variable "location" {
  type    = string
  default = "uksouth"
}

data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

locals {
  tags = { project = "secure-environment-baseline", purpose = "terraform-state", managed = "terraform" }
}

# ---- Remote state ----
resource "azurerm_resource_group" "state" {
  name     = "rg-msp-tfstate"
  location = var.location
  tags     = local.tags
}

resource "azurerm_storage_account" "state" {
  #checkov:skip=CKV_AZURE_59:State account must stay reachable from GitHub runners; access is Entra ID only
  #checkov:skip=CKV2_AZURE_33:Private endpoint needs paid networking and self-hosted runners
  #checkov:skip=CKV_AZURE_206:LRS chosen for cost; production would use GRS or ZRS
  #checkov:skip=CKV_AZURE_33:Queue service is not used by this account
  #checkov:skip=CKV2_AZURE_1:Customer-managed keys are out of scope for the free-tier lab
  name                            = "stmsptf${random_string.suffix.result}"
  resource_group_name             = azurerm_resource_group.state.name
  location                        = azurerm_resource_group.state.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false # Entra ID auth only
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true
    delete_retention_policy { days = 14 }
    container_delete_retention_policy { days = 14 }
  }
}

resource "azurerm_storage_container" "tfstate" {
  #checkov:skip=CKV2_AZURE_21:Blob read logging needs Log Analytics and is out of scope for the lab
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "me_state" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ---- GitHub Actions identity (OIDC, no stored secrets) ----
resource "azuread_application" "github" {
  display_name = "sp-msp-github-actions"
}

resource "azuread_service_principal" "github" {
  client_id = azuread_application.github.client_id
}

resource "azuread_application_federated_identity_credential" "pull_request" {
  #checkov:skip=CKV_AZURE_249:Subject is pinned to one repo; dev environment restricted to the main branch
  application_id = azuread_application.github.id
  display_name   = "github-pull-request"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = "repo:${var.github_org}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}:pull_request"
}

resource "azuread_application_federated_identity_credential" "env_dev" {
  #checkov:skip=CKV_AZURE_249:Subject is pinned to one repo; dev environment restricted to the main branch
  application_id = azuread_application.github.id
  display_name   = "github-env-dev"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = "repo:${var.github_org}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}:environment:dev"
}

resource "azurerm_role_assignment" "github_contributor" {
  scope                = "/subscriptions/${var.subscription_id}"
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github.object_id
}

# Needed because the security module creates role assignments (Contributor cannot).
resource "azurerm_role_assignment" "github_rbac_admin" {
  scope                = "/subscriptions/${var.subscription_id}"
  role_definition_name = "Role Based Access Control Administrator"
  principal_id         = azuread_service_principal.github.object_id
}

resource "azurerm_role_assignment" "github_state" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azuread_service_principal.github.object_id
}

output "AZURE_CLIENT_ID" { value = azuread_application.github.client_id }
output "AZURE_TENANT_ID" { value = data.azurerm_client_config.current.tenant_id }
output "AZURE_SUBSCRIPTION_ID" { value = var.subscription_id }
output "TFSTATE_RG" { value = azurerm_resource_group.state.name }
output "TFSTATE_SA" { value = azurerm_storage_account.state.name }
