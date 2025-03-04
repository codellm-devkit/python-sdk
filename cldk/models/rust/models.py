from enum import Enum
from typing import Dict, List, Optional, Set, ForwardRef
from pydantic import BaseModel, Field


class RustVisibility(Enum):
    """Represents Rust visibility modifiers."""

    PUBLIC = "pub"
    PRIVATE = ""
    CRATE = "pub(crate)"
    SUPER = "pub(super)"
    IN_PATH = "pub(in path)"


class SafetyClassification(Enum):
    """Classifies the safety level of Rust code."""

    SAFE = "safe"
    UNSAFE = "unsafe"
    UNSAFE_CONTAINER = "unsafe_container"
    FFI = "ffi"


class UnsafeReason(Enum):
    """Reasons why code might be unsafe."""

    RAW_POINTER_DEREF = "raw_pointer_deref"
    MUTABLE_STATIC = "mutable_static"
    FFI_CALL = "ffi_call"
    UNION_FIELD_ACCESS = "union_field_access"
    INLINE_ASSEMBLY = "inline_assembly"
    UNSAFE_TRAIT_IMPL = "unsafe_trait_impl"
    CUSTOM = "custom"


class RustType(BaseModel):
    """Represents a Rust type."""

    name: str
    is_reference: bool = False
    is_mutable: bool = False
    lifetime: Optional[str] = None
    generic_params: List[str] = Field(default_factory=list)
    is_sized: bool = True
    is_static: bool = False
    contains_raw_pointers: bool = False
    is_union: bool = False


class RustAttribute(BaseModel):
    """Represents a Rust attribute."""

    name: str
    arguments: List[str] = Field(default_factory=list)
    is_inner: bool = False  # #![foo] vs #[foo]


class RustGenericParam(BaseModel):
    """Represents a generic type parameter in Rust."""

    name: str
    bounds: List[str] = Field(default_factory=list)  # Trait bounds
    default_type: Optional[str] = None
    is_const: bool = False  # For const generics


class RustLifetimeParam(BaseModel):
    """Represents a lifetime parameter in Rust."""

    name: str
    bounds: List[str] = Field(default_factory=list)  # Lifetime bounds


class RustParameter(BaseModel):
    """Represents a function parameter in Rust."""

    name: str
    type: RustType
    is_self: bool = False  # For methods (self, &self, &mut self)
    is_mut: bool = False  # For mutable bindings
    default_value: Optional[str] = None


class RustCallSite(BaseModel):
    """Represents a location where a function is called."""

    line_number: int
    caller_function: Optional[str] = None
    caller_module: Optional[str] = None
    argument_types: List[RustType] = Field(default_factory=list)
    is_unsafe_context: bool = False


class RustVariableDeclaration(BaseModel):
    """Represents a variable declaration in Rust."""

    name: str
    type: Optional[RustType] = None
    is_mut: bool = False
    is_static: bool = False
    is_const: bool = False
    initializer: Optional[str] = None
    visibility: RustVisibility = RustVisibility.PRIVATE
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)
    line_number: int


class UnsafeBlock(BaseModel):
    """Represents an unsafe block within Rust code."""

    start_line: int
    end_line: int
    reasons: List[UnsafeReason] = Field(default_factory=list)
    explanation: Optional[str] = None  # Documentation explaining why unsafe is needed
    containing_function: Optional[str] = None


class SafetyAnalysis(BaseModel):
    """Analyzes and tracks safety-related information."""

    classification: SafetyClassification
    unsafe_blocks: List[UnsafeBlock] = Field(default_factory=list)
    unsafe_fn_calls: List[str] = Field(default_factory=list)
    raw_pointer_usage: bool = False
    ffi_interactions: bool = False
    unsafe_traits_used: List[str] = Field(default_factory=list)
    mutable_statics: List[str] = Field(default_factory=list)
    safety_comments: Optional[str] = None


