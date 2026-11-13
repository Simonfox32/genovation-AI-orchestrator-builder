from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, relationship
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, CheckConstraint, ForeignKey, Integer
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
    summary: Mapped[str | None] = mapped_column(Text())  # Filled in by the orchestrator; empty until then
    optimization: Mapped[str] = mapped_column(String(20), server_default="balanced")

    # Python-side link to this project's tasks (no column). Deleting a project deletes its tasks;
    # passive_deletes lets the database's ON DELETE CASCADE do it instead of loading every task first.
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="project",
        order_by="Task.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def files(self) -> list[dict]:
        """The files the coding agent wrote, latest version of each. Not a column: derived from the
        agent's write_file task rows (named "Wrote <path>" by orchestration.py, code in `output`)."""
        latest = {}
        for task in self.tasks:  # ordered by position, so a later write of the same path replaces an earlier one
            label, found, path = task.name.partition("Wrote ")  # names look like "Wrote app/main.py" or "Tester · Wrote ..."
            if task.tool == "write_file" and found and task.output is not None:
                by_tester = label.startswith("Tester")
                latest[path] = ("Tests written by the tester agent" if by_tester
                                else "Final version written by the AI agent", task.output)
        return [{"path": path, "description": description, "content": content}
                for path, (description, content) in latest.items()]

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="projects_status_valid",
        ),
        CheckConstraint(
            "optimization IN ('balanced', 'speed', 'quality')",
            name="projects_optimization_valid",
        ),
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer())  # Order within the plan: 1, 2, 3...
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text())
    tool: Mapped[str | None] = mapped_column(String(50))  # None until routed, or if every tool failed
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer(), server_default="0")
    output: Mapped[str | None] = mapped_column(Text())

    project: Mapped["Project"] = relationship(back_populates="tasks")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="tasks_status_valid",
        ),
    )