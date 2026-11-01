param(
    [string]$Context = "",
    [string]$Image = "cloudcrew:local"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env.cloud"
if (-not (Test-Path $envFile)) { python (Join-Path $PSScriptRoot "bootstrap.py") }
$values = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -and -not $_.StartsWith('#') -and $_.Contains('=')) {
        $name, $value = $_.Split('=', 2)
        $values[$name] = $value
    }
}
$contextArgs = @()
if ($Context) { $contextArgs = @('--context', $Context) }
kubectl @contextArgs apply -f (Join-Path $root 'deploy/kubernetes/namespace.yaml')
kubectl @contextArgs -n cloudcrew create secret generic cloudcrew-secrets `
    --from-literal="API_KEYS=$($values.API_KEYS)" `
    --from-literal="POSTGRES_PASSWORD=$($values.POSTGRES_PASSWORD)" `
    --from-literal="GEMINI_API_KEY=$($values.GEMINI_API_KEY)" `
    --dry-run=client -o yaml | kubectl @contextArgs apply -f -
$rendered = kubectl kustomize (Join-Path $root 'deploy/kubernetes') | ForEach-Object { $_ -replace 'image: cloudcrew:local', "image: $Image" }
$rendered | kubectl @contextArgs apply -f -
kubectl @contextArgs -n cloudcrew rollout status statefulset/postgres --timeout=180s
kubectl @contextArgs -n cloudcrew wait --for=condition=complete job/cloudcrew-migrate --timeout=180s
kubectl @contextArgs -n cloudcrew rollout status deployment/cloudcrew-api --timeout=180s
kubectl @contextArgs -n cloudcrew rollout status deployment/cloudcrew-worker --timeout=180s
Write-Output 'CloudCrew is ready. Run: kubectl -n cloudcrew port-forward service/cloudcrew-api 8000:80'
