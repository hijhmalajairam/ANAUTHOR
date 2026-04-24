from flask import Blueprint, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from psycopg2.extras import RealDictCursor
from db import get_db_connection

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username, email, password = request.form['username'], request.form['email'], request.form['password']
        hashed_password = generate_password_hash(password)
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO Authors (username, email, password_hash, is_anonymous) VALUES (%s, %s, %s, False)', (username, email, hashed_password))
            conn.commit()
            cur.close(); conn.close()
            return redirect(url_for('auth.login'))
        except Exception:
            conn.rollback()
            return "Error: Username or Email already exists!"
    return render_template('register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username, password_attempt = request.form['username'], request.form['password']
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM Authors WHERE username = %s AND is_anonymous = False", (username,))
        user = cur.fetchone()
        cur.close(); conn.close()
        
        if user and check_password_hash(user['password_hash'], password_attempt):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('pages.index'))
        return "Invalid username or password"
    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('pages.gateway'))
