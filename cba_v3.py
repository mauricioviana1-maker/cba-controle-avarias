"""CBA 3.0 pilot features. Role/site-scoped views, deterministic analytics.

All forecast/assistant responses are descriptive/statistical, not generative AI.
No corporate data are sent to third parties.
"""
from __future__ import annotations

import csv
import io
import os
import re
import secrets
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import qrcode
from flask import (Response, abort, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from sqlalchemy import func

_CONTEXT = {}
_DEFAULT_SLA = {'Baixa': 72, 'Média': 48, 'Alta': 24, 'Crítica': 8}


def _time(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now():
    return datetime.now(timezone.utc)


def get_sla_status(d):
    """Resolve SLA from a per-site policy, handling naive legacy timestamps."""
    g = _CONTEXT
    policy = g['db'].session.scalar(g['db'].select(g['SlaPolicy'].hours).where(
        g['SlaPolicy'].site_id == d.site_id, g['SlaPolicy'].severity == d.severity)) if d.site_id else None
    hours = policy or _DEFAULT_SLA.get(d.severity, 48)
    due = _time(d.created_at) + timedelta(hours=hours)
    checkpoint = _time(d.resolved_at) if d.status == 'Concluída' and d.resolved_at else _now()
    late = checkpoint > due
    return {'hours': hours, 'due': due, 'late': late,
            'label': ('Concluída fora do prazo' if late else 'Concluída no prazo') if d.status == 'Concluída' else
                     ('Atrasada' if late else 'Dentro do prazo'),
            'remaining_hours': round((due - checkpoint).total_seconds()/3600, 1)}


def register_v3_routes(g):
    _CONTEXT.update(g)
    app, db = g['app'], g['db']
    Damage, ActionPlan, User, Site, SlaPolicy = (g[k] for k in ('Damage','ActionPlan','User','Site','SlaPolicy'))
    ReportSchedule, ReportRun = g['ReportSchedule'], g['ReportRun']
    current_user, require_login, require_role = g['current_user'], g['require_login'], g['require_role']
    visible_query, get_visible_damage, audit = g['visible_query'], g['get_visible_damage'], g['audit']
    amount = g['amount']

    def accessible_sites():
        u = current_user()
        if u.role == 'admin':
            return db.session.execute(db.select(Site).order_by(Site.name)).scalars().all()
        return db.session.execute(db.select(Site).where(Site.id == u.site_id)).scalars().all()

    def scoped_site(raw):
        u = current_user()
        if u.role != 'admin':
            if not u.site_id:
                abort(403)
            return u.site_id
        if str(raw).isdecimal() and db.session.get(Site, int(raw)):
            return int(raw)
        abort(400, 'Selecione um CD válido.')

    def history(days=30, before=None, after=None, *, limit=20000):
        """Recent site-scoped records; limit declared in UI. Dates are UTC."""
        stop = before or _now()
        start = after or (stop - timedelta(days=days))
        q = visible_query(current_user()).where(Damage.created_at >= start, Damage.created_at < stop)
        total = db.session.scalar(db.select(func.count()).select_from(q.subquery())) or 0
        rows = db.session.execute(q.order_by(Damage.created_at.desc()).limit(limit)).scalars().all()
        return rows, total

    def loss(d):
        return max(Decimal('0'), (d.value or Decimal('0')) - (d.recovery_value or Decimal('0')))

    @app.get('/analytics')
    @require_role('lider','gestor','admin')
    def analytics():
        days = request.args.get('days', 30, type=int)
        if days not in (7,30,90,180,365):
            days = 30
        now = _now()
        begin = now - timedelta(days=days)
        previous_begin = begin - timedelta(days=days)
        rows, total = history(before=now, after=begin)
        prev, prior_count = history(before=begin, after=previous_begin)
        grouped = defaultdict(lambda: [Decimal(0),0])
        recurrence = defaultdict(lambda: [0, Decimal(0)])
        for d in rows:
            group = d.cause or 'Não classificada'
            grouped[group][0] += loss(d)
            grouped[group][1] += 1
            recurrence[(d.sku, d.sector or 'Não informado', group)][0] += 1
            recurrence[(d.sku, d.sector or 'Não informado', group)][1] += loss(d)
        pareto = sorted([{'name': k, 'loss': v[0], 'count': v[1]} for k,v in grouped.items()],
                        key=lambda x:x['loss'],reverse=True)
        grand = sum((x['loss'] for x in pareto), Decimal(0))
        cumulative = Decimal(0)
        for p in pareto:
            cumulative += p['loss']
            p['percent'] = round(float(p['loss']/grand*100),1) if grand else 0
            p['cumulative'] = round(float(cumulative/grand*100),1) if grand else 0
        repeat = sorted([{'sku':k[0], 'sector':k[1], 'cause':k[2], 'count':v[0], 'loss':v[1]}
                         for k,v in recurrence.items() if v[0]>=2],
                        key=lambda x:(x['count'],x['loss']),reverse=True)[:15]
        curr_loss = sum((loss(d) for d in rows), Decimal(0))
        prev_loss = sum((loss(d) for d in prev), Decimal(0))
        delta = round(float((curr_loss-prev_loss)/prev_loss*100),1) if prev_loss else None
        return render_template('analytics.html',days=days,pareto=pareto,repeat=repeat,
                               total=total,prior_count=prior_count,curr_loss=curr_loss,
                               prev_loss=prev_loss,change=delta,partial=total>len(rows) or prior_count>len(prev))

    @app.get('/sla')
    @require_role('lider','gestor','admin')
    def sla():
        u = current_user()
        rows,total = history(days=365)
        entries = [{'record':d,'sla':get_sla_status(d)} for d in rows]
        entries = sorted(entries,key=lambda x:(not x['sla']['late'],x['sla']['due']))
        policies = db.session.execute(db.select(SlaPolicy).where(SlaPolicy.site_id == u.site_id)).scalars().all() if u.site_id else []
        return render_template('sla.html',entries=entries[:100],total=total,late=sum(x['sla']['late'] for x in entries),
                               policies={p.severity:p.hours for p in policies},defaults=_DEFAULT_SLA,
                               sites=accessible_sites(),partial=total>len(rows))

    @app.post('/sla/policy')
    @require_role('gestor','admin')
    def sla_policy():
        sid = scoped_site(request.form.get('site_id'))
        severity=request.form.get('severity')
        h=request.form.get('hours','')
        if severity not in _DEFAULT_SLA or not h.isdecimal() or not 1 <= int(h) <= 720:
            abort(400)
        p=db.session.scalar(db.select(SlaPolicy).where(SlaPolicy.site_id==sid,SlaPolicy.severity==severity))
        if p:
            p.hours=int(h)
        else:
            db.session.add(SlaPolicy(site_id=sid,severity=severity,hours=int(h)))
        audit('configuração SLA',detail=f'CD {sid}; {severity}: {h}h')
        db.session.commit()
        flash('Prazo de SLA atualizado.','success')
        return redirect(url_for('sla'))

    @app.route('/plans',methods=['GET','POST'])
    @require_role('lider','gestor','admin')
    def plans():
        if request.method == 'POST':
            did=request.form.get('damage_id','')
            if not did.isdecimal():
                abort(400)
            d=get_visible_damage(int(did))
            if d.status=='Concluída':
                flash('Não crie plano para ocorrência já concluída.','error')
                return redirect(url_for('plans'))
            values = {k:request.form.get(k,'').strip() for k in ('what','why','where','when_due','who','how','how_much')}
            try:
                due=date.fromisoformat(values['when_due'])
                cost=amount(values['how_much'] or '0','Custo previsto')
                if any(not values[k] for k in ('what','why','where','who','how')) or len(values['what'])>250 or len(values['why'])>250 or len(values['where'])>150 or len(values['who'])>120 or len(values['how'])>500:
                    raise ValueError('Preencha os sete campos do plano 5W2H.')
            except ValueError as exc:
                flash(str(exc),'error')
                return redirect(url_for('plans',damage_id=did))
            db.session.add(ActionPlan(site_id=d.site_id,damage_id=d.id,created_by_id=current_user().id,
                                      what=values['what'],why=values['why'],where=values['where'],
                                      when_due=due,who=values['who'],how=values['how'],how_much=cost))
            audit('plano 5W2H',d,f'Plano de ação: {values["what"][:130]}')
            db.session.commit()
            flash('Plano de ação registrado.','success')
            return redirect(url_for('plans'))
        q=db.select(ActionPlan).join(Damage,ActionPlan.damage_id==Damage.id)
        u=current_user()
        if u.role != 'admin':
            q=q.where(ActionPlan.site_id==u.site_id)
        rows=db.session.execute(q.order_by(ActionPlan.created_at.desc()).limit(500)).scalars().all()
        return render_template('plans.html',plans=rows,damage_id=request.args.get('damage_id',''),today=date.today())

    @app.post('/plans/<int:pid>/status')
    @require_role('lider','gestor','admin')
    def plan_status(pid):
        p=db.get_or_404(ActionPlan,pid)
        get_visible_damage(p.damage_id)
        status=request.form.get('status')
        if status not in ('Pendente','Em andamento','Concluído'):
            abort(400)
        p.status=status
        p.completed_at=_now() if status=='Concluído' else None
        audit('plano 5W2H',p.damage,f'Ação #{p.id}: {status}')
        db.session.commit()
        flash('Plano atualizado.','success')
        return redirect(url_for('plans'))

    def report_csv(site_id, days):
        end=_now()
        begin=end-timedelta(days=days)
        data=db.session.execute(db.select(Damage).where(Damage.site_id==site_id,
               Damage.created_at>=begin,Damage.created_at<end).order_by(Damage.created_at.desc()).limit(20000)).scalars().all()
        o=io.StringIO()
        writer=csv.writer(o,delimiter=';')
        writer.writerow(['ID','Data UTC','SKU','Produto','Causa','Setor','Status','Valor bruto','Valor recuperado','Perda líquida'])
        def clean(s):
            s=str(s or '')
            return "'"+s if s.startswith(('=','+','-','@','\t','\r')) else s
        for d in data:
            writer.writerow([d.id,d.created_at.isoformat(),clean(d.sku),clean(d.product),clean(d.cause),
                             clean(d.sector),clean(d.status),str(d.value),str(d.recovery_value or 0),str(loss(d))])
        return ('\ufeff'+o.getvalue()).encode('utf-8'),len(data)

    def run_due_reports(site_id=None,force=False):
        now=_now()
        q=db.select(ReportSchedule).where(ReportSchedule.active.is_(True))
        if site_id:
            q=q.where(ReportSchedule.site_id==site_id)
        schedules=db.session.execute(q).scalars().all()
        generated=[]
        days_by_freq={'diário':1,'semanal':7,'mensal':30}
        for schedule in schedules:
            days=days_by_freq[schedule.frequency]
            past=_time(schedule.last_run_at)
            if not force and past and now-past<timedelta(days=days):
                continue
            content,count=report_csv(schedule.site_id,days)
            db.session.add(ReportRun(schedule_id=schedule.id,site_id=schedule.site_id,rows_count=count,content=content))
            schedule.last_run_at=now
            generated.append(schedule.id)
        db.session.commit()
        return generated

    @app.route('/reports',methods=['GET','POST'])
    @require_role('gestor','admin')
    def reports():
        if request.method=='POST':
            sid=scoped_site(request.form.get('site_id'))
            freq=request.form.get('frequency')
            name=request.form.get('name','').strip()[:120]
            if freq not in ('diário','semanal','mensal') or not name:
                abort(400)
            db.session.add(ReportSchedule(site_id=sid,frequency=freq,name=name,created_by_id=current_user().id))
            audit('agenda de relatórios',detail=f'{name} ({freq}) / CD {sid}')
            db.session.commit()
            flash('Agenda cadastrada. Gere o primeiro relatório ou configure o agendamento automático.','success')
            return redirect(url_for('reports'))
        u=current_user()
        q=db.select(ReportSchedule)
        runs=db.select(ReportRun)
        if u.role != 'admin':
            q=q.where(ReportSchedule.site_id==u.site_id)
            runs=runs.where(ReportRun.site_id==u.site_id)
        return render_template('reports.html',schedules=db.session.execute(q.order_by(ReportSchedule.id.desc())).scalars().all(),
                               runs=db.session.execute(runs.order_by(ReportRun.created_at.desc()).limit(50)).scalars().all(),
                               sites=accessible_sites())

    @app.post('/reports/run')
    @require_role('gestor','admin')
    def reports_run():
        sid=None if current_user().role=='admin' else current_user().site_id
        run_due_reports(site_id=sid,force=True)
        flash('Relatórios CSV gerados e disponíveis para consulta.','success')
        return redirect(url_for('reports'))

    @app.post('/reports/<int:sid>/toggle')
    @require_role('gestor','admin')
    def reports_toggle(sid):
        schedule=db.get_or_404(ReportSchedule,sid)
        if current_user().role!='admin' and schedule.site_id != current_user().site_id:
            abort(403)
        schedule.active = not schedule.active
        db.session.commit()
        return redirect(url_for('reports'))

    @app.get('/reports/download/<int:rid>')
    @require_role('gestor','admin')
    def report_download(rid):
        run=db.get_or_404(ReportRun,rid)
        if current_user().role!='admin' and run.site_id!=current_user().site_id:
            abort(403)
        return send_file(io.BytesIO(run.content),mimetype='text/csv; charset=utf-8',as_attachment=True,
                         download_name=f'cba_relatorio_{run.id}.csv')

    @app.post('/jobs/reports/run')
    def scheduled_report_job():
        expected=os.getenv('REPORT_JOB_TOKEN','')
        supplied=request.headers.get('Authorization','')
        if len(expected)<32 or not secrets.compare_digest(supplied, 'Bearer '+expected):
            abort(401)
        created=run_due_reports()
        return jsonify(status='ok',generated=len(created))

    @app.get('/damages/<int:did>/qr.png')
    @require_login
    def damage_qr(did):
        get_visible_damage(did)
        # QR carries a URL only, never a secret or unrestricted access.
        target=url_for('damage_detail',did=did,_external=True)
        img=qrcode.make(target)
        output=io.BytesIO()
        img.save(output,format='PNG')
        output.seek(0)
        return send_file(output,mimetype='image/png',max_age=0)

    @app.get('/api/session')
    def api_session():
        u=current_user()
        if not u:
            return jsonify(authenticated=False),401
        if 'csrf_token' not in session:
            session['csrf_token']=secrets.token_urlsafe(32)
        return jsonify(authenticated=True,csrf_token=session['csrf_token'],username=u.username)

    @app.post('/api/offline/damages')
    @require_login
    def sync_offline_damage():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):
            return jsonify(error='Envie um objeto JSON.'),400
        identifier=str(data.get('offline_key',''))
        try:
            identifier=UUID(identifier,version=4).hex
        except (ValueError,AttributeError):
            return jsonify(error='Identificador offline inválido.'),400
        old=db.session.scalar(db.select(Damage).where(Damage.offline_key==identifier))
        if old:
            if old.creator_id != current_user().id:
                abort(409)
            return jsonify(status='already_saved',id=old.id),200
        # The offline client does not upload photos. It uses same validation as online form.
        try:
            payload={k:str(v) for k,v in data.items() if isinstance(v,(str,int,float))}
            fields=g['parse_damage_form'](source=payload)
        except (ValueError, OverflowError) as exc:
            return jsonify(error=str(exc)),400
        d=Damage(**fields,creator_id=current_user().id,status='Aberta',offline_key=identifier)
        db.session.add(d)
        db.session.flush()
        audit('criação offline',d,'Sincronização de lançamento offline, sem fotos.')
        db.session.commit()
        return jsonify(status='created',id=d.id),201

    @app.get('/sw.js')
    def service_worker():
        res=send_file(os.path.join(app.static_folder,'sw.js'),mimetype='text/javascript')
        res.headers['Service-Worker-Allowed']='/'
        return res

    def weekly_forecast(rows):
        if not rows:
            return None
        dates=sorted(_time(d.created_at) for d in rows)
        oldest=dates[0]
        monday=_now().date()-timedelta(days=_now().weekday())
        complete_weeks=int((monday-oldest.date()).days/7)
        if complete_weeks<8:
            return None
        series=[]
        for n in range(min(12,complete_weeks),0,-1):
            week_begin=monday-timedelta(weeks=n)
            week_end=week_begin+timedelta(days=7)
            series.append({'week':week_begin.isoformat(), 'amount':round(float(sum(
                (loss(d) for d in rows if week_begin<=_time(d.created_at).date()<week_end), Decimal(0))),2)})
        if len(series)<8:
            return None
        values=[entry['amount'] for entry in series]
        base=statistics.mean(values[-4:])
        # Transparent dispersion band, not a probabilistic confidence interval.
        spread=statistics.pstdev(values[-8:]) if len(values)>=8 else 0
        low=max(0,round(base-spread,2))
        high=round(base+spread,2)
        return {'weeks':series,'estimate':round(base,2),'lower':low,'upper':high,
                'method':'Média das últimas 4 semanas concluídas; faixa ilustrativa ±1 desvio-padrão das últimas 8 semanas.'}

    @app.get('/forecast')
    @require_role('gestor','admin')
    def forecast():
        rows,total=history(days=365,limit=20000)
        estimate=weekly_forecast(rows) if total<=len(rows) else None
        return render_template('forecast.html',forecast=estimate,count=total,partial=total>len(rows))

    def normalize(s):
        return ''.join(c for c in unicodedata.normalize('NFKD',s.lower()) if not unicodedata.combining(c))

    def assistant_answer(raw):
        q=normalize(raw)
        rows,total=history(days=30,limit=20000)
        if total>len(rows):
            return 'Há mais de 20.000 registros no período; use o relatório completo antes de concluir a análise.'
        if not rows:
            return 'Não há registros no seu escopo nos últimos 30 dias.'
        if 'sla' in q or 'atras' in q:
            late=[d for d in rows if get_sla_status(d)['late'] and d.status!='Concluída']
            return f'Nos últimos 30 dias, {len(late)} ocorrências ainda abertas estão fora do SLA. Consulte a página SLA para os protocolos.'
        if 'reincid' in q or 'repetid' in q:
            counts=Counter(d.sku for d in rows)
            tops=[(k,v) for k,v in counts.most_common(5) if v>=2]
            return 'SKUs reincidentes em 30 dias: '+('; '.join(f'{k}: {v} ocorrências' for k,v in tops) if tops else 'nenhum SKU com 2 ou mais ocorrências.')
        if 'penden' in q or 'abert' in q:
            count=sum(d.status not in ('Concluída','Rejeitada') for d in rows)
            return f'Existem {count} ocorrências em andamento ou aguardando tratamento, entre {len(rows)} registradas nos últimos 30 dias.'
        if 'sku' in q or 'produto' in q:
            totals=defaultdict(Decimal)
            for d in rows: totals[d.sku]+=loss(d)
            sku,amount_value=max(totals.items(),key=lambda x:x[1])
            return f'O SKU com maior perda líquida nos últimos 30 dias é {sku}, com R$ {amount_value:.2f}.'
        if 'setor' in q or 'area' in q:
            totals=defaultdict(Decimal)
            for d in rows: totals[d.sector or 'Não informado']+=loss(d)
            sector,amount_value=max(totals.items(),key=lambda x:x[1])
            return f'O setor com maior perda líquida nos últimos 30 dias é {sector}, com R$ {amount_value:.2f}.'
        if 'causa' in q or 'motivo' in q:
            totals=defaultdict(Decimal)
            for d in rows: totals[d.cause or 'Não classificada']+=loss(d)
            cause,amount_value=max(totals.items(),key=lambda x:x[1])
            return f'A causa com maior perda líquida nos últimos 30 dias é {cause}, com R$ {amount_value:.2f}.'
        if 'previs' in q or 'projec' in q:
            forecast_data=weekly_forecast(rows)
            if not forecast_data:
                return 'Histórico insuficiente para uma projeção: são necessárias pelo menos 8 semanas completas de dados.'
            return f'Projeção experimental para a próxima semana: R$ {forecast_data["estimate"]:.2f} (faixa descritiva de R$ {forecast_data["lower"]:.2f} a R$ {forecast_data["upper"]:.2f}).'
        if 'perda' in q or 'valor' in q or 'total' in q:
            total_value=sum((loss(d) for d in rows),Decimal(0))
            return f'A perda líquida dos últimos 30 dias foi R$ {total_value:.2f}, em {len(rows)} ocorrências no seu escopo de acesso.'
        return ('Posso responder sobre perda líquida, principal causa, pior SKU, setor, recorrência, '
                'pendências e SLA nos últimos 30 dias. Não tenho acesso a dados externos ou a um modelo generativo.')

    @app.route('/assistant',methods=['GET','POST'])
    @require_role('gestor','admin')
    def assistant():
        question=''
        answer=None
        if request.method=='POST':
            question=request.form.get('question','').strip()[:300]
            if question:
                answer=assistant_answer(question)
        return render_template('assistant.html',question=question,answer=answer)

    # Make helper callable by unit tests and authenticated job runners.
    app.extensions['cba_v3']={'reports_runner':run_due_reports,'analytics_history':history,'forecast':weekly_forecast}
