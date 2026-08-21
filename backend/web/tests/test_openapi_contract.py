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


def test_openapi_only_allows_explicit_agent_state_machine_post_operations():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    expected_posts = {
        '/api/v2/country-outage/chat/conversations',
        '/api/v2/country-outage/chat/conversations/{conversation_id}/turns',
        (
            '/api/v2/country-outage/chat/conversations/{conversation_id}/turns/'
            '{turn_id}/cancel'
        ),
    }
    actual_posts = set()
    for path, path_item in contract['paths'].items():
        assert set(path_item) <= {'get', 'post', 'servers'}, path
        operations = set(path_item) & {'get', 'post'}
        assert len(operations) == 1, path
        if 'post' in operations:
            actual_posts.add(path)
    assert actual_posts == expected_posts


def test_openapi_interactive_chat_exposes_only_the_public_answer_projection():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    schemas = contract['components']['schemas']

    conversation = schemas['CountryOutageInteractiveConversation']
    assert conversation['properties']['schema_version']['const'] == (
        'domeye_interactive_agent_conversation_v2'
    )
    assert set(conversation['properties']) == {
        'schema_version',
        'conversation_id',
        'binding',
        'turns',
        'expires_at',
        'created_at',
    }

    answer = schemas['CountryOutageInteractiveSuccessfulTurnAnswer']
    assert answer['additionalProperties'] is False
    assert set(answer['properties']) == {
        'schema_version',
        'answerability',
        'answer_source',
        'answer_text',
        'basis',
    }
    assert answer['properties']['schema_version']['const'] == (
        'domeye_interactive_agent_turn_answer_v2'
    )
    assert answer['properties']['answer_text']['maxLength'] == 360

    basis = schemas['CountryOutageInteractiveAnswerBasis']
    assert basis['additionalProperties'] is False
    assert set(basis['required']) == {
        'source_label_zh',
        'observed_object_zh',
        'window_start_utc',
        'window_end_utc',
        'important_boundary_zh',
    }

    for name, answerability in (
        ('CountryOutageInteractiveClarificationTurnAnswer',
         'clarification_required'),
        ('CountryOutageInteractiveStoppedTurnAnswer', 'stopped'),
    ):
        non_success = schemas[name]
        assert set(non_success['properties']) == {
            'schema_version',
            'answerability',
            'answer_source',
            'answer_text',
        }
        assert non_success['properties']['answerability']['const'] == (
            answerability
        )
        assert non_success['properties']['answer_source']['const'] == 'none'
        assert non_success['properties']['answer_text']['maxLength'] == 140

    turns = schemas['CountryOutageInteractiveTurn']['oneOf']
    assert len(turns) == 6
    assert {
        branch['properties']['state']['const'] for branch in turns
    } == {
        'executing',
        'completed',
        'clarification_required',
        'stopped',
        'failed',
        'cancelled',
    }
    assert all(branch['additionalProperties'] is False for branch in turns)

    turn_error = schemas['CountryOutageInteractiveTurnError']
    assert turn_error['discriminator'] == {'propertyName': 'code'}
    assert len(turn_error['oneOf']) == 3
    assert all(
        branch['additionalProperties'] is False
        and branch['required'] == ['code', 'message', 'retryable']
        for branch in turn_error['oneOf']
    )
    assert {
        (
            branch['properties']['code']['const'],
            branch['properties']['message']['const'],
            branch['properties']['retryable']['const'],
        )
        for branch in turn_error['oneOf']
    } == {
        (
            'answer_temporarily_unavailable',
            '这次没有形成可靠答案，临时服务异常。请稍后重试。',
            True,
        ),
        (
            'answer_not_published',
            '本轮未通过回答合同或安全校验，没有发布答案。',
            False,
        ),
        ('cancelled', '本轮已取消，未发布答案', False),
    }

    retired_chat_schemas = {
        'CountryOutageInteractiveDataIdentity',
        'CountryOutageInteractiveFinding',
        'CountryOutageInteractiveEvidence',
        'CountryOutageInteractiveTrace',
        'CountryOutageInteractiveSuccessfulTrace',
        'CountryOutageInteractiveUsage',
        'CountryOutageInteractiveUsageAttempt',
    }
    assert retired_chat_schemas.isdisjoint(schemas)

    public_chat_schema = json.dumps(
        {
            name: value
            for name, value in schemas.items()
            if name.startswith('CountryOutageInteractive')
        },
        ensure_ascii=False,
    ).lower()
    for forbidden in (
        'candidate_id',
        'identity_receipt_id',
        'finding_id',
        'receipt_refs',
        'artifact_refs',
        'evidence_refs',
        'response_guard',
        'provider_usage',
        'model_api_attempts',
    ):
        assert forbidden not in public_chat_schema

    assert not any('/chat/internal/' in path for path in contract['paths'])


def test_openapi_country_outage_general_read_model_is_bounded_and_versioned():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    schemas = contract['components']['schemas']
    paths = contract['paths']

    expected_variants = {
        '/api/v2/events/resolve': 'CountryOutageGeneralResolutionV1',
        '/api/v2/country-outages/{incident_id}/overview':
            'CountryOutageGeneralOverviewV1',
        '/api/v2/country-outages/{incident_id}/series':
            'CountryOutageGeneralSeriesV1',
        '/api/v2/country-outages/{incident_id}/asns':
            'CountryOutageGeneralAffectedAsPageV1',
        '/api/v2/country-outages/{incident_id}/audit':
            'CountryOutageGeneralAuditV1',
    }
    for path, schema_name in expected_variants.items():
        response_schema = paths[path]['get']['responses']['200'][
            'content'
        ]['application/json']['schema']
        assert {'$ref': f'#/components/schemas/{schema_name}'} in response_schema[
            'oneOf'
        ]
        assert schemas[schema_name]['additionalProperties'] is False

    downstream_path = paths[
        '/api/v2/country-outages/{incident_id}/path-downstreams'
    ]['get']
    assert set(downstream_path['responses']) == {'200', '400', '404', '503'}
    parameters = {item['name']: item for item in downstream_path['parameters']}
    assert parameters['page_size']['schema']['maximum'] == 60
    assert parameters['scope']['schema']['enum'] == ['all', 'concurrent']
    page_schema = schemas['CountryOutageGeneralPathDownstreamPageV1']
    assert page_schema['properties']['items']['maxItems'] == 60
    path_item = schemas['CountryOutageGeneralPathDownstreamItemV1']
    assert path_item['properties']['path_samples']['maxItems'] == 3
    assert path_item['properties']['relationship_semantics']['const'].endswith(
        'not_dependency_or_cause'
    )
    assert schemas['CountryOutageGeneralCapabilitiesV1']['properties'][
        'full_path_evidence'
    ] == {'const': 'audit_only'}


def test_openapi_legacy_country_outage_agent_paths_are_retired():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    paths = contract['paths']
    retired_fragments = (
        '/api/v2/country-outage/reports',
        '/api/v2/country-outage/runs/',
        '/api/v2/country-outage/capabilities/external-evidence',
        '/api/v2/country-outage/investigations',
    )
    assert not any(
        path.startswith(retired_fragments)
        for path in paths
    )
    assert '/api/v2/country-outage/chat/conversations' in paths

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


def test_openapi_w5_public_contract_is_retired():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    assert not any(
        path.startswith('/api/v2/country-outage/investigations')
        for path in contract['paths']
    )
