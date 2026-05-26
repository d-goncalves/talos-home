from diagrams import Diagram, Cluster, Edge
from diagrams.k8s.compute import Deployment
from diagrams.k8s.storage import PV
from diagrams.k8s.network import Ingress
from diagrams.onprem.gitops import Flux
from diagrams.onprem.vcs import Gitea, Github
from diagrams.generic.network import VPN
from diagrams.generic.storage import Storage
from diagrams.onprem.monitoring import Grafana
from diagrams.onprem.security import Vault

graph_attr = {
    "fontsize": "28",
    "bgcolor": "#0d1117",
    "fontcolor": "#e6edf3",
    "pad": "2.5",
    "splines": "ortho",
    "nodesep": "1.5",
    "ranksep": "2.0",
    "size": "28,18",
    "dpi": "150",
}

cluster_attr = {
    "fontsize": "18",
    "fontcolor": "#e6edf3",
    "bgcolor": "#161b22",
    "style": "rounded",
    "color": "#30363d",
    "margin": "20",
}

dark_cluster = {**cluster_attr, "bgcolor": "#0d1117", "color": "#58a6ff"}

node_attr = {"fontsize": "14", "fontcolor": "#e6edf3"}

with Diagram(
    "",
    filename="/Users/diogo/talos/docs/architecture",
    outformat="png",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
    node_attr=node_attr,
):
    # ── External ──────────────────────────────────────────────────────
    with Cluster("External", graph_attr=cluster_attr):
        op  = Vault("1Password")
        gh  = Github("GitHub\nmirror")

    # ── Cluster ───────────────────────────────────────────────────────
    with Cluster("Talos Linux · NODE_IP_PLACEHOLDER (Proxmox VM)", graph_attr=dark_cluster):

        with Cluster("GitOps", graph_attr=cluster_attr):
            gitea = Gitea("Gitea")
            flux  = Flux("Flux CD")

        with Cluster("Infrastructure", graph_attr=cluster_attr):
            ts_op   = VPN("Tailscale\nOperator")
            eso     = Deployment("External Secrets\nOperator")
            nfs_csi = Deployment("NFS CSI\nDriver")

        with Cluster("Media", graph_attr=cluster_attr):
            media = Deployment("Jellyfin · Sonarr\nRadarr · Prowlarr\nBazarr · qBittorrent")

        with Cluster("Personal", graph_attr=cluster_attr):
            personal = Deployment("Immich · Actual Budget\nAudiobookshelf · Wallos\nAdventureLog · Homebox")

        with Cluster("Infrastructure Apps", graph_attr=cluster_attr):
            infra_apps = Grafana("Gitea · Outline\nGrafana · ntfy\nUptime Kuma · Homepage")

        with Cluster("Storage", graph_attr=cluster_attr):
            nfs_pvc   = PV("NFS PVCs\n(survives wipes)")
            local_pvc = PV("local-path PVCs\n(node disk only)")

        tailnet = Ingress("*.<tailnet>.ts.net")

    # ── NAS ───────────────────────────────────────────────────────────
    with Cluster("Unifi NAS · NAS_IP_PLACEHOLDER", graph_attr=cluster_attr):
        nas = Storage("NFS Server")

    # ── Edges ─────────────────────────────────────────────────────────
    # GitOps
    gh    >> Edge(label="push mirror", color="#58a6ff")  >> gitea
    gitea >> Edge(label="SSH",         color="#58a6ff")  >> flux
    flux  >> Edge(color="#3fb950")                       >> [media, personal, infra_apps]

    # Secrets
    op >> Edge(label="service account", color="#d2a8ff", style="dashed") >> eso
    eso >> Edge(label="ExternalSecret\nsync", color="#d2a8ff", style="dashed") >> [media, personal, infra_apps]

    # NFS storage
    nfs_csi >> Edge(color="#e3b341")        >> nfs_pvc
    nfs_pvc >> Edge(color="#e3b341")        >> nas
    media      >> Edge(color="#e3b341")     >> nfs_pvc
    personal   >> Edge(color="#e3b341")     >> nfs_pvc
    infra_apps >> Edge(color="#e3b341")     >> nfs_pvc

    # local-path storage
    infra_apps >> Edge(color="#f85149", style="dashed") >> local_pvc

    # Networking
    ts_op                              >> Edge(color="#58a6ff")                       >> tailnet
    [media, personal, infra_apps]      >> Edge(label="Tailscale\ningress", color="#58a6ff") >> tailnet
