import time
import random
from src.mailshift.models.models import MailMeta
from src.mailshift.db.database import save_mails_cache, init_db, clear_mails_cache

def generate_dummy_mails(num):
    mails = []
    for i in range(num):
        mails.append(MailMeta(
            uid=str(i),
            subject=f"Subject {i}",
            sender=f"sender{i}@example.com",
            date="2023-01-01",
            size_bytes=random.randint(100, 10000),
            body_preview="Hello world " * 10,
            has_attachment=False
        ))
    return mails

def run_benchmark():
    clear_mails_cache()
    init_db()

    mails = generate_dummy_mails(50000) # Increased to 50k to make the difference more visible

    start_time = time.time()
    save_mails_cache(mails, batch_size=500)
    end_time = time.time()

    duration = end_time - start_time
    print(f"Time taken to insert {len(mails)} mails: {duration:.4f} seconds")

if __name__ == "__main__":
    run_benchmark()