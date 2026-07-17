import io
import json
import os
import sys
import tempfile
import types
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage


current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(current_dir, '..', '..'))
sys.path.append(backend_dir)

from web.tests._fake_database import install_fake_database_module

install_fake_database_module()


def install_fake_outage_topo_module():
    fake_outage_topo_module = types.ModuleType('core.visualize.outage_topo')
    fake_outage_topo_module.generate_graph = lambda *args, **kwargs: {}
    fake_outage_topo_module.graph_to_dict = lambda *args, **kwargs: {}
    fake_outage_topo_module.build_country_topo_dict = lambda *args, **kwargs: {}
    sys.modules['core.visualize.outage_topo'] = fake_outage_topo_module


install_fake_outage_topo_module()


def install_fake_reports_module():
    from flask_restful import Resource

    fake_reports_package = types.ModuleType('web.api.reports')
    fake_reports_api_module = types.ModuleType('web.api.reports.api')

    for resource_name in [
        'WordExportResource',
        'ExcelExportResource',
        'ExcelExportCountryResource',
        'TemplateExportResource',
        'FileDownloadResource',
        'CountryOutageReportDataResource',
        'CountryOutageReportExportResource',
    ]:
        setattr(fake_reports_api_module, resource_name, type(resource_name, (Resource,), {}))

    fake_reports_package.api = fake_reports_api_module
    sys.modules['web.api.reports'] = fake_reports_package
    sys.modules['web.api.reports.api'] = fake_reports_api_module


install_fake_reports_module()

from run import create_app
from services import data_query_service


@pytest.fixture(scope='module')
def app():
    os.environ['FLASK_CONFIG'] = 'testing'
    app = create_app()
    del os.environ['FLASK_CONFIG']
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def build_schema_catalog():
    return {
        'as_info': {
            'tables': ['as_info'],
            'fields': ['asn', 'as_country_cn', 'org_name_cn'],
            'data_types': {'asn': 'text', 'as_country_cn': 'text', 'org_name_cn': 'text'},
        },
        'event_table': {'tables': [], 'fields': [], 'data_types': {}},
        'as_outage': {
            'tables': ['as_outage_202603'],
            'fields': ['asn', 'source', 's_time', 'outage_level'],
            'data_types': {'outage_level': 'character varying'},
        },
        'prefix_outage': {
            'tables': ['prefix_outage_202603'],
            'fields': ['prefix', 'source', 's_time', 'event_info'],
            'data_types': {'event_info': 'text'},
        },
        'country_outage': {
            'tables': ['country_outage_202603'],
            'fields': ['country', 'source', 's_time', 'max_outage_as_num'],
            'data_types': {'max_outage_as_num': 'integer'},
        },
        'feature_asn': {
            'tables': ['feature_other_202603'],
            'fields': ['t', 'source', 'asn', 'country', 'announ_num'],
            'data_types': {'announ_num': 'bigint'},
        },
        'feature_country': {
            'tables': ['feature_country'],
            'fields': ['t', 'source', 'country', 'withdraw_num'],
            'data_types': {'withdraw_num': 'bigint'},
        },
    }


