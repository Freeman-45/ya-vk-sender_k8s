import os
import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = 'frontend_secret_key_change_me'
API_URL = os.getenv("API_URL", "http://api:8000")

# --- НАСТРОЙКИ ЯНДЕКСА ---
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID", "ваш_client_id")
YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET", "ваш_client_secret")
YANDEX_REDIRECT_URI = os.getenv("YANDEX_REDIRECT_URI", "http://159.194.249.101:8080/auth/yandex/callback")

def get_headers():
    return {"Authorization": f"Bearer {session.get('token')}"}

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action')
        email = request.form.get('email')
        password = request.form.get('password')

        if action == 'register':
            resp = requests.post(f"{API_URL}/register?email={email}&password={password}")
            if resp.status_code == 200:
                flash('Регистрация успешна! Теперь войдите.', 'success')
            else:
                flash('Ошибка регистрации (возможно, регистрация закрыта или email занят)', 'danger')
            return redirect(url_for('login'))

        resp = requests.post(f"{API_URL}/token", data={"username": email, "password": password})
        if resp.status_code == 200:
            session['token'] = resp.json()['access_token']
            session['email'] = email
            
            # Получаем роль пользователя
            me_resp = requests.get(f"{API_URL}/users/me", headers=get_headers())
            if me_resp.status_code == 200:
                session['is_admin'] = me_resp.json().get('is_admin', False)
                
            return redirect(url_for('dashboard'))
        
        flash('Неверный email или пароль', 'danger')

    return render_template('login.html')

# --- OAuth ЯНДЕКС ---
@app.route('/auth/yandex')
def auth_yandex():
    auth_url = (
        f"https://oauth.yandex.ru/authorize?"
        f"response_type=code&"
        f"client_id={YANDEX_CLIENT_ID}&"
        f"redirect_uri={YANDEX_REDIRECT_URI}"
    )
    return redirect(auth_url)

@app.route('/auth/yandex/callback')
def auth_yandex_callback():
    code = request.args.get('code')
    if not code:
        flash('Ошибка авторизации через Яндекс', 'danger')
        return redirect(url_for('login'))

    token_resp = requests.post('https://oauth.yandex.ru/token', data={
        'grant_type': 'authorization_code',
        'code': code,
        'client_id': YANDEX_CLIENT_ID,
        'client_secret': YANDEX_CLIENT_SECRET
    })
    
    if token_resp.status_code != 200:
        flash('Не удалось получить токен от Яндекса', 'danger')
        return redirect(url_for('login'))

    yandex_token = token_resp.json().get('access_token')

    user_info = requests.get(
        'https://login.yandex.ru/info?format=json',
        headers={'Authorization': f'OAuth {yandex_token}'}
    ).json()
    
    email = user_info.get('default_email')
    if not email:
        flash('Не удалось получить email от Яндекса', 'danger')
        return redirect(url_for('login'))

    backend_resp = requests.post(f"{API_URL}/auth/yandex", json={"email": email})
    
    if backend_resp.status_code == 200:
        session['token'] = backend_resp.json()['access_token']
        session['email'] = email
        
        # Получаем роль пользователя
        me_resp = requests.get(f"{API_URL}/users/me", headers=get_headers())
        if me_resp.status_code == 200:
            session['is_admin'] = me_resp.json().get('is_admin', False)
            
        return redirect(url_for('dashboard'))
    elif backend_resp.status_code == 403:
        flash(f'Пользователь с email {email} не найден. Обратитесь к администратору.', 'danger')
        return redirect(url_for('login'))
    else:
        flash('Ошибка входа через Яндекс', 'danger')
        return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def dashboard():
    if not session.get('token'): return redirect(url_for('login'))
    
    f_resp = requests.get(f"{API_URL}/forms", headers=get_headers())
    b_resp = requests.get(f"{API_URL}/bots", headers=get_headers())
    r_resp = requests.get(f"{API_URL}/routes", headers=get_headers())
    
    forms_count = len(f_resp.json()) if f_resp.status_code == 200 else 0
    bots_count = len(b_resp.json()) if b_resp.status_code == 200 else 0
    routes_count = len(r_resp.json()) if r_resp.status_code == 200 else 0

    return render_template('dashboard.html', active_page='dashboard', 
                           email=session.get('email'),
                           forms_count=forms_count, bots_count=bots_count, routes_count=routes_count)

