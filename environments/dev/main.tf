terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 4.0" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
    time    = { source = "hashicorp/time", version = "~> 0.12" }
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = true # lab only: lets destroy/recreate reuse names
    }
  }
  # subscription + OIDC settings come from ARM_* environment variables
}

locals {
  name_prefix = "${var.client_name}-${var.environment}"

  tags = {
    client      = var.client_name
    environment = var.environment
    project     = "client-environment-baseline"
    managed_by  = "terraform"
    owner       = "jayden"
  }

  subnets = {
    # Client workload tier. SSH only from the management subnet; everything else inbound is denied.
    app = {
      address_prefix = "10.10.1.0/24"
      nsg_rules = [
        {
          name                   = "allow-ssh-from-mgmt"
          priority               = 100
          direction              = "Inbound"
          access                 = "Allow"
          protocol               = "Tcp"
          destination_port_range = "22"
          source_address_prefix  = "10.10.2.0/24"
        },
        {
          name                   = "deny-all-inbound"
          priority               = 4000
          direction              = "Inbound"
          access                 = "Deny"
          protocol               = "*"
          destination_port_range = "*"
          source_address_prefix  = "*"
        },
      ]
    }

    # MSP management tier. Never reachable from the internet.
    mgmt = {
      address_prefix = "10.10.2.0/24"
      nsg_rules = [
        {
          name                   = "deny-internet-inbound"
          priority               = 100
          direction              = "Inbound"
          access                 = "Deny"
          protocol               = "*"
          destination_port_range = "*"
          source_address_prefix  = "Internet"
        },
      ]
    }
  }
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name_prefix}"
  location = var.location
  tags     = local.tags
}

module "networking" {
  source              = "../../modules/networking"
  name_prefix         = local.name_prefix
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  subnets             = local.subnets
  tags                = local.tags
}

module "security" {
  source              = "../../modules/security"
  name_prefix         = local.name_prefix
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

module "compute" {
  source               = "../../modules/compute"
  name_prefix          = local.name_prefix
  location             = var.location
  resource_group_name  = azurerm_resource_group.main.name
  subnet_id            = module.networking.subnet_ids["app"]
  identity_id          = module.security.identity_id
  admin_ssh_public_key = var.admin_ssh_public_key
  vm_size              = var.vm_size
  zone                 = var.vm_zone
  enabled              = var.enable_compute
  tags                 = local.tags
}

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "key_vault_uri" {
  value = module.security.key_vault_uri
}

output "vm_private_ip" {
  value = module.compute.private_ip
}
