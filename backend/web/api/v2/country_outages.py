"""国家中断观测 v2 资源。"""

from __future__ import annotations

import hashlib
import json

from flask import request
from flask_restful import Resource

from services.country_outage_registry import (
    CountryOutagePublicationNotFound,
    CountryOutageRegistryError,
)
from services.country_outage_service import (
    CountryOutageInvalidReference,
    CountryOutageNotFound,
    CountryOutageSourceUnavailable,
    EventStoryUnavailable,
    get_country_outage_asns,
    get_country_outage_audit,
    get_country_outage_overview,
    get_country_outage_query_context,
    get_country_outage_series,
    resolve_country_outage,
)
from services.features_service import get_country_feature_series
from services.data_layer_224_310_runtime import (
    DataLayerIntegrityError,
    DataLayerNotConfigured,
    DataLayerPublicationNotFound,
    data_layer_runtime,
)
from services.country_outage_trend_product import (
    TrendProductValidationError,
    get_country_outage_trend_product,
)


def _not_found():
    return {
        "status": False,
        "msg": "未找到该国家中断事件事实",
        "observation_state": "event_not_found",
    }, 404


def _unavailable(error: Exception):
    return {
        "status": False,
        "msg": str(error),
        "observation_state": "unavailable",
    }, 503


def _publication_not_found(error: Exception):
    return {
        "status": False,
        "msg": f"请求的发布快照不存在或不可读取：{error}",
        "observation_state": "publication_not_found",
    }, 404


def _etag_response(payload: dict, suffix: str):
    revision = int(payload.get("revision") or payload.get("latest_revision") or 1)
    incident_id = (
        payload.get("incident_id")
        or (payload.get("event_identity") or {}).get("incident_id")
        or "country-outage"
    )
    freshness = str(payload.get("data_through") or "no-data")
    publication_id = str(payload.get("publication_id") or "no-publication")
    query_hash = hashlib.sha256(request.query_string).hexdigest()[:12]
    payload_hash = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]
    etag = (
        f'"{incident_id}:{publication_id}:{revision}:{freshness}:'
        f'{suffix}:{query_hash}:{payload_hash}"'
    )
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("If-None-Match") == etag:
        return "", 304, headers
    return payload, 200, headers


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(request.args.get(name, default)))
    except (TypeError, ValueError):
        return default


def _data_layer_call(method: str, *args):
    """命中生产选择时只读统一层；未配置或未收录事件时才允许旧路径。"""
    try:
        runtime = data_layer_runtime()
    except DataLayerNotConfigured:
        return False, None
    except DataLayerIntegrityError as error:
        raise CountryOutageSourceUnavailable(str(error)) from error
    try:
        payload = getattr(runtime, method)(*args)
    except DataLayerIntegrityError as error:
        raise CountryOutageSourceUnavailable(str(error)) from error
    return payload is not None, payload


class CountryOutageResolveResource(Resource):
    def get(self):
        legacy_reference = request.args.get("ref", "").strip()
        if not legacy_reference:
            return {
                "status": False,
                "msg": "缺少事件 ref",
                "observation_state": "invalid_reference",
            }, 400
        try:
            selected, payload = _data_layer_call("resolve", legacy_reference)
            if selected:
                return _etag_response(payload, "resolve")
            payload = resolve_country_outage(legacy_reference)
        except CountryOutageInvalidReference:
            return {
                "status": False,
                "msg": "事件 ref 不是合法的 country_outage 引用",
                "observation_state": "invalid_reference",
            }, 400
        except CountryOutageNotFound:
            return _not_found()
        except (
            CountryOutageRegistryError,
            CountryOutageSourceUnavailable,
        ) as error:
            return _unavailable(error)
        return _etag_response(payload, "resolve")


class CountryOutageOverviewResource(Resource):
    def get(self, incident_id):
        publication_id = request.args.get("publication_id")
        try:
            selected, payload = _data_layer_call(
                "overview", incident_id, publication_id
            )
            if selected:
                return _etag_response(payload, "overview")
            payload = get_country_outage_overview(
                incident_id,
                publication_id=publication_id,
            )
        except CountryOutageNotFound:
            return _not_found()
        except (CountryOutagePublicationNotFound, DataLayerPublicationNotFound) as error:
            return _publication_not_found(error)
        except (
            CountryOutageRegistryError,
            CountryOutageSourceUnavailable,
            EventStoryUnavailable,
        ) as error:
            return _unavailable(error)
        return _etag_response(payload, "overview")


