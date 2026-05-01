@pages_bp.route('/feed')
def index():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # 1. Clean up expired
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
    
    return render_template('index.html', dispatches=dispatches, session=session, feed_type=feed_type, live_verified=live_verified, live_ghosts=live_ghosts)
