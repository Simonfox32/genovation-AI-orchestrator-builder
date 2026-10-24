from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, CheckConstraint
import uuid

class Base(DeclarativeBase):
    pass

class Project(Base):
    __tablename__ = "projects"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')", 
            name="projects_status_valid",
        ),
    )