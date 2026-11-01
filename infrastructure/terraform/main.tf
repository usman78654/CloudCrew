provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig_path)
  config_context = var.kube_context == "" ? null : var.kube_context
}
provider "kubectl" {
  config_path      = pathexpand(var.kubeconfig_path)
  config_context   = var.kube_context == "" ? null : var.kube_context
  load_config_file = true
}

resource "kubernetes_namespace_v1" "cloudcrew" {
  metadata { name = var.namespace }
}

resource "kubernetes_secret_v1" "cloudcrew" {
  metadata {
    name      = "cloudcrew-secrets"
    namespace = kubernetes_namespace_v1.cloudcrew.metadata[0].name
  }
  data = {
    API_KEYS          = var.api_keys_json
    POSTGRES_PASSWORD = var.postgres_password
    GEMINI_API_KEY    = var.gemini_api_key
  }
  type = "Opaque"
}

data "kubectl_path_documents" "config" { pattern = "${path.module}/../../deploy/kubernetes/config.yaml" }
data "kubectl_path_documents" "postgres" { pattern = "${path.module}/../../deploy/kubernetes/postgres.yaml" }
data "kubectl_path_documents" "migrate" { pattern = "${path.module}/../../deploy/kubernetes/migrate.yaml" }
data "kubectl_path_documents" "api" { pattern = "${path.module}/../../deploy/kubernetes/api.yaml" }
data "kubectl_path_documents" "worker" { pattern = "${path.module}/../../deploy/kubernetes/worker.yaml" }
data "kubectl_path_documents" "policies" { pattern = "${path.module}/../../deploy/kubernetes/policies.yaml" }

locals {
  manifests = merge(
    data.kubectl_path_documents.config.manifests,
    data.kubectl_path_documents.postgres.manifests,
    data.kubectl_path_documents.migrate.manifests,
    data.kubectl_path_documents.api.manifests,
    data.kubectl_path_documents.worker.manifests,
    data.kubectl_path_documents.policies.manifests,
  )
}

resource "kubectl_manifest" "cloudcrew" {
  for_each           = local.manifests
  yaml_body          = replace(each.value, "image: cloudcrew:local", "image: ${var.image}")
  override_namespace = var.namespace
  depends_on         = [kubernetes_secret_v1.cloudcrew]
}
