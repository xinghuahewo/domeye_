from flask import request
from flask_restful import Resource

from services.features_service import (
    get_as_feature_series,
    get_as_outage_feature,
    get_country_feature_series,
    get_prefix_outage_feature,
    get_top_feature_data,
)


class TopFeatureResource(Resource):
    """
    获取置顶/关键目标的时序特征图表数据
    Endpoint: /api/v1/features/top
    """

    def get(self):
        return get_top_feature_data(
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
            target=request.args.get('target'),
        )

class CountryFeatureListResource(Resource):
    """
    获取国家时序特征列表
    Endpoint: /api/v1/features/countries
    """

    def get(self):
        return get_country_feature_series(
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
            country=request.args.get('country', ''),
            page_num=request.args.get('page_num'),
            page_size=request.args.get('page_size', 5),
        )


class ASFeatureListResource(Resource):
    """
    获取AS时序特征列表
    Endpoint: /api/v1/features/ases
    """

    def get(self):
        return get_as_feature_series(
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
            asn=request.args.get('asn', ''),
            country=request.args.get('country', ''),
            page_num=request.args.get('page_num'),
            page_size=request.args.get('page_size', 5),
        )


class CountryASOutageFeatureResource(Resource):
    """
    获取某个国家的AS中断事件时序特征
    Endpoint: /api/v1/features/outages/country-as
    """

    def get(self):
        return get_as_outage_feature(
            country=request.args.get('country'),
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
        )


class CountryPrefixOutageFeatureResource(Resource):
    """
    获取某个国家的前缀中断时序特征
    Endpoint: /api/v1/features/outages/country-prefix
    """

    def get(self):
        return get_prefix_outage_feature(
            country=request.args.get('country'),
            asn=None,
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
        )


class ASPrefixOutageFeatureResource(Resource):
    """
    获取某个AS的前缀中断时序特征
    Endpoint: /api/v1/features/outages/as-prefix
    """

    def get(self):
        return get_prefix_outage_feature(
            country=None,
            asn=request.args.get('asn'),
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
        )


class GlobalASOutageFeatureResource(Resource):
    """
    获取全局 AS 中断时序特征
    Endpoint: /api/v1/features/outages/global-as
    """

    def get(self):
        return get_as_outage_feature(
            country=None,
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
        )


class GlobalPrefixOutageFeatureResource(Resource):
    """
    获取全局前缀中断时序特征
    Endpoint: /api/v1/features/outages/global-prefix
    """

    def get(self):
        return get_prefix_outage_feature(
            country=None,
            asn=None,
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
        )
