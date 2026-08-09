"""Tests for the generated apps kustomization.

These guard the two properties that make app toggling safe:
  * disabled apps leave `resources`, so Flux prunes their workloads
  * data-bearing kinds carry the annotations that keep the data
"""

from __future__ import annotations

import pytest
import yaml

from provision import kustomize, settings

PRUNE = "kustomize.toolkit.fluxcd.io/prune"
SSA = "kustomize.toolkit.fluxcd.io/ssa"


def app(name: str, enabled: bool) -> settings.App:
    return settings.App(name=name, enabled=enabled)


ALL_ON = [app("gitea", True), app("immich", True), app("adventurelog", True)]
ONE_OFF = [app("gitea", True), app("immich", True), app("adventurelog", False)]


def parse(text: str) -> dict:
    return yaml.safe_load(text)


# ── resources ────────────────────────────────────────────────────────────────

def test_enabled_apps_are_listed_in_file_order():
    doc = parse(kustomize.render(ALL_ON))
    assert doc["resources"] == ["gitea/", "immich/", "adventurelog/"]


def test_disabled_app_is_removed_from_resources():
    doc = parse(kustomize.render(ONE_OFF))
    assert doc["resources"] == ["gitea/", "immich/"]
    assert "adventurelog/" not in doc["resources"]


def test_disabled_apps_are_recorded_as_a_comment():
    text = kustomize.render(ONE_OFF)
    assert "#   adventurelog" in text


def test_all_disabled_yields_no_resources():
    doc = parse(kustomize.render([app("gitea", False)]))
    assert not doc.get("resources")


def test_output_is_valid_yaml_and_a_kustomization():
    doc = parse(kustomize.render(ALL_ON))
    assert doc["kind"] == "Kustomization"
    assert doc["apiVersion"].startswith("kustomize.config.k8s.io/")


def test_render_is_deterministic():
    assert kustomize.render(ALL_ON) == kustomize.render(ALL_ON)


# ── protection annotations ───────────────────────────────────────────────────

def patches_by_kind(text: str) -> dict[str, dict]:
    """Annotations from the blanket, kind-wide patches only.

    Patches that also target a specific `name` are the narrow legacy-PVC ones
    and would otherwise clobber the kind-wide entry in this dict.
    """
    doc = parse(text)
    out = {}
    for p in doc["patches"]:
        if "name" in p["target"]:
            continue
        body = yaml.safe_load(p["patch"])
        out[p["target"]["kind"]] = body["metadata"]["annotations"]
    return out


@pytest.mark.parametrize(
    "kind", ["Namespace", "PersistentVolumeClaim", "PersistentVolume"]
)
def test_data_bearing_kinds_are_prune_exempt(kind):
    """Without this, pruning an app deletes its Namespace, which cascades to
    PVCs regardless of the Retain reclaim policy."""
    assert patches_by_kind(kustomize.render(ALL_ON))[kind][PRUNE] == "disabled"


@pytest.mark.parametrize(
    "kind", ["Namespace", "PersistentVolumeClaim", "PersistentVolume"]
)
def test_blanket_patches_never_set_ssa(kind):
    """ssa=IfNotPresent blocks resources.requests, the one mutable field on a
    bound claim, so it must never be applied to PVCs wholesale — only to the
    named legacy ones below."""
    assert SSA not in patches_by_kind(kustomize.render(ALL_ON))[kind]


def test_legacy_pvcs_are_create_only_by_name():
    """These three carry stale kustomize-controller ownership of volumeName.
    Clearing managedFields does not hold — Flux re-acquires it — so the ssa
    annotation is the only durable fix. Scoped by name to protect the rest."""
    doc = parse(kustomize.render(ALL_ON))
    targeted = {
        (p["target"]["namespace"], p["target"]["name"])
        for p in doc["patches"]
        if "name" in p["target"] and SSA in p["patch"]
    }
    assert targeted == set(kustomize.CREATE_ONLY_PVCS)


def test_legacy_pvc_patches_survive_disabling_apps():
    assert kustomize.render(ONE_OFF).count(SSA) == len(kustomize.CREATE_ONLY_PVCS)


def test_protection_survives_when_apps_are_disabled():
    assert patches_by_kind(kustomize.render(ONE_OFF)) == patches_by_kind(
        kustomize.render(ALL_ON)
    )


def test_no_other_kind_is_patched():
    kinds = set(patches_by_kind(kustomize.render(ALL_ON)))
    assert kinds == {"Namespace", "PersistentVolumeClaim", "PersistentVolume"}


# ── write() ──────────────────────────────────────────────────────────────────

def test_write_returns_a_bool_not_a_tuple(tmp_path, monkeypatch):
    """Regression: write() returned a tuple, callers unpacked it, and the
    signature change broke `apps enable/disable` at runtime."""
    monkeypatch.setattr(settings, "repo_root", lambda: tmp_path)
    (tmp_path / "kubernetes" / "apps").mkdir(parents=True)

    first = kustomize.write(ALL_ON)
    assert isinstance(first, bool) and first is True
    assert kustomize.write(ALL_ON) is False
    assert kustomize.write(ONE_OFF) is True
