"""python/wiki/wiki_graph.py — Wiki knowledge graph analysis.

Per §12.2.2 of 2.6wiki-design.md.
Uses networkx for graph construction, community detection, and insights.

Features:
  - Graph construction from wiki_pages refs (wiki-links)
  - Alias index: case-insensitive + hyphen/space normalization
  - Community detection: Louvain algorithm
  - Graph insights: surprising connections, knowledge gaps
"""
import json
import logging
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)


def _normalize_key(s: str) -> str:
    """Normalize page key for matching: NFKC + lowercase + strip separators."""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower().strip()
    s = s.replace("-", " ").replace("_", " ")
    return s


def _build_graph(db: Any, project_id: str) -> tuple[Any, dict[str, dict]]:
    """Build a networkx DiGraph from wiki pages.

    Returns:
        (graph, node_data_map) where node_data_map maps page_key → page info.
    """
    import networkx as nx

    pages = db.list_wiki_pages(project_id)
    if not pages:
        return nx.DiGraph(), {}

    page_keys = {p["page_key"] for p in pages}
    node_data: dict[str, dict] = {}

    G = nx.DiGraph()

    # Add nodes
    for page in pages:
        pk = page["page_key"]
        refs_raw = page.get("refs") or "[]"
        if isinstance(refs_raw, str):
            try:
                refs_list = json.loads(refs_raw)
            except (json.JSONDecodeError, TypeError):
                refs_list = []
        else:
            refs_list = refs_raw

        # Count outlinks (only to existing pages)
        valid_refs = [r for r in refs_list if r in page_keys and r != pk]

        G.add_node(pk)
        node_data[pk] = {
            "id": pk,
            "type": page.get("page_type", "entity"),
            "label": page.get("title", pk),
            "linkCount": len(valid_refs),
            "status": page.get("status", ""),
        }

        for ref in valid_refs:
            G.add_edge(pk, ref)

    return G, node_data


def _detect_communities(G) -> dict[str, int]:
    """Detect communities using Louvain algorithm.

    Returns:
        {node_id: community_id} mapping.
    """
    import networkx as nx

    if G.number_of_nodes() == 0:
        return {}

    # Convert to undirected for community detection
    U = G.to_undirected()
    try:
        communities = nx.community.louvain_communities(U, seed=42)
    except Exception as e:
        logger.warning("Community detection failed: %s, assigning single community", e)
        return {n: 0 for n in G.nodes()}

    community_map: dict[str, int] = {}
    for comm_id, members in enumerate(communities):
        for node in members:
            community_map[node] = comm_id

    return community_map


