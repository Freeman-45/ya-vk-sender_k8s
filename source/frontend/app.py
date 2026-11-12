import os
import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = 'frontend_secret_key_change_me'
API_URL = os.getenv("API_URL", "http://api:8000")

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
                flash('Ошибка регистрации (возможно, email занят)', 'danger')
            return redirect(url_for('login'))

        resp = requests.post(f"{API_URL}/token", data={"username": email, "password": password})
        if resp.status_code == 200:
            session['token'] = resp.json()['access_token']
            session['email'] = email
            return redirect(url_for('dashboard'))
        
        flash('Неверный email или пароль', 'danger')

    return render_template('login.html')

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)