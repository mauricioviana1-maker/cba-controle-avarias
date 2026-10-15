import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from flask import Flask, flash, redirect, render_template, request, session, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

app = Flask(__name__)
secret = os.getenv('SECRET_KEY')
if not secret or len(secret) < 32:
    raise RuntimeError('SECRET_KEY must be set to a random secret of 32+ characters')
app.config['SECRET_KEY'] = secret
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV', 'production') == 'production'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600 * 8
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024

url = os.getenv('DATABASE_URL')
if not url:
    raise RuntimeError('DATABASE_URL is required')
if url.startswith('postgres://'):
    url = 'postgresql://' + url[len('postgres://'):]
if url.startswith('postgresql://'):
    url = 'postgresql+psycopg://' + url[len('postgresql://'):]
app.config['SQLALCHEMY_DATABASE_URI'] = url
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True}
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='operador')
    active = db.Column(db.Boolean, nullable=False, default=True)

class Damage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    product = db.Column(db.String(160), nullable=False)
    sku = db.Column(db.String(80), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    value = db.Column(db.Numeric(12, 2), nullable=False)
    stage = db.Column(db.String(60), nullable=False)
    shift = db.Column(db.String(30), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500), nullable=False, default='')
    status = db.Column(db.String(20), nullable=False, default='Aberta')
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    creator = db.relationship('User')

with app.app_context():
    db.create_all()
    admin = db.session.execute(db.select(User).where(User.username == 'admin')).scalar_one_or_none()
    if not admin:
        p = os.getenv('ADMIN_PASSWORD')
        if not p or len(p) < 12:
            raise RuntimeError('ADMIN_PASSWORD needs at least 12 characters to initialize admin')
        db.session.add(User(username='admin',password_hash=generate_password_hash(p), role='gestor'))
        db.session.commit()

@app.before_request
def csrf_protection():
    if request.method in ('POST','PUT','PATCH','DELETE'):
        token = session.get('csrf_token')
        supplied = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
        if not token or not supplied or not secrets.compare_digest(token, supplied):
            abort(403, 'Token de segurança inválido. Atualize a página e tente novamente.')

@app.context_processor
def inject_csrf():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return {'csrf_token': session['csrf_token']}

@app.after_request
def response_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    response.headers['Cache-Control'] = 'no-store'
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

def current_user():
    if not session.get('uid'):
        return None
    return db.session.get(User, session['uid'])

def login_required(fn):
    @wraps(fn)
    def decorated(*a, **kw):
        if not current_user():
            return redirect(url_for('login'))
        return fn(*a, **kw)
    return decorated

def manager_required(fn):
    @wraps(fn)
    def decorated(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for('login'))
        if u.role != 'gestor':
            abort(403)
        return fn(*a, **kw)
    return decorated

# In-memory throttling is intentionally NOT used in production; implement shared
# rate limiting (Redis/WAF) before exposing this pilot to untrusted users.

@app.get('/health')
def health():
    return {'status':'ok'}

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        user = db.session.execute(db.select(User).where(User.username == request.form.get('username','').strip())).scalar_one_or_none()
        if user and user.active and check_password_hash(user.password_hash,request.form.get('password','')):
            session.clear()
            session['uid'] = user.id
            session.permanent = True
            return redirect(url_for('index'))
        time.sleep(.4)
        flash('Usuário ou senha inválidos.')
    return render_template('login.html')

@app.post('/logout')
@login_required
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.get('/')
@login_required
def index():
    user = current_user()
    damages = db.session.execute(db.select(Damage).order_by(Damage.created_at.desc()).limit(200)).scalars().all()
    total = db.session.scalar(db.select(func.count()).select_from(Damage)) or 0
    opened = db.session.scalar(db.select(func.count()).select_from(Damage).where(Damage.status=='Aberta')) or 0
    value = db.session.scalar(db.select(func.coalesce(func.sum(Damage.value),0))) or Decimal('0')
    return render_template('index.html',user=user,damages=damages,total=total,opened=opened,value=value)

@app.post('/damages')
@login_required
def add_damage():
    try:
        product = request.form['product'].strip()[:160]
        sku = request.form['sku'].strip()[:80]
        quantity = int(request.form['quantity'])
        value = Decimal(request.form['value'].replace(',','.'))
        stage = request.form['stage']
        shift = request.form['shift']
        location = request.form['location'].strip()[:100]
        desc = request.form.get('description','').strip()[:500]
        if not (product and sku and location and quantity > 0 and Decimal('0') <= value <= Decimal('9999999999.99')):
            raise ValueError()
        if stage not in {'Recebimento','Armazenagem','Separação','Expedição','Transporte','Outros'} or shift not in {'1º turno','2º turno','3º turno'}:
            raise ValueError()
    except (ValueError,KeyError,InvalidOperation):
        flash('Confira os campos e os valores informados.')
        return redirect(url_for('index'))
    db.session.add(Damage(product=product,sku=sku,quantity=quantity,value=value,stage=stage,shift=shift,location=location,description=desc,creator_id=current_user().id))
    db.session.commit()
    flash('Avaria registrada com sucesso.')
    return redirect(url_for('index'))

@app.post('/damages/<int:damage_id>/status')
@manager_required
def set_status(damage_id):
    item = db.get_or_404(Damage,damage_id)
    status = request.form.get('status')
    if status not in {'Aberta','Em análise','Concluída'}:
        abort(400)
    item.status = status
    db.session.commit()
    flash('Status atualizado.')
    return redirect(url_for('index'))

@app.post('/users')
@manager_required
def create_user():
    name = request.form.get('username','').strip().lower()
    pwd = request.form.get('password','')
    role = request.form.get('role')
    if not (3 <= len(name) <= 80 and all(c.isascii() and (c.isalnum() or c in '_-.') for c in name) and len(pwd) >= 12 and role in {'operador','gestor'}):
        flash('Dados inválidos. Senha: mínimo 12 caracteres.')
        return redirect(url_for('index'))
    if db.session.execute(db.select(User).where(User.username==name)).scalar_one_or_none():
        flash('Usuário já existe.')
        return redirect(url_for('index'))
    db.session.add(User(username=name,password_hash=generate_password_hash(pwd),role=role))
    db.session.commit()
    flash('Usuário criado.')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(port=5000)
