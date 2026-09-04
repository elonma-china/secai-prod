import sqlite3

def get_user(conn, user_id):
    cur = conn.cursor()
    query = "SELECT * FROM users WHERE id = '" + user_id + "'"
    cur.execute(query)
    return cur.fetchall()
