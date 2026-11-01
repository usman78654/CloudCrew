terraform {
  required_version = ">= 1.6.0"
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.36" }
    kubectl    = { source = "gavinbunney/kubectl", version = "~> 1.19" }
  }
}
