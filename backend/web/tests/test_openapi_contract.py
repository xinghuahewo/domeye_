import json
import re
from pathlib import Path


def _openapi_path(flask_path):
    without_prefix = (
        flask_path.removeprefix('/api/v1')
        if flask_path.startswith('/api/v1/')
        else flask_path
    )
    return re.sub(r'<(?:[^:>]+:)?([^>]+)>', r'{\1}', without_prefix)


def test_openapi_paths_match_runtime_routes(app):
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    runtime_paths = {
        _openapi_path(str(rule))
        for rule in app.url_map.iter_rules()
        if str(rule).startswith(('/api/v1/', '/api/v2/'))
    }

    assert set(contract['paths']) == runtime_paths


def test_openapi_only_describes_read_only_get_operations():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    for path, path_item in contract['paths'].items():
        assert set(path_item) <= {'get', 'servers'}, path
        assert 'get' in path_item, path


def test_openapi_event_count_matches_existing_http_contract():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    assert (
        contract['components']['schemas']['EventPage']['properties']['record_count']
        == {'type': 'string'}
    )


def test_openapi_feature_list_pages_match_nested_runtime_contracts():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    countries = contract['paths']['/features/countries']['get']['responses']['200'][
        'content'
    ]['application/json']['schema']
    ases = contract['paths']['/features/ases']['get']['responses']['200']['content'][
        'application/json'
    ]['schema']
    assert countries == {'$ref': '#/components/schemas/CountryFeaturePage'}
    assert ases == {'$ref': '#/components/schemas/AsFeaturePage'}

    schemas = contract['components']['schemas']
    assert schemas['CountryFeaturePage']['properties']['data']['items'] == {
        '$ref': '#/components/schemas/CountryFeatureItem',
    }
    assert schemas['AsFeaturePage']['properties']['data']['items'] == {
        '$ref': '#/components/schemas/AsFeatureItem',
    }
    assert schemas['CountryFeatureItem']['required'] == ['country', 'time_series_data']
    assert schemas['AsFeatureItem']['required'] == [
        'asn', 'as_name', 'country', 'org_name', 'time_series_data',
    ]


def test_openapi_requires_legacy_event_semantic_guardrails():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    schemas = contract['components']['schemas']
    guardrail_ref = {
        '$ref': '#/components/schemas/LegacyEventSemanticGuardrails',
    }

    assert 'semantic_guardrails' in schemas['EventItem']['required']
    assert schemas['EventItem']['properties']['semantic_guardrails'] == guardrail_ref
    assert 'semantic_guardrails' in schemas['EventDetail']['required']
    assert schemas['EventDetail']['properties']['semantic_guardrails'] == guardrail_ref
    assert 'semantic_guardrails' in schemas['EvidenceBundle']['required']
    assert schemas['EvidenceBundle']['properties']['semantic_guardrails'] == guardrail_ref