# --- УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ---
@app.route('/users', methods=['GET', 'POST'])
def users():
    if not session.get('token'): return redirect(url_for('login'))
    if not session.get('is_admin'):
        flash('У вас нет прав для просмотра этой страницы.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        is_admin_new = request.form.get('is_admin') == 'on'
        
        resp = requests.post(f"{API_URL}/users", json={"email": email, "password": password, "is_admin": is_admin_new}, headers=get_headers())
        if resp.status_code == 200:
            flash('Пользователь успешно создан!', 'success')
        else:
            flash('Ошибка при создании (возможно, email занят)', 'danger')
        return redirect(url_for('users'))

    resp = requests.get(f"{API_URL}/users", headers=get_headers())
    users_data = resp.json() if resp.status_code == 200 else []
    return render_template('users.html', active_page='users', users=users_data, email=session.get('email'))

# --- CRUD: ФОРМЫ ---
@app.route('/forms', methods=['GET', 'POST'])
def forms():
    if not session.get('token'): return redirect(url_for('login'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        survey_id = request.form.get('survey_id')
        requests.post(f"{API_URL}/forms?name={name}&survey_id={survey_id}", headers=get_headers())
        return redirect(url_for('forms'))

    resp = requests.get(f"{API_URL}/forms", headers=get_headers())
    forms_data = resp.json() if resp.status_code == 200 else []
    return render_template('forms.html', active_page='forms', forms=forms_data, email=session.get('email'))

@app.route('/forms/delete/<int:form_id>', methods=['POST'])
def delete_form(form_id):
    if not session.get('token'): return redirect(url_for('login'))
    requests.delete(f"{API_URL}/forms/{form_id}", headers=get_headers())
    return redirect(url_for('forms'))

# --- CRUD: БОТЫ ---
@app.route('/bots', methods=['GET', 'POST'])
def bots():
    if not session.get('token'): return redirect(url_for('login'))
    
    if request.method == 'POST':
        name = request.form.get('name')
        group_id = request.form.get('group_id')
        vk_token = request.form.get('vk_token')
        requests.post(f"{API_URL}/bots?name={name}&group_id={group_id}&vk_token={vk_token}", headers=get_headers())
        return redirect(url_for('bots'))

    resp = requests.get(f"{API_URL}/bots", headers=get_headers())
    bots_data = resp.json() if resp.status_code == 200 else []
    return render_template('bots.html', active_page='bots', bots=bots_data, email=session.get('email'))

@app.route('/bots/delete/<int:bot_id>', methods=['POST'])
def delete_bot(bot_id):
    if not session.get('token'): return redirect(url_for('login'))
    requests.delete(f"{API_URL}/bots/{bot_id}", headers=get_headers())
    return redirect(url_for('bots'))

# --- CRUD: МАРШРУТЫ ---
@app.route('/routes', methods=['GET', 'POST'])
def routes():
    if not session.get('token'): return redirect(url_for('login'))
    
    if request.method == 'POST':
        form_id = request.form.get('form_id')
        bot_id = request.form.get('bot_id')
        target_id = request.form.get('target_id')
        requests.post(f"{API_URL}/routes?form_id={form_id}&bot_id={bot_id}&target_vk_id={target_id}", headers=get_headers())
        return redirect(url_for('routes'))

    r_resp = requests.get(f"{API_URL}/routes", headers=get_headers())
    f_resp = requests.get(f"{API_URL}/forms", headers=get_headers())
    b_resp = requests.get(f"{API_URL}/bots", headers=get_headers())
    
    routes_data = r_resp.json() if r_resp.status_code == 200 else []
    forms_data = f_resp.json() if f_resp.status_code == 200 else []
    bots_data = b_resp.json() if b_resp.status_code == 200 else []
    
    forms_dict = {f['id']: f['name'] for f in forms_data}
    bots_dict = {b['id']: b['name'] for b in bots_data}

    return render_template('routes.html', active_page='routes', routes=routes_data, 
                           forms=forms_data, bots=bots_data, 
                           forms_dict=forms_dict, bots_dict=bots_dict, email=session.get('email'))

@app.route('/routes/edit/<int:route_id>', methods=['POST'])
def edit_route(route_id):
    if not session.get('token'): return redirect(url_for('login'))
    
    target_id = request.form.get('target_id')
    is_active = request.form.get('is_active') == 'on'
    
    requests.put(
        f"{API_URL}/routes/{route_id}", 
        json={"target_vk_id": target_id, "is_active": is_active},
        headers=get_headers()
    )
    return redirect(url_for('routes'))

@app.route('/routes/delete/<int:route_id>', methods=['POST'])
def delete_route(route_id):
    if not session.get('token'): return redirect(url_for('login'))
    requests.delete(f"{API_URL}/routes/{route_id}", headers=get_headers())
    return redirect(url_for('routes'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)