from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ServiceError
from app.core.workspaces import require_workspace
from app.models.incident import Incident
from app.schemas.domain import Failure


class IncidentRepository:
    def __init__(self, session: Session, workspace_id: str, instance_id: str | None = None):
        self.workspace_id = require_workspace(session)
        if workspace_id != self.workspace_id:
            raise ValueError("Incident session is outside the workspace")
        self.instance_id = instance_id
        self.session = session

    def get(self, incident_id):
        statement = select(Incident).where(
            Incident.workspace_id == self.workspace_id,
            Incident.id == incident_id,
        )
        if self.instance_id is not None:
            statement = statement.where(Incident.instance_id == self.instance_id)
        return self.session.scalar(statement)

    def list(self, limit=50, offset=0):
        statement = select(Incident).where(Incident.workspace_id == self.workspace_id)
        if self.instance_id is not None:
            statement = statement.where(Incident.instance_id == self.instance_id)
        return self.session.scalars(
            statement.order_by(Incident.id).offset(offset).limit(limit)
        ).all()

    def create_once(self, failure: Failure):
        if self.instance_id is None:
            raise ServiceError("instance_required", "An instance is required", 422)
        predicate = (
            Incident.workspace_id == self.workspace_id,
            Incident.instance_id == self.instance_id,
            Incident.execution_id == failure.execution_id,
        )
        existing = self.session.scalar(select(Incident).where(*predicate))
        if existing:
            return existing, False
        incident = Incident(
            workspace_id=self.workspace_id,
            instance_id=self.instance_id,
            **failure.model_dump(),
        )
        try:
            with self.session.begin_nested():
                self.session.add(incident)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(select(Incident).where(*predicate))
            if existing is None:
                raise
            return existing, False
        return incident, True
