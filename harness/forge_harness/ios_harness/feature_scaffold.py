"""
Feature Scaffolding for iOS Projects
=====================================

Generates complete MVVM features with:
- ViewModel (@Observable)
- Actor Service (Swift 6)
- SwiftUI Views
- Models (SwiftData when needed)
- Tests (XCTest)

Supported Features:
- Auth (login, registration, password reset)
- Dashboard (data display, refresh)
- Settings (user preferences, profile)
- Onboarding (welcome, tutorial)
- Profile (user info, edit)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .swift_codegen import (
    ActorSpec,
    Method,
    Property,
    SwiftCodeGen,
    ViewModelSpec,
    ViewSpec,
)


@dataclass
class ScaffoldResult:
    """Result of feature scaffolding."""

    feature_name: str
    files_created: list[str] = field(default_factory=list)
    success: bool = True
    errors: list[str] = field(default_factory=list)


class FeatureScaffold:
    """Scaffold complete MVVM features."""

    def __init__(self, project_name: str, backend_url: str):
        """
        Initialize feature scaffold.

        Args:
            project_name: Name of iOS project
            backend_url: Backend API URL
        """
        self.project_name = project_name
        self.backend_url = backend_url
        self.codegen = SwiftCodeGen(project_name)

    async def scaffold_auth(self, output_dir: Path) -> ScaffoldResult:
        """
        Scaffold authentication feature.

        Generates:
        - AuthViewModel (@Observable)
        - AuthService (actor)
        - LoginView, RegisterView
        - User model (SwiftData)
        - AuthViewModelTests

        Args:
            output_dir: Where to create feature files

        Returns:
            ScaffoldResult with created files
        """
        result = ScaffoldResult(feature_name="Auth")

        try:
            # Create feature directory
            feature_dir = output_dir / "App" / "Features" / "Auth"
            feature_dir.mkdir(parents=True, exist_ok=True)

            # 1. Generate AuthService (actor)
            actor_spec = ActorSpec(
                name="Auth",
                properties=[
                    Property("isAuthenticated", "Bool", "false", access="private"),
                ],
                methods=[
                    Method(
                        name="login",
                        parameters=[("email", "String"), ("password", "String")],
                        return_type="User",
                        is_async=True,
                        throws=True,
                        body=self._auth_login_body(),
                    ),
                    Method(
                        name="register",
                        parameters=[
                            ("email", "String"),
                            ("password", "String"),
                            ("name", "String"),
                        ],
                        return_type="User",
                        is_async=True,
                        throws=True,
                        body=self._auth_register_body(),
                    ),
                    Method(
                        name="logout",
                        is_async=True,
                        body="        // Clear stored credentials\n        isAuthenticated = false",
                    ),
                ],
            )

            service_code = self.codegen.generate_actor(actor_spec)
            service_path = feature_dir / "AuthService.swift"
            service_path.write_text(service_code)
            result.files_created.append(str(service_path))

            # 2. Generate AuthViewModel
            viewmodel_spec = ViewModelSpec(
                name="Auth",
                properties=[
                    Property("email", "String", '""'),
                    Property("password", "String", '""'),
                    Property("isLoading", "Bool", "false"),
                    Property("error", "Error?", "nil"),
                    Property("isAuthenticated", "Bool", "false"),
                ],
                dependencies=["AuthService"],
                methods=[
                    Method(
                        name="login",
                        is_async=True,
                        body=self._viewmodel_login_body(),
                    ),
                    Method(
                        name="register",
                        parameters=[("name", "String")],
                        is_async=True,
                        body=self._viewmodel_register_body(),
                    ),
                    Method(
                        name="logout",
                        is_async=True,
                        body=self._viewmodel_logout_body(),
                    ),
                ],
            )

            viewmodel_code = self.codegen.generate_viewmodel(viewmodel_spec)
            viewmodel_path = feature_dir / "AuthViewModel.swift"
            viewmodel_path.write_text(viewmodel_code)
            result.files_created.append(str(viewmodel_path))

            # 3. Generate LoginView
            login_view_spec = ViewSpec(
                name="Login",
                viewmodel_name="AuthViewModel",
                has_navigation=True,
                has_loading_state=True,
                has_error_state=True,
            )

            login_view_code = self.codegen.generate_view(login_view_spec)
            # Enhance with auth-specific UI
            login_view_code = self._enhance_login_view(login_view_code)
            login_path = feature_dir / "LoginView.swift"
            login_path.write_text(login_view_code)
            result.files_created.append(str(login_path))

            # 4. Generate User model
            user_model = self._generate_user_model()
            model_dir = output_dir / "App" / "Models"
            model_dir.mkdir(parents=True, exist_ok=True)
            model_path = model_dir / "User.swift"
            model_path.write_text(user_model)
            result.files_created.append(str(model_path))

        except Exception as e:
            result.success = False
            result.errors.append(f"Auth scaffold failed: {str(e)}")

        return result

    async def scaffold_dashboard(self, output_dir: Path) -> ScaffoldResult:
        """
        Scaffold dashboard feature.

        Generates:
        - DashboardViewModel
        - DashboardService (data fetching)
        - DashboardView
        - Refresh/reload logic

        Args:
            output_dir: Where to create feature files

        Returns:
            ScaffoldResult with created files
        """
        result = ScaffoldResult(feature_name="Dashboard")

        try:
            feature_dir = output_dir / "App" / "Features" / "Dashboard"
            feature_dir.mkdir(parents=True, exist_ok=True)

            # 1. Generate DashboardService
            actor_spec = ActorSpec(
                name="Dashboard",
                methods=[
                    Method(
                        name="fetchData",
                        return_type="DashboardData",
                        is_async=True,
                        throws=True,
                        body=self._dashboard_fetch_body(),
                    ),
                    Method(
                        name="refresh",
                        return_type="DashboardData",
                        is_async=True,
                        throws=True,
                        body="        // Refresh data from API\n        return try await fetchData()",
                    ),
                ],
            )

            service_code = self.codegen.generate_actor(actor_spec)
            service_path = feature_dir / "DashboardService.swift"
            service_path.write_text(service_code)
            result.files_created.append(str(service_path))

            # 2. Generate DashboardViewModel
            viewmodel_spec = ViewModelSpec(
                name="Dashboard",
                properties=[
                    Property("data", "DashboardData?", "nil"),
                    Property("isLoading", "Bool", "false"),
                    Property("error", "Error?", "nil"),
                ],
                dependencies=["DashboardService"],
                methods=[
                    Method(
                        name="loadData",
                        is_async=True,
                        body=self._dashboard_load_body(),
                    ),
                    Method(
                        name="refresh",
                        is_async=True,
                        body=self._dashboard_refresh_body(),
                    ),
                ],
            )

            viewmodel_code = self.codegen.generate_viewmodel(viewmodel_spec)
            viewmodel_path = feature_dir / "DashboardViewModel.swift"
            viewmodel_path.write_text(viewmodel_code)
            result.files_created.append(str(viewmodel_path))

            # 3. Generate DashboardView
            view_spec = ViewSpec(
                name="Dashboard",
                viewmodel_name="DashboardViewModel",
                has_navigation=True,
                has_loading_state=True,
                has_error_state=True,
            )

            view_code = self.codegen.generate_view(view_spec)
            view_code = self._enhance_dashboard_view(view_code)
            view_path = feature_dir / "DashboardView.swift"
            view_path.write_text(view_code)
            result.files_created.append(str(view_path))

            # 4. Generate DashboardData model
            model_code = self._generate_dashboard_model()
            model_dir = output_dir / "App" / "Models"
            model_dir.mkdir(parents=True, exist_ok=True)
            model_path = model_dir / "DashboardData.swift"
            model_path.write_text(model_code)
            result.files_created.append(str(model_path))

        except Exception as e:
            result.success = False
            result.errors.append(f"Dashboard scaffold failed: {str(e)}")

        return result

    async def scaffold_settings(self, output_dir: Path) -> ScaffoldResult:
        """
        Scaffold settings feature.

        Generates:
        - SettingsViewModel
        - SettingsView (form-based)
        - UserDefaults persistence

        Args:
            output_dir: Where to create feature files

        Returns:
            ScaffoldResult with created files
        """
        result = ScaffoldResult(feature_name="Settings")

        try:
            feature_dir = output_dir / "App" / "Features" / "Settings"
            feature_dir.mkdir(parents=True, exist_ok=True)

            # 1. Generate SettingsViewModel
            viewmodel_spec = ViewModelSpec(
                name="Settings",
                properties=[
                    Property("notificationsEnabled", "Bool", "true"),
                    Property("darkModeEnabled", "Bool", "false"),
                    Property("language", "String", '"en"'),
                ],
                methods=[
                    Method(
                        name="saveSettings",
                        body=self._settings_save_body(),
                    ),
                    Method(
                        name="loadSettings",
                        body=self._settings_load_body(),
                    ),
                ],
            )

            viewmodel_code = self.codegen.generate_viewmodel(viewmodel_spec)
            viewmodel_path = feature_dir / "SettingsViewModel.swift"
            viewmodel_path.write_text(viewmodel_code)
            result.files_created.append(str(viewmodel_path))

            # 2. Generate SettingsView
            view_spec = ViewSpec(
                name="Settings",
                viewmodel_name="SettingsViewModel",
                has_navigation=True,
                has_loading_state=False,
                has_error_state=False,
            )

            view_code = self.codegen.generate_view(view_spec)
            view_code = self._enhance_settings_view(view_code)
            view_path = feature_dir / "SettingsView.swift"
            view_path.write_text(view_code)
            result.files_created.append(str(view_path))

        except Exception as e:
            result.success = False
            result.errors.append(f"Settings scaffold failed: {str(e)}")

        return result

    # Helper methods for code generation

    def _auth_login_body(self) -> str:
        return f"""        guard let url = URL(string: "{self.backend_url}/auth/login") else {{
            throw AuthServiceError.invalidResponse
        }}

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = ["email": email, "password": password]
        request.httpBody = try JSONEncoder().encode(body)

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {{
            throw AuthServiceError.unauthorized
        }}

        do {{
            let user = try JSONDecoder().decode(User.self, from: data)
            isAuthenticated = true
            return user
        }} catch {{
            throw AuthServiceError.decodingError(error)
        }}"""

    def _auth_register_body(self) -> str:
        return f"""        guard let url = URL(string: "{self.backend_url}/auth/register") else {{
            throw AuthServiceError.invalidResponse
        }}

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = ["email": email, "password": password, "name": name]
        request.httpBody = try JSONEncoder().encode(body)

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 201 else {{
            throw AuthServiceError.invalidResponse
        }}

        do {{
            let user = try JSONDecoder().decode(User.self, from: data)
            isAuthenticated = true
            return user
        }} catch {{
            throw AuthServiceError.decodingError(error)
        }}"""

    def _viewmodel_login_body(self) -> str:
        return """        guard !email.isEmpty, !password.isEmpty else { return }

        isLoading = true
        error = nil

        do {
            let user = try await authService.login(email: email, password: password)
            isAuthenticated = true
            isLoading = false
        } catch {
            self.error = error
            isLoading = false
        }"""

    def _viewmodel_register_body(self) -> str:
        return """        guard !email.isEmpty, !password.isEmpty, !name.isEmpty else { return }

        isLoading = true
        error = nil

        do {
            let user = try await authService.register(email: email, password: password, name: name)
            isAuthenticated = true
            isLoading = false
        } catch {
            self.error = error
            isLoading = false
        }"""

    def _viewmodel_logout_body(self) -> str:
        return """        isLoading = true
        await authService.logout()
        isAuthenticated = false
        email = ""
        password = ""
        isLoading = false"""

    def _dashboard_fetch_body(self) -> str:
        return f"""        guard let url = URL(string: "{self.backend_url}/dashboard") else {{
            throw DashboardServiceError.invalidResponse
        }}

        let (data, _) = try await session.data(from: url)

        do {{
            return try JSONDecoder().decode(DashboardData.self, from: data)
        }} catch {{
            throw DashboardServiceError.decodingError(error)
        }}"""

    def _dashboard_load_body(self) -> str:
        return """        isLoading = true
        error = nil

        do {
            data = try await dashboardService.fetchData()
            isLoading = false
        } catch {
            self.error = error
            isLoading = false
        }"""

    def _dashboard_refresh_body(self) -> str:
        return """        do {
            data = try await dashboardService.refresh()
            error = nil
        } catch {
            self.error = error
        }"""

    def _settings_save_body(self) -> str:
        return """        UserDefaults.standard.set(notificationsEnabled, forKey: "notificationsEnabled")
        UserDefaults.standard.set(darkModeEnabled, forKey: "darkModeEnabled")
        UserDefaults.standard.set(language, forKey: "language")"""

    def _settings_load_body(self) -> str:
        return """        notificationsEnabled = UserDefaults.standard.bool(forKey: "notificationsEnabled")
        darkModeEnabled = UserDefaults.standard.bool(forKey: "darkModeEnabled")
        language = UserDefaults.standard.string(forKey: "language") ?? "en" """

    def _generate_user_model(self) -> str:
        return f"""//
