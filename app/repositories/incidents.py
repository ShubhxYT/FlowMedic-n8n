from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.schemas.domain import Failure


class IncidentRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, incident_id):
        return self.session.get(Incident, incident_id)

    def list(self, limit=50, offset=0):
        return self.session.scalars(
            select(Incident).order_by(Incident.id).offset(offset).limit(limit)
        ).all()

    def create_once(self, failure: Failure):
        existing = self.session.scalar(
            select(Incident).where(Incident.execution_id == failure.execution_id)
        )
        if existing:
            return existing, False
        incident = Incident(**failure.model_dump())
        try:
            with self.session.begin_nested():
                self.session.add(incident)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(
                select(Incident).where(Incident.execution_id == failure.execution_id)
            )
            if existing is None:
                raise
            return existing, False
        return incident, True
