import json
import traceback
import psycopg2
from psycopg2 import extras
import os
import sys
import datetime
from typing import Optional, List

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.logger import database_logger
from database.utils import if_table_exist, time_cost
from psycopg2 import extensions


def _read_transaction_started_idle(conn):
    return (
        conn is not None
        and getattr(conn, 'closed', 1) == 0
        and conn.get_transaction_status() == extensions.TRANSACTION_STATUS_IDLE
    )


def _cleanup_implicit_read_transaction(conn, started_idle):
    if not started_idle or conn is None or getattr(conn, 'closed', 1) != 0:
        return

    try:
        if conn.get_transaction_status() != extensions.TRANSACTION_STATUS_IDLE:
            conn.rollback()
    except Exception as error:
        database_logger.warning(f"cleanup implicit read transaction failed: {error}")


@time_cost
def create_country_topology_edge_table(conn, table_name: str):
    """
    国家内部拓扑无向边表（所有国家一张表）：
    - 无向边按 (min_asn, max_asn) 规范化，只存一条
    - build_time 用于baseline版本标识（可保留最近N个版本回滚）
    """
    cursor = conn.cursor()
    sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name}(
            country_cn   text NOT NULL,
            build_time   timestamp(0) without time zone NOT NULL,
            a_asn        int NOT NULL,
            b_asn        int NOT NULL,
            weight       int NOT NULL DEFAULT 1,
            PRIMARY KEY(country_cn, build_time, a_asn, b_asn)
        );
    """
    cursor.execute(sql)
    conn.commit()

    try:
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_country_time
            ON {table_name} (country_cn, build_time DESC)
        """)
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_country_a
            ON {table_name} (country_cn, a_asn)
        """)
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_country_b
            ON {table_name} (country_cn, b_asn)
        """)
        conn.commit()
    except Exception as e:
        database_logger.warning(f"Failed creating indexes for {table_name}: {e}")
        conn.rollback()
    finally:
        cursor.close()


@time_cost
def create_country_topology_snapshot_table(conn, table_name: str):
    """
    可选：国家拓扑快照（用于小国全量图快速返回）
    graph_json 建议存：{node_count, edge_count, nodes, links}
    """
    cursor = conn.cursor()
    sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name}(
            country_cn   text PRIMARY KEY,
            build_time   timestamp(0) without time zone NOT NULL,
            node_count   int NOT NULL,
            edge_count   int NOT NULL,
            graph_json   jsonb NOT NULL
        );
    """
    cursor.execute(sql)
    conn.commit()
    try:
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table_name}_time
            ON {table_name} (build_time DESC)
        """)
        conn.commit()
    except Exception as e:
        database_logger.warning(f"Failed creating indexes for {table_name}: {e}")
        conn.rollback()
    finally:
        cursor.close()


def get_latest_build_time(conn, edge_table: str, country_cn: str):
    """
    获取某个国家最新build_time（baseline场景）。
    """
    started_idle = _read_transaction_started_idle(conn)
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT MAX(build_time) FROM {edge_table} WHERE country_cn = %s;",
            (country_cn,),
        )
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        database_logger.error(f"get_latest_build_time failed: {e}")
        database_logger.error(traceback.format_exc())
        conn.rollback()
        return None
    finally:
        cursor.close()
        _cleanup_implicit_read_transaction(conn, started_idle)


def delete_country_edges(conn, edge_table: str, country_cn: str, build_time=None):
    """
    baseline replace：删除某国旧版本（build_time=None表示删除该国所有版本）。
    """
    cursor = conn.cursor()
    try:
        if build_time is None:
            cursor.execute(
                f"DELETE FROM {edge_table} WHERE country_cn = %s;",
                (country_cn,),
            )
        else:
            cursor.execute(
                f"DELETE FROM {edge_table} WHERE country_cn = %s AND build_time = %s;",
                (country_cn, build_time),
            )
        conn.commit()
        database_logger.info(
            f"[DB] delete_country_edges ok: table={edge_table}, country={country_cn}, build_time={build_time}, rows={cursor.rowcount}"
        )
        return True
    except Exception as e:
        database_logger.error(f"delete_country_edges failed: {e}")
        database_logger.error(traceback.format_exc())
        conn.rollback()
        return False
    finally:
        cursor.close()


@time_cost
def insert_country_edges(conn, edge_table: str, country_cn: str, build_time, edges, weight_default: int = 1, page_size: int = 5000):
    """
    批量写入无向边。
    edges: Iterable[Tuple[int,int]] 或 Iterable[Tuple[int,int,int]]
    """
    if not edges:
        return 0

    rows = []
    for item in edges:
        if len(item) == 2:
            a, b = item
            w = weight_default
        else:
            a, b, w = item
        rows.append((country_cn, build_time, int(a), int(b), int(w)))

    cursor = conn.cursor()
    try:
        extras.execute_values(
            cursor,
            f"""
            INSERT INTO {edge_table} (country_cn, build_time, a_asn, b_asn, weight)
            VALUES %s
            ON CONFLICT (country_cn, build_time, a_asn, b_asn) DO UPDATE
            SET weight = EXCLUDED.weight
            """,
            rows,
            page_size=page_size,
        )
        conn.commit()
        database_logger.info(
            f"[DB] insert_country_edges ok: table={edge_table}, country={country_cn}, build_time={build_time}, rows={len(rows)}"
        )
        return len(rows)
    except Exception as e:
        database_logger.error(f"insert_country_edges failed: {e}")
        database_logger.error(traceback.format_exc())
        conn.rollback()
        return 0
    finally:
        cursor.close()