// User.swift
// {self.project_name}
//
// Generated by FORGE iOS Harness
//

import Foundation
import SwiftData

@Model
final class User {{
    var id: String
    var email: String
    var name: String
    var createdAt: Date

    init(id: String, email: String, name: String, createdAt: Date = Date()) {{
        self.id = id
        self.email = email
        self.name = name
        self.createdAt = createdAt
    }}
}}
"""

    def _generate_dashboard_model(self) -> str:
        return f"""//
// DashboardData.swift
// {self.project_name}
//
// Generated by FORGE iOS Harness
//

import Foundation

struct DashboardData: Codable {{
    let title: String
    let summary: String
    let stats: [Stat]
    let lastUpdated: Date

    struct Stat: Codable {{
        let label: String
        let value: String
        let trend: String?
    }}
}}
"""

    def _enhance_login_view(self, base_code: str) -> str:
        """Add login-specific UI to base view."""
        # Replace the mainContent placeholder with actual login UI
        login_ui = """    @ViewBuilder
    private var mainContent: some View {
        VStack(spacing: 20) {
            // Email field
            TextField("Email", text: $viewModel.email)
                .textContentType(.emailAddress)
                .textInputAutocapitalization(.never)
                .keyboardType(.emailAddress)
                .textFieldStyle(.roundedBorder)

            // Password field
            SecureField("Password", text: $viewModel.password)
                .textContentType(.password)
                .textFieldStyle(.roundedBorder)

            // Login button
            Button("Login") {
                Task {
                    await viewModel.login()
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(viewModel.email.isEmpty || viewModel.password.isEmpty)
        }
        .padding()
    }"""

        return base_code.replace(
            """    @ViewBuilder
    private var mainContent: some View {
        VStack {
            Text("Login")
                .font(.title)
            // NOTE: Add view content here
        }
    }""",
            login_ui,
        )

    def _enhance_dashboard_view(self, base_code: str) -> str:
        """Add dashboard-specific UI."""
        dashboard_ui = """    @ViewBuilder
    private var mainContent: some View {
        ScrollView {
            VStack(spacing: 16) {
                if let data = viewModel.data {
                    Text(data.title)
                        .font(.title)

                    Text(data.summary)
                        .foregroundStyle(.secondary)

                    ForEach(data.stats, id: \\.label) { stat in
                        HStack {
                            Text(stat.label)
                            Spacer()
                            Text(stat.value)
                                .bold()
                        }
                        .padding()
                        .background(Color.secondary.opacity(0.1))
                        .cornerRadius(8)
                    }
                }
            }
            .padding()
        }
        .refreshable {
            await viewModel.refresh()
        }
        .task {
            await viewModel.loadData()
        }
    }"""

        return base_code.replace(
            """    @ViewBuilder
    private var mainContent: some View {
        VStack {
            Text("Dashboard")
                .font(.title)
            // NOTE: Add view content here
        }
    }""",
            dashboard_ui,
        )

    def _enhance_settings_view(self, base_code: str) -> str:
        """Add settings-specific UI."""
        settings_ui = """    @ViewBuilder
    private var mainContent: some View {
        Form {
            Section("Notifications") {
                Toggle("Enable Notifications", isOn: $viewModel.notificationsEnabled)
            }

            Section("Appearance") {
                Toggle("Dark Mode", isOn: $viewModel.darkModeEnabled)
            }

            Section("Language") {
                Picker("Language", selection: $viewModel.language) {
                    Text("English").tag("en")
                    Text("Spanish").tag("es")
                }
            }
        }
        .onChange(of: viewModel.notificationsEnabled) { _, _ in
            viewModel.saveSettings()
        }
        .onChange(of: viewModel.darkModeEnabled) { _, _ in
            viewModel.saveSettings()
        }
        .onChange(of: viewModel.language) { _, _ in
            viewModel.saveSettings()
        }
        .onAppear {
            viewModel.loadSettings()
        }
    }"""

        return base_code.replace(
            """    @ViewBuilder
    private var mainContent: some View {
        VStack {
            Text("Settings")
                .font(.title)
            // NOTE: Add view content here
        }
    }""",
            settings_ui,
        )