class RustCallable(BaseModel):
    """Represents a Rust function or method."""

    name: str
    visibility: RustVisibility = RustVisibility.PRIVATE
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)
    parameters: List["RustParameter"] = Field(default_factory=list)
    return_type: Optional[RustType] = None
    is_async: bool = False
    is_const: bool = False
    is_unsafe: bool = False
    is_extern: bool = False
    extern_abi: Optional[str] = None
    generic_params: List["RustGenericParam"] = Field(default_factory=list)
    lifetime_params: List["RustLifetimeParam"] = Field(default_factory=list)
    where_clauses: List[str] = Field(default_factory=list)
    code: str
    start_line: int
    end_line: int
    referenced_types: List[str] = Field(default_factory=list)
    accessed_variables: List[str] = Field(default_factory=list)
    call_sites: List["RustCallSite"] = Field(default_factory=list)
    variable_declarations: List["RustVariableDeclaration"] = Field(default_factory=list)
    cyclomatic_complexity: Optional[int] = None
    safety_analysis: SafetyAnalysis

    def is_fully_safe(self) -> bool:
        """Check if the function is completely safe (no unsafe blocks or calls)."""
        return (
            self.safety_analysis.classification == SafetyClassification.SAFE
            and not self.safety_analysis.unsafe_blocks
            and not self.safety_analysis.unsafe_fn_calls
        )

    def contains_unsafe(self) -> bool:
        """Check if the function contains any unsafe code."""
        return (
            self.safety_analysis.classification
            in [SafetyClassification.UNSAFE, SafetyClassification.UNSAFE_CONTAINER]
            or bool(self.safety_analysis.unsafe_blocks)
            or bool(self.safety_analysis.unsafe_fn_calls)
        )

    def get_unsafe_reasons(self) -> Set[UnsafeReason]:
        """Get all reasons for unsafe usage in this function."""
        reasons = set()
        for block in self.safety_analysis.unsafe_blocks:
            reasons.update(block.reasons)
        return reasons


class RustStructKind(Enum):
    """Represents different kinds of Rust structs."""

    NORMAL = "normal"  # Regular struct with named fields
    TUPLE = "tuple"  # Tuple struct
    UNIT = "unit"  # Unit struct without fields


class RustStructField(BaseModel):
    """Represents a field in a Rust struct."""

    name: str
    type: RustType
    visibility: RustVisibility = RustVisibility.PRIVATE
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)


class RustStruct(BaseModel):
    """Represents a Rust struct definition."""

    name: str
    kind: RustStructKind = RustStructKind.NORMAL
    visibility: RustVisibility = RustVisibility.PRIVATE
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)
    fields: List[RustStructField] = Field(default_factory=list)
    generic_params: List[RustGenericParam] = Field(default_factory=list)
    lifetime_params: List[RustLifetimeParam] = Field(default_factory=list)
    where_clauses: List[str] = Field(default_factory=list)
    derives: List[str] = Field(default_factory=list)
    associated_items: Dict[str, RustCallable] = Field(default_factory=dict)
    impl_traits: List[str] = Field(default_factory=list)
    is_public: bool = False
    contains_unsafe: bool = False
    start_line: int
    end_line: int

    def get_field(self, name: str) -> Optional[RustStructField]:
        """Get a field by name."""
        for field in self.fields:
            if field.name == name:
                return field
        return None

    def has_field(self, name: str) -> bool:
        """Check if the struct has a field with the given name."""
        return any(field.name == name for field in self.fields)

    def get_public_fields(self) -> List[RustStructField]:
        """Get all public fields of the struct."""
        return [
            field for field in self.fields if field.visibility == RustVisibility.PUBLIC
        ]

    def get_private_fields(self) -> List[RustStructField]:
        """Get all private fields of the struct."""
        return [
            field for field in self.fields if field.visibility == RustVisibility.PRIVATE
        ]

    def add_field(self, field: RustStructField) -> None:
        """Add a new field to the struct."""
        if self.kind == RustStructKind.UNIT:
            raise ValueError("Cannot add fields to a unit struct")
        if self.kind == RustStructKind.NORMAL and not field.name:
            raise ValueError("Normal struct fields must have names")
        self.fields.append(field)

    def is_generic(self) -> bool:
        """Check if the struct has any generic parameters."""
        return bool(self.generic_params) or bool(self.lifetime_params)

    def get_associated_functions(self) -> List[RustCallable]:
        """Get all associated functions (not methods)."""
        return [
            func
            for func in self.associated_items.values()
            if not any(param.is_self for param in func.parameters)
        ]

    def get_methods(self) -> List[RustCallable]:
        """Get all methods (functions that take self)."""
        return [
            func
            for func in self.associated_items.values()
            if any(param.is_self for param in func.parameters)
        ]


