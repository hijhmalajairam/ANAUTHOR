from flask import Blueprint, request, redirect, url_for, session, flash, current_app
import os, random, string
from datetime import timedelta, datetime
from werkzeug.utils import secure_filename
from db import get_db_connection
from helpers import get_ist_time, allowed_file, run_background_ai_checks, fact_check_content
from security import limiter  
import threading

actions_bp = Blueprint('actions', __name__)

@actions_bp.route('/post_anonymous', methods=['POST'])
def post_anonymous():
    title, post_content = request.form.get('title'), request.form['content']
    media_url = request.form.get('media_url')
    media_file = request.files.get('media_upload') if request.files else None
    
    if media_file and allowed_file(media_file.filename):
        filename = secure_filename(media_file.filename)
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        media_file.save(save_path)
        media_url = f"/{save_path}" 
    
    expiration_time = None
    if request.form.get('expiration_time'):
        try: expiration_time = datetime.fromisoformat(request.form.get('expiration_time'))
        except ValueError: pass 
    
    anon_username = f"Anon_{''.join(random.choices(string.digits, k=5))}"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO Authors (username, password_hash, is_anonymous, expires_at) VALUES (%s, %s, %s, %s) RETURNING id', (anon_username, 'no_password_needed', True, expiration_time))
    author_id = cur.fetchone()[0]
    
    # Store Ghost ID
    ghosts = session.get('ghost_ids', [])
    ghosts.append(author_id)
    session['ghost_ids'] = ghosts
    
    cur.execute('INSERT INTO Dispatches (author_id, title, content, media_url, expires_at, fact_check_result, is_debunked, visibility) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id', 
                (author_id, title, post_content, media_url, expiration_time, "[AI Sentinel: Fact Check Pending...]", False, 'pending'))
    dispatch_id = cur.fetchone()[0]
    
    conn.commit()
    cur.close()
    conn.close()
    
    threading.Thread(target=run_background_ai_checks, args=(dispatch_id, post_content, author_id)).start()
    return redirect(url_for('pages.index'))

@actions_bp.route('/post_dispatch', methods=['POST'])
def post_dispatch():
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    title, post_content = request.form.get('title'), request.form['content']
    
    media_url = request.form.get('media_url')
    media_file = request.files.get('media_upload') if request.files else None
    if media_file and allowed_file(media_file.filename):
        filename = secure_filename(media_file.filename)
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        media_file.save(save_path)
        media_url = f"/{save_path}" 
        
    post_type = request.form.get('post_type') 
    expiration_time = None
    if request.form.get('expiration_time'):
        try: expiration_time = datetime.fromisoformat(request.form.get('expiration_time'))
        except ValueError: pass
    
    conn = get_db_connection()
    cur = conn.cursor()

    if post_type == 'named': 
        author_id = session['user_id']
    else:
        ghost_expiry = expiration_time if expiration_time else (get_ist_time() + timedelta(hours=24))
        anon_username = f"Anon_{''.join(random.choices(string.digits, k=5))}"
        cur.execute('INSERT INTO Authors (username, password_hash, is_anonymous, expires_at) VALUES (%s, %s, %s, %s) RETURNING id', (anon_username, 'no_password_needed', True, ghost_expiry))
        author_id = cur.fetchone()[0]
        # Store Ghost ID
        ghosts = session.get('ghost_ids', [])
        ghosts.append(author_id)
        session['ghost_ids'] = ghosts

    cur.execute('INSERT INTO Dispatches (author_id, title, content, media_url, expires_at, fact_check_result, is_debunked, visibility) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id', 
                (author_id, title, post_content, media_url, expiration_time, "[AI Sentinel: Fact Check Pending...]", False, 'pending'))
    dispatch_id = cur.fetchone()[0]
    
    conn.commit()
    cur.close()
    conn.close()

    threading.Thread(target=run_background_ai_checks, args=(dispatch_id, post_content, author_id)).start()
    return redirect(url_for('pages.index'))

@actions_bp.route('/dispatch/<int:dispatch_id>/comment', methods=['POST'])
def post_comment(dispatch_id):
    content = request.form['comment_content']
    conn = get_db_connection()
    cur = conn.cursor()
    if session.get('user_id'): author_id = session['user_id']
    else:
        anon_username = f"Anon_{''.join(random.choices(string.digits, k=5))}"
        cur.execute("INSERT INTO Authors (username, password_hash, is_anonymous) VALUES (%s, %s, %s) RETURNING id", (anon_username, 'no_password', True))
        author_id = cur.fetchone()[0]
        
    cur.execute("INSERT INTO Comments (dispatch_id, author_id, content) VALUES (%s, %s, %s)", (dispatch_id, author_id, content))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('pages.view_dispatch', dispatch_id=dispatch_id))

