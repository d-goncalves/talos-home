"""Tests for the Flux postBuild substitution audit."""

from __future__ import annotations

import textwrap

from provision import substitution

# A Kustomization that substitutes, and one that does not. The audit reads
# these to decide what is in scope, so the fixtures are the real mechanism.
SUBSTITUTING = """
    apiVersion: kustomize.toolkit.fluxcd.io/v1
    kind: Kustomization
    metadata: {name: apps}
    spec:
      path: ./kubernetes/apps
      postBuild:
        substituteFrom:
          - kind: Secret
            name: cluster-vars
"""

PLAIN = """
    apiVersion: kustomize.toolkit.fluxcd.io/v1
    kind: Kustomization
    metadata: {name: flux-system}
    spec:
      path: ./kubernetes/flux
"""


def repo(tmp_path, *, kustomizations=(SUBSTITUTING,), **files):
    root = tmp_path
    defs = root / "kubernetes" / "flux" / "kustomizations"
    defs.mkdir(parents=True)
    for i, k in enumerate(kustomizations):
        (defs / f"k{i}.yaml").write_text(textwrap.dedent(k).strip())
    for name, body in files.items():
        p = root / "kubernetes" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip())
    return root


def test_cluster_var_is_accepted(tmp_path):
    root = repo(tmp_path, **{"apps/a.yaml": "host: immich.${TAILNET_DOMAIN}\n"})
    assert substitution.unknown_vars(root) == []


def test_unknown_var_is_reported(tmp_path):
    root = repo(tmp_path, **{"apps/a.yaml": "key: ${PROWLARR_API_KEY}\n"})
    (problem,) = substitution.unknown_vars(root)
    assert "PROWLARR_API_KEY" in problem
    assert "apps/a.yaml:1" in problem


def test_escaped_var_is_left_alone(tmp_path):
    """`$${VAR}` is the shell's, not Flux's — the whole point of the audit."""
    root = repo(tmp_path, **{"apps/a.yaml": "key: $${PROWLARR_API_KEY}\n"})
    assert substitution.unknown_vars(root) == []


def test_reports_every_occurrence_with_its_line(tmp_path):
    root = repo(
        tmp_path,
        **{
            "apps/a.yaml": """
                one: ${FIRST}
                fine: ${NAS_IP}
                two: ${SECOND}
            """
        },
    )
    problems = substitution.unknown_vars(root)
    assert len(problems) == 2
    assert ":1:" in problems[0] and ":3:" in problems[1]


def test_path_of_a_non_substituting_kustomization_is_out_of_scope(tmp_path):
    """The generated Flux bundle is full of ${} that Flux never expands."""
    root = repo(
        tmp_path,
        kustomizations=(PLAIN,),
        **{"flux/flux-system/gotk-components.yaml": "x: ${SOMETHING}\n"},
    )
    assert substitution.substituted_paths(root) == []
    assert substitution.unknown_vars(root) == []


def test_scope_follows_the_kustomizations(tmp_path):
    """Adding substitution to a Kustomization brings its path under the audit."""
    root = repo(
        tmp_path,
        kustomizations=(SUBSTITUTING, PLAIN),
        **{
            "apps/a.yaml": "key: ${IN_SCOPE}\n",
            "flux/b.yaml": "key: ${OUT_OF_SCOPE}\n",
        },
    )
    (problem,) = substitution.unknown_vars(root)
    assert "IN_SCOPE" in problem


def test_non_yaml_is_ignored(tmp_path):
    root = repo(tmp_path, **{"apps/README.md": "run with ${SOME_VAR}\n"})
    assert substitution.unknown_vars(root) == []


def test_shell_style_var_without_braces_is_not_flagged(tmp_path):
    """Flux only substitutes ${VAR}; a bare $VAR is the shell's and always was."""
    root = repo(tmp_path, **{"apps/a.yaml": "cmd: echo $HOME\n"})
    assert substitution.unknown_vars(root) == []


def test_the_real_manifests_pass():
    """Guards the repo itself, not just the parser."""
    from provision import settings

    assert substitution.unknown_vars(settings.repo_root()) == []


def test_the_real_repo_substitutes_somewhere():
    """Stops the audit silently going no-op if the scope logic breaks."""
    from provision import settings

    assert substitution.substituted_paths(settings.repo_root())
