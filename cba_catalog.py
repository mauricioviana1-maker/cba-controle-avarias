"""Catálogo mestre de produtos do CBA 3.1 (rotas e importação CSV).

O cadastro mestre é gerenciado exclusivamente pelo administrador. Uma ocorrência
sempre usa o nome/preço do SKU ativo no servidor, nunca o valor enviado pelo
navegador; o cadastro histórico da ocorrência não muda após atualizações do SKU.
"""
import csv
import io
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from flask import abort, flash, jsonify, redirect, render_template, request, Response, url_for
from sqlalchemy import or_

VALID_SKU = re.compile(r'^[A-Z0-9][A-Z0-9 ._/-]{0,79}$')
CSV_HEADERS = ('sku', 'produto', 'valor_unitario', 'unidade', 'marca', 'ean')


def clean_sku(raw):
    sku = str(raw or '').strip().upper()
    if not VALID_SKU.fullmatch(sku):
        raise ValueError('SKU inválido. Use até 80 caracteres: letras, números, espaço, ponto, hífen, barra ou sublinhado.')
    return sku


def decimal_price(raw):
    """Preço opcional; aceita formato BR 1.234,56 e formato 1234.56."""
    value = str(raw or '').strip().replace('R$', '').replace(' ', '')
    if not value:
        return None
    if ',' in value and '.' in value:
        value = value.replace('.', '').replace(',', '.') if value.rfind(',') > value.rfind('.') else value.replace(',', '')
    elif ',' in value:
        value = value.replace(',', '.')
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount > Decimal('9999999.99'):
            raise ValueError()
        if amount.as_tuple().exponent < -2:
            raise ValueError()
        return amount.quantize(Decimal('0.01'))
    except (ValueError, InvalidOperation):
        raise ValueError(f'Valor unitário inválido: {raw!r}. Informe valor positivo com até 2 casas, ou deixe vazio.')


def validate_product(row):
    sku = clean_sku(row.get('sku'))
    product = str(row.get('produto') or '').strip()
    if not (1 <= len(product) <= 160) or any(ord(c) < 32 for c in product):
        raise ValueError(f'SKU {sku}: nome do produto obrigatório (até 160 caracteres).')
    cleaned = {'sku': sku, 'product': product,
               'unit_value': decimal_price(row.get('valor_unitario'))}
    for csv_name, model_name, limit in [('unidade', 'unit', 30), ('marca', 'brand', 100), ('ean', 'ean', 50)]:
        value = str(row.get(csv_name) or '').strip()
        if len(value) > limit or any(ord(c) < 32 for c in value):
            raise ValueError(f'SKU {sku}: valor inválido para {csv_name}.')
        cleaned[model_name] = value or None
    return cleaned


