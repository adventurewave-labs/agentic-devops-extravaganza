/**
 * Vercel Serverless Function: K8sGPT Scan Touchpoint
 *
 * Supports three actions via ?action=scan|remediate|reset
 *
 * - scan:      Returns the 8 findings from captured k8sgpt_analyze.json
 * - remediate: Returns 0 findings (the fixed state)
 * - reset:     Acknowledges reset (client-side state management)
 */

// Real findings data parsed from outputs/k8sgpt_analyze.json
const RAW_FINDINGS = [
  {
    kind: "Node",
    name: "worker-3",
    error: [
      { Text: "worker-3 has condition of type DiskPressure, reason KubeletHasNoDiskSpace: kubelet has disk pressure" }
    ]
  },
  {
    kind: "Deployment",
    name: "payment-prod/payment-api",
    error: [
      { Text: "Deployment payment-prod/payment-api has 1 replicas but 0 are available with status running" }
    ]
  },
  {
    kind: "Deployment",
    name: "payment-prod/payment-worker",
    error: [
      { Text: "Deployment payment-prod/payment-worker has 1 replicas but 0 are available with status running" }
    ]
  },
  {
    kind: "PersistentVolumeClaim",
    name: "payment-prod/payment-data-pvc",
    error: [
      { Text: 'storageclass.storage.k8s.io "standard" not found' }
    ]
  },
  {
    kind: "Service",
    name: "payment-prod/payment-api-svc",
    error: [
      { Text: "Service has no endpoints, expected label app=payment-api-frontend" }
    ]
  },
  {
    kind: "Ingress",
    name: "payment-prod/payment-ingress",
    error: [
      { Text: "Ingress uses the ingress class nginx which does not exist." },
      { Text: "Ingress uses the service payment-prod/payment-frontend which does not exist." }
    ]
  },
  {
    kind: "Pod",
    name: "payment-prod/payment-api-7c4f5b-x9qkl",
    error: [
      { Text: "the last termination reason is Error container=api pod=payment-api-7c4f5b-x9qkl" }
    ],
    parentObject: "Deployment/payment-api"
  },
  {
    kind: "Pod",
    name: "payment-prod/payment-worker-6d8b2c-p3mnr",
    error: [
      { Text: "the last termination reason is OOMKilled container=worker pod=payment-worker-6d8b2c-p3mnr" }
    ],
    parentObject: "Deployment/payment-worker"
  }
];

// Severity classification logic based on kind and error content
function classifySeverity(kind, errorText) {
  const text = errorText.toLowerCase();
  // Critical: pods crashing, OOMKilled, Error, deployments with 0 available
  if (kind === "Pod" && (text.includes("oomkilled") || text.includes("error"))) return "critical";
  if (kind === "Deployment" && text.includes("0 are available")) return "critical";
  if (kind === "Node" && text.includes("diskpressure")) return "critical";
  // Warning: missing resources that block provisioning
  if (kind === "PersistentVolumeClaim") return "warning";
  if (kind === "Ingress") return "warning";
  if (kind === "Service") return "warning";
  // Info for everything else
  return "info";
}

// Analyzer name derived from kind
function analyzerForKind(kind) {
  const map = {
    Node: "node",
    Pod: "pod",
    Deployment: "deployment",
    PersistentVolumeClaim: "pvc",
    Service: "service",
    Ingress: "ingress"
  };
  return map[kind] || kind.toLowerCase();
}

// Short resource name (strip namespace prefix)
function shortName(fullName) {
  const parts = fullName.split("/");
  return parts.length > 1 ? parts[parts.length - 1] : fullName;
}

// Transform raw findings into the response schema
function transformFindings(rawFindings) {
  return rawFindings.map((f, idx) => {
    // Combine all error texts for this result
    const allErrors = f.error.map(e => e.Text).join("; ");
    const severity = classifySeverity(f.kind, allErrors);

    return {
      id: idx,
      severity: severity,
      resource: shortName(f.name),
      namespace: f.name.includes("/") ? f.name.split("/")[0] : "default",
      kind: f.kind,
      analyzer: analyzerForKind(f.kind),
      description: allErrors,
      parentObject: f.parentObject || ""
    };
  });
}

// Health state from captured data
const HEALTH_BEFORE = {
  pods_ready: "0/2",
  deployments_available: "0/2",
  worker3_disk_pressure: "True",
  pvc_phase: "Pending",
  ingress_class_exists: false,
  backend_service_exists: false,
  remediations_applied: 0
};

const HEALTH_AFTER = {
  pods_ready: "2/2",
  deployments_available: "2/2",
  worker3_disk_pressure: "False",
  pvc_phase: "Bound",
  ingress_class_exists: true,
  backend_service_exists: true,
  remediations_applied: 4
};

export default function handler(request, response) {
  const { action } = request.query;

  // CORS headers for local dev flexibility
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  response.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (request.method === "OPTIONS") {
    return response.status(200).end();
  }

  const startTime = Date.now();

  switch (action) {
    case "scan": {
      const findings = transformFindings(RAW_FINDINGS);
      const duration = 2000 + Math.floor(Math.random() * 800); // 2-2.8s realistic
      return response.status(200).json({
        findings,
        total_findings: findings.length,
        duration_ms: duration,
        cluster_state: "broken",
        health: HEALTH_BEFORE
      });
    }

    case "remediate": {
      const duration = 3000 + Math.floor(Math.random() * 1200); // 3-4.2s
      return response.status(200).json({
        findings: [],
        total_findings: 0,
        duration_ms: duration,
        cluster_state: "healthy",
        health: HEALTH_AFTER
      });
    }

    case "reset": {
      return response.status(200).json({
        findings: [],
        total_findings: 0,
        duration_ms: 50,
        cluster_state: "broken",
        health: HEALTH_BEFORE,
        message: "Cluster reset to broken state"
      });
    }

    default: {
      return response.status(400).json({
        error: "Invalid action. Use ?action=scan|remediate|reset"
      });
    }
  }
}
