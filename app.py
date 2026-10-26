"""CBA 2.0 — Controle de Avarias | Pilot application.

Flask + PostgreSQL. Existing CBA 1.x user/damage data are retained by additive migration.
Photographs reside in protected DB records for this pilot; migrate to private object
storage before extensive deployment.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, send_file, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, inspect, or_, text
from werkzeug.security import check_password_hash, generate_password_hash

VERSION = '2.0.0'
ROLES = {'operador', 'lider', 'gestor', 'admin'}
STAGES = ('Recebimento', 'Armazenagem', 'Separação', 'Expedição', 'Transporte', 'Outros')
SHIFTS = ('1º turno', '2º turno', '3º turno')
CAUSES = ('Não identificada', 'Queda', 'Esmagamento', 'Vazamento', 'Ruptura de embalagem',
          'Manuseio inadequado', 'Transporte', 'Vencimento', 'Outros')
CATEGORIES = ('Produto danificado', 'Embalagem', 'Palete', 'Validade', 'Outros')
SEVERITIES = ('Baixa', 'Média', 'Alta', 'Crítica')
DESTINATIONS = ('', 'Reembalagem', 'Reaproveitamento', 'Devolução', 'Descarte', 'Retrabalho', 'Outros')
STATUSES = ('Aberta', 'Em análise', 'Aguardando aprovação', 'Aprovada', 'Rejeitada', 'Concluída')
TRANSITIONS = {
    'Aberta': {'Em análise'},
    'Em análise': {'Aguardando aprovação'},
    'Aguardando aprovação': {'Aprovada', 'Rejeitada'},
    'Rejeitada': {'Em análise'},
    'Aprovada': {'Concluída'},
    'Concluída': set(),
}

app = Flask(__name__)
secret = os.environ.get('SECRET_KEY')
if not secret or len(secret) < 32:
    raise RuntimeError('Defina SECRET_KEY com no mínimo 32 caracteres aleatórios.')
app.config.update(
    SECRET_KEY=secret,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv('FLASK_ENV', 'production') == 'production',
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=11 * 1024 * 1024,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)
uri = os.environ.get('DATABASE_URL')
if not uri:
    raise RuntimeError('Defina DATABASE_URL no Render.')
if uri.startswith('postgres://'):
    uri = 'postgresql://' + uri[len('postgres://'):]
if uri.startswith('postgresql://'):
    uri = 'postgresql+psycopg://' + uri[len('postgresql://'):]
app.config['SQLALCHEMY_DATABASE_URI'] = uri
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
db = SQLAlchemy(app)


def now_utc():
    return datetime.now(timezone.utc)


class Site(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(24), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='operador')
    active = db.Column(db.Boolean, nullable=False, default=True)
    display_name = db.Column(db.String(120))
    site_id = db.Column(db.Integer, db.ForeignKey('site.id'))
    site = db.relationship('Site')


class Damage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    product = db.Column(db.String(160), nullable=False)
    sku = db.Column(db.String(80), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    value = db.Column(db.Numeric(12, 2), nullable=False)
    stage = db.Column(db.String(60), nullable=False)
    shift = db.Column(db.String(30), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=False, default='')
    status = db.Column(db.String(30), nullable=False, default='Aberta')
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    creator = db.relationship('User', foreign_keys=[creator_id])
    site_id = db.Column(db.Integer, db.ForeignKey('site.id'))
    site = db.relationship('Site')
    sector = db.Column(db.String(100))
    category = db.Column(db.String(80))
    cause = db.Column(db.String(80))
    responsible_name = db.Column(db.String(120))
    lot = db.Column(db.String(80))
    expiry_date = db.Column(db.Date)
    severity = db.Column(db.String(20))
    destination = db.Column(db.String(80))
    recovery_value = db.Column(db.Numeric(12, 2), default=0)
    updated_at = db.Column(db.DateTime(timezone=True))
    approved_at = db.Column(db.DateTime(timezone=True))
    approved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    approved_by = db.relationship('User', foreign_keys=[approved_by_id])
    resolved_at = db.Column(db.DateTime(timezone=True))


class DamagePhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    damage_id = db.Column(db.Integer, db.ForeignKey('damage.id', ondelete='CASCADE'), nullable=False, index=True)
    name = db.Column(db.String(180), nullable=False)
    mime_type = db.Column(db.String(60), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    damage = db.relationship('Damage', backref=db.backref('photos', lazy='select'))


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    damage_id = db.Column(db.Integer, db.ForeignKey('damage.id'), index=True)
    action = db.Column(db.String(60), nullable=False)
    detail = db.Column(db.String(600), nullable=False, default='')
    actor = db.relationship('User')


class LoginFailure(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key_hash = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now_utc)


# Additive migrations for the CBA 1.x tables. Never drop tables/data automatically.
# Production changes beyond this initial upgrade should be managed by Alembic.
ADDITIONS = {
    'user': {
        'display_name': 'VARCHAR(120)', 'site_id': 'INTEGER',
    },
    'damage': {
        'site_id': 'INTEGER', 'sector': 'VARCHAR(100)', 'category': 'VARCHAR(80)',
        'cause': 'VARCHAR(80)', 'responsible_name': 'VARCHAR(120)',
        'lot': 'VARCHAR(80)', 'expiry_date': 'DATE', 'severity': 'VARCHAR(20)',
        'destination': 'VARCHAR(80)', 'recovery_value': 'NUMERIC(12,2) DEFAULT 0',
        'updated_at': 'TIMESTAMP WITH TIME ZONE',
        'approved_at': 'TIMESTAMP WITH TIME ZONE',
        'approved_by_id': 'INTEGER', 'resolved_at': 'TIMESTAMP WITH TIME ZONE',
    },
}


def initialize():
    # SQLite only used by isolated automated tests; deployed application requires Postgres.
    with db.engine.begin() as connection:
        inspector = inspect(connection)
        for table, columns in ADDITIONS.items():
            if table not in inspector.get_table_names():
                continue
            existing = {c['name'] for c in inspector.get_columns(table)}
            for column, definition in columns.items():
                if column not in existing:
                    if connection.dialect.name == 'sqlite':
                        definition = definition.replace('TIMESTAMP WITH TIME ZONE', 'DATETIME')
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'))
    db.create_all()
    site = db.session.execute(db.select(Site).where(Site.code == 'PILOTO')).scalar_one_or_none()
    if not site:
        site = Site(code='PILOTO', name='CD Demonstração (dados fictícios)')
        db.session.add(site)
        db.session.flush()
    admin = db.session.execute(db.select(User).where(User.username == 'admin')).scalar_one_or_none()
    if not admin:
        password = os.getenv('ADMIN_PASSWORD')
        if not password or len(password) < 12:
            raise RuntimeError('ADMIN_PASSWORD com pelo menos 12 caracteres é obrigatório para o primeiro acesso.')
        db.session.add(User(username='admin', display_name='Administrador',
                            password_hash=generate_password_hash(password), role='admin', site_id=site.id))
    else:
        if admin.site_id is None:
            admin.site_id = site.id
        if admin.role == 'gestor':
            admin.role = 'admin'  # Preserve legacy V1 superuser permissions.
    # Historical V1 occurrences were not associated with a distribution center.
    # Assign them to the pilot site once, avoiding cross-site visibility.
    db.session.execute(db.update(Damage).where(Damage.site_id.is_(None)).values(site_id=site.id))
    db.session.commit()


with app.app_context():
    initialize()


def current_user():
    uid = session.get('uid')
    if not uid:
        return None
    user = db.session.get(User, uid)
    return user if user and user.active else None


def require_login(fn):
    @wraps(fn)
    def decorated(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login', next=request.path))
        return fn(*args, **kwargs)
    return decorated


def require_role(*roles):
    def wrap(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for('login'))
            if user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return decorated
    return wrap


def may_view(user, d):
    if user.role == 'admin':
        return True
    if user.role == 'operador':
        return d.creator_id == user.id
    return user.site_id is not None and d.site_id == user.site_id


def visible_query(user):
    query = db.select(Damage)
    if user.role == 'operador':
        return query.where(Damage.creator_id == user.id)
    if user.role != 'admin':
        return query.where(Damage.site_id == user.site_id)
    return query


def get_visible_damage(did):
    d = db.get_or_404(Damage, did)
    if not may_view(current_user(), d):
        abort(403)
    return d


def audit(action, damage=None, detail=''):
    u = current_user()
    db.session.add(AuditLog(actor_id=u.id if u else None,
                            damage_id=damage.id if damage else None,
                            action=action, detail=str(detail)[:600]))


@app.before_request
def csrf_protection():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        expected = session.get('csrf_token')
        provided = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        if not expected or not provided or not secrets.compare_digest(expected, provided):
            abort(403, 'Token CSRF inválido. Atualize a página e tente novamente.')


@app.context_processor
def template_globals():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return {'csrf_token': session['csrf_token'], 'viewer': current_user(),
            'version': VERSION, 'status_names': STATUSES, 'causes': CAUSES,
            'stages': STAGES, 'shifts': SHIFTS, 'destinations': DESTINATIONS,
            'severities': SEVERITIES}


@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
    response.headers['Cache-Control'] = 'no-store'
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


@app.get('/health')
def health():
    try:
        db.session.execute(text('SELECT 1')).scalar_one()
        return {'status': 'ok', 'version': VERSION}
    except Exception:
        db.session.rollback()
        return {'status': 'unavailable', 'version': VERSION}, 503


def login_key(username):
    ip = request.remote_addr or 'unknown'
    value = f'{ip}|{username.lower()}|{app.config["SECRET_KEY"]}'
    return hashlib.sha256(value.encode()).hexdigest()


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user():
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        key = login_key(username)
        window = now_utc() - timedelta(minutes=15)
        db.session.execute(db.delete(LoginFailure).where(
            LoginFailure.created_at < now_utc() - timedelta(days=1)))
        failures = db.session.scalar(db.select(func.count()).select_from(LoginFailure).where(
            LoginFailure.key_hash == key, LoginFailure.created_at >= window)) or 0
        if failures >= 5:
            flash('Muitas tentativas. Aguarde 15 minutos e tente novamente.', 'error')
            return render_template('login.html'), 429
        user = db.session.execute(db.select(User).where(User.username == username)).scalar_one_or_none()
        if user and user.active and check_password_hash(user.password_hash, password):
            db.session.execute(db.delete(LoginFailure).where(LoginFailure.key_hash == key))
            db.session.commit()
            session.clear()
            session['uid'] = user.id
            session.permanent = True
            return redirect(url_for('dashboard'))
        db.session.add(LoginFailure(key_hash=key))
        db.session.commit()
        flash('Usuário ou senha inválidos.', 'error')
    return render_template('login.html')


@app.post('/logout')
@require_login
def logout():
    session.clear()
    return redirect(url_for('login'))


def filter_query(query):
    status = request.args.get('status', '')
    stage = request.args.get('stage', '')
    cause = request.args.get('cause', '')
    sku = request.args.get('sku', '').strip()[:80]
    site = request.args.get('site', '').strip()
    start = request.args.get('start', '').strip()
    end = request.args.get('end', '').strip()
    if status in STATUSES:
        query = query.where(Damage.status == status)
    if stage in STAGES:
        query = query.where(Damage.stage == stage)
    if cause in CAUSES:
        query = query.where(Damage.cause == cause)
    if sku:
        query = query.where(Damage.sku.ilike(f'%{sku}%'))
    if site.isdecimal():
        query = query.where(Damage.site_id == int(site))
    for key, raw in [('start', start), ('end', end)]:
        try:
            value = date.fromisoformat(raw)
            if key == 'start':
                query = query.where(Damage.created_at >= datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc))
            else:
                value += timedelta(days=1)
                query = query.where(Damage.created_at < datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc))
        except ValueError:
            pass
    return query


def aging(d):
    created = d.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now_utc() - created).total_seconds() > 48 * 3600 and d.status not in ('Concluída', 'Rejeitada')


@app.get('/')
@require_login
def dashboard():
    user = current_user()
    query = visible_query(user)
    records = db.session.execute(query.order_by(Damage.created_at.desc()).limit(500)).scalars().all()
    total = len(records)
    opened = sum(d.status not in ('Concluída', 'Rejeitada') for d in records)
    overdue = sum(aging(d) for d in records)
    losses = sum((d.value or Decimal('0')) for d in records)
    recovered = sum((d.recovery_value or Decimal('0')) for d in records)
    stages = sorted([(s, sum(1 for d in records if d.stage == s)) for s in STAGES],
                    key=lambda x: x[1], reverse=True)
    causes_count = sorted([(s, sum(1 for d in records if d.cause == s)) for s in CAUSES],
                          key=lambda x: x[1], reverse=True)[:5]
    return render_template('dashboard.html', records=records[:12], total=total, opened=opened,
                           overdue=overdue, losses=losses, recovered=recovered, stages=stages,
                           causes_count=causes_count, max_stage=max((v for _, v in stages), default=1) or 1)


@app.get('/damages')
@require_login
def damages():
    query = filter_query(visible_query(current_user()))
    total = db.session.scalar(db.select(func.count()).select_from(query.subquery())) or 0
    page = max(1, min(request.args.get('page', 1, type=int), 100000))
    rows = db.session.execute(query.order_by(Damage.created_at.desc()).offset((page-1)*30).limit(30)).scalars().all()
    sites = db.session.execute(db.select(Site).order_by(Site.name)).scalars().all()
    return render_template('damages.html', rows=rows, total=total, page=page, sites=sites)


def amount(raw, field='valor'):
    try:
        d = Decimal((raw or '').replace(',', '.'))
        if not d.is_finite() or d < 0 or d > Decimal('9999999999.99'):
            raise ValueError()
        return d.quantize(Decimal('0.01'))
    except (ValueError, InvalidOperation):
        raise ValueError(f'{field}: informe um valor válido.')


def parse_damage_form(is_new=True):
    product = request.form.get('product', '').strip()[:160]
    sku = request.form.get('sku', '').strip()[:80]
    qty = int(request.form.get('quantity', '0'))
    val = amount(request.form.get('value'), 'Valor total')
    stage = request.form.get('stage')
    shift = request.form.get('shift')
    location = request.form.get('location', '').strip()[:100]
    sector = request.form.get('sector', '').strip()[:100]
    responsible = request.form.get('responsible_name', '').strip()[:120]
    category = request.form.get('category')
    cause = request.form.get('cause')
    severity = request.form.get('severity')
    lot = request.form.get('lot', '').strip()[:80]
    raw_expiry = request.form.get('expiry_date', '').strip()
    expiry = date.fromisoformat(raw_expiry) if raw_expiry else None
    description = request.form.get('description', '').strip()[:500]
    requested_site = request.form.get('site_id', '').strip()
    user = current_user()
    site_id = user.site_id
    if user.role == 'admin' and requested_site.isdecimal():
        site_id = int(requested_site)
    if not (product and sku and location and sector and responsible and 0 < qty <= 1000000):
        raise ValueError('Preencha produto, SKU, quantidade, setor, local e responsável.')
    if stage not in STAGES or shift not in SHIFTS or category not in CATEGORIES or cause not in CAUSES or severity not in SEVERITIES:
        raise ValueError('Selecione os campos de classificação corretamente.')
    if not site_id or not db.session.get(Site, site_id):
        raise ValueError('Selecione um CD válido.')
    return dict(product=product, sku=sku, quantity=qty, value=val, stage=stage, shift=shift,
                location=location, description=description, site_id=site_id, sector=sector,
                responsible_name=responsible, category=category, cause=cause, severity=severity,
                lot=lot, expiry_date=expiry)


def extract_photos():
    uploaded = [f for f in request.files.getlist('photos') if f and f.filename]
    if len(uploaded) > 3:
        raise ValueError('Anexe no máximo 3 fotos por ocorrência.')
    photos = []
    for f in uploaded:
        try:
            image = Image.open(f.stream)
            if image.width * image.height > 16_000_000:
                raise ValueError('Imagem grande demais. Limite: 16 megapixels.')
            if image.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError('Foto inválida. Use JPG, PNG ou WebP.')
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1400, 1400))
            output = io.BytesIO()
            image.convert('RGB').save(output, 'JPEG', quality=78, optimize=True)
            data = output.getvalue()
            if len(data) > 1_500_000:
                raise ValueError('Foto muito grande. Reduza a resolução.')
            photos.append((f.filename[:180], data))
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValueError('Uma das fotos não pôde ser processada.')
    return photos


@app.route('/damages/new', methods=['GET', 'POST'])
@require_login
def new_damage():
    if request.method == 'POST':
        try:
            data = parse_damage_form()
            photos = extract_photos()
        except (ValueError, OverflowError) as exc:
            flash(str(exc), 'error')
            return redirect(url_for('new_damage'))
        damage = Damage(**data, creator_id=current_user().id, status='Aberta')
        db.session.add(damage)
        db.session.flush()
        for name, raw in photos:
            db.session.add(DamagePhoto(damage_id=damage.id, name=name, mime_type='image/jpeg',
                                       data=raw, uploader_id=current_user().id))
        audit('criação', damage, f'Nova avaria. {len(photos)} foto(s).')
        db.session.commit()
        flash(f'Avaria #{damage.id} registrada com sucesso.', 'success')
        return redirect(url_for('damage_detail', did=damage.id))
    sites = db.session.execute(db.select(Site).where(Site.active.is_(True)).order_by(Site.name)).scalars().all()
    return render_template('new_damage.html', sites=sites, categories=CATEGORIES)


@app.get('/damages/<int:did>')
@require_login
def damage_detail(did):
    d = get_visible_damage(did)
    history = db.session.execute(db.select(AuditLog).where(AuditLog.damage_id == did).order_by(AuditLog.created_at.desc())).scalars().all()
    allowed = []
    user = current_user()
    for step in sorted(TRANSITIONS.get(d.status, set()), key=STATUSES.index):
        if step in ('Aprovada', 'Rejeitada', 'Concluída') and user.role not in ('gestor', 'admin'):
            continue
        if user.role not in ('lider', 'gestor', 'admin'):
            continue
        allowed.append(step)
    return render_template('detail.html', d=d, history=history, allowed=allowed)


@app.get('/photos/<int:photo_id>')
@require_login
def photo(photo_id):
    p = db.get_or_404(DamagePhoto, photo_id)
    get_visible_damage(p.damage_id)
    return send_file(io.BytesIO(p.data), mimetype=p.mime_type, download_name=f'avaria-{p.damage_id}-{p.id}.jpg',
                     as_attachment=False, max_age=0)


@app.post('/damages/<int:did>/status')
@require_role('lider', 'gestor', 'admin')
def change_status(did):
    d = get_visible_damage(did)
    target = request.form.get('status', '')
    if target not in TRANSITIONS.get(d.status, set()):
        abort(400, 'Transição de status não permitida.')
    if target in ('Aprovada', 'Rejeitada', 'Concluída') and current_user().role not in ('gestor', 'admin'):
        abort(403)
    if target == 'Concluída' and not d.destination:
        flash('Informe a destinação do produto antes de concluir.', 'error')
        return redirect(url_for('damage_detail', did=did))
    old = d.status
    d.status = target
    d.updated_at = now_utc()
    if target == 'Aprovada':
        d.approved_at = now_utc()
        d.approved_by_id = current_user().id
    if target == 'Concluída':
        d.resolved_at = now_utc()
    audit('alteração de status', d, f'{old} → {target}. {request.form.get("notes", "").strip()[:250]}')
    db.session.commit()
    flash(f'Status atualizado para {target}.', 'success')
    return redirect(url_for('damage_detail', did=did))


@app.post('/damages/<int:did>/treatment')
@require_role('lider', 'gestor', 'admin')
def treatment(did):
    d = get_visible_damage(did)
    if d.status == 'Concluída':
        abort(400, 'Ocorrência concluída não pode ser alterada.')
    dest = request.form.get('destination', '')
    if dest not in DESTINATIONS:
        abort(400)
    try:
        recovered = amount(request.form.get('recovery_value', '0'), 'Valor recuperado')
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('damage_detail', did=did))
    if recovered > d.value:
        flash('Valor recuperado não pode superar o valor da avaria.', 'error')
        return redirect(url_for('damage_detail', did=did))
    d.destination = dest
    d.recovery_value = recovered
    d.updated_at = now_utc()
    audit('tratativa', d, f'Destinação: {dest or "pendente"}; recuperação: R$ {recovered}')
    db.session.commit()
    flash('Tratativa registrada.', 'success')
    return redirect(url_for('damage_detail', did=did))


@app.get('/damages/export.csv')
@require_login
def export_csv():
    records = db.session.execute(filter_query(visible_query(current_user())).order_by(Damage.created_at.desc()).limit(10000)).scalars().all()
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=';')
    writer.writerow(['ID','Data UTC','CD','Setor','Produto','SKU','Quantidade','Valor avaria','Valor recuperado',
                     'Turno','Etapa','Local','Tipo','Causa','Gravidade','Lote','Validade','Responsável','Destinação','Status'])
    def safe_csv(value):
        s = str(value or '')
        return "'" + s if s.startswith(('=', '+', '-', '@', '\t', '\r')) else s
    for d in records:
        writer.writerow([d.id, d.created_at.isoformat(), safe_csv(d.site.name if d.site else ''), safe_csv(d.sector),
                         safe_csv(d.product), safe_csv(d.sku), d.quantity, d.value, d.recovery_value or 0,
                         safe_csv(d.shift), safe_csv(d.stage), safe_csv(d.location), safe_csv(d.category),
                         safe_csv(d.cause), safe_csv(d.severity), safe_csv(d.lot), d.expiry_date or '',
                         safe_csv(d.responsible_name), safe_csv(d.destination), safe_csv(d.status)])
    data = '\ufeff' + stream.getvalue()
    return Response(data, mimetype='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename="cba_avarias.csv"'})


@app.route('/users', methods=['GET', 'POST'])
@require_role('gestor', 'admin')
def users():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        pwd = request.form.get('password', '')
        name = request.form.get('display_name', '').strip()[:120]
        role = request.form.get('role', '')
        site = request.form.get('site_id', '')
        site_id = int(site) if site.isdecimal() else current_user().site_id
        if current_user().role != 'admin':
            site_id = current_user().site_id
        if (not 3 <= len(username) <= 80 or not all(c.isascii() and (c.isalnum() or c in '._-') for c in username)
                or len(pwd) < 12 or role not in ROLES or not db.session.get(Site, site_id)):
            flash('Confira o usuário, o CD e a senha (mínimo 12 caracteres).', 'error')
            return redirect(url_for('users'))
        if role == 'admin' and current_user().role != 'admin':
            abort(403)
        if db.session.scalar(db.select(User.id).where(User.username == username)):
            flash('Esse usuário já existe.', 'error')
            return redirect(url_for('users'))
        db.session.add(User(username=username, password_hash=generate_password_hash(pwd), display_name=name,
                            site_id=site_id, role=role))
        audit('cadastro de usuário', detail=f'Usuário: {username}; perfil: {role}')
        db.session.commit()
        flash('Novo usuário cadastrado.', 'success')
        return redirect(url_for('users'))
    users_query = db.select(User).order_by(User.username)
    if current_user().role != 'admin' and current_user().site_id:
        users_query = users_query.where(User.site_id == current_user().site_id)
    records = db.session.execute(users_query).scalars().all()
    sites = db.session.execute(db.select(Site).order_by(Site.name)).scalars().all()
    return render_template('users.html', users=records, sites=sites, roles=('operador','lider','gestor','admin'))


@app.route('/sites', methods=['GET', 'POST'])
@require_role('admin')
def sites():
    if request.method == 'POST':
        code = request.form.get('code', '').strip().upper()[:24]
        name = request.form.get('name', '').strip()[:120]
        if not code or not name or not all(c.isascii() and (c.isalnum() or c in '-_') for c in code):
            flash('Informe um código e nome válidos.', 'error')
            return redirect(url_for('sites'))
        if db.session.scalar(db.select(Site.id).where(Site.code == code)):
            flash('Código já cadastrado.', 'error')
            return redirect(url_for('sites'))
        db.session.add(Site(code=code, name=name))
        audit('cadastro de CD', detail=f'{code}: {name}')
        db.session.commit()
        flash('CD cadastrado.', 'success')
        return redirect(url_for('sites'))
    records = db.session.execute(db.select(Site).order_by(Site.name)).scalars().all()
    return render_template('sites.html', sites=records)


@app.errorhandler(413)
def too_large(error):
    flash('Arquivo muito grande. Limite total: 11 MB.', 'error')
    return redirect(url_for('new_damage'))


if __name__ == '__main__':
    app.run(port=int(os.environ.get('PORT', '5000')))
