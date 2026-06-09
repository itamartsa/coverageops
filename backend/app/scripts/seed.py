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

    users_to_seed = [
        {"username": "adminuser", "full_name": "מנהל מערכת",  "password": "12345678", "role": UserRole.ADMIN},
        {"username": "operator1", "full_name": "קצין קשר א", "password": "Ops1234!",  "role": UserRole.OPERATOR},
        {"username": "viewer1",   "full_name": "צופה א",      "password": "View1234!", "role": UserRole.VIEWER},
    ]

    seeded = []
    for u_data in users_to_seed:
        if db.query(User).filter(User.username == u_data["username"]).first():
            continue
        db.add(User(
            username=u_data["username"],
            full_name=u_data["full_name"],
            hashed_pw=hash_password(u_data["password"]),
            role=u_data["role"],
        ))
        seeded.append(u_data["username"])

    if seeded:
        db.commit()
        print(f"✅ Seeded users: {', '.join(seeded)}")
    else:
        print("✅ DB already seeded")

    db.close()


if __name__ == "__main__":
    seed()
