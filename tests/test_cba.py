"""Tests for the V2 pilot; run with `pytest -q` after pip install -r requirements.txt."""
import importlib
import io
import os
import sqlite3
import sys

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash


@pytest.fixture(scope='module')
def module(tmp_path_factory):
    temp = tmp_path_factory.mktemp('database')
    dbpath = temp / 'cba.db'
    # Recreate the V1 tables as deployed by Flask-SQLAlchemy 1.x to test
    # an additive migration and unchanged legacy data.
    connection = sqlite3.connect(dbpath)
    connection.executescript('''
    CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(80) UNIQUE NOT NULL,
      password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL, active BOOLEAN NOT NULL);
    CREATE TABLE damage (id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL,
      product VARCHAR(160) NOT NULL, sku VARCHAR(80) NOT NULL,
      quantity INTEGER NOT NULL, value NUMERIC(12, 2) NOT NULL,
      stage VARCHAR(60) NOT NULL, shift VARCHAR(30) NOT NULL,
      location VARCHAR(100) NOT NULL, description VARCHAR(500) NOT NULL,
      status VARCHAR(20) NOT NULL, creator_id INTEGER NOT NULL);
    ''')
    connection.execute('INSERT INTO user VALUES(?,?,?,?,?)',
                       (1, 'admin', generate_password_hash('senha-antiga-super-segura'), 'gestor', 1))
    connection.execute('''INSERT INTO damage VALUES(1, '2026-10-15 12:00:00',
      'Produto Legado', 'LEG-100', 3, 25.00, 'Recebimento', '1º turno',
      'Rua 04', 'Cadastro v1', 'Aberta', 1)''')
    connection.commit()
    connection.close()
    os.environ.update(SECRET_KEY='some-really-long-secret-only-for-unit-tests-xxxxxxxx',
                      DATABASE_URL='sqlite:///' + str(dbpath),
                      ADMIN_PASSWORD='initial-admin-password-123', FLASK_ENV='development')
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    sys.modules.pop('app', None)
    mod = importlib.import_module('app')
    mod.app.config['TESTING'] = True
    yield mod


def csrf(client):
    with client.session_transaction() as s:
        return s.get('csrf_token')


def login(client, username='admin', password='senha-antiga-super-segura'):
    client.get('/login')
    result = client.post('/login', data={'username':username, 'password':password,
                                         'csrf_token':csrf(client)}, follow_redirects=True)
    assert result.status_code == 200
    return result


def damage_payload(client):
    return {'csrf_token':csrf(client), 'product':'Produto piloto', 'sku':'SKU-100',
            'quantity':'4', 'value':'155.50','stage':'Separação','shift':'2º turno',
            'location':'Rua 04','sector':'Picking','responsible_name':'Operador fictício',
            'category':'Embalagem','cause':'Queda','severity':'Alta',
            'lot':'ABC123','expiry_date':'2027-01-22', 'description':'Teste automatizado'}


def test_migrate_legacy_data_and_admin_permissions(module):
    with module.app.app_context():
        assert module.db.session.get(module.Damage, 1).product == 'Produto Legado'
        admin = module.db.session.get(module.User, 1)
        assert admin.role == 'admin'
        assert admin.site_id is not None
        assert module.db.session.get(module.Damage, 1).category is None
        assert module.db.session.get(module.Damage, 1).site_id == admin.site_id


def test_health_login_and_new_damage_with_photo(module):
    client = module.app.test_client()
    assert client.get('/health').json == {'status':'ok','version':'3.0.0'}
    assert client.get('/').status_code == 302
    login(client)
    client.get('/damages/new')
    img = Image.new('RGB', (40,40), 'white')
    raw = io.BytesIO()
    img.save(raw, format='PNG')
    raw.seek(0)
    data = damage_payload(client)
    data['photos'] = (raw, 'teste.png')
    response = client.post('/damages/new', data=data,
                           content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200
    assert b'SKU-100' in response.data
    with module.app.app_context():
        d = module.db.session.execute(module.db.select(module.Damage).where(module.Damage.sku == 'SKU-100')).scalar_one()
        assert d.site_id is not None
        assert len(d.photos) == 1
        assert d.photos[0].mime_type == 'image/jpeg'
        assert module.db.session.scalar(module.db.select(module.func.count()).select_from(module.AuditLog).where(module.AuditLog.damage_id == d.id)) >= 1
        pid = d.photos[0].id
    response = client.get(f'/photos/{pid}')
    assert response.status_code == 200
    assert response.mimetype == 'image/jpeg'


def test_workflow_requires_treatment_and_manager_approval(module):
    client = module.app.test_client()
    login(client)
    with module.app.app_context():
        d = module.db.session.execute(module.db.select(module.Damage).where(module.Damage.sku == 'SKU-100')).scalar_one()
        did = d.id
    for target in ['Em análise', 'Aguardando aprovação', 'Aprovada']:
        client.get(f'/damages/{did}')
        result = client.post(f'/damages/{did}/status', data={'csrf_token':csrf(client),'status':target},follow_redirects=True)
        assert result.status_code == 200
    client.get(f'/damages/{did}')
    failed = client.post(f'/damages/{did}/status', data={'csrf_token':csrf(client),'status':'Concluída'},follow_redirects=True)
    assert b'destina' in failed.data.lower()
    client.get(f'/damages/{did}')
    client.post(f'/damages/{did}/treatment', data={'csrf_token':csrf(client),'destination':'Reembalagem','recovery_value':'20.00'},follow_redirects=True)
    client.get(f'/damages/{did}')
    client.post(f'/damages/{did}/status', data={'csrf_token':csrf(client),'status':'Concluída'})
    with module.app.app_context():
        record = module.db.session.get(module.Damage,did)
        assert record.status == 'Concluída'
        assert record.recovery_value == 20
        assert record.resolved_at is not None


def test_operator_cannot_approve_or_view_others_photos(module):
    with module.app.app_context():
        admin = module.db.session.get(module.User, 1)
        if not module.db.session.scalar(module.db.select(module.User.id).where(module.User.username == 'operador_teste')):
            module.db.session.add(module.User(username='operador_teste', password_hash=generate_password_hash('operador-senha-123'),
                                              role='operador',active=True,site_id=admin.site_id))
            module.db.session.commit()
        d = module.db.session.execute(module.db.select(module.Damage).where(module.Damage.sku == 'SKU-100')).scalar_one()
        did, pid = d.id, d.photos[0].id
    client = module.app.test_client()
    login(client, 'operador_teste', 'operador-senha-123')
    assert client.get(f'/damages/{did}').status_code == 403
    assert client.get(f'/photos/{pid}').status_code == 403
    client.get('/damages')
    assert client.post(f'/damages/{did}/status', data={'csrf_token':csrf(client),'status':'Aprovada'}).status_code == 403


def test_export_csv_and_csrf(module):
    client = module.app.test_client()
    login(client)
    response = client.get('/damages/export.csv?sku=SKU-100')
    assert response.status_code == 200
    assert 'SKU-100' in response.get_data(as_text=True)
    assert client.post('/damages/new', data={'product':'Test'}).status_code == 403
