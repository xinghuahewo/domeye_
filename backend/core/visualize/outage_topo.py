import pandas as pd
import json

import os
import sys


def load_local_env():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(current_dir, '.env'),
        os.path.join(os.path.dirname(current_dir), '.env'),
        os.path.join(os.path.dirname(os.path.dirname(current_dir)), '.env'),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))), '.env'),
    ]

    for env_path in candidates:
        if not os.path.exists(env_path):
            continue
        with open(env_path, 'r', encoding='utf-8') as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if not key or key in os.environ:
                    continue
                os.environ[key] = value
        break


load_local_env()

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config.config import *
import pickle
import networkx as nx

from config.database import conn_15, DATABASE, USER, PASSWORD, HOST, PORT
from config.logger import outage_logger
from database.country_topology import (
    get_latest_build_time,
    get_country_edge_count,
    get_country_edges_by_nodes,
    get_all_country_edges,
    get_country_snapshot,
)
from database.utils import get_conn

INFO_PATH = BASE_DIR + '/screen_data/info/'
OUTPUT_PATH = BASE_DIR + '/screen_data/output_data/'
with open(INFO_PATH + "as_dict.json", encoding="utf-8") as f:
    as_dict = json.load(f)
    f.close()


def get_as_country(asn: str) -> str:
    country = as_dict[asn]["country_cn"]
    return country


def outage_topo(domestic_path):
    global country_topo
    country_topo = dict()
    for i in range(len(domestic_path)):
        path_list = domestic_path.iloc[i][1:-1].replace("'", "").split(",")
        for index in range(len(path_list)):
            path_list[index] = path_list[index].strip().split(" ")
        for path in path_list:
            previous_path = path[0]
            for idx in range(1, len(path)):
                if int(path[idx]) in range(64512, 65536):
                    continue
                if previous_path == path[idx]:
                    continue
                try:
                    if get_as_country(previous_path) == get_as_country(path[idx]):
                        if get_as_country(path[idx]) not in country_topo.keys():
                            country_topo.setdefault(
                                get_as_country(path[idx]),
                                {"node": set(), "edge": set()},
                            )
                            country_topo[get_as_country(path[idx])]["edge"].add(
                                (previous_path, path[idx])
                            )
                            country_topo[get_as_country(path[idx])]["node"].add(
                                path[idx]
                            )
                            country_topo[get_as_country(path[idx])]["node"].add(
                                previous_path
                            )
                        else:
                            country_topo[get_as_country(path[idx])]["edge"].add(
                                (previous_path, path[idx])
                            )
                            country_topo[get_as_country(path[idx])]["node"].add(
                                path[idx]
                            )
                            country_topo[get_as_country(path[idx])]["node"].add(
                                previous_path
                            )
                        previous_path = path[idx]
                except KeyError:
                    previous_path = path[idx]


def cul_topo():
    domestic_path = pd.read_csv(OUTPUT_PATH + "domestic_connection.csv")["path"]
    abroad_path = pd.read_csv(OUTPUT_PATH + "abroad_connection.csv")["path"]

    outage_topo(domestic_path)
    outage_topo(abroad_path)

    with open(OUTPUT_PATH + "topo.pkl", "wb") as f:
        pickle.dump(country_topo, f)


def _generate_graph_with_conn(conn, country: str) -> nx.DiGraph:
    """
    获取某个国家的拓扑
    优先从数据库读取（country_topology_edge），若失败再回退到旧 topo.pkl。
    """
    build_time = get_latest_build_time(conn, COUNTRY_TOPOLOGY_EDGE_TABLE, country)
    if build_time is None:
        raise KeyError("no build_time")
    rows = get_all_country_edges(conn, COUNTRY_TOPOLOGY_EDGE_TABLE, country, build_time)
    G = nx.Graph()
    for a, b, _w in rows:
        G.add_edge(str(a), str(b))
    return G