def register_catalog_routes(g):
    app, db = g['app'], g['db']
    Product = g['ProductCatalog']
    require_login, require_role = g['require_login'], g['require_role']
    audit, now_utc = g['audit'], g['now_utc']

    @app.get('/api/products')
    @require_login
    def products_lookup():
        term = request.args.get('q', '').strip()[:80]
        if not term:
            return jsonify(items=[])
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = '%' + escaped + '%'
        rows = db.session.execute(db.select(Product).where(
            Product.active.is_(True),
            or_(Product.sku.ilike(pattern, escape='\\'), Product.product.ilike(pattern, escape='\\'))
        ).order_by(Product.sku).limit(12)).scalars().all()
        return jsonify(items=[{'sku': p.sku, 'produto': p.product,
                               'unidade': p.unit or '', 'marca': p.brand or '',
                               'preco_disponivel': p.unit_value is not None} for p in rows])

    def upsert(data):
        row = db.session.scalar(db.select(Product).where(Product.sku == data['sku']))
        created = row is None
        if created:
            row = Product(sku=data['sku'])
            db.session.add(row)
        for key in ('product', 'unit_value', 'unit', 'brand', 'ean'):
            setattr(row, key, data[key])
        row.active = True
        row.updated_at = now_utc()
        return created

    @app.route('/products', methods=['GET', 'POST'])
    @require_role('admin')
    def products():
        if request.method == 'POST':
            raw = {'sku':request.form.get('sku'), 'produto':request.form.get('produto'),
                   'valor_unitario':request.form.get('valor_unitario'),
                   'unidade':request.form.get('unidade'), 'marca':request.form.get('marca'),
                   'ean':request.form.get('ean')}
            try:
                data = validate_product(raw)
                created = upsert(data)
                audit('catálogo de produtos', detail=f'SKU {data["sku"]}: '+('cadastrado' if created else 'atualizado'))
                db.session.commit()
                flash('Produto cadastrado.' if created else 'Cadastro do produto atualizado.', 'success')
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), 'error')
            return redirect(url_for('products'))
        q = request.args.get('q', '').strip()[:80]
        pattern = '%' + q.replace('%','\\%').replace('_','\\_') + '%'
        query = db.select(Product)
        if q:
            query = query.where(or_(Product.sku.ilike(pattern, escape='\\'), Product.product.ilike(pattern, escape='\\')))
        rows = db.session.execute(query.order_by(Product.sku).limit(150)).scalars().all()
        count = db.session.scalar(db.select(db.func.count()).select_from(Product)) or 0
        return render_template('products.html', products=rows, count=count, search=q)

    @app.post('/products/import')
    @require_role('admin')
    def products_import():
        upload = request.files.get('catalog_file')
        if not upload or not upload.filename or not upload.filename.lower().endswith('.csv'):
            flash('Selecione um arquivo CSV (não um ZIP ou Excel).', 'error')
            return redirect(url_for('products'))
        content = upload.stream.read(2_000_001)
        if len(content) > 2_000_000:
            flash('Arquivo CSV muito grande: máximo de 2 MB por importação.', 'error')
            return redirect(url_for('products'))
        try:
            try:
                text_data = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                text_data = content.decode('cp1252')
            header_line = text_data.splitlines()[0] if text_data.splitlines() else ''
            delimiter = ';' if ';' in header_line else ','
            rows = csv.DictReader(io.StringIO(text_data, newline=''), delimiter=delimiter)
            if not rows.fieldnames:
                raise ValueError('CSV sem cabeçalho.')
            rows.fieldnames = [str(name or '').strip().lower().removeprefix('\ufeff') for name in rows.fieldnames]
            if len(rows.fieldnames) != len(set(rows.fieldnames)):
                raise ValueError('O CSV contém nomes de colunas duplicados.')
            present_columns = set(rows.fieldnames)
            missing = {'sku', 'produto'} - present_columns
            if missing:
                raise ValueError('Faltam colunas obrigatórias: ' + ', '.join(sorted(missing)))
            entries = []
            seen = set()
            for number, raw in enumerate(rows, 2):
                if number > 10002:
                    raise ValueError('Limite de 10.000 linhas por arquivo.')
                if None in raw:
                    raise ValueError(f'Linha {number}: número de colunas diferente do cabeçalho.')
                if not any(str(v or '').strip() for v in raw.values()):
                    continue
                try:
                    record = validate_product(raw)
                except ValueError as exc:
                    raise ValueError(f'Linha {number}: {exc}')
                if record['sku'] in seen:
                    raise ValueError(f'Linha {number}: SKU duplicado neste arquivo: {record["sku"]}.')
                seen.add(record['sku'])
                entries.append(record)
            if not entries:
                raise ValueError('O arquivo CSV não contém produtos.')
            # Todas as linhas são validadas antes do início da atualização.
            existing = {r.sku: r for r in db.session.execute(
                db.select(Product).where(Product.sku.in_([x['sku'] for x in entries]))).scalars()}
            created = updated = 0
            for data in entries:
                row = existing.get(data['sku'])
                if row is None:
                    row = Product(sku=data['sku'])
                    db.session.add(row)
                    created += 1
                else:
                    updated += 1
                for key, csv_column in (('product', 'produto'), ('unit_value', 'valor_unitario'),
                                        ('unit', 'unidade'), ('brand', 'marca'), ('ean', 'ean')):
                    # Colunas opcionais ausentes não apagam dados já cadastrados.
                    if csv_column in present_columns:
                        setattr(row, key, data[key])
                row.active = True
                row.updated_at = now_utc()
            audit('importação de catálogo', detail=f'{len(entries)} SKUs: {created} novos e {updated} atualizados.')
            db.session.commit()
            flash(f'Importação concluída: {created} novos, {updated} atualizados.', 'success')
        except (ValueError, UnicodeError, csv.Error) as exc:
            db.session.rollback()
            flash(f'Importação cancelada. Nenhuma linha gravada: {exc}', 'error')
        return redirect(url_for('products'))

    @app.post('/products/<int:pid>/toggle')
    @require_role('admin')
    def product_toggle(pid):
        row = db.get_or_404(Product, pid)
        row.active = not row.active
        row.updated_at = now_utc()
        audit('catálogo de produtos', detail=f'{row.sku}: '+('ativado' if row.active else 'desativado'))
        db.session.commit()
        flash('Situação do SKU atualizada.', 'success')
        return redirect(url_for('products'))

    @app.get('/products/template.csv')
    @require_role('admin')
    def products_template():
        example = ('sku;produto;valor_unitario;unidade;marca;ean\r\n'
                   'SKU-TESTE-001;Produto de demonstração A;12,50;UN;Marca fictícia;\r\n'
                   'SKU-TESTE-002;Produto de demonstração B;;CX;;\r\n')
        return Response('\ufeff'+example, mimetype='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="modelo_catalogo_cba.csv"'})