@actions_bp.route('/dispatch/<int:dispatch_id>/rate', methods=['POST'])
def rate_dispatch(dispatch_id):
    if 'user_id' not in session: return redirect(url_for('auth.login')) 
    rating_value, author_id = int(request.form['rating']), session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM Reviews WHERE dispatch_id = %s AND author_id = %s", (dispatch_id, author_id))
    existing_review = cur.fetchone()
    if existing_review: cur.execute("UPDATE Reviews SET rating = %s WHERE id = %s", (rating_value, existing_review[0]))
    else: cur.execute("INSERT INTO Reviews (dispatch_id, author_id, rating) VALUES (%s, %s, %s)", (dispatch_id, author_id, rating_value))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('pages.view_dispatch', dispatch_id=dispatch_id))

@actions_bp.route('/send_message', methods=['POST'])
@limiter.limit("5 per minute") 
def send_message():
    receiver_username = request.form['receiver_username']
    content = request.form['content']
    deliver_at = datetime.fromisoformat(request.form.get('deliver_at')) if request.form.get('deliver_at') else get_ist_time()
    expires_at = datetime.fromisoformat(request.form.get('expires_at')) if request.form.get('expires_at') else None

    conn = get_db_connection()
    cur = conn.cursor()
    
    if 'user_id' in session:
        sender_id = session['user_id']
    else:
        ghost_expiry = expires_at if expires_at else (get_ist_time() + timedelta(days=7))
        anon_username = f"Anon_{''.join(random.choices(string.digits, k=5))}"
        cur.execute('INSERT INTO Authors (username, password_hash, is_anonymous, expires_at) VALUES (%s, %s, %s, %s) RETURNING id', (anon_username, 'no_password_needed', True, ghost_expiry))
        sender_id = cur.fetchone()[0]

    cur.execute("SELECT id FROM Authors WHERE username = %s AND is_anonymous = False", (receiver_username,))
    receiver = cur.fetchone()
    if receiver:
        try:
            cur.execute('INSERT INTO Messages (sender_id, receiver_id, content, deliver_at, expires_at) VALUES (%s, %s, %s, %s, %s)', (sender_id, receiver[0], content, deliver_at, expires_at))
        except Exception:
            conn.rollback()
            cur.execute('INSERT INTO Messages (sender_id, receiver_id, content, deliver_at) VALUES (%s, %s, %s, %s)', (sender_id, receiver[0], content, deliver_at))
        conn.commit()
        flash(f"Secure payload delivered to {receiver_username}.", "success")
    else:
        flash("Agent not found. Dead drop failed.", "danger")
        
    cur.close(); conn.close()
    referrer = request.referrer
    if referrer: return redirect(referrer)
    return redirect(url_for('pages.index'))

@actions_bp.route('/toggle_follow/<username>', methods=['POST'])
def toggle_follow(username):
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    follower_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM Authors WHERE username = %s AND is_anonymous = False", (username,))
    target = cur.fetchone()
    if target:
        cur.execute("SELECT * FROM Follows WHERE follower_id = %s AND followed_id = %s", (follower_id, target[0]))
        if cur.fetchone(): cur.execute("DELETE FROM Follows WHERE follower_id = %s AND followed_id = %s", (follower_id, target[0]))
        else: cur.execute("INSERT INTO Follows (follower_id, followed_id) VALUES (%s, %s)", (follower_id, target[0]))
        conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('pages.profile', username=username))

@actions_bp.route('/delete_dispatch/<int:dispatch_id>', methods=['POST'])
def delete_dispatch(dispatch_id):
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM Dispatches WHERE id = %s AND author_id = %s", (dispatch_id, session['user_id']))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('pages.profile', username=session['username']))

@actions_bp.route('/trigger_fact_check/<int:dispatch_id>', methods=['POST'])
def trigger_fact_check(dispatch_id):
    import psycopg2.extras
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) 
    cur.execute("SELECT content, author_id FROM Dispatches WHERE id = %s", (dispatch_id,))
    dispatch = cur.fetchone()
    if dispatch:
        full_result, is_debunked = fact_check_content(dispatch['content'])
        cur.execute("UPDATE Dispatches SET fact_check_result = %s, is_debunked = %s WHERE id = %s", (full_result, is_debunked, dispatch_id))
        if is_debunked:
            cur.execute('UPDATE Authors SET ai_trust_score = GREATEST(1.0, ai_trust_score - 1.5) WHERE id = %s AND is_anonymous = False', (dispatch['author_id'],))
        conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('pages.view_dispatch', dispatch_id=dispatch_id))

@actions_bp.route('/edit_profile', methods=['POST'])
def edit_profile():
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    bio = request.form.get('bio')
    profile_pic = request.files.get('profile_pic')
    conn = get_db_connection()
    cur = conn.cursor()
    if profile_pic and allowed_file(profile_pic.filename):
        filename = secure_filename(f"avatar_{session['user_id']}_{profile_pic.filename}")
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        profile_pic.save(save_path)
        pic_url = f"/{save_path}"
        cur.execute("UPDATE Authors SET bio = %s, profile_pic = %s WHERE id = %s", (bio, pic_url, session['user_id']))
    else:
        cur.execute("UPDATE Authors SET bio = %s WHERE id = %s", (bio, session['user_id']))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('pages.profile', username=session['username']))
