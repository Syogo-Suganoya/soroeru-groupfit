"""設計書 §5 の技術スタック図を生成する。

    docker compose --profile diagram run --rm diagram

出力: docs/architecture.png
エージェントの内訳や同意・削除の導線は図に載せず、設計書の本文に任せる。
ここは「どの技術で組んでいるか」だけが読み取れればよい。
"""

from __future__ import annotations

from diagrams import Cluster, Diagram
from diagrams.gcp.compute import Run
from diagrams.gcp.database import Firestore
from diagrams.gcp.ml import AIPlatform
from diagrams.gcp.operations import Logging
from diagrams.gcp.storage import GCS
from diagrams.onprem.client import Users
from diagrams.programming.flowchart import Display

# graphviz に日本語フォントを明示する（fonts-ipafont-gothic を同梱）
FONT = "IPAGothic"
GRAPH_ATTR = {
    "fontname": FONT,
    "fontsize": "13",
    "bgcolor": "#f7f4f0",
    "pad": "0.4",
    "nodesep": "0.6",
    "ranksep": "0.6",
    "splines": "spline",
}
NODE_ATTR = {"fontname": FONT, "fontsize": "11"}
EDGE_ATTR = {"fontname": FONT, "fontsize": "10", "color": "#6b4f8a"}
CLUSTER_ATTR = {"fontname": FONT, "fontsize": "12", "bgcolor": "#ffffff", "pencolor": "#d9d2c8"}

with Diagram(
    "ソロエル 技術スタック",
    filename="docs/architecture",
    outformat="png",
    show=False,
    direction="TB",
    graph_attr=GRAPH_ATTR,
    node_attr=NODE_ATTR,
    edge_attr=EDGE_ATTR,
):
    users = Users("ユーザー\nPWA")

    with Cluster("Cloud Run", graph_attr=CLUSTER_ATTR):
        api = Run("api\nFastAPI")
        agent = Run("agent\nADK エージェント")

    # LINE は MessagingPort の実装として口だけあるが、MVP では使わないので載せない
    with Cluster("AI・外部API", graph_attr=CLUSTER_ATTR):
        gemini = AIPlatform("Gemini API\n判断・調和提案")
        youcam = Display("YouCam API\n個人試着")

    with Cluster("データ", graph_attr=CLUSTER_ATTR):
        firestore = Firestore("Firestore")
        gcs = GCS("Cloud Storage")
        logging = Logging("Cloud Logging")

    users >> api >> agent
    agent >> gemini
    agent >> youcam
    agent >> firestore
    agent >> gcs
    agent >> logging
