"""Tests for Swift code generation."""

import pytest

from forge_harness.ios_harness.swift_codegen import (
    ActorSpec,
    Method,
    Property,
    SwiftCodeGen,
    ViewModelSpec,
    ViewSpec,
)


@pytest.fixture
def codegen():
    """Create SwiftCodeGen instance."""
    return SwiftCodeGen("TestApp")


class TestSwiftCodeGen:
    """Test SwiftCodeGen class."""

    def test_init(self, codegen):
        """Should initialize with project name."""
        assert codegen.project_name == "TestApp"

    def test_generate_viewmodel_basic(self, codegen):
        """Should generate basic ViewModel."""
        spec = ViewModelSpec(
            name="Auth",
            properties=[
                Property("email", "String", '""'),
                Property("isLoading", "Bool", "false"),
            ],
        )

        code = codegen.generate_viewmodel(spec)

        assert "@Observable" in code
        assert "final class AuthViewModel" in code
        assert 'var email: String = ""' in code
        assert "var isLoading: Bool = false" in code
        assert "import Foundation" in code
        assert "import Observation" in code

    def test_generate_viewmodel_with_dependencies(self, codegen):
        """Should generate ViewModel with service dependencies."""
        spec = ViewModelSpec(
            name="Auth",
            properties=[Property("email", "String", '""')],
            dependencies=["AuthService"],
        )

        code = codegen.generate_viewmodel(spec)

        assert "private let auth: AuthService" in code
        assert "init(auth: AuthService)" in code
        assert "self.auth = auth" in code

    def test_generate_viewmodel_with_methods(self, codegen):
        """Should generate ViewModel with methods."""
        spec = ViewModelSpec(
            name="Auth",
            methods=[
                Method(
                    name="login",
                    is_async=True,
                    body="        // Login logic",
                ),
            ],
        )

        code = codegen.generate_viewmodel(spec)

        assert "func login() async" in code
        assert "// Login logic" in code

    def test_generate_actor_basic(self, codegen):
        """Should generate basic actor service."""
        spec = ActorSpec(
            name="Auth",
            properties=[Property("isAuthenticated", "Bool", "false", access="private")],
        )

        code = codegen.generate_actor(spec)

        assert "actor AuthService" in code
        assert "private var isAuthenticated: Bool = false" in code
        assert "enum AuthServiceError: Error" in code
        assert "private let session: URLSession" in code

    def test_generate_actor_with_methods(self, codegen):
        """Should generate actor with async methods."""
        spec = ActorSpec(
            name="Auth",
            methods=[
                Method(
                    name="login",
                    parameters=[("email", "String"), ("password", "String")],
                    return_type="User",
                    is_async=True,
                    throws=True,
                    body="        // Login implementation",
                ),
            ],
        )

        code = codegen.generate_actor(spec)

        assert "func login(email: String, password: String) async throws" in code
        assert "-> User" in code
        assert "// Login implementation" in code

    def test_generate_view_basic(self, codegen):
        """Should generate basic SwiftUI view."""
        spec = ViewSpec(
            name="Login",
            viewmodel_name="AuthViewModel",
        )

        code = codegen.generate_view(spec)

        assert "struct LoginView: View" in code
        assert "import SwiftUI" in code
        assert "@State private var viewModel: AuthViewModel" in code
        assert "var body: some View" in code

    def test_generate_view_with_navigation(self, codegen):
        """Should generate view with NavigationStack."""
        spec = ViewSpec(
            name="Login",
            viewmodel_name="AuthViewModel",
            has_navigation=True,
        )

        code = codegen.generate_view(spec)

        assert "NavigationStack" in code
        assert ".navigationTitle" in code

    def test_generate_view_with_loading_state(self, codegen):
        """Should generate view with loading state."""
        spec = ViewSpec(
            name="Login",
            viewmodel_name="AuthViewModel",
            has_loading_state=True,
        )

        code = codegen.generate_view(spec)

        assert "if viewModel.isLoading" in code
        assert "ProgressView()" in code

    def test_generate_view_with_error_state(self, codegen):
        """Should generate view with error handling."""
        spec = ViewSpec(
            name="Login",
            viewmodel_name="AuthViewModel",
            has_error_state=True,
        )

        code = codegen.generate_view(spec)

        assert "if let error = viewModel.error" in code
        assert "ContentUnavailableView" in code
        assert "errorView(error)" in code

    def test_generate_view_includes_preview(self, codegen):
        """Should include SwiftUI preview."""
        spec = ViewSpec(
            name="Login",
            viewmodel_name="AuthViewModel",
        )

        code = codegen.generate_view(spec)

        assert "#Preview {" in code
        assert "LoginView(viewModel: AuthViewModel())" in code

    def test_camel_case_conversion(self, codegen):
        """Should convert to camelCase correctly."""
        assert codegen._camel_case("AuthService") == "auth"
        assert codegen._camel_case("UserService") == "user"
        assert codegen._camel_case("Service") == ""


class TestProperty:
    """Test Property dataclass."""

    def test_property_basic(self):
        """Should create basic property."""
        prop = Property("name", "String")

        assert prop.name == "name"
        assert prop.type == "String"
        assert prop.default_value is None
        assert prop.is_optional is False

    def test_property_with_default(self):
        """Should create property with default value."""
        prop = Property("count", "Int", "0")

        assert prop.default_value == "0"

    def test_property_optional(self):
        """Should create optional property."""
        prop = Property("email", "String?", is_optional=True)

        assert prop.is_optional is True
        assert prop.type == "String?"


class TestMethod:
    """Test Method dataclass."""

    def test_method_basic(self):
        """Should create basic method."""
        method = Method("fetchData")

        assert method.name == "fetchData"
        assert method.return_type == "Void"
        assert method.is_async is False
        assert method.throws is False

    def test_method_with_parameters(self):
        """Should create method with parameters."""
        method = Method(
            "login",
            parameters=[("email", "String"), ("password", "String")],
        )

        assert len(method.parameters) == 2
        assert method.parameters[0] == ("email", "String")

    def test_method_async_throws(self):
        """Should create async throwing method."""
        method = Method(
            "fetchUser",
            return_type="User",
            is_async=True,
            throws=True,
        )

        assert method.is_async is True
        assert method.throws is True
        assert method.return_type == "User"


class TestViewModelSpec:
    """Test ViewModelSpec dataclass."""

    def test_spec_basic(self):
        """Should create basic spec."""
        spec = ViewModelSpec("Auth")

        assert spec.name == "Auth"
        assert len(spec.properties) == 0
        assert len(spec.methods) == 0
        assert len(spec.dependencies) == 0

    def test_spec_with_properties(self):
        """Should create spec with properties."""
        spec = ViewModelSpec(
            "Auth",
            properties=[
                Property("email", "String"),
                Property("password", "String"),
            ],
        )

        assert len(spec.properties) == 2

    def test_spec_with_dependencies(self):
        """Should create spec with dependencies."""
        spec = ViewModelSpec(
            "Auth",
            dependencies=["AuthService", "ValidationService"],
        )

        assert len(spec.dependencies) == 2
        assert "AuthService" in spec.dependencies
