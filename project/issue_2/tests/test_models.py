import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from domain.enums import ComponentType, ServiceType
from domain.models import (
    ApplicationComponent,
    ComputeRequirements,
    ConnectionPoint,
    ServiceDescriptor,
    SLAProfile,
)
from pydantic import ValidationError


def test_duplicate_component_ids():
    comp1 = ApplicationComponent(
        id="c1",
        name="Comp1",
        component_type=ComponentType.CNF,
        image_ref="img1",
        compute_requirements=ComputeRequirements(vcpu=1, memory_gb=1, storage_gb=1),
        interfaces=[],
    )
    comp2 = ApplicationComponent(
        id="c1",
        name="Comp2",
        component_type=ComponentType.VNF,
        image_ref="img2",
        compute_requirements=ComputeRequirements(vcpu=1, memory_gb=1, storage_gb=1),
        interfaces=[],
    )
    with pytest.raises(ValidationError, match="ApplicationComponent IDs must be unique"):
        ServiceDescriptor(
            name="Service",
            version="1.0.0",
            service_type=ServiceType.SDWAN,
            sla_profile=SLAProfile(
                max_latency_ms=10, min_bandwidth_mbps=100, availability_pct=99.9
            ),
            components=[comp1, comp2],
        )


def test_invalid_sla_range():
    with pytest.raises(ValidationError):
        SLAProfile(max_latency_ms=10, min_bandwidth_mbps=100, availability_pct=100.1)
    with pytest.raises(ValidationError):
        SLAProfile(max_latency_ms=10, min_bandwidth_mbps=100, availability_pct=-0.1)


def test_zero_negative_resource_allocation():
    with pytest.raises(ValidationError):
        ComputeRequirements(vcpu=0, memory_gb=1, storage_gb=1)
    with pytest.raises(ValidationError):
        ComputeRequirements(vcpu=1, memory_gb=-1, storage_gb=1)


def test_invalid_port_range():
    with pytest.raises(ValidationError):
        ConnectionPoint(name="cp1", type="ingress", protocol="TCP", port=0)
    with pytest.raises(ValidationError):
        ConnectionPoint(name="cp1", type="ingress", protocol="TCP", port=65536)


def test_empty_component_list():
    sd = ServiceDescriptor(
        name="Service",
        version="1.0.0",
        service_type=ServiceType.SDWAN,
        sla_profile=SLAProfile(
            max_latency_ms=10, min_bandwidth_mbps=100, availability_pct=99.9
        ),
        components=[],
    )
    assert sd.components == []


def test_none_components_fails():
    with pytest.raises(ValidationError):
        ServiceDescriptor(
            name="Service",
            version="1.0.0",
            service_type=ServiceType.SDWAN,
            sla_profile=SLAProfile(
                max_latency_ms=10, min_bandwidth_mbps=100, availability_pct=99.9
            ),
            components=None,
        )
