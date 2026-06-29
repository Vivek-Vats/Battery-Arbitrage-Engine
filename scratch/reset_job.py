import sqlite3

def reset_jobs():
    conn = sqlite3.connect('bess_data.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE job_queue SET status='PENDING' WHERE status IN ('RUNNING', 'FAILED')")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    reset_jobs()