class RustTraitBound(BaseModel):
    """Represents a trait bound in Rust."""

    trait_name: str
    generic_params: List[str] = Field(default_factory=list)
    is_sized: bool = True
    is_optional: bool = False  # For ?Sized etc.
    lifetime_bounds: List[str] = Field(default_factory=list)


class RustEnum(BaseModel):
    """Represents a Rust enum."""

    name: str
    visibility: RustVisibility = RustVisibility.PRIVATE
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)
    variants: List["RustEnumVariant"] = Field(default_factory=list)
    generic_params: List[RustGenericParam] = Field(default_factory=list)
    lifetime_params: List[RustLifetimeParam] = Field(default_factory=list)
    where_clauses: List[str] = Field(default_factory=list)
    derives: List[str] = Field(default_factory=list)
    associated_items: Dict[str, RustCallable] = Field(default_factory=dict)
    impl_traits: List[str] = Field(default_factory=list)
    is_public: bool = False
    start_line: int
    end_line: int


class RustEnumVariant(BaseModel):
    """Represents a variant in a Rust enum."""

    name: str
    fields: Optional[List[RustStructField]] = None  # For struct variants
    tuple_types: Optional[List[RustType]] = None  # For tuple variants
    discriminant: Optional[str] = None  # For explicit discriminants
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)


class RustTrait(BaseModel):
    """Represents a Rust trait definition."""

    name: str
    visibility: RustVisibility = RustVisibility.PRIVATE
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)
    generic_params: List[RustGenericParam] = Field(default_factory=list)
    lifetime_params: List[RustLifetimeParam] = Field(default_factory=list)
    where_clauses: List[str] = Field(default_factory=list)
    super_traits: List[RustTraitBound] = Field(default_factory=list)
    associated_types: Dict[str, RustType] = Field(default_factory=dict)
    associated_consts: Dict[str, str] = Field(default_factory=dict)
    methods: Dict[str, RustCallable] = Field(default_factory=dict)
    is_unsafe: bool = False
    is_auto: bool = False
    start_line: int
    end_line: int


class RustImpl(BaseModel):
    """Represents a Rust impl block."""

    type_name: str
    trait_name: Optional[str] = None  # None for inherent impls
    generic_params: List[RustGenericParam] = Field(default_factory=list)
    lifetime_params: List[RustLifetimeParam] = Field(default_factory=list)
    where_clauses: List[str] = Field(default_factory=list)
    methods: Dict[str, RustCallable] = Field(default_factory=dict)
    associated_types: Dict[str, RustType] = Field(default_factory=dict)
    associated_consts: Dict[str, str] = Field(default_factory=dict)
    is_unsafe: bool = False
    is_negative: bool = False  # For negative impls (!Send etc.)
    start_line: int
    end_line: int


class RustMacro(BaseModel):
    """Represents a Rust macro definition."""

    name: str
    visibility: RustVisibility = RustVisibility.PRIVATE
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)
    rules: List[str] = Field(default_factory=list)
    is_procedural: bool = False
    is_derive: bool = False
    is_attribute: bool = False
    is_function_like: bool = True
    exported_from_macro_use: bool = False
    start_line: int
    end_line: int


