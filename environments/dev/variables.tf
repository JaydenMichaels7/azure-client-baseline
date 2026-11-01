variable "client_name" {
  description = "Short client identifier (lowercase, <=8 chars). One client = one environment folder."
  type        = string
  default     = "acme"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "location" {
  description = "If B1s is unavailable here, try westeurope or another region and note why."
  type        = string
  default     = "uksouth"
}

variable "vm_size" {
  type    = string
  default = "Standard_B1s"
}

variable "enable_compute" {
  description = "Turn the VM off to stop any compute cost while keeping network + security."
  type        = bool
  default     = false
}

variable "vm_zone" {
  description = "Availability zone for the VM (1, 2 or 3). null lets Azure choose."
  type        = string
  default     = "3"
}

variable "admin_ssh_public_key" {
  description = "Contents of your .pub file. Provided via TF_VAR_admin_ssh_public_key."
  type        = string
}
