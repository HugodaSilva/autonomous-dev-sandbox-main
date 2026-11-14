from enum import Enum


class ServiceType(str, Enum):
    FIVE_G_SLICE = "5G_SLICE"
    L3VPN = "L3VPN"
    SDWAN = "SDWAN"


class ComponentType(str, Enum):
    CNF = "CNF"
    VNF = "VNF"
    MICROSERVICE = "MICROSERVICE"
