"""
Architecture diagram for talos-home.
Requires: pip install diagrams pillow graphviz
Icons:    downloaded to /tmp/diagram_icons from walkxcode/dashboard-icons
Run:      python3 docs/draw_arch.py
Output:   docs/architecture.png
"""

import math
from pathlib import Path
from PIL import Image

from diagrams import Diagram, Cluster, Edge
from diagrams.custom import Custom
from diagrams.k8s.storage import PV
from diagrams.k8s.network import Ingress
from diagrams.onprem.vcs import Github
from diagrams.generic.storage import Storage

ICONS = Path("/tmp/diagram_icons")

# ── Helper: build a grid tile from multiple app icons ─────────────────────────

def make_tile(names: list[str], cols: int = 3, cell: int = 128, pad: int = 10) -> str:
    """
    Composite multiple icons into a grid PNG.
    Returns the path to the generated tile (saved in /tmp).
    """
    rows = math.ceil(len(names) / cols)
    w = cols * cell + (cols + 1) * pad
    h = rows * cell + (rows + 1) * pad
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    for i, name in enumerate(names):
        src = ICONS / f"{name}.png"
        img = Image.open(src).convert("RGBA").resize((cell, cell), Image.LANCZOS)
        col = i % cols
        row = i // cols
        x = pad + col * (cell + pad)
        y = pad + row * (cell + pad)
        tile.paste(img, (x, y), img)

    slug = "_".join(names[:3])
    out = Path(f"/tmp/tile_{slug}.png")
    tile.save(out)
    return str(out)


def icon(name: str, label: str = "") -> Custom:
    return Custom(label, str(ICONS / f"{name}.png"))


def tile(names: list[str], label: str = "", cols: int = 3) -> Custom:
    return Custom(label, make_tile(names, cols=cols))


# ── Graph attributes ──────────────────────────────────────────────────────────

graph_attr = {
    "fontsize": "22",
    "bgcolor": "white",
    "fontcolor": "#24292f",
    "pad": "2.0",
    "splines": "ortho",
    "nodesep": "1.4",
    "ranksep": "2.0",
    "size": "32,20",
    "dpi": "180",
}

cluster_attr = {
    "fontsize": "15",
    "fontcolor": "#24292f",
    "bgcolor": "#f6f8fa",
    "style": "rounded",
    "color": "#d0d7de",
    "margin": "20",
}

outer_cluster = {**cluster_attr, "bgcolor": "#eaf5ff", "color": "#0969da", "fontsize": "18"}

node_attr = {"fontsize": "13", "fontcolor": "#24292f"}

# ── Diagram ───────────────────────────────────────────────────────────────────

with Diagram(
    "",
    filename="/Users/diogo/talos/docs/architecture",
    outformat="png",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    # ── External ──────────────────────────────────────────────────────────────
    with Cluster("External", graph_attr=cluster_attr):
        op = icon("onepassword", "1Password")
        gh = icon("github",     "GitHub\nmirror")

    # ── Talos cluster ─────────────────────────────────────────────────────────
    with Cluster("Talos Linux · NODE_IP_PLACEHOLDER (Proxmox VM)", graph_attr=outer_cluster):

        with Cluster("GitOps", graph_attr=cluster_attr):
            gitea = icon("gitea",  "Gitea")
            flux  = icon("flux",   "Flux CD")

        with Cluster("Infrastructure", graph_attr=cluster_attr):
            ts_op   = icon("tailscale", "Tailscale\nOperator")
            eso     = icon("eso", "Ext. Secrets\nOperator")
            nfs_csi = Storage("NFS CSI")

        # App clusters — each group becomes a single icon-grid tile
        with Cluster("Media", graph_attr=cluster_attr):
            media = tile(
                ["jellyfin", "sonarr", "radarr", "prowlarr", "bazarr", "qbittorrent"],
                cols=3,
            )

        with Cluster("Personal", graph_attr=cluster_attr):
            personal = tile(
                ["immich", "actual-budget", "audiobookshelf", "wallos", "adventurelog", "homebox"],
                cols=3,
            )

        with Cluster("Infrastructure Apps", graph_attr=cluster_attr):
            infra_apps = tile(
                ["gitea", "outline", "grafana", "ntfy", "uptime-kuma", "homepage", "it-tools"],
                cols=4,
            )

        with Cluster("Storage", graph_attr=cluster_attr):
            nfs_pvc   = PV("NFS PVCs\n(survives wipes)")
            local_pvc = PV("local-path\n(node only)")

        tailnet = Ingress("*.<tailnet>.ts.net")

    # ── NAS ───────────────────────────────────────────────────────────────────
    with Cluster("Unifi NAS · NAS_IP_PLACEHOLDER", graph_attr=cluster_attr):
        nas = Storage("NFS Server")

    # ── Edges ─────────────────────────────────────────────────────────────────
    # GitOps
    gh    >> Edge(label="push mirror", color="#0969da") >> gitea
    gitea >> Edge(label="SSH",         color="#0969da") >> flux
    flux  >> Edge(color="#1a7f37") >> [media, personal, infra_apps]

    # Secrets
    op >> Edge(label="service account", color="#8250df", style="dashed") >> eso
    eso >> Edge(label="ExternalSecret sync", color="#8250df", style="dashed") >> [media, personal, infra_apps]

    # NFS storage
    nfs_csi     >> Edge(color="#bf8700")           >> nfs_pvc
    nfs_pvc     >> Edge(color="#bf8700")           >> nas
    media       >> Edge(color="#bf8700")           >> nfs_pvc
    personal    >> Edge(color="#bf8700")           >> nfs_pvc
    infra_apps  >> Edge(color="#bf8700")           >> nfs_pvc

    # local-path
    infra_apps  >> Edge(color="#cf222e", style="dashed") >> local_pvc

    # Networking
    ts_op                              >> Edge(color="#0969da")                       >> tailnet
    [media, personal, infra_apps]      >> Edge(label="Tailscale ingress", color="#0969da") >> tailnet
