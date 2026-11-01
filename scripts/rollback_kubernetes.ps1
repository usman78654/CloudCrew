param([string]$Deployment = "cloudcrew-api", [string]$Context = "")
$ErrorActionPreference = "Stop"
if ($Deployment -notin @('cloudcrew-api', 'cloudcrew-worker')) { throw 'Deployment must be cloudcrew-api or cloudcrew-worker' }
$contextArgs = @()
if ($Context) { $contextArgs = @('--context', $Context) }
kubectl @contextArgs -n cloudcrew rollout undo "deployment/$Deployment"
kubectl @contextArgs -n cloudcrew rollout status "deployment/$Deployment" --timeout=180s