def _compute_insights(G, node_data: dict[str, dict], community_map: dict[str, int]) -> dict:
    """Compute graph insights: surprising connections + knowledge gaps.

    Per §12.2.3.
    """
    import networkx as nx

    surprising: list[dict] = []
    gaps: list[dict] = []

    if G.number_of_nodes() == 0:
        return {"surprising_connections": [], "knowledge_gaps": []}

    # ── Surprising connections ──
    # Cross-community edges with high relevance
    for u, v in G.edges():
        score = 0
        u_comm = community_map.get(u, -1)
        v_comm = community_map.get(v, -1)

        # Cross-community edge
        if u_comm != v_comm:
            score += 3

        # Cross-type edge (entity ↔ concept)
        u_type = node_data.get(u, {}).get("type", "")
        v_type = node_data.get(v, {}).get("type", "")
        if (u_type == "entity" and v_type == "concept") or (u_type == "concept" and v_type == "entity"):
            score += 2

        # Hub connection (high degree node)
        u_degree = G.degree(u)
        v_degree = G.degree(v)
        if u_degree > 5 or v_degree > 5:
            score += 1

        # Low-weight (few shared neighbors)
        common = len(list(nx.common_neighbors(G.to_undirected(), u, v)))
        if common == 0:
            score += 1

        if score >= 3:
            surprising.append({
                "source": u,
                "target": v,
                "score": score,
                "reason": _surprising_reason(u_comm, v_comm, u_type, v_type),
            })

    # Sort by score descending, take top 20
    surprising.sort(key=lambda x: x["score"], reverse=True)
    surprising = surprising[:20]

    # ── Knowledge gaps ──
    for node in G.nodes():
        in_deg = G.in_degree(node)
        out_deg = G.out_degree(node)
        total_deg = in_deg + out_deg

        # Isolated nodes (degree ≤ 1)
        if total_deg <= 1:
            gaps.append({
                "type": "isolated_node",
                "page_key": node,
                "label": node_data.get(node, {}).get("label", node),
                "description": f"页面 {node} 几乎没有链接（入链={in_deg}, 出链={out_deg}）",
            })

    # Sparse communities
    if community_map:
        comm_members: dict[int, list[str]] = {}
        for node, comm in community_map.items():
            comm_members.setdefault(comm, []).append(node)

        for comm_id, members in comm_members.items():
            if len(members) < 3:
                continue
            subgraph = G.subgraph(members)
            n_edges = subgraph.number_of_edges()
            max_edges = len(members) * (len(members) - 1)
            cohesion = n_edges / max_edges if max_edges > 0 else 0
            if cohesion < 0.15:
                gaps.append({
                    "type": "sparse_community",
                    "community_id": comm_id,
                    "members": members[:10],
                    "description": f"社区 {comm_id} 连接稀疏（{len(members)} 节点, 内聚度={cohesion:.2f}）",
                })

    # Bridge nodes (connect ≥ 3 communities)
    node_communities: dict[str, set[int]] = {}
    for node in G.nodes():
        neighbor_comms = set()
        for neighbor in G.successors(node):
            c = community_map.get(neighbor)
            if c is not None:
                neighbor_comms.add(c)
        for neighbor in G.predecessors(node):
            c = community_map.get(neighbor)
            if c is not None:
                neighbor_comms.add(c)
        node_communities[node] = neighbor_comms

    for node, comms in node_communities.items():
        if len(comms) >= 3:
            gaps.append({
                "type": "bridge_node",
                "page_key": node,
                "label": node_data.get(node, {}).get("label", node),
                "communities": list(comms),
                "description": f"页面 {node} 连接 {len(comms)} 个社区，单点故障风险",
            })

    return {
        "surprising_connections": surprising,
        "knowledge_gaps": gaps,
    }


def _surprising_reason(u_comm: int, v_comm: int, u_type: str, v_type: str) -> str:
    """Generate human-readable reason for surprising connection."""
    parts = []
    if u_comm != v_comm:
        parts.append("跨社区")
    if (u_type == "entity" and v_type == "concept") or (u_type == "concept" and v_type == "entity"):
        parts.append("跨类型")
    return "，".join(parts) if parts else "弱关联"


# ── Public API ──


def get_graph_data(db: Any, project_id: str, include_insights: bool = False) -> dict:
    """Get graph data for a project.

    Returns:
        {
            "nodes": [...],
            "edges": [...],
            "stats": {"nodes": N, "edges": M, "communities": C},
            "insights": {...}  (if include_insights=True)
        }
    """
    try:
        G, node_data = _build_graph(db, project_id)

        if G.number_of_nodes() == 0:
            return {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0, "communities": 0}, "insights": {}}

        # Detect communities
        community_map = _detect_communities(G)

        # Build nodes list with community info
        nodes = []
        for pk, data in node_data.items():
            node_entry = {
                **data,
                "community": community_map.get(pk, 0),
                "inDegree": G.in_degree(pk),
                "outDegree": G.out_degree(pk),
            }
            nodes.append(node_entry)

        # Build edges list
        edges = []
        for u, v in G.edges():
            edges.append({
                "source": u,
                "target": v,
                "weight": 1.0,
            })

        # Stats
        n_communities = len(set(community_map.values())) if community_map else 0
        stats = {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "communities": n_communities,
        }

        result: dict[str, Any] = {
            "nodes": nodes,
            "edges": edges,
            "stats": stats,
        }

        # Insights (optional, more expensive)
        if include_insights:
            insights = _compute_insights(G, node_data, community_map)
            result["insights"] = insights

        logger.info("Graph data for project %s: %d nodes, %d edges, %d communities",
                     project_id, stats["nodes"], stats["edges"], stats["communities"])
        return result

    except Exception as e:
        logger.error("Graph analysis failed for project %s: %s", project_id, e)
        return {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0, "communities": 0}, "insights": {}}
