variable "kubeconfig_path" {
  type    = string
  default = "~/.kube/config"
}

variable "kube_context" {
  type    = string
  default = ""
}

variable "namespace" {
  type    = string
  default = "cloudcrew"
}

variable "api_keys_json" {
  type      = string
  sensitive = true
}

variable "postgres_password" {
  type      = string
  sensitive = true
}

variable "gemini_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "image" {
  type    = string
  default = "cloudcrew:local"
}
