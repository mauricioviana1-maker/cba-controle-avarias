"""CBA 3.1: teste de segurança/autocomplete/importação e preservação histórica."""
import io
from decimal import Decimal
from test_cba import module, csrf, login, damage_payload


def test_catalog_admin_and_search(module):
    client = module.app.test_client()
    login(client)
    page = client.get('/products')
    assert page.status_code == 200
    assert b'Cat' in page.data
    response = client.get('/api/products?q=SKU-100')
    assert response.status_code == 200
    assert response.json['items'][0]['produto'] == 'Produto piloto no catálogo'
    form = client.get('/damages/new')
    assert b'id="product-display" readonly' in form.data
    assert b'name="value"' not in form.data
    assert b'name="location"' not in form.data
    assert b'name="severity"' not in form.data
    assert b'>Palete<' not in form.data
    assert b'>Validade</option>' not in form.data


def test_product_values_are_only_from_catalog(module):
    client = module.app.test_client()
    login(client)
    client.get('/damages/new')
    payload = damage_payload(client)
    payload.update(product='Produto falsificado', value='0.01', severity='Crítica', location='Local falso', category='Embalagem')
    saved = client.post('/damages/new', data=payload, follow_redirects=True)
    assert saved.status_code == 200
    with module.app.app_context():
        damage = module.db.session.execute(module.db.select(module.Damage).where(
            module.Damage.sku=='SKU-100').order_by(module.Damage.id.desc())).scalars().first()
        assert damage.product == 'Produto piloto no catálogo'
        assert damage.value == Decimal('155.52')
        assert damage.value_known is True
        assert damage.location == 'Não informado'
        assert damage.severity == 'Média'


def test_unknown_or_inactive_sku_is_rejected(module):
    client = module.app.test_client()
    login(client)
    client.get('/damages/new')
    payload = damage_payload(client)
    payload['sku'] = 'NAO-EXISTE'
    response = client.post('/damages/new', data=payload, follow_redirects=True)
    assert response.status_code == 200
    assert 'Selecione um SKU ativo' in response.data.decode()
    with module.app.app_context():
        catalog = module.db.session.scalar(module.db.select(module.ProductCatalog).where(module.ProductCatalog.sku=='SKU-100'))
        catalog.active=False
        module.db.session.commit()
    payload['sku']='SKU-100'
    response = client.post('/damages/new',data=payload,follow_redirects=True)
    assert 'Selecione um SKU ativo' in response.data.decode()
    with module.app.app_context():
        catalog = module.db.session.scalar(module.db.select(module.ProductCatalog).where(module.ProductCatalog.sku=='SKU-100'))
        catalog.active=True
        module.db.session.commit()


def test_import_with_missing_price_and_atomic_rejection(module):
    client = module.app.test_client()
    login(client)
    client.get('/products')
    good = b'sku;produto;valor_unitario;unidade;marca;ean\nCAT-001;Produto novo;12,50;UN;Teste;\nCAT-002;Produto sem preco;;CX;;\n'
    response = client.post('/products/import',data={'csrf_token':csrf(client),'catalog_file':(io.BytesIO(good),'catalogo.csv')},content_type='multipart/form-data',follow_redirects=True)
    assert response.status_code==200
    with module.app.app_context():
        p=module.db.session.scalar(module.db.select(module.ProductCatalog).where(module.ProductCatalog.sku=='CAT-001'))
        assert p.product=='Produto novo' and p.unit_value==Decimal('12.50')
        q=module.db.session.scalar(module.db.select(module.ProductCatalog).where(module.ProductCatalog.sku=='CAT-002'))
        assert q.unit_value is None
    bad=b'sku;produto\nNO-VALIDO;Produto ok\nNO-VALIDO;Produto repetido\n'
    response=client.post('/products/import',data={'csrf_token':csrf(client),'catalog_file':(io.BytesIO(bad),'catalogo.csv')},content_type='multipart/form-data',follow_redirects=True)
    assert 'Importação cancelada' in response.data.decode()
    with module.app.app_context():
        assert module.db.session.scalar(module.db.select(module.ProductCatalog.id).where(module.ProductCatalog.sku=='NO-VALIDO')) is None


def test_missing_price_is_not_assumed_zero(module):
    client=module.app.test_client()
    login(client)
    client.get('/damages/new')
    payload=damage_payload(client)
    payload['sku']='CAT-002'
    response=client.post('/damages/new',data=payload,follow_redirects=True)
    assert response.status_code==200
    with module.app.app_context():
        d=module.db.session.execute(module.db.select(module.Damage).where(module.Damage.sku=='CAT-002')).scalars().first()
        assert d and d.value_known is False
        assert d.value==0
    assert 'Não informado' in client.get('/damages').data.decode()


def test_non_admin_cannot_manage_catalog(module):
    with module.app.app_context():
        admin = module.db.session.get(module.User, 1)
        from werkzeug.security import generate_password_hash
        if not module.db.session.scalar(module.db.select(module.User.id).where(module.User.username == 'catalog_operator')):
            module.db.session.add(module.User(username='catalog_operator',password_hash=generate_password_hash('senha-de-operador-teste'),role='operador',active=True,site_id=admin.site_id))
            module.db.session.commit()
    client=module.app.test_client()
    login(client, 'catalog_operator', 'senha-de-operador-teste')
    assert client.get('/products').status_code==403
    assert client.get('/products/template.csv').status_code==403
    assert client.get('/api/products?q=CAT-002').status_code==200
