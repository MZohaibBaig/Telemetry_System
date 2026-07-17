from app.database import engine, Base
from app.models import User, Device, Reading

Base.metadata.create_all(bind=engine)
print("Tables created.")