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
        '/api/v2/country-outage/reports',
        '/api/v2/country-outage/reports/{report_id}/questions',
        '/api/v2/country-outage/runs/{run_id}/abort',
        '/api/v2/country-outage/chat/conversations',
        '/api/v2/country-outage/chat/conversations/{conversation_id}/turns',
        (
            '/api/v2/country-outage/chat/conversations/{conversation_id}/turns/'
            '{turn_id}/cancel'
        ),
        '/api/v2/country-outage/investigations',
        '/api/v2/country-outage/investigations/{investigation_id}/start',
        '/api/v2/country-outage/investigations/{investigation_id}/cancel',
        (
            '/api/v2/country-outage/investigations/{investigation_id}/nodes/'
            '{node_id}/cancel'
        ),
        (
            '/api/v2/country-outage/investigations/{investigation_id}/nodes/'
            '{node_id}/reruns'
        ),
        '/api/v2/country-outage/investigations/{investigation_id}/turns',
        '/api/v2/country-outage/investigations/{investigation_id}/exports',
    }
    actual_posts = set()
    for path, path_item in contract['paths'].items():
        assert set(path_item) <= {'get', 'post', 'servers'}, path
        operations = set(path_item) & {'get', 'post'}
        assert len(operations) == 1, path
        if 'post' in operations:
            actual_posts.add(path)
    assert actual_posts == expected_posts


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


