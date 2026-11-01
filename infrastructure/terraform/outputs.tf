output "namespace" { value = kubernetes_namespace_v1.cloudcrew.metadata[0].name }
output "port_forward_command" { value = "kubectl -n ${var.namespace} port-forward service/cloudcrew-api 8000:80" }