def upsert_country_snapshot(conn, snapshot_table: str, country_cn: str, build_time, graph_json: dict):
    cursor = conn.cursor()
    try:
        node_count = int(graph_json.get("node_count", len(graph_json.get("nodes", []))))
        edge_count = int(graph_json.get("edge_count", len(graph_json.get("links", []))))
        cursor.execute(
            f"""
            INSERT INTO {snapshot_table} (country_cn, build_time, node_count, edge_count, graph_json)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (country_cn) DO UPDATE
            SET build_time = EXCLUDED.build_time,
                node_count = EXCLUDED.node_count,
                edge_count = EXCLUDED.edge_count,
                graph_json = EXCLUDED.graph_json
            """,
            (country_cn, build_time, node_count, edge_count, json.dumps(graph_json)),
        )
        conn.commit()
        database_logger.info(
            f"[DB] upsert_country_snapshot ok: table={snapshot_table}, country={country_cn}, build_time={build_time}, "
            f"node_count={node_count}, edge_count={edge_count}"
        )
        return True
    except Exception as e:
        database_logger.error(f"upsert_country_snapshot failed: {e}")
        database_logger.error(traceback.format_exc())
        conn.rollback()
        return False
    finally:
        cursor.close()


def get_country_snapshot(conn, snapshot_table: str, country_cn: str):
    started_idle = _read_transaction_started_idle(conn)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cursor.execute(
            f"SELECT country_cn, build_time, node_count, edge_count, graph_json FROM {snapshot_table} WHERE country_cn = %s;",
            (country_cn,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        database_logger.error(f"get_country_snapshot failed: {e}")
        database_logger.error(traceback.format_exc())
        conn.rollback()
        return None
    finally:
        cursor.close()
        _cleanup_implicit_read_transaction(conn, started_idle)


def get_country_edges_by_nodes(conn, edge_table: str, country_cn: str, build_time, nodes: List[int]):
    """
    取子图边：返回所有与 nodes 集合相邻的边（1-hop）。
    注意：为避免 OR 影响索引使用，采用 UNION ALL 两次查询再去重。
    """
    if not nodes:
        return []

    started_idle = _read_transaction_started_idle(conn)
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT a_asn, b_asn, weight FROM {edge_table}
            WHERE country_cn = %s AND build_time = %s AND a_asn = ANY(%s)
            UNION
            SELECT a_asn, b_asn, weight FROM {edge_table}
            WHERE country_cn = %s AND build_time = %s AND b_asn = ANY(%s)
            """,
            (country_cn, build_time, nodes, country_cn, build_time, nodes),
        )
        rows = cursor.fetchall()
        return rows
    except Exception as e:
        database_logger.error(f"get_country_edges_by_nodes failed: {e}")
        database_logger.error(traceback.format_exc())
        conn.rollback()
        return []
    finally:
        cursor.close()
        _cleanup_implicit_read_transaction(conn, started_idle)


def get_country_edge_count(conn, edge_table: str, country_cn: str, build_time):
    started_idle = _read_transaction_started_idle(conn)
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT COUNT(*) FROM {edge_table} WHERE country_cn = %s AND build_time = %s;",
            (country_cn, build_time),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        database_logger.error(f"get_country_edge_count failed: {e}")
        database_logger.error(traceback.format_exc())
        conn.rollback()
        return 0
    finally:
        cursor.close()
        _cleanup_implicit_read_transaction(conn, started_idle)


def get_all_country_edges(conn, edge_table: str, country_cn: str, build_time, limit: Optional[int] = None):
    """
    取某国某版本的全量边（谨慎使用，大国可能很大）。
    """
    started_idle = _read_transaction_started_idle(conn)
    cursor = conn.cursor()
    try:
        if limit is None:
            cursor.execute(
                f"""
                SELECT a_asn, b_asn, weight FROM {edge_table}
                WHERE country_cn = %s AND build_time = %s
                """,
                (country_cn, build_time),
            )
        else:
            cursor.execute(
                f"""
                SELECT a_asn, b_asn, weight FROM {edge_table}
                WHERE country_cn = %s AND build_time = %s
                LIMIT %s
                """,
                (country_cn, build_time, limit),
            )
        return cursor.fetchall()
    except Exception as e:
        database_logger.error(f"get_all_country_edges failed: {e}")
        database_logger.error(traceback.format_exc())
        conn.rollback()
        return []
    finally:
        cursor.close()
        _cleanup_implicit_read_transaction(conn, started_idle)
