from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# Creates a local database file named echo.db in your project directory
SQLALCHEMY_DATABASE_URL = "sqlite:///./echo.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Database Table Schema for storing Brain Dumps
class BrainDumpDB(Base):
    __tablename__ = "brain_dumps"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.now)
    raw_text = Column(Text, nullable=False)
    parsed_json = Column(Text, nullable=False) # Stores the raw JSON response

# Initialize the database file
def init_db():
    Base.metadata.create_all(bind=engine)