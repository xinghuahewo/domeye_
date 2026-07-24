"""国家中断 Incident/Episode/Observation v2 的事务 Repository。"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

try:
    from psycopg2 import extras, sql
except ModuleNotFoundError:  # 本地纯单元测试不要求安装 PostgreSQL 驱动。
    class _FallbackComposable(str):
        def format(self, *values):
            rendered = str(self)
            for value in values:
                rendered = rendered.replace("{}", str(value), 1)
            return _FallbackComposable(rendered)

    class _FallbackSQL:
        @staticmethod
        def SQL(value):
            return _FallbackComposable(value)

        @staticmethod
        def Identifier(value):
            return _FallbackComposable('"' + value.replace('"', '""') + '"')

    class _FallbackExtras:
        RealDictCursor = object

    sql = _FallbackSQL()
    extras = _FallbackExtras()


INCIDENT_TABLE = "country_outage_incident_v2"
EPISODE_TABLE = "country_outage_episode_v2"
OBSERVATION_TABLE = "country_outage_observation_v2"
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class CountryOutageV2RepositoryError(RuntimeError):
    """v2 事务、追加写或同快照合同失败。"""


def identifier(value: object) -> sql.Identifier:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise CountryOutageV2RepositoryError("数据库标识符非法")
    return sql.Identifier(value)


def canonical_payload(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise CountryOutageV2RepositoryError(
            "v2 payload 无法规范序列化"
        ) from error


def create_v2_tables(conn) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS country_outage_incident_v2 (
                incident_id text PRIMARY KEY,
                source text NOT NULL,
                country_code char(2) NOT NULL,
                collector_id text NOT NULL,
                detected_at timestamptz NOT NULL,
                onset_at timestamptz,
                peak_at timestamptz,
                trough_at timestamptz,
                partial_recovery_at timestamptz,
                full_recovery_at timestamptz,
                observation_end_at timestamptz NOT NULL,
                duration_state text NOT NULL,
                recovery_state text NOT NULL,
                cohort_id text NOT NULL,
                peak_snapshot_id text,
                trough_snapshot_id text,
                algorithm_version text NOT NULL,
                legacy_ref text,
                incident_payload jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                derived_at timestamptz NOT NULL DEFAULT now(),
                CHECK (duration_state IN (
                    'exact','lower_bound','interval','unknown'
                )),
                CHECK (recovery_state IN (
                    'ongoing','recovering','partially_recovered',
                    'fully_recovered','unknown'
                ))
            )
            """
        )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_country_outage_incident_v2_legacy_ref
            ON country_outage_incident_v2 (legacy_ref)
            WHERE legacy_ref IS NOT NULL
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS country_outage_episode_v2 (
                episode_id text PRIMARY KEY,
                incident_id text NOT NULL REFERENCES
                    country_outage_incident_v2(incident_id),
                ordinal integer NOT NULL,
                onset_at timestamptz,
                peak_at timestamptz,
                trough_at timestamptz,
                partial_recovery_at timestamptz,
                full_recovery_at timestamptz,
                observation_end_at timestamptz NOT NULL,
                duration_state text NOT NULL,
                recovery_state text NOT NULL,
                cohort_id text NOT NULL,
                peak_snapshot_id text,
                trough_snapshot_id text,
                algorithm_version text NOT NULL,
                episode_payload jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                derived_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (incident_id, ordinal)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS country_outage_observation_v2 (
                snapshot_id text PRIMARY KEY,
                incident_id text REFERENCES
                    country_outage_incident_v2(incident_id),
                source text NOT NULL,
                country_code char(2) NOT NULL,
                collector_id text NOT NULL,
                observed_at timestamptz NOT NULL,
                continuity_state text NOT NULL,
                cohort_id text NOT NULL,
                baseline_asn_count integer NOT NULL,
                affected_asn_count integer,
                affected_asn_ratio double precision,
                visible_asn_count integer,
                visible_asn_ratio double precision,
                baseline_prefix_vp_count bigint,
                visible_prefix_vp_count bigint,
                visible_prefix_vp_ratio double precision,
                affected_asns jsonb,
                state_result_ref jsonb,
                observation_payload jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (
                    source, country_code, collector_id, observed_at, cohort_id
                )
            )
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def observation_columns(
    observation: Mapping[str, Any], incident_id: str
) -> dict[str, Any]:
    if not isinstance(observation, Mapping):
        raise CountryOutageV2RepositoryError("observation 必须是对象")
    schema_version = observation.get("schema_version")
    if schema_version == "country-outage-observation/v2":
        metrics = observation.get("metrics", {})
        prefix_vp = observation.get("prefix_vp", {})
        cohort = observation.get("cohort", {})
        dual = observation.get("dual_stack", {})
        source = observation.get("source", "state_replay")
        country = observation.get("country_code", "IR")
        collector = observation.get("collector_id", "rrc25")
        affected_count = metrics.get("affected_asn_count")
        affected_ratio = metrics.get("affected_asn_ratio")
        visible_count = metrics.get("visible_origin_asn_count")
        visible_ratio = metrics.get("visible_origin_asn_ratio")
    elif schema_version == "country-outage-live-observation/v2":
        metrics = observation.get("asn_state", {})
        prefix_vp = observation.get("prefix_vp", {})
        cohort = observation.get("cohort", {})
        dual = {"affected_asns": metrics.get("affected_asns")}
        source = observation.get("source")
        country = observation.get("country_code")
        collector = observation.get("collector_id")
        affected_count = metrics.get("affected_asn_count")
        affected_ratio = metrics.get("affected_asn_ratio")
        visible_count = metrics.get("visible_asn_count")
        visible_ratio = metrics.get("visible_asn_ratio")
    else:
        raise CountryOutageV2RepositoryError(
            "observation schema_version 非法"
        )
    required_text = {
        "snapshot_id": observation.get("snapshot_id"),
        "source": source,
        "country_code": country,
        "collector_id": collector,
        "observed_at": observation.get("observed_at"),
        "continuity_state": observation.get("continuity_state"),
        "cohort_id": cohort.get("cohort_id"),
    }
    if any(
        not isinstance(value, str) or not value
        for value in required_text.values()
    ):
        raise CountryOutageV2RepositoryError(
            "observation 标识字段不完整"
        )
    baseline_asn_count = cohort.get("baseline_asn_count")
    if (
        isinstance(baseline_asn_count, bool)
        or not isinstance(baseline_asn_count, int)
        or baseline_asn_count < 0
    ):
        raise CountryOutageV2RepositoryError("baseline_asn_count 非法")
    return {
        **required_text,
        "incident_id": incident_id,
        "baseline_asn_count": baseline_asn_count,
        "affected_asn_count": affected_count,
        "affected_asn_ratio": affected_ratio,
        "visible_asn_count": visible_count,
        "visible_asn_ratio": visible_ratio,
        "baseline_prefix_vp_count": prefix_vp.get("baseline_count"),
        "visible_prefix_vp_count": prefix_vp.get("visible_count"),
        "visible_prefix_vp_ratio": prefix_vp.get("visible_ratio"),
        "affected_asns": dual.get("affected_asns"),
        "state_result_ref": observation.get("state_result_ref"),
        "observation_payload": dict(observation),
    }


def _append_observation(cursor, observation, incident_id) -> None:
    values = observation_columns(observation, incident_id)
    cursor.execute(
        """
        INSERT INTO country_outage_observation_v2 (
            snapshot_id, incident_id, source, country_code, collector_id,
            observed_at, continuity_state, cohort_id, baseline_asn_count,
            affected_asn_count, affected_asn_ratio,
            visible_asn_count, visible_asn_ratio,
            baseline_prefix_vp_count, visible_prefix_vp_count,
            visible_prefix_vp_ratio, affected_asns, state_result_ref,
            observation_payload
        ) VALUES (
            %(snapshot_id)s, %(incident_id)s, %(source)s, %(country_code)s,
            %(collector_id)s, %(observed_at)s, %(continuity_state)s,
            %(cohort_id)s, %(baseline_asn_count)s,
            %(affected_asn_count)s, %(affected_asn_ratio)s,
            %(visible_asn_count)s, %(visible_asn_ratio)s,
            %(baseline_prefix_vp_count)s, %(visible_prefix_vp_count)s,
            %(visible_prefix_vp_ratio)s, %(affected_asns)s::jsonb,
            %(state_result_ref)s::jsonb, %(observation_payload)s::jsonb
        )
        ON CONFLICT (snapshot_id) DO NOTHING
        """,
        {
            **values,
            "affected_asns": canonical_payload(values["affected_asns"]),
            "state_result_ref": canonical_payload(values["state_result_ref"]),
            "observation_payload": canonical_payload(
                values["observation_payload"]
            ),
        },
    )
    if cursor.rowcount != 0:
        return
    cursor.execute(
        """
        SELECT incident_id, observation_payload
        FROM country_outage_observation_v2
        WHERE snapshot_id = %s
        """,
        (values["snapshot_id"],),
    )
    row = cursor.fetchone()
    if row is None:
        raise CountryOutageV2RepositoryError(
            "Observation 冲突后无法读取既有 payload"
        )
    if isinstance(row, Mapping):
        existing_incident_id = row.get("incident_id")
        existing = row.get("observation_payload")
    else:
        existing_incident_id, existing = row
    if existing_incident_id != incident_id:
        raise CountryOutageV2RepositoryError(
            "同 snapshot_id 的 Incident 关联不一致"
        )
    if isinstance(existing, str):
        try:
            existing = json.loads(existing)
        except json.JSONDecodeError as error:
            raise CountryOutageV2RepositoryError(
                "既有 Observation payload 非法"
            ) from error
    if canonical_payload(existing) != canonical_payload(observation):
        raise CountryOutageV2RepositoryError(
            "同 snapshot_id 的 Observation payload 不一致"
        )


def _upsert_incident(cursor, incident: Mapping[str, Any]) -> None:
    required = (
        "incident_id",
        "source",
        "country_code",
        "collector_id",
        "detected_at",
        "observation_end_at",
        "duration_state",
        "recovery_state",
        "cohort_id",
        "algorithm_version",
    )
    if not isinstance(incident, Mapping) or any(
        not isinstance(incident.get(field), str) or not incident.get(field)
        for field in required
    ):
        raise CountryOutageV2RepositoryError(
            "Incident v2 必填字段不完整"
        )
    cursor.execute(
        """
        INSERT INTO country_outage_incident_v2 (
            incident_id, source, country_code, collector_id, detected_at,
            onset_at, peak_at, trough_at, partial_recovery_at,
            full_recovery_at, observation_end_at, duration_state,
            recovery_state, cohort_id, peak_snapshot_id, trough_snapshot_id,
            algorithm_version, legacy_ref, incident_payload, derived_at
        ) VALUES (
            %(incident_id)s, %(source)s, %(country_code)s, %(collector_id)s,
            %(detected_at)s, %(onset_at)s, %(peak_at)s, %(trough_at)s,
            %(partial_recovery_at)s, %(full_recovery_at)s,
            %(observation_end_at)s, %(duration_state)s, %(recovery_state)s,
            %(cohort_id)s, %(peak_snapshot_id)s, %(trough_snapshot_id)s,
            %(algorithm_version)s, %(legacy_ref)s,
            %(incident_payload)s::jsonb, now()
        )
        ON CONFLICT (incident_id) DO UPDATE SET
            onset_at = EXCLUDED.onset_at,
            peak_at = EXCLUDED.peak_at,
            trough_at = EXCLUDED.trough_at,
            partial_recovery_at = EXCLUDED.partial_recovery_at,
            full_recovery_at = EXCLUDED.full_recovery_at,
            observation_end_at = EXCLUDED.observation_end_at,
            duration_state = EXCLUDED.duration_state,
            recovery_state = EXCLUDED.recovery_state,
            peak_snapshot_id = EXCLUDED.peak_snapshot_id,
            trough_snapshot_id = EXCLUDED.trough_snapshot_id,
            algorithm_version = EXCLUDED.algorithm_version,
            incident_payload = EXCLUDED.incident_payload,
            derived_at = now()
        """,
        {
            **{field: incident.get(field) for field in required},
            "onset_at": incident.get("onset_at"),
            "peak_at": incident.get("peak_at"),
            "trough_at": incident.get("trough_at"),
            "partial_recovery_at": incident.get("partial_recovery_at"),
            "full_recovery_at": incident.get("full_recovery_at"),
            "peak_snapshot_id": incident.get("peak_snapshot_id"),
            "trough_snapshot_id": incident.get("trough_snapshot_id"),
            "legacy_ref": incident.get("legacy_ref"),
            "incident_payload": canonical_payload(incident),
        },
    )


def _upsert_episode(cursor, episode: Mapping[str, Any]) -> None:
    required = (
        "episode_id",
        "incident_id",
        "ordinal",
        "observation_end_at",
        "duration_state",
        "recovery_state",
        "cohort_id",
        "algorithm_version",
    )
    if not isinstance(episode, Mapping) or any(
        episode.get(field) is None for field in required
    ):
        raise CountryOutageV2RepositoryError(
            "Episode v2 必填字段不完整"
        )
    cursor.execute(
        """
        INSERT INTO country_outage_episode_v2 (
            episode_id, incident_id, ordinal, onset_at, peak_at, trough_at,
            partial_recovery_at, full_recovery_at, observation_end_at,
            duration_state, recovery_state, cohort_id, peak_snapshot_id,
            trough_snapshot_id, algorithm_version, episode_payload,
            derived_at
        ) VALUES (
            %(episode_id)s, %(incident_id)s, %(ordinal)s, %(onset_at)s,
            %(peak_at)s, %(trough_at)s, %(partial_recovery_at)s,
            %(full_recovery_at)s, %(observation_end_at)s,
            %(duration_state)s, %(recovery_state)s, %(cohort_id)s,
            %(peak_snapshot_id)s, %(trough_snapshot_id)s,
            %(algorithm_version)s, %(episode_payload)s::jsonb, now()
        )
        ON CONFLICT (episode_id) DO UPDATE SET
            peak_at = EXCLUDED.peak_at,
            trough_at = EXCLUDED.trough_at,
            partial_recovery_at = EXCLUDED.partial_recovery_at,
            full_recovery_at = EXCLUDED.full_recovery_at,
            observation_end_at = EXCLUDED.observation_end_at,
            duration_state = EXCLUDED.duration_state,
            recovery_state = EXCLUDED.recovery_state,
            peak_snapshot_id = EXCLUDED.peak_snapshot_id,
            trough_snapshot_id = EXCLUDED.trough_snapshot_id,
            algorithm_version = EXCLUDED.algorithm_version,
            episode_payload = EXCLUDED.episode_payload,
            derived_at = now()
        """,
        {
            **{field: episode.get(field) for field in required},
            "onset_at": episode.get("onset_at"),
            "peak_at": episode.get("peak_at"),
            "trough_at": episode.get("trough_at"),
            "partial_recovery_at": episode.get("partial_recovery_at"),
            "full_recovery_at": episode.get("full_recovery_at"),
            "peak_snapshot_id": episode.get("peak_snapshot_id"),
            "trough_snapshot_id": episode.get("trough_snapshot_id"),
            "episode_payload": canonical_payload(episode),
        },
    )


def validate_legacy_projection(
    incident: Mapping[str, Any], projection: Mapping[str, Any] | None
) -> None:
    if projection is None:
        return
    if not isinstance(projection, Mapping):
        raise CountryOutageV2RepositoryError(
            "legacy_projection 必须是对象"
        )
    members = projection.get("outage_ases")
    count = projection.get("max_outage_as_num")
    total = projection.get("total_as_num")
    ratio = projection.get("max_outage_as_ratio")
    if (
        not isinstance(members, list)
        or members != sorted(set(members))
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != len(members)
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total <= 0
        or not isinstance(ratio, (int, float))
        or isinstance(ratio, bool)
        or abs(float(ratio) - count / total) > 1e-15
        or projection.get("peak_snapshot_id")
        != incident.get("peak_snapshot_id")
    ):
        raise CountryOutageV2RepositoryError(
            "legacy projection 未绑定同一 peak snapshot"
        )


def _upsert_legacy_projection(
    cursor,
    *,
    table: str,
    incident: Mapping[str, Any],
    projection: Mapping[str, Any],
    source: str,
    country: str,
    outage_id: int,
) -> None:
    validate_legacy_projection(incident, projection)
    cursor.execute(
        sql.SQL(
            """
            INSERT INTO {} (
                s_time, e_time, duration, country_chinese_name,
                total_as_num, max_outage_as_num, max_outage_as_ratio,
                outage_level, outage_level_descr, outage_ases, event_info,
                source, country, outage_id, incident_id_v2,
                peak_snapshot_id, legacy_semantics
            ) VALUES (
                %(s_time)s, %(e_time)s, %(duration)s,
                %(country_chinese_name)s, %(total_as_num)s,
                %(max_outage_as_num)s, %(max_outage_as_ratio)s,
                %(outage_level)s, %(outage_level_descr)s,
                %(outage_ases)s::jsonb, %(event_info)s, %(source)s,
                %(country)s, %(outage_id)s, %(incident_id_v2)s,
                %(peak_snapshot_id)s, %(legacy_semantics)s::jsonb
            )
            ON CONFLICT (source, country, outage_id) DO UPDATE SET
                e_time = EXCLUDED.e_time,
                duration = EXCLUDED.duration,
                total_as_num = EXCLUDED.total_as_num,
                max_outage_as_num = EXCLUDED.max_outage_as_num,
                max_outage_as_ratio = EXCLUDED.max_outage_as_ratio,
                outage_level = EXCLUDED.outage_level,
                outage_level_descr = EXCLUDED.outage_level_descr,
                outage_ases = EXCLUDED.outage_ases,
                event_info = EXCLUDED.event_info,
                incident_id_v2 = EXCLUDED.incident_id_v2,
                peak_snapshot_id = EXCLUDED.peak_snapshot_id,
                legacy_semantics = EXCLUDED.legacy_semantics
            """
        ).format(identifier(table)),
        {
            "s_time": projection.get("s_time"),
            "e_time": (
                projection.get("e_time")
                if incident.get("recovery_state") == "fully_recovered"
                else None
            ),
            "duration": (
                projection.get("duration")
                if incident.get("duration_state") == "exact"
                else None
            ),
            "country_chinese_name": projection.get("country_chinese_name"),
            "total_as_num": projection.get("total_as_num"),
            "max_outage_as_num": projection.get("max_outage_as_num"),
            "max_outage_as_ratio": projection.get("max_outage_as_ratio"),
            "outage_level": projection.get("outage_level"),
            "outage_level_descr": projection.get("outage_level_descr"),
            "outage_ases": canonical_payload(projection.get("outage_ases")),
            "event_info": projection.get("event_info"),
            "source": source,
            "country": country,
            "outage_id": outage_id,
            "incident_id_v2": incident.get("incident_id"),
            "peak_snapshot_id": incident.get("peak_snapshot_id"),
            "legacy_semantics": canonical_payload(
                {
                    "s_time": "detected_at_projection",
                    "e_time": (
                        "full_recovery_at_only"
                        if incident.get("recovery_state")
                        == "fully_recovered"
                        else "unknown_null"
                    ),
                    "outage_ases": "peak_snapshot_members",
                    "event_info": "rendered_from_structured_facts",
                }
            ),
        },
    )


def persist_v2(
    *,
    conn,
    incident: Mapping[str, Any],
    episodes: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
    legacy_table: str | None = None,
    legacy_projection: Mapping[str, Any] | None = None,
    legacy_source: str | None = None,
    legacy_country: str | None = None,
    legacy_outage_id: int | None = None,
) -> None:
    if (
        isinstance(episodes, (str, bytes, Mapping))
        or not isinstance(episodes, Sequence)
        or isinstance(observations, (str, bytes, Mapping))
        or not isinstance(observations, Sequence)
        or not observations
    ):
        raise CountryOutageV2RepositoryError(
            "episodes/observations 必须是非空合法序列"
        )
    if legacy_table is not None:
        identifier(legacy_table)
        if (
            not isinstance(legacy_source, str)
            or not legacy_source
            or not isinstance(legacy_country, str)
            or not legacy_country
            or isinstance(legacy_outage_id, bool)
            or not isinstance(legacy_outage_id, int)
            or legacy_projection is None
        ):
            raise CountryOutageV2RepositoryError(
                "旧兼容投影身份不完整"
            )
    cursor = conn.cursor()
    try:
        _upsert_incident(cursor, incident)
        for episode in episodes:
            _upsert_episode(cursor, episode)
        for observation in observations:
            _append_observation(
                cursor, observation, str(incident.get("incident_id"))
            )
        if legacy_table is not None:
            _upsert_legacy_projection(
                cursor,
                table=legacy_table,
                incident=incident,
                projection=legacy_projection,
                source=legacy_source,
                country=legacy_country,
                outage_id=legacy_outage_id,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def get_v2(conn, *, incident_id: str | None = None, legacy_ref: str | None = None):
    if bool(incident_id) == bool(legacy_ref):
        raise CountryOutageV2RepositoryError(
            "incident_id 与 legacy_ref 必须且只能提供一个"
        )
    cursor = conn.cursor(cursor_factory=extras.RealDictCursor)
    try:
        if incident_id:
            cursor.execute(
                """
                SELECT incident_payload
                FROM country_outage_incident_v2
                WHERE incident_id = %s
                """,
                (incident_id,),
            )
        else:
            cursor.execute(
                """
                SELECT incident_payload
                FROM country_outage_incident_v2
                WHERE legacy_ref = %s
                """,
                (legacy_ref,),
            )
        row = cursor.fetchone()
        return None if row is None else row["incident_payload"]
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


__all__ = (
    "CountryOutageV2RepositoryError",
    "EPISODE_TABLE",
    "INCIDENT_TABLE",
    "OBSERVATION_TABLE",
    "canonical_payload",
    "create_v2_tables",
    "get_v2",
    "identifier",
    "observation_columns",
    "persist_v2",
    "validate_legacy_projection",
)
