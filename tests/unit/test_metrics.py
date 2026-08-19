"""Unit tests for metrics module"""

import pytest
from contextlib import contextmanager
from unittest.mock import Mock, patch
from datetime import datetime, timedelta
from flask import Flask


def _mock_edit_data(metrics):
    """Return a context manager standing in for DataInterface.edit_data, yielding
    the given Metrics object so the route mutates it in place (and the test can
    assert on it)."""
    @contextmanager
    def _cm(user):
        yield metrics
    return _cm

# Import app from __main__ where blueprints are registered
import web_app.__main__ as main_module
from web_app.users import User
from web_app.metrics import metrics_api
from web_app.metrics.app_data import Metric, DataPoint, Metrics
from web_app.helpers import limiter
import web_app.helpers as helpers

app = main_module.app


@pytest.fixture
def client():
    """Create a test client for the Flask app"""
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    limiter.enabled = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_user():
    """Create a test user"""
    return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)


@pytest.fixture
def test_metric():
    """Create a test metric"""
    return Metric(
        id=1,
        name='Test Metric',
        data=[
            DataPoint(date=datetime(2026, 2, 1), value=10.0),
            DataPoint(date=datetime(2026, 2, 2), value=15.0),
            DataPoint(date=datetime(2026, 2, 3), value=12.0),
        ],
        unit='kg',
        description='Test metric description',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1)
    )


@pytest.fixture
def test_metrics():
    """Create test metrics with different last_modified times"""
    metric1 = Metric(
        id=1,
        name='Weight',
        data=[DataPoint(date=datetime(2026, 2, 1), value=70.0)],
        unit='kg',
        description='Body weight',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1, 10, 0, 0)  # Oldest
    )
    metric2 = Metric(
        id=2,
        name='Steps',
        data=[DataPoint(date=datetime(2026, 2, 1), value=10000.0)],
        unit='steps',
        description='Daily steps',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1, 12, 0, 0)  # Middle
    )
    metric3 = Metric(
        id=3,
        name='Calories',
        data=[DataPoint(date=datetime(2026, 2, 1), value=2000.0)],
        unit='kcal',
        description='Daily calories',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1, 14, 0, 0)  # Most recent
    )
    return Metrics(metrics={1: metric1, 2: metric2, 3: metric3})


@pytest.fixture
def auth_mock(test_user):
    """Setup authentication mocking for tests"""
    # Mock the user_loader to return our test user
    original_user_loader = helpers.login_manager._user_callback
    helpers.login_manager._user_callback = lambda username: test_user if username == test_user.id else None
    
    yield test_user
    
    # Restore original user_loader
    helpers.login_manager._user_callback = original_user_loader


class TestMetricsLastModified:
    """Tests for metrics last_modified functionality"""

    @patch('web_app.metrics.DataInterface')
    def test_metrics_sorted_by_last_modified(self, mock_di_class, client, auth_mock, test_metrics):
        """Test that metrics are sorted by last_modified (most recent first)"""
        mock_di = mock_di_class.return_value
        mock_di.load_data.return_value = test_metrics

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.get('/metrics/')

        assert response.status_code == 200
        # The metrics should be rendered in order: Calories (14:00), Steps (12:00), Weight (10:00)
        html = response.data.decode()
        # Check that Calories appears before Steps, and Steps before Weight
        calories_pos = html.find('Calories')
        steps_pos = html.find('Steps')
        weight_pos = html.find('Weight')
        assert calories_pos < steps_pos < weight_pos, "Metrics should be sorted by last_modified descending"

    @patch('web_app.metrics.DataInterface')
    def test_edit_metric_updates_last_modified(self, mock_di_class, client, auth_mock):
        """Test that editing a metric updates last_modified"""
        old_time = datetime(2026, 1, 1, 10, 0, 0)
        metric = Metric(
            id=1,
            name='Weight',
            data=[],
            unit='kg',
            last_modified=old_time
        )
        metrics = Metrics(metrics={1: metric})

        mock_di = mock_di_class.return_value
        mock_di.edit_data = _mock_edit_data(metrics)

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.post('/metrics/edit', data={
            'metric_id': '1',
            'name': 'Body Weight',
            'units': 'lbs',
            'description': 'Updated description'
        })

        assert response.status_code == 302
        # Verify last_modified was updated (should be different from old_time)
        assert metric.last_modified != old_time
        assert metric.last_modified > old_time

    @patch('web_app.metrics.DataInterface')
    def test_log_metric_updates_last_modified(self, mock_di_class, client, auth_mock):
        """Test that logging a metric value updates last_modified"""
        old_time = datetime(2026, 1, 1, 10, 0, 0)
        metric = Metric(
            id=1,
            name='Weight',
            data=[],
            unit='kg',
            last_modified=old_time
        )
        metrics = Metrics(metrics={1: metric})

        mock_di = mock_di_class.return_value
        mock_di.edit_data = _mock_edit_data(metrics)

        with client.session_transaction() as sess:
            sess['_user_id'] = auth_mock.id

        response = client.post('/metrics/log', data={
            'metric_id': '1',
            'value': '75.5'
        })

        assert response.status_code == 302
        # Verify last_modified was updated
        assert metric.last_modified != old_time
        assert metric.last_modified > old_time
        # Verify data point was added
        assert len(metric.data) == 1
        assert metric.data[0].value == 75.5


def test_metrics_mutations_use_partial_page_updates():
    template = open('web_app/metrics/templates/metrics_page.html').read()
    script = open('web_app/metrics/static/script.js').read()

    assert 'data-async-mutation' in template
    assert 'refreshMetricsPage' in script
    assert 'window.location.reload' not in script


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
