from flask import Flask, render_template, request, redirect, url_for, session, flash
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import random
import string
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from google import genai

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True) 
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- THE AI SENTINEL ---
def check_content_safety(text):
    try:
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key: return True
        client = genai.Client(api_key=api_key)
        prompt = f"You are a strict content moderator for a free speech platform. Read this text. If it contains severe hate speech, direct violence, or dangerous illegal instructions, respond with ONLY the word 'FLAGGED'. If it is acceptable, respond with ONLY the word 'SAFE'. Text to analyze: '{text}'"
        # UPGRADED BACK TO 2.5-FLASH
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        if 'FLAGGED' in response.text.upper(): return False
        return True
    except Exception as e:
        print(f"!!! AI Safety Error: {str(e)} !!!")
        return True 

# --- THE AI NUANCE SCALE ---
def fact_check_content(text):
    try:
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key: return "[Error] Your GEMINI_API_KEY is missing!", False
        client = genai.Client(api_key=api_key)
        prompt = f"""Analyze the factual claims in this text. You must return your analysis as a strict scale of percentages adding up to 100%, followed by an explanation. 
        Format strictly as: TRUE_PERCENT|FALSE_PERCENT|UNVERIFIABLE_PERCENT|EXPLANATION. Example: '80|0|20|Explanation.' Text: '{text}'"""
        # UPGRADED BACK TO 2.5-FLASH
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        parts = response.text.split('|')
        if len(parts) >= 4:
            true_pct, false_pct, unverifiable_pct, explanation = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            formatted_result = f"[Truth Scale: {true_pct}% True | {false_pct}% False | {unverifiable_pct}% Unverifiable] {explanation}"
            return formatted_result, int(false_pct) > 50 
        return "[Error] AI returned a weird format.", False
    except Exception as e:
        return f"[System Crash] Reason: {str(e)}", False

def get_db_connection():
    try:
        # This checks if DATABASE_URL exists (which we set in Render)
        db_url = os.getenv('DATABASE_URL')
        
        if db_url:
            # If on Render, use the Cloud URL
            return psycopg2.connect(db_url)
        else:
            # If on your laptop, use the local settings from .env
            return psycopg2.connect(
                host=os.getenv('DB_HOST'),
                database=os.getenv('DB_NAME'),
                user=os.getenv('DB_USER'),
                password=os.getenv('DB_PASSWORD'),
                port=os.getenv('DB_PORT')
            )
    except Exception as e:
        print(f"!!! Database Connection Error: {e} !!!")
        return None

@app.route('/register', methods=['GET', 'POST'])
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
            return redirect(url_for('login'))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return "Error: Username or Email already exists!"
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
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
            return redirect(url_for('index')) # this now points to /feed
        return "Invalid username or password"
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('gateway')) # Sends you back to the front door

# --- THE FRONT DOOR (GATEWAY) ---
@app.route('/')
def gateway():
    # If logged in, go to feed. If not, show login page.
    if 'user_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

# --- THE MAIN GRID (FEED) ---
@app.route('/feed')
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("DELETE FROM Dispatches WHERE expires_at < NOW()")
    cur.execute("DELETE FROM Authors WHERE is_anonymous = True AND expires_at < NOW()")
    cur.execute("DELETE FROM Messages WHERE expires_at < NOW()") # Global Message Cleanup
    conn.commit() 
    
    feed_type = request.args.get('feed', 'global')
    if feed_type == 'following' and 'user_id' in session:
        cur.execute('''SELECT d.id, d.title, d.content, d.media_url, d.created_at, a.username, a.is_anonymous FROM Dispatches d JOIN Authors a ON d.author_id = a.id JOIN Follows f ON a.id = f.followed_id WHERE f.follower_id = %s ORDER BY d.created_at DESC''', (session['user_id'],))
    else:
        cur.execute('''SELECT d.id, d.title, d.content, d.media_url, d.created_at, a.username, a.is_anonymous FROM Dispatches d JOIN Authors a ON d.author_id = a.id ORDER BY d.created_at DESC''')
        
    dispatches = cur.fetchall()
    cur.close(); conn.close()
    return render_template('index.html', dispatches=dispatches, session=session, feed_type=feed_type)

