"""
Run once to seed the database:
    python -m app.scripts.seed
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.core.database import SessionLocal, engine, Base
from app.core.security import hash_password
from app.models.user import User, UserRole

Base.metadata.create_all(bind=engine)


def seed():
    db = SessionLocal()

    # Check if already seeded
    if db.query(User).filter(User.username == "admin").first():
        print("✅ DB already seeded")
        db.close()
        return

    users = [
        User(
            username="admin",
            full_name="מנהל מערכת",
            hashed_pw=hash_password("Admin1234!"),
            role=UserRole.ADMIN,
        ),
        User(
            username="operator1",
            full_name="קצין קשר א",
            hashed_pw=hash_password("Ops1234!"),
            role=UserRole.OPERATOR,
        ),
        User(
            username="viewer1",
            full_name="צופה א",
            hashed_pw=hash_password("View1234!"),
            role=UserRole.VIEWER,
        ),
    ]

    for u in users:
        db.add(u)

    db.commit()
    print("✅ Seeded users:")
    print("   admin / Admin1234! (ADMIN)")
    print("   operator1 / Ops1234! (OPERATOR)")
    print("   viewer1 / View1234! (VIEWER)")
    db.close()


if __name__ == "__main__":
    seed()