def generate_graph(country: str) -> nx.DiGraph:
    try:
        return _generate_graph_with_conn(conn_15, country)
    except Exception as e:
        outage_logger.error(f"[TOPO] generate_graph primary failed: country={country}, err={e}", exc_info=True)
        conn = None
        try:
            conn = get_conn(database=DATABASE, user=USER, password=PASSWORD, host=HOST, port=PORT)
            return _generate_graph_with_conn(conn, country)
        except Exception as e2:
            outage_logger.error(f"[TOPO] generate_graph fallback failed: country={country}, err={e2}", exc_info=True)
            with open(OUTPUT_PATH + "topo.pkl", "rb") as f:
                country_topo = pickle.load(f)
            G = nx.DiGraph()
            G.add_edges_from(country_topo[country]["edge"])
            G.add_nodes_from(country_topo[country]["node"])
            return G
        finally:
            if conn is not None:
                conn.close()


def _build_country_topo_dict_with_conn(
    conn,
    country_cn: str,
    outage_ases,
    topo_mode: str = "auto",
    k_hop: int = 2,
    max_nodes: int = 2000,
    max_edges: int = 5000,
):
    """
    国家中断页面用：返回 {nodes, links}，并将 outage_ases 标红。
    - topo_mode: auto|full|subgraph
      - auto：小国返回full；大国返回subgraph
    - subgraph：以 outage_ases 为种子取 k-hop 子图（默认2跳），并做节点/边上限保护
    """
    if outage_ases is None:
        outage_ases = []
    # 确保是字符串列表（与前端/事件表一致）
    outage_ases_str = [str(x) for x in outage_ases]

    if topo_mode == "fullgraph":
        topo_mode = "full"
    if topo_mode not in ("auto", "full", "subgraph"):
        topo_mode = "auto"

    outage_logger.info(
        f"[TOPO] build_country_topo_dict start: country={country_cn}, topo_mode={topo_mode}, "
        f"outage_ases={len(outage_ases_str)}"
    )

    if topo_mode in ("auto", "full"):
        try:
            snap = get_country_snapshot(conn, COUNTRY_TOPOLOGY_SNAPSHOT_TABLE, country_cn)
            if snap and isinstance(snap.get("graph_json"), (dict,)):
                topo = snap["graph_json"]
            elif snap and isinstance(snap.get("graph_json"), str):
                topo = json.loads(snap["graph_json"])
            else:
                topo = None
        except Exception as e:
            outage_logger.error(f"[TOPO] snapshot read failed: country={country_cn}, err={e}", exc_info=True)
            topo = None

        if topo:
            red = set(outage_ases_str)
            for node in topo.get("nodes", []):
                if str(node.get("name")) in red:
                    node["itemStyle"] = {"color": "#ff0000"}
            outage_logger.info(
                f"[TOPO] snapshot used: country={country_cn}, nodes={len(topo.get('nodes', []))}, "
                f"links={len(topo.get('links', []))}"
            )
            return topo

    build_time = get_latest_build_time(conn, COUNTRY_TOPOLOGY_EDGE_TABLE, country_cn)
    if build_time is None:
        raise KeyError("no build_time")

    resolved_topo_mode = topo_mode
    edge_count = None
    if topo_mode == "auto":
        edge_count = get_country_edge_count(conn, COUNTRY_TOPOLOGY_EDGE_TABLE, country_cn, build_time)
        resolved_topo_mode = "full" if edge_count <= COUNTRY_TOPOLOGY_FULL_EDGE_THRESHOLD else "subgraph"
    outage_logger.info(
        f"[TOPO] build_country_topo_dict: country={country_cn}, build_time={build_time}, edge_count={edge_count}, "
        f"topo_mode={topo_mode}, resolved={resolved_topo_mode}, outage_ases={len(outage_ases_str)}"
    )

    if resolved_topo_mode == "full":
        rows = get_all_country_edges(conn, COUNTRY_TOPOLOGY_EDGE_TABLE, country_cn, build_time)
        outage_logger.info(f"[TOPO] full mode db rows: country={country_cn}, rows={len(rows)}")
        G = nx.Graph()
        for a, b, _w in rows:
            G.add_edge(str(a), str(b))
        for n in outage_ases_str:
            G.add_node(n)
        topo = graph_to_dict(G, "country", outage_ases_str)
        red = set(outage_ases_str)
        for node in topo.get("nodes", []):
            if str(node.get("name")) in red:
                node["itemStyle"] = {"color": "#ff0000"}
        return topo

    seed_int = []
    for s in outage_ases_str:
        try:
            seed_int.append(int(s))
        except Exception:
            continue

    visited = set(seed_int)
    frontier = list(seed_int)
    edges = set()

    for _ in range(max(0, int(k_hop))):
        if not frontier:
            break
        rows = get_country_edges_by_nodes(conn, COUNTRY_TOPOLOGY_EDGE_TABLE, country_cn, build_time, frontier)
        outage_logger.info(f"[TOPO] subgraph hop rows: country={country_cn}, rows={len(rows)}, frontier={len(frontier)}")
        next_frontier = []
        for a, b, _w in rows:
            if a > b:
                a, b = b, a
            edges.add((a, b))
            if len(edges) >= max_edges:
                break
            if a not in visited:
                visited.add(a)
                next_frontier.append(a)
            if b not in visited:
                visited.add(b)
                next_frontier.append(b)
            if len(visited) >= max_nodes:
                break
        if len(edges) >= max_edges or len(visited) >= max_nodes:
            break
        frontier = next_frontier

    G = nx.Graph()
    for a, b in edges:
        G.add_edge(str(a), str(b))
    for n in outage_ases_str:
        G.add_node(n)
    return graph_to_dict(G, "country", outage_ases_str)


