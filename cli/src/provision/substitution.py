"""Which `${...}` in the manifests belong to Flux, and which do not.

Flux's postBuild substitution rewrites every `${VAR}` it finds in the built
manifests, including ones that were meant for a shell inside a container. The
two cases look identical in YAML and fail in opposite directions:

1. A variable Flux should substitute but that is missing from `cluster-vars`.
   Under Flux v2.9 this is a build error and the whole Kustomization stops
   applying — not just the offending file.

2. A variable a container's shell should expand at runtime, left unescaped.
   Flux claims it first and substitutes it away, so the container sees an empty
   value. Worse when the value is a secret: substituting it for real would bake
   it into the manifest Flux applies.

Case 2 is the one that bit: three servarr init containers wrote `<ApiKey>` from
a mounted secret via `${SONARR_API_KEY}` and friends. Flux v2.8 tolerated them,
v2.9 turned them into a hard error, and `kubectl kustomize` — which is what CI
built with — does no substitution at all, so nothing caught it.

Escaping is `$${VAR}`: Flux collapses `$$` to `$` and leaves the rest alone.

Only some Kustomizations substitute, so only their paths are in scope — the
generated Flux bundle is not, and neither is the external-secrets store. That
set is read from the Kustomizations themselves rather than listed here, so
adding substitution to a new one brings it under the audit automatically.
"""

from __future__ import annotations

import re

import yaml

# Seeded into the cluster-vars Secret by `bootstrap secrets`, which asserts its
# keys still match this set.
CLUSTER_VARS = {
    "TAILNET_DOMAIN",
    "NODE_IP",
    "LAN_SUBNET",
    "NAS_IP",
    "NAS_DATA_PATH",
}

# A `${VAR}` not preceded by another `$`. The lookbehind is what separates a
# live reference from an escaped `$${VAR}`.
_UNESCAPED = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def substituted_paths(repo_root) -> list:
    """Repo paths built by a Kustomization that does postBuild substitution."""
    defs = repo_root / "talos" / "kubernetes" / "flux" / "kustomizations"
    paths = []
    for f in sorted(defs.glob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if not isinstance(doc, dict) or doc.get("kind") != "Kustomization":
                continue
            spec = doc.get("spec") or {}
            if not (spec.get("postBuild") or {}).get("substituteFrom"):
                continue
            # spec.path is repo-relative and written as ./kubernetes/...
            rel = (spec.get("path") or "").lstrip("./")
            if rel:
                paths.append(repo_root / rel)
    return paths


def unknown_vars(repo_root) -> list[str]:
    """Every unescaped `${VAR}`, in a substituted path, that has no value.

    Each is one of the two failure modes above. Which one it is depends on
    intent, so the message names both remedies rather than guessing.
    """
    problems = []
    for root in substituted_paths(repo_root):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in (".yaml", ".yml"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for n, line in enumerate(text.splitlines(), 1):
                for var in sorted(set(_UNESCAPED.findall(line))):
                    if var in CLUSTER_VARS:
                        continue
                    problems.append(
                        f"{path.relative_to(repo_root)}:{n}: ${{{var}}} is not "
                        f"in cluster-vars — add it there, or escape it as "
                        f"$${{{var}}} if a container expands it at runtime"
                    )
    return problems
