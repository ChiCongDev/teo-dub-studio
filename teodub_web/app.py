from datetime import timedelta
import json
from pathlib import Path
import secrets
import uuid

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import SecurityError

from teodub.ai_models import CHOICES, NEW_MODELS, TURBO_VOICES, NANO_VOICES, local_voices
from teodub.speech_audio import VOICE_SPEEDS
from teodub_web.form_settings import MODEL_LABELS, model_labels
from teodub.speech_plan import STYLES
from teodub.subtitle_style import SUBTITLE_COLORS, SUBTITLE_WEIGHTS
from teodub.web_address import fixed_url
from urllib.parse import urlsplit
from teodub_web.runner import describe, validate_spec, verified_output
from teodub_web.source_clean import prepared_source, verified_source, saved_region, save_region


class CookieSession(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):
        return request.is_secure


def create_app(store, settings):
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.update(SECRET_KEY=settings['secret'], MAX_CONTENT_LENGTH=16384,
                      MAX_FORM_MEMORY_SIZE=16384, MAX_FORM_PARTS=25,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                      TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
    app.config['TEO_INSTANCE'] = uuid.uuid4().hex
    if settings.get('fixed_public_url'):
        app.config['TRUSTED_HOSTS'].append(urlsplit(fixed_url(settings['fixed_public_url'])).hostname)
    app.session_interface = CookieSession()
    # Origin is only reachable on loopback through the one local tunnel.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.extensions['teo_store'] = store
    from teodub_web.facebook_control import register_facebook
    register_facebook(app, store)

    @app.before_request
    def access():
        if isinstance(request.routing_exception, SecurityError):
            raise request.routing_exception
        g.user = store.user_for_token(session.get('sid'))
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        if request.method == 'POST':
            origin = request.headers.get('Origin')
            if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
                abort(403)
            token = request.headers.get('X-CSRF-Token') or request.form.get('csrf', '')
            if not secrets.compare_digest(str(token), session['csrf']):
                abort(403)
        if request.endpoint not in ('login', 'static', 'health') and not g.user:
            if request.path.startswith('/api/') or request.path.startswith('/files/'):
                abort(401)
            return redirect(url_for('login'))

    @app.after_request
    def headers(response):
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=()'})
        if request.is_secure:
            response.headers['Strict-Transport-Security'] = 'max-age=86400'
        return response

    @app.context_processor
    def context():
        return dict(user=getattr(g, 'user', None), csrf=session.get('csrf'),
                    fixed_address=bool(settings.get('fixed_public_url')),
                    address_state=settings.get('address_state', ''))

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def error(exc):
        code = exc.code
        message = {400: 'Yêu cầu chưa hợp lệ.', 401: 'Phiên đăng nhập đã hết hạn.',
                   403: 'Phiếu thao tác đã hết hạn. Tải lại trang rồi thử lại.',
                   404: 'Không tìm thấy công việc hoặc thành phẩm.', 413: 'Yêu cầu quá lớn.',
                   500: 'Chưa thực hiện được. Quản trị viên kiểm tra máy xử lý.'}[code]
        if request.path.startswith('/api/') or isinstance(exc, SecurityError):
            return jsonify(error=message), code
        return render_template('error.html', message=message), code

    @app.get('/health')
    def health():
        return jsonify(status='ok', instance=app.config['TEO_INSTANCE'])

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            try:
                token = store.authenticate(request.form.get('username', '').strip().lower(),
                                           request.form.get('password', ''), request.remote_addr)
                session.clear()
                session['sid'] = token
                session['csrf'] = secrets.token_urlsafe(32)
                session.permanent = True
                return redirect(url_for('home'))
            except ValueError as exc:
                flash(str(exc), 'error')
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        store.logout(session.get('sid', ''))
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    def home():
        models = store.model_policy()
        return render_template('home.html', submit_key=uuid.uuid4().hex, choices=CHOICES,
                               defaults=store.form_preferences(g.user['id'], models),
                               models=models, model_labels=model_labels(models), model_names=MODEL_LABELS,
                               voices=local_voices(models['tts_model']), voice_speeds=VOICE_SPEEDS, styles=STYLES,
                               colors=SUBTITLE_COLORS, weights=SUBTITLE_WEIGHTS,
                               public_url=settings.get('public_url', ''),
                               is_admin=g.user['role'] == 'admin')

    @app.get('/api/jobs')
    def jobs():
        models = store.model_policy()
        return jsonify(jobs=[dict(describe(store, job), can_clean=job['owner']==g.user['id']) for job in store.jobs(g.user)],
                       worker='Máy xử lý đang kết nối', voices={'v3turbo': TURBO_VOICES, 'v3nano': NANO_VOICES},
                       model_policy=models, model_labels=model_labels(models),
                       public_address=dict(url=settings.get('public_url', ''),
                         mode='fixed' if settings.get('fixed_public_url') else 'quick',
                         state=settings.get('address_state', 'online' if settings.get('public_url') else 'offline'))
                       if g.user['role'] == 'admin' else None)

    @app.post('/api/jobs')
    def submit():
        try:
            data = request.get_json()
            if not isinstance(data, dict):
                raise ValueError('Cấu hình chưa hợp lệ.')
            models = store.model_policy()
            if any(key in data and data[key] != value for key, value in models.items()):
                raise ValueError('Model AI do quản trị viên thiết lập. Làm mới trang để dùng cấu hình hiện tại.')
            effective = dict(store.form_preferences(g.user['id'], models))
            effective.update(data)
            effective.update(models)
            url, title, spec = validate_spec(effective)
            if spec.get('source_job_id'):
                prepared_source(store, g.user['id'], spec['source_job_id'], url)
            ident = store.submit(g.user['id'], title, url, spec, data.get('submit_key'))
            return jsonify(id=ident, submit_key=uuid.uuid4().hex), 201
        except (TypeError, ValueError, OSError, KeyError) as exc:
            if isinstance(exc, (OSError, KeyError)):
                return jsonify(error='Chưa đọc được video gốc đã tải. Hãy tải và khoanh vùng lại.'), 400
            return jsonify(error=str(exc)), 400

    @app.route('/api/preferences', methods=['GET', 'POST'])
    def personal_preferences():
        if request.method == 'GET':
            return jsonify(preferences=store.form_preferences(g.user['id']))
        try:
            return jsonify(preferences=store.save_preferences(g.user['id'], request.get_json()))
        except (TypeError, ValueError) as exc:
            return jsonify(error=str(exc)), 400
        except PermissionError:
            abort(401)

    @app.route('/ai-settings', methods=['GET', 'POST'])
    def ai_settings():
        if g.user['role'] != 'admin':
            abort(403)
        if request.method == 'POST':
            try:
                values = {key: request.form.get(key) for key in NEW_MODELS}
                store.save_model_policy(values, g.user['id'])
                flash('Đã lưu model AI. Các công việc mới sẽ dùng cấu hình này.', 'success')
                return redirect(url_for('ai_settings'))
            except ValueError as exc:
                flash(str(exc), 'error')
            except PermissionError:
                abort(403)
        return render_template('ai_settings.html', choices=CHOICES, models=store.model_policy(), model_names=MODEL_LABELS)

    @app.post('/api/jobs/<ident>/<action>')
    def action(ident, action):
        try:
            store.action(ident, action, g.user)
            return jsonify(ok=True)
        except LookupError:
            abort(404)
        except ValueError as exc:
            return jsonify(error=str(exc)), 409

    @app.get('/files/<ident>/<kind>')
    def files(ident, kind):
        job = store.job(ident, g.user)
        if not job:
            abort(404)
        try:
            path = verified_output(store, job, kind)
        except (FileNotFoundError, OSError, ValueError, TypeError):
            abort(404)
        return send_file(path, as_attachment=True, download_name=path.name,
                         mimetype='video/mp4' if kind == 'video' else 'application/x-subrip', conditional=True)

    @app.route('/api/jobs/<ident>/source', methods=['GET', 'POST'])
    def source_info(ident):
        job = store.job(ident, g.user)
        if not job:
            abort(404)
        if request.method == 'POST':
            if job['owner'] != g.user['id']:
                abort(404)
            try:
                return jsonify(region=save_region(store, job, request.get_json()))
            except (ValueError, TypeError) as exc:
                return jsonify(error=str(exc)), 400
            except (OSError, KeyError):
                return jsonify(error='Chưa lưu được vùng. Kiểm tra video gốc rồi thử lại.'), 400
        try:
            source = verified_source(store, job)
        except (OSError, ValueError, KeyError, TypeError):
            abort(404)
        return jsonify(id=ident, input=job['input'], title=job['title'],
                       width=source['width'], height=source['height'], duration_s=source['duration_s'],
                       url=url_for('source_video', ident=ident), region=saved_region(store, job, source))

    @app.get('/files/<ident>/source')
    def source_video(ident):
        job = store.job(ident, g.user)
        if not job:
            abort(404)
        try:
            source = verified_source(store, job)
        except (OSError, ValueError, KeyError, TypeError):
            abort(404)
        return send_file(source['path'], mimetype='video/mp4', as_attachment=False, conditional=True)

    @app.route('/account', methods=['GET', 'POST'])
    def account():
        if request.method == 'POST':
            password = request.form.get('password', '')
            if password != request.form.get('confirm'):
                flash('Hai mật khẩu chưa giống nhau.', 'error')
            else:
                try:
                    store.change_password(g.user['id'], password, actor=g.user['id'])
                    session.clear()
                    flash('Đã đổi mật khẩu. Đăng nhập lại bằng mật khẩu mới.', 'success')
                    return redirect(url_for('login'))
                except ValueError as exc:
                    flash(str(exc), 'error')
        return render_template('account.html')

    @app.route('/team', methods=['GET', 'POST'])
    def team():
        if g.user['role'] != 'admin':
            abort(403)
        created = None
        if request.method == 'POST':
            try:
                action = request.form.get('action')
                if action == 'create':
                    password = secrets.token_urlsafe(15)
                    username = request.form.get('username', '').strip().lower()
                    store.add_user(username, request.form.get('name', '').strip(), password, actor=g.user['id'])
                    created = dict(username=username, password=password)
                elif action == 'disable':
                    store.disable_user(request.form.get('user_id'), g.user['id'])
                    flash('Đã khóa tài khoản và đăng xuất các phiên của người đó.', 'success')
                elif action == 'reset':
                    ident = request.form.get('user_id')
                    target = next((u for u in store.users() if u['id'] == ident and u['role'] == 'staff'), None)
                    if not target:
                        raise ValueError('Chọn tài khoản nhân viên.')
                    password = secrets.token_urlsafe(15)
                    store.change_password(ident, password, actor=g.user['id'])
                    created = dict(username=target['username'], password=password)
                else:
                    abort(400)
            except ValueError as exc:
                flash(str(exc), 'error')
        return render_template('team.html', people=store.users(), created=created,
                               public_url=settings.get('public_url', ''))

    return app