def test_openapi_country_outage_agent_contract_is_narrow_and_exact():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    schemas = contract['components']['schemas']
    paths = contract['paths']

    idempotency_key = schemas['CountryOutageAgentIdempotencyKey']
    assert idempotency_key == {
        'type': 'string',
        'minLength': 8,
        'maxLength': 128,
        'pattern': '^[A-Za-z0-9._:-]{8,128}$',
    }

    report_phase = schemas['CountryOutageAgentReportPhase']['enum']
    assert 'generating_report' in report_phase
    assert 'generating' not in report_phase
    assert 'rendering' not in report_phase
    assert schemas['CountryOutageAgentQuestionPhase']['enum'] == [
        'answering',
        'collecting_external',
        'completed',
        'failed',
        'cancelled',
    ]
    assert schemas['CountryOutageAgentSessionNoticeEvent']['properties']['phase'][
        'enum'
    ] == ['session_expiring', 'session_expired']

    quote = schemas['CountryOutageAgentQuestionQuote']
    assert quote['discriminator']['propertyName'] == 'kind'
    quote_variants = {
        item['$ref'].rsplit('/', 1)[-1]
        for item in quote['oneOf']
    }
    assert quote_variants == {
        'CountryOutageAgentQuoteSummary',
        'CountryOutageAgentQuoteHighlight',
        'CountryOutageAgentQuoteSectionParagraph',
    }
    for name in quote_variants:
        assert schemas[name]['additionalProperties'] is False
    assert schemas['CountryOutageAgentQuoteHighlight']['required'] == [
        'kind', 'highlight_index',
    ]
    assert schemas['CountryOutageAgentQuoteSectionParagraph']['required'] == [
        'kind', 'section_id', 'paragraph_index',
    ]

    question_request = schemas['CountryOutageAgentCreateQuestionRequest']
    assert question_request['discriminator']['propertyName'] == 'evidence_mode'
    assert {
        item['$ref'].rsplit('/', 1)[-1]
        for item in question_request['oneOf']
    } == {
        'CountryOutageAgentDomeyeOnlyQuestionRequest',
        'CountryOutageAgentExternalQuestionRequest',
    }
    external_request = schemas['CountryOutageAgentExternalQuestionRequest']
    assert external_request['additionalProperties'] is False
    assert external_request['required'] == [
        'question',
        'evidence_mode',
        'external_authorization',
        'external_urls',
        'idempotency_key',
    ]
    assert external_request['properties']['external_urls']['minItems'] == 1
    assert external_request['properties']['external_urls']['maxItems'] == 5
    authorization = schemas['CountryOutageAgentExternalAuthorization']
    assert authorization['additionalProperties'] is False
    assert authorization['properties']['authorized'] == {
        'type': 'boolean',
        'const': True,
    }
    appendix = schemas['CountryOutageAgentExternalAppendix']
    assert appendix['additionalProperties'] is False
    assert appendix['properties']['status']['enum'] == [
        'collecting', 'completed', 'partial', 'failed',
    ]
    assert appendix['properties']['sources']['maxItems'] == 5
    assert appendix['properties']['classification_policy_version'] == {
        'type': 'string',
        'const':
            'country_outage_external_source_classification_policy_v1',
    }
    external_source = schemas['CountryOutageAgentExternalSource']
    assert external_source['additionalProperties'] is False
    assert external_source['properties']['source_classification'] == {
        'type': 'string',
        'enum': ['measurement_platform', 'unknown'],
    }
    frozen_binding = schemas['CountryOutageAgentExternalFrozenBinding']
    assert frozen_binding['additionalProperties'] is False
    assert set(frozen_binding['required']) >= {
        'fact_set_id',
        'cohort_id',
    }
    assert frozen_binding['properties']['fact_set_id']['minLength'] == 1
    assert frozen_binding['properties']['cohort_id']['minLength'] == 1

    report_document = schemas['CountryOutageAgentReportDocument']
    assert report_document['properties']['projectKnowledgeVersion'] == {
        'type': 'string',
        'const': 'country_outage_report_skill_v6',
    }
    assert 'validatorRulesVersion' in report_document['required']
    assert report_document['properties']['validatorRulesVersion'] == {
        'type': 'string',
    }
    assert 'skillBundleSha256' in report_document['required']
    assert report_document['properties']['skillBundleSha256'] == {
        'type': 'string',
        'pattern': '^[a-f0-9]{64}$',
    }
    assert 'unknown' in schemas['CountryOutageCapability']['properties'][
        'state'
    ]['enum']
    external_capability = schemas[
        'CountryOutageExternalEvidenceCapability'
    ]
    assert len(external_capability['oneOf']) == 3
    ready_capability = external_capability['oneOf'][0]
    not_configured_capability = external_capability['oneOf'][1]
    self_check_failed_capability = external_capability['oneOf'][2]
    assert ready_capability['properties']['state'] == {
        'type': 'string',
        'const': 'ready',
    }
    assert ready_capability['properties']['provider'] == {
        'type': 'string',
        'const': 'managed-egress-v1',
    }
    assert not_configured_capability['properties']['state'] == {
        'type': 'string',
        'const': 'not_configured',
    }
    assert not_configured_capability['properties']['provider'] == {
        'type': 'string',
        'const': 'disabled',
    }
    assert self_check_failed_capability['properties']['state'] == {
        'type': 'string',
        'const': 'self_check_failed',
    }
    assert self_check_failed_capability['properties']['provider'] == {
        'type': 'string',
        'const': 'managed-egress-v1',
    }
    assert schemas['CountryOutageExternalEvidencePolicy']['properties'][
        'maximum_urls'
    ]['maximum'] == 5

    events_response = paths[
        '/api/v2/country-outage/reports/{report_id}/events'
    ]['get']['responses']['200']
    assert events_response['content']['text/event-stream']['schema'] == {
        '$ref': '#/components/schemas/CountryOutageAgentEvent',
    }
    assert {
        item['$ref'].rsplit('/', 1)[-1]
        for item in schemas['CountryOutageAgentEvent']['oneOf']
    } == {
        'CountryOutageAgentReportStateEvent',
        'CountryOutageAgentQuestionStateEvent',
        'CountryOutageAgentSessionNoticeEvent',
    }

    artifact_response = paths[
        '/api/v2/country-outage/reports/{report_id}/artifacts/{artifact_format}'
    ]['get']['responses']
    assert set(artifact_response['200']['headers']) >= {
        'Content-Disposition',
        'X-Artifact-Id',
        'X-Content-SHA256',
    }
    assert artifact_response['200']['headers']['X-Content-SHA256']['schema'] == {
        'type': 'string',
        'pattern': '^[0-9a-f]{64}$',
    }
    assert '409' in artifact_response

    appendix_artifact_response = paths[
        (
            '/api/v2/country-outage/reports/{report_id}/questions/'
            '{question_id}/artifacts/external-appendix'
        )
    ]['get']['responses']
    assert set(appendix_artifact_response['200']['content']) == {
        'text/markdown',
    }
    appendix_path_parameters = paths[
        (
            '/api/v2/country-outage/reports/{report_id}/questions/'
            '{question_id}/artifacts/external-appendix'
        )
    ]['get']['parameters']
    assert next(
        item for item in appendix_path_parameters
        if item['name'] == 'question_id'
    )['schema']['pattern'] == '^q_[A-Za-z0-9_-]{1,124}$'
    assert set(appendix_artifact_response['200']['headers']) >= {
        'Cache-Control',
        'Content-Disposition',
        'X-Artifact-Id',
        'X-Content-SHA256',
        'X-Content-Type-Options',
    }
    assert appendix_artifact_response['200']['headers'][
        'Cache-Control'
    ]['schema'] == {
        'type': 'string',
        'const': 'private, no-store',
    }
    assert appendix_artifact_response['200']['headers'][
        'X-Content-Type-Options'
    ]['schema'] == {
        'type': 'string',
        'const': 'nosniff',
    }
    assert appendix_artifact_response['200']['headers'][
        'X-Artifact-Id'
    ]['schema'] == {
        'type': 'string',
        'pattern': '^external_appendix_[0-9a-f]{32}$',
    }
    assert '409' in appendix_artifact_response

    expected_statuses = {
        '/api/v2/country-outage/capabilities/external-evidence': {
            '200', '401', '503',
        },
        '/api/v2/country-outage/reports': {
            '200', '202', '400', '401', '403', '409', '410', '429', '503',
        },
        '/api/v2/country-outage/reports/{report_id}/events': {
            '200', '400', '401', '403', '404', '410', '503',
        },
        '/api/v2/country-outage/reports/{report_id}/questions': {
            '200', '202', '400', '401', '403', '404', '409', '410', '429', '503',
        },
        '/api/v2/country-outage/runs/{run_id}/abort': {
            '200', '400', '401', '403', '404', '410', '503',
        },
        '/api/v2/country-outage/reports/{report_id}/artifacts/{artifact_format}': {
            '200', '400', '401', '403', '404', '409', '410', '503',
        },
        (
            '/api/v2/country-outage/reports/{report_id}/questions/'
            '{question_id}/artifacts/external-appendix'
        ): {
            '200', '400', '401', '403', '404', '409', '410', '503',
        },
    }
    for path, statuses in expected_statuses.items():
        operation = next(
            value for method, value in paths[path].items()
            if method in {'get', 'post'}
        )
        assert set(operation['responses']) == statuses


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


