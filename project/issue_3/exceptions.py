class CatalogError(Exception): pass
class DuplicateTemplateError(CatalogError): pass
class ImmutableTemplateError(CatalogError): pass
class DependencyViolationError(CatalogError): pass
class InvalidVersionFormatError(CatalogError): pass
