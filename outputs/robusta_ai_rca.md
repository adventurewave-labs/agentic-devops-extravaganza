## 🔥 Root Cause
The `payment-api` pod is in a `ContainersNotReady` state, likely due to a crash loop or failed startup, causing the high 5xx error rate.

## Evidence
- Pod status is `False` for `Ready` and `ContainersReady` since 03:34:02Z.
- Container status message: "Containers with unready status: [api]".
- Alert started 28 minutes ago, coinciding with the deployment of `payment-api rev 3`.

## Impact
Critical: Payment API is unavailable, with 12.4% of requests failing. All payment processing is likely impacted.

## Recommended Action
Check the pod's logs for the `api` container: `kubectl logs payment-api-7c4f5b-x9qkl -c api --previous`. If the container is crashing, roll back the deployment: `kubectl rollout undo deployment/payment-api`.