def test_openapi_w5_turn_answer_is_closed_versioned_and_fixture_bounded():
    project_root = Path(__file__).resolve().parents[3]
    contract = json.loads(
        (project_root / 'contracts' / 'openapi.json').read_text(encoding='utf-8')
    )
    path = (
        '/api/v2/country-outage/investigations/{investigation_id}/turns/'
        '{turn_id}/revisions/{turn_revision}'
    )
    assert contract['paths'][path]['get']['responses']['200']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/CountryOutageInvestigationTurnEnvelope'
    }
    schemas = contract['components']['schemas']
    for name in (
        'CountryOutageInvestigationTurnRef',
        'CountryOutageInvestigationAnswerClaim',
        'CountryOutageInvestigationAnswer',
        'CountryOutageInvestigationTurn',
        'CountryOutageInvestigationTurnEnvelope',
        'CountryOutageInvestigationTurnActionResponse',
    ):
        assert schemas[name]['additionalProperties'] is False
    boundary = schemas['CountryOutageInvestigationAnswer']['properties']['fixture_boundary']
    assert boundary['additionalProperties'] is False
    assert boundary['properties']['external_provider_called']['const'] is False
    assert boundary['properties']['fixture_replay_only']['const'] is True
    assert boundary['properties']['runtime_integrated']['const'] is True
    assert boundary['properties']['production_deployed']['const'] is False
    assert 'turn_refs' in schemas['CountryOutageInvestigation']['required']
    claim = schemas['CountryOutageInvestigationAnswerClaim']
    assert set(claim['required']) == {
        'claim_id', 'claim_kind', 'claim_relation', 'text', 'fact_ids',
        'source_node_ids', 'source_value_digests', 'evidence_refs',
        'boundary_assertion_ids', 'verification_requirements',
    }
    assert 'gate' in schemas['CountryOutageInvestigationReceipt']['properties']['receipt_kind']['enum']
