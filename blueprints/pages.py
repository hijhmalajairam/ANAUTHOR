from flask import Blueprint, render_template, request, redirect, url_for, session
from psycopg2.extras import RealDictCursor
from db import get_db_connection

pages_bp = Blueprint('pages', __name__)

@pages_bp.route('/')
def gateway():
    if 'user_id' in session:
        return redirect(url_for('pages.index'))
    return render_template('login.html')

@pages_bp.route('/feed')
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Clean up expired ghosts and messages
    cur.execute("DELETE FROM Dispatches WHERE expires_at < CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'")
    cur.execute("DELETE FROM Messages WHERE expires_at < CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'") 
    cur.execute("""
        DELETE FROM Messages 
        WHERE sender_id IN (SELECT id FROM Authors WHERE is_anonymous = True AND expires_at < CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')
           OR receiver_id IN (SELECT id FROM Authors WHERE is_anonymous = True AND expires_at < CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')
    """)
    cur.execute("DELETE FROM Authors WHERE is_anonymous = True AND expires_at < CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'")
    conn.commit() 
    
    feed_type = request.args.get('feed', 'global')
    
    # 2. PRIVACY RADAR: Update activity and count users
    if 'user_id' in session:
        cur.execute("UPDATE Authors SET last_active = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata' WHERE id = %s", (session['user_id'],))
        conn.commit()
        
    cur.execute("SELECT COUNT(*) FROM Authors WHERE is_anonymous = False AND last_active >= CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata' - INTERVAL '15 minutes'")
    live_verified = cur.fetchone()['count']
    
    cur.execute("SELECT COUNT(*) FROM Authors WHERE is_anonymous = True")
    live_ghosts = cur.fetchone()['count']

    # --- NEW: Collect all IDs owned by this browser (Main ID + Ghost IDs) ---
    owned_ids = session.get('ghost_ids', [])
    if 'user_id' in session:
        owned_ids.append(session['user_id'])
    
    # Give it a dummy ID so the SQL doesn't crash if the list is empty
    if not owned_ids:
        owned_ids = [-1]
        
    owned_ids_tuple = tuple(owned_ids)

    # --- NEW: Use 'IN' to check against all your active IDs ---
    if 'user_id' in session and feed_type == 'following':
        cur.execute('''SELECT d.id, d.title, d.content, d.media_url, d.created_at, a.username, a.is_anonymous, d.visibility 
                       FROM Dispatches d JOIN Authors a ON d.author_id = a.id JOIN Follows f ON a.id = f.followed_id 
                       WHERE f.follower_id = %s AND (d.visibility = 'live' OR d.author_id IN %s) 
                       ORDER BY d.created_at DESC''', (session['user_id'], owned_ids_tuple))
    else:
        cur.execute('''SELECT d.id, d.title, d.content, d.media_url, d.created_at, a.username, a.is_anonymous, d.visibility 
                       FROM Dispatches d JOIN Authors a ON d.author_id = a.id 
                       WHERE d.visibility = 'live' OR d.author_id IN %s 
                       ORDER BY d.created_at DESC''', (owned_ids_tuple,))
        
    dispatches = cur.fetchall()
    cur.close(); conn.close()
    
    # Send the live stats to the HTML page
    return render_template('index.html', dispatches=dispatches, session=session, feed_type=feed_type, live_verified=live_verified, live_ghosts=live_ghosts)

@pages_bp.route('/search')
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

@pages_bp.route('/dispatch/<int:dispatch_id>')
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

@pages_bp.route('/inbox')
def inbox():
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("DELETE FROM Messages WHERE expires_at < CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata'") 
    conn.commit()
    cur.execute('''SELECT m.content, m.deliver_at, m.expires_at, a.username as sender_username FROM Messages m JOIN Authors a ON m.sender_id = a.id WHERE m.receiver_id = %s AND m.deliver_at <= CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata' ORDER BY m.deliver_at DESC''', (session['user_id'],))
    messages = cur.fetchall()
    cur.close(); conn.close()
    return render_template('inbox.html', messages=messages)

@pages_bp.route('/profile/<username>')
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

@pages_bp.route('/author/<username>')
def view_profile(username):
    return redirect(url_for('pages.profile', username=username))