@app.route('/search')
def search():
    query = request.args.get('q', '')
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''SELECT id, username, ai_trust_score, bio, profile_pic FROM Authors WHERE username ILIKE %s AND is_anonymous = False''', (f'%{query}%',))
    matched_authors = cur.fetchall()
    cur.execute('''SELECT d.id, d.title, d.content, d.media_url, d.created_at, a.username, a.is_anonymous FROM Dispatches d JOIN Authors a ON d.author_id = a.id WHERE d.title ILIKE %s OR d.content ILIKE %s OR a.username ILIKE %s ORDER BY d.created_at DESC''', (f'%{query}%', f'%{query}%', f'%{query}%'))
    dispatches = cur.fetchall()
    cur.close(); conn.close()
    return render_template('index.html', dispatches=dispatches, matched_authors=matched_authors, session=session, search_query=query)

@app.route('/post_anonymous', methods=['POST'])
def post_anonymous():
    title, post_content = request.form.get('title'), request.form['content']
    media_url = request.form.get('media_url')
    media_file = request.files.get('media_upload') if request.files else None
    
    if media_file and allowed_file(media_file.filename):
        filename = secure_filename(media_file.filename)
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        media_file.save(save_path)
        media_url = f"/{save_path}" 
    
    if not check_content_safety(post_content):
        flash("Your ghost dispatch was blocked by the AI Sentinel for violating safety guidelines.", "danger")
        return redirect(url_for('index'))
    
    expiration_time = None
    if request.form.get('expiration_time'):
        try: expiration_time = datetime.fromisoformat(request.form.get('expiration_time'))
        except ValueError: pass 
    
    anon_username = f"Anon_{''.join(random.choices(string.digits, k=5))}"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO Authors (username, password_hash, is_anonymous, expires_at) VALUES (%s, %s, %s, %s) RETURNING id', (anon_username, 'no_password_needed', True, expiration_time))
    author_id = cur.fetchone()[0]
    
    full_result, is_debunked = fact_check_content(post_content)
    cur.execute('INSERT INTO Dispatches (author_id, title, content, media_url, expires_at, fact_check_result, is_debunked) VALUES (%s, %s, %s, %s, %s, %s, %s)', (author_id, title, post_content, media_url, expiration_time, full_result, is_debunked))
    
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('index'))

@app.route('/post_dispatch', methods=['POST'])
def post_dispatch():
    if 'user_id' not in session: return redirect(url_for('login'))
    title, post_content = request.form.get('title'), request.form['content']
    
    media_url = request.form.get('media_url')
    media_file = request.files.get('media_upload') if request.files else None
    if media_file and allowed_file(media_file.filename):
        filename = secure_filename(media_file.filename)
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        media_file.save(save_path)
        media_url = f"/{save_path}" 
    
    if not check_content_safety(post_content):
        flash("Your dispatch was blocked by the AI Sentinel.", "danger")
        return redirect(url_for('index'))
        
    post_type = request.form.get('post_type') 
    expiration_time = None
    if request.form.get('expiration_time'):
        try: expiration_time = datetime.fromisoformat(request.form.get('expiration_time'))
        except ValueError: pass
    
    conn = get_db_connection()
    cur = conn.cursor()

    if post_type == 'named': author_id = session['user_id']
    else:
        ghost_expiry = expiration_time if expiration_time else (datetime.now() + timedelta(hours=24))
        anon_username = f"Anon_{''.join(random.choices(string.digits, k=5))}"
        cur.execute('INSERT INTO Authors (username, password_hash, is_anonymous, expires_at) VALUES (%s, %s, %s, %s) RETURNING id', (anon_username, 'no_password_needed', True, ghost_expiry))
        author_id = cur.fetchone()[0]

    cur.execute('INSERT INTO Dispatches (author_id, title, content, media_url, expires_at) VALUES (%s, %s, %s, %s, %s)', (author_id, title, post_content, media_url, expiration_time))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('index'))

@app.route('/dispatch/<int:dispatch_id>')
def view_dispatch(dispatch_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('''SELECT d.id, d.title, d.content, d.media_url, d.created_at, d.fact_check_result, d.is_debunked, a.username, a.is_anonymous FROM Dispatches d JOIN Authors a ON d.author_id = a.id WHERE d.id = %s''', (dispatch_id,))
    dispatch = cur.fetchone()
    cur.execute('''SELECT c.content, c.created_at, a.username, a.is_anonymous FROM Comments c JOIN Authors a ON c.author_id = a.id WHERE c.dispatch_id = %s ORDER BY c.created_at DESC''', (dispatch_id,))
    comments = cur.fetchall()
    cur.execute('''SELECT AVG(rating) as avg_rating, COUNT(rating) as rating_count FROM Reviews WHERE dispatch_id = %s''', (dispatch_id,))
    rating = cur.fetchone()
    cur.close(); conn.close()
    if not dispatch: return "Dispatch not found.", 404
    return render_template('dispatch.html', dispatch=dispatch, comments=comments, rating=rating, session=session)

@app.route('/dispatch/<int:dispatch_id>/comment', methods=['POST'])
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
    return redirect(url_for('view_dispatch', dispatch_id=dispatch_id))

@app.route('/dispatch/<int:dispatch_id>/rate', methods=['POST'])
def rate_dispatch(dispatch_id):
    if 'user_id' not in session: return redirect(url_for('login')) 
    rating_value, author_id = int(request.form['rating']), session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM Reviews WHERE dispatch_id = %s AND author_id = %s", (dispatch_id, author_id))
    existing_review = cur.fetchone()
    if existing_review: cur.execute("UPDATE Reviews SET rating = %s WHERE id = %s", (rating_value, existing_review[0]))
    else: cur.execute("INSERT INTO Reviews (dispatch_id, author_id, rating) VALUES (%s, %s, %s)", (dispatch_id, author_id, rating_value))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('view_dispatch', dispatch_id=dispatch_id))

@app.route('/inbox')
def inbox():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("DELETE FROM Messages WHERE expires_at < NOW()") # Inbox-specific cleanup
    conn.commit()
    cur.execute('''SELECT m.content, m.deliver_at, m.expires_at, a.username as sender_username FROM Messages m JOIN Authors a ON m.sender_id = a.id WHERE m.receiver_id = %s AND m.deliver_at <= NOW() ORDER BY m.deliver_at DESC''', (session['user_id'],))
    messages = cur.fetchall()
    cur.close(); conn.close()
    return render_template('inbox.html', messages=messages)

@app.route('/send_message', methods=['POST'])
def send_message():
    # REMOVED the login block! Anyone can send a message now.
    receiver_username = request.form['receiver_username']
    content = request.form['content']
    deliver_at = datetime.fromisoformat(request.form.get('deliver_at')) if request.form.get('deliver_at') else datetime.now()
    
    # Check if expires_at is in the form (from profile page), otherwise ignore it
    expires_at = None
    if request.form.get('expires_at'):
        expires_at = datetime.fromisoformat(request.form.get('expires_at'))

    conn = get_db_connection()
    cur = conn.cursor()
    
    # Handle Anonymous vs Named Senders
    if 'user_id' in session:
        sender_id = session['user_id']
    else:
        ghost_expiry = expires_at if expires_at else (datetime.now() + timedelta(days=7))
        anon_username = f"Anon_{''.join(random.choices(string.digits, k=5))}"
        cur.execute('INSERT INTO Authors (username, password_hash, is_anonymous, expires_at) VALUES (%s, %s, %s, %s) RETURNING id', (anon_username, 'no_password_needed', True, ghost_expiry))
        sender_id = cur.fetchone()[0]

    cur.execute("SELECT id FROM Authors WHERE username = %s AND is_anonymous = False", (receiver_username,))
    receiver = cur.fetchone()
    if receiver:
        # Check if your Messages table has expires_at column. If it crashes here, we can remove expires_at.
        try:
            cur.execute('INSERT INTO Messages (sender_id, receiver_id, content, deliver_at, expires_at) VALUES (%s, %s, %s, %s, %s)', (sender_id, receiver[0], content, deliver_at, expires_at))
        except psycopg2.errors.UndefinedColumn:
            conn.rollback() # Fallback if expires_at isn't in your db yet
            cur.execute('INSERT INTO Messages (sender_id, receiver_id, content, deliver_at) VALUES (%s, %s, %s, %s)', (sender_id, receiver[0], content, deliver_at))
            
        conn.commit()
        flash(f"Secure payload delivered to {receiver_username}.", "success")
    else:
        flash("Agent not found. Dead drop failed.", "danger")
        
    cur.close(); conn.close()
    
    # Send user back to where they came from
    referrer = request.referrer
    if referrer: return redirect(referrer)
    return redirect(url_for('index'))

@app.route('/profile/<username>')
def profile(username):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id, username, ai_trust_score, bio, profile_pic FROM Authors WHERE username = %s AND is_anonymous = False", (username,))
    author = cur.fetchone()
    if not author: return "Author not found or is a ghost.", 404
        
    cur.execute("SELECT * FROM Dispatches WHERE author_id = %s ORDER BY created_at DESC", (author['id'],))
    dispatches = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM Follows WHERE followed_id = %s", (author['id'],))
    followers_count = cur.fetchone()['count']
    cur.execute("SELECT COUNT(*) FROM Follows WHERE follower_id = %s", (author['id'],))
    following_count = cur.fetchone()['count']
    
    is_following = False
    if 'user_id' in session:
        cur.execute("SELECT * FROM Follows WHERE follower_id = %s AND followed_id = %s", (session['user_id'], author['id']))
        if cur.fetchone(): is_following = True
            
    cur.close(); conn.close()
    return render_template('profile.html', author=author, dispatches=dispatches, followers_count=followers_count, following_count=following_count, is_following=is_following, session=session)

@app.route('/toggle_follow/<username>', methods=['POST'])
def toggle_follow(username):
    if 'user_id' not in session: return redirect(url_for('login'))
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
    return redirect(url_for('profile', username=username))

@app.route('/delete_dispatch/<int:dispatch_id>', methods=['POST'])
def delete_dispatch(dispatch_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM Dispatches WHERE id = %s AND author_id = %s", (dispatch_id, session['user_id']))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('profile', username=session['username']))

@app.route('/trigger_fact_check/<int:dispatch_id>', methods=['POST'])
def trigger_fact_check(dispatch_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT content, author_id FROM Dispatches WHERE id = %s", (dispatch_id,))
    dispatch = cur.fetchone()
    if dispatch:
        full_result, is_debunked = fact_check_content(dispatch['content'])
        cur.execute("UPDATE Dispatches SET fact_check_result = %s, is_debunked = %s WHERE id = %s", (full_result, is_debunked, dispatch_id))
        if is_debunked:
            cur.execute('UPDATE Authors SET ai_trust_score = GREATEST(1.0, ai_trust_score - 1.5) WHERE id = %s AND is_anonymous = False', (dispatch['author_id'],))
        conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('view_dispatch', dispatch_id=dispatch_id))

# --- VIEW AUTHOR PROFILE DOSSIER ---
@app.route('/author/<username>')
def view_profile(username):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    cur.execute("SELECT id, username, created_at FROM Authors WHERE username = %s AND is_anonymous = False", (username,))
    author = cur.fetchone()
    
    if not author:
        cur.close(); conn.close()
        return "Author not found or is a Ghost.", 404
        
    cur.execute('''
        SELECT id, content, created_at 
        FROM Dispatches 
        WHERE author_id = %s 
        ORDER BY created_at DESC
    ''', (author['id'],))
    dispatches = cur.fetchall()
    
    cur.execute('''
        SELECT AVG(r.rating) as avg_score 
        FROM Reviews r
        JOIN Dispatches d ON r.dispatch_id = d.id
        WHERE d.author_id = %s
    ''', (author['id'],))
    stats = cur.fetchone()
    
    cur.close(); conn.close()
    return render_template('profile.html', author=author, dispatches=dispatches, stats=stats, session=session)
# --- NEW: EDIT PROFILE ENGINE ---
@app.route('/edit_profile', methods=['POST'])
def edit_profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    
    bio = request.form.get('bio')
    profile_pic = request.files.get('profile_pic')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # If they uploaded a new photo, save it and update DB
    if profile_pic and allowed_file(profile_pic.filename):
        filename = secure_filename(f"avatar_{session['user_id']}_{profile_pic.filename}")
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        profile_pic.save(save_path)
        pic_url = f"/{save_path}"
        cur.execute("UPDATE Authors SET bio = %s, profile_pic = %s WHERE id = %s", (bio, pic_url, session['user_id']))
    else:
        # Just update the bio if no new photo
        cur.execute("UPDATE Authors SET bio = %s WHERE id = %s", (bio, session['user_id']))
        
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('profile', username=session['username']))
if __name__ == '__main__':
    app.run(debug=True)
