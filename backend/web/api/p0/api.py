"""P0 候选数据制品的只读 HTTP 资源。"""

from flask_restful import Resource

from services.p0_data_service import (
    P0DataError,
    get_p0_evidence,
    get_p0_metric,
    get_p0_quality,
    get_p0_status,
)


def _result(function, *arguments):
    try:
        return function(*arguments), 200
    except P0DataError as error:
        return error.as_payload(), error.status_code


class P0StatusResource(Resource):
    """返回显式候选仓库的数据身份、准入与覆盖。"""

    def get(self):
        return _result(get_p0_status)


class P0MetricResource(Resource):
    """返回一个已准入且缺失语义完整的 MetricSeries。"""

    def get(self, metric_name):
        return _result(get_p0_metric, metric_name)


class P0EvidenceResource(Resource):
    """通过稳定 Incident ID 返回引用闭合的 Evidence Bundle v2。"""

    def get(self, incident_id):
        return _result(get_p0_evidence, incident_id)


class P0QualityResource(Resource):
    """返回完整 P0 数据质量报告，不把失败决定隐藏为空数据。"""

    def get(self):
        return _result(get_p0_quality)