class TestDataQueryAPI:
    def _prepare_export_task(self, temp_dir, task_id='task-export-name', file_name='query.csv'):
        os.makedirs(os.path.join(temp_dir, task_id), exist_ok=True)
        data_query_service._write_json(
            os.path.join(temp_dir, task_id, data_query_service.TASK_METADATA_FILE),
            {
                'task_id': task_id,
                'task_version': data_query_service.DATA_QUERY_TASK_VERSION,
                'task_name': '导出命名任务',
                'file_name': file_name,
                'stored_file_name': 'upload_source.csv',
                'status': 'parsed',
                'created_at': '2026-03-19 00:00:00',
                'generated_at': '',
                'row_count': 1,
                'original_columns': ['ASN'],
                'recognized_fields': ['asn'],
                'upload_columns': [{'original_name': 'ASN', 'standard_field': 'asn'}],
                'preview_rows': [{'ASN': '13335'}],
                'matched_database_fields': [
                    {
                        'field_id': 'canonical.country',
                        'field_name': 'country',
                        'table_family': '',
                        'table_label': '标准字段补全',
                        'label': '国家',
                        'source_kind': 'canonical',
                    },
                    {
                        'field_id': 'canonical.org_name',
                        'field_name': 'org_name',
                        'table_family': '',
                        'table_label': '标准字段补全',
                        'label': '机构名称',
                        'source_kind': 'canonical',
                    },
                ],
                'selected_field_ids': [],
            },
        )
        data_query_service._write_json(
            os.path.join(temp_dir, task_id, data_query_service.TASK_RECORDS_FILE),
            [{'ASN': '13335'}],
        )
        return task_id

    @patch('web.api.data_query.api.list_data_query_tasks', return_value={'status': True, 'data': [{'task_id': 'task-1'}]})
    def test_task_list_api(self, mock_service, client):
        response = client.get('/api/v1/data-query/tasks')

        assert response.status_code == 200
        assert json.loads(response.data) == {'status': True, 'data': [{'task_id': 'task-1'}]}
        mock_service.assert_called_once_with()

    @patch('web.api.data_query.api.delete_data_query_task', return_value={'status': True, 'msg': '任务删除成功'})
    def test_task_delete_api(self, mock_service, client):
        response = client.delete('/api/v1/data-query/tasks/task-1')

        assert response.status_code == 200
        assert json.loads(response.data) == {'status': True, 'msg': '任务删除成功'}
        mock_service.assert_called_once_with(task_id='task-1')

    @patch(
        'web.api.data_query.api.get_data_query_preview',
        return_value={'status': True, 'data': {'preview_columns': ['ASN'], 'preview_rows': [{'ASN': '12345'}]}},
    )
    def test_task_preview_api(self, mock_service, client):
        response = client.post('/api/v1/data-query/tasks/task-1/preview', json={'selected_field_ids': ['as_info.as_country_cn']})

        assert response.status_code == 200
        assert json.loads(response.data) == {
            'status': True,
            'data': {'preview_columns': ['ASN'], 'preview_rows': [{'ASN': '12345'}]},
        }
        mock_service.assert_called_once_with(
            task_id='task-1',
            selected_field_ids=['as_info.as_country_cn'],
        )

    def test_create_data_query_task_detects_fields_and_candidates(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

            file_storage = FileStorage(
                stream=io.BytesIO(
                    (
                        'ASN,prefix,country,source,s_time\n'
                        '12345,1.1.1.0/24,中国,r,2026-03-16 00:00:00\n'
                    ).encode('utf-8')
                ),
                filename='query.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            task = result['data']
            assert task['recognized_fields'] == ['asn', 'prefix', 'country', 'source', 's_time']
            field_ids = {item['field_id'] for item in task['matched_database_fields']}
            assert field_ids == {'canonical.as_name', 'canonical.org_name'}

    def test_create_data_query_task_accepts_unicode_filename_csv(self, monkeypatch):
        """Unicode-only basename must not break extension detection (secure_filename strips CJK)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

            file_storage = FileStorage(
                stream=io.BytesIO(
                    (
                        'ASN,prefix,country,source,s_time\n'
                        '12345,1.1.1.0/24,中国,r,2026-03-16 00:00:00\n'
                    ).encode('utf-8')
                ),
                filename='伊朗源地址情况.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            task = result['data']
            assert task['file_name'].endswith('.csv')
            assert task['recognized_fields'] == ['asn', 'prefix', 'country', 'source', 's_time']

    def test_list_data_query_tasks_returns_checkable_field_summaries(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

            file_storage = FileStorage(
                stream=io.BytesIO(
                    (
                        'ASN,prefix,country,source,s_time\n'
                        '12345,1.1.1.0/24,中国,r,2026-03-16 00:00:00\n'
                    ).encode('utf-8')
                ),
                filename='query.csv',
                content_type='text/csv',
            )

            created = data_query_service.create_data_query_task(file_storage=file_storage)

            assert created['status'] is True

            result = data_query_service.list_data_query_tasks()

            assert result['status'] is True
            assert len(result['data']) == 1
            summary = result['data'][0]
            assert summary['matched_field_count'] == len(summary['matched_database_fields'])
            assert summary['matched_database_fields']
            first_field = summary['matched_database_fields'][0]
            assert {'field_id', 'field_name', 'label', 'table_label', 'sample_hit_count', 'sample_total_count'} <= set(first_field)

    def test_create_data_query_task_offers_country_feature_when_asn_can_derive_country(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

            file_storage = FileStorage(
                stream=io.BytesIO(
                    (
                        'ASN,s_time\n'
                        '12345,2026-03-16 00:00:00\n'
                    ).encode('utf-8')
                ),
                filename='query.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            matched_fields = {item['field_id']: item for item in result['data']['matched_database_fields']}
            assert {'canonical.country', 'canonical.as_name', 'canonical.org_name'} <= set(matched_fields)
            assert matched_fields['canonical.country']['resolution_hint'] == 'ASN -> AS基础信息 -> 国家'
            assert matched_fields['canonical.country']['resolution_entry_fields'] == ['asn']

    def test_create_data_query_task_recognizes_ip_alias(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            monkeypatch.setattr(data_query_service, '_PREFIX_NETWORK_CACHE', None)

            file_storage = FileStorage(
                stream=io.BytesIO(
                    (
                        'ip来源,source,s_time\n'
                        '1.1.1.1,r,2026-03-16 00:00:00\n'
                    ).encode('utf-8')
                ),
                filename='query.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            task = result['data']
            assert task['recognized_fields'] == ['ip', 'source', 's_time']
            assert task['upload_columns'][0]['original_name'] == 'ip来源'
            assert task['upload_columns'][0]['standard_field'] == 'ip'
            field_ids = {item['field_id'] for item in task['matched_database_fields']}
            assert field_ids == {
                'canonical.prefix',
                'canonical.asn',
                'canonical.country',
                'canonical.as_name',
                'canonical.org_name',
            }

    def test_create_data_query_task_recognizes_ip_variant_column_name(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            monkeypatch.setattr(data_query_service, '_PREFIX_NETWORK_CACHE', None)

            file_storage = FileStorage(
                stream=io.BytesIO(
                    (
                        'ip来源地,数据来源,开始日期\n'
                        '1.1.1.1,r,2026-03-16 00:00:00\n'
                    ).encode('utf-8')
                ),
                filename='query.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            task = result['data']
            assert task['recognized_fields'] == ['ip', 'source', 's_time']
            assert task['upload_columns'][0]['standard_field'] == 'ip'
            assert task['upload_columns'][1]['standard_field'] == 'source'
            assert task['upload_columns'][2]['standard_field'] == 's_time'

    def test_create_data_query_task_inferrs_ip_from_sample_values(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            monkeypatch.setattr(data_query_service, '_PREFIX_NETWORK_CACHE', None)

            file_storage = FileStorage(
                stream=io.BytesIO(
                    (
                        '访问地址,结束时间\n'
                        '8.8.8.8,2026-03-16 00:00:00\n'
                        '1.1.1.1,2026-03-16 01:00:00\n'
                    ).encode('utf-8')
                ),
                filename='query.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            task = result['data']
            assert task['recognized_fields'] == ['ip', 'e_time']
            assert task['upload_columns'][0]['standard_field'] == 'ip'
            assert task['upload_columns'][0]['match_method'] in ['keyword_and_sample', 'sample_inference']
            assert task['upload_columns'][1]['standard_field'] == 'e_time'

    def test_build_canonical_row_derives_prefix_and_asn_from_ip(self, monkeypatch):
        monkeypatch.setattr(
            data_query_service,
            'prefix_info',
            {
                '1.1.1.0/24': {'bgp': '13335', 'route': '13335', 'country': 'US', 'source': 'ripe'},
                '1.1.0.0/16': {'bgp': '64512', 'route': '64512', 'country': 'US', 'source': 'ripe'},
            },
        )
        monkeypatch.setattr(data_query_service, '_PREFIX_NETWORK_CACHE', None)

        canonical_row = data_query_service._build_canonical_row(
            {'ip来源': '1.1.1.1', 'source': 'r', 's_time': '2026-03-16 00:00:00'},
            [
                {'original_name': 'ip来源', 'standard_field': 'ip'},
                {'original_name': 'source', 'standard_field': 'source'},
                {'original_name': 's_time', 'standard_field': 's_time'},
            ],
        )

        assert canonical_row['ip'] == '1.1.1.1'
        assert canonical_row['prefix'] == '1.1.1.0/24'
        assert canonical_row['asn'] == '13335'
        assert canonical_row['country'] == 'US'
        assert canonical_row['source'] == 'r'

    def test_build_canonical_row_derives_prefix_and_asn_from_ip_using_pfx2as_fallback(self, monkeypatch):
        monkeypatch.setattr(data_query_service, 'prefix_info', {})
        monkeypatch.setattr(data_query_service, '_PREFIX_NETWORK_CACHE', None)
        monkeypatch.setattr(
            data_query_service,
            '_PFX2AS_PREFIX_CACHE',
            {
                '77.51.0.0/16': {'asn': '64501'},
                '77.51.56.0/24': {'asn': '64500'},
            },
        )
        monkeypatch.setattr(data_query_service, '_PFX2AS_NETWORK_CACHE', None)

        canonical_row = data_query_service._build_canonical_row(
            {'源IP地址': '77.51.56.86'},
            [{'original_name': '源IP地址', 'standard_field': 'ip'}],
        )

        assert canonical_row['ip'] == '77.51.56.86'
        assert canonical_row['prefix'] == '77.51.56.0/24'
        assert canonical_row['asn'] == '64500'

    def test_resolve_row_for_family_prefers_local_info_file_as_info(self, monkeypatch):
        monkeypatch.setattr(
            data_query_service,
            'as_info',
            {
                '13335': {
                    'as_country_cn': '美国',
                    'as_name': 'Cloudflare',
                    'org_name_cn': '云耀网络',
                    'org_name': 'Cloudflare, Inc.',
                }
            },
        )
        monkeypatch.setattr(
            data_query_service,
            '_query_best_row_from_table',
            lambda *args, **kwargs: {
                'asn': '13335',
                'as_country_cn': '',
                'as_name': 'DB Cloudflare',
                'org_name_cn': '',
            },
        )

        row = data_query_service._resolve_row_for_family('as_info', {'asn': '13335'})

        assert row['asn'] == '13335'
        assert row['as_country_cn'] == '美国'
        assert row['as_name'] == 'Cloudflare'
        assert row['org_name_cn'] == '云耀网络'

    def test_build_canonical_row_normalizes_country_and_source_values(self):
        canonical_row = data_query_service._build_canonical_row(
            {'country_name': 'Russia', 'data_source': 'route-views'},
            [
                {'original_name': 'country_name', 'standard_field': 'country'},
                {'original_name': 'data_source', 'standard_field': 'source'},
            ],
        )

        assert canonical_row['country'] == 'RU'
        assert canonical_row['source'] == 'r'

    def test_create_data_query_task_records_sample_hit_counts(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            monkeypatch.setattr(
                data_query_service,
                '_resolve_row_for_family',
                lambda family_name, canonical_row, **kwargs: {'as_country_cn': '俄罗斯'} if family_name == 'as_info' else {},
            )

            file_storage = FileStorage(
                stream=io.BytesIO(('ASN\n12345\n').encode('utf-8')),
                filename='query.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            matched_fields = {item['field_id']: item for item in result['data']['matched_database_fields']}
            assert matched_fields['canonical.country']['sample_hit_count'] == 1
            assert matched_fields['canonical.country']['sample_value_examples'] == ['RU']
            assert matched_fields['canonical.country']['sample_trace_examples'] == ['ASN -> AS基础信息 -> 国家']

    def test_create_data_query_task_uses_local_info_files_for_ip_chain_supplement(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            monkeypatch.setattr(
                data_query_service,
                'prefix_info',
                {
                    '1.1.1.0/24': {
                        'bgp': '13335',
                        'route': '13335',
                        'country': '',
                        'source': 'ripe',
                    }
                },
            )
            monkeypatch.setattr(
                data_query_service,
                'as_info',
                {
                    '13335': {
                        'as_country_cn': '美国',
                        'as_name': 'Cloudflare',
                        'org_name_cn': '云耀网络',
                    }
                },
            )
            monkeypatch.setattr(data_query_service, '_PREFIX_NETWORK_CACHE', None)

            file_storage = FileStorage(
                stream=io.BytesIO(('源IP地址\n1.1.1.1\n').encode('utf-8')),
                filename='query.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            matched_fields = {item['field_id']: item for item in result['data']['matched_database_fields']}
            assert matched_fields['canonical.country']['sample_hit_count'] == 1
            assert matched_fields['canonical.country']['sample_value_examples'] == ['US']
            assert matched_fields['canonical.as_name']['sample_hit_count'] == 1
            assert matched_fields['canonical.as_name']['sample_value_examples'] == ['Cloudflare']
            assert matched_fields['canonical.org_name']['sample_hit_count'] == 1
            assert matched_fields['canonical.org_name']['sample_value_examples'] == ['云耀网络']

    def test_create_data_query_task_records_diagnostic_when_ip_chain_cannot_resolve(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            monkeypatch.setattr(data_query_service, 'prefix_info', {})
            monkeypatch.setattr(data_query_service, '_PREFIX_NETWORK_CACHE', None)
            monkeypatch.setattr(data_query_service, '_PFX2AS_PREFIX_CACHE', {})
            monkeypatch.setattr(data_query_service, '_PFX2AS_NETWORK_CACHE', None)

            file_storage = FileStorage(
                stream=io.BytesIO(('源IP地址\n77.51.56.86\n').encode('utf-8')),
                filename='query.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            matched_fields = {item['field_id']: item for item in result['data']['matched_database_fields']}
            assert matched_fields['canonical.prefix']['sample_hit_count'] == 0
            assert matched_fields['canonical.prefix']['sample_trace_examples'] == [
                'IP -> 本地前缀归属信息 / Prefix-AS映射均未命中，无法推导 Prefix'
            ]

    def test_create_data_query_task_records_trace_when_ip_uses_pfx2as_fallback(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            monkeypatch.setattr(data_query_service, 'prefix_info', {})
            monkeypatch.setattr(data_query_service, '_PREFIX_NETWORK_CACHE', None)
            monkeypatch.setattr(
                data_query_service,
                '_PFX2AS_PREFIX_CACHE',
                {'77.51.56.0/24': {'asn': '64500'}},
            )
            monkeypatch.setattr(data_query_service, '_PFX2AS_NETWORK_CACHE', None)
            monkeypatch.setattr(
                data_query_service,
                '_resolve_row_for_family',
                lambda family_name, canonical_row, **kwargs: (
                    {'as_country_cn': '俄罗斯'} if family_name == 'as_info' and canonical_row.get('asn') == '64500' else {}
                ),
            )

            file_storage = FileStorage(
                stream=io.BytesIO(('源IP地址\n77.51.56.86\n').encode('utf-8')),
                filename='query.csv',
                content_type='text/csv',
            )

            result = data_query_service.create_data_query_task(file_storage=file_storage)

            assert result['status'] is True
            matched_fields = {item['field_id']: item for item in result['data']['matched_database_fields']}
            assert matched_fields['canonical.country']['sample_hit_count'] == 1
            assert matched_fields['canonical.country']['sample_trace_examples'] == [
                'IP -> Prefix-AS映射 -> AS基础信息 -> 国家'
            ]

    def test_build_enriched_preview_supports_prefix_to_asn_to_target_chain(self, monkeypatch):
        task = {
            'original_columns': ['prefix', 'source', 's_time'],
            'upload_columns': [
                {'original_name': 'prefix', 'standard_field': 'prefix'},
                {'original_name': 'source', 'standard_field': 'source'},
                {'original_name': 's_time', 'standard_field': 's_time'},
            ],
        }

        def fake_resolve_row_for_family(family_name, canonical_row, **kwargs):
            if family_name == 'prefix_outage' and canonical_row.get('prefix') == '1.1.1.0/24':
                return {'prefix': '1.1.1.0/24', 'asn': '13335', 'country': '美国'}
            if family_name == 'as_info' and canonical_row.get('asn') == '13335':
                return {'as_country_cn': '美国'}
            if family_name == 'feature_asn' and canonical_row.get('asn') == '13335':
                return {'announ_num': 42}
            return {}

        monkeypatch.setattr(data_query_service, '_resolve_row_for_family', fake_resolve_row_for_family)
        monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

        headers, rows = data_query_service._build_enriched_preview(
            task,
            [{'prefix': '1.1.1.0/24', 'source': 'r', 's_time': '2026-03-16 00:00:00'}],
            [
                {
                    'field_id': 'feature_asn.announ_num',
                    'field_name': 'announ_num',
                    'table_family': 'feature_asn',
                    'label': 'AS时序特征.announ_num',
                }
            ],
        )

        assert headers == ['prefix', 'source', 's_time', 'AS时序特征.announ_num']
        assert rows == [
            {
                'prefix': '1.1.1.0/24',
                'source': 'r',
                's_time': '2026-03-16 00:00:00',
                'AS时序特征.announ_num': '42',
            }
        ]

    def test_build_enriched_preview_supports_asn_to_country_to_target_chain(self, monkeypatch):
        task = {
            'original_columns': ['ASN', 's_time'],
            'upload_columns': [
                {'original_name': 'ASN', 'standard_field': 'asn'},
                {'original_name': 's_time', 'standard_field': 's_time'},
            ],
        }

        def fake_resolve_row_for_family(family_name, canonical_row, **kwargs):
            if family_name == 'as_info' and canonical_row.get('asn') == '13335':
                return {'as_country_cn': '美国'}
            if family_name == 'feature_country' and canonical_row.get('country') == 'US':
                return {'withdraw_num': 17}
            return {}

        monkeypatch.setattr(data_query_service, '_resolve_row_for_family', fake_resolve_row_for_family)
        monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

        headers, rows = data_query_service._build_enriched_preview(
            task,
            [{'ASN': '13335', 's_time': '2026-03-16 00:00:00'}],
            [
                {
                    'field_id': 'feature_country.withdraw_num',
                    'field_name': 'withdraw_num',
                    'table_family': 'feature_country',
                    'label': '国家时序特征.withdraw_num',
                }
            ],
        )

        assert headers == ['ASN', 's_time', '国家时序特征.withdraw_num']
        assert rows == [
            {
                'ASN': '13335',
                's_time': '2026-03-16 00:00:00',
                '国家时序特征.withdraw_num': '17',
            }
        ]

    def test_country_query_value_keeps_db_country_queries_compatible(self):
        assert data_query_service._country_query_value('US') == '美国'
        assert data_query_service._country_query_value('usa') == '美国'
        assert data_query_service._country_query_value('中国') == '中国'

    def test_delete_data_query_task_removes_task_directory(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)

            task_id = 'task-delete'
            task_dir = os.path.join(temp_dir, task_id)
            os.makedirs(task_dir, exist_ok=True)
            data_query_service._write_json(
                os.path.join(task_dir, data_query_service.TASK_METADATA_FILE),
                {'task_id': task_id},
            )

            result = data_query_service.delete_data_query_task(task_id)

            assert result == {'status': True, 'msg': '任务删除成功'}
            assert not os.path.exists(task_dir)

    def test_generate_data_query_export_returns_csv(self, monkeypatch, app):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

            task_id = 'task-export'
            os.makedirs(os.path.join(temp_dir, task_id), exist_ok=True)
            data_query_service._write_json(
                os.path.join(temp_dir, task_id, data_query_service.TASK_METADATA_FILE),
                {
                    'task_id': task_id,
                    'task_name': '导出任务',
                    'file_name': 'query.csv',
                    'status': 'parsed',
                    'created_at': '2026-03-18 00:00:00',
                    'generated_at': '',
                    'row_count': 1,
                    'original_columns': ['ASN', 'prefix'],
                    'recognized_fields': ['asn', 'prefix'],
                    'upload_columns': [
                        {'original_name': 'ASN', 'standard_field': 'asn'},
                        {'original_name': 'prefix', 'standard_field': 'prefix'},
                    ],
                    'preview_rows': [{'ASN': '12345', 'prefix': '1.1.1.0/24'}],
                    'matched_database_fields': [
                        {
                            'field_id': 'canonical.country',
                            'field_name': 'country',
                            'table_family': '',
                            'table_label': '标准字段补全',
                            'label': 'country',
                            'source_kind': 'canonical',
                        }
                    ],
                    'selected_field_ids': [],
                },
            )
            data_query_service._write_json(
                os.path.join(temp_dir, task_id, data_query_service.TASK_RECORDS_FILE),
                [{'ASN': '12345', 'prefix': '1.1.1.0/24'}],
            )

            monkeypatch.setattr(
                data_query_service,
                '_resolve_row_for_family',
                lambda *args, **kwargs: {'as_country_cn': '中国'},
            )

            with app.app_context():
                response = data_query_service.generate_data_query_export(
                    task_id=task_id,
                    selected_field_ids=['canonical.country'],
                )

            body = response.get_data(as_text=True)
            assert response.status_code == 200
            assert 'country' in body
            assert 'CN' in body

    def test_get_data_query_preview_returns_original_plus_selected_fields(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

            task_id = 'task-preview'
            os.makedirs(os.path.join(temp_dir, task_id), exist_ok=True)
            data_query_service._write_json(
                os.path.join(temp_dir, task_id, data_query_service.TASK_METADATA_FILE),
                {
                    'task_id': task_id,
                    'task_name': '预览任务',
                    'file_name': 'query.csv',
                    'status': 'parsed',
                    'created_at': '2026-03-18 00:00:00',
                    'generated_at': '',
                    'row_count': 1,
                    'original_columns': ['ASN', 'prefix'],
                    'recognized_fields': ['asn', 'prefix'],
                    'upload_columns': [
                        {'original_name': 'ASN', 'standard_field': 'asn'},
                        {'original_name': 'prefix', 'standard_field': 'prefix'},
                    ],
                    'preview_rows': [{'ASN': '12345', 'prefix': '1.1.1.0/24'}],
                    'matched_database_fields': [
                        {
                            'field_id': 'canonical.country',
                            'field_name': 'country',
                            'table_family': '',
                            'table_label': '标准字段补全',
                            'label': 'country',
                            'source_kind': 'canonical',
                        }
                    ],
                    'selected_field_ids': [],
                },
            )
            data_query_service._write_json(
                os.path.join(temp_dir, task_id, data_query_service.TASK_RECORDS_FILE),
                [{'ASN': '12345', 'prefix': '1.1.1.0/24'}],
            )

            monkeypatch.setattr(
                data_query_service,
                '_resolve_row_for_family',
                lambda *args, **kwargs: {'as_country_cn': '中国'},
            )

            result = data_query_service.get_data_query_preview(
                task_id=task_id,
                selected_field_ids=['canonical.country'],
            )

            assert result['status'] is True
            assert result['data']['preview_columns'] == ['ASN', 'prefix', 'country']
            assert result['data']['preview_rows'] == [
                {'ASN': '12345', 'prefix': '1.1.1.0/24', 'country': 'CN'}
            ]

    def test_get_data_query_preview_uses_cache_for_same_selection(self, monkeypatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

            task_id = 'task-preview-cache'
            os.makedirs(os.path.join(temp_dir, task_id), exist_ok=True)
            data_query_service._write_json(
                os.path.join(temp_dir, task_id, data_query_service.TASK_METADATA_FILE),
                {
                    'task_id': task_id,
                    'task_version': data_query_service.DATA_QUERY_TASK_VERSION,
                    'task_name': '预览缓存任务',
                    'file_name': 'query.csv',
                    'status': 'parsed',
                    'created_at': '2026-03-19 00:00:00',
                    'generated_at': '',
                    'row_count': 1,
                    'original_columns': ['ASN'],
                    'recognized_fields': ['asn'],
                    'upload_columns': [{'original_name': 'ASN', 'standard_field': 'asn'}],
                    'preview_rows': [{'ASN': '12345'}],
                    'matched_database_fields': [
                        {
                            'field_id': 'canonical.country',
                            'field_name': 'country',
                            'table_family': '',
                            'table_label': '标准字段补全',
                            'label': 'country',
                            'source_kind': 'canonical',
                            'sample_hit_count': 1,
                            'sample_total_count': 1,
                            'sample_value_examples': ['CN'],
                            'sample_trace_examples': ['ASN -> AS基础信息 -> 国家'],
                            'resolution_hint': 'ASN -> AS基础信息 -> 国家',
                            'resolution_entry_fields': ['asn'],
                        }
                    ],
                    'selected_field_ids': [],
                },
            )
            data_query_service._write_json(
                os.path.join(temp_dir, task_id, data_query_service.TASK_RECORDS_FILE),
                [{'ASN': '12345'}],
            )

            call_count = {'count': 0}

            def fake_build_enriched_preview(*args, **kwargs):
                call_count['count'] += 1
                return ['ASN', 'country'], [{'ASN': '12345', 'country': 'CN'}]

            monkeypatch.setattr(data_query_service, '_build_enriched_preview', fake_build_enriched_preview)

            first_result = data_query_service.get_data_query_preview(
                task_id=task_id,
                selected_field_ids=['canonical.country'],
            )
            second_result = data_query_service.get_data_query_preview(
                task_id=task_id,
                selected_field_ids=['canonical.country'],
            )

            assert first_result['status'] is True
            assert second_result['status'] is True
            assert call_count['count'] == 1
            assert first_result['data'] == second_result['data']

    def test_generate_data_query_export_uses_source_filename_and_selected_fields(self, monkeypatch, app):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())

            task_id = self._prepare_export_task(temp_dir, file_name='样例源文件.csv')

            monkeypatch.setattr(
                data_query_service,
                '_build_enriched_preview',
                lambda *args, **kwargs: (
                    ['ASN', '国家', '机构名称'],
                    [{'ASN': '13335', '国家': '美国', '机构名称': 'Cloudflare'}],
                ),
            )

            with app.app_context():
                response = data_query_service.generate_data_query_export(
                    task_id=task_id,
                    selected_field_ids=['canonical.country', 'canonical.org_name'],
                )

            assert response.status_code == 200
            content_disposition = response.headers['Content-Disposition']
            assert "filename*=UTF-8''" in content_disposition
            assert '%E6%A0%B7%E4%BE%8B%E6%BA%90%E6%96%87%E4%BB%B6' in content_disposition
            assert '%E5%8C%B9%E9%85%8D%E5%AD%97%E6%AE%B5_country_org_name.csv' in content_disposition

    def test_generate_data_query_export_uses_single_selected_field_in_filename(self, monkeypatch, app):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            task_id = self._prepare_export_task(temp_dir, file_name='query.csv')

            monkeypatch.setattr(
                data_query_service,
                '_build_enriched_preview',
                lambda *args, **kwargs: (
                    ['ASN', '国家'],
                    [{'ASN': '13335', '国家': '美国'}],
                ),
            )

            with app.app_context():
                response = data_query_service.generate_data_query_export(
                    task_id=task_id,
                    selected_field_ids=['canonical.country'],
                )

            assert response.status_code == 200
            assert response.headers['Content-Disposition'].endswith("query_%E5%8C%B9%E9%85%8D%E5%AD%97%E6%AE%B5_country.csv")

    def test_generate_data_query_export_changes_filename_when_selection_changes(self, monkeypatch, app):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            task_id = self._prepare_export_task(temp_dir, file_name='query.csv')

            monkeypatch.setattr(
                data_query_service,
                '_build_enriched_preview',
                lambda *args, **kwargs: (
                    ['ASN', '国家', '机构名称'],
                    [{'ASN': '13335', '国家': '美国', '机构名称': 'Cloudflare'}],
                ),
            )

            with app.app_context():
                response_one = data_query_service.generate_data_query_export(
                    task_id=task_id,
                    selected_field_ids=['canonical.country'],
                )
                response_two = data_query_service.generate_data_query_export(
                    task_id=task_id,
                    selected_field_ids=['canonical.country', 'canonical.org_name'],
                )

            assert response_one.status_code == 200
            assert response_two.status_code == 200
            assert response_one.headers['Content-Disposition'] != response_two.headers['Content-Disposition']
            assert response_one.headers['Content-Disposition'].endswith("query_%E5%8C%B9%E9%85%8D%E5%AD%97%E6%AE%B5_country.csv")
            assert response_two.headers['Content-Disposition'].endswith(
                "query_%E5%8C%B9%E9%85%8D%E5%AD%97%E6%AE%B5_country_org_name.csv"
            )

    def test_generate_data_query_export_preserves_selected_field_order_in_filename(self, monkeypatch, app):
        with tempfile.TemporaryDirectory() as temp_dir:
            monkeypatch.setattr(data_query_service, 'TASK_STORAGE_DIR', temp_dir)
            monkeypatch.setattr(data_query_service, '_SCHEMA_CACHE', build_schema_catalog())
            task_id = self._prepare_export_task(temp_dir, file_name='query.csv')

            monkeypatch.setattr(
                data_query_service,
                '_build_enriched_preview',
                lambda *args, **kwargs: (
                    ['ASN', '国家', '机构名称'],
                    [{'ASN': '13335', '国家': '美国', '机构名称': 'Cloudflare'}],
                ),
            )

            with app.app_context():
                response = data_query_service.generate_data_query_export(
                    task_id=task_id,
                    selected_field_ids=['canonical.org_name', 'canonical.country'],
                )

            assert response.status_code == 200
            assert response.headers['Content-Disposition'].endswith(
                "query_%E5%8C%B9%E9%85%8D%E5%AD%97%E6%AE%B5_org_name_country.csv"
            )
