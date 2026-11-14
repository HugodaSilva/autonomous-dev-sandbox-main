from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, PositiveInt, model_validator

from .enums import ComponentType, ServiceType


class SLAProfile(BaseModel):
    max_latency_ms: PositiveInt
    min_bandwidth_mbps: PositiveInt
    availability_pct: float = Field(ge=0.0, le=100.0)


class ConnectionPoint(BaseModel):
    name: str
    type: Literal['ingress', 'egress']
    protocol: Literal['TCP', 'UDP', 'SCTP']
    port: int = Field(ge=1, le=65535)


class ComputeRequirements(BaseModel):
    vcpu: PositiveInt
    memory_gb: PositiveInt
    storage_gb: PositiveInt
    accelerator: str | None = None


class ApplicationComponent(BaseModel):
    id: str
    name: str
    component_type: ComponentType
    image_ref: str
    compute_requirements: ComputeRequirements
    interfaces: list[ConnectionPoint]


class ServiceDescriptor(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    version: str
    service_type: ServiceType
    sla_profile: SLAProfile
    components: list[ApplicationComponent] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_unique_component_ids(self) -> 'ServiceDescriptor':
        ids = [c.id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("ApplicationComponent IDs must be unique within a ServiceDescriptor.")
        return self
