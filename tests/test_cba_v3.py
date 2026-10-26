"""CBA 3 pilot smoke checks. Use a fresh SQLite database fixture from test_cba.py."""
import uuid
import pytest
from test_cba import csrf, login, damage_payload


def test_v3_routes_and_pareto(module):
    client=module.app.test_client()
    login(client)
    for uri in ['/analytics','/plans','/sla','/reports','/forecast','/assistant']:
        result=client.get(uri)
        assert result.status_code==200,(uri,result.status_code)
    assert client.get('/api/session').json['authenticated'] is True
    response=client.get('/damages/1/qr.png')
    assert response.status_code==200 and response.mimetype=='image/png'


def test_v3_action_plan_and_sla(module):
    client=module.app.test_client()
    login(client)
    with module.app.app_context():
        sid=module.db.session.get(module.User,1).site_id
    client.get('/plans')
    response=client.post('/plans',data={'csrf_token':csrf(client),'damage_id':'1',
       'what':'Revisar empilhamento','why':'Reduzir danos','where':'Armazenagem',
       'when_due':'2027-02-15','who':'Lider A','how':'Treinar equipe',
       'how_much':'0'},follow_redirects=True)
    assert response.status_code==200
    with module.app.app_context():
        assert module.db.session.scalar(module.db.select(module.func.count()).select_from(module.ActionPlan))>=1
    client.get('/sla')
    response=client.post('/sla/policy',data={'csrf_token':csrf(client),
       'site_id':str(sid),'severity':'Alta','hours':'16'},follow_redirects=True)
    assert response.status_code==200


def test_v3_offline_idempotent(module):
    client=module.app.test_client()
    login(client)
    data={k:v for k,v in damage_payload(client).items() if k!='csrf_token'}
    data['offline_key']=str(uuid.uuid4())
    client.get('/api/session')
    response=client.post('/api/offline/damages',json=data,
        headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==201,response.get_json()
    response=client.post('/api/offline/damages',json=data,
        headers={'X-CSRF-Token':csrf(client)})
    assert response.status_code==200
    assert response.get_json()['status']=='already_saved'


def test_v3_report_generation(module):
    client=module.app.test_client()
    login(client)
    with module.app.app_context():
        sid=module.db.session.get(module.User,1).site_id
    client.get('/reports')
    response=client.post('/reports',data={'csrf_token':csrf(client),'site_id':str(sid),
       'name':'Relatorio Teste','frequency':'diário'},follow_redirects=True)
    assert response.status_code==200
    client.get('/reports')
    response=client.post('/reports/run',data={'csrf_token':csrf(client)},follow_redirects=True)
    assert response.status_code==200
    with module.app.app_context():
        assert module.db.session.scalar(module.db.select(module.func.count()).select_from(module.ReportRun))>=1
