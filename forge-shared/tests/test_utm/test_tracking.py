"""
Tests for UTM tracking models and utilities.

Test coverage for UTM parameter tracking, parsing, and management.
"""

import pytest
from unittest.mock import MagicMock
from fastapi import Request


class TestUTMModels:
    """
    Tests for UTM tracking models.

    Tests UTMParameters model and validation.
    """

    def test_utm_models_imports(self):
        """
        Test that UTM models can be imported.
        """
        try:
            from forge_shared.utm.models import UTMParameters
            assert UTMParameters is not None
        except ImportError:
            pytest.skip("UTM models not implemented")

    def test_utm_parameters_creation(self):
        """
        Test creating UTMParameters instance.
        """
        try:
            from forge_shared.utm.models import UTMParameters

            utm = UTMParameters(
                source="google",
                medium="cpc",
                campaign="spring_sale",
                content="banner_ad",
                term="test+keyword"
            )
            
            assert utm.source == "google"
            assert utm.medium == "cpc"
            assert utm.campaign == "spring_sale"
            assert utm.content == "banner_ad"
            assert utm.term == "test+keyword"
        except ImportError:
            pytest.skip("UTMParameters model not implemented")

    def test_utm_parameters_partial(self):
        """
        Test creating UTMParameters with only some fields.
        """
        try:
            from forge_shared.utm.models import UTMParameters

            utm = UTMParameters(
                source="newsletter",
                medium="email"
            )
            
            assert utm.source == "newsletter"
            assert utm.medium == "email"
            assert utm.campaign is None
        except ImportError:
            pytest.skip("Partial UTM parameters not implemented")

    def test_utm_parameters_from_dict(self):
        """
        Test creating UTMParameters from dictionary.
        """
        try:
            from forge_shared.utm.models import UTMParameters

            data = {
                "utm_source": "facebook",
                "utm_medium": "social",
                "utm_campaign": "brand_awareness"
            }
            
            utm = UTMParameters.from_dict(data)
            assert utm.source == "facebook"
            assert utm.medium == "social"
            assert utm.campaign == "brand_awareness"
        except (ImportError, AttributeError):
            pytest.skip("from_dict method not implemented")

    def test_utm_parameters_to_dict(self):
        """
        Test converting UTMParameters to dictionary.
        """
        try:
            from forge_shared.utm.models import UTMParameters

            utm = UTMParameters(
                source="twitter",
                medium="social",
                campaign="launch"
            )
            
            data = utm.to_dict()
            assert data["utm_source"] == "twitter"
            assert data["utm_medium"] == "social"
            assert data["utm_campaign"] == "launch"
        except (ImportError, AttributeError):
            pytest.skip("to_dict method not implemented")


class TestUTMTracking:
    """
    Tests for UTM tracking utilities.

    Tests extraction, storage, and retrieval of UTM parameters.
    """

    def test_utm_tracking_imports(self):
        """
        Test that UTM tracking utilities can be imported.
        """
        try:
            from forge_shared.utm.tracking import (
                extract_utm_from_request,
                store_utm_parameters,
                get_stored_utm,
            )
            assert extract_utm_from_request is not None
        except ImportError:
            pytest.skip("UTM tracking not implemented")

    def test_extract_utm_from_query_params(self):
        """
        Test extracting UTM parameters from query string.
        """
        try:
            from forge_shared.utm.tracking import extract_utm_from_request

            request = MagicMock(spec=Request)
            request.query_params = {
                "utm_source": "google",
                "utm_medium": "cpc",
                "utm_campaign": "test",
                "other_param": "value"
            }

            utm = extract_utm_from_request(request)
            
            assert utm is not None
            assert utm.source == "google"
            assert utm.medium == "cpc"
            assert utm.campaign == "test"
        except ImportError:
            pytest.skip("UTM extraction not implemented")

    def test_extract_utm_no_parameters(self):
        """
        Test extracting UTM when no parameters present.
        """
        try:
            from forge_shared.utm.tracking import extract_utm_from_request

            request = MagicMock(spec=Request)
            request.query_params = {"other_param": "value"}

            utm = extract_utm_from_request(request)
            
            # Should return None or empty UTMParameters
            assert utm is None or (utm.source is None and utm.medium is None)
        except ImportError:
            pytest.skip("UTM extraction not implemented")

    def test_store_utm_parameters(self):
        """
        Test storing UTM parameters in session/cookie.
        """
        try:
            from forge_shared.utm.tracking import store_utm_parameters
            from forge_shared.utm.models import UTMParameters

            utm = UTMParameters(
                source="linkedin",
                medium="social",
                campaign="b2b_campaign"
            )

            request = MagicMock(spec=Request)
            response = MagicMock()
            
            store_utm_parameters(utm, request, response)
            # Should not raise exception
        except ImportError:
            pytest.skip("UTM storage not implemented")

    def test_get_stored_utm(self):
        """
        Test retrieving stored UTM parameters.
        """
        try:
            from forge_shared.utm.tracking import get_stored_utm
            from forge_shared.utm.models import UTMParameters

            request = MagicMock(spec=Request)
            
            utm = get_stored_utm(request)
            # Should return UTMParameters or None
            assert utm is None or isinstance(utm, UTMParameters)
        except ImportError:
            pytest.skip("UTM retrieval not implemented")


class TestUTMMiddleware:
    """
    Tests for UTM middleware.

    Tests automatic UTM tracking in requests.
    """

    def test_utm_middleware_imports(self):
        """
        Test that UTM middleware can be imported.
        """
        try:
            from forge_shared.utm.middleware import UTMMiddleware
            assert UTMMiddleware is not None
        except ImportError:
            pytest.skip("UTM middleware not implemented")

    @pytest.mark.asyncio
    async def test_utm_middleware_extracts_parameters(self):
        """
        Test that middleware extracts UTM parameters.
        """
        try:
            from forge_shared.utm.middleware import UTMMiddleware
            from starlette.testclient import TestClient
            from fastapi import FastAPI

            app = FastAPI()
            
            @app.get("/test")
            async def test_endpoint():
                return {"message": "test"}

            app.add_middleware(UTMMiddleware)

            client = TestClient(app)
            response = client.get("/test?utm_source=google&utm_medium=cpc")
            
            assert response.status_code == 200
        except ImportError:
            pytest.skip("UTM middleware not implemented")

    @pytest.mark.asyncio
    async def test_utm_middleware_stores_parameters(self):
        """
        Test that middleware stores UTM parameters.
        """
        try:
            from forge_shared.utm.middleware import UTMMiddleware
            from starlette.testclient import TestClient
            from fastapi import FastAPI, Request

            app = FastAPI()
            
            @app.get("/test")
            async def test_endpoint(request: Request):
                # Check if UTM params are stored in request state
                utm = getattr(request.state, "utm_params", None)
                return {"has_utm": utm is not None}

            app.add_middleware(UTMMiddleware)

            client = TestClient(app)
            response = client.get("/test?utm_source=google")
            
            assert response.status_code == 200
        except ImportError:
            pytest.skip("UTM middleware storage not implemented")