def build_country_topo_dict(
    country_cn: str,
    outage_ases,
    topo_mode: str = "auto",
    k_hop: int = 2,
    max_nodes: int = 2000,
    max_edges: int = 5000,
):
    if outage_ases is None:
        outage_ases = []
    outage_ases_str = [str(x) for x in outage_ases]
    try:
        return _build_country_topo_dict_with_conn(
            conn_15,
            country_cn=country_cn,
            outage_ases=outage_ases_str,
            topo_mode=topo_mode,
            k_hop=k_hop,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    except Exception as e:
        outage_logger.error(f"[TOPO] build_country_topo_dict primary failed: country={country_cn}, err={e}", exc_info=True)
        conn = None
        try:
            conn = get_conn(database=DATABASE, user=USER, password=PASSWORD, host=HOST, port=PORT)
            return _build_country_topo_dict_with_conn(
                conn,
                country_cn=country_cn,
                outage_ases=outage_ases_str,
                topo_mode=topo_mode,
                k_hop=k_hop,
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
        except Exception as e2:
            outage_logger.error(
                f"[TOPO] build_country_topo_dict fallback failed: country={country_cn}, err={e2}",
                exc_info=True,
            )
            topo_dict = {"nodes": [], "links": []}
            for i in outage_ases_str:
                topo_dict["nodes"].append({"name": i, "itemStyle": {"color": "#ff0000"}})
            return topo_dict
        finally:
            if conn is not None:
                conn.close()

def graph_to_dict(graph:nx.DiGraph, type = 'as',*args) -> dict:
    nodes = []
    edges = []

    if type == "as":
        nodes.append({
            "name": args[0],
            "itemStyle": {
                "color": "#00ffff"
            },

        })
    elif type == "boundary":
        nodes.append({
            "name": args[0],
            "itemStyle": {
                "color": "#fc8552"
            },
        })
        nodes.append({
            "name": args[1],
            "itemStyle": {
                "color": "#ff0000"
            },
        })
        edges.append({
            "source": args[0],
            "target": args[1],
            "value": 1,
            "lineStyle": {
                "color": "#000"
            }
        })
    else:
        args = args[0]
        for i in args:
            nodes.append({"name": i, "itemStyle": {"color": '#ff0000'}})

    for node in graph.nodes():
        if node not in args:
            nodes.append({
                "name": node,
                "itemStyle": {
                    "color": "#777"
                },
            })
    for edge in graph.edges():
        edges.append({
            "source": edge[0],
            "target": edge[1],
            "value": 1,
            "lineStyle": {
                "color": "#000"
            }
        })
    topo = {"nodes":nodes, "links":edges}
    return topo

if __name__ == '__main__':
    cul_topo()
