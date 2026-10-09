import base64
from copy import deepcopy
from io import BytesIO
import json
import time
import uuid
from zipfile import ZipFile

from fastapi.testclient import TestClient
from just_read.app import COOKIE, create_app
from just_read.settings import Settings


def settings(tmp_path):
    return Settings(database_path=str(tmp_path / 'api.sqlite3'), test_mode=True,
                    llm_provider='fixture', fixture_delay_seconds=.01)


def finished(client, task_id):
    for _ in range(500):
        task = client.get(f'/api/v1/research-tasks/{task_id}').json()
        if task['status'] in {'succeeded', 'failed', 'cancelled'}:
            assert task['status'] == 'succeeded', task
            return task['report']
        time.sleep(.01)
    raise AssertionError('fixture did not finish')


def test_full_ad_api_material_research_revision_review_share_export(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        health = client.get('/api/v1/health').json()
        assert health['runtime']['workers_expected'] == 2
        upload = {'name': '模拟材料.md', 'media_type': 'text/markdown',
            'content_base64': base64.b64encode('模拟材料：数字为 20 和 30。'.encode()).decode()}
        result = client.post('/api/v1/sources', json=upload)
        assert result.status_code == 201, result.text
        source = result.json()
        assert len(client.get('/api/v1/sources').json()['sources']) == 1
        assert client.get(f"/api/v1/sources/{source['id']}/download").content == base64.b64decode(upload['content_base64'])
        response = client.post('/api/v1/research-tasks', json={'question': '模拟研究', 'depth': 'brief',
            'source_ids': [source['id']], 'reader': '开发者', 'scope': '模拟数字', 'enable_3d': True},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        assert response.status_code == 201, response.text
        report = finished(client, response.json()['id'])
        rid = report['id']; url = '/api/v1/reports/' + rid
        assert report['version'] == 1
        assert source['id'] in {s['id'] for s in report['workflow']['sources']}
        assert report['workflow']['scene']['parts']
        assert client.get(f"/api/v1/research-tasks/{response.json()['id']}/runs").json()['runs']
        changed = deepcopy(report); changed['title'] = '模拟修改后标题'
        updated = client.post(url + '/versions', json={'base_version': 1, 'report': changed})
        assert updated.status_code == 200, updated.text
        assert updated.json()['version'] == 2
        assert client.post(url + '/versions', json={'base_version': 1, 'report': changed}).status_code == 409
        assert client.get(url + '/versions/1').json()['title'] == report['title']
        for action in ('validate', 'review', 'human-review'):
            review = client.post(url + '/actions', json={'action': action, 'version': 2, 'reviewer': '测试审查员', 'notes': '仅测试交互'})
            assert review.status_code == 200, review.text
        records = client.get(url + '/workflow?version=2').json()['reviews']
        assert {r['kind'] for r in records} == {'automatic', 'ai', 'human'}
        assert next(r for r in records if r['kind'] == 'ai')['simulated'] is True
        share = client.post(url + '/actions', json={'action': 'publish', 'version': 1}).json()
        assert share['url'].endswith('/#/share/' + share['token'])
        owner_cookie = client.cookies.get(COOKIE)
        client.cookies.clear()
        public = client.get('/api/v1/shared/' + share['token'])
        assert public.status_code == 200 and public.json()['version'] == 1
        assert client.get(url).status_code == 428
        client.cookies.set(COOKIE, owner_cookie)
        exported = client.get(url + '/export?version=2&format=zip')
        assert exported.status_code == 200
        with ZipFile(BytesIO(exported.content)) as archive:
            assert archive.testzip() is None
            assert json.loads(archive.read('report.json'))['version'] == 2
        assert client.get(url + '/export?version=1&format=html').headers['content-type'].startswith('text/html')
        restored = client.post(url + '/actions', json={'action': 'restore', 'base_version': 2, 'version': 1})
        assert restored.json()['version'] == 3 and restored.json()['title'] == report['title']
        assert client.post(url + '/actions', json={'action': 'revoke-share', 'token': share['token']}).status_code == 200
        assert client.get('/api/v1/shared/' + share['token']).status_code == 404


def test_material_owner_and_task_source_validation(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.get('/api/v1/health')
        source = client.post('/api/v1/sources', json={'name': '模拟.txt', 'content_base64': 'YWJj'}).json()
        client.cookies.clear(); client.get('/api/v1/health')
        assert client.get('/api/v1/sources').json() == {'sources': []}
        assert client.get(f"/api/v1/sources/{source['id']}").status_code == 404
        result = client.post('/api/v1/research-tasks', json={'question': '无法访问他人材料', 'source_ids': [source['id']]},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        assert result.status_code == 404
        assert client.post('/api/v1/sources', json={'name': 'bad.txt', 'content_base64': '@@@'}).status_code == 422


def test_manual_calculation_creates_version_and_preserves_inputs(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.get('/api/v1/health')
        task = client.post('/api/v1/research-tasks', json={'question': '模拟数值研究'},
            headers={'Idempotency-Key': str(uuid.uuid4())}).json()
        report = finished(client, task['id'])
        report['workflow']['datasets'] = [{'id': 'ds-simulated', 'unit': '万元', 'period': '2025',
            'rows': [{'id': 'd1', 'label': '模拟A', 'value_text': '20', 'source_id': 'S1'},
                     {'id': 'd2', 'label': '模拟B', 'value_text': '30', 'source_id': 'S2'}]}]
        rid = client.post('/api/v1/reports/import', json=report).json()['id']
        url = f'/api/v1/reports/{rid}/actions'
        computed = client.post(url, json={'action': 'calculate', 'base_version': 1,
            'operation': 'sum', 'input_refs': ['d1', 'd2']})
        assert computed.status_code == 200, computed.text
        value = computed.json()
        assert value['calculation']['result'] == 50 and value['calculation']['unit'] == '万元'
        assert value['calculation']['source_refs'] == ['S1', 'S2']
        assert value['report']['version'] == 2
        assert any('核算验证运算结果' in p for c in value['report']['chapters'] for p in c['paragraphs'])
        assert client.post(url, json={'action': 'calculate', 'base_version': 1, 'input_refs': ['d1']}).status_code == 409
        unavailable = client.post(url, json={'action': 'calculate', 'base_version': 2, 'operation': 'ratio', 'input_refs': ['unknown', 'd2']})
        assert unavailable.status_code == 422
        assert client.get(f'/api/v1/reports/{rid}').json()['version'] == 2


def test_invalid_nested_workflow_numeric_values_do_not_500(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        client.get('/api/v1/health')
        task = client.post('/api/v1/research-tasks', json={'question': '模拟研究'}, headers={'Idempotency-Key': str(uuid.uuid4())}).json()
        report = finished(client, task['id'])
        report['workflow']['datasets'] = [{'rows': [{'value': float('inf')}]}]
        response = client.post('/api/v1/reports/import', content=json.dumps(report), headers={'Content-Type': 'application/json'})
        assert response.status_code == 422