class CountryOutageSeriesResource(Resource):
    def get(self, incident_id):
        publication_id = request.args.get("publication_id")
        try:
            selected, payload = _data_layer_call(
                "series", incident_id, publication_id
            )
            if selected:
                return _etag_response(payload, "series")
            context = get_country_outage_query_context(
                incident_id,
                publication_id=publication_id,
            )
            resource_series = None
            if context["resource_state"] == "available":
                feature_payload = get_country_feature_series(
                    start_time=context["window_start_local"][:19].replace(
                        "T", " "
                    ),
                    end_time=context["window_end_local"][:19].replace(
                        "T", " "
                    ),
                    country=context["country_name"],
                    page_num="1",
                    page_size="5",
                )
                feature_items = (
                    feature_payload.get("data")
                    if isinstance(feature_payload, dict)
                    else []
                )
                feature_item = next(
                    (
                        item
                        for item in feature_items or []
                        if isinstance(item, dict)
                        and item.get("country") == context["country_name"]
                    ),
                    None,
                )
                resource_series = (
                    feature_item.get("time_series_data")
                    if isinstance(feature_item, dict)
                    else None
                )
            payload = get_country_outage_series(
                incident_id,
                publication_id=publication_id,
                resource_series=resource_series,
            )
        except CountryOutageNotFound:
            return _not_found()
        except (CountryOutagePublicationNotFound, DataLayerPublicationNotFound) as error:
            return _publication_not_found(error)
        except (
            CountryOutageRegistryError,
            CountryOutageSourceUnavailable,
            EventStoryUnavailable,
        ) as error:
            return _unavailable(error)
        return _etag_response(payload, "series")


class CountryOutageAsnResource(Resource):
    def get(self, incident_id):
        publication_id = request.args.get("publication_id")
        try:
            page = _positive_int("page", 1)
            page_size = min(60, _positive_int("page_size", 60))
            selected, payload = _data_layer_call(
                "empty_asn_page",
                incident_id,
                publication_id,
                page,
                page_size,
            )
            if selected:
                return _etag_response(payload, "asns")
            payload = get_country_outage_asns(
                incident_id,
                publication_id=publication_id,
                page=page,
                page_size=page_size,
                query=request.args.get("query", ""),
                address_family=request.args.get("address_family", "all"),
                state=request.args.get("state", "all"),
                sort=request.args.get(
                    "sort", "longest_fully_invisible_desc"
                ),
            )
        except CountryOutageNotFound:
            return _not_found()
        except (CountryOutagePublicationNotFound, DataLayerPublicationNotFound) as error:
            return _publication_not_found(error)
        except (
            CountryOutageRegistryError,
            CountryOutageSourceUnavailable,
            EventStoryUnavailable,
        ) as error:
            return _unavailable(error)
        return _etag_response(payload, "asns")


class CountryOutageAuditResource(Resource):
    def get(self, incident_id):
        publication_id = request.args.get("publication_id")
        try:
            selected, payload = _data_layer_call(
                "audit", incident_id, publication_id
            )
            if selected:
                return _etag_response(payload, "audit")
            payload = get_country_outage_audit(
                incident_id,
                publication_id=publication_id,
            )
        except CountryOutageNotFound:
            return _not_found()
        except (CountryOutagePublicationNotFound, DataLayerPublicationNotFound) as error:
            return _publication_not_found(error)
        except (
            CountryOutageRegistryError,
            CountryOutageSourceUnavailable,
            EventStoryUnavailable,
        ) as error:
            return _unavailable(error)
        return _etag_response(payload, "audit")


class CountryOutageTrendResource(Resource):
    """返回由同一不可变发布确定性编译的趋势分析制品。"""

    def get(self, incident_id):
        publication_id = request.args.get("publication_id")
        try:
            selected, payload = _data_layer_call(
                "trend", incident_id, publication_id
            )
            if selected:
                return _etag_response(payload, "trend")
            payload = get_country_outage_trend_product(
                incident_id,
                publication_id=publication_id,
            )
        except CountryOutageNotFound:
            return _not_found()
        except (CountryOutagePublicationNotFound, DataLayerPublicationNotFound) as error:
            return _publication_not_found(error)
        except TrendProductValidationError as error:
            return {
                "status": False,
                "msg": str(error),
                "observation_state": "trend_unavailable",
                "error_code": error.code,
                "error_field": error.field,
            }, 422
        except (
            CountryOutageRegistryError,
            CountryOutageSourceUnavailable,
            EventStoryUnavailable,
        ) as error:
            return _unavailable(error)
        return _etag_response(payload, "trend")
