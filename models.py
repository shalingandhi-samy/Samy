"""Pydantic models for the Action Tracker."""
from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    """Model for creating a new task."""
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    task_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    priority: str = Field(default="medium", pattern=r"^(high|medium|low)$")


class TaskResponse(BaseModel):
    """Model for task response."""
    id: int
    title: str
    description: str
    status: str
    priority: str
    task_date: str
    created_at: str
    updated_at: str
