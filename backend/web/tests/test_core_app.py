import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_ROUTES = {
    '/api/v1/healthz',
    '/api/v1/p0/status',
    '/api/v1/p0/metrics/<metric_name>',
    '/api/v1/p0/quality',
    '/api/v1/events',
    '/api/v1/events/top',
    '/api/v1/events/evidence-bundle/<event_type>/<start_time>/<problem>/<int:event_id>/<source>',
    '/api/v1/events/story/<event_type>/<start_time>/<problem>/<int:event_id>/<source>',
    '/api/v1/events/observations/<event_type>/<start_time>/<problem>/<int:event_id>/<source>',
    '/api/v1/<event_type>/<start_time>/<problem>/<int:event_id>/<source>',
    '/api/v1/features/top',
    '/api/v1/features/countries',
    '/api/v1/features/countries/overview',
        '/api/v1/features/ases',
        '/api/v1/features/ases/overview',
        '/api/v1/features/ases/events',
    '/api/v1/features/outages/country-as',
    '/api/v1/features/outages/country-prefix',
    '/api/v1/features/outages/as-prefix',
    '/api/v1/features/outages/global-as',
    '/api/v1/features/outages/global-prefix',
    '/api/v1/dashboard/counts/total',
    '/api/v1/dashboard/counts/type',
    '/api/v1/dashboard/overview',
    '/api/v2/events/resolve',
    '/api/v2/country-outages/<incident_id>/overview',
    '/api/v2/country-outages/<incident_id>/series',
    '/api/v2/country-outages/<incident_id>/asns',
    '/api/v2/country-outages/<incident_id>/audit',
}


def test_route_whitelist(app):
    routes = {
        str(rule)
        for rule in app.url_map.iter_rules()
        if not str(rule).startswith('/static/')
    }
    assert routes == EXPECTED_ROUTES


def test_removed_platform_routes_return_404(client):
    for path in (
        '/api/v1/login',
        '/api/v1/events/state',
        '/api/v1/events/judge',
        '/api/v1/events/notify',
        '/api/v1/reports/word-export',
        '/api/v1/geodata/boundaries',
        '/api/v1/node-status',
        '/api/v1/data-query/tasks',
    ):
        assert client.get(path).status_code == 404


def test_health_does_not_require_database_or_assets(client, assert_contract):
    before = datetime.now(timezone.utc)
    response = client.get('/api/v1/healthz')
    after = datetime.now(timezone.utc)

    assert response.status_code == 200
    payload = response.get_json()
    assert_contract(payload, {'status': str, 'service': str, 'time': str})
    assert payload['status'] == 'ok'
    assert payload['service'] == 'domeye-core'
    assert payload['time'].endswith('Z')

    health_time = datetime.fromisoformat(payload['time'].replace('Z', '+00:00'))
    assert health_time.tzinfo == timezone.utc
    assert before <= health_time <= after


def test_removed_services_are_not_imported(app):
    assert 'services.auth_service' not in sys.modules
    assert 'services.data_query_service' not in sys.modules
    assert 'core.visualize.outage_topo' not in sys.modules
    assert 'web.api.reports.api' not in sys.modules


def test_local_env_loader_can_be_disabled(monkeypatch):
    from run import load_local_env

    monkeypatch.setenv('DOMEYE_CORE_SKIP_LOCAL_ENV', 'true')

    def unexpected_env_lookup(_):
        raise AssertionError('禁用本地环境加载后不应读取 .env')

    monkeypatch.setattr('run.os.path.exists', unexpected_env_lookup)
    load_local_env()


def test_core_files_match_migration_manifest():
    backend_dir = Path(__file__).resolve().parents[2]
    manifest = backend_dir / 'core.sha256'
    for line in manifest.read_text(encoding='utf-8').splitlines():
        expected, relative_path = line.split('  ', 1)
        actual = hashlib.sha256((backend_dir / relative_path).read_bytes()).hexdigest()
        assert actual == expected, relative_path