class RustTypeAlias(BaseModel):
    """Represents a Rust type alias."""

    name: str
    visibility: RustVisibility = RustVisibility.PRIVATE
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)
    generic_params: List[RustGenericParam] = Field(default_factory=list)
    lifetime_params: List[RustLifetimeParam] = Field(default_factory=list)
    where_clauses: List[str] = Field(default_factory=list)
    target_type: RustType
    start_line: int
    end_line: int


# Update RustModule to include new types
class RustModule(BaseModel):
    """Represents a Rust module with all possible items."""

    name: str
    doc_comment: Optional[str] = None
    attributes: List[RustAttribute] = Field(default_factory=list)
    visibility: RustVisibility = RustVisibility.PRIVATE

    # Type definitions
    types: Dict[str, RustType] = Field(default_factory=dict)
    structs: Dict[str, RustStruct] = Field(default_factory=dict)
    enums: Dict[str, RustEnum] = Field(default_factory=dict)
    traits: Dict[str, RustTrait] = Field(default_factory=dict)
    impls: List[RustImpl] = Field(default_factory=list)
    type_aliases: Dict[str, RustTypeAlias] = Field(default_factory=dict)

    # Functions and macros
    functions: Dict[str, RustCallable] = Field(default_factory=dict)
    safe_functions: Dict[str, RustCallable] = Field(default_factory=dict)
    unsafe_functions: Dict[str, RustCallable] = Field(default_factory=dict)
    macros: Dict[str, RustMacro] = Field(default_factory=dict)

    # Module structure
    submodules: Dict[str, "RustModule"] = Field(default_factory=dict)
    constants: List[RustVariableDeclaration] = Field(default_factory=list)
    use_declarations: List[str] = Field(default_factory=list)
    extern_crates: List[str] = Field(default_factory=list)

    # Module properties
    is_unsafe: bool = False
    file_path: Optional[str] = None
    is_mod_rs: bool = False
    is_root_module: bool = False

    def categorize_functions(self):
        """Categorize functions based on their safety analysis."""
        self.safe_functions.clear()
        self.unsafe_functions.clear()

        for name, func in self.functions.items():
            if func.is_fully_safe():
                self.safe_functions[name] = func
            else:
                self.unsafe_functions[name] = func


class RustDependency(BaseModel):
    """Represents a Rust Dependency, Maybe internal or external"""

    name: str
    is_external: bool = True
    crate: Optional[ForwardRef("RustCrate")]


class RustCrate(BaseModel):
    """Represents a complete Rust crate."""

    name: str
    version: str
    is_lib: bool = False
    modules: List[RustModule]
    edition: str = "2021"
    features: List[str] = Field(default_factory=list)
    dependencies: List[RustDependency] = Field(default_factory=list)

    def analyze_safety(self) -> Dict[str, int]:
        """Analyze safety statistics across the crate."""
        stats = {
            "total_functions": 0,
            "safe_functions": 0,
            "unsafe_functions": 0,
            "unsafe_blocks": 0,
            "ffi_functions": 0,
        }

        def analyze_module(module: RustModule):
            for func in module.functions.values():
                stats["total_functions"] += 1
                if func.is_fully_safe():
                    stats["safe_functions"] += 1
                else:
                    stats["unsafe_functions"] += 1
                    if func.safety_analysis.classification == SafetyClassification.FFI:
                        stats["ffi_functions"] += 1
                stats["unsafe_blocks"] += len(func.safety_analysis.unsafe_blocks)

            for submodule in module.submodules.values():
                analyze_module(submodule)

        analyze_module(self.root_module)
        return stats

    def get_unsafe_functions(self) -> List[tuple[str, RustCallable]]:
        """Get all unsafe functions in the crate with their module paths."""
        unsafe_fns = []

        def collect_unsafe(module: RustModule, path: str):
            for name, func in module.functions.items():
                if not func.is_fully_safe():
                    full_path = f"{path}::{name}" if path else name
                    unsafe_fns.append((full_path, func))

            for submod_name, submod in module.submodules.items():
                new_path = f"{path}::{submod_name}" if path else submod_name
                collect_unsafe(submod, new_path)

        collect_unsafe(self.root_module, "")
        return unsafe_fns
