import psycopg2
import os

def get_db_connection():
    try:
        db_url = os.getenv('DATABASE_URL')
        if db_url:
            return psycopg2.connect(db_url)
        else:
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